"""Recovery orchestration with automation and explicit approval boundaries."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from dr_platform.data_validation import Comparison
from dr_platform.errors import ApprovalRequired, ValidationFailed
from dr_platform.health import failback_safe
from dr_platform.reconciliation import (
    ConsistencyProof,
    ReconciliationPlan,
    ReplayTarget,
    execute_replay,
)
from dr_platform.state_machine import RecoveryStateMachine
from dr_platform.types import (
    Incident,
    RecoveryState,
    RegionHealth,
    Scenario,
    Transaction,
    ValidationResult,
    iso,
    normalize_utc,
    utc_now,
)


class RecoveryOrchestrator:
    def __init__(self, clock: Callable[[], datetime] = utc_now) -> None:
        self.clock = clock
        self.machine = RecoveryStateMachine(clock)

    def declare(
        self,
        scenario: Scenario,
        failure_at: datetime,
        affected_region: str | None = None,
    ) -> Incident:
        now = normalize_utc(self.clock())
        failure_at = normalize_utc(failure_at)
        run_id = str(uuid4())
        scenario_material = (
            f"{scenario.value}|{affected_region or 'global'}|{failure_at.isoformat()}"
        )
        incident = Incident(
            incident_id=f"dr-{uuid4().hex[:12]}",
            scenario=scenario,
            state=RecoveryState.HEALTHY,
            declared_at=now,
            failure_at=failure_at,
            affected_region=affected_region,
            run_id=run_id,
            scenario_id="scenario-" + hashlib.sha256(scenario_material.encode()).hexdigest()[:16],
            timestamp_sources={
                "incident_declaration": "CONTROLLER_UTC_WALL_CLOCK",
                "failure_or_corruption": "OPERATOR_OR_FAULT_INJECTOR",
                "validation": "CONTROLLER_UTC_WALL_CLOCK",
                "recovered_transaction": "SYNTHETIC_TRANSACTION_FIELD",
            },
        )
        return self.machine.transition(
            incident, RecoveryState.INCIDENT_DECLARED, actor="operator", reason="declared"
        )

    def start_recovery(
        self, incident: Incident, recovery_point: datetime | None = None
    ) -> Incident:
        if incident.scenario is Scenario.LOGICAL_CORRUPTION and (
            recovery_point is None
            or normalize_utc(recovery_point) >= normalize_utc(incident.failure_at)
        ):
            raise ValidationFailed(
                "Logical recovery point must be explicitly selected before corruption"
            )
        incident.recovery_point = recovery_point
        return self.machine.transition(
            incident, RecoveryState.RECOVERY_IN_PROGRESS, reason="isolated restore started"
        )

    def begin_validation(
        self,
        incident: Incident,
        infrastructure_ready_at: datetime,
        *,
        restore_configuration: dict[str, object] | None = None,
    ) -> Incident:
        if incident.scenario is Scenario.LOGICAL_CORRUPTION:
            required = {
                "table_active",
                "encryption_verified",
                "pitr_enabled",
                "tags_verified",
                "ttl_verified",
                "stream_verified",
                "no_replicas",
                "deletion_protection",
                "ready_for_validation",
            }
            if not restore_configuration or not all(
                restore_configuration.get(key) is True for key in required
            ):
                raise ValidationFailed(
                    "PITR completion is insufficient: restored-table configuration is unproven"
                )
            incident.restore_configuration = dict(restore_configuration)
        incident.infrastructure_ready_at = infrastructure_ready_at
        incident.restore_completed_at = infrastructure_ready_at
        return self.machine.transition(incident, RecoveryState.VALIDATING)

    def record_validation(
        self,
        incident: Incident,
        comparison: Comparison,
        *,
        api_health: bool,
        read_write: bool,
        freshness: bool,
        s3_versions: bool,
        cross_region_consistency: bool,
        synthetic_transaction: bool,
        reconciliation: ReconciliationPlan | None = None,
    ) -> Incident:
        incident.record_counts = {
            "expected": comparison.source_count,
            "recovered": comparison.recovered_count,
            "lost": len(comparison.missing_keys),
            "unexpected": len(comparison.unexpected_keys),
        }
        incident.validation = ValidationResult(
            api_health=api_health,
            read_write=read_write,
            record_count=comparison.source_count == comparison.recovered_count,
            expected_keys=not comparison.missing_keys and not comparison.unexpected_keys,
            checksum=comparison.source_checksum == comparison.recovered_checksum,
            freshness=freshness,
            s3_versions=s3_versions,
            cross_region_consistency=cross_region_consistency,
            synthetic_transaction=synthetic_transaction,
            details={
                "source_checksum": comparison.source_checksum,
                "recovered_checksum": comparison.recovered_checksum,
                "missing_keys": list(comparison.missing_keys),
                "unexpected_keys": list(comparison.unexpected_keys),
                "newest_recovered_transaction": iso(
                    reconciliation.newest_authoritative_transaction
                    if reconciliation
                    else comparison.newest_recovered_transaction
                ),
                "evidence_scope": incident.evidence_scope,
            },
        )
        if incident.scenario is Scenario.LOGICAL_CORRUPTION:
            if reconciliation is None:
                raise ValidationFailed("Logical recovery requires a reconciliation plan")
            incident.reconciliation = reconciliation.as_dict()
            incident.reconciliation["replay_result"] = {
                "complete": not bool(reconciliation.replay_transaction_ids),
                "status": (
                    "NOT_REQUIRED"
                    if not reconciliation.replay_transaction_ids
                    else "AWAITING_APPROVAL"
                ),
            }
            if not reconciliation.safe_to_promote:
                raise ValidationFailed("Recovery remains isolated: reconciliation conflicts exist")
        if not incident.validation.passed:
            raise ValidationFailed("Recovery remains isolated because validation failed")
        validated = self.machine.transition(incident, RecoveryState.AWAITING_APPROVAL)
        if validated.reconciliation is not None:
            validated.reconciliation["validation_timestamp"] = iso(
                validated.validation_completed_at
            )
        return validated

    def reconcile(
        self,
        incident: Incident,
        plan: ReconciliationPlan,
        candidates: list[Transaction],
        target: ReplayTarget,
        *,
        approved: bool,
        dry_run: bool,
        approver: str | None = None,
        reference: str | None = None,
    ) -> Incident:
        if incident.state is not RecoveryState.AWAITING_APPROVAL or not incident.reconciliation:
            raise ValidationFailed("Replay requires a validated recovery awaiting approval")
        validation_time = incident.validation_completed_at
        recorded_time = incident.reconciliation.get("validation_timestamp")
        if validation_time is None or recorded_time != iso(validation_time):
            raise ValidationFailed("Replay blocked because validation evidence is stale")
        if not dry_run and not approved:
            raise ApprovalRequired("Material reconciliation requires explicit approval")
        result = execute_replay(
            plan,
            candidates,
            target,
            validation_timestamp=validation_time,
            executed_at=self.clock(),
            approved=approved,
            approver=approver,
            approval_reference=reference,
            dry_run=dry_run,
        )
        incident.reconciliation["replay_result"] = result.as_dict()
        incident.reconciliation["status"] = (
            "REPLAY_COMPLETE" if result.complete else "DRY_RUN" if dry_run else "BLOCKED"
        )
        return incident

    def promote(
        self, incident: Incident, *, approved: bool, approver: str, reference: str
    ) -> Incident:
        if not approved or not approver or not reference:
            raise ApprovalRequired("Promotion requires approver identity and approval reference")
        if incident.scenario is Scenario.LOGICAL_CORRUPTION:
            if not incident.reconciliation or not incident.reconciliation.get("safe_to_promote"):
                raise ValidationFailed(
                    "Promotion blocked without a conflict-free reconciliation plan"
                )
            replay_result = incident.reconciliation.get("replay_result", {})
            if not isinstance(replay_result, dict) or not replay_result.get("complete"):
                raise ValidationFailed("Promotion blocked until bounded replay completes")
            validation_time = incident.reconciliation.get("validation_timestamp")
            if validation_time != iso(incident.validation_completed_at):
                raise ValidationFailed("Promotion blocked because validation evidence is stale")
            incident.reconciliation["status"] = "PROMOTION_APPROVED"
        incident.approver = approver
        incident.approval_reference = reference
        return self.machine.transition(
            incident,
            RecoveryState.RECOVERY_ACTIVE,
            approved=True,
            actor=approver,
            reason=reference,
        )

    def start_failback(
        self,
        incident: Incident,
        *,
        approved: bool,
        approver: str,
        reference: str,
        original: RegionHealth,
        survivor: RegionHealth,
        consistency_proof: ConsistencyProof,
    ) -> Incident:
        proof_is_fresh = bool(
            incident.promotion_at and consistency_proof.checked_at >= incident.promotion_at
        )
        if not failback_safe(original, survivor, consistency_proof.passed and proof_is_fresh):
            raise ValidationFailed("Failback blocked: regional health or data consistency failed")
        if not approved or not reference:
            raise ApprovalRequired("Failback requires explicit approval")
        return self.machine.transition(
            incident,
            RecoveryState.FAILBACK_IN_PROGRESS,
            approved=True,
            actor=approver,
            reason=reference,
        )

    def complete_failback(
        self,
        incident: Incident,
        *,
        approved: bool,
        approver: str,
        reference: str,
        both_regions_validated: bool,
    ) -> Incident:
        if not both_regions_validated:
            raise ValidationFailed(
                "Both regions must be validated before active-active restoration"
            )
        return self.machine.transition(
            incident,
            RecoveryState.HEALTHY,
            approved=approved,
            actor=approver,
            reason=reference,
        )
