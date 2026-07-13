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
        "QualityDecisionV3Recorded",
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
        },
    )
    events = (retrieval, start, evaluation, terminal, decision, dossier)
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
    )

    assert "factor_spec_id" not in terminal.payload
    assert result.derived_effective_candidate_ids == (factor_spec_id,)
    assert result.source_failure_codes == ()
    assert result.source_complete is True
