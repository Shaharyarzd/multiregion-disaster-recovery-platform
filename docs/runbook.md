# Operator runbook

## Preconditions

- Confirm the account is disposable/non-production, synthetic-data-only, budgeted, and owner-approved.
- Record workflow commit SHA, `drctl` version, regions, UTC clock health, expected key/object manifest,
  and both regional API baselines.
- Confirm the normal deploy identity cannot restore/promote and the recovery environment has an
  independent reviewer. Never apply from this runbook without explicit owner authorization.

## Regional outage drill

1. Baseline health/read/write in both regions and record the latest global transaction.
2. Declare the incident immediately before scoped endpoint fault injection.
3. Remove only failed Region A from the synthetic route set; do not modify durable data.
4. Write and read a deterministic transaction through Region B. Check failed-region quarantine and
   that no endpoint has been duplicated/implicitly promoted.
5. Stop RTO on the successful Region B read-back. Generate evidence; keep status degraded.
6. Restore Region A. Validate API, write/read, data freshness and cross-region consistency.
7. Obtain failback approval, re-add Region A gradually, observe, then approve `HEALTHY`.

## Logical corruption drill

1. Capture expected keys/checksum/newest transaction, then timestamp and perform the approved
   synthetic deletion/corruption.
2. Declare, list available PITR window, and select the latest point before corruption plus margin.
3. Restore to a uniquely named isolated table. Never update Lambda `TABLE_NAME` yet.
4. Validate counts, exact key set, checksum, newest timestamp, RPO, S3 manifest/versions/replica,
   API probes against an isolated validation function, and a post-restore synthetic write/read.
5. On any failure, remain isolated, record differences, and retry from an earlier point.
6. On PASS, enter `AWAITING_APPROVAL`. Present report/diff and reconciliation strategy.
7. After protected approval, reconcile/switch, observe, generate RTO, and retain rollback target.
8. Repair the original active-active path, revalidate both regions, approve controlled failback, and
   only then return to `HEALTHY`.

## S3 recovery drill

List versions/delete markers in both buckets, choose the last known-good source version, copy it to
a quarantine key, compare SHA-256/metadata/encryption, verify replica freshness, then obtain approval
before copying to the live key. A source restoration without a validated replica remains degraded.

## Abort conditions

Abort/hold on unknown affected scope, missing expected manifest, clock skew, no safe PITR point,
checksum/key mismatch, stale required object, unhealthy survivor, absent approver/reference, or any
unexpected non-synthetic data. Preserve evidence and do not improvise an overwrite.

