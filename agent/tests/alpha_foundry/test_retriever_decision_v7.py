from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.alpha_foundry.activation import ActivationEvidenceService, ActivationExperimentPlan
from src.alpha_foundry.activation.runner import activation_arm_execution_run_id
from src.alpha_foundry.control_evidence import FlatControlPolicyV1
from src.alpha_foundry.flat_schedule_v1 import PreArmFlatScheduleServiceV1
from src.alpha_foundry.mutators import SeedMutator
from src.alpha_foundry.retrieval.action_template_v1 import RetrieverActionTemplateServiceV1
from src.alpha_foundry.retrieval.feature_producer_v1 import RetrieverFeatureSourceServiceV1
from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy
from src.alpha_foundry.retrieval.service_v7 import RetrieverDecisionV7Service
from src.alpha_foundry.search import AlphaFoundrySearch
from src.alpha_foundry.search_lifecycle import EventSourcedSearchLifecycle
from src.alpha_foundry.seed_bank import AlphaSeed, SeedBank
from src.research_ledger.events import (
    EventDraft,
    EventValidationError,
    ResearchEventAppendError,
)
from src.research_ledger.hash_utils import canonical_json_hash
from test_activation_retriever import plan
from test_generation_consumption_v1 import _SkipEvaluator
from test_retriever_feature_producer_v1 import _record as _feature_fixture
from test_search_lifecycle import _semantics


def _record(tmp_path: Path):
    store, snapshot, target, _, _, initial = _feature_fixture(tmp_path)
    flat_policy = FlatControlPolicyV1.create(
        max_candidates_per_seed=3, max_candidates=3, trial_budget=3
    )
    retriever_policy = ActivationRetrieverPolicy()
    base = plan()
    frozen = ActivationExperimentPlan.create(
        experiment_id="schedule-bound-v7-fixture",
        phase="confirmatory",
        registered_at=base.registered_at,
        provenance=replace(
            base.provenance,
            control_policy_hash=flat_policy.policy_hash,
            treatment_policy_hash=retriever_policy.policy_hash,
            discovery_watermark=initial.source.eligible_event_watermark,
            eligible_event_chain_head=initial.source.eligible_event_watermark,
            train_snapshot_hash=snapshot.snapshot.snapshot_hash,
            valid_snapshot_hash=snapshot.snapshot.snapshot_hash,
        ),
        design=replace(base.design, candidate_budget=3, compute_budget=3),
        analysis=base.analysis,
        readiness_requirements=base.readiness_requirements,
        decision_policy_hash=base.decision_policy_hash,
        truth_table_hash=base.truth_table_hash,
    )
    ActivationEvidenceService(store).register_plan(frozen)
    treatment_run_id = activation_arm_execution_run_id(
        plan_hash=frozen.plan_hash, run_group_id="group-00", arm="treatment"
    )
    schedule = PreArmFlatScheduleServiceV1(store).freeze(
        plan_hash=frozen.plan_hash,
        pair_id="group-00:momentum:leaf",
        run_group_id="group-00",
        data_snapshot_hash=snapshot.snapshot.snapshot_hash,
        seed_bank=SeedBank([AlphaSeed("flat", "close", "fixture")]),
        mutator=SeedMutator(max_candidates_per_seed=3),
        max_candidates=3,
        trial_budget=3,
    )
    action_service = RetrieverActionTemplateServiceV1(store=store, flags=store.flags)
    actions = tuple(
        action_service.freeze(
            execution_run_id=treatment_run_id,
            parent_factor_spec_id=target.factor_spec_id,
            template_id=template,
            eligible_event_watermark=initial.source.eligible_event_watermark,
            data_snapshot_hash=snapshot.snapshot.snapshot_hash,
            retrieval_policy_hash=retriever_policy.policy_hash,
        )
        for template in ("rank_wrap", "delay_1", "zscore_wrap")
    )
    source = RetrieverFeatureSourceServiceV1(store, flags=store.flags).record(
        execution_run_id=treatment_run_id,
        snapshot_event_hash=snapshot.event.event_hash,
        action_event_hashes=tuple(item.event.event_hash for item in actions),
        eligible_event_watermark=initial.source.eligible_event_watermark,
        retrieval_policy=retriever_policy,
    )
    recorded = RetrieverDecisionV7Service(store).record(
        schedule_event_hash=schedule.event.event_hash,
        feature_source_event_hash=source.event.event_hash,
    )
    return store, frozen, snapshot, source, schedule, recorded


