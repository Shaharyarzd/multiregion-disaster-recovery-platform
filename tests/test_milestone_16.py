from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest

from dr_platform.aws_adapters import EvidenceArchiveAdapter
from dr_platform.cleanup import CleanupResource, cleanup_plan
from dr_platform.errors import ApprovalRequired, ValidationFailed
from dr_platform.evidence import build_report, verify_report
from dr_platform.iam_contract import load_contract, permits, validate_contract
from dr_platform.integrity import canonical_json
from dr_platform.local_crypto import LocalEcdsaEvidenceKey
from dr_platform.reconciliation import execute_replay, plan_reconciliation
from dr_platform.replication import (
    dynamodb_marker_matches,
    measure_convergence,
    s3_replica_matches,
)
from dr_platform.types import Transaction

ROOT = Path(__file__).resolve().parents[1]
POINT = datetime(2026, 1, 1, tzinfo=UTC)


def tx(key: str, offset: int, payload: str = "ok") -> Transaction:
    return Transaction(key, POINT + timedelta(seconds=offset), "region-a", 100, payload)


class Target:
    def __init__(
        self, items: list[Transaction] | None = None, fail: set[str] | None = None
    ) -> None:
        self.items = {item.transaction_id: item for item in items or []}
        self.fail = fail or set()

    def get(self, transaction_id: str) -> Transaction | None:
        return self.items.get(transaction_id)

    def put_if_absent(self, transaction: Transaction) -> bool:
        if transaction.transaction_id in self.fail:
            raise RuntimeError("synthetic partial failure")
        if transaction.transaction_id in self.items:
            return False
        self.items[transaction.transaction_id] = transaction
        return True


def replay_plan():
    base, one, two = tx("base", -1), tx("one", 1), tx("two", 2)
    plan = plan_reconciliation([base], [base, one, two], POINT, POINT + timedelta(seconds=3), set())
    return plan, [one, two]


def test_replay_is_bounded_approved_idempotent_and_retryable() -> None:
    plan, candidates = replay_plan()
    target = Target(fail={"two"})
    first = execute_replay(
        plan,
        candidates,
        target,
        validation_timestamp=POINT,
        executed_at=POINT + timedelta(seconds=1),
        approved=True,
        approver="owner",
        approval_reference="DR-1",
        dry_run=False,
    )
    assert first.applied_ids == ("one",)
    assert first.failed_ids == ("two",)
    assert not first.complete
    target.fail.clear()
    retry = execute_replay(
        plan,
        candidates,
        target,
        validation_timestamp=POINT,
        executed_at=POINT + timedelta(seconds=2),
        approved=True,
        approver="owner",
        approval_reference="DR-1-RETRY",
        dry_run=False,
    )
    assert retry.idempotent_ids == ("one",)
    assert retry.applied_ids == ("two",)
    assert retry.complete


def test_replay_conflict_and_missing_candidate_block_completion() -> None:
    plan, candidates = replay_plan()
    conflict = tx("one", 1, "different")
    result = execute_replay(
        plan,
        candidates[:1],
        Target([conflict]),
        validation_timestamp=POINT,
        executed_at=POINT,
        approved=True,
        approver="owner",
        approval_reference="DR-2",
        dry_run=False,
    )
    assert result.conflict_ids == ("one",)
    assert result.failed_ids == ("two",)
    assert not result.complete
    with pytest.raises(ValueError, match="approver"):
        execute_replay(
            plan,
            candidates,
            Target(),
            validation_timestamp=POINT,
            executed_at=POINT,
            approved=True,
            dry_run=False,
        )


