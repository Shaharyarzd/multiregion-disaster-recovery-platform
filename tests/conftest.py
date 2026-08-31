from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dr_platform.data_validation import Comparison
from dr_platform.orchestrator import RecoveryOrchestrator
from dr_platform.types import Incident, Scenario


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(seconds=10)
        return current


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def comparison() -> Comparison:
    return Comparison(3, 3, (), (), "abc", "abc", datetime(2025, 12, 31, 23, 59, 58, tzinfo=UTC))


@pytest.fixture
def declared(clock: Clock) -> tuple[RecoveryOrchestrator, Incident]:
    orchestrator = RecoveryOrchestrator(clock)
    incident = orchestrator.declare(
        Scenario.LOGICAL_CORRUPTION,
        datetime(2026, 1, 1, tzinfo=UTC),
        "us-east-1",
    )
    return orchestrator, incident
