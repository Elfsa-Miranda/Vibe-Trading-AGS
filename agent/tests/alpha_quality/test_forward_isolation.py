from __future__ import annotations

from pathlib import Path

import pytest

from src.alpha_foundry.dag import FactorDAGError, FactorDAGProjector, FactorDAGQuery
from src.alpha_foundry.dsl.identity import FactorIdentityService, FactorSpecSemantics
from src.alpha_foundry.retrieval import ShadowRetriever
from src.alpha_foundry.retrieval.model import DiscoveryEvidenceView
from src.alpha_quality.decision_v2 import (
    DecisionEvidenceRecord,
    DecisionEvidenceRefs,
    DecisionEvidenceRepository,
    DecisionV2Policy,
    QualityDecisionV2Runner,
)
from src.alpha_quality.final_test.model import (
    FinalDecisionEvidenceView,
    FinalTestDataRequest,
    FinalTestDataset,
    FinalTestPolicy,
    FrozenFinalCandidate,
)
from src.alpha_quality.final_test.runner import FinalTestRunner
from src.alpha_quality.flags import ResolvedAGSFlags
from src.alpha_quality.forward import (
    ForwardMonitoringService,
    ForwardProjector,
    FrozenForwardPlan,
)
from src.alpha_quality.scope import DiscoveryEvidenceProjector, TestScopeAuthority
from src.research_ledger.events import EventDraft, ResearchEventStore
from src.research_ledger.hash_utils import canonical_json_hash, utc_now_iso


def _flags(*, forward: bool = True) -> ResolvedAGSFlags:
    return ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
            "VIBE_TRADING_ALPHA_FOUNDRY": "1",
            "VIBE_TRADING_DECISION_V2": "1",
            "VIBE_TRADING_FORWARD_TRACKING": "1" if forward else "0",
            "VIBE_TRADING_FACTOR_DAG": "1",
            "VIBE_TRADING_PROCESS_MEMORY": "1",
            "VIBE_TRADING_TOPOLOGY_RETRIEVER": "1",
        }
    )


class _Provider:
    def load_final(self, request: FinalTestDataRequest) -> FinalTestDataset:
        return FinalTestDataset(
            data_snapshot_hash=request.data_snapshot_hash,
            period_start=request.period_start,
            period_end=request.period_end,
            rank_ic_series=(0.03, 0.04, 0.02, 0.05),
            net_returns=(0.001, 0.002, 0.001, 0.003),
        )


def _final_context(tmp_path: Path):
    flags = _flags()
    store = ResearchEventStore(
        tmp_path / "research.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=flags,
        code_version="pr11-forward",
    )
    digest = canonical_json_hash({"fixture": "forward"})
    attempt = FactorIdentityService(store=store, flags=flags).record_attempt(
        trial_id="trial-forward-discovery",
        run_id="run-forward",
        candidate_id="candidate-forward",
        formula="rank(close)",
        semantics=FactorSpecSemantics(
            transform_pipeline_hash=digest,
            field_semantics={"close": "pit_eod"},
            signal_time="close",
            order_time="next_open",
            entry_price_time="next_open",
            execution_lag=1,
            return_horizon=5,
            universe_mask_hash=digest,
            tradability_mask_hash=digest,
        ),
    )
    assert attempt.factor_spec_id is not None
    definition_event = store.query_events(
        event_type="FactorDefinitionRecorded", entity_id=attempt.factor_spec_id
    )[-1]
    evaluation = store.append_event(
        EventDraft(
            event_type="EvaluationRecorded",
            entity_id="evaluation-forward-discovery",
            run_id="run-forward",
            payload_schema_version="evaluation_recorded.v1",
            payload={
                "evaluation_id": "evaluation-forward-discovery",
                "trial_id": "trial-forward-discovery",
                "factor_spec_id": attempt.factor_spec_id,
                "data_scope": "valid",
                "scorecard_hash": canonical_json_hash({"scorecard": "forward"}),
                "artifact_refs": [],
                "metadata": {},
            },
        )
    )
    store.append_event(
        EventDraft(
            event_type="TrialTerminated",
            entity_id="trial-forward-discovery",
            run_id="run-forward",
            payload_schema_version="trial_terminated.v1",
            payload={
                "trial_id": "trial-forward-discovery",
                "status": "success",
                "reason_codes": [],
                "decision": "candidate_zoo",
                "evaluation_event_hash": evaluation.event_hash,
                "terminated_at": utc_now_iso(),
            },
        )
    )
    policy = FinalTestPolicy(
        schema_version="final_test_policy.v1",
        policy_version="final-policy.1",
        minimum_effective_observations=4,
        minimum_rank_ic_mean=0.01,
        minimum_net_return_mean=0.0001,
    )
    candidate = FrozenFinalCandidate.create(
        factor_spec_id=attempt.factor_spec_id,
        definition_hash=definition_event.event_hash,
        transform_pipeline_hash=digest,
        cost_model_hash=digest,
        regime_config_hash=digest,
        policy_hash=policy.policy_hash,
        data_snapshot_hash=digest,
        frozen_at=utc_now_iso(),
    )
    authority = TestScopeAuthority(store=store, flags=flags)
    capability = authority.issue(
        candidate,
        run_id="run-forward",
        period_start="2025-01-01",
        period_end="2025-03-31",
    )
    request = FinalTestDataRequest(
        run_id="run-forward",
        factor_spec_id=candidate.factor_spec_id,
        candidate_hash=candidate.candidate_hash,
        data_snapshot_hash=candidate.data_snapshot_hash,
        period_start="2025-01-01",
        period_end="2025-03-31",
        fields=("net_returns", "rank_ic_series"),
    )
    artifact = FinalTestRunner(
        flags=flags,
        authority=authority,
        provider=_Provider(),
        policy=policy,
        store=store,
    ).run(candidate, capability, request, run_id="run-forward")
    return flags, store, candidate, artifact


