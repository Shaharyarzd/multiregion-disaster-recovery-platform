from __future__ import annotations

from argparse import Namespace
from datetime import UTC, datetime, timedelta

import pytest

from dr_platform.cli import execute, parse_time, parser
from dr_platform.errors import DrError
from dr_platform.store import LocalIncidentStore
from dr_platform.types import RecoveryState, Scenario


def args(state_dir, command: str, **values) -> Namespace:
    return Namespace(state_dir=state_dir, command=command, **values)


def test_parse_time_normalizes_naive_timestamp() -> None:
    assert parse_time("2026-01-01T00:00:00").tzinfo is UTC


def test_parser_exposes_expected_commands() -> None:
    parsed = parser().parse_args(["status"])
    assert parsed.command == "status"


def test_cli_drill_and_store_round_trip(tmp_path) -> None:
    failure = datetime(2026, 1, 1, tzinfo=UTC)
    declared = execute(
        args(
            tmp_path,
            "declare",
            scenario=Scenario.LOGICAL_CORRUPTION.value,
            failure_time=failure,
            affected_region="us-east-1",
        )
    )
    assert declared["state"] == RecoveryState.INCIDENT_DECLARED.value
    assert execute(args(tmp_path, "validate"))["readable"] is True

    execute(args(tmp_path, "recover-data", recovery_point=failure - timedelta(seconds=1)))
    execute(args(tmp_path, "validate-recovery"))
    execute(
        args(
            tmp_path,
            "reconcile",
            dry_run=False,
            approve=True,
            approver="owner",
            reference="LOCAL-REPLAY",
        )
    )
    promoted = execute(
        args(
            tmp_path,
            "promote",
            approve=True,
            approver="owner",
            reference="LOCAL-TEST",
        )
    )
    assert promoted["state"] == RecoveryState.RECOVERY_ACTIVE.value

    output = tmp_path / "report.json"
    report = execute(args(tmp_path, "report", output=output))
    assert report["validation_result"] == "PASS"
    assert output.exists()
    assert execute(args(tmp_path, "status"))["state"] == RecoveryState.RECOVERY_ACTIVE.value

    loaded = LocalIncidentStore(tmp_path).load()
    assert loaded.validation is not None and loaded.validation.passed


def test_cli_failback_requires_current_health(tmp_path) -> None:
    execute(
        args(
            tmp_path,
            "declare",
            scenario=Scenario.REGIONAL_OUTAGE.value,
            failure_time=datetime.now(UTC),
            affected_region="us-east-1",
        )
    )
    incident = LocalIncidentStore(tmp_path).load()
    incident.state = RecoveryState.RECOVERY_ACTIVE
    LocalIncidentStore(tmp_path).save(incident)
    with pytest.raises(DrError, match="health or data consistency"):
        execute(
            args(
                tmp_path,
                "failback",
                approve=True,
                approver="owner",
                reference="LOCAL-TEST",
                phase="start",
                both_regions_healthy=False,
                data_consistent=True,
            )
        )


def test_cli_completes_controlled_failback(tmp_path) -> None:
    failure = datetime(2026, 1, 1, tzinfo=UTC)
    execute(
        args(
            tmp_path,
            "declare",
            scenario=Scenario.LOGICAL_CORRUPTION.value,
            failure_time=failure,
            affected_region="us-east-1",
        )
    )
    execute(args(tmp_path, "recover-data", recovery_point=failure - timedelta(seconds=1)))
    execute(args(tmp_path, "validate-recovery"))
    execute(
        args(
            tmp_path,
            "reconcile",
            dry_run=False,
            approve=True,
            approver="owner",
            reference="LOCAL-REPLAY",
        )
    )
    execute(
        args(
            tmp_path,
            "promote",
            approve=True,
            approver="owner",
            reference="LOCAL-PROMOTE",
        )
    )
    common = {
        "approve": True,
        "approver": "owner",
        "both_regions_healthy": True,
        "data_consistent": True,
    }
    execute(args(tmp_path, "failback", phase="start", reference="LOCAL-FB-1", **common))
    result = execute(args(tmp_path, "failback", phase="complete", reference="LOCAL-FB-2", **common))
    assert result["state"] == RecoveryState.HEALTHY.value
