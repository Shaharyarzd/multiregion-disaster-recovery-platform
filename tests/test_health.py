from __future__ import annotations

from datetime import UTC, datetime

from dr_platform.health import decide_routing, failback_safe
from dr_platform.types import RegionHealth


def health(region: str, healthy: bool) -> RegionHealth:
    return RegionHealth(region, healthy, healthy, healthy, datetime.now(UTC))


def test_surviving_region_remains_writable() -> None:
    decision = decide_routing([health("region-a", False), health("region-b", True)])
    assert decision.writable_regions == ("region-b",)
    assert decision.quarantined_regions == ("region-a",)
    assert decision.degraded


def test_no_healthy_region_fails_closed() -> None:
    assert decide_routing([health("a", False), health("b", False)]).writable_regions == ()


def test_no_health_evidence_fails_closed() -> None:
    assert decide_routing([]).reason == "No regional health evidence"


def test_failback_requires_health_and_consistency() -> None:
    assert failback_safe(health("a", True), health("b", True), True)
    assert not failback_safe(health("a", True), health("b", True), False)
    assert not failback_safe(health("a", False), health("b", True), True)
