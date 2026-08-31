"""Observed replication-convergence measurement without arbitrary sleeps."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from dr_platform.types import iso, normalize_utc


@dataclass(frozen=True)
class ConvergenceEvidence:
    system: str
    marker_id: str
    source_write_at: datetime
    observed_at: datetime | None
    timeout_seconds: float
    attempts: int
    completed: bool

    @property
    def measured_lag_ms(self) -> float | None:
        if self.observed_at is None:
            return None
        return (self.observed_at - self.source_write_at).total_seconds() * 1000

    def as_dict(self) -> dict[str, object]:
        return {
            "system": self.system,
            "marker_id": self.marker_id,
            "source_write_at": iso(self.source_write_at),
            "observed_at": iso(self.observed_at),
            "timeout_seconds": self.timeout_seconds,
            "attempts": self.attempts,
            "completed": self.completed,
            "measured_lag_ms": self.measured_lag_ms,
            "measurement": "CLIENT_OBSERVED_CONVERGENCE",
        }


def measure_convergence(
    *,
    system: str,
    marker_id: str,
    source_write_at: datetime,
    observe: Callable[[], bool],
    clock: Callable[[], datetime],
    wait: Callable[[float], None],
    timeout_seconds: float,
    initial_backoff_seconds: float = 0.5,
    maximum_backoff_seconds: float = 5.0,
) -> ConvergenceEvidence:
    source = normalize_utc(source_write_at)
    deadline = source.timestamp() + timeout_seconds
    attempts = 0
    backoff = initial_backoff_seconds
    while normalize_utc(clock()).timestamp() <= deadline:
        attempts += 1
        if observe():
            return ConvergenceEvidence(
                system, marker_id, source, normalize_utc(clock()), timeout_seconds, attempts, True
            )
        now = normalize_utc(clock()).timestamp()
        if now + backoff > deadline:
            break
        wait(backoff)
        backoff = min(backoff * 2, maximum_backoff_seconds)
    return ConvergenceEvidence(system, marker_id, source, None, timeout_seconds, attempts, False)


def dynamodb_marker_matches(item: dict[str, object] | None, marker_id: str, digest: str) -> bool:
    """Region-B GetItem should use ConsistentRead=True for this marker check."""
    return bool(item and item.get("transaction_id") == marker_id and item.get("digest") == digest)


def s3_replica_matches(head: dict[str, object] | None, marker_id: str, digest: str) -> bool:
    """Destination HeadObject must report REPLICA and matching marker metadata."""
    metadata = head.get("Metadata", {}) if head else {}
    return bool(
        head
        and head.get("ReplicationStatus") == "REPLICA"
        and isinstance(metadata, dict)
        and metadata.get("marker-id") == marker_id
        and metadata.get("sha256") == digest
    )
