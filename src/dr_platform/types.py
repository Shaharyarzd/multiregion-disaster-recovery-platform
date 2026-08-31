"""Domain types shared by the recovery controller."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None


class RecoveryState(StrEnum):
    HEALTHY = "HEALTHY"
    INCIDENT_DECLARED = "INCIDENT_DECLARED"
    RECOVERY_IN_PROGRESS = "RECOVERY_IN_PROGRESS"
    VALIDATING = "VALIDATING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    RECOVERY_ACTIVE = "RECOVERY_ACTIVE"
    FAILBACK_IN_PROGRESS = "FAILBACK_IN_PROGRESS"


class Scenario(StrEnum):
    REGIONAL_OUTAGE = "regional-outage"
    LOGICAL_CORRUPTION = "logical-data-corruption"
    S3_RECOVERY = "s3-object-recovery"


@dataclass(frozen=True)
class Transaction:
    transaction_id: str
    timestamp: datetime
    region: str
    amount_cents: int
    payload: str

    def canonical(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "timestamp": iso(self.timestamp),
            "region": self.region,
            "amount_cents": self.amount_cents,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class RegionHealth:
    region: str
    api_healthy: bool
    read_ok: bool
    write_ok: bool
    checked_at: datetime
    details: str = ""

    @property
    def healthy(self) -> bool:
        return self.api_healthy and self.read_ok and self.write_ok


@dataclass
class ValidationResult:
    api_health: bool = False
    read_write: bool = False
    record_count: bool = False
    expected_keys: bool = False
    checksum: bool = False
    freshness: bool = False
    s3_versions: bool = False
    cross_region_consistency: bool = False
    synthetic_transaction: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        checks = [
            self.api_health,
            self.read_write,
            self.record_count,
            self.expected_keys,
            self.checksum,
            self.freshness,
            self.s3_versions,
            self.cross_region_consistency,
            self.synthetic_transaction,
        ]
        return all(checks)

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "passed": self.passed}


@dataclass
class Incident:
    incident_id: str
    scenario: Scenario
    state: RecoveryState
    declared_at: datetime
    failure_at: datetime
    affected_region: str | None = None
    recovery_started_at: datetime | None = None
    infrastructure_ready_at: datetime | None = None
    recovery_point: datetime | None = None
    validation_completed_at: datetime | None = None
    promotion_at: datetime | None = None
    failback_completed_at: datetime | None = None
    approval_status: str = "NOT_REQUESTED"
    approver: str | None = None
    approval_reference: str | None = None
    validation: ValidationResult | None = None
    record_counts: dict[str, int] = field(default_factory=dict)
    event_log: list[dict[str, Any]] = field(default_factory=list)
    evidence_scope: str = "LOCAL_SIMULATION"
