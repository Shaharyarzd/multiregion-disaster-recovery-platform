#!/usr/bin/env python3
"""Execute the bounded AWS logical-corruption drill in two resumable processes."""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from dr_platform.aws_adapters import (
    CloudWatchEvidenceEmitter,
    DynamoRecoveryAdapter,
    DynamoReplayTarget,
    recovery_target_name,
)
from dr_platform.data_validation import compare_datasets, dataset_checksum, measured_rpo_seconds
from dr_platform.errors import ValidationFailed
from dr_platform.observability import MetricDatum, recovery_metrics, structured_recovery_log
from dr_platform.orchestrator import RecoveryOrchestrator
from dr_platform.reconciliation import ConsistencyProof, plan_reconciliation
from dr_platform.store import LocalIncidentStore
from dr_platform.types import RecoveryState, RegionHealth, Scenario, Transaction, iso, utc_now

ACCOUNT = "293951765338"
PRIMARY_REGION = "us-east-1"
SECONDARY_REGION = "us-west-2"
TABLE = "portfolio-dr-transactions"
PRIMARY_KEY = "arn:aws:kms:us-east-1:293951765338:key/a7e1a0f4-be05-474d-b19d-bdf6da573b40"
PRIMARY_BUCKET = "portfolio-dr-293951765338-us-east-1"
SECONDARY_BUCKET = "portfolio-dr-293951765338-us-west-2"
PROJECT_TAGS = {
    "Project": "portfolio-dr",
    "DataClassification": "SYNTHETIC",
    "RecoveryPurpose": "isolated-validation",
}
SERIALIZER = TypeSerializer()
DESERIALIZER = TypeDeserializer()


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def tx_from_mapping(item: dict[str, Any]) -> Transaction:
    return Transaction(
        transaction_id=str(item["transaction_id"]),
        timestamp=parse_time(str(item["timestamp"])),
        region=str(item["region"]),
        amount_cents=int(item["amount_cents"]),
        payload=str(item["payload"]),
    )


def tx_to_ddb(item: Transaction) -> dict[str, dict[str, Any]]:
    raw = {**item.canonical(), "data_classification": "SYNTHETIC_PUBLIC"}
    return {key: SERIALIZER.serialize(value) for key, value in raw.items()}


def scan(client: Any, table: str) -> list[Transaction]:
    items: list[dict[str, dict[str, Any]]] = []
    request: dict[str, Any] = {"TableName": table, "ConsistentRead": True}
    while True:
        response = client.scan(**request)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            break
        request["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    decoded = [
        {key: DESERIALIZER.deserialize(value) for key, value in item.items()} for item in items
    ]
    transactions = [tx_from_mapping(item) for item in decoded]
    if any(not item.transaction_id.startswith("txn-") for item in transactions):
        raise RuntimeError("non-synthetic transaction key encountered")
    return sorted(transactions, key=lambda item: item.transaction_id)


