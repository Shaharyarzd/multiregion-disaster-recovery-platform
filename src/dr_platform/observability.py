"""Deterministic, low-cardinality CloudWatch metric and log contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dr_platform.data_validation import measured_rpo_seconds, measured_rto_seconds
from dr_platform.types import Incident, iso, normalize_utc

NAMESPACE = "Portfolio/DisasterRecovery"
PROJECT_DIMENSION = "portfolio-dr"
ALLOWED_FAILURE_CODES = frozenset(
    {
        "API_UNHEALTHY",
        "CLOCK_SKEW",
        "CONFIGURATION_INCOMPLETE",
        "DATA_CONFLICT",
        "REPLICATION_TIMEOUT",
        "RESTORE_FAILED",
        "VALIDATION_FAILED",
    }
)
ALLOWED_UNITS = frozenset({"Count", "Seconds", "Milliseconds", "None"})


@dataclass(frozen=True)
class MetricDatum:
    name: str
    value: float
    unit: str
    timestamp: datetime
    dimensions: dict[str, str]

    def as_cloudwatch(self) -> dict[str, Any]:
        if self.unit not in ALLOWED_UNITS:
            raise ValueError(f"Unsupported CloudWatch unit: {self.unit}")
        return {
            "MetricName": self.name,
            "Value": self.value,
            "Unit": self.unit,
            "Timestamp": normalize_utc(self.timestamp),
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
    """Build metrics without generating or signing the final evidence report."""
    timestamp = normalize_utc(at)
    common = {"Project": PROJECT_DIMENSION, "Scenario": incident.scenario.value}
    validation_passed = bool(incident.validation and incident.validation.passed)
    metrics = [
        MetricDatum(
            "RecoveryState",
            float(list(type(incident.state)).index(incident.state)),
            "None",
            timestamp,
            common,
        ),
        MetricDatum("ValidationResult", float(validation_passed), "Count", timestamp, common),
    ]
    for region, healthy in sorted(region_health.items()):
        metrics.append(
            MetricDatum(
                "RegionHealthy", float(healthy), "Count", timestamp, {**common, "Region": region}
            )
        )
    newest = (
        incident.validation.details.get("newest_recovered_transaction")
        if incident.validation
        else None
    )
    optional: tuple[tuple[str, float | None, str], ...] = (
        (
            "RestoreDuration",
            (
                (incident.restore_completed_at - incident.recovery_started_at).total_seconds()
                if incident.restore_completed_at and incident.recovery_started_at
                else None
            ),
            "Seconds",
        ),
        (
            "MeasuredRTO",
            measured_rto_seconds(incident.declared_at, incident.rto_end_at)
            if incident.rto_end_at
            else None,
            "Seconds",
        ),
        (
            "MeasuredRPO",
            measured_rpo_seconds(
                incident.failure_at,
                datetime.fromisoformat(str(newest).replace("Z", "+00:00")),
            )
            if newest
            else None,
            "Seconds",
        ),
        (
            "LastSuccessfulDrill",
            incident.failback_completed_at.timestamp() if incident.failback_completed_at else None,
            "Seconds",
        ),
        ("ReplicationLag", replication_lag_ms, "Milliseconds"),
    )
    for name, value, unit in optional:
        if value is not None:
            metrics.append(MetricDatum(name, float(value), unit, timestamp, common))
    if failure_reason_code:
        if failure_reason_code not in ALLOWED_FAILURE_CODES:
            raise ValueError("FailureCode must be from the bounded contract")
        metrics.append(
            MetricDatum(
                "FailureCode", 1.0, "Count", timestamp, {**common, "Code": failure_reason_code}
            )
        )
    return metrics


def structured_recovery_log(
    incident: Incident, event: str, at: datetime, **details: object
) -> dict[str, object]:
    return {
        "schema_version": "1.1",
        "event": event,
        "timestamp": iso(at),
        "timestamp_source": incident.timestamp_sources.get("validation"),
        "run_id": incident.run_id,
        "scenario_id": incident.scenario_id,
        "scenario": incident.scenario.value,
        "state": incident.state.value,
        "evidence_scope": incident.evidence_scope,
        **details,
    }
