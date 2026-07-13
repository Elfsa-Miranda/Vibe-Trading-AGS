from __future__ import annotations

from typing import Any

from src.alpha_foundry.activation.run_source_v3 import (
    FormalActivationRunSourceAuditorV3,
)
from src.alpha_foundry.activation.runner import activation_arm_execution_run_id
from src.research_ledger.events import ResearchEventEnvelope
from src.research_ledger.hash_utils import canonical_json_hash


def _hash(name: str) -> str:
    return canonical_json_hash({"activation-run-source-v3-fixture": name})


def _event(
    event_type: str,
    name: str,
    *,
    run_id: str,
    payload: dict[str, Any],
) -> ResearchEventEnvelope:
    return ResearchEventEnvelope(
        schema_version="research_event.v1",
        event_id=f"event-{name}",
        event_type=event_type,
        entity_id=f"entity-{name}",
        run_id=run_id,
        payload_schema_version=f"{event_type}.test.v1",
        payload=payload,
        payload_hash=_hash(f"payload-{name}"),
        idempotency_key=None,
        previous_event_hash=None,
        event_hash=_hash(f"event-{name}"),
        created_at="2026-07-13T00:00:00Z",
        code_version="test",
        feature_flags={},
        warnings=(),
        hard_failures=(),
    )


class _VerifiedStore:
    def __init__(self, events: tuple[ResearchEventEnvelope, ...]) -> None:
        self._events = events

    def query_events(self) -> list[ResearchEventEnvelope]:
        return list(self._events)

    def verify_chain(self) -> bool:
        return True


def _resource(
    *,
    plan_hash: str,
    pair_id: str,
    run_group_id: str,
    arm: str = "control",
    source_complete: bool = True,
) -> ResearchEventEnvelope:
    return _event(
        "ActivationResourceMeasuredV2",
        f"resource-{pair_id}-{arm}",
        run_id=run_group_id,
        payload={
            "plan_hash": plan_hash,
            "pair_id": pair_id,
            "run_group_id": run_group_id,
            "arm": arm,
            "source_complete": source_complete,
        },
    )


def test_run_source_binds_decision_to_evaluation_factor_when_terminal_omits_it() -> None:
    plan_hash = _hash("plan")
    pair_id = "pair-1"
    run_group_id = "group-1"
    execution_run_id = activation_arm_execution_run_id(
        plan_hash=plan_hash,
        run_group_id=run_group_id,
        arm="control",
    )
    trial_id = "trial-1"
    factor_spec_id = _hash("factor")
    retrieval = _event(
        "PreArmFlatScheduleFrozen",
        "retrieval",
        run_id=execution_run_id,
        payload={"plan_hash": plan_hash, "pair_id": pair_id},
    )
    start = _event(
        "TrialStarted",
        "start",
        run_id=execution_run_id,
        payload={"trial_id": trial_id, "candidate_id": "candidate-1"},
    )
    evaluation = _event(
        "EvaluationRecorded",
        "evaluation",
        run_id=execution_run_id,
        payload={"trial_id": trial_id, "factor_spec_id": factor_spec_id},
    )
    terminal = _event(
        "TrialTerminated",
        "terminal",
        run_id=execution_run_id,
        payload={
            "trial_id": trial_id,
            "status": "success",
            "evaluation_event_hash": evaluation.event_hash,
        },
    )
    decision = _event(
        "QualityDecisionV4Recorded",
        "decision",
        run_id=execution_run_id,
        payload={
            "factor_spec_id": factor_spec_id,
            "decision": "candidate_zoo",
            "tier": 2,
        },
    )
    dossier = _event(
        "TrialTerminalDossierRecorded",
        "dossier",
        run_id=execution_run_id,
        payload={
            "trial_id": trial_id,
            "factor_spec_id": factor_spec_id,
            "terminal_event_hash": terminal.event_hash,
            "evaluation_event_hash": evaluation.event_hash,
            "quality_decision_event_hash": decision.event_hash,
        },
    )
    resource = _resource(
        plan_hash=plan_hash,
        pair_id=pair_id,
        run_group_id=run_group_id,
    )
    events = (retrieval, start, evaluation, terminal, decision, dossier, resource)
    auditor = FormalActivationRunSourceAuditorV3(_VerifiedStore(events))  # type: ignore[arg-type]

    result = auditor.audit(
        plan_hash=plan_hash,
        pair_id=pair_id,
        run_group_id=run_group_id,
        arm="flat",
        candidate_budget=1,
        retrieval_authority_event_hashes=(retrieval.event_hash,),
        terminal_event_hashes=(terminal.event_hash,),
        evaluation_event_hashes=(evaluation.event_hash,),
        quality_decision_event_hashes=(decision.event_hash,),
        terminal_dossier_event_hashes=(dossier.event_hash,),
        resource_event_hashes=(resource.event_hash,),
    )

    assert "factor_spec_id" not in terminal.payload
    assert result.derived_effective_candidate_ids == (factor_spec_id,)
    assert result.source_failure_codes == ()
    assert result.source_complete is True

    missing_dossier = auditor.audit(
        plan_hash=plan_hash,
        pair_id=pair_id,
        run_group_id=run_group_id,
        arm="flat",
        candidate_budget=1,
        retrieval_authority_event_hashes=(retrieval.event_hash,),
        terminal_event_hashes=(terminal.event_hash,),
        evaluation_event_hashes=(evaluation.event_hash,),
        quality_decision_event_hashes=(decision.event_hash,),
        terminal_dossier_event_hashes=(),
        resource_event_hashes=(resource.event_hash,),
    )
    assert "EVERY_EVALUATED_TERMINAL_REQUIRES_ONE_DOSSIER" in (
        missing_dossier.source_failure_codes
    )
    assert "QUALITY_DECISION_DOSSIER_BINDING_MISMATCH" in (
        missing_dossier.source_failure_codes
    )
    assert missing_dossier.source_complete is False


