from __future__ import annotations

from datetime import UTC, datetime, timedelta

from dr_platform.evidence import build_report, redact
from dr_platform.types import RecoveryState, Scenario, ValidationResult


def test_report_calculates_measured_values(declared) -> None:
    _, incident = declared
    incident.state = RecoveryState.RECOVERY_ACTIVE
    incident.promotion_at = incident.declared_at + timedelta(seconds=42)
    incident.validation = ValidationResult(
        api_health=True,
        read_write=True,
        record_count=True,
        expected_keys=True,
        checksum=True,
        freshness=True,
        s3_versions=True,
        cross_region_consistency=True,
        synthetic_transaction=True,
        details={"newest_recovered_transaction": "2025-12-31T23:59:55Z"},
    )
    report = build_report(incident)
    assert report["measured_rto_seconds"] == 42
    assert report["measured_rpo_seconds"] == 5
    assert report["validation_result"] == "PASS"


def test_report_never_manufactures_pass(declared) -> None:
    _, incident = declared
    assert build_report(incident)["validation_result"] == "NOT_PASSED"


def test_redaction_is_recursive() -> None:
    result = redact({"token": "abc", "nested": [{"password": "bad"}], "safe": "ok"})
    assert result == {
        "token": "[REDACTED]",
        "nested": [{"password": "[REDACTED]"}],
        "safe": "ok",
    }


def test_incident_synthetic_defaults(declared) -> None:
    _, incident = declared
    assert incident.scenario is Scenario.LOGICAL_CORRUPTION
    assert incident.failure_at == datetime(2026, 1, 1, tzinfo=UTC)
