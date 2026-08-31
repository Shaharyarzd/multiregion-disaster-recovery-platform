"""Regional health and active-active safety decisions."""

from __future__ import annotations

from dataclasses import dataclass

from dr_platform.types import RegionHealth


@dataclass(frozen=True)
class RoutingDecision:
    writable_regions: tuple[str, ...]
    quarantined_regions: tuple[str, ...]
    degraded: bool
    reason: str


def decide_routing(checks: list[RegionHealth]) -> RoutingDecision:
    if not checks:
        return RoutingDecision((), (), True, "No regional health evidence")
    healthy = tuple(sorted(check.region for check in checks if check.healthy))
    unhealthy = tuple(sorted(check.region for check in checks if not check.healthy))
    if not healthy:
        return RoutingDecision((), unhealthy, True, "No region is safe for writes")
    return RoutingDecision(
        healthy,
        unhealthy,
        bool(unhealthy),
        (
            "Active-active"
            if not unhealthy
            else "Failed region quarantined; survivor remains writable"
        ),
    )


def failback_safe(original: RegionHealth, survivor: RegionHealth, data_consistent: bool) -> bool:
    return original.healthy and survivor.healthy and data_consistent
