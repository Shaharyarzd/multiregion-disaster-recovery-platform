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
accepted as authority. AWS evidence uses the ECC KMS signer and Object-Lock archive adapter.

## Milestone 2 AWS runtime result

The controlled run used synthetic data in account `293951765338`, Regions `us-east-1` and
`us-west-2`. These are runtime observations, not extrapolations from Terraform.

| Proof | Result | Observed evidence |
|---|---|---|
| Scenario A: regional endpoint outage | **PASS** | synthetic-router RTO `0.897355 s`; 2/4 requests failed during quarantine; survivor read/write and safe active-active return passed |
| DynamoDB convergence | **PASS** | `837.783 ms` east-to-west and `1394.320 ms` west-to-east |
| Scenario B: isolated PITR recovery | **PASS** | target `portfolio-dr-recovery-github-34097882635`; restore `901.812355 s`; configuration gates all passed; 8/8 records recovered |
| Reconciliation | **PASS** | 21 approved replay candidates applied; duplicate replay classified all 21 as idempotent; two bounded conditional repairs completed; unresolved conflicts `0` |
| Promotion/failback safety | **PASS** | conflicts and pre-repair state blocked promotion; stale validation and stale consistency proof were rejected; approved promotion and failback returned both Regions healthy |
| RPO | **PARTIAL** | bounded observed interval `323.244446–323.957230 s`; reported value `323.957230 s` |
| S3 CRR/version recovery | **PASS** | observed lag `23813.423 ms`; replica status, digest equality, delete marker, and exact-version recovery passed |
| CloudWatch contract | **PASS** | AWS accepted and returned all nine metrics in `Portfolio/DisasterRecovery`; dimensions remained low-cardinality |
| Crash/resume | **PASS** | persisted `VALIDATING` checkpoint resumed across a process boundary; approval, replay idempotency, and promotion gates remained enforced |

The RPO is deliberately bounded rather than presented as causally exact. DynamoDB Global Tables use
last-writer-wins conflict resolution, application timestamps are evidence fields rather than a
global order, and the measurement uses the validated PITR manifest plus synced-controller/AWS
observation bounds. Observed controller skew was `652.253 ms`, below the `1500 ms` limit.

The final canonical report (`github-34097882635`) has SHA-256
`16241016f4f920da50bc1e5bd647e16f7d35467d8c5988f3972f731c32b03f9f`. AWS KMS signed it with
`ECDSA_SHA_256`; verification passed before upload and after exact-version read-back. Report,
signature, and digest versions are retained in GOVERNANCE mode until `2026-09-14T10:01:57Z`.
Failed attempts remain in the signed report rather than being rewritten out of the audit trail.

Cleanup completed at `2026-09-07T15:03:09Z`. Exact-resource checks confirmed zero disposable
runtime or replication resources: both APIs, both runtime S3 buckets, the Global Table, isolated
PITR table, regional runtime resources, and all four temporary Project #2 IAM roles are absent.
Only the Object-Locked evidence bucket, its primary encryption key, and the asymmetric signing key
remain. The secondary regional key is `PendingDeletion`. Bootstrap Terraform state contains data
sources only, and the temporary source-user permission and local bootstrap profiles were removed.

Recommended production retention: upload the JSON and hashed raw probes to an Object Lock evidence
bucket in a security account, KMS-sign the report digest, and attach it to the incident/change. Do
not store access tokens, API headers, plaintext payloads, or Terraform state in evidence.

The CloudWatch namespace is `Portfolio/DisasterRecovery`. Its exact metric contract is
`RegionHealthy` (Count, Region dimension), `RecoveryState` (None), `RestoreDuration` (Seconds),
`ValidationResult` (Count), `ReplicationLag` (Milliseconds), `MeasuredRTO`/`MeasuredRPO` (Seconds),
`LastSuccessfulDrill` (epoch Seconds), and `FailureCode` (Count with a bounded code). Metrics use
only `Project`, bounded `Scenario`, and where applicable `Region`/bounded `Code`; run and scenario
IDs remain structured-log fields to avoid unbounded cardinality. Milestone 2 verified actual AWS
ingestion and read-back for every metric in this contract.
