# ADR-004: Synthetic routing for demo validation

**Status:** Accepted

Use a deterministic client-side router for cheap, repeatable local/AWS validation. It can prove that
unhealthy endpoints are excluded and survivor transactions work; it cannot prove managed global
DNS/anycast behavior. Production targets Route 53 with health/ARC controls or Global Accelerator,
custom domains, and multi-vantage probes. Documentation must preserve this distinction.