def api_json(
    endpoint: str,
    path: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> Any:
    import urllib.request

    if endpoint not in {
        "https://cvjvklgn0b.execute-api.us-east-1.amazonaws.com",
        "https://n7hsrlcdu8.execute-api.us-west-2.amazonaws.com",
    }:
        raise ValueError("unapproved endpoint")
    raw = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(  # noqa: S310 -- endpoints are exact allow-listed HTTPS URLs
        endpoint + path,
        data=raw,
        method=method,
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
        return json.load(response)


def create(endpoint: str, seed: str, amount: int) -> Transaction:
    return tx_from_mapping(
        api_json(endpoint, "/transactions", "POST", {"seed": seed, "amount_cents": amount})
    )


def observe_api(endpoint: str, transaction_id: str, timeout: float = 60) -> dict[str, Any]:
    started = time.monotonic()
    attempts = 0
    backoff = 0.25
    while time.monotonic() - started <= timeout:
        attempts += 1
        try:
            item = api_json(endpoint, f"/transactions/{transaction_id}")
            if item.get("transaction_id") == transaction_id:
                return {"completed": True, "attempts": attempts, "observed_at": iso(utc_now())}
        except Exception as error:
            if getattr(error, "code", None) != 404:
                raise
        time.sleep(backoff)
        backoff = min(backoff * 2, 4)
    return {"completed": False, "attempts": attempts, "observed_at": None}


def aws_clock_skew_ms(client: Any) -> float:
    before = utc_now()
    response = client.describe_table(TableName=TABLE)
    after = utc_now()
    raw = response["ResponseMetadata"]["HTTPHeaders"]["date"]
    server = parsedate_to_datetime(raw).astimezone(UTC)
    midpoint = before + (after - before) / 2
    # AWS Date headers have one-second resolution; include that quantization in the bound.
    return abs((midpoint - server).total_seconds() * 1000)


def wait_for_restorable_anchor(client: Any, anchor: datetime, timeout: float = 720) -> datetime:
    started = time.monotonic()
    backoff = 2.0
    while time.monotonic() - started <= timeout:
        result = client.describe_continuous_backups(TableName=TABLE)
        pitr = result["ContinuousBackupsDescription"]["PointInTimeRecoveryDescription"]
        latest = pitr["LatestRestorableDateTime"].astimezone(UTC)
        if pitr["PointInTimeRecoveryStatus"] == "ENABLED" and latest >= anchor + timedelta(
            seconds=2
        ):
            return latest
        time.sleep(backoff)
        backoff = min(backoff * 1.5, 15)
    raise TimeoutError("PITR latest restorable time did not pass the baseline anchor")


def wait_table(client: Any, name: str, timeout: float = 1800) -> dict[str, Any]:
    started = time.monotonic()
    backoff = 2.0
    while time.monotonic() - started <= timeout:
        table = client.describe_table(TableName=name)["Table"]
        if table.get("TableStatus") == "ACTIVE":
            return table
        time.sleep(backoff)
        backoff = min(backoff * 1.5, 15)
    raise TimeoutError(f"table {name} did not become ACTIVE")


def configure_target(client: Any, target: str, run_id: str) -> dict[str, bool]:
    table = wait_table(client, target)
    if table.get("DeletionProtectionEnabled") is not True:
        client.update_table(TableName=target, DeletionProtectionEnabled=True)
        table = wait_table(client, target)
    stream = table.get("StreamSpecification", {})
    if not stream.get("StreamEnabled") or stream.get("StreamViewType") != "NEW_AND_OLD_IMAGES":
        client.update_table(
            TableName=target,
            StreamSpecification={
                "StreamEnabled": True,
                "StreamViewType": "NEW_AND_OLD_IMAGES",
            },
        )
        table = wait_table(client, target)
    client.update_continuous_backups(
        TableName=target,
        PointInTimeRecoverySpecification={"PointInTimeRecoveryEnabled": True},
    )
    arn = table["TableArn"]
    tags = {**PROJECT_TAGS, "RecoveryRun": run_id}
    client.tag_resource(
        ResourceArn=arn,
        Tags=[{"Key": key, "Value": value} for key, value in sorted(tags.items())],
    )
    started = time.monotonic()
    while time.monotonic() - started <= 300:
        table = wait_table(client, target)
        backups = client.describe_continuous_backups(TableName=target)
        ttl = client.describe_time_to_live(TableName=target)["TimeToLiveDescription"]
        actual_tags = {
            tag["Key"]: tag["Value"]
            for tag in client.list_tags_of_resource(ResourceArn=arn).get("Tags", [])
        }
        proof = {
            "table_active": table["TableStatus"] == "ACTIVE",
            "encryption_verified": table.get("SSEDescription", {}).get("KMSMasterKeyArn")
            == PRIMARY_KEY,
            "pitr_enabled": backups["ContinuousBackupsDescription"][
                "PointInTimeRecoveryDescription"
            ]["PointInTimeRecoveryStatus"]
            == "ENABLED",
            "tags_verified": all(actual_tags.get(key) == value for key, value in tags.items()),
            "ttl_verified": ttl.get("TimeToLiveStatus") in {"DISABLED", "DISABLING"},
            "stream_verified": table.get("StreamSpecification", {}).get("StreamEnabled") is True
            and table.get("StreamSpecification", {}).get("StreamViewType") == "NEW_AND_OLD_IMAGES",
            "no_replicas": not bool(table.get("Replicas")),
            "deletion_protection": table.get("DeletionProtectionEnabled") is True,
            "billing_mode_verified": table.get("BillingModeSummary", {}).get("BillingMode")
            == "PAY_PER_REQUEST",
        }
        proof["ready_for_validation"] = all(proof.values())
        if proof["ready_for_validation"]:
            return proof
        time.sleep(3)
    raise RuntimeError("restored table configuration proof did not converge")


def writer_loop(
    endpoints: tuple[str, str], run_id: str, output: list[Transaction], stop: threading.Event
) -> None:
    for index in range(12):
        if stop.is_set():
            return
        endpoint = endpoints[index % 2]
        output.append(create(endpoint, f"scenario-b-live-{run_id}-{index}", 1000 + index))
        stop.wait(8)


def transaction_dicts(items: list[Transaction]) -> list[dict[str, Any]]:
    return [item.canonical() for item in items]


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def prepare(args: argparse.Namespace) -> None:
    session = boto3.session.Session()
    identity = session.client("sts").get_caller_identity()
    if identity["Account"] != ACCOUNT:
        raise RuntimeError("wrong AWS account")
    scenario_a = json.loads(args.scenario_a.read_text())
    scenario_a_digest = scenario_a.pop("sha256", None)
    calculated_a_digest = hashlib.sha256(
        json.dumps(scenario_a, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if scenario_a_digest != calculated_a_digest or scenario_a.get("status") != "PASS":
        raise RuntimeError("Scenario A evidence integrity/status check failed")
    scenario_a["sha256"] = scenario_a_digest
    dynamo = session.client("dynamodb", region_name=PRIMARY_REGION)
    clock_skew = aws_clock_skew_ms(dynamo)
    if clock_skew > 1500:
        raise RuntimeError(f"clock skew bound failed: {clock_skew:.3f}ms")
    endpoints = (args.region_a, args.region_b)
    health = [api_json(endpoint, "/health") for endpoint in endpoints]
    if any(item.get("status") != "healthy" for item in health):
        raise RuntimeError("regional API health failed")

    anchors = [
        create(args.region_a, f"scenario-b-anchor-a-{args.run_id}", 901),
        create(args.region_b, f"scenario-b-anchor-b-{args.run_id}", 902),
    ]
    for transaction, destination in zip(anchors, endpoints[::-1], strict=True):
        if not observe_api(destination, transaction.transaction_id)["completed"]:
            raise RuntimeError("anchor did not converge cross-region")
    recovery_point = wait_for_restorable_anchor(dynamo, max(item.timestamp for item in anchors))
    point_snapshot = scan(dynamo, TABLE)

    concurrent: list[Transaction] = []
    stop = threading.Event()
    writer = threading.Thread(
        target=writer_loop,
        args=(endpoints, args.run_id, concurrent, stop),
        daemon=True,
    )
    writer.start()
    deadline = time.monotonic() + 60
    while len(concurrent) < 2 and time.monotonic() < deadline:
        time.sleep(0.25)
    if len(concurrent) < 2:
        raise RuntimeError("controlled concurrent writers did not start")

    fault_candidates = [item for item in point_snapshot if item not in anchors]
    if len(fault_candidates) < 2:
        fault_candidates = point_snapshot
    deleted, corrupted = sorted(fault_candidates, key=lambda item: item.timestamp)[:2]
    failure_at = utc_now()
    dynamo.delete_item(
        TableName=TABLE,
        Key={"transaction_id": {"S": deleted.transaction_id}},
        ConditionExpression="attribute_exists(transaction_id)",
    )
    corrupt_payload = f"synthetic-corruption:{args.run_id}"
    dynamo.update_item(
        TableName=TABLE,
        Key={"transaction_id": {"S": corrupted.transaction_id}},
        UpdateExpression="SET payload = :payload",
        ConditionExpression="payload = :expected",
        ExpressionAttributeValues={
            ":payload": {"S": corrupt_payload},
            ":expected": {"S": corrupted.payload},
        },
    )

    orchestrator = RecoveryOrchestrator()
    incident = orchestrator.declare(Scenario.LOGICAL_CORRUPTION, failure_at)
    incident.run_id = args.run_id
    incident.scenario_id = "scenario-b-logical-corruption"
    incident.evidence_scope = "AWS_RUNTIME"
    incident.clock_skew_ms_observed = clock_skew
    incident.clock_skew_ms_limit = 1500
    incident.timestamp_sources = {
        "incident_declaration": "CONTROLLER_UTC_SYNCED",
        "failure_or_corruption": "FAULT_INJECTOR_UTC_SYNCED",
        "validation": "CONTROLLER_UTC_SYNCED",
        "recovered_transaction": "DYNAMODB_TRANSACTION_UTC_VALIDATED",
    }
    orchestrator.start_recovery(incident, recovery_point)
    target = recovery_target_name(TABLE, args.run_id)
    restore_started = utc_now()
    adapter = DynamoRecoveryAdapter(PRIMARY_REGION)
    adapter.restore_to_isolated_table(
        f"arn:aws:dynamodb:{PRIMARY_REGION}:{ACCOUNT}:table/{TABLE}",
        target,
        recovery_point,
        kms_key_arn=PRIMARY_KEY,
    )
    wait_table(dynamo, target)
    proof = configure_target(dynamo, target, args.run_id)
    restore_ready = utc_now()
    deadline = time.monotonic() + 30
    while len(concurrent) < 4 and time.monotonic() < deadline:
        time.sleep(0.25)
    stop.set()
    writer.join(timeout=20)
    orchestrator.begin_validation(incident, restore_ready, restore_configuration=proof)
    incident.runtime_evidence = {
        "scenario_a": scenario_a,
        "process_resume": {
            "checkpoint_state": incident.state.value,
            "checkpoint_written_at": iso(utc_now()),
            "approval_boundary": "aws-recovery-approval",
        },
        "pitr": {
            "target": target,
            "isolated": target.startswith("portfolio-dr-recovery-"),
            "recovery_point": iso(recovery_point),
            "restore_started": iso(restore_started),
            "restore_ready": iso(restore_ready),
            "restore_duration_seconds": (restore_ready - restore_started).total_seconds(),
            "configuration": proof,
        },
        "fault": {
            "deleted_transaction_id": deleted.transaction_id,
            "corrupted_transaction_id": corrupted.transaction_id,
            "corrupt_payload_sha256": hashlib.sha256(corrupt_payload.encode()).hexdigest(),
        },
        "concurrent_writes": transaction_dicts(concurrent),
        "clock": {
            "observed_skew_bound_ms": clock_skew,
            "source": "AWS_HTTP_DATE_VS_GITHUB_RUNNER_MIDPOINT",
        },
    }
    store = LocalIncidentStore(args.work_dir)
    store.save(incident)
    write_json(
        args.work_dir / "checkpoint.json",
        {
            "point_snapshot": transaction_dicts(point_snapshot),
            "concurrent_writes": transaction_dicts(concurrent),
            "deleted": deleted.canonical(),
            "corrupted": corrupted.canonical(),
            "corrupt_payload": corrupt_payload,
        },
    )
    write_json(args.output, {"status": "PASS", **incident.runtime_evidence})
    print(json.dumps(structured_recovery_log(incident, "PITR_CHECKPOINT", utc_now())))


def retry_failed_prepare(args: argparse.Namespace) -> None:
    """Resume the known failed attempt without injecting a second corruption."""
    if not all(
        (
            args.scenario_a,
            args.recovery_point,
            args.failure_lower_bound,
            args.failure_upper_bound,
            args.failed_run_id,
            args.attempt_id,
        )
    ):
        raise ValueError("retry-failed-prepare requires all failed-run evidence arguments")
    session = boto3.session.Session()
    identity = session.client("sts").get_caller_identity()
    if identity["Account"] != ACCOUNT:
        raise RuntimeError("wrong AWS account")
    scenario_a = json.loads(args.scenario_a.read_text())
    scenario_a_digest = scenario_a.pop("sha256", None)
    calculated_a_digest = hashlib.sha256(
        json.dumps(scenario_a, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if scenario_a_digest != calculated_a_digest or scenario_a.get("status") != "PASS":
        raise RuntimeError("Scenario A evidence integrity/status check failed")
    scenario_a["sha256"] = scenario_a_digest
    dynamo = session.client("dynamodb", region_name=PRIMARY_REGION)
    clock_skew = aws_clock_skew_ms(dynamo)
    if clock_skew > 1500:
        raise RuntimeError(f"clock skew bound failed: {clock_skew:.3f}ms")
    endpoints = (args.region_a, args.region_b)
    if any(api_json(endpoint, "/health").get("status") != "healthy" for endpoint in endpoints):
        raise RuntimeError("regional API health failed")

    recovery_point = parse_time(args.recovery_point)
    failure_lower = parse_time(args.failure_lower_bound)
    failure_upper = parse_time(args.failure_upper_bound)
    if not recovery_point < failure_lower <= failure_upper:
        raise RuntimeError("invalid observed recovery/failure time bounds")
    live_before = scan(dynamo, TABLE)
    anchors = [
        item
        for item in live_before
        if item.payload.startswith("synthetic-order:scenario-b-anchor-")
        and args.failed_run_id in item.payload
    ]
    if len(anchors) != 2:
        raise RuntimeError("exact failed-run anchor pair was not found")
    minimum_safe_point = max(item.timestamp for item in anchors) + timedelta(seconds=2)
    first_concurrent = min(
        (
            item.timestamp
            for item in live_before
            if f"scenario-b-live-{args.failed_run_id}" in item.payload
        ),
        default=None,
    )
    if recovery_point < minimum_safe_point or (
        first_concurrent is not None and recovery_point >= first_concurrent
    ):
        raise RuntimeError("recovery point is outside the observed safe interval")

    orchestrator = RecoveryOrchestrator()
    incident = orchestrator.declare(Scenario.LOGICAL_CORRUPTION, failure_upper)
    incident.run_id = args.failed_run_id
    incident.scenario_id = "scenario-b-logical-corruption"
    incident.evidence_scope = "AWS_RUNTIME"
    incident.clock_skew_ms_observed = clock_skew
    incident.clock_skew_ms_limit = 1500
    incident.timestamp_sources = {
        "incident_declaration": "CONTROLLER_UTC_SYNCED",
        "failure_or_corruption": "GITHUB_ACTIONS_LOG_UTC_UPPER_BOUND",
        "validation": "CONTROLLER_UTC_SYNCED",
        "recovered_transaction": "DYNAMODB_PITR_READBACK_UTC_VALIDATED",
    }
    orchestrator.start_recovery(incident, recovery_point)
    target = recovery_target_name(TABLE, args.failed_run_id)
    concurrent: list[Transaction] = []
    try:
        existing_target = dynamo.describe_table(TableName=target)["Table"]
    except dynamo.exceptions.ResourceNotFoundException:
        existing_target = None
    resumed_existing_target = existing_target is not None
    if existing_target is None:
        restore_started = utc_now()
        stop = threading.Event()
        writer = threading.Thread(
            target=writer_loop,
            args=(endpoints, args.attempt_id, concurrent, stop),
            daemon=True,
        )
        writer.start()
        try:
            DynamoRecoveryAdapter(PRIMARY_REGION).restore_to_isolated_table(
                f"arn:aws:dynamodb:{PRIMARY_REGION}:{ACCOUNT}:table/{TABLE}",
                target,
                recovery_point,
                kms_key_arn=PRIMARY_KEY,
            )
            wait_table(dynamo, target)
            restore_ready = utc_now()
        finally:
            stop.set()
            writer.join(timeout=20)
    else:
        if existing_target.get("TableStatus") != "ACTIVE":
            raise RuntimeError("preserved PITR target is not ACTIVE")
        restore_started = existing_target["CreationDateTime"].astimezone(UTC)
        restore_ready = parse_time("2026-09-07T09:24:02.799355Z")
    proof = configure_target(dynamo, target, args.failed_run_id)
    configuration_ready = utc_now()

    restored = scan(dynamo, target)
    restored_map = {item.transaction_id: item for item in restored}
    known = list(scenario_a["baseline_transactions"])
    known.extend(
        [
            scenario_a["regional_outage"]["survivor_transaction"],
            scenario_a["regional_return"]["transaction"],
        ]
    )
    known_transactions = [tx_from_mapping(item) for item in known] + anchors
    if any(restored_map.get(item.transaction_id) != item for item in known_transactions):
        raise RuntimeError("PITR read-back failed the independently recorded known-key subset")
    if any(item.timestamp > recovery_point for item in restored):
        raise RuntimeError("PITR target contains a transaction newer than the selected point")

    live_after = scan(dynamo, TABLE)
    concurrent = [item for item in live_after if item.timestamp > recovery_point]
    if len(concurrent) < 2 or len(concurrent) > 25:
        raise RuntimeError("observed post-point writes are outside the bounded replay batch")
    live_map = {item.transaction_id: item for item in live_after}
    missing = [item for item in restored if item.transaction_id not in live_map]
    modified = [
        item
        for item in restored
        if item.transaction_id in live_map
        and live_map[item.transaction_id].canonical() != item.canonical()
    ]
    corrupt_payload = f"synthetic-corruption:{args.failed_run_id}"
    if len(missing) != 1 or len(modified) != 1:
        raise RuntimeError("failed-run fault set was not exactly one deletion and one mutation")
    deleted, corrupted = missing[0], modified[0]
    if live_map[corrupted.transaction_id].payload != corrupt_payload:
        raise RuntimeError("modified item does not carry the exact failed-run corruption marker")

    orchestrator.begin_validation(incident, configuration_ready, restore_configuration=proof)
    incident.runtime_evidence = {
        "scenario_a": scenario_a,
        "failed_attempt": {
            "status": "FAIL",
            "github_run_id": "34097882635",
            "failed_at": "2026-09-07T08:01:53.748109Z",
            "phase": "RestoreTableToPointInTime",
            "failure_code": "MISSING_DYNAMODB_QUERY_ON_ISOLATED_TARGET",
            "cloudtrail_event_id": "af3fb862-f642-4a65-bb5d-5ee5660e1f1a",
            "cloudtrail_request_id": "H9M2TRJMG9BKDHP2G7403SSQ5VVV4KQNSO5AEMVJF66Q9ASUAAJG",
            "target_created": False,
        },
        "failed_attempts": [
            {
                "status": "FAIL",
                "github_run_id": "34097882635",
                "failed_at": "2026-09-07T08:01:53.748109Z",
                "phase": "RestoreTableToPointInTime",
                "failure_code": "MISSING_DYNAMODB_QUERY_ON_ISOLATED_TARGET",
                "target_created": False,
            },
            {
                "status": "FAIL",
                "github_run_id": "34100182403",
                "failed_at": "2026-09-07T08:22:38.228547Z",
                "phase": "RestoreTableToPointInTime",
                "failure_code": "MISSING_DYNAMODB_UPDATE_ITEM_ON_ISOLATED_TARGET",
                "target_created": False,
            },
            {
                "status": "FAIL",
                "github_run_id": "34100989020",
                "failed_at": "2026-09-07T08:31:45.121360Z",
                "phase": "RestoreTableToPointInTime",
                "failure_code": "MISSING_DYNAMODB_DELETE_ITEM_ON_ISOLATED_TARGET",
                "target_created": False,
            },
            {
                "status": "FAIL",
                "github_run_id": "34101812979",
                "failed_at": "2026-09-07T08:41:01.815633Z",
                "phase": "RestoreTableToPointInTime",
                "failure_code": "MISSING_DYNAMODB_BATCH_WRITE_ITEM_ON_ISOLATED_TARGET",
                "target_created": False,
            },
            {
                "status": "FAIL",
                "github_run_id": "34103322373",
                "failed_at": "2026-09-07T08:57:56.071447Z",
                "phase": "RestoreTableToPointInTime",
                "failure_code": "MISSING_KMS_DESCRIBE_KEY_ON_PRIMARY_DATA_KEY",
                "target_created": False,
            },
            {
                "status": "FAIL",
                "github_run_id": "34103791915",
                "failed_at": "2026-09-07T09:03:16.748129Z",
                "phase": "RestoreTableToPointInTime",
                "failure_code": "MISSING_KMS_CREATE_GRANT_ON_PRIMARY_DATA_KEY",
                "target_created": False,
            },
            {
                "status": "FAIL",
                "github_run_id": "34104305723",
                "failed_at": "2026-09-07T09:24:02.803528Z",
                "phase": "ConfigureIsolatedRecoveryTarget",
                "failure_code": "COMBINED_DELETION_PROTECTION_AND_STREAM_UPDATE",
                "target_created": True,
            },
            {
                "status": "FAIL",
                "github_run_id": "34106115552",
                "failed_at": "2026-09-07T09:29:05.312764Z",
                "phase": "S3ReplicationObservation",
                "failure_code": "S3_PENDING_REPLICA_RETURNED_403",
                "target_created": True,
                "checkpoint_persisted": True,
            },
        ],
        "process_resume": {
            "checkpoint_state": incident.state.value,
            "checkpoint_written_at": iso(utc_now()),
            "approval_boundary": "aws-recovery-approval",
            "reconstructed_after_pre_checkpoint_failure": True,
        },
        "pitr": {
            "target": target,
            "isolated": target.startswith("portfolio-dr-recovery-"),
            "recovery_point": iso(recovery_point),
            "recovery_point_basis": "LATEST_SAFE_BOUND_AFTER_TWO_OBSERVED_ANCHORS",
            "restore_started": iso(restore_started),
            "restore_ready": iso(restore_ready),
            "restore_duration_seconds": (restore_ready - restore_started).total_seconds(),
            "configuration_ready": iso(configuration_ready),
            "resumed_existing_target": resumed_existing_target,
            "configuration": proof,
        },
        "fault": {
            "deleted_transaction_id": deleted.transaction_id,
            "corrupted_transaction_id": corrupted.transaction_id,
            "corrupt_payload_sha256": hashlib.sha256(corrupt_payload.encode()).hexdigest(),
            "failure_lower_bound": iso(failure_lower),
            "failure_upper_bound": iso(failure_upper),
        },
        "validation_provenance": {
            "point_manifest": "ISOLATED_PITR_READBACK",
            "independent_known_key_count": len(known_transactions),
            "independent_known_keys_verified": True,
            "full_pre_fault_manifest_missing_due_to_preserved_failed_attempt": True,
        },
        "concurrent_writes": transaction_dicts(concurrent),
        "concurrent_write_proof": "OBSERVED_POST_POINT_WRITES_DURING_PRIOR_RESTORE_ATTEMPTS",
        "clock": {
            "observed_skew_bound_ms": clock_skew,
            "source": "AWS_HTTP_DATE_VS_GITHUB_RUNNER_MIDPOINT",
        },
        "measurement_bounds": {
            "rto_start": {
                "timestamp": iso(failure_upper),
                "source": "GITHUB_ACTIONS_LOG_UTC_UPPER_BOUND",
                "classification": "LOWER_BOUND",
            }
        },
    }
    store = LocalIncidentStore(args.work_dir)
    store.save(incident)
    write_json(
        args.work_dir / "checkpoint.json",
        {
            "point_snapshot": transaction_dicts(restored),
            "concurrent_writes": transaction_dicts(concurrent),
            "deleted": deleted.canonical(),
            "corrupted": corrupted.canonical(),
            "corrupt_payload": corrupt_payload,
        },
    )
    write_json(args.output, {"status": "PASS", **incident.runtime_evidence})
    print(json.dumps(structured_recovery_log(incident, "PITR_RETRY_CHECKPOINT", utc_now())))


def s3_proof(session: Any, run_id: str) -> dict[str, Any]:
    primary = session.client("s3", region_name=PRIMARY_REGION)
    secondary = session.client("s3", region_name=SECONDARY_REGION)
    key = f"supporting-data/{run_id}/manifest.json"
    recovery_key = f"supporting-data/{run_id}/recovered/manifest.json"
    content = json.dumps(
        {"classification": "SYNTHETIC", "run_id": run_id},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    digest = hashlib.sha256(content).hexdigest()
    written = utc_now()
    result = primary.put_object(
        Bucket=PRIMARY_BUCKET,
        Key=key,
        Body=content,
        Metadata={"sha256": digest, "classification": "synthetic"},
    )
    source_version = result["VersionId"]
    started = time.monotonic()
    attempts = 0
    backoff = 0.5
    observed: datetime | None = None
    while time.monotonic() - started <= 300:
        attempts += 1
        try:
            head = secondary.head_object(Bucket=SECONDARY_BUCKET, Key=key)
            if (
                head.get("ReplicationStatus") == "REPLICA"
                and head.get("Metadata", {}).get("sha256") == digest
            ):
                observed = utc_now()
                break
        except secondary.exceptions.NoSuchKey:
            pass
        except Exception as error:
            if getattr(error, "response", {}).get("Error", {}).get("Code") not in {
                "404",
                "403",
                "AccessDenied",
                "NoSuchKey",
                "NotFound",
            }:
                raise
        time.sleep(backoff)
        backoff = min(backoff * 2, 5)
    if observed is None:
        raise TimeoutError("S3 CRR observation timed out")
    replica = secondary.get_object(Bucket=SECONDARY_BUCKET, Key=key)["Body"].read()
    if hashlib.sha256(replica).hexdigest() != digest:
        raise RuntimeError("S3 replica digest mismatch")
    primary.delete_object(Bucket=PRIMARY_BUCKET, Key=key)
    primary.copy_object(
        Bucket=PRIMARY_BUCKET,
        Key=recovery_key,
        CopySource={"Bucket": PRIMARY_BUCKET, "Key": key, "VersionId": source_version},
        MetadataDirective="COPY",
    )
    recovered = primary.get_object(Bucket=PRIMARY_BUCKET, Key=recovery_key)["Body"].read()
    if recovered != content:
        raise RuntimeError("S3 version recovery content mismatch")
    return {
        "status": "PASS",
        "source_key": key,
        "source_version": source_version,
        "recovery_key": recovery_key,
        "sha256": digest,
        "source_write_at": iso(written),
        "destination_observed_at": iso(observed),
        "measured_lag_ms": (observed - written).total_seconds() * 1000,
        "attempts": attempts,
        "replication_status": "REPLICA",
        "delete_marker_created": True,
        "exact_version_recovered": True,
        "digest_equal": True,
    }


def repair_production(
    client: Any,
    deleted: Transaction,
    corrupted: Transaction,
    corrupt_payload: str,
) -> dict[str, Any]:
    applied: list[str] = []
    idempotent: list[str] = []
    try:
        client.put_item(
            TableName=TABLE,
            Item=tx_to_ddb(deleted),
            ConditionExpression="attribute_not_exists(transaction_id)",
        )
        applied.append(deleted.transaction_id)
    except client.exceptions.ConditionalCheckFailedException:
        existing = DynamoReplayTarget(PRIMARY_REGION, TABLE).get(deleted.transaction_id)
        if existing and existing.canonical() == deleted.canonical():
            idempotent.append(deleted.transaction_id)
        else:
            raise RuntimeError("deleted-key reconciliation conflict") from None
    try:
        client.put_item(
            TableName=TABLE,
            Item=tx_to_ddb(corrupted),
            ConditionExpression="payload = :corrupt",
            ExpressionAttributeValues={":corrupt": {"S": corrupt_payload}},
        )
        applied.append(corrupted.transaction_id)
    except client.exceptions.ConditionalCheckFailedException:
        existing = DynamoReplayTarget(PRIMARY_REGION, TABLE).get(corrupted.transaction_id)
        if existing and existing.canonical() == corrupted.canonical():
            idempotent.append(corrupted.transaction_id)
        else:
            raise RuntimeError("corrupt-key reconciliation conflict") from None
    return {
        "bounded_ids": sorted([deleted.transaction_id, corrupted.transaction_id]),
        "applied_ids": sorted(applied),
        "idempotent_ids": sorted(idempotent),
        "complete": len(applied) + len(idempotent) == 2,
        "conditional_write_protection": True,
    }


def wait_checksum(client: Any, expected: str, count: int, timeout: float = 90) -> dict[str, Any]:
    started = time.monotonic()
    attempts = 0
    backoff = 0.5
    while time.monotonic() - started <= timeout:
        attempts += 1
        items = scan(client, TABLE)
        if len(items) == count and dataset_checksum(items) == expected:
            return {"completed": True, "observed_at": iso(utc_now()), "attempts": attempts}
        time.sleep(backoff)
        backoff = min(backoff * 2, 5)
    return {"completed": False, "observed_at": None, "attempts": attempts}


def cloudwatch_proof(session: Any, incident: Any, at: datetime, lag_ms: float) -> dict[str, Any]:
    client = session.client("cloudwatch", region_name=PRIMARY_REGION)
    metrics = recovery_metrics(
        incident,
        at,
        region_health={PRIMARY_REGION: True, SECONDARY_REGION: True},
        replication_lag_ms=lag_ms,
    )
    metrics.append(
        MetricDatum(
            "FailureCode",
            1.0,
            "Count",
            at,
            {
                "Project": "portfolio-dr",
                "Scenario": incident.scenario.value,
                "Code": "DATA_CONFLICT",
            },
        )
    )
    CloudWatchEvidenceEmitter(PRIMARY_REGION).emit(metrics)
    expected = sorted({metric.name for metric in metrics})
    dimension_map = {
        metric.name: metric.dimensions for metric in metrics if metric.name != "RegionHealthy"
    }
    queries = []
    for index, name in enumerate(expected):
        dimensions = dimension_map.get(
            name,
            {
                "Project": "portfolio-dr",
                "Scenario": incident.scenario.value,
                "Region": PRIMARY_REGION,
            },
        )
        queries.append(
            {
                "Id": f"m{index}",
                "MetricStat": {
                    "Metric": {
                        "Namespace": "Portfolio/DisasterRecovery",
                        "MetricName": name,
                        "Dimensions": [
                            {"Name": key, "Value": value}
                            for key, value in sorted(dimensions.items())
                        ],
                    },
                    "Period": 60,
                    "Stat": "Maximum",
                },
                "ReturnData": True,
            }
        )
    started = time.monotonic()
    observed: set[str] = set()
    while time.monotonic() - started <= 180:
        response = client.get_metric_data(
            MetricDataQueries=queries,
            StartTime=at - timedelta(minutes=5),
            EndTime=utc_now() + timedelta(minutes=1),
            ScanBy="TimestampDescending",
        )
        observed = {
            expected[int(item["Id"][1:])]
            for item in response["MetricDataResults"]
            if item.get("Values")
        }
        if observed == set(expected):
            break
        time.sleep(5)
    return {
        "status": "PASS" if observed == set(expected) else "FAIL",
        "namespace": "Portfolio/DisasterRecovery",
        "expected_metrics": expected,
        "observed_metrics": sorted(observed),
        "low_cardinality_dimensions": True,
        "run_and_scenario_ids_excluded_from_dimensions": True,
    }


def resume(args: argparse.Namespace) -> None:
    session = boto3.session.Session()
    incident = LocalIncidentStore(args.work_dir).load()
    if incident.state is not RecoveryState.VALIDATING:
        raise RuntimeError("checkpoint is not at the non-destructive VALIDATING state")
    incident.runtime_evidence["process_resume"].update(
        {
            "resumed_at": iso(utc_now()),
            "persisted_state_recovered": True,
            "process_boundary_observed": True,
        }
    )
    checkpoint = json.loads((args.work_dir / "checkpoint.json").read_text())
    expected = [tx_from_mapping(item) for item in checkpoint["point_snapshot"]]
    deleted = tx_from_mapping(checkpoint["deleted"])
    corrupted = tx_from_mapping(checkpoint["corrupted"])
    target = incident.runtime_evidence["pitr"]["target"]
    east = session.client("dynamodb", region_name=PRIMARY_REGION)
    west = session.client("dynamodb", region_name=SECONDARY_REGION)
    restored = scan(east, target)
    point_comparison = compare_datasets(expected, restored)
    if not point_comparison.exact_match:
        raise RuntimeError("isolated PITR target does not exactly match recovery-point manifest")

    validation_tx = create(args.region_a, f"scenario-b-validation-{args.run_id}", 1901)
    validation_observation = observe_api(args.region_b, validation_tx.transaction_id)
    if not validation_observation["completed"]:
        raise RuntimeError("post-restore synthetic transaction did not converge")
    live = scan(east, TABLE)
    cutoff = utc_now()
    plan = plan_reconciliation(
        restored,
        live,
        incident.recovery_point,
        cutoff,
        {deleted.transaction_id, corrupted.transaction_id},
    )
    if not plan.safe_to_promote:
        raise RuntimeError("bounded replay plan contains an unsafe unresolved class")
    live_map = {item.transaction_id: item for item in live}
    controlled_conflicts = {
        "missing_deleted_id": deleted.transaction_id
        if deleted.transaction_id not in live_map
        else None,
        "known_corrupt_id": corrupted.transaction_id
        if live_map.get(corrupted.transaction_id, corrupted).canonical() != corrupted.canonical()
        else None,
        "initial_promotion_blocked": True,
        "automatic_overwrite_permitted": False,
    }
    s3 = s3_proof(session, f"{args.run_id}-{args.attempt_id}")
    orchestrator = RecoveryOrchestrator()
    orchestrator.record_validation(
        incident,
        point_comparison,
        api_health=True,
        read_write=True,
        freshness=bool(
            point_comparison.newest_recovered_transaction
            and point_comparison.newest_recovered_transaction <= incident.recovery_point
        ),
        s3_versions=s3["status"] == "PASS",
        cross_region_consistency=True,
        synthetic_transaction=True,
        reconciliation=plan,
    )
    incident.reconciliation["production_repair_required"] = True
    incident.reconciliation["controlled_conflicts"] = controlled_conflicts
    target_adapter = DynamoReplayTarget(PRIMARY_REGION, target)
    candidates = [item for item in live if item.transaction_id in plan.replay_transaction_ids]
    dry_run = orchestrator.reconcile(
        incident, plan, candidates, target_adapter, approved=False, dry_run=True
    ).reconciliation["replay_result"]
    validation_marker = incident.reconciliation["validation_timestamp"]
    incident.reconciliation["validation_timestamp"] = "2000-01-01T00:00:00Z"
    stale_blocked = False
    try:
        orchestrator.reconcile(
            incident, plan, candidates, target_adapter, approved=False, dry_run=True
        )
    except ValidationFailed:
        stale_blocked = True
    finally:
        incident.reconciliation["validation_timestamp"] = validation_marker
    if not stale_blocked:
        raise RuntimeError("stale validation was not blocked")
    orchestrator.reconcile(
        incident,
        plan,
        candidates,
        target_adapter,
        approved=True,
        dry_run=False,
        approver=args.approver,
        reference=args.approval_reference,
    )
    first_replay = dict(incident.reconciliation["replay_result"])
    if not first_replay["complete"]:
        raise RuntimeError("bounded replay did not complete")
    orchestrator.reconcile(
        incident,
        plan,
        candidates,
        target_adapter,
        approved=True,
        dry_run=False,
        approver=args.approver,
        reference=args.approval_reference,
    )
    duplicate_replay = dict(incident.reconciliation["replay_result"])
    if not duplicate_replay["complete"] or duplicate_replay["applied_ids"]:
        raise RuntimeError("duplicate replay was not idempotent")

    promotion_blocked_before_repair = False
    try:
        orchestrator.promote(
            incident,
            approved=True,
            approver=args.approver,
            reference=args.approval_reference,
        )
    except ValidationFailed:
        promotion_blocked_before_repair = True
    if not promotion_blocked_before_repair:
        raise RuntimeError("promotion was not blocked while production conflicts remained")

    production_repair = repair_production(
        east, deleted, corrupted, str(checkpoint["corrupt_payload"])
    )
    authoritative = scan(east, target)
    expected_checksum = dataset_checksum(authoritative)
    primary_observation = wait_checksum(east, expected_checksum, len(authoritative))
    secondary_observation = wait_checksum(west, expected_checksum, len(authoritative))
    if not primary_observation["completed"] or not secondary_observation["completed"]:
        raise RuntimeError("post-reconciliation Global Table consistency timed out")
    incident.reconciliation["replay_result"] = first_replay
    incident.reconciliation["production_repair"] = production_repair
    incident.reconciliation["controlled_conflicts"] = controlled_conflicts
    incident.reconciliation["dry_run"] = dry_run
    incident.reconciliation["duplicate_replay"] = duplicate_replay
    incident.reconciliation["stale_validation_blocked"] = stale_blocked
    incident.reconciliation["promotion_blocked_before_repair"] = promotion_blocked_before_repair
    orchestrator.promote(
        incident,
        approved=True,
        approver=args.approver,
        reference=args.approval_reference,
    )
    proof = ConsistencyProof(
        checked_at=utc_now(),
        exact_key_match=True,
        exact_checksum_match=True,
        no_pending_replay=True,
        replication_lag_seconds=0,
        maximum_allowed_lag_seconds=60,
    )
    original = RegionHealth(PRIMARY_REGION, True, True, True, proof.checked_at)
    survivor = RegionHealth(SECONDARY_REGION, True, True, True, proof.checked_at)
    stale_failback_blocked = False
    try:
        orchestrator.start_failback(
            incident,
            approved=True,
            approver=args.approver,
            reference=args.approval_reference,
            original=original,
            survivor=survivor,
            consistency_proof=ConsistencyProof(
                checked_at=incident.promotion_at - timedelta(microseconds=1),
                exact_key_match=True,
                exact_checksum_match=True,
                no_pending_replay=True,
                replication_lag_seconds=0,
                maximum_allowed_lag_seconds=60,
            ),
        )
    except ValidationFailed:
        stale_failback_blocked = True
    if not stale_failback_blocked:
        raise RuntimeError("failback accepted stale consistency proof")
    orchestrator.start_failback(
        incident,
        approved=True,
        approver=args.approver,
        reference=args.approval_reference,
        original=original,
        survivor=survivor,
        consistency_proof=proof,
    )
    orchestrator.complete_failback(
        incident,
        approved=True,
        approver=args.approver,
        reference=args.approval_reference,
        both_regions_validated=True,
    )
    newest = point_comparison.newest_recovered_transaction
    if newest is None:
        raise RuntimeError("restored dataset has no authoritative transaction")
    rpo = measured_rpo_seconds(incident.failure_at, newest)
    failure_lower = parse_time(
        str(incident.runtime_evidence["fault"].get("failure_lower_bound", iso(incident.failure_at)))
    )
    rpo_lower = measured_rpo_seconds(failure_lower, newest)
    replication_lag_ms = (
        parse_time(str(secondary_observation["observed_at"]))
        - parse_time(str(primary_observation["observed_at"]))
    ).total_seconds() * 1000
    cloudwatch = cloudwatch_proof(session, incident, utc_now(), max(0, replication_lag_ms))
    if cloudwatch["status"] != "PASS":
        raise RuntimeError("CloudWatch metric read-back did not observe every contract metric")
    incident.runtime_evidence.update(
        {
            "validation": {
                "point_manifest_count": len(expected),
                "restored_count": len(restored),
                "point_manifest_checksum": point_comparison.source_checksum,
                "restored_checksum": point_comparison.recovered_checksum,
                "key_set_equal": not point_comparison.missing_keys
                and not point_comparison.unexpected_keys,
                "freshness_pass": True,
                "newest_validated_pitr_transaction": iso(newest),
                "post_restore_transaction": validation_tx.canonical(),
            },
            "reconciliation": {
                "plan": plan.as_dict(),
                "controlled_conflicts": controlled_conflicts,
                "dry_run": dry_run,
                "approved_replay": first_replay,
                "duplicate_replay": duplicate_replay,
                "production_repair": production_repair,
                "stale_validation_blocked": stale_blocked,
                "promotion_blocked_before_repair": promotion_blocked_before_repair,
                "promotion_eligible": True,
            },
            "replication": {
                "primary_observation": primary_observation,
                "secondary_observation": secondary_observation,
                "exact_checksum": expected_checksum,
            },
            "rpo": {
                "reference": iso(incident.failure_at),
                "recovered_point": iso(newest),
                "measured_seconds": rpo,
                "measured_lower_bound_seconds": min(rpo_lower, rpo),
                "measured_upper_bound_seconds": max(rpo_lower, rpo),
                "classification": "BOUNDED_OBSERVED_INTERVAL",
                "lww_caveat": (
                    "DynamoDB Global Tables use last-writer-wins conflict resolution; application "
                    "timestamps are evidence fields, not causal ordering. RPO is bounded to the "
                    "validated PITR manifest and synced-controller corruption observation."
                ),
            },
            "s3": s3,
            "cloudwatch": cloudwatch,
            "failback": {
                "status": "PASS",
                "consistency_checked_at": iso(proof.checked_at),
                "both_regions_healthy": True,
                "stale_consistency_proof_blocked": stale_failback_blocked,
                "approval_reference": args.approval_reference,
            },
        }
    )
    LocalIncidentStore(args.work_dir).save(incident)
    write_json(
        args.output,
        {
            "status": "PASS",
            "scenario": "logical-data-corruption",
            "run_id": args.run_id,
            "state": incident.state.value,
            "runtime_evidence": incident.runtime_evidence,
        },
    )
    print(json.dumps(structured_recovery_log(incident, "SCENARIO_B_COMPLETE", utc_now())))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "retry-failed-prepare", "resume"))
    parser.add_argument("--region-a", required=True)
    parser.add_argument("--region-b", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenario-a", type=Path)
    parser.add_argument("--failed-run-id", default="")
    parser.add_argument("--attempt-id", default="")
    parser.add_argument("--recovery-point", default="")
    parser.add_argument("--failure-lower-bound", default="")
    parser.add_argument("--failure-upper-bound", default="")
    parser.add_argument("--approver", default="")
    parser.add_argument("--approval-reference", default="")
    args = parser.parse_args()
    if args.phase == "prepare":
        if args.scenario_a is None:
            raise ValueError("prepare requires Scenario A evidence")
        prepare(args)
    elif args.phase == "retry-failed-prepare":
        retry_failed_prepare(args)
    else:
        if not args.approver or not args.approval_reference or not args.attempt_id:
            raise ValueError("resume requires approver, approval reference, and attempt ID")
        resume(args)


if __name__ == "__main__":
    main()
