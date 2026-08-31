from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import pytest

from dr_platform.errors import EvidenceIntegrityError
from dr_platform.evidence import build_report, redact, verify_report
from dr_platform.types import RecoveryState, Scenario, ValidationResult


def test_report_calculates_measured_values(declared) -> None:
    _, incident = declared
    incident.state = RecoveryState.RECOVERY_ACTIVE
    incident.rto_end_at = incident.declared_at + timedelta(seconds=42)
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
    assert report["measurements"]["rto"]["derived_seconds"] == 42
    assert report["measurements"]["rpo"]["derived_seconds"] == 5
    assert report["validation_result"] == "PASS"
    assert report["schema"]["version"] == "2.1.0"
    assert verify_report(report)


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


def test_report_tampering_is_detected(declared) -> None:
    _, incident = declared
    report = build_report(incident)
    tampered = copy.deepcopy(report)
    tampered["approval"]["status"] = "APPROVED"
    assert not verify_report(tampered)


def test_event_chain_tampering_blocks_report(declared) -> None:
    _, incident = declared
    incident.event_log[0]["actor"] = "forged"
    with pytest.raises(EvidenceIntegrityError, match="hash chain"):
        build_report(incident)


def test_aws_runtime_requires_clock_proof(declared) -> None:
    _, incident = declared
    incident.evidence_scope = "AWS_RUNTIME"
    with pytest.raises(EvidenceIntegrityError, match="clock-skew"):
        build_report(incident)


def test_aws_runtime_rejects_local_timestamp_authorities(declared) -> None:
    _, incident = declared
    incident.evidence_scope = "AWS_RUNTIME"
    incident.clock_skew_ms_observed = 100
    with pytest.raises(EvidenceIntegrityError, match="untrusted timestamp authority"):
        build_report(incident, SignerForAws())


class SignerForAws:
    def sign_digest(self, digest_hex: str) -> dict[str, str]:
        return {
            "algorithm": "TEST",
            "key_id": "test-key",
            "signature_base64": digest_hex[:8],
        }


def test_aws_runtime_requires_signer_after_trusted_clock_inputs(declared) -> None:
    _, incident = declared
    incident.evidence_scope = "AWS_RUNTIME"
    incident.clock_skew_ms_observed = 100
    incident.timestamp_sources = {
        "incident_declaration": "CONTROLLER_UTC_SYNCED",
        "failure_or_corruption": "FAULT_INJECTOR_UTC_SYNCED",
        "validation": "CONTROLLER_UTC_SYNCED",
        "recovered_transaction": "DYNAMODB_TRANSACTION_UTC_VALIDATED",
    }
    with pytest.raises(EvidenceIntegrityError, match="require an evidence signer"):
        build_report(incident)
    assert build_report(incident, SignerForAws())["integrity"]["signature"]["status"] == "SIGNED"


def test_optional_signer_is_recorded_without_claiming_local_immutability(declared) -> None:
    class Signer:
        def sign_digest(self, digest_hex: str) -> dict[str, str]:
            return {
                "algorithm": "TEST",
                "key_id": "test-key",
                "signature_base64": digest_hex[:8],
            }

        def verify_digest(self, digest_hex: str, signature: dict[str, str]) -> bool:
            return signature["signature_base64"] == digest_hex[:8]

    _, incident = declared
    report = build_report(incident, Signer())
    assert report["integrity"]["signature"]["status"] == "SIGNED"
    assert report["integrity"]["immutability"] == "NOT_PROVIDED_BY_LOCAL_FILE"
    assert not verify_report(report)
    assert verify_report(report, Signer())
