"""Evidence generation derived only from controller state and validations."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

from dr_platform.data_validation import measured_rpo_seconds, measured_rto_seconds
from dr_platform.types import Incident, iso

SENSITIVE_KEY = re.compile(r"(secret|token|password|authorization|credential)", re.IGNORECASE)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if SENSITIVE_KEY.search(key) else redact(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [redact(child) for child in value]
    return value


def build_report(incident: Incident) -> dict[str, Any]:
    newest = None
    validation = incident.validation.as_dict() if incident.validation else None
    if validation:
        newest = validation.get("details", {}).get("newest_recovered_transaction")

    rto = None
    if incident.promotion_at:
        rto = measured_rto_seconds(incident.declared_at, incident.promotion_at)

    rpo = None
    if newest:
        from datetime import datetime

        newest_time = datetime.fromisoformat(str(newest).replace("Z", "+00:00"))
        rpo = measured_rpo_seconds(incident.failure_at, newest_time)

    return cast(
        dict[str, Any],
        redact(
            {
                "schema_version": "1.0",
                "evidence_source": "generated-by-drctl",
                "evidence_scope": incident.evidence_scope,
                "incident_id": incident.incident_id,
                "scenario": incident.scenario.value,
                "state": incident.state.value,
                "incident_time": iso(incident.declared_at),
                "failure_or_corruption_time": iso(incident.failure_at),
                "recovery_start": iso(incident.recovery_started_at),
                "infrastructure_ready_time": iso(incident.infrastructure_ready_at),
                "data_recovery_point": iso(incident.recovery_point),
                "validation_completion": iso(incident.validation_completed_at),
                "promotion_time": iso(incident.promotion_at),
                "failback_completion": iso(incident.failback_completed_at),
                "measured_rto_seconds": rto,
                "measured_rpo_seconds": rpo,
                "record_counts": incident.record_counts,
                "validation": validation,
                "validation_result": "PASS"
                if validation and validation["passed"]
                else "NOT_PASSED",
                "approval_status": incident.approval_status,
                "approval_reference": incident.approval_reference,
                "failback_status": (
                    "COMPLETE" if incident.failback_completed_at else "NOT_COMPLETE"
                ),
                "events": incident.event_log,
            },
        ),
    )


def write_report(incident: Incident, path: Path) -> dict[str, Any]:
    report = build_report(incident)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