def test_v7_derives_seed_budget_and_official_order_before_outcomes(tmp_path: Path) -> None:
    store, frozen, _, source, schedule, recorded = _record(tmp_path)
    index = frozen.design.run_group_ids.index("group-00")
    assert recorded.decision.seed == frozen.design.seeds[index]
    assert recorded.decision.candidate_budget == frozen.design.candidate_budget
    assert recorded.decision.official_output_hash == schedule.schedule.output_hash
    assert recorded.decision.action_template_event_hashes == source.source.action_event_hashes
    arm_run_ids = {
        activation_arm_execution_run_id(
            plan_hash=frozen.plan_hash, run_group_id="group-00", arm=arm
        )
        for arm in ("control", "treatment")
    }
    assert not [event for event in store.query_events(event_type="TrialStarted")
                if event.run_id in arm_run_ids]
    assert store.verify_chain()


def test_v7_has_no_caller_seed_or_budget_channel(tmp_path: Path) -> None:
    store, _, _, source, schedule, _ = _record(tmp_path)
    with pytest.raises(TypeError, match="unexpected keyword argument 'seed'"):
        RetrieverDecisionV7Service(store).record(
            schedule_event_hash=schedule.event.event_hash,
            feature_source_event_hash=source.event.event_hash,
            seed=999,
            candidate_budget=1,
        )


def test_v7_rejects_schedule_chosen_after_treatment_features(tmp_path: Path) -> None:
    store, _, _, _, schedule, _ = _record(tmp_path)
    earlier_source = store.query_events(
        event_type="RetrieverFeatureSourceRecorded"
    )[0]
    with pytest.raises(ValueError, match="schedule must precede treatment features"):
        RetrieverDecisionV7Service(store).record(
            schedule_event_hash=schedule.event.event_hash,
            feature_source_event_hash=earlier_source.event_hash,
        )


def test_v7_rejects_decision_after_either_arm_outcome(tmp_path: Path) -> None:
    store, frozen, snapshot, source, schedule, _ = _record(tmp_path)
    control_run_id = activation_arm_execution_run_id(
        plan_hash=frozen.plan_hash, run_group_id="group-00", arm="control"
    )
    lifecycle = EventSourcedSearchLifecycle(
        store=store, flags=store.flags, semantics=_semantics(),
        evaluator=_SkipEvaluator(), data_snapshot_hash=snapshot.snapshot.snapshot_hash,
    )
    AlphaFoundrySearch(
        seed_bank=SeedBank([AlphaSeed("late", "open", "fixture")]),
        mutator=SeedMutator(max_candidates_per_seed=1), max_candidates=1,
        trial_budget=1, lifecycle=lifecycle, run_id=control_run_id,
    ).generate()
    assert store.verify_chain()
    with pytest.raises(ValueError, match="precede both arm outcomes"):
        RetrieverDecisionV7Service(store).record(
            schedule_event_hash=schedule.event.event_hash,
            feature_source_event_hash=source.event.event_hash,
        )


def test_rehashed_v7_component_fabrication_fails_rebuild(tmp_path: Path) -> None:
    store, _, _, _, _, recorded = _record(tmp_path)
    payload = recorded.event.to_dict()["payload"]
    payload["components"][0]["action_score"] += 10.0
    content = {
        "schema_version": "retriever_action_schedule_bound_decision.v7",
        **{key: value for key, value in payload.items()
           if key not in {"decision_id", "decision_hash", "artifact_refs"}},
    }
    payload["decision_hash"] = canonical_json_hash(content)
    payload["decision_id"] = (
        "retriever-v7-" + payload["decision_hash"].removeprefix("sha256:")[:20]
    )
    with pytest.raises(EventValidationError, match="deterministic rebuild"):
        store.append_event(
            EventDraft(
                event_type="RetrieverDecisionV7Recorded",
                entity_id=payload["decision_id"],
                run_id=recorded.event.run_id,
                payload_schema_version="retriever_decision_recorded.v7",
                payload=payload,
                idempotency_key="retriever-v7:forged-component",
            )
        )


def test_v7_append_rebuilds_upstream_scorecards(tmp_path: Path) -> None:
    store, _, _, source, schedule, _ = _record(tmp_path)
    evaluation = next(
        event for event in store.query_events(event_type="EvaluationRecorded")
        if event.event_hash in source.source.scorecard_event_hashes
    )
    reference = evaluation.payload["artifact_refs"][0]
    path = store.artifact_root.joinpath(*str(reference["relative_path"]).split("/"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["execution"]["cost_bps_mean"] += 1.0
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(
        (EventValidationError, ResearchEventAppendError),
        match="upstream|feature source|invalid historical prefix",
    ):
        RetrieverDecisionV7Service(store).record(
            schedule_event_hash=schedule.event.event_hash,
            feature_source_event_hash=source.event.event_hash,
        )
