"""Deterministic CloudWatch metric and structured-log envelopes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dr_platform.evidence import build_report
from dr_platform.types import Incident, iso

NAMESPACE = "Portfolio/DisasterRecovery"


@dataclass(frozen=True)
class MetricDatum:
    name: str
    value: float
    unit: str
    timestamp: datetime
    dimensions: dict[str, str]

    def as_cloudwatch(self) -> dict[str, Any]:
        return {
            "MetricName": self.name,
            "Value": self.value,
            "Unit": self.unit,
            "Timestamp": self.timestamp,
            "Dimensions": [
                {"Name": key, "Value": value} for key, value in sorted(self.dimensions.items())
            ],
        }


def recovery_metrics(
    incident: Incident,
    at: datetime,
    *,
    region_health: dict[str, bool],
    replication_lag_ms: float | None = None,
    failure_reason_code: str | None = None,
) -> list[MetricDatum]:
    report = build_report(incident)
    common = {"RunId": incident.run_id, "ScenarioId": incident.scenario_id}
    metrics = [
        MetricDatum(
            "RecoveryState",
            float(list(type(incident.state)).index(incident.state)),
            "None",
            at,
            common,
        ),
        MetricDatum(
            "RecoveryValidationPassed",
            1.0 if report["validation_result"] == "PASS" else 0.0,
            "Count",
            at,
            common,
        ),
    ]
    for region, healthy in sorted(region_health.items()):
        metrics.append(
            MetricDatum("RegionHealthy", float(healthy), "Count", at, {**common, "Region": region})
        )
    optional = {
        "RestoreDurationSeconds": (
            (incident.restore_completed_at - incident.recovery_started_at).total_seconds()
            if incident.restore_completed_at and incident.recovery_started_at
            else None
        ),
        "MeasuredRTOSeconds": report["measurements"]["rto"]["derived_seconds"],
        "MeasuredRPOSeconds": report["measurements"]["rpo"]["derived_seconds"],
        "LastSuccessfulDrDrillEpoch": (
            incident.failback_completed_at.timestamp() if incident.failback_completed_at else None
        ),
        "ReplicationLatencyMilliseconds": replication_lag_ms,
    }
    for name, value in optional.items():
        if value is not None:
            unit = "Milliseconds" if name.endswith("Milliseconds") else "Seconds"
            metrics.append(MetricDatum(name, float(value), unit, at, common))
    if failure_reason_code:
        metrics.append(
            MetricDatum(
                "RecoveryFailure",
                1.0,
                "Count",
                at,
                {**common, "ReasonCode": failure_reason_code},
            )
        )
    return metrics


def structured_recovery_log(
    incident: Incident, event: str, at: datetime, **details: object
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "event": event,
        "timestamp": iso(at),
        "run_id": incident.run_id,
        "scenario_id": incident.scenario_id,
        "state": incident.state.value,
        "evidence_scope": incident.evidence_scope,
        **details,
    }
