# Disaster scenarios

## Scenario 1: regional outage

```mermaid
sequenceDiagram
  participant V as Validation client
  participant A as Region A
  participant B as Region B
  participant D as drctl
  participant E as Evidence
  V->>A: health/read/write probe
  A--xV: unavailable
  V->>B: health/read/write probe
  B-->>V: healthy
  D->>D: declare incident + timestamp
  D->>A: quarantine from synthetic route set
  D->>B: create deterministic transaction
  B-->>D: read-back succeeds
  D->>D: assert one writable survivor, no duplicate route target
  D->>E: measured RTO + routing/transaction evidence
```

Inject the failure only through the test harness in Milestone 1. In AWS, an owner-authorized drill
may disable the validation client’s Region A endpoint or use a scoped fault mechanism; it must not
destroy data. Success means the Region B read/write path is observed, the failed endpoint is
excluded, and the RTO clock stops at the first successful survivor transaction—not when an alarm
changes state.

## Scenario 2: logical DynamoDB corruption

```mermaid
flowchart LR
  Bad[Delete/corrupt synthetic records] --> Detect[Detect and timestamp corruption]
  Detect --> Point[Select latest safe point]
  Point --> PITR[Restore to isolated table]
  PITR --> Validate{Count + keys + checksum + freshness + API + S3 + write}
  Validate -->|fail| Isolate[Keep isolated / choose earlier point]
  Validate -->|pass| Gate[Await protected approval]
  Gate -->|approved| Reconcile[Controlled reconcile or switch]
  Reconcile --> Observe[Post-promotion observation]
  Observe --> Failback[Validate original path + approve failback]
```

The restored table is never named or wired as production during restore. “Amount lost” is the
missing expected key count; RPO is the corruption timestamp minus newest recovered transaction.
Unexpected keys also fail validation. Production reconciliation strategy—table switch, selective
copy, or immutable replay—must be chosen from the incident’s corruption scope.

## Scenario 3: S3 deletion/stale replica/version recovery

Upload only `examples/supporting-data`. Record source version IDs and SHA-256 values, verify the
replication status and destination version, then delete the current source version. The controller:

1. lists source and replica versions (delete markers included in evidence);
2. selects the last non-delete version before the incident;
3. copies it to a quarantine prefix/bucket, never over the live key;
4. checks bytes/checksum, encryption, metadata, and replica freshness;
5. requests approval before restoring the live key.

A missing/stale replica does not automatically fail DynamoDB recovery, but it fails the composite
application recovery gate when that object belongs to the expected manifest.

