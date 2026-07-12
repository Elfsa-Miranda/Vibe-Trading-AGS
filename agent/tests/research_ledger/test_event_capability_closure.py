from __future__ import annotations

from pathlib import Path

import pytest

from src.alpha_quality.flags import AGS_FLAG_DEFAULTS, ResolvedAGSFlags
from src.research_ledger.hash_utils import utc_now_iso
from src.research_ledger.events import (
    EventDraft,
    EventTransitionError,
    EventValidationError,
    ResearchEventStore,
)
from src.research_ledger.events.store import _EVENT_CAPABILITY_REQUIREMENTS


_SPECIALIZED_EVENT_TYPES = {
    "RegistryBootstrapRecordedV2",
    "ProcessActionFrozenV2",
    "ProcessOutcomeRecordedV2",
    "RetrieverActionTemplateFrozen",
    "TrainValidDataSnapshotFrozen",
    "EvaluationPolicyRegistered",
    "RetrieverFeatureSourceRecorded",
    "RetrieverDecisionV2Recorded",
    "RetrieverDecisionV3Recorded",
    "RetrieverDecisionV4Recorded",
    "RetrieverDecisionV5Recorded",
    "RetrieverDecisionV6Recorded",
    "RetrieverDecisionV7Recorded",
    "OfficialSearchControlRecorded",
    "PreArmFlatScheduleFrozen",
    "ActivationPairExecutionScheduled",
    "ActivationPairExecutionClaimed",
    "ActivationPlanRegistered",
    "ActivationRunRecorded",
    "ActivationRunSourceAudited",
    "ActivationResourceMeasured",
    "ActivationResourceMeasuredV2",
    "ActivationGenerationConsumptionRecorded",
    "ActivationGenerationConsumptionV2Recorded",
    "ActivationGenerationConsumptionV3Recorded",
    "ActivationGenerationConsumptionV4Recorded",
    "ActivationResultRecorded",
    "RetrieverActivationDecisionRecorded",
    "FalsificationContractRegistered",
    "SequentialProtocolRegistered",
    "SequentialLookRecorded",
    "OutcomeDataAccessed",
    "FalsificationResultRecorded",
    "MechanismEvidenceIndexRecorded",
    "ComplementEvidenceRecorded",
    "QualityDecisionRecorded",
    "DecisionEvidenceV3Recorded",
    "QualityDecisionV2Recorded",
    "QualityDecisionV3Recorded",
    "FinalCandidateFrozen",
    "FinalTestCapabilityIssued",
    "FinalTestAccessRecorded",
    "FinalTestArtifactRecorded",
    "ForwardPlanV2Recorded",
    "ForwardObservationV2Recorded",
    "ForwardPlanRecorded",
    "ForwardObservationRecorded",
}


def _store(tmp_path: Path, settings: dict[str, str] | None = None) -> ResearchEventStore:
    flags = ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
            **(settings or {}),
        }
    )
    return ResearchEventStore(
        tmp_path / "events.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=flags,
        code_version="capability-closure-test",
    )


def test_specialized_event_registry_has_an_explicit_child_capability() -> None:
    assert set(_EVENT_CAPABILITY_REQUIREMENTS) == _SPECIALIZED_EVENT_TYPES
    assert all(requirements for requirements in _EVENT_CAPABILITY_REQUIREMENTS.values())


@pytest.mark.parametrize("event_type", sorted(_SPECIALIZED_EVENT_TYPES))
def test_direct_append_cannot_bypass_disabled_child_capability(
    tmp_path: Path,
    event_type: str,
) -> None:
    store = _store(tmp_path)
    with pytest.raises(EventValidationError, match="capability is disabled"):
        store.append_event(
            EventDraft(
                event_type=event_type,
                entity_id="blocked",
                run_id="blocked-run",
                payload_schema_version="caller-invented.v1",
                payload={},
                idempotency_key=f"blocked:{event_type}",
            )
        )


def test_enabled_child_reaches_closed_payload_validation(tmp_path: Path) -> None:
    store = _store(tmp_path, {"VIBE_TRADING_COMPLEMENT_V2": "1"})
    with pytest.raises(EventValidationError, match="payload schema version"):
        store.append_event(
            EventDraft(
                event_type="ComplementEvidenceRecorded",
                entity_id="invalid",
                run_id="invalid-run",
                payload_schema_version="caller-invented.v1",
                payload={},
            )
        )


def test_existing_event_chain_replays_after_future_default_off_flag_is_added(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    store.append_event(
        EventDraft(
            event_type="TrialStarted",
            entity_id="historical-trial",
            run_id="historical-run",
            payload_schema_version="trial_started.v1",
            payload={
                "trial_id": "historical-trial",
                "candidate_id": "historical-candidate",
                "data_scope": "train_valid",
                "objective": "historical-replay",
                "started_at": utc_now_iso(),
            },
        )
    )
    monkeypatch.setitem(
        AGS_FLAG_DEFAULTS,
        "VIBE_TRADING_FUTURE_DEFAULT_OFF_TEST_CAPABILITY",
        False,
    )
    assert store.verify_chain()
    assert store.replay().event_count == 1


def test_orphan_or_post_terminal_generation_failure_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    failure = EventDraft(
        event_type="GenerationFailureRecorded",
        entity_id="trial-failure",
        run_id="failure-run",
        payload_schema_version="generation_failure_recorded.v1",
        payload={
            "trial_id": "trial-failure",
            "failure_code": "EVALUATION_ERROR",
            "failure_kind": "error",
            "message": "bounded failure",
            "occurred_at": utc_now_iso(),
        },
    )
    with pytest.raises(EventTransitionError, match="prior active trial"):
        store.append_event(failure)
    store.append_event(
        EventDraft(
            event_type="TrialStarted",
            entity_id="trial-failure",
            run_id="failure-run",
            payload_schema_version="trial_started.v1",
            payload={
                "trial_id": "trial-failure",
                "candidate_id": "candidate-failure",
                "data_scope": "train_valid",
                "objective": "failure-ordering",
                "started_at": utc_now_iso(),
            },
        )
    )
    store.append_event(failure)
    store.append_event(
        EventDraft(
            event_type="TrialTerminated",
            entity_id="trial-failure",
            run_id="failure-run",
            payload_schema_version="trial_terminated.v1",
            payload={
                "trial_id": "trial-failure",
                "status": "error",
                "reason_codes": ["EVALUATION_ERROR"],
                "decision": "research_only",
                "evaluation_event_hash": None,
                "terminated_at": utc_now_iso(),
            },
        )
    )
    with pytest.raises(EventTransitionError, match="prior active trial"):
        store.append_event(
            EventDraft(
                **{
                    **failure.__dict__,
                    "idempotency_key": "post-terminal-failure",
                }
            )
        )
