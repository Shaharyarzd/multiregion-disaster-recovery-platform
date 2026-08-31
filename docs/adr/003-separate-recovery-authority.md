# ADR-003: Separate deployment and recovery authority

**Status:** Accepted

Normal GitHub OIDC deployment and high-impact recovery use different roles and protected environment
subjects. Deploy explicitly denies PITR/routing changes. Recovery is resource-prefixed and short-
lived, with promotion/failback additionally guarded in controller state. This adds bootstrap and
reviewer administration but reduces accidental or compromised pipeline blast radius.

