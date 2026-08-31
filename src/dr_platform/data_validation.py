"""Deterministic recovered-data comparison and recovery-point selection."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from dr_platform.types import Transaction


@dataclass(frozen=True)
class Comparison:
    source_count: int
    recovered_count: int
    missing_keys: tuple[str, ...]
    unexpected_keys: tuple[str, ...]
    source_checksum: str
    recovered_checksum: str
    newest_recovered_transaction: datetime | None

    @property
    def exact_match(self) -> bool:
        return (
            not self.missing_keys
            and not self.unexpected_keys
            and self.source_checksum == self.recovered_checksum
        )


def dataset_checksum(transactions: Iterable[Transaction]) -> str:
    canonical = sorted(
        (transaction.canonical() for transaction in transactions),
        key=lambda value: value["transaction_id"],
    )
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def compare_datasets(
    expected: Iterable[Transaction], recovered: Iterable[Transaction]
) -> Comparison:
    expected_list = list(expected)
    recovered_list = list(recovered)
    expected_keys = {item.transaction_id for item in expected_list}
    recovered_keys = {item.transaction_id for item in recovered_list}
    newest = max((item.timestamp for item in recovered_list), default=None)
    return Comparison(
        source_count=len(expected_list),
        recovered_count=len(recovered_list),
        missing_keys=tuple(sorted(expected_keys - recovered_keys)),
        unexpected_keys=tuple(sorted(recovered_keys - expected_keys)),
        source_checksum=dataset_checksum(expected_list),
        recovered_checksum=dataset_checksum(recovered_list),
        newest_recovered_transaction=newest,
    )


def choose_recovery_point(
    available_points: Iterable[datetime], corruption_time: datetime, safety_margin_seconds: int = 1
) -> datetime:
    if safety_margin_seconds < 0:
        raise ValueError("safety_margin_seconds cannot be negative")
    eligible = [
        point
        for point in available_points
        if (corruption_time - point).total_seconds() >= safety_margin_seconds
    ]
    if not eligible:
        raise ValueError("No safe recovery point exists before corruption")
    return max(eligible)


def measured_rto_seconds(incident_time: datetime, service_recovery_time: datetime) -> float:
    result = (service_recovery_time - incident_time).total_seconds()
    if result < 0:
        raise ValueError("service recovery cannot precede incident declaration")
    return result


def measured_rpo_seconds(failure_time: datetime, newest_recovered: datetime) -> float:
    result = (failure_time - newest_recovered).total_seconds()
    if result < 0:
        raise ValueError("newest recovered transaction cannot be after the failure")
    return result
