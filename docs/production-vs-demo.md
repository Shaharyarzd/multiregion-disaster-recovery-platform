# Production versus demo

| Concern | Low-cost executed demo | Production target |
|---|---|---|
| global routing | deterministic client-side active-active router | Route 53 health routing + ARC safety rules or Global Accelerator |
| API edge | regional default HTTP API endpoints | custom domains, WAF, auth, throttling, Shield as required |
| data | dozens of synthetic records | capacity modeling, quotas, backpressure, conflict semantics, streams/replay |
| corruption truth | synthetic expected manifest | immutable ledger/event source, business reconciliation rules |
| evidence | generated local/cloud JSON | signed immutable cross-account archive and audit integration |
| approvals | CLI fields / GitHub environment design | independent change approvers, JIT access, break-glass audit |
| monitoring | CloudWatch metrics/logs/alarms | multi-vantage synthetics, paging, SLO/error budgets, centralized security logs |
| DR measurement | individual observed drill | scheduled drill distributions, p95 objectives, control compliance |
| S3 | one-way CRR, delete marker excluded | replication time control if required, bidirectional design only with ownership rules |

The demo router validates request behavior after endpoint removal. It does not validate TTL/resolver
caches, BGP/anycast convergence, certificate/custom-domain dependencies, or public internet paths.
Those are explicit Milestone 2/production tests.

DynamoDB Global Tables provide multi-active writes but last-writer conflict resolution may be wrong
for some businesses. Deterministic IDs and conditional create reduce duplicates in this synthetic
domain. A real financial/order system may require an immutable regional write journal, idempotency
tokens, causal rules, or a home-region ownership model.

