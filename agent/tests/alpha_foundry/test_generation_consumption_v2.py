from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src.alpha_foundry.activation import (
    ActivationArtifactStore,
    ActivationEvidenceService,
    ActivationExperimentPlan,
    PairedActivationRunner,
    activation_arm_execution_run_id,
)
from src.alpha_foundry.activation.generation_consumption_v2 import (
    ActivationTreatmentGeneratorV2,
    BoundSelectedActionV2,
    ExactSelectedActionMutatorV2,
)
from src.alpha_foundry.control_evidence import (
    FlatControlPolicyV1,
    OfficialSearchControlServiceV1,
)
from src.alpha_foundry.mutators import SeedMutator
from src.alpha_foundry.retrieval.action_template_v1 import (
    RetrieverActionTemplateServiceV1,
)
from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy
from src.alpha_foundry.retrieval.service_v5 import RetrieverDecisionV5Service
from src.alpha_foundry.search import AlphaFoundrySearch
from src.alpha_foundry.search_lifecycle import EventSourcedSearchLifecycle
from src.alpha_foundry.seed_bank import AlphaSeed, SeedBank
from src.research_ledger.events import EventDraft, EventValidationError
from src.research_ledger.events.artifacts import hash_artifact
from src.research_ledger.hash_utils import canonical_json_hash
from test_activation_retriever import plan
from test_generation_consumption_v1 import _SkipEvaluator, _manifest
from test_retriever_shadow import _candidate, _views
from test_search_lifecycle import _semantics


def _run(tmp_path: Path):
    store, query, discovery = _views(tmp_path)
    base_candidate = _candidate(query, discovery)
    control_policy = FlatControlPolicyV1.create(
        max_candidates_per_seed=3,
        max_candidates=3,
        trial_budget=3,
    )
    retriever_policy = ActivationRetrieverPolicy()
    base = plan()
    frozen_plan = ActivationExperimentPlan.create(
        experiment_id="exact-generation-consumption-fixture",
        phase="confirmatory",
        registered_at=base.registered_at,
        provenance=replace(
            base.provenance,
            control_policy_hash=control_policy.policy_hash,
            treatment_policy_hash=retriever_policy.policy_hash,
            discovery_watermark=discovery.source_watermark,
            eligible_event_chain_head=discovery.source_watermark,
            train_snapshot_hash=discovery.data_snapshot_hash,
            valid_snapshot_hash=discovery.data_snapshot_hash,
        ),
        design=replace(base.design, candidate_budget=3, compute_budget=3),
        analysis=base.analysis,
        readiness_requirements=base.readiness_requirements,
        decision_policy_hash=base.decision_policy_hash,
        truth_table_hash=base.truth_table_hash,
    )
    runner = PairedActivationRunner(ActivationArtifactStore(tmp_path / "activation"))
    evidence_service = ActivationEvidenceService(store)
    evidence_service.register_plan(frozen_plan)
    treatment_run_id = activation_arm_execution_run_id(
        plan_hash=frozen_plan.plan_hash,
        run_group_id="group-00",
        arm="treatment",
    )
    action_service = RetrieverActionTemplateServiceV1(
        store=store,
        flags=store.flags,
    )
    actions = tuple(
        action_service.freeze(
            execution_run_id=treatment_run_id,
            parent_factor_spec_id=base_candidate.factor_spec_id,
            template_id=template_id,
            eligible_event_watermark=discovery.source_watermark,
            data_snapshot_hash=discovery.data_snapshot_hash,
            retrieval_policy_hash=retriever_policy.policy_hash,
        )
        for template_id in ("rank_wrap", "delay_1", "zscore_wrap")
    )
    candidates = tuple(
        replace(
            base_candidate,
            action_id=item.action.action_id,
            motif=str(item.action.expected_motif),
        )
        for item in actions
    )
    holder = {}

    def execute(request, scope):
        lifecycle = EventSourcedSearchLifecycle(
            store=store,
            flags=store.flags,
            semantics=_semantics(),
            evaluator=_SkipEvaluator(),
            data_snapshot_hash=discovery.data_snapshot_hash,
        )
        if request.arm == "control":
            search = AlphaFoundrySearch(
                seed_bank=SeedBank(
                    [AlphaSeed("flat-control-parent", "delay(volume, 11)", "fixture")]
                ),
                mutator=SeedMutator(max_candidates_per_seed=3),
                max_candidates=request.candidate_budget,
                trial_budget=request.compute_budget,
                lifecycle=lifecycle,
                run_id=request.execution_run_id,
            )
            result = search.generate()
            holder["control"] = OfficialSearchControlServiceV1(store).record(
                search, result
            )
            return _manifest(request, discovery.data_snapshot_hash, result)

        recorded = RetrieverDecisionV5Service(store, policy=retriever_policy).record(
            control_evidence_event_hash=holder["control"].event.event_hash,
            action_template_event_hashes=tuple(
                item.event.event_hash for item in actions
            ),
            candidates=candidates,
            data_snapshot_hash=discovery.data_snapshot_hash,
            eligible_event_watermark=discovery.source_watermark,
            seed=request.seed,
            candidate_budget=request.candidate_budget,
            control_run_id=holder["control"].event.run_id,
            run_id=request.execution_run_id,
        )
        generated = ActivationTreatmentGeneratorV2().run(
            request=request,
            scope=scope,
            recorded_decision=recorded,
            parent_seeds=(
                AlphaSeed(
                    base_candidate.factor_spec_id,
                    "zscore(rank(open))",
                    "fixture",
                ),
            ),
            lifecycle=lifecycle,
        )
        relative = evidence_service.record_generation_consumption_v2(
            generated.evidence
        )
        holder.update(
            request=request,
            scope=scope,
            recorded=recorded,
            generated=generated,
            relative=relative,
        )
        return _manifest(request, discovery.data_snapshot_hash, generated.search_result)

    runner.run_pair(
        runner.register(frozen_plan),
        run_group_id="group-00",
        mechanism_family="momentum",
        dag_region="leaf",
        executor=execute,
    )
    return store, discovery, actions, candidates, holder


