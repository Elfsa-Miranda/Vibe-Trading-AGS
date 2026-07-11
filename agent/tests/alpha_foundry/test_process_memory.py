from __future__ import annotations

import json
from dataclasses import replace

import pytest

from src.alpha_foundry.dag import FactorDAGProjector, FactorDAGService
from src.alpha_foundry.dsl.identity import FactorIdentityService, FactorSpecSemantics
from src.alpha_foundry.dsl.canonical import thaw_canonical_ast
from src.alpha_foundry.memory import (
    EpisodicProjector, FactualMemoryView, ProcessMemoryService, WorkingMemory,
)
from src.alpha_foundry.memory.model import ProcessMemoryObservation
from src.alpha_quality.flags import ResolvedAGSFlags
from src.research_ledger.events import (
    EventDraft, EventTransitionError, EventValidationError, ResearchEventStore,
)
from src.research_ledger.hash_utils import canonical_json_hash, utc_now_iso


def _flags() -> ResolvedAGSFlags:
    return ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_ALPHA_FOUNDRY": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
            "VIBE_TRADING_FACTOR_DAG": "1",
            "VIBE_TRADING_PROCESS_MEMORY": "1",
            "VIBE_TRADING_FORWARD_TRACKING": "1",
        }
    )


def _semantics() -> FactorSpecSemantics:
    digest = canonical_json_hash({"memory": "fixture"})
    return FactorSpecSemantics(
        transform_pipeline_hash=digest,
        field_semantics={"close": "pit", "open": "pit"},
        signal_time="close",
        order_time="open",
        entry_price_time="open",
        execution_lag=1,
        return_horizon=5,
        universe_mask_hash=digest,
        tradability_mask_hash=digest,
    )


def _store(tmp_path) -> ResearchEventStore:
    return ResearchEventStore(
        tmp_path / "research.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=_flags(),
        code_version="memory-test",
    )


def _scorecard(store: ResearchEventStore, factor_id: str, snapshot_hash: str) -> dict[str, str]:
    relative = "scorecards/valid.json"
    path = store.artifact_root / "scorecards" / "valid.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "alpha_quality_scorecard.v1",
        "factor_id": factor_id,
        "scope": "discovery",
        "data_snapshot_ref": snapshot_hash,
        "predictive": {
            "by_horizon": {
                "5": {
                    "horizon": 5,
                    "by_split": {"valid": {"rank_icir": 0.30}},
                }
            }
        },
    }
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return {
        "relative_path": relative,
        "artifact_hash": store.hash_artifact(path),
        "media_type": "application/vnd.vibe.alpha-quality-scorecard+json",
    }


def _record_valid_outcome(tmp_path, *, run_group_id: str = "group-1"):
    store = _store(tmp_path)
    identity = FactorIdentityService(store=store, flags=_flags())
    dag = FactorDAGService(store=store, flags=_flags())
    memory = ProcessMemoryService(store=store, flags=_flags())
    parent = identity.record_attempt(
        trial_id="parent", run_id="run", candidate_id="parent",
        formula="rank(close)", semantics=_semantics(),
    )
    assert parent.factor_spec_id and parent.expression
    watermark = store.replay().watermark_event_hash
    assert watermark
    policy_hash = canonical_json_hash({"policy": "v2"})
    snapshot_hash = canonical_json_hash({"snapshot": "valid-1"})
    action = memory.freeze_action(
        action_id="action-1", trial_id="child", parent_factor_spec_id=parent.factor_spec_id,
        candidate_id="child", base_expected_utility=0.10,
        eligible_event_watermark=watermark, policy_hash=policy_hash,
        data_snapshot_hash=snapshot_hash, run_group_id=run_group_id,
        seed=7, candidate_budget=10, run_id="run",
    )
    child = identity.record_attempt(
        trial_id="child", run_id="run", candidate_id="child",
        formula="zscore(rank(open))", semantics=_semantics(),
    )
    assert child.factor_spec_id and child.expression
    artifact = _scorecard(store, child.factor_spec_id, snapshot_hash)
    evaluation = store.append_event(
        EventDraft(
            event_type="EvaluationRecorded", entity_id="eval-child", run_id="run",
            payload_schema_version="evaluation_recorded.v1",
            idempotency_key="evaluation:child",
            payload={
                "evaluation_id": "eval-child", "trial_id": "child",
                "factor_spec_id": child.factor_spec_id, "data_scope": "valid",
                "scorecard_hash": artifact["artifact_hash"], "artifact_refs": [artifact],
                "metadata": {"source": "deterministic_scorecard"},
            },
        )
    )
    terminal = store.append_event(
        EventDraft(
            event_type="TrialTerminated", entity_id="child", run_id="run",
            payload_schema_version="trial_terminated.v1", idempotency_key="terminal:child",
            payload={
                "trial_id": "child", "status": "success", "reason_codes": ["EVALUATED"],
                "decision": "candidate_zoo", "evaluation_event_hash": evaluation.event_hash,
                "terminated_at": utc_now_iso(),
            },
        )
    )
    derivation = dag.record_derivation(
        child_factor_spec_id=child.factor_spec_id,
        parent_factor_spec_ids=[parent.factor_spec_id],
        trial_terminal_event_hash=terminal.event_hash,
        derivation_kind="mutation", run_id="run",
    )
    outcome = memory.record_outcome(
        outcome_id="outcome-1", action_id="action-1", trial_id="child",
        terminal_event_hash=terminal.event_hash,
        evaluation_event_hash=evaluation.event_hash,
        derivation_event_hash=derivation.event_hash,
        child_factor_spec_id=child.factor_spec_id,
        parent_expression=parent.expression, child_expression=child.expression,
        run_id="run",
    )
    return store, action, derivation, evaluation, outcome


