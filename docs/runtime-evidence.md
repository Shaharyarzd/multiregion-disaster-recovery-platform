# Runtime evidence

`drctl report` emits schema `com.portfolio.dr.recovery-report/2.1.0`. It includes run/scenario IDs,
timestamp authorities, raw RTO/RPO inputs, derived values, reconciliation, approvals, promotion and
failback state. Events form a SHA-256 previous-hash chain; the canonical report body has its own
SHA-256. `verify_report` detects edits. A local file is explicitly `UNSIGNED` and
`NOT_PROVIDED_BY_LOCAL_FILE`; hashing is tamper-evidence, not immutability.

| Field | Evidence source |
|---|---|
| incident/failure times | declaration and injected fault/corruption event |
| recovery start / infrastructure ready | state transitions / AWS describe-ready observation |
| recovery point | PITR selection recorded before restore |
| validation completion | successful aggregate gate transition |
| promotion / failback | approval-bearing state transitions |
| RTO / RPO | timestamp arithmetic in `data_validation.py` |
| counts/checksums/newest transaction | reads from expected and isolated recovered datasets |
| S3 checks | version lists, checksums, replica status, quarantine read |

Local evidence proves only controller behavior and carries `LOCAL_SIMULATION`. `AWS_RUNTIME` refuses
generation without an evidence signer, a passing clock-skew observation, and allow-listed synced
controller/fault-injector/AWS/DynamoDB timestamp authorities; a raw synthetic payload time is not
accepted as authority. AWS evidence uses
the ECC KMS signer and Object-Lock archive adapter; signature verification, bucket retention, and
archive durability remain **PENDING AWS** until executed. AWS evidence should additionally include
CloudWatch query IDs/metric timestamps, request IDs, table/bucket ARNs, object version IDs, and
signed workflow/run references after redaction.

Recommended production retention: upload the JSON and hashed raw probes to an Object Lock evidence
bucket in a security account, KMS-sign the report digest, and attach it to the incident/change. Do
not store access tokens, API headers, plaintext payloads, or Terraform state in evidence.

The CloudWatch namespace is `Portfolio/DisasterRecovery`. Its exact metric contract is
`RegionHealthy` (Count, Region dimension), `RecoveryState` (None), `RestoreDuration` (Seconds),
`ValidationResult` (Count), `ReplicationLag` (Milliseconds), `MeasuredRTO`/`MeasuredRPO` (Seconds),
`LastSuccessfulDrill` (epoch Seconds), and `FailureCode` (Count with a bounded code). Metrics use
only `Project`, bounded `Scenario`, and where applicable `Region`/bounded `Code`; run and scenario
IDs remain structured-log fields to avoid unbounded cardinality. Actual emission is **PENDING AWS**.
