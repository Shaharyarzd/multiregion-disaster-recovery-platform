# IAM/KMS pre-cloud review

| Activity | Required authority | Important actions / boundary |
|---|---|---|
| bootstrap | owner-operated one-time identity path | create OIDC provider/roles/policies; not the runtime deploy role |
| normal regional deployment | deploy role | scoped Lambda/API/CloudWatch/log and portfolio IAM role management/pass-role |
| global data deployment | deploy role in owner-approved environment | scoped DynamoDB/S3/KMS creation and cleanup; `prevent_destroy`; PITR restore explicitly denied |
| discovery/validation | recovery role | DynamoDB describe/scan, S3 list/get versions, CloudWatch read, API invocation |
| PITR restore | recovery role + protected environment | `DescribeContinuousBackups`, `RestoreTableToPointInTime`, describe/scan target |
| S3 version recovery | recovery role | list/get source version; put only quarantine/evidence objects; KMS decrypt/encrypt |
| promotion/failback | protected environment + controller approval | no generic table delete/overwrite; bounded replay and routing change are separate reviewed steps |
| evidence | separate evidence role | `kms:Sign/Verify` only on the evidence key; Object Lock write/read-back; explicit deny on governance bypass |

Normal deployment retains an explicit deny for PITR and Route 53 changes. Recovery and evidence use
different protected environments. The exact action inventory is machine-checked in
`config/iam-action-paths.json`; it contains no service action wildcard or managed Administrator
policy. The offline simulator checks intended positives and obvious resource-prefix negatives but
is not AWS policy evaluation.

**PENDING AWS PREFLIGHT:** Access Analyzer validation and IAM policy simulation for API Gateway ARNs, IAM create/update-role permissions,
KMS key-policy grants to DynamoDB/S3 replication, S3 Object Lock headers, CloudWatch metric reads,
and `iam:PassRole` resource/`iam:PassedToService` conditions. Runtime errors must be added narrowly;
do not respond by attaching broad service wildcards.

KMS uses independent regional data keys because S3 treats multi-Region keys as regional keys for
replication behavior. Explicit key policies delegate an enumerated set of administration/use actions
to the account principal; IAM then narrows the deploy, replication, recovery, and evidence roles.
Service-created grants are limited in the deploy policy by `kms:GrantIsForAWSResource`. The evidence
key is asymmetric `ECC_NIST_P256` and exposes only signing-key operations. Policy simulation,
service-created grants, replication-service access, scheduled-deletion ordering, and the 30-day
deletion window are **PENDING AWS PREFLIGHT**. Locally, P-256 DER ECDSA signing/verification uses the identical
canonical SHA-256 evidence digest and `ECDSA_SHA_256` contract. KMS authorization, grants and
service integration still require AWS proof. See [S3 KMS replication](https://docs.aws.amazon.com/AmazonS3/latest/userguide/replication-config-for-kms-objects.html).
