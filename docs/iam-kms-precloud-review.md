# IAM/KMS pre-cloud review

| Activity | Required authority | Important actions / boundary |
|---|---|---|
| bootstrap | owner-operated one-time identity path | create OIDC provider/roles/policies; not the runtime deploy role |
| normal regional deployment | deploy role | scoped Lambda/API/CloudWatch/log and portfolio IAM role management/pass-role |
| global data deployment | owner-authorized global stack | DynamoDB/S3/KMS creation; `prevent_destroy`; no routine recovery |
| discovery/validation | recovery role | DynamoDB describe/scan, S3 list/get versions, CloudWatch read, API invocation |
| PITR restore | recovery role + protected environment | `DescribeContinuousBackups`, `RestoreTableToPointInTime`, describe/scan target |
| S3 version recovery | recovery role | list/get source version; put only quarantine/evidence objects; KMS decrypt/encrypt |
| promotion/failback | protected environment + controller approval | no generic table delete/overwrite; bounded replay and routing change are separate reviewed steps |
| evidence | recovery role | `kms:Sign` on project-tagged signing key; `s3:PutObject` with checksum/Object Lock; CloudWatch DR namespace |

Normal deployment retains an explicit deny for PITR and Route 53 changes. Recovery and signing use
the protected recovery environment. No managed Administrator policy is present.

**PENDING AWS:** IAM policy simulation for API Gateway ARNs, IAM create/update-role permissions,
KMS key-policy grants to DynamoDB/S3 replication, S3 Object Lock headers, CloudWatch metric reads,
and `iam:PassRole` resource/`iam:PassedToService` conditions. Runtime errors must be added narrowly;
do not respond by attaching broad service wildcards.

KMS uses independent regional data keys because S3 treats multi-Region keys as regional keys for
replication behavior. The evidence key is asymmetric `ECC_NIST_P256` and only signs/verifies. Key
policy, replication-service access, scheduled-deletion ordering, and 30-day deletion-window cost are
**PENDING AWS**. See [S3 KMS replication](https://docs.aws.amazon.com/AmazonS3/latest/userguide/replication-config-for-kms-objects.html).

