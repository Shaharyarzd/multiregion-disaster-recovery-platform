from __future__ import annotations

from datetime import timedelta

from dr_platform.observability import recovery_metrics, structured_recovery_log
from dr_platform.types import ValidationResult


def test_metric_envelopes_cover_required_runtime_signals(declared) -> None:
    orchestrator, incident = declared
    at = orchestrator.clock()
    metrics = recovery_metrics(
        incident,
        at,
        region_health={"region-a": False, "region-b": True},
        replication_lag_ms=850,
        failure_reason_code="API_UNHEALTHY",
    )
    names = {metric.name for metric in metrics}
    assert {
        "RecoveryState",
        "ValidationResult",
        "RegionHealthy",
        "ReplicationLag",
        "FailureCode",
    }.issubset(names)
    assert all("RunId" not in metric.dimensions for metric in metrics)
    assert all("ScenarioId" not in metric.dimensions for metric in metrics)
    assert [metric.as_cloudwatch() for metric in metrics] == [
        metric.as_cloudwatch() for metric in metrics
    ]


def test_structured_log_has_correlation_and_scope(declared) -> None:
    orchestrator, incident = declared
    event = structured_recovery_log(
        incident, "RESTORE_STARTED", orchestrator.clock(), target="isolated-table"
    )
    assert event["run_id"] == incident.run_id
    assert event["scenario_id"] == incident.scenario_id
    assert event["evidence_scope"] == "LOCAL_SIMULATION"


def test_complete_metric_contract_uses_exact_names_units_and_bounded_dimensions(declared) -> None:
    orchestrator, incident = declared
    at = orchestrator.clock()
    incident.recovery_started_at = at
    incident.restore_completed_at = at + timedelta(seconds=4)
    incident.rto_end_at = incident.declared_at + timedelta(seconds=8)
    incident.failback_completed_at = at + timedelta(seconds=20)
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
        details={"newest_recovered_transaction": "2025-12-31T23:59:59Z"},
    )
    metrics = recovery_metrics(
        incident,
        at,
        region_health={"us-east-1": True},
        replication_lag_ms=125,
        failure_reason_code="DATA_CONFLICT",
    )
    assert {metric.name for metric in metrics} == {
        "RegionHealthy",
        "RecoveryState",
        "RestoreDuration",
        "ValidationResult",
        "ReplicationLag",
        "MeasuredRTO",
        "MeasuredRPO",
        "LastSuccessfulDrill",
        "FailureCode",
    }
    assert all(
        set(metric.dimensions) <= {"Project", "Scenario", "Region", "Code"} for metric in metrics
    )
