"""Local persistence for reproducible drills; AWS adapters plug into the same boundary."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from dr_platform.types import Incident, RecoveryState, Scenario, ValidationResult


class LocalIncidentStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "incident.json"

    def save(self, incident: Incident) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        raw = asdict(incident)
        raw["scenario"] = incident.scenario.value
        raw["state"] = incident.state.value
        for key, value in list(raw.items()):
            if isinstance(value, datetime):
                raw[key] = value.isoformat()
        self.path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def load(self) -> Incident:
        raw: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        datetime_fields = {
            "declared_at",
            "failure_at",
            "recovery_started_at",
            "infrastructure_ready_at",
            "recovery_point",
            "validation_completed_at",
            "promotion_at",
            "failback_completed_at",
            "restore_completed_at",
            "validation_started_at",
            "rto_end_at",
        }
        for key in datetime_fields:
            if raw.get(key):
                raw[key] = datetime.fromisoformat(raw[key])
        if raw.get("validation"):
            raw["validation"] = ValidationResult(**raw["validation"])
        raw["scenario"] = Scenario(raw["scenario"])
        raw["state"] = RecoveryState(raw["state"])
        return Incident(**raw)
