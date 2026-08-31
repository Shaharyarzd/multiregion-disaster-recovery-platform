# Runtime evidence

`drctl report` emits `evidence/recovery-report.json`. Values come from the incident event log and
validation result; `validation_result` is computed and cannot be passed as a CLI value. Sensitive-
looking fields are recursively redacted.

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

Local evidence proves only controller behavior. It must carry `evidence_source: generated-by-drctl`
and `evidence_scope: LOCAL_SIMULATION`. It is not an AWS runtime PASS. AWS evidence should additionally include
CloudWatch query IDs/metric timestamps, request IDs, table/bucket ARNs, object version IDs, and
signed workflow/run references after redaction.

Recommended production retention: upload the JSON and hashed raw probes to an Object Lock evidence
bucket in a security account, KMS-sign the report digest, and attach it to the incident/change. Do
not store access tokens, API headers, plaintext payloads, or Terraform state in evidence.

Useful CloudWatch namespace `Portfolio/DisasterRecovery` signals are `RegionHealthy`,
`LatestRecoveryPointAgeSeconds`, `RecoveryValidationPassed`, `RecoveryDurationSeconds`,
`MeasuredRTOSeconds`, `MeasuredRPOSeconds`, and `LastSuccessfulDrDrillEpoch`. The Terraform dashboard
defines the key result metrics; Milestone 2 wires controller emission and alarm thresholds.
