# Architecture

## Decision frame

The system keeps both regional APIs live and accepts writes in either region. DynamoDB Global
Tables resolve physical replication; application transaction IDs are deterministic and writes are
conditional, limiting duplicate creation. This is not a generic “promote secondary” design: during
a regional outage the failed endpoint is quarantined while the healthy endpoint remains active.
During logical corruption, both replicas may contain the bad write, so traffic failover is
insufficient—PITR restoration occurs into an isolated table.

```mermaid
flowchart LR
  subgraph Traffic[Traffic boundary]
    Client[Client traffic] --> Router{Healthy endpoint set}
  end

  subgraph A[Region A — active]
    APIA[HTTP API] --> LambdaA[Transaction Lambda]
    LambdaA --> DDBA[(Global Table replica A)]
    S3A[(Versioned S3 + KMS A)]
  end

  subgraph B[Region B — active]
    APIB[HTTP API] --> LambdaB[Transaction Lambda]
    LambdaB --> DDBB[(Global Table replica B)]
    S3B[(Versioned S3 + KMS B)]
  end

  Router --> APIA
  Router --> APIB
  DDBA <-->|active-active replication| DDBB
  S3A -->|encrypted CRR| S3B

  subgraph Recovery[Protected recovery control plane]
    Controller[drctl] --> Restore[(Isolated PITR table)]
    Restore --> Validate{Validation gates}
    Validate -->|pass| Approval[Human approval]
    Approval --> Reconcile[Bounded reconciliation]
    Controller --> Evidence[CloudWatch + signed report]
  end

  Reconcile -.->|conditional writes| DDBA
```

## Failure semantics

- **Regional outage:** health requires API, read, and write probes. An unhealthy region is removed
  from the writable set. The survivor receives a deterministic synthetic transaction and reads it
  back. There is no singular leader flag to “promote,” so double-promotion is avoided; endpoint
  eligibility is health-evidence based and failed endpoints are quarantined.
- **Logical corruption:** Global Tables correctly replicate the corruption. The controller selects
  the latest safe point before the corruption, restores a differently named table, and denies
  promotion until key set, count, checksum, freshness, S3, and post-recovery transaction gates pass.
- **Failback:** the old region is never blindly reintroduced. API health, read/write tests,
  cross-region comparison, freshness, and approval are required before returning to `HEALTHY`.

## Routing truthfulness

The executable demo uses `SyntheticRouter`, a deterministic client-side endpoint selector that
removes failed endpoints. It proves application behavior and routing assertions in a reproducible,
low-cost test. It does **not** prove DNS propagation, anycast convergence, resolver caching, or
internet-path health. The production target is Route 53 health-aware routing with custom regional
API domains, safety controls, and multi-vantage probes. Global Accelerator would require a
compatible endpoint layer rather than targeting API Gateway directly and was not part of this run.

## State ownership

| State | Owns | Failure blast radius |
|---|---|---|
| `bootstrap` | GitHub OIDC and deploy/recovery roles | identity only; rare changes |
| `global` | Global Table, PITR, KMS keys, S3/CRR | durable multi-region data plane |
| `region-a` | API, Lambda, role, logs, alarms, dashboard A | Region A runtime only |
| `region-b` | API, Lambda, role, logs, alarms, dashboard B | Region B runtime only |

Regional roots receive global outputs as explicit pipeline inputs. This permits independent plan
and recreation without granting every stack write access to shared state.
