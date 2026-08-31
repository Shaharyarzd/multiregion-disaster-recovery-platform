"""Deterministic, tamper-evident evidence generation."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, cast

from dr_platform.data_validation import measured_rpo_seconds, measured_rto_seconds
from dr_platform.errors import EvidenceIntegrityError
from dr_platform.integrity import canonical_json, sha256_hex, verify_event_chain
from dr_platform.types import Incident, iso

SCHEMA_ID = "com.portfolio.dr.recovery-report"
SCHEMA_VERSION = "2.0.0"
SENSITIVE_KEY = re.compile(r"(secret|token|password|authorization|credential)", re.IGNORECASE)
AWS_TRUSTED_TIMESTAMP_SOURCES = {
    "incident_declaration": {"CONTROLLER_UTC_SYNCED", "AWS_EVENT_TIMESTAMP"},
    "failure_or_corruption": {"FAULT_INJECTOR_UTC_SYNCED", "AWS_CLOUDTRAIL_EVENT"},
    "validation": {"CONTROLLER_UTC_SYNCED"},
    "recovered_transaction": {"DYNAMODB_TRANSACTION_UTC_VALIDATED"},
}


class EvidenceSigner(Protocol):
    def sign_digest(self, digest_hex: str) -> dict[str, str]: ...


class EvidenceVerifier(Protocol):
    def verify_digest(self, digest_hex: str, signature: dict[str, str]) -> bool: ...


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if SENSITIVE_KEY.search(key) else redact(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [redact(child) for child in value]
    return value


def _measurement_inputs(incident: Incident, newest: str | None) -> dict[str, Any]:
    rto = (
        measured_rto_seconds(incident.declared_at, incident.rto_end_at)
        if incident.rto_end_at
        else None
    )
    rpo = None
    if newest:
        newest_time = datetime.fromisoformat(newest.replace("Z", "+00:00"))
        rpo = measured_rpo_seconds(incident.failure_at, newest_time)
    return {
        "clock": {
            "observed_skew_ms": incident.clock_skew_ms_observed,
            "maximum_allowed_skew_ms": incident.clock_skew_ms_limit,
            "production_clock_validation": (
                "PASS"
                if incident.clock_skew_ms_observed is not None
                and incident.clock_skew_ms_observed <= incident.clock_skew_ms_limit
                else "NOT_PROVEN"
            ),
        },
        "rto": {
            "start": iso(incident.declared_at),
            "start_source": incident.timestamp_sources.get("incident_declaration"),
            "end": iso(incident.rto_end_at),
            "end_source": incident.timestamp_sources.get("validation"),
            "definition": "validated recovered service ready for intended traffic",
            "derived_seconds": rto,
        },
        "rpo": {
            "reference": iso(incident.failure_at),
            "reference_source": incident.timestamp_sources.get("failure_or_corruption"),
            "recovered_point": newest,
            "recovered_point_source": incident.timestamp_sources.get("recovered_transaction"),
            "definition": "failure point minus newest validated business transaction",
            "derived_seconds": rpo,
        },
    }


def _validate_aws_runtime_inputs(incident: Incident) -> None:
    if incident.evidence_scope != "AWS_RUNTIME":
        return
    if (
        incident.clock_skew_ms_observed is None
        or incident.clock_skew_ms_observed > incident.clock_skew_ms_limit
    ):
        raise EvidenceIntegrityError("AWS_RUNTIME evidence requires passing clock-skew proof")
    for field, allowed_sources in AWS_TRUSTED_TIMESTAMP_SOURCES.items():
        if incident.timestamp_sources.get(field) not in allowed_sources:
            raise EvidenceIntegrityError(
                f"AWS_RUNTIME evidence has untrusted timestamp authority for {field}"
            )


def build_report(incident: Incident, signer: EvidenceSigner | None = None) -> dict[str, Any]:
    _validate_aws_runtime_inputs(incident)
    if incident.evidence_scope == "AWS_RUNTIME" and signer is None:
        raise EvidenceIntegrityError("AWS_RUNTIME reports require an evidence signer")
    if not incident.run_id or not incident.scenario_id:
        raise EvidenceIntegrityError("run_id and scenario_id are required")
    if not verify_event_chain(incident.event_log):
        raise EvidenceIntegrityError("Recovery event hash chain verification failed")
    validation = incident.validation.as_dict() if incident.validation else None
    newest = (
        cast(str | None, validation.get("details", {}).get("newest_recovered_transaction"))
        if validation
        else None
    )
    body = cast(
        dict[str, Any],
        redact(
            {
                "schema": {"id": SCHEMA_ID, "version": SCHEMA_VERSION},
                "evidence_source": "generated-by-drctl",
                "evidence_scope": incident.evidence_scope,
                "run_id": incident.run_id,
                "scenario_id": incident.scenario_id,
                "incident_id": incident.incident_id,
                "scenario": incident.scenario.value,
                "state": incident.state.value,
                "timestamps": {
                    "incident_declared": iso(incident.declared_at),
                    "failure_or_corruption": iso(incident.failure_at),
                    "recovery_started": iso(incident.recovery_started_at),
                    "restore_completed": iso(incident.restore_completed_at),
                    "validation_started": iso(incident.validation_started_at),
                    "validation_completed": iso(incident.validation_completed_at),
                    "service_ready": iso(incident.rto_end_at),
                    "promotion": iso(incident.promotion_at),
                    "failback_completed": iso(incident.failback_completed_at),
                    "sources": incident.timestamp_sources,
                },
                "data_recovery_point": iso(incident.recovery_point),
                "measurements": _measurement_inputs(incident, newest),
                "record_counts": incident.record_counts,
                "reconciliation": incident.reconciliation,
                "validation": validation,
                "validation_result": (
                    "PASS" if validation and validation["passed"] else "NOT_PASSED"
                ),
                "approval": {
                    "status": incident.approval_status,
                    "approver": incident.approver,
                    "reference": incident.approval_reference,
                },
                "promotion_status": "COMPLETE" if incident.promotion_at else "NOT_COMPLETE",
                "failback_status": (
                    "COMPLETE" if incident.failback_completed_at else "NOT_COMPLETE"
                ),
                "events": incident.event_log,
            }
        ),
    )
    digest = sha256_hex(body)
    integrity: dict[str, Any] = {
        "canonicalization": "SORTED_KEYS_COMPACT_JSON",
        "hash_algorithm": "SHA-256",
        "report_sha256": digest,
        "event_chain_verified": True,
        "immutability": "NOT_PROVIDED_BY_LOCAL_FILE",
        "signature": {"status": "UNSIGNED"},
    }
    if signer:
        integrity["signature"] = {"status": "SIGNED", **signer.sign_digest(digest)}
    return {**body, "integrity": integrity}


def verify_report(report: dict[str, Any], verifier: EvidenceVerifier | None = None) -> bool:
    integrity = report.get("integrity", {})
    body = {key: value for key, value in report.items() if key != "integrity"}
    events = cast(list[dict[str, Any]], body.get("events", []))
    structurally_valid = bool(
        integrity.get("report_sha256") == sha256_hex(body)
        and integrity.get("event_chain_verified") is True
        and verify_event_chain(events)
    )
    if not structurally_valid:
        return False
    signature = cast(dict[str, str], integrity.get("signature", {}))
    if signature.get("status") == "SIGNED":
        return bool(verifier and verifier.verify_digest(str(integrity["report_sha256"]), signature))
    return signature.get("status") == "UNSIGNED"


def write_report(
    incident: Incident, path: Path, signer: EvidenceSigner | None = None
) -> dict[str, Any]:
    report = build_report(incident, signer)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(report) + b"\n")
    return report
