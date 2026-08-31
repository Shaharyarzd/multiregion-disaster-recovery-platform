"""Tiny Lambda-compatible synthetic transaction API."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any, Protocol, cast


class TransactionRepository(Protocol):
    def put(self, transaction: dict[str, Any]) -> None: ...

    def get(self, transaction_id: str) -> dict[str, Any] | None: ...

    def recent(self, limit: int) -> list[dict[str, Any]]: ...


class MemoryRepository:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    def put(self, transaction: dict[str, Any]) -> None:
        self.items[transaction["transaction_id"]] = transaction

    def get(self, transaction_id: str) -> dict[str, Any] | None:
        return self.items.get(transaction_id)

    def recent(self, limit: int) -> list[dict[str, Any]]:
        return sorted(self.items.values(), key=lambda item: item["timestamp"], reverse=True)[:limit]


class DynamoRepository:
    def __init__(self, table_name: str) -> None:
        import boto3  # type: ignore[import-not-found]

        self.table = boto3.resource("dynamodb").Table(table_name)

    def put(self, transaction: dict[str, Any]) -> None:
        self.table.put_item(
            Item=transaction,
            ConditionExpression="attribute_not_exists(transaction_id)",
        )

    def get(self, transaction_id: str) -> dict[str, Any] | None:
        result = self.table.get_item(Key={"transaction_id": transaction_id}).get("Item")
        return cast(dict[str, Any] | None, result)

    def recent(self, limit: int) -> list[dict[str, Any]]:
        response = self.table.scan(Limit=limit)
        return sorted(response.get("Items", []), key=lambda item: item["timestamp"], reverse=True)


def deterministic_id(seed: str, timestamp: str) -> str:
    return "txn-" + hashlib.sha256(f"{seed}|{timestamp}".encode()).hexdigest()[:20]


def create_transaction(
    repository: TransactionRepository,
    *,
    seed: str,
    timestamp: datetime,
    region: str,
    amount_cents: int,
) -> dict[str, Any]:
    timestamp_text = timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
    transaction = {
        "transaction_id": deterministic_id(seed, timestamp_text),
        "timestamp": timestamp_text,
        "region": region,
        "amount_cents": amount_cents,
        "payload": f"synthetic-order:{seed}",
        "data_classification": "SYNTHETIC_PUBLIC",
    }
    repository.put(transaction)
    return transaction


def response(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def handler(
    event: dict[str, Any],
    _context: Any,
    repository: TransactionRepository | None = None,
) -> dict[str, Any]:
    repo = repository or DynamoRepository(os.environ["TABLE_NAME"])
    region = os.environ.get("AWS_REGION", "local-region")
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/health")
    if method == "GET" and path == "/health":
        return response(200, {"status": "healthy", "region": region})
    if method == "POST" and path == "/transactions":
        body = json.loads(event.get("body") or "{}")
        transaction = create_transaction(
            repo,
            seed=str(body.get("seed", "demo")),
            timestamp=datetime.now(UTC),
            region=region,
            amount_cents=int(body.get("amount_cents", 100)),
        )
        return response(201, transaction)
    if method == "GET" and path.startswith("/transactions/"):
        item = repo.get(path.rsplit("/", 1)[-1])
        return response(200, item) if item else response(404, {"error": "not found"})
    if method == "GET" and path == "/transactions":
        return response(200, {"items": repo.recent(25)})
    return response(404, {"error": "route not found"})
