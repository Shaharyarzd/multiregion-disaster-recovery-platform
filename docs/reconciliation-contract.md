# Reconciliation contract

## Bounded Milestone 2 behavior

The PITR table is an isolated **base snapshot**, never a drop-in replacement for the live global
table. `drctl` requires a recovery point strictly before the corruption timestamp, restores to a
unique non-production name, and computes a `ReconciliationPlan` from the restored snapshot plus a
captured live-data manifest.

Authoritative demo state is:

```text
validated PITR base
+ unique, immutable transaction IDs written after the recovery point
- explicitly identified corrupt IDs
```

Unique post-point transactions absent from the restore and timestamped no later than the recorded
corruption cutoff are classified for bounded replay. The demo fences new writes at that cutoff;
observed transactions after it block promotion and require a production journal/business review. A live
and restored item with the same ID but different canonical content is a conflict and blocks
promotion. Pre-point live items absent from the restore and restored items timestamped after the
chosen point also block promotion. The plan records the authoritative checksum and newest
authoritative transaction used for RPO.

The demo does not overwrite the healthy global table or implement general business merge semantics.
Milestone 2 promotion means owner-approved replay into a separately named validation target and
traffic validation. A production cutover requires an immutable change journal/stream archive,
business-specific conflict rules, idempotent replay, write fencing during final convergence, and a
rollback plan. Those requirements remain **PENDING AWS / production implementation**.

## Hard blocks

Promotion is blocked by: absent/unsafe recovery point, failed validation gate, missing reconciliation
plan, same-ID content conflict, pre-point omission, post-failure write, impossible post-point record in the restored
snapshot, unreviewed corruption scope, missing approver/reference, or invalid evidence chain.

Failback is blocked until both regional APIs pass read/write checks and a consistency proof created
after promotion shows exact keys, exact checksum, no pending replay, and replication lag within the
declared bound. A boolean “data consistent” assertion is no longer sufficient in the library.
