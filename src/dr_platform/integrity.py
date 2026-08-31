"""Canonical hashing and tamper-evident event chains."""

from __future__ import annotations

import hashlib
import json
from typing import Any

GENESIS_HASH = "0" * 64


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def chained_event(event: dict[str, Any], previous_hash: str, sequence: int) -> dict[str, Any]:
    chained = {**event, "sequence": sequence, "previous_hash": previous_hash}
    return {**chained, "event_hash": sha256_hex(chained)}


def verify_event_chain(events: list[dict[str, Any]]) -> bool:
    previous = GENESIS_HASH
    for sequence, event in enumerate(events, start=1):
        payload = {key: value for key, value in event.items() if key != "event_hash"}
        if (
            event.get("sequence") != sequence
            or event.get("previous_hash") != previous
            or event.get("event_hash") != sha256_hex(payload)
        ):
            return False
        previous = str(event["event_hash"])
    return True
