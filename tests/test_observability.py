from __future__ import annotations

from dr_platform.observability import recovery_metrics, structured_recovery_log


def test_metric_envelopes_cover_required_runtime_signals(declared) -> None:
    orchestrator, incident = declared
    at = orchestrator.clock()
    metrics = recovery_metrics(
        incident,
        at,
        region_health={"region-a": False, "region-b": True},
        replication_lag_ms=850,
        failure_reason_code="REGION_ENDPOINT_UNAVAILABLE",
    )
    names = {metric.name for metric in metrics}
    assert {
        "RecoveryState",
        "RecoveryValidationPassed",
        "RegionHealthy",
        "ReplicationLatencyMilliseconds",
        "RecoveryFailure",
    }.issubset(names)
    assert all(metric.dimensions["RunId"] == incident.run_id for metric in metrics)


def test_structured_log_has_correlation_and_scope(declared) -> None:
    orchestrator, incident = declared
    event = structured_recovery_log(
        incident, "RESTORE_STARTED", orchestrator.clock(), target="isolated-table"
    )
    assert event["run_id"] == incident.run_id
    assert event["scenario_id"] == incident.scenario_id
    assert event["evidence_scope"] == "LOCAL_SIMULATION"
