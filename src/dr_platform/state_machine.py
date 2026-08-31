"""Policy-enforced recovery state machine."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from dr_platform.errors import ApprovalRequired, InvalidTransition, ValidationFailed
from dr_platform.integrity import GENESIS_HASH, chained_event
from dr_platform.types import Incident, RecoveryState, normalize_utc, utc_now

Clock = Callable[[], datetime]

ALLOWED_TRANSITIONS: dict[RecoveryState, frozenset[RecoveryState]] = {
    RecoveryState.HEALTHY: frozenset({RecoveryState.INCIDENT_DECLARED}),
    RecoveryState.INCIDENT_DECLARED: frozenset({RecoveryState.RECOVERY_IN_PROGRESS}),
    RecoveryState.RECOVERY_IN_PROGRESS: frozenset({RecoveryState.VALIDATING}),
    RecoveryState.VALIDATING: frozenset(
        {RecoveryState.RECOVERY_IN_PROGRESS, RecoveryState.AWAITING_APPROVAL}
    ),
    RecoveryState.AWAITING_APPROVAL: frozenset({RecoveryState.RECOVERY_ACTIVE}),
    RecoveryState.RECOVERY_ACTIVE: frozenset({RecoveryState.FAILBACK_IN_PROGRESS}),
    RecoveryState.FAILBACK_IN_PROGRESS: frozenset(
        {RecoveryState.RECOVERY_ACTIVE, RecoveryState.HEALTHY}
    ),
}

APPROVAL_TRANSITIONS = {
    (RecoveryState.AWAITING_APPROVAL, RecoveryState.RECOVERY_ACTIVE),
    (RecoveryState.RECOVERY_ACTIVE, RecoveryState.FAILBACK_IN_PROGRESS),
    (RecoveryState.FAILBACK_IN_PROGRESS, RecoveryState.HEALTHY),
}


class RecoveryStateMachine:
    def __init__(self, clock: Clock = utc_now) -> None:
        self.clock = clock

    def transition(
        self,
        incident: Incident,
        target: RecoveryState,
        *,
        approved: bool = False,
        actor: str = "drctl",
        reason: str = "",
    ) -> Incident:
        source = incident.state
        if target not in ALLOWED_TRANSITIONS[source]:
            raise InvalidTransition(f"Transition {source} -> {target} is not allowed")
        if target is RecoveryState.AWAITING_APPROVAL and (
            incident.validation is None or not incident.validation.passed
        ):
            raise ValidationFailed("Every recovery validation gate must pass before approval")
        if (source, target) in APPROVAL_TRANSITIONS and not approved:
            raise ApprovalRequired(f"Transition {source} -> {target} requires explicit approval")

        changed_at = normalize_utc(self.clock())
        incident.state = target
        previous_hash = (
            str(incident.event_log[-1]["event_hash"]) if incident.event_log else GENESIS_HASH
        )
        incident.event_log.append(
            chained_event(
                {
                    "from": source.value,
                    "to": target.value,
                    "at": changed_at.isoformat(),
                    "actor": actor,
                    "reason": reason,
                    "approved": approved,
                },
                previous_hash,
                len(incident.event_log) + 1,
            )
        )
        if target is RecoveryState.RECOVERY_IN_PROGRESS and incident.recovery_started_at is None:
            incident.recovery_started_at = changed_at
        elif target is RecoveryState.VALIDATING:
            incident.validation_started_at = changed_at
        elif target is RecoveryState.AWAITING_APPROVAL:
            incident.validation_completed_at = changed_at
            incident.approval_status = "PENDING"
            if incident.scenario.value == "regional-outage":
                incident.rto_end_at = changed_at
        elif target is RecoveryState.RECOVERY_ACTIVE:
            incident.promotion_at = changed_at
            incident.rto_end_at = changed_at
            incident.approval_status = "APPROVED"
        elif target is RecoveryState.HEALTHY:
            incident.failback_completed_at = changed_at
        return incident