def _plan(candidate: FrozenFinalCandidate, artifact_hash: str, **changes: object) -> FrozenForwardPlan:
    values: dict[str, object] = {
        "factor_spec_id": candidate.factor_spec_id,
        "final_test_artifact_hash": artifact_hash,
        "definition_hash": candidate.definition_hash,
        "transform_pipeline_hash": candidate.transform_pipeline_hash,
        "cost_model_hash": candidate.cost_model_hash,
        "regime_config_hash": candidate.regime_config_hash,
        "policy_hash": candidate.policy_hash,
        "expected_horizon": 5,
        "minimum_effective_observations": 10,
        "minimum_rank_ic": -0.01,
        "maximum_drawdown": 0.20,
        "kill_rules_hash": canonical_json_hash({"kill": "v1"}),
        "created_at": utc_now_iso(),
    }
    values.update(changes)
    return FrozenForwardPlan.create(**values)  # type: ignore[arg-type]


def test_forward_report_view_cannot_be_cast_to_discovery_view(tmp_path: Path) -> None:
    flags, store, candidate, artifact = _final_context(tmp_path)
    plan = _plan(candidate, artifact.artifact_hash)
    service = ForwardMonitoringService(store=store, flags=flags)
    service.record_plan(plan, run_id="run-forward")
    observation = service.append_observation(
        plan,
        period_start="2025-04-01",
        period_end="2025-04-30",
        effective_observations=4,
        rank_ic=0.02,
        net_return=0.01,
        drawdown=0.03,
        run_id="run-forward",
    )
    monitoring = ForwardProjector.monitoring_view(plan, (observation,))

    with pytest.raises(TypeError, match="monitoring or final"):
        DiscoveryEvidenceView.from_verified_subsequence(
            factual=monitoring,  # type: ignore[arg-type]
            episodic=monitoring,  # type: ignore[arg-type]
            data_snapshot_hash=candidate.data_snapshot_hash,
            verified_subsequence=object(),  # type: ignore[arg-type]
        )
    retriever = ShadowRetriever(flags=flags)
    dag = FactorDAGProjector(flags=flags).project(store.query_events())
    with pytest.raises(TypeError, match="DiscoveryEvidenceView"):
        retriever.decide(
            official_candidate_ids=(),
            evidence=monitoring,  # type: ignore[arg-type]
            query=FactorDAGQuery(dag),
            candidates=(),
            seed=1,
            candidate_budget=1,
        )


def test_forward_event_does_not_change_discovery_projection_or_retriever(
    tmp_path: Path,
) -> None:
    flags, store, candidate, artifact = _final_context(tmp_path)
    discovery_projector = DiscoveryEvidenceProjector(flags=flags)
    before_view = discovery_projector.project(
        store, data_snapshot_hash=candidate.data_snapshot_hash
    )
    retriever = ShadowRetriever(flags=flags)
    before_decision = retriever.decide(
        official_candidate_ids=(),
        evidence=before_view,
        query=FactorDAGQuery(before_view.factual.dag),
        candidates=(),
        seed=7,
        candidate_budget=1,
    )
    plan = _plan(candidate, artifact.artifact_hash)
    service = ForwardMonitoringService(store=store, flags=flags)
    service.record_plan(plan, run_id="run-forward")
    service.append_observation(
        plan,
        period_start="2025-04-01",
        period_end="2025-04-30",
        effective_observations=4,
        rank_ic=0.02,
        net_return=0.01,
        drawdown=0.03,
        run_id="run-forward",
    )
    after_view = discovery_projector.project(
        store, data_snapshot_hash=candidate.data_snapshot_hash
    )
    after_decision = retriever.decide(
        official_candidate_ids=(),
        evidence=after_view,
        query=FactorDAGQuery(after_view.factual.dag),
        candidates=(),
        seed=7,
        candidate_budget=1,
    )

    assert after_view == before_view
    assert after_decision == before_decision


