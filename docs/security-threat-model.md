# Security and threat model

## Assets and trust boundaries

Protected assets are data integrity, KMS keys, Terraform state, recovery evidence, regional routing,
and the authority to promote/fail back. Trust boundaries exist at GitHub OIDC→AWS, API Gateway→
Lambda, Lambda→DynamoDB, S3 replication→destination KMS, and operator→`drctl` approvals.

| Threat | Control | Residual risk / validation |
|---|---|---|
| stolen long-lived CI key | OIDC only; short session; repository/environment `sub` conditions | GitHub org/repo compromise; require protected environment reviewers |
| ordinary deploy triggers recovery | distinct roles; deploy-role explicit deny for PITR/routing | IAM policy must be simulated in AWS |
| compromised Lambda reads broad data | table-specific IAM and scoped log group; no secret variables | DynamoDB table is shared across regions by design |
| public object exposure | four S3 public-access blocks, no ACL, SSE-KMS | bucket policies/Access Analyzer require cloud validation |
| replicated encrypted object unreadable | source/destination KMS permissions and version validation | KMS policy integration must be exercised |
| corrupt data promoted | isolated PITR, exact keys/checksum/freshness gates, protected approval | bad expected manifest could validate wrong truth |
| fabricated evidence | report generated from state; PASS derived from checks; recursive redaction | local files are mutable; production should sign/store immutably |
| stale region reintroduced | two health checks + cross-region consistency + approval | asynchronous replication may change after validation |
| injection/PII in demo | fixed synthetic schema/classification; no user identity | API authorization/rate control needed for public production |

## IAM boundary

Bootstrap creates three GitHub roles. The deploy role manages portfolio-prefixed runtime resources but
cannot perform PITR or Route 53 changes. The recovery role restores only prefixed tables, works with
prefixed versioned buckets, and emits only the DR metric namespace. The evidence role alone signs
and archives reports. Real production should add
permissions boundaries, SCPs, session tags, CloudTrail data events, IAM Access Analyzer, and a
break-glass path with time-bound access.

No secrets are Terraform inputs. Account IDs/ARNs and recovery metadata are not secrets. State must
use encrypted, locked remote storage with restricted readers; backend creation is deliberately not
embedded in the same state it protects.

## Approval integrity

The local CLI records an approver/reference to demonstrate control flow, not identity assurance.
AWS promotion/failback must run behind a GitHub protected environment or an equivalent change system
with independent reviewers. The actor performing normal deployment should not self-approve high-
impact recovery in production.

### Disposable Milestone 2 exception

For the single disposable validation run, the `aws-deployment`, `aws-recovery-approval`, and
`aws-evidence-approval` environments are restricted to `main` and require `Shaharyarzd` review, but
GitHub self-review is explicitly enabled by owner authorization. This proves environment-bound OIDC
subjects and approval gates, not separation of duties. It must be reported as a demo exception.
The production target requires a different authorized reviewer from the workflow initiator and
`prevent_self_review=true`; no production promotion or failback may inherit this exception.
