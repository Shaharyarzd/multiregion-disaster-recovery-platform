"""drctl command line interface."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict

from dr_platform.application import MemoryRepository, create_transaction, handler
from dr_platform.data_validation import Comparison, compare_datasets, measured_rpo_seconds
from dr_platform.errors import DrError
from dr_platform.evidence import build_report, write_report
from dr_platform.orchestrator import RecoveryOrchestrator
from dr_platform.reconciliation import (
    ConsistencyProof,
    ReconciliationPlan,
    plan_reconciliation,
)
from dr_platform.store import LocalIncidentStore
from dr_platform.types import RecoveryState, RegionHealth, Scenario, Transaction, utc_now


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="drctl", description="Safe DR recovery controller")
    result.add_argument("--state-dir", type=Path, default=Path(".drctl"))
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("validate")
    declare = commands.add_parser("declare")
    declare.add_argument("--scenario", choices=[item.value for item in Scenario], required=True)
    declare.add_argument("--failure-time", type=parse_time, required=True)
    declare.add_argument("--affected-region")
    recovery = commands.add_parser("recover-data")
    recovery.add_argument("--recovery-point", type=parse_time)
    commands.add_parser("validate-recovery")
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("--dry-run", action="store_true")
    reconcile.add_argument("--approve", action="store_true")
    reconcile.add_argument("--approver")
    reconcile.add_argument("--reference")
    promote = commands.add_parser("promote")
    promote.add_argument("--approve", action="store_true")
    promote.add_argument("--approver", required=True)
    promote.add_argument("--reference", required=True)
    failback = commands.add_parser("failback")
    failback.add_argument("--approve", action="store_true")
    failback.add_argument("--approver", required=True)
    failback.add_argument("--reference", required=True)
    failback.add_argument("--phase", choices=["start", "complete"], required=True)
    failback.add_argument("--both-regions-healthy", action="store_true")
    failback.add_argument("--data-consistent", action="store_true")
    report = commands.add_parser("report")
    report.add_argument("--output", type=Path, default=Path("evidence/recovery-report.json"))
    return result


def synthetic_transactions(now: datetime) -> list[Transaction]:
    return [
        Transaction(
            f"txn-{index:03}",
            now - timedelta(seconds=3 - index),
            "region-a",
            index * 100,
            f"order-{index}",
        )
        for index in range(1, 4)
    ]


class LocalValidationInputs(TypedDict):
    comparison: Comparison
    api_health: bool
    read_write: bool
    freshness: bool
    s3_versions: bool
    cross_region_consistency: bool
    synthetic_transaction: bool
    reconciliation: ReconciliationPlan


class LocalReplayTarget:
    def __init__(self, transactions: list[Transaction]) -> None:
        self.items = {item.transaction_id: item for item in transactions}

    def get(self, transaction_id: str) -> Transaction | None:
        return self.items.get(transaction_id)

    def put_if_absent(self, transaction: Transaction) -> bool:
        if transaction.transaction_id in self.items:
            return False
        self.items[transaction.transaction_id] = transaction
        return True


def local_validation_inputs(
    incident_failure_at: datetime, recovery_point: datetime
) -> LocalValidationInputs:
    """Execute local equivalents of the cloud validation gates.

    AWS execution replaces these fixtures with adapter results; evidence scope prevents
    local results from being represented as cloud observations.
    """
    repository = MemoryRepository()
    health_response = handler(
        {"requestContext": {"http": {"method": "GET"}}, "rawPath": "/health"},
        None,
        repository,
    )
    created = create_transaction(
        repository,
        seed="post-recovery-proof",
        timestamp=utc_now(),
        region="local-region-a",
        amount_cents=123,
    )
    read_back = repository.get(created["transaction_id"])

    root = Path(__file__).resolve().parents[2]
    manifest = json.loads(
        (root / "examples/supporting-data/manifest.json").read_text(encoding="utf-8")
    )
    manifest_item = manifest["objects"][0]
    object_path = root / "examples" / manifest_item["key"]
    object_checksum = hashlib.sha256(object_path.read_bytes()).hexdigest()
    s3_fixture_valid = object_checksum == manifest_item["sha256"]

    live = synthetic_transactions(incident_failure_at)
    expected = [item for item in live if item.timestamp <= recovery_point]
    recovered = list(expected)
    comparison = compare_datasets(expected, recovered)
    replica_comparison = compare_datasets(recovered, list(recovered))
    reconciliation = plan_reconciliation(
        recovered, live, recovery_point, incident_failure_at, set()
    )
    newest = reconciliation.newest_authoritative_transaction
    freshness = bool(newest and measured_rpo_seconds(incident_failure_at, newest) <= 60)
    return {
        "comparison": comparison,
        "api_health": health_response["statusCode"] == 200,
        "read_write": read_back == created,
        "freshness": freshness,
        "s3_versions": s3_fixture_valid,
        "cross_region_consistency": replica_comparison.exact_match,
        "synthetic_transaction": read_back is not None,
        "reconciliation": reconciliation,
    }


def execute(args: argparse.Namespace) -> dict[str, object]:
    store = LocalIncidentStore(args.state_dir)
    orchestrator = RecoveryOrchestrator()
    if args.command == "declare":
        incident = orchestrator.declare(
            Scenario(args.scenario), args.failure_time, args.affected_region
        )
        store.save(incident)
        return {"incident_id": incident.incident_id, "state": incident.state.value}
    incident = store.load()
    if args.command == "status":
        return build_report(incident)
    if args.command == "validate":
        return {"state_file": str(store.path), "readable": True, "state": incident.state.value}
    if args.command == "recover-data":
        orchestrator.start_recovery(incident, args.recovery_point)
    elif args.command == "validate-recovery":
        if incident.state is RecoveryState.RECOVERY_IN_PROGRESS:
            orchestrator.begin_validation(
                incident,
                utc_now(),
                restore_configuration={
                    "table_active": True,
                    "encryption_verified": True,
                    "pitr_enabled": True,
                    "tags_verified": True,
                    "ttl_verified": True,
                    "stream_verified": True,
                    "no_replicas": True,
                    "deletion_protection": True,
                    "ready_for_validation": True,
                },
            )
        if incident.recovery_point is None:
            raise DrError("Recovery point is required before validation")
        validation = local_validation_inputs(incident.failure_at, incident.recovery_point)
        orchestrator.record_validation(incident, **validation)
    elif args.command == "reconcile":
        if incident.recovery_point is None:
            raise DrError("Recovery point is required before reconciliation")
        live = synthetic_transactions(incident.failure_at)
        restored = [item for item in live if item.timestamp <= incident.recovery_point]
        plan = plan_reconciliation(
            restored, live, incident.recovery_point, incident.failure_at, set()
        )
        orchestrator.reconcile(
            incident,
            plan,
            live,
            LocalReplayTarget(restored),
            approved=args.approve,
            dry_run=args.dry_run,
            approver=args.approver,
            reference=args.reference,
        )
    elif args.command == "promote":
        orchestrator.promote(
            incident,
            approved=args.approve,
            approver=args.approver,
            reference=args.reference,
        )
    elif args.command == "failback":
        health = RegionHealth(
            "both-regions",
            args.both_regions_healthy,
            args.both_regions_healthy,
            args.both_regions_healthy,
            utc_now(),
            "operator-supplied local simulation evidence",
        )
        if args.phase == "start":
            proof = ConsistencyProof(
                checked_at=utc_now(),
                exact_key_match=args.data_consistent,
                exact_checksum_match=args.data_consistent,
                no_pending_replay=args.data_consistent,
                replication_lag_seconds=0.0,
                maximum_allowed_lag_seconds=5.0,
            )
            orchestrator.start_failback(
                incident,
                approved=args.approve,
                approver=args.approver,
                reference=args.reference,
                original=health,
                survivor=health,
                consistency_proof=proof,
            )
        else:
            orchestrator.complete_failback(
                incident,
                approved=args.approve,
                approver=args.approver,
                reference=args.reference,
                both_regions_validated=(args.both_regions_healthy and args.data_consistent),
            )
    elif args.command == "report":
        return write_report(incident, args.output)
    store.save(incident)
    return {"incident_id": incident.incident_id, "state": incident.state.value}


def main() -> None:
    args = parser().parse_args()
    try:
        print(json.dumps(execute(args), indent=2, sort_keys=True))
    except (DrError, FileNotFoundError, ValueError) as error:
        print(json.dumps({"error": str(error)}, sort_keys=True), file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