def test_interleaved_monitoring_cannot_break_or_mint_discovery_evidence(
    tmp_path: Path,
) -> None:
    flags, store, candidate, artifact = _final_context(tmp_path)
    plan = _plan(candidate, artifact.artifact_hash)
    ForwardMonitoringService(store=store, flags=flags).record_plan(
        plan, run_id="run-forward"
    )
    monitoring_hashes = {
        event.event_hash
        for event in store.query_events()
        if event.event_type.startswith("Forward")
        or event.event_type.startswith("Final")
    }

    digest = canonical_json_hash({"fixture": "second-cycle"})
    second = FactorIdentityService(store=store, flags=flags).record_attempt(
        trial_id="trial-second-cycle",
        run_id="run-second-cycle",
        candidate_id="candidate-second-cycle",
        formula="rank(open)",
        semantics=FactorSpecSemantics(
            transform_pipeline_hash=digest,
            field_semantics={"open": "pit_eod"},
            signal_time="close",
            order_time="next_open",
            entry_price_time="next_open",
            execution_lag=1,
            return_horizon=5,
            universe_mask_hash=digest,
            tradability_mask_hash=digest,
        ),
    )
    assert second.factor_spec_id is not None
    evaluation = store.append_event(
        EventDraft(
            event_type="EvaluationRecorded",
            entity_id="evaluation-second-cycle",
            run_id="run-second-cycle",
            payload_schema_version="evaluation_recorded.v1",
            payload={
                "evaluation_id": "evaluation-second-cycle",
                "trial_id": "trial-second-cycle",
                "factor_spec_id": second.factor_spec_id,
                "data_scope": "valid",
                "scorecard_hash": digest,
                "artifact_refs": [],
                "metadata": {},
            },
        )
    )
    terminal = store.append_event(
        EventDraft(
            event_type="TrialTerminated",
            entity_id="trial-second-cycle",
            run_id="run-second-cycle",
            payload_schema_version="trial_terminated.v1",
            payload={
                "trial_id": "trial-second-cycle",
                "status": "success",
                "reason_codes": [],
                "decision": "candidate_zoo",
                "evaluation_event_hash": evaluation.event_hash,
                "terminated_at": utc_now_iso(),
            },
        )
    )

    projector = DiscoveryEvidenceProjector(flags=flags)
    raw_eligible = projector.eligible_events(store.query_events())
    with pytest.raises(FactorDAGError, match="out of order"):
        FactorDAGProjector(flags=flags).project(raw_eligible)

    view = projector.project(store, data_snapshot_hash=digest)
    assert len(view.factual.factor_ids()) == 2
    assert view.source_watermark == terminal.event_hash
    assert not monitoring_hashes.intersection(
        event.event_hash for event in view._verified_subsequence.events
    )
    assert not hasattr(store, "verified_subsequence")
    with pytest.raises(TypeError, match="store-verified"):
        DiscoveryEvidenceView.from_verified_subsequence(
            factual=view.factual,
            episodic=view.episodic,
            data_snapshot_hash=digest,
            verified_subsequence=object(),  # type: ignore[arg-type]
        )


def test_forward_success_is_forbidden_before_min_effective_observations(
    tmp_path: Path,
) -> None:
    flags, store, candidate, artifact = _final_context(tmp_path)
    plan = _plan(candidate, artifact.artifact_hash)
    service = ForwardMonitoringService(store=store, flags=flags)
    service.record_plan(plan, run_id="run-forward")
    observation = service.append_observation(
        plan,
        period_start="2025-04-01",
        period_end="2025-04-30",
        effective_observations=4,
        rank_ic=0.02,
        net_return=0.01,
        drawdown=0.03,
        run_id="run-forward",
    )

    with pytest.raises(ValueError, match="forbidden"):
        ForwardProjector.monitoring_view(plan, (observation,), claim_success=True)
    view = ForwardProjector.monitoring_view(plan, (observation,))
    assert view.status == "insufficient"
    assert view.success_statement is None


def test_changed_forward_definition_requires_new_plan(tmp_path: Path) -> None:
    flags, store, candidate, artifact = _final_context(tmp_path)
    service = ForwardMonitoringService(store=store, flags=flags)
    original = _plan(candidate, artifact.artifact_hash)
    changed = _plan(
        candidate,
        artifact.artifact_hash,
        definition_hash=canonical_json_hash({"definition": "changed"}),
    )

    assert changed.plan_hash != original.plan_hash
    assert changed.plan_id != original.plan_id
    service.record_plan(original, run_id="run-forward")
    with pytest.raises(ValueError, match="qualified uncontaminated"):
        service.record_plan(changed, run_id="run-forward")
    assert len(store.query_events(event_type="ForwardPlanV2Recorded")) == 1
    assert not (
        store.artifact_root
        / "forward_plans"
        / (changed.plan_hash.removeprefix("sha256:") + ".json")
    ).exists()


