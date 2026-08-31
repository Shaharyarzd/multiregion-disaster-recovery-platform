"""Conservative offline checks for the authored IAM action-path contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_contract(path: Path) -> dict[str, Any]:
    return dict(json.loads(path.read_text()))


def validate_contract(contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    roles = contract.get("roles", {})
    if not isinstance(roles, dict) or set(roles) != {"deploy", "recovery", "evidence"}:
        errors.append("exact deploy/recovery/evidence roles are required")
        return errors
    for role, body in roles.items():
        actions = body.get("actions", [])
        if actions != sorted(set(actions)):
            errors.append(f"{role} actions must be sorted and unique")
        for action in actions:
            if action == "*" or action.endswith(":*") or "administrator" in action.casefold():
                errors.append(f"{role} has wildcard/admin-style action {action}")
        denied = set(body.get("denied_actions", []))
        if denied & set(actions):
            errors.append(f"{role} allows an explicitly denied action")
    if "dynamodb:RestoreTableToPointInTime" in roles["deploy"]["actions"]:
        errors.append("deploy role may not perform PITR")
    if "kms:Sign" in roles["recovery"]["actions"]:
        errors.append("recovery role may not sign evidence")
    if "s3:BypassGovernanceRetention" not in roles["evidence"].get("denied_actions", []):
        errors.append("evidence role must explicitly deny governance bypass")
    return errors


def permits(contract: dict[str, Any], role: str, action: str, resource_name: str) -> bool:
    """Minimal negative simulator; not a substitute for AWS policy evaluation."""
    body = contract["roles"][role]
    if action in body.get("denied_actions", []):
        return False
    return action in body["actions"] and resource_name.startswith(body["resource_prefix"])
