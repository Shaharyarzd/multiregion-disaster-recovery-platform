# Cost model

The demo avoids EC2, NAT gateways, EKS, RDS, and always-on workers. Cost drivers are API Gateway
requests, Lambda requests/duration, DynamoDB on-demand reads/writes and replicated writes, PITR
storage/restore, tiny S3 version/replication storage, KMS key-month/API calls, and CloudWatch logs/
metrics. Three KMS keys are likely the dominant fixed demo cost while provisioned.

## Validation-session profile

A drill uses tens of transactions and a few kilobytes of objects, then removes recoverable test
resources under owner control. Usage-based compute/storage should be pennies or below; three customer-
managed KMS keys, PITR, alarms, and retained logs may make a short session cost more than workload
traffic. Exact prices vary by region and date, so review the AWS Pricing Calculator immediately
before Milestone 2. The bounded budget is in
[AWS validation profile](aws-validation-profile.md).

Set an account budget and anomaly alert before apply. Tag every resource. Retain KMS keys through
their deletion window and account for that cost. PITR restore creates another table, so delete the
isolated table only after evidence capture and explicit owner authorization.

## Milestone 2 observed cost posture

The conservative wall-clock validation window was `2026-09-01T08:54:46Z` through
`2026-09-07T15:03:09Z` (`6d 6h 8m 23s`), including owner-approval pauses rather than continuous
execution. All usage-bearing runtime resources were removed. Based on the documented regional
prices and tiny observed request/data volumes, the estimated validation charge is **USD 1.50–3.00**;
the provider invoice remains authoritative and can lag. This remains below the USD 10 stop ceiling.

Two enabled customer-managed keys and a 30,861-byte Object-Locked report plus its tiny signature
and digest remain intentionally. Their expected residual cost is approximately **USD 0.067/day for
the two keys plus negligible S3 storage** until owner-approved evidence teardown. The secondary key
is already `PendingDeletion`; no other disposable workload remains.

## Active-active trade-off

Active-active pays for two runtime/control surfaces, replicated writes, cross-region transfer,
regional data keys, duplicated logs/alarms, and operational testing. It reduces regional service RTO and exercises
both regions continuously, but does not solve logical corruption. Production may justify ARC,
Global Accelerator, multi-vantage monitoring, immutable evidence storage, longer retention, and
24×7 alerting; the portfolio demo intentionally excludes those fixed/operational costs.