def test_forward_observations_are_ordered_hash_chain_and_feature_off_is_no_write(
    tmp_path: Path,
) -> None:
    flags, store, candidate, artifact = _final_context(tmp_path)
    plan = _plan(candidate, artifact.artifact_hash)
    service = ForwardMonitoringService(store=store, flags=flags)
    service.record_plan(plan, run_id="run-forward")
    first = service.append_observation(
        plan,
        period_start="2025-04-01",
        period_end="2025-04-30",
        effective_observations=5,
        rank_ic=0.02,
        net_return=0.01,
        drawdown=0.03,
        run_id="run-forward",
    )
    second = service.append_observation(
        plan,
        period_start="2025-05-01",
        period_end="2025-05-31",
        effective_observations=5,
        rank_ic=0.03,
        net_return=0.02,
        drawdown=0.02,
        run_id="run-forward",
    )
    assert second.previous_observation_hash == first.observation_hash
    assert store.verify_chain()
    with pytest.raises(ValueError, match="append after"):
        service.append_observation(
            plan,
            period_start="2025-05-15",
            period_end="2025-06-15",
            effective_observations=1,
            rank_ic=0.01,
            net_return=0.0,
            drawdown=0.01,
            run_id="run-forward",
        )
    before = len(store.query_events())
    with pytest.raises(RuntimeError, match="disabled"):
        ForwardMonitoringService(store=store, flags=_flags(forward=False))
    assert len(store.query_events()) == before


def test_final_and_forward_artifacts_feed_decision_v2_only_through_views(
    tmp_path: Path,
) -> None:
    flags, _, candidate, artifact = _final_context(tmp_path)
    plan = _plan(candidate, artifact.artifact_hash)
    repository = DecisionEvidenceRepository(tmp_path / "decision-evidence")

    def put(kind: str, payload: dict[str, object]) -> str:
        return repository.put(
            DecisionEvidenceRecord.create(
                evidence_kind=kind,  # type: ignore[arg-type]
                factor_spec_id=candidate.factor_spec_id,
                payload=payload,
            )
        )

    scorecard = put(
        "scorecard",
        {
            "formula_valid": True,
            "formula_ambiguous": False,
            "lookahead_detected": False,
            "train_valid_terminal": True,
            "reproducible": True,
            "bounded": True,
            "validation_rank_ic": 0.03,
            "regime_dependent": False,
            "limitations": ["TRAIN_VALID_ONLY"],
        },
    )
    execution = put(
        "execution",
        {
            "available": True,
            "execution_alpha": 0.002,
            "total_cost": 0.0005,
            "economically_nonnegative": True,
            "limitations": [],
        },
    )
    snapshot = put(
        "snapshot",
        {"pit_available": True, "survivorship_bias": False, "limitations": []},
    )
    ledger = put(
        "ledger",
        {
            "complete": True,
            "terminal_train_valid": True,
            "reduced_durability": False,
            "ledger_schema_version": "decision_ledger_evidence.v2",
            "infrastructure_failure_event_hashes": [],
            "limitations": [],
        },
    )
    mechanism = put(
        "mechanism",
        {
            "contract_registered": True,
            "decisive_available": True,
            "ordinal_state": "supported",
            "limitations": ["ORDINAL_NOT_PROBABILITY"],
        },
    )
    complement = put(
        "complement",
        {"status": "complementary", "limitations": ["TRAIN_VALID_ONLY"]},
    )
    final_view = FinalDecisionEvidenceView.create(
        artifact=artifact,
        contaminated=False,
        limitations=artifact.limitations,
    )
    final_hash = repository.put(final_view.to_decision_record())
    plan_hash = repository.put(plan.to_decision_record())
    decision_policy = DecisionV2Policy(
        schema_version="decision_v2_policy.v1",
        policy_version="decision-v2-policy.1",
    )
    result = QualityDecisionV2Runner(
        flags=flags,
        policy=decision_policy,
        repository=repository,
    ).run(
        DecisionEvidenceRefs(
            factor_spec_id=candidate.factor_spec_id,
            scorecard_hash=scorecard,
            execution_hash=execution,
            snapshot_hash=snapshot,
            ledger_watermark_hash=ledger,
            mechanism_evidence_hash=mechanism,
            complement_evidence_hash=complement,
            final_test_artifact_hash=final_hash,
            forward_plan_hash=plan_hash,
        )
    )

    assert result.decision == "forward_track"
    assert result.forward_success_claim is False