def test_v2_consumes_only_selected_actions_in_v5_draw_order(tmp_path: Path) -> None:
    store, _, actions, _, holder = _run(tmp_path)
    recorded = holder["recorded"]
    evidence = holder["generated"].evidence
    result = holder["generated"].search_result
    assert evidence.selected_action_ids == recorded.decision.selected_action_ids
    assert evidence.consumed_action_ids == evidence.selected_action_ids
    assert evidence.selected_parent_factor_spec_ids == (
        evidence.selected_parent_factor_spec_ids[0],
    ) * 3
    assert evidence.consumed_parent_factor_spec_ids == (
        evidence.selected_parent_factor_spec_ids[0],
    ) * 3
    action_by_id = {item.action.action_id: item.action for item in actions}
    assert [candidate.formula for candidate in result.candidates] == [
        action_by_id[action_id].expected_formula
        for action_id in evidence.selected_action_ids
    ]
    assert [candidate.metadata["retriever_action_id"] for candidate in result.candidates] == list(
        evidence.selected_action_ids
    )
    assert {str(candidate.metadata["mutation"]) for candidate in result.candidates} == {
        "rank_wrap", "delay_1", "zscore_wrap",
    }
    assert evidence.source_failure_codes == (
        "RETRIEVER_FEATURE_SOURCE_UNVERIFIED",
    )
    assert "RETRIEVER_ACTION_SOURCE_UNVERIFIED" not in evidence.source_failure_codes
    assert evidence.source_complete is False
    events = store.query_events(
        event_type="ActivationGenerationConsumptionV2Recorded"
    )
    assert len(events) == 1
    assert events[0].payload["selected_action_ids"] == evidence.selected_action_ids
    assert store.verify_chain()


def test_exact_mutator_rejects_unselected_action_seed(tmp_path: Path) -> None:
    _, _, actions, _, _ = _run(tmp_path)
    bound = BoundSelectedActionV2(
        action_event_hash=actions[0].event.event_hash,
        action=actions[0].action,
    )
    mutator = ExactSelectedActionMutatorV2((bound,))
    with pytest.raises(ValueError, match="unselected action seed"):
        mutator.mutate(
            AlphaSeed(
                "caller-action",
                "zscore(rank(open))",
                "fixture",
                parent_seed_id=bound.action.parent_factor_spec_id,
            )
        )


def test_rehashed_v2_action_substitution_is_rejected_by_replay(
    tmp_path: Path,
) -> None:
    store, _, _, _, holder = _run(tmp_path)
    event = store.query_events(
        event_type="ActivationGenerationConsumptionV2Recorded"
    )[0]
    evidence = holder["generated"].evidence
    artifact_store = ActivationArtifactStore(store.artifact_root)
    forged = evidence.to_dict()
    forged["generated_candidates"][0]["action_id"] = evidence.selected_action_ids[1]
    forged["evidence_hash"] = canonical_json_hash(
        forged,
        exclude_keys=("evidence_hash",),
    )
    relative = artifact_store.put("generation_consumption_v2", forged)
    forged_id = (
        "activation-generation-v2-"
        + forged["evidence_hash"].removeprefix("sha256:")[:24]
    )
    payload = event.to_dict()["payload"]
    payload.update(
        generation_id=forged_id,
        evidence_hash=forged["evidence_hash"],
        artifact_refs=[
            holder["recorded"].event.payload["artifact_refs"][0]
        ],
    )
    payload["artifact_refs"] = [
        {
            "relative_path": relative,
            "artifact_hash": hash_artifact(
                store.artifact_root.joinpath(*relative.split("/"))
            ),
            "media_type": (
                "application/vnd.vibe.activation-generation-consumption-v2+json"
            ),
        }
    ]
    with pytest.raises(EventValidationError, match="evidence is invalid"):
        store.append_event(
            EventDraft(
                event_type="ActivationGenerationConsumptionV2Recorded",
                entity_id=forged_id,
                run_id=evidence.execution_run_id,
                payload_schema_version=(
                    "activation_generation_consumption_recorded.v2"
                ),
                payload=payload,
                idempotency_key="activation-generation-v2:forged-action",
            )
        )
