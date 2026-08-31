# ADR-002: Restore then validate before promotion

**Status:** Accepted

PITR always creates a separately named isolated table. No restore overwrites or immediately rewires
the live data path. Promotion requires exact keys/count/checksum, freshness, API/read/write, S3,
cross-region, and post-recovery transaction checks. This consumes time/storage and needs a business
source of truth, but preserves rollback and prevents “available” from being confused with “correct.”