def test_motif_is_derived_from_diff_and_records_evidence_hashes(tmp_path) -> None:
    store, action, derivation, evaluation, _ = _record_valid_outcome(tmp_path)
    projection = EpisodicProjector().project(store.query_events())

    assert projection.schema_version == "episodic_process_projection.v2"
    assert len(projection.observations) == 1
    observation = projection.observations[0]
    assert observation.derivation_event_hash == derivation.event_hash
    assert observation.evaluation_event_hash == evaluation.event_hash
    assert observation.eligible_event_watermark == action.payload["eligible_event_watermark"]
    assert observation.observed_validation_utility == pytest.approx(0.30)
    assert observation.motif_version == "ast-motif.v1"


def test_replay_and_factual_discovery_view_are_terminal_and_deterministic(tmp_path) -> None:
    store, _, _, _, _ = _record_valid_outcome(tmp_path)
    events = store.query_events()
    first = EpisodicProjector().project(events)
    second = EpisodicProjector().project(tuple(events))
    dag = FactorDAGProjector(flags=_flags()).project(events)
    factual = FactualMemoryView.from_terminal_discovery_events(dag, events)

    assert first == second
    assert first.projection_hash == second.projection_hash
    assert factual.factor_ids() == (first.observations[0].child_factor_spec_id,)


def test_final_and_forward_events_do_not_change_eligible_memory(tmp_path) -> None:
    store, _, _, _, _ = _record_valid_outcome(tmp_path)
    before = EpisodicProjector().project(store.query_events())
    child_id = before.observations[0].child_factor_spec_id
    store.append_event(
        EventDraft(
            event_type="TrialStarted", entity_id="final-trial", run_id="monitor",
            payload_schema_version="trial_started.v1", idempotency_key="final-trial:start",
            payload={
                "trial_id": "final-trial", "candidate_id": "final-candidate",
                "data_scope": "final_test", "objective": "final_only",
                "started_at": utc_now_iso(),
            },
        )
    )
    store.append_event(
        EventDraft(
            event_type="EvaluationRecorded", entity_id="final-evaluation", run_id="monitor",
            payload_schema_version="evaluation_recorded.v1",
            idempotency_key="final-evaluation:record",
            payload={
                "evaluation_id": "final-evaluation", "trial_id": "final-trial",
                "factor_spec_id": child_id, "data_scope": "final_test",
                "scorecard_hash": canonical_json_hash({"final": True}),
                "artifact_refs": [], "metadata": {},
            },
        )
    )
    plan_hash = canonical_json_hash({"plan": "monitor-only"})
    store.append_event(
        EventDraft(
            event_type="ForwardPlanRecorded", entity_id="plan-monitor", run_id="monitor",
            payload_schema_version="forward_plan_recorded.v1", idempotency_key="plan-monitor",
            payload={
                "plan_id": "plan-monitor", "factor_spec_id": child_id,
                "plan_hash": plan_hash, "minimum_observations": 12,
                "policy_hash": canonical_json_hash({"policy": "monitor"}),
            },
        )
    )
    after = EpisodicProjector().project(store.query_events())
    assert after.observations == before.observations
    assert after.posteriors == before.posteriors


def test_trial_start_and_action_survive_generation_crash(tmp_path) -> None:
    store = _store(tmp_path)
    identity = FactorIdentityService(store=store, flags=_flags())
    memory = ProcessMemoryService(store=store, flags=_flags())
    parent = identity.record_attempt(
        trial_id="parent", run_id="run", candidate_id="parent",
        formula="rank(close)", semantics=_semantics(),
    )
    watermark = store.replay().watermark_event_hash
    assert parent.factor_spec_id and watermark
    memory.freeze_action(
        action_id="crash-action", trial_id="crash-trial",
        parent_factor_spec_id=parent.factor_spec_id, candidate_id="crash-candidate",
        base_expected_utility=0.0, eligible_event_watermark=watermark,
        policy_hash=canonical_json_hash({"policy": "v2"}),
        data_snapshot_hash=canonical_json_hash({"snapshot": "v"}),
        run_group_id="crash-group", seed=2, candidate_budget=1, run_id="run",
    )
    assert len(store.query_events(event_type="TrialStarted", entity_id="crash-trial")) == 1
    assert len(store.query_events(event_type="ProcessActionFrozenV2", entity_id="crash-action")) == 1
    assert EpisodicProjector().project(store.query_events()).observations == ()