def test_run_source_rejects_legacy_v3_as_effective_yield_authority() -> None:
    plan_hash = _hash("legacy-plan")
    pair_id = "pair-legacy"
    run_group_id = "group-legacy"
    execution_run_id = activation_arm_execution_run_id(
        plan_hash=plan_hash,
        run_group_id=run_group_id,
        arm="control",
    )
    trial_id = "trial-legacy"
    factor_spec_id = _hash("legacy-factor")
    retrieval = _event(
        "PreArmFlatScheduleFrozen",
        "legacy-retrieval",
        run_id=execution_run_id,
        payload={"plan_hash": plan_hash, "pair_id": pair_id},
    )
    start = _event(
        "TrialStarted",
        "legacy-start",
        run_id=execution_run_id,
        payload={"trial_id": trial_id, "candidate_id": "candidate-legacy"},
    )
    evaluation = _event(
        "EvaluationRecorded",
        "legacy-evaluation",
        run_id=execution_run_id,
        payload={"trial_id": trial_id, "factor_spec_id": factor_spec_id},
    )
    terminal = _event(
        "TrialTerminated",
        "legacy-terminal",
        run_id=execution_run_id,
        payload={
            "trial_id": trial_id,
            "status": "success",
            "evaluation_event_hash": evaluation.event_hash,
        },
    )
    legacy_decision = _event(
        "QualityDecisionV3Recorded",
        "legacy-decision",
        run_id=execution_run_id,
        payload={
            "factor_spec_id": factor_spec_id,
            "decision": "candidate_zoo",
            "tier": 2,
        },
    )
    dossier = _event(
        "TrialTerminalDossierRecorded",
        "legacy-dossier",
        run_id=execution_run_id,
        payload={
            "trial_id": trial_id,
            "factor_spec_id": factor_spec_id,
            "terminal_event_hash": terminal.event_hash,
            "evaluation_event_hash": evaluation.event_hash,
            "quality_decision_event_hash": legacy_decision.event_hash,
        },
    )
    resource = _resource(
        plan_hash=plan_hash,
        pair_id=pair_id,
        run_group_id=run_group_id,
    )
    auditor = FormalActivationRunSourceAuditorV3(
        _VerifiedStore(
            (
                retrieval,
                start,
                evaluation,
                terminal,
                legacy_decision,
                dossier,
                resource,
            )
        )  # type: ignore[arg-type]
    )

    result = auditor.audit(
        plan_hash=plan_hash,
        pair_id=pair_id,
        run_group_id=run_group_id,
        arm="flat",
        candidate_budget=1,
        retrieval_authority_event_hashes=(retrieval.event_hash,),
        terminal_event_hashes=(terminal.event_hash,),
        evaluation_event_hashes=(evaluation.event_hash,),
        quality_decision_event_hashes=(legacy_decision.event_hash,),
        terminal_dossier_event_hashes=(dossier.event_hash,),
        resource_event_hashes=(resource.event_hash,),
    )

    assert result.derived_effective_candidate_ids == ()
    assert "PRODUCTION_QUALITY_DECISION_SOURCE_MISSING" in result.source_failure_codes
    assert "QUALITY_DECISION_DOSSIER_BINDING_MISMATCH" in result.source_failure_codes
    assert result.source_complete is False


