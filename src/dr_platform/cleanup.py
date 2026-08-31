"""Dependency-aware teardown plan; performs no AWS calls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from dr_platform.types import normalize_utc

STAGES = (
    "temporary-recovery",
    "regional-runtime",
    "replication-relationships",
    "dynamodb",
    "s3-data",
    "kms",
    "cloudwatch",
    "evidence-archive",
)


@dataclass(frozen=True)
class CleanupResource:
    identifier: str
    stage: str
    region: str
    shared: bool = False
    locked_until: datetime | None = None
    protects_evidence: bool = False
    replicated: bool = False


@dataclass(frozen=True)
class CleanupDecision:
    delete: tuple[str, ...]
    retain: tuple[str, ...]


def cleanup_plan(
    resources: list[CleanupResource], *, allowed_regions: set[str], now: datetime
) -> CleanupDecision:
    current = normalize_utc(now)
    seen: set[str] = set()
    for resource in resources:
        if resource.identifier in seen:
            raise ValueError("duplicate cleanup resource")
        seen.add(resource.identifier)
        if resource.stage not in STAGES:
            raise ValueError("unknown cleanup stage")
        if resource.region not in allowed_regions:
            raise ValueError("cleanup resource is outside the approved regions")
        if resource.shared:
            raise ValueError("shared/bootstrap resources require a separate owner-authorized run")
    ordered = sorted(resources, key=lambda item: (STAGES.index(item.stage), item.identifier))
    if any(item.replicated for item in resources) and not any(
        item.stage == "replication-relationships" for item in resources
    ):
        raise ValueError("replicated resources require an explicit replication-detach stage")
    delete: list[str] = []
    retain: list[str] = []
    for resource in ordered:
        if (
            resource.stage == "evidence-archive"
            or resource.protects_evidence
            or (resource.locked_until and normalize_utc(resource.locked_until) > current)
        ):
            retain.append(resource.identifier)
        else:
            delete.append(resource.identifier)
    return CleanupDecision(tuple(delete), tuple(retain))
