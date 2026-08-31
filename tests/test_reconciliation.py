from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dr_platform.errors import ValidationFailed
from dr_platform.reconciliation import plan_reconciliation
from dr_platform.types import Transaction

POINT = datetime(2026, 1, 1, tzinfo=UTC)


def tx(key: str, offset: int, payload: str = "ok") -> Transaction:
    return Transaction(key, POINT + timedelta(seconds=offset), "region-a", 100, payload)


def test_unique_post_point_write_is_preserved_for_bounded_replay() -> None:
    base = tx("base", -1)
    post = tx("post", 1)
    plan = plan_reconciliation([base], [base, post], POINT, POINT + timedelta(seconds=2), set())
    assert plan.safe_to_promote
    assert plan.replay_transaction_ids == ("post",)
    assert plan.newest_authoritative_transaction == post.timestamp


def test_conflicting_same_id_blocks_promotion() -> None:
    restored = tx("same", -1, "restored")
    live = tx("same", 1, "newer-conflict")
    plan = plan_reconciliation([restored], [live], POINT, POINT + timedelta(seconds=2), set())
    assert not plan.safe_to_promote
    assert plan.conflicting_transaction_ids == ("same",)


def test_restored_record_newer_than_recovery_point_blocks_promotion() -> None:
    impossible = tx("future", 1)
    plan = plan_reconciliation([impossible], [], POINT, POINT + timedelta(seconds=2), set())
    assert not plan.safe_to_promote
    assert plan.restored_after_point_transaction_ids == ("future",)


def test_pre_point_live_record_missing_from_restore_blocks_promotion() -> None:
    plan = plan_reconciliation([], [tx("missing", -1)], POINT, POINT + timedelta(seconds=2), set())
    assert not plan.safe_to_promote
    assert plan.pre_point_missing_transaction_ids == ("missing",)


def test_logical_recovery_rejects_unselected_or_unsafe_point(declared) -> None:
    orchestrator, incident = declared
    with pytest.raises(ValidationFailed, match="before corruption"):
        orchestrator.start_recovery(incident, incident.failure_at)


def test_write_after_failure_cutoff_blocks_bounded_demo() -> None:
    plan = plan_reconciliation([], [tx("too-new", 5)], POINT, POINT + timedelta(seconds=2), set())
    assert not plan.safe_to_promote
    assert plan.post_failure_transaction_ids == ("too-new",)