def test_run_source_accepts_identity_invalid_terminal_without_fabricated_dossier() -> None:
    plan_hash = _hash("invalid-plan")
    pair_id = "pair-invalid"
    run_group_id = "group-invalid"
    execution_run_id = activation_arm_execution_run_id(
        plan_hash=plan_hash,
        run_group_id=run_group_id,
        arm="control",
    )
    trial_id = "trial-invalid"
    retrieval = _event(
        "PreArmFlatScheduleFrozen",
        "invalid-retrieval",
        run_id=execution_run_id,
        payload={"plan_hash": plan_hash, "pair_id": pair_id},
    )
    start = _event(
        "TrialStarted",
        "invalid-start",
        run_id=execution_run_id,
        payload={"trial_id": trial_id, "candidate_id": "candidate-invalid"},
    )
    failure = _event(
        "GenerationFailureRecorded",
        "invalid-generation",
        run_id=execution_run_id,
        payload={"trial_id": trial_id, "failure_code": "INVALID_FORMULA"},
    )
    terminal = _event(
        "TrialTerminated",
        "invalid-terminal",
        run_id=execution_run_id,
        payload={
            "trial_id": trial_id,
            "status": "invalid",
            "evaluation_event_hash": None,
        },
    )
    resource = _resource(
        plan_hash=plan_hash,
        pair_id=pair_id,
        run_group_id=run_group_id,
    )
    auditor = FormalActivationRunSourceAuditorV3(
        _VerifiedStore((retrieval, start, failure, terminal, resource))  # type: ignore[arg-type]
    )

    result = auditor.audit(
        plan_hash=plan_hash,
        pair_id=pair_id,
        run_group_id=run_group_id,
        arm="flat",
        candidate_budget=1,
        retrieval_authority_event_hashes=(retrieval.event_hash,),
        terminal_event_hashes=(terminal.event_hash,),
        evaluation_event_hashes=(),
        quality_decision_event_hashes=(),
        terminal_dossier_event_hashes=(),
        resource_event_hashes=(resource.event_hash,),
    )

    assert result.derived_terminal_status_counts[3] == ("invalid", 1)
    assert result.derived_effective_candidate_ids == ()
    assert result.source_failure_codes == ()
    assert result.source_complete is True


