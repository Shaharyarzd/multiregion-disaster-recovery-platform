from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dr_platform.errors import ApprovalRequired, ValidationFailed
from dr_platform.types import RecoveryState, RegionHealth


def regional(region: str, ok: bool) -> RegionHealth:
    return RegionHealth(region, ok, ok, ok, datetime.now(UTC))


def promoted_incident(declared, comparison):
    orchestrator, incident = declared
    orchestrator.start_recovery(incident)
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
    )
    orchestrator.promote(incident, approved=True, approver="owner", reference="CHG-1")
    return orchestrator, incident


def test_failback_blocks_stale_region(declared, comparison) -> None:
    orchestrator, incident = promoted_incident(declared, comparison)
    with pytest.raises(ValidationFailed):
        orchestrator.start_failback(
            incident,
            approved=True,
            approver="owner",
            reference="CHG-2",
            original=regional("a", False),
            survivor=regional("b", True),
            data_consistent=True,
        )


def test_failback_requires_approval(declared, comparison) -> None:
    orchestrator, incident = promoted_incident(declared, comparison)
    with pytest.raises(ApprovalRequired):
        orchestrator.start_failback(
            incident,
            approved=False,
            approver="owner",
            reference="CHG-2",
            original=regional("a", True),
            survivor=regional("b", True),
            data_consistent=True,
        )


def test_controlled_failback_returns_healthy(declared, comparison) -> None:
    orchestrator, incident = promoted_incident(declared, comparison)
    orchestrator.start_failback(
        incident,
        approved=True,
        approver="owner",
        reference="CHG-2",
        original=regional("a", True),
        survivor=regional("b", True),
        data_consistent=True,
    )
    orchestrator.complete_failback(
        incident,
        approved=True,
        approver="owner",
        reference="CHG-3",
        both_regions_validated=True,
    )
    assert incident.state is RecoveryState.HEALTHY