def test_orchestrator_blocks_unconfigured_restore_and_stale_replay(
    declared, comparison, reconciliation
) -> None:
    orchestrator, incident = declared
    orchestrator.start_recovery(incident, reconciliation.recovery_point)
    with pytest.raises(ValidationFailed, match="configuration"):
        orchestrator.begin_validation(incident, orchestrator.clock())
    orchestrator.begin_validation(
        incident,
        orchestrator.clock(),
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
    orchestrator.record_validation(
        incident,
        comparison,
        api_health=True,
        read_write=True,
        freshness=True,
        s3_versions=True,
        cross_region_consistency=True,
        synthetic_transaction=True,
        reconciliation=reconciliation,
    )
    assert incident.reconciliation is not None
    incident.reconciliation["validation_timestamp"] = "2020-01-01T00:00:00Z"
    with pytest.raises(ValidationFailed, match="stale"):
        orchestrator.reconcile(incident, reconciliation, [], Target(), approved=False, dry_run=True)
    with pytest.raises(ApprovalRequired):
        incident.reconciliation["validation_timestamp"] = (
            incident.validation_completed_at.isoformat().replace("+00:00", "Z")
        )
        orchestrator.reconcile(
            incident, reconciliation, [], Target(), approved=False, dry_run=False
        )


def test_local_ecdsa_signing_matches_evidence_digest_contract(declared) -> None:
    _, incident = declared
    key = LocalEcdsaEvidenceKey()
    report = build_report(incident, key)
    assert verify_report(report, key)
    report["scenario_id"] = "forged"
    assert not verify_report(report, key)


def test_archived_exact_version_is_read_back_and_verified(declared) -> None:
    _, incident = declared
    key = LocalEcdsaEvidenceKey()
    report = build_report(incident, key)

    class ArchiveClient:
        def get_object(self, **kwargs):
            assert kwargs["VersionId"] == "v1"
            return {"Body": BytesIO(canonical_json(report) + b"\n")}

        def get_object_retention(self, **_kwargs):
            return {"Retention": {"Mode": "GOVERNANCE", "RetainUntilDate": POINT}}

    adapter = object.__new__(EvidenceArchiveAdapter)
    adapter.client = ArchiveClient()
    assert adapter.verify_archived_report("bucket", "key", "v1", key)


def test_iam_contract_positive_negative_and_no_admin_wildcards() -> None:
    contract = load_contract(ROOT / "config/iam-action-paths.json")
    assert validate_contract(contract) == []
    assert permits(contract, "recovery", "dynamodb:RestoreTableToPointInTime", "portfolio-dr-table")
    assert permits(contract, "evidence", "kms:Verify", "portfolio-dr-signing")
    assert not permits(
        contract, "deploy", "dynamodb:RestoreTableToPointInTime", "portfolio-dr-table"
    )
    assert not permits(contract, "recovery", "dynamodb:DeleteTable", "unrelated-table")
    assert not permits(
        contract, "evidence", "s3:BypassGovernanceRetention", "portfolio-dr-evidence"
    )
    terraform_policy = (ROOT / "terraform/modules/github-oidc/main.tf").read_text()
    for body in contract["roles"].values():
        for action in body["actions"]:
            assert f'"{action}"' in terraform_policy
    assert "dynamodb:*:${data.aws_caller_identity.current.account_id}" in terraform_policy
    assert "kms:*:${data.aws_caller_identity.current.account_id}" in terraform_policy
    assert '"kms:*"' not in terraform_policy
    assert "aws:RequestTag/Project" in terraform_policy
    assert "aws:ResourceTag/Project" in terraform_policy
    assert "kms:GrantIsForAWSResource" in terraform_policy


def test_preflight_manifest_is_machine_checkable() -> None:
    preflight = json.loads((ROOT / "config/aws-preflight.json").read_text())
    allowed = set(preflight["allowed_statuses"])
    ids = [check["id"] for check in preflight["checks"]]
    assert len(ids) == len(set(ids))
    assert all(check["status"] in allowed and check["proof"] for check in preflight["checks"])


def test_replication_convergence_uses_observation_and_timeout() -> None:
    times = iter([POINT, POINT, POINT + timedelta(seconds=1), POINT + timedelta(seconds=1)])
    observations = iter([False, True])
    result = measure_convergence(
        system="DYNAMODB",
        marker_id="marker",
        source_write_at=POINT,
        observe=lambda: next(observations),
        clock=lambda: next(times),
        wait=lambda _delay: None,
        timeout_seconds=10,
    )
    assert result.completed and result.measured_lag_ms == 1000
    assert dynamodb_marker_matches({"transaction_id": "marker", "digest": "abc"}, "marker", "abc")
    assert s3_replica_matches(
        {
            "ReplicationStatus": "REPLICA",
            "Metadata": {"marker-id": "marker", "sha256": "abc"},
        },
        "marker",
        "abc",
    )


def test_cleanup_order_regions_shared_and_object_lock_guards() -> None:
    later = POINT + timedelta(days=7)
    plan = cleanup_plan(
        [
            CleanupResource("evidence-v1", "evidence-archive", "us-east-1", locked_until=later),
            CleanupResource("evidence-kms", "kms", "us-east-1", protects_evidence=True),
            CleanupResource("recovery-table", "temporary-recovery", "us-west-2"),
            CleanupResource("replication-rule", "replication-relationships", "us-east-1"),
        ],
        allowed_regions={"us-east-1", "us-west-2"},
        now=POINT,
    )
    assert plan.delete == ("recovery-table", "replication-rule")
    assert plan.retain == ("evidence-kms", "evidence-v1")
    with pytest.raises(ValueError, match="outside"):
        cleanup_plan(
            [CleanupResource("wrong", "dynamodb", "eu-west-1")],
            allowed_regions={"us-east-1"},
            now=POINT,
        )
    with pytest.raises(ValueError, match="shared"):
        cleanup_plan(
            [CleanupResource("bootstrap", "kms", "us-east-1", shared=True)],
            allowed_regions={"us-east-1"},
            now=POINT,
        )
    with pytest.raises(ValueError, match="replication-detach"):
        cleanup_plan(
            [CleanupResource("replica-bucket", "s3-data", "us-east-1", replicated=True)],
            allowed_regions={"us-east-1"},
            now=POINT,
        )
