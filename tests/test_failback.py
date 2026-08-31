from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dr_platform.errors import ApprovalRequired, ValidationFailed
from dr_platform.reconciliation import ConsistencyProof
from dr_platform.types import RecoveryState, RegionHealth


def regional(region: str, ok: bool) -> RegionHealth:
    return RegionHealth(region, ok, ok, ok, datetime.now(UTC))


def proof(incident, passed: bool = True, before_promotion: bool = False) -> ConsistencyProof:
    checked_at = incident.declared_at if before_promotion else datetime.now(UTC)
    return ConsistencyProof(checked_at, passed, passed, passed, 0.0, 5.0)


def promoted_incident(declared, comparison, reconciliation):
    orchestrator, incident = declared
    orchestrator.start_recovery(incident, reconciliation.recovery_point)
    orchestrator.begin_validation(incident, orchestrator.clock())
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
    orchestrator.promote(incident, approved=True, approver="owner", reference="CHG-1")
    return orchestrator, incident


def test_failback_blocks_stale_region(declared, comparison, reconciliation) -> None:
    orchestrator, incident = promoted_incident(declared, comparison, reconciliation)
    with pytest.raises(ValidationFailed):
        orchestrator.start_failback(
            incident,
            approved=True,
            approver="owner",
            reference="CHG-2",
            original=regional("a", False),
            survivor=regional("b", True),
            consistency_proof=proof(incident),
        )


def test_failback_requires_approval(declared, comparison, reconciliation) -> None:
    orchestrator, incident = promoted_incident(declared, comparison, reconciliation)
    with pytest.raises(ApprovalRequired):
        orchestrator.start_failback(
            incident,
            approved=False,
            approver="owner",
            reference="CHG-2",
            original=regional("a", True),
            survivor=regional("b", True),
            consistency_proof=proof(incident),
        )


def test_controlled_failback_returns_healthy(declared, comparison, reconciliation) -> None:
    orchestrator, incident = promoted_incident(declared, comparison, reconciliation)
    orchestrator.start_failback(
        incident,
        approved=True,
        approver="owner",
        reference="CHG-2",
        original=regional("a", True),
        survivor=regional("b", True),
        consistency_proof=proof(incident),
    )
    orchestrator.complete_failback(
        incident,
        approved=True,
        approver="owner",
        reference="CHG-3",
        both_regions_validated=True,
    )
    assert incident.state is RecoveryState.HEALTHY


def test_failback_rejects_consistency_proof_older_than_promotion(
    declared, comparison, reconciliation
) -> None:
    orchestrator, incident = promoted_incident(declared, comparison, reconciliation)
    with pytest.raises(ValidationFailed):
        orchestrator.start_failback(
            incident,
            approved=True,
            approver="owner",
            reference="CHG-STALE",
            original=regional("a", True),
            survivor=regional("b", True),
            consistency_proof=proof(incident, before_promotion=True),
        )
