from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dr_platform.data_validation import (
    choose_recovery_point,
    compare_datasets,
    dataset_checksum,
    measured_rpo_seconds,
    measured_rto_seconds,
)
from dr_platform.errors import EvidenceIntegrityError
from dr_platform.types import Transaction


def transaction(key: str, seconds: int = 0) -> Transaction:
    return Transaction(
        key,
        datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds),
        "us-east-1",
        100,
        "synthetic",
    )


def test_checksum_is_order_independent() -> None:
    assert dataset_checksum([transaction("b"), transaction("a")]) == dataset_checksum(
        [transaction("a"), transaction("b")]
    )


def test_comparison_reports_loss_and_checksum() -> None:
    result = compare_datasets([transaction("a"), transaction("b")], [transaction("a")])
    assert result.missing_keys == ("b",)
    assert not result.exact_match
    assert result.recovered_count == 1


def test_recovery_point_selects_latest_safe_point() -> None:
    corruption = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
    points = [corruption - timedelta(seconds=value) for value in (10, 2, 0)]
    assert choose_recovery_point(points, corruption) == corruption - timedelta(seconds=2)


def test_recovery_point_rejects_no_safe_option() -> None:
    corruption = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="No safe recovery point"):
        choose_recovery_point([corruption], corruption)


def test_rto_and_rpo_use_observed_timestamps() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    assert measured_rto_seconds(start, start + timedelta(seconds=75)) == 75
    assert measured_rpo_seconds(start, start - timedelta(seconds=12)) == 12


def test_rto_rejects_negative_interval() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError):
        measured_rto_seconds(now, now - timedelta(seconds=1))


def test_rpo_rejects_transaction_after_failure() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError):
        measured_rpo_seconds(now, now + timedelta(seconds=1))


def test_naive_timestamps_are_rejected() -> None:
    naive = datetime(2026, 1, 1)
    aware = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(EvidenceIntegrityError, match="Naive timestamps"):
        measured_rto_seconds(naive, aware)


def test_out_of_order_records_do_not_change_newest_recovered() -> None:
    result = compare_datasets(
        [transaction("a", 1), transaction("b", 2)],
        [transaction("b", 2), transaction("a", 1)],
    )
    assert result.newest_recovered_transaction == transaction("b", 2).timestamp
