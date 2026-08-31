"""Bounded, reviewable reconciliation for PITR recovery."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Protocol

from dr_platform.data_validation import dataset_checksum
from dr_platform.types import Transaction, iso, normalize_utc


@dataclass(frozen=True)
class ReconciliationPlan:
    recovery_point: datetime
    authoritative_policy: str
    replay_transaction_ids: tuple[str, ...]
    ignored_corrupt_transaction_ids: tuple[str, ...]
    conflicting_transaction_ids: tuple[str, ...]
    pre_point_missing_transaction_ids: tuple[str, ...]
    restored_after_point_transaction_ids: tuple[str, ...]
    post_failure_transaction_ids: tuple[str, ...]
    authoritative_checksum: str
    newest_authoritative_transaction: datetime | None

    @property
    def safe_to_promote(self) -> bool:
        return not (
            self.conflicting_transaction_ids
            or self.pre_point_missing_transaction_ids
            or self.restored_after_point_transaction_ids
            or self.post_failure_transaction_ids
        )

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "recovery_point": iso(self.recovery_point),
            "newest_authoritative_transaction": iso(self.newest_authoritative_transaction),
            "safe_to_promote": self.safe_to_promote,
            "status": "AWAITING_APPROVAL" if self.safe_to_promote else "BLOCKED",
        }


@dataclass(frozen=True)
class ConsistencyProof:
    checked_at: datetime
    exact_key_match: bool
    exact_checksum_match: bool
    no_pending_replay: bool
    replication_lag_seconds: float
    maximum_allowed_lag_seconds: float

    @property
    def passed(self) -> bool:
        return (
            self.exact_key_match
            and self.exact_checksum_match
            and self.no_pending_replay
            and 0 <= self.replication_lag_seconds <= self.maximum_allowed_lag_seconds
        )


class ReplayTarget(Protocol):
    """Conditional target used by the bounded portfolio replay."""

    def get(self, transaction_id: str) -> Transaction | None: ...

    def put_if_absent(self, transaction: Transaction) -> bool: ...


@dataclass(frozen=True)
class ReplayResult:
    plan_checksum: str
    validation_timestamp: datetime
    executed_at: datetime
    requested_ids: tuple[str, ...]
    applied_ids: tuple[str, ...]
    idempotent_ids: tuple[str, ...]
    conflict_ids: tuple[str, ...]
    failed_ids: tuple[str, ...]
    dry_run: bool
    approved: bool
    approver: str | None
    approval_reference: str | None

    @property
    def complete(self) -> bool:
        return bool(
            not self.dry_run
            and self.approved
            and not self.conflict_ids
            and not self.failed_ids
            and set(self.requested_ids) == set(self.applied_ids + self.idempotent_ids)
        )

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "validation_timestamp": iso(self.validation_timestamp),
            "executed_at": iso(self.executed_at),
            "complete": self.complete,
        }


def execute_replay(
    plan: ReconciliationPlan,
    candidates: list[Transaction],
    target: ReplayTarget,
    *,
    validation_timestamp: datetime,
    executed_at: datetime,
    approved: bool,
    approver: str | None = None,
    approval_reference: str | None = None,
    dry_run: bool = True,
    batch_size: int = 25,
) -> ReplayResult:
    """Execute only the deterministic unique-write plan using conditional puts.

    This is intentionally not a general merge engine. Existing different content,
    missing candidates, stale validation, and partial write failures are evidence and
    hard promotion blockers.
    """
    checked = normalize_utc(validation_timestamp)
    executed = normalize_utc(executed_at)
    if batch_size < 1 or batch_size > 25:
        raise ValueError("replay batch_size must be between 1 and 25")
    if executed < checked:
        raise ValueError("replay cannot precede validation")
    if approved and (not approver or not approval_reference):
        raise ValueError("approved replay requires approver and approval reference")
    wanted = plan.replay_transaction_ids
    by_id = {item.transaction_id: item for item in candidates}
    applied: list[str] = []
    idempotent: list[str] = []
    conflicts: list[str] = []
    failed: list[str] = []
    for transaction_id in wanted[:batch_size]:
        candidate = by_id.get(transaction_id)
        if candidate is None:
            failed.append(transaction_id)
            continue
        existing = target.get(transaction_id)
        if existing is not None:
            (idempotent if existing.canonical() == candidate.canonical() else conflicts).append(
                transaction_id
            )
            continue
        if dry_run:
            continue
        if not approved:
            failed.append(transaction_id)
            continue
        try:
            if target.put_if_absent(candidate):
                applied.append(transaction_id)
            else:
                concurrent = target.get(transaction_id)
                if concurrent and concurrent.canonical() == candidate.canonical():
                    idempotent.append(transaction_id)
                else:
                    conflicts.append(transaction_id)
        except Exception:  # the exact item remains retryable through conditional idempotency
            failed.append(transaction_id)
    if len(wanted) > batch_size:
        failed.extend(wanted[batch_size:])
    return ReplayResult(
        plan_checksum=plan.authoritative_checksum,
        validation_timestamp=checked,
        executed_at=executed,
        requested_ids=wanted,
        applied_ids=tuple(sorted(applied)),
        idempotent_ids=tuple(sorted(idempotent)),
        conflict_ids=tuple(sorted(conflicts)),
        failed_ids=tuple(sorted(failed)),
        dry_run=dry_run,
        approved=approved,
        approver=approver,
        approval_reference=approval_reference,
    )


def plan_reconciliation(
    restored: list[Transaction],
    live: list[Transaction],
    recovery_point: datetime,
    authoritative_cutoff: datetime,
    corrupted_transaction_ids: set[str],
) -> ReconciliationPlan:
    point = normalize_utc(recovery_point)
    cutoff = normalize_utc(authoritative_cutoff)
    if cutoff < point:
        raise ValueError("authoritative cutoff cannot precede recovery point")
    restored_by_id = {item.transaction_id: item for item in restored}
    live_by_id = {item.transaction_id: item for item in live}
    restored_after = tuple(
        sorted(item.transaction_id for item in restored if normalize_utc(item.timestamp) > point)
    )
    replay: list[Transaction] = []
    conflicts: list[str] = []
    pre_point_missing: list[str] = []
    post_failure: list[str] = []
    for item in live:
        if item.transaction_id in corrupted_transaction_ids:
            continue
        restored_item = restored_by_id.get(item.transaction_id)
        if normalize_utc(item.timestamp) > cutoff:
            post_failure.append(item.transaction_id)
        elif normalize_utc(item.timestamp) > point:
            if restored_item is None:
                replay.append(item)
            elif restored_item.canonical() != item.canonical():
                conflicts.append(item.transaction_id)
        elif restored_item is None:
            pre_point_missing.append(item.transaction_id)
        elif restored_item.canonical() != item.canonical():
            conflicts.append(item.transaction_id)

    authoritative = list(restored) + replay
    ignored = tuple(sorted(corrupted_transaction_ids & set(live_by_id)))
    return ReconciliationPlan(
        recovery_point=point,
        authoritative_policy="PITR_BASE_PLUS_APPROVED_UNIQUE_POST_POINT_WRITES",
        replay_transaction_ids=tuple(sorted(item.transaction_id for item in replay)),
        ignored_corrupt_transaction_ids=ignored,
        conflicting_transaction_ids=tuple(sorted(set(conflicts))),
        pre_point_missing_transaction_ids=tuple(sorted(set(pre_point_missing))),
        restored_after_point_transaction_ids=restored_after,
        post_failure_transaction_ids=tuple(sorted(set(post_failure))),
        authoritative_checksum=dataset_checksum(authoritative),
        newest_authoritative_transaction=max(
            (item.timestamp for item in authoritative), default=None
        ),
    )
