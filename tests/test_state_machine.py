from __future__ import annotations

import pytest

from dr_platform.errors import ApprovalRequired, InvalidTransition, ValidationFailed
from dr_platform.types import RecoveryState, ValidationResult


def proof() -> dict[str, bool]:
    return {
        "table_active": True,
        "encryption_verified": True,
        "pitr_enabled": True,
        "tags_verified": True,
        "ttl_verified": True,
        "stream_verified": True,
        "no_replicas": True,
        "deletion_protection": True,
        "ready_for_validation": True,
    }


def test_happy_path_reaches_approval(declared, comparison, reconciliation) -> None:
    orchestrator, incident = declared
    orchestrator.start_recovery(incident, reconciliation.recovery_point)
    orchestrator.begin_validation(incident, orchestrator.clock(), restore_configuration=proof())
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
    assert incident.state is RecoveryState.AWAITING_APPROVAL


def test_cannot_skip_recovery(declared) -> None:
    orchestrator, incident = declared
    with pytest.raises(InvalidTransition):
        orchestrator.machine.transition(incident, RecoveryState.RECOVERY_ACTIVE, approved=True)


def test_validation_failure_blocks_gate(declared, comparison, reconciliation) -> None:
    orchestrator, incident = declared
    orchestrator.start_recovery(incident, reconciliation.recovery_point)
    orchestrator.begin_validation(incident, orchestrator.clock(), restore_configuration=proof())
    with pytest.raises(ValidationFailed):
        orchestrator.record_validation(
            incident,
            comparison,
            api_health=True,
            read_write=True,
            freshness=False,
            s3_versions=True,
            cross_region_consistency=True,
            synthetic_transaction=True,
            reconciliation=reconciliation,
        )
    assert incident.state is RecoveryState.VALIDATING


def test_approval_gate_rejects_incomplete_validation(declared, reconciliation) -> None:
    orchestrator, incident = declared
    orchestrator.start_recovery(incident, reconciliation.recovery_point)
    orchestrator.begin_validation(incident, orchestrator.clock(), restore_configuration=proof())
    incident.validation = ValidationResult(api_health=True)
    with pytest.raises(ValidationFailed):
        orchestrator.machine.transition(incident, RecoveryState.AWAITING_APPROVAL)


def test_promotion_needs_external_approval(declared, comparison, reconciliation) -> None:
    orchestrator, incident = declared
    orchestrator.start_recovery(incident, reconciliation.recovery_point)
    orchestrator.begin_validation(incident, orchestrator.clock(), restore_configuration=proof())
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
    with pytest.raises(ApprovalRequired):
        orchestrator.promote(incident, approved=False, approver="owner", reference="CHG-1")
