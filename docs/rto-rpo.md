# Measured RTO and RPO

Targets are policy; the report contains observations.

```text
measured RTO = validated recovered service ready for intended traffic - incident declaration
measured RPO = failure/corruption timestamp - newest successfully recovered transaction
```

For a regional outage, the end is validation completion after a write then read through the surviving
regional endpoint after the failed endpoint is quarantined. For corruption, it is promotion of the
validated restored/reconciled data path. Infrastructure-ready time remains a separate milestone so
it cannot masquerade as RTO.

For logical corruption, the end is protected promotion after validation and reconciliation because
the isolated table is not yet eligible for intended traffic. The evidence records restore start,
recovery point, validation completion, record counts,
newest recovered transaction, and promotion. Negative intervals are rejected. Timestamps are UTC,
and AWS execution must record timestamp authority and observed clock skew. Naive timestamps, negative
intervals, recovered timestamps after the failure reference, or `AWS_RUNTIME` without clock proof are
rejected. Application timestamps alone are not trusted: Milestone 2 pairs them with controller receipt
time, fault-injector time, AWS request IDs, and replication-lag observations.

RPO can be zero for a regional outage when the global-table survivor contains the last accepted
transaction. Corruption recovery commonly has non-zero RPO because the safe point must precede the
bad mutation. “Lost records” counts missing expected transaction IDs; it complements time-based RPO
and avoids concealing high transaction volume within a short interval.

Milestone 2 should repeat drills and report distributions (median/p95/max), separate detection time
from repair time, and compare claimed objectives with measured results. One successful drill is not
an SLO.
