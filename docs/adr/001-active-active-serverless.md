# ADR-001: Active-active serverless regional runtimes

**Status:** Accepted

Use Lambda and regional HTTP APIs in two continuously active regions with a DynamoDB Global Table.
This keeps both paths exercised and avoids idle compute/NAT cost. Conditional deterministic writes
limit duplicates. Consequences: replicated writes and two control surfaces cost more; Global Tables
replicate logical corruption and use last-writer conflict behavior, so PITR/reconciliation remains
mandatory. EKS, PostgreSQL, and Argo are excluded because they distract from recovery correctness
and are covered by Portfolio Project #1.

