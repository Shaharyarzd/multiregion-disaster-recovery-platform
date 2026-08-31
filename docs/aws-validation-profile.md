# Minimal AWS validation profile

## Executed topology

| Boundary | Resources | Lifetime |
|---|---|---|
| Region A (`us-east-1`) | HTTP API, Lambda, log group/alarm/dashboard, DynamoDB replica/PITR, KMS data key, source S3 bucket | validation session |
| Region B (`us-west-2`) | HTTP API, Lambda, log group/alarm/dashboard, DynamoDB replica/PITR, KMS data key, replica S3 bucket | validation session |
| global/shared | Global Table definition and three separated GitHub OIDC roles | validation session |
| evidence control | ECC KMS signing key and versioned Object-Lock evidence bucket (7-day governance retention) | bucket/key retained through evidence review and key deletion window |
| corruption drill only | isolated PITR table, quarantine S3 key/version, bounded replay target | temporary; delete after accepted evidence |

No VPC, NAT, EC2, EKS, RDS, ALB/NLB, Route 53 zone, custom domain, or Global Accelerator is required.

## Routing proof selected

Milestone 2 executes the synthetic validation client directly against both regional API Gateway
endpoints. It polls API/read/write health every 5 seconds; three consecutive failures quarantine an
endpoint. Routing recovery is the first successful deterministic write/read through the survivor
after quarantine, with the failed endpoint absent from the selection set. Expected controller
convergence is detection window (nominally 15 seconds) plus one request; the observed value is
reported and no DNS/anycast claim is made.

Route 53 latency/weighted active-active records with endpoint health checks remain the lowest-cost
production target when an owned domain/custom API domain exists. Global Accelerator is rejected for
this demo because standard endpoints are ALB, NLB, EC2, or Elastic IP—not API Gateway—and would add
non-serverless infrastructure and a fixed accelerator charge. See [Route 53 active-active](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-failover-types.html)
and [Global Accelerator endpoints](https://docs.aws.amazon.com/global-accelerator/latest/dg/about-endpoints.html).

## Cost envelope (USD, pricing review 2026-08-31)

- Three customer-managed KMS keys are the clearest fixed cost: $1/key-month, prorated hourly,
  approximately **$0.0041/hour total**. Signing/API requests should remain within the KMS free-tier
  request volume. [AWS KMS pricing](https://aws.amazon.com/kms/pricing/)
- Lambda, HTTP API, DynamoDB on-demand/global replicated writes, PITR/restore, S3/CRR, CloudWatch,
  and data transfer use tiny synthetic volumes. PITR and restore are size-based; global-table writes
  are billed per replica. [DynamoDB pricing](https://aws.amazon.com/dynamodb/pricing/)
- Route 53 and Global Accelerator are not provisioned. For comparison, Global Accelerator has a
  $0.025/full-or-partial-hour fixed charge plus transfer/public-IPv4 charges; Route 53 hosted zones
  and health checks have separate charges. [Global Accelerator pricing](https://aws.amazon.com/global-accelerator/pricing/),
  [Route 53 pricing](https://aws.amazon.com/route53/pricing/)

Budget envelope: **$0.01–$0.10/hour while the tiny topology is active; $1 expected for a 4–6 hour
session; $10 conservative maximum** including restore, logs/metrics, transfer, cleanup delays, and
price variance. These are guardrails, not a quote. Confirm both selected Regions in AWS Pricing
Calculator immediately before apply. Stop if the plan adds charge-bearing network/compute resources.

The locked evidence object/version and its primary-region KMS key are intentionally retained for
seven days. The key must remain enabled until read-back/retention review completes; the encrypted
object is only a few kilobytes, so retained S3 storage is negligible and the key contributes roughly
one week of its monthly fixed price. Teardown must not use governance bypass.