def test_action_freeze_must_precede_child_definition(tmp_path) -> None:
    store = _store(tmp_path)
    identity = FactorIdentityService(store=store, flags=_flags())
    memory = ProcessMemoryService(store=store, flags=_flags())
    parent = identity.record_attempt(
        trial_id="parent", run_id="run", candidate_id="parent",
        formula="rank(close)", semantics=_semantics(),
    )
    watermark = store.replay().watermark_event_hash
    identity.record_attempt(
        trial_id="child", run_id="run", candidate_id="child",
        formula="rank(open)", semantics=_semantics(),
    )
    assert parent.factor_spec_id and watermark
    with pytest.raises(EventTransitionError, match="before child generation"):
        memory.freeze_action(
            action_id="late", trial_id="child", parent_factor_spec_id=parent.factor_spec_id,
            candidate_id="child", base_expected_utility=0.1,
            eligible_event_watermark=watermark,
            policy_hash=canonical_json_hash({"policy": "v2"}),
            data_snapshot_hash=canonical_json_hash({"snapshot": "v"}),
            run_group_id="group", seed=1, candidate_budget=2, run_id="run",
        )


def test_duplicate_action_outcome_and_forged_diff_fail_closed(tmp_path) -> None:
    store, action, _, _, outcome = _record_valid_outcome(tmp_path)
    forged_utility = thaw_canonical_ast(outcome.payload)
    forged_utility["outcome_id"] = "forged-utility"
    forged_utility["observed_validation_utility"] = 99.0
    with pytest.raises(EventValidationError, match="deterministically rebuilt"):
        store.append_event(
            EventDraft(
                event_type="ProcessOutcomeRecordedV2", entity_id="forged-utility",
                run_id="run", payload_schema_version="process_outcome_recorded.v2",
                payload=forged_utility, idempotency_key="process-outcome-v2:forged-utility",
            )
        )
    forged = thaw_canonical_ast(outcome.payload)
    forged["outcome_id"] = "forged"
    forged["ast_diff"]["extractor_hash"] = canonical_json_hash({"fake": True})
    forged["ast_diff_hash"] = canonical_json_hash(forged["ast_diff"])
    with pytest.raises(EventTransitionError, match="already has"):
        store.append_event(
            EventDraft(
                event_type="ProcessOutcomeRecordedV2", entity_id="forged", run_id="run",
                payload_schema_version="process_outcome_recorded.v2", payload=forged,
                idempotency_key="process-outcome-v2:forged",
            )
        )
    replay_events = [
        replace(event, payload=forged) if event.event_hash == outcome.event_hash else event
        for event in store.query_events()
    ]
    with pytest.raises(ValueError, match="extractor"):
        EpisodicProjector().project(replay_events)
    assert action.payload["action_id"] == "action-1"


def test_independent_run_groups_gate_positive_memory() -> None:
    base = ProcessMemoryObservation(
        "ctx", "parent", "child", "edge", "evaluation", "scorecard", "diff",
        "ast-motif.v1", "Wrap:rank", 0.0, 0.5, 0.5, "success", (), None,
        "snapshot", "watermark", "same-group", "policy", "now",
    )
    projector = EpisodicProjector(minimum_effective_count=3, max_positive_adjustment=0.05)
    correlated = projector._posteriors([replace(base, child_factor_spec_id=f"child-{i}") for i in range(5)])[0]
    independent = projector._posteriors(
        [replace(base, child_factor_spec_id=f"child-{i}", run_group_id=f"group-{i}") for i in range(3)]
    )[0]
    assert correlated.effective_count == 1 and correlated.observation_count == 5
    assert correlated.positive_adjustment == 0.0
    assert independent.effective_count == 3
    assert independent.positive_adjustment == 0.05


def test_working_memory_is_bounded_deeply_immutable_and_destroyed(tmp_path) -> None:
    payload = {"score": [1, {"nested": 2}]}
    with WorkingMemory(capacity=2) as working:
        working.add("one", payload)
        payload["score"][1]["nested"] = 99
        working.add("two", {"score": 2})
        working.add("three", {"score": 3})
        assert [item.candidate_id for item in working.snapshot()] == ["two", "three"]
    with pytest.raises(RuntimeError, match="destroyed"):
        working.snapshot()
    assert _store(tmp_path).query_events() == []


def test_process_memory_flag_off_refuses_service(tmp_path) -> None:
    flags = ResolvedAGSFlags.from_settings(
        {"VIBE_TRADING_AGS_ENABLED": "1", "VIBE_TRADING_RESEARCH_EVENTS": "1"}
    )
    store = ResearchEventStore(
        tmp_path / "db.sqlite", artifact_root=tmp_path / "artifacts",
        flags=flags, code_version="test",
    )
    with pytest.raises(RuntimeError, match="disabled"):
        ProcessMemoryService(store=store, flags=flags)
