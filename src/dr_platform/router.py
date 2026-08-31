"""Reproducible synthetic active-active router for local/executed validation.

This is deliberately not represented as globally managed DNS or anycast routing.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Endpoint:
    region: str
    url: str
    healthy: bool


class SyntheticRouter:
    def select(self, transaction_key: str, endpoints: list[Endpoint]) -> Endpoint:
        healthy = sorted((item for item in endpoints if item.healthy), key=lambda item: item.region)
        if not healthy:
            raise RuntimeError("No healthy endpoint is available")
        bucket = int(hashlib.sha256(transaction_key.encode()).hexdigest(), 16) % len(healthy)
        return healthy[bucket]

    def assertions(self, endpoints: list[Endpoint]) -> dict[str, bool]:
        healthy = [item for item in endpoints if item.healthy]
        return {
            "at_least_one_writable_region": bool(healthy),
            "failed_regions_quarantined": all(
                not item.healthy for item in endpoints if not item.healthy
            ),
            "no_double_promotion": len({item.region for item in healthy}) == len(healthy),
        }
