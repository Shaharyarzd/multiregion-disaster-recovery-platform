# Milestone 2 controlled-run contract

## PITR lifecycle

The source must be `ACTIVE`, PITR `ENABLED`, and the chosen UTC recovery point must be inside the
observed earliest/latest restorable window. `drctl` records that point before requesting
`RestoreTableToPointInTime`. The target name is deterministic:
`<source>-recovery-<sanitized-run-id>`. The request overrides billing to on-demand and encryption to
the recovery-region KMS key. It creates no replica.

Restore completion is only infrastructure availability. Before validation, the controller verifies
`ACTIVE`, the exact KMS key, PITR re-enabled, required tags, intended TTL state, intended stream
state, and no replicas. Alarms/autoscaling are not used by the on-demand demo; a production restore
must re-create them. TTL and streams are opt-in because enabling either without the application
contract would be unsafe.

The restored table remains isolated. The bounded portfolio policy is PITR base plus unique,
uncorrupted writes between recovery point and corruption cutoff. Pre-point missing records,
different content under the same key, restored records after the selected point, and writes after
the corruption cutoff block promotion. Replay is dry-run first, at most 25 candidates, then an
approved conditional `PutItem`. Identical duplicates are idempotent; a different existing item or
partial failure remains blocked and retryable. This is not a universal production merge engine.

## Replication measurement

- DynamoDB: write a digest-bearing marker in Region A with controller-synced UTC and poll Region B
  `GetItem(ConsistentRead=true)` until the exact marker/digest appears or 60 seconds expires.
- S3: upload a tiny checksum-bearing marker and poll destination `HeadObject` until status is
  `REPLICA` and marker metadata/digest match, or 300 seconds expires.

Both use exponential 0.5-to-5-second backoff. Evidence records source write, first matching
observation, attempts, timeout, and client-observed convergence. DynamoDB `ReplicationLatency` is
also captured as an AWS aggregate but is labelled separately; no fixed sleep is evidence.

## Evidence archive and teardown

The evidence role KMS-signs the canonical report digest, uploads canonical JSON at
`evidence/<scenario-id>/<run-id>/recovery-report.json` with SHA-256 checksum and seven-day
`GOVERNANCE` retention, downloads the exact version, checks retention, canonical bytes and KMS
signature. The role is explicitly denied `s3:BypassGovernanceRetention`.

Teardown order is: temporary recovery/quarantine; regional Lambda/API; S3 replication rule/role;
DynamoDB replicas/table; S3 data buckets; KMS dependencies; CloudWatch; evidence archive. The guard
rejects an unapproved region or shared/bootstrap target. The evidence object/version and the key
needed to decrypt it are deliberately retained through review; locked versions are not deletable
early. Remove the bucket only after retention expires, verification completes, and the owner starts
a separate cleanup run.

## Single-run phases and stop conditions

| Phase | Proof | Retry | Stop condition |
|---|---|---|---|
| preflight | `config/aws-preflight.json` all pending checks resolved | fix policy/quota only | any unresolved prerequisite |
| deploy | scoped plans, tagged two-region resources | idempotent Terraform plan/apply | unexpected resource/cost or access widening |
| baseline | regional API R/W plus observed DynamoDB/S3 convergence | new marker | either replica times out |
| regional fault | three failed endpoint probes, survivor R/W, synthetic-router recovery | probe only | survivor write/read fails |
| PITR | isolated active/configured target | describe/config calls | point outside window or any config proof fails |
| reconcile | deterministic dry-run and approved conditional replay | per-item idempotent retry | conflict, stale validation, partial failure |
| evidence | KMS signature plus exact-version Object Lock read-back | new version, never overwrite evidence | signature/retention/read-back failure |
| failback | both regional APIs and exact data/replication proof after promotion | revalidate | stale or inconsistent original region |
| cleanup | guarded ordered disposal; retained evidence listed | repeat safe stage | wrong region/shared target/locked evidence |

AWS Access Analyzer, real principal simulation, region/quota availability, service-created KMS
grants, PITR timing, Global Table convergence, S3 CRR, Object Lock read-back, CloudWatch acceptance,
and cleanup behavior are `PENDING AWS PREFLIGHT` or `PENDING AWS` until observed. No AWS apply is
authorized by this document.
