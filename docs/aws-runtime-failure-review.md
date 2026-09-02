# AWS runtime failure review and dry runs

## Known platform risks

### API Gateway V2 two-phase stage authorization

AWS evaluates HTTP API stage creation against a stage-collection authorization before the stage
exists. In this account, the dependent tag authorization did not expose request-tag context, so a
tag-conditioned `POST /apis/*/stages` statement could not authorize creation safely. The demo uses
two phases: create both APIs with stages disabled, capture their exact IDs in the preserved runtime
context, install `POST` and `PUT` authority only on `/apis/{exact-api-id}/stages` through the
bootstrap boundary, then
rerun with stage provisioning enabled. Encoded tag-resource permissions use the exact API ID and
`$default` stage ARN and remain request-tag and tag-key conditioned. Regional plans are rejected if
they contain any delete or replacement action.

Inline stage tags cannot be restored without redesigning IAM because API Gateway authorizes
`TagResource` against the not-yet-created stage collection during `CreateStage`. The controlled
workflow therefore creates an inert untagged stage with `auto_deploy=false`, tags it in an
idempotent second Terraform apply, refreshes state and verifies all three exact values, and only
then enables auto-deployment. A shared concurrency lock prevents another plan/deploy workflow from
running during this interval. A tagging or verification failure permits only the stage rollback
plan, deletes the inert stage, verifies its state entry is absent, and fails the workflow.

- DynamoDB PITR restores to a new table and does not restore tags, alarms, streams, TTL, deletion
  protection, PITR, or global replicas automatically. Restore duration is variable. The target name
  must be unique and the source PITR window available. **PENDING AWS.**
  [AWS restore behavior](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/pointintimerecovery_restores.html)
- MREC Global Tables are eventually consistent, use last-writer-wins for conflicts, and expose
  `ReplicationLatency`; conflict events themselves are not logged. Strong cross-region reads are
  unavailable. **PENDING AWS measurement.**
  [Global Table behavior](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/globaltables_HowItWorks.html)
- S3 CRR requires versioning on both buckets, enabled Regions, IAM/KMS permissions, and copies new
  objects asynchronously; pre-rule objects need Batch Replication. Destination versioning disabled
  causes replication failure. **PENDING AWS.**
  [S3 replication requirements](https://docs.aws.amazon.com/AmazonS3/latest/userguide/replication-requirements.html)
- Global-table backfill/throughput/table-operation quotas, Lambda concurrency, API throttles,
  CloudWatch metric appearance delay, propagation, and service-linked-role creation can block a
  first run. Check Service Quotas before apply. [DynamoDB quotas](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/ServiceQuotas.html)
- Cleanup order: stop traffic/faults; archive evidence; remove temporary restored targets; remove
  regional runtimes; remove global replicas/table/buckets; retain or empty evidence per Object Lock;
  schedule KMS deletion last. Global replica deletion can have a 24-hour restriction after adding a
  Region. **PENDING AWS.**

## Scenario A — regional outage

| Step | Dependency / likely failure | Detect / retry / stop | Evidence |
|---|---|---|---|
| baseline active-active | both APIs, Global Table convergence | API + write/read + max ReplicationLatency; stop on baseline failure | endpoint probes, keys/checksum, lag |
| fault one endpoint | scoped synthetic endpoint quarantine | fault ID and three failed 5s probes; retry probes only | declaration/fault timestamps |
| survivor continues | Region B API/Lambda/table | idempotent deterministic write/read; stop on throttle/data mismatch | request IDs, transaction, health |
| routing recovery | synthetic selector | first survivor write/read after quarantine ends RTO | selection log and RTO inputs |
| restore Region A | service recovery + replication | wait/retry reads until bounded lag; stop on conflict/mismatch | two-region proof, lag |
| controlled restoration | fresh proof + approval | re-add gradually; abort to survivor on failure | approval, failback chain |

## Scenario B — logical corruption

| Step | Dependency / likely failure | Detect / retry / stop | Evidence |
|---|---|---|---|
| baseline/corrupt synthetic IDs | exact manifest and fault timestamp | stop if scope is unknown/non-synthetic | pre-fault manifest/checksum |
| choose point | PITR earliest/latest restorable times | select latest second before corruption plus margin; earlier retry only | candidate/selected point |
| isolated restore | unique target, quota, KMS | describe until ACTIVE; retry transient API errors, never overwrite | restore request/table/times |
| compare/reconcile | scans, stable manifests, post-point write capture | conflict/pre-point omission/newer restored row stops promotion | counts, keys, hashes, plan |
| RPO | trusted failure and authoritative transaction times | reject naive/future/untrusted timestamps or excess skew | sources, skew, raw inputs/value |
| approve/promote | protected environment | replay only approved unique IDs; no generic live-table replacement | signature, approver/reference |
| failback | fresh exact proof and bounded lag | stale proof/pending replay/mismatch blocks; retry after convergence | consistency proof, lag, final hash |