def test_run_source_rejects_missing_or_incomplete_resource_authority() -> None:
    plan_hash = _hash("resource-plan")
    pair_id = "pair-resource"
    run_group_id = "group-resource"
    execution_run_id = activation_arm_execution_run_id(
        plan_hash=plan_hash,
        run_group_id=run_group_id,
        arm="control",
    )
    retrieval = _event(
        "PreArmFlatScheduleFrozen",
        "resource-retrieval",
        run_id=execution_run_id,
        payload={"plan_hash": plan_hash, "pair_id": pair_id},
    )
    start = _event(
        "TrialStarted",
        "resource-start",
        run_id=execution_run_id,
        payload={"trial_id": "trial-resource", "candidate_id": "candidate-resource"},
    )
    terminal = _event(
        "TrialTerminated",
        "resource-terminal",
        run_id=execution_run_id,
        payload={
            "trial_id": "trial-resource",
            "status": "invalid",
            "evaluation_event_hash": None,
        },
    )
    failure = _event(
        "GenerationFailureRecorded",
        "resource-generation-failure",
        run_id=execution_run_id,
        payload={"trial_id": "trial-resource", "failure_code": "INVALID_FORMULA"},
    )
    incomplete = _resource(
        plan_hash=plan_hash,
        pair_id=pair_id,
        run_group_id=run_group_id,
        source_complete=False,
    )
    auditor = FormalActivationRunSourceAuditorV3(
        _VerifiedStore((retrieval, start, terminal, failure, incomplete))  # type: ignore[arg-type]
    )
    common = {
        "plan_hash": plan_hash,
        "pair_id": pair_id,
        "run_group_id": run_group_id,
        "arm": "flat",
        "candidate_budget": 1,
        "retrieval_authority_event_hashes": (retrieval.event_hash,),
        "terminal_event_hashes": (terminal.event_hash,),
        "evaluation_event_hashes": (),
        "quality_decision_event_hashes": (),
        "terminal_dossier_event_hashes": (),
    }

    missing = auditor.audit(**common, resource_event_hashes=())  # type: ignore[arg-type]
    unresolved = auditor.audit(
        **common, resource_event_hashes=(incomplete.event_hash,)  # type: ignore[arg-type]
    )

    assert "PRODUCTION_ARM_RESOURCE_SOURCE_MISSING" in missing.source_failure_codes
    assert "EVERY_ARM_REQUIRES_ONE_RESOURCE_EVENT" in missing.source_failure_codes
    assert (
        "PRODUCTION_ARM_RESOURCE_SOURCE_INCOMPLETE"
        in unresolved.source_failure_codes
    )
    assert missing.source_complete is unresolved.source_complete is False


def test_run_source_rejects_resource_recorded_before_arm_outcome() -> None:
    plan_hash = _hash("resource-order-plan")
    pair_id = "pair-resource-order"
    run_group_id = "group-resource-order"
    execution_run_id = activation_arm_execution_run_id(
        plan_hash=plan_hash,
        run_group_id=run_group_id,
        arm="control",
    )
    retrieval = _event(
        "PreArmFlatScheduleFrozen",
        "resource-order-retrieval",
        run_id=execution_run_id,
        payload={"plan_hash": plan_hash, "pair_id": pair_id},
    )
    resource = _resource(
        plan_hash=plan_hash,
        pair_id=pair_id,
        run_group_id=run_group_id,
    )
    start = _event(
        "TrialStarted",
        "resource-order-start",
        run_id=execution_run_id,
        payload={"trial_id": "trial-order", "candidate_id": "candidate-order"},
    )
    terminal = _event(
        "TrialTerminated",
        "resource-order-terminal",
        run_id=execution_run_id,
        payload={
            "trial_id": "trial-order",
            "status": "invalid",
            "evaluation_event_hash": None,
        },
    )
    failure = _event(
        "GenerationFailureRecorded",
        "resource-order-failure",
        run_id=execution_run_id,
        payload={"trial_id": "trial-order", "failure_code": "INVALID_FORMULA"},
    )
    auditor = FormalActivationRunSourceAuditorV3(
        _VerifiedStore((retrieval, resource, start, terminal, failure))  # type: ignore[arg-type]
    )

    result = auditor.audit(
        plan_hash=plan_hash,
        pair_id=pair_id,
        run_group_id=run_group_id,
        arm="flat",
        candidate_budget=1,
        retrieval_authority_event_hashes=(retrieval.event_hash,),
        terminal_event_hashes=(terminal.event_hash,),
        evaluation_event_hashes=(),
        quality_decision_event_hashes=(),
        terminal_dossier_event_hashes=(),
        resource_event_hashes=(resource.event_hash,),
    )

    assert "PRODUCTION_ARM_RESOURCE_PRECEDES_ARM_OUTCOME" in (
        result.source_failure_codes
    )
    assert result.source_complete is False
