from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

from dr_platform.application import MemoryRepository, create_transaction, deterministic_id, handler


def event(method: str, path: str, body: dict | None = None) -> dict:
    return {
        "requestContext": {"http": {"method": method}},
        "rawPath": path,
        "body": json.dumps(body) if body else None,
    }


def test_deterministic_id() -> None:
    assert deterministic_id("seed", "time") == deterministic_id("seed", "time")
    assert deterministic_id("seed", "time") != deterministic_id("other", "time")


def test_create_and_fetch_transaction(monkeypatch) -> None:
    monkeypatch.setenv("AWS_REGION", "region-a")
    repository = MemoryRepository()
    created = create_transaction(
        repository,
        seed="order-1",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        region="region-a",
        amount_cents=500,
    )
    result = handler(event("GET", f"/transactions/{created['transaction_id']}"), None, repository)
    assert result["statusCode"] == 200
    assert json.loads(result["body"])["data_classification"] == "SYNTHETIC_PUBLIC"


def test_health_and_unknown_route() -> None:
    repository = MemoryRepository()
    assert handler(event("GET", "/health"), None, repository)["statusCode"] == 200
    assert handler(event("DELETE", "/anything"), None, repository)["statusCode"] == 404


def test_post_and_list(monkeypatch) -> None:
    monkeypatch.setenv("AWS_REGION", "region-b")
    repository = MemoryRepository()
    created = handler(
        event("POST", "/transactions", {"seed": "x", "amount_cents": 99}),
        None,
        repository,
    )
    listed = handler(event("GET", "/transactions"), None, repository)
    assert created["statusCode"] == 201
    assert len(json.loads(listed["body"])["items"]) == 1


def test_dynamodb_decimal_values_are_json_serializable() -> None:
    repository = MemoryRepository()
    repository.items["txn-decimal"] = {
        "transaction_id": "txn-decimal",
        "timestamp": "2026-01-01T00:00:00Z",
        "region": "us-east-1",
        "amount_cents": Decimal("101"),
        "payload": "synthetic-order:decimal",
    }
    listed = handler(event("GET", "/transactions"), None, repository)
    assert listed["statusCode"] == 200
    assert json.loads(listed["body"])["items"][0]["amount_cents"] == 101
