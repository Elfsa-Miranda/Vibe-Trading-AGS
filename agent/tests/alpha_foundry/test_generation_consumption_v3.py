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
from src.alpha_foundry.activation.generation_consumption_v3 import (
    ActivationGenerationConsumptionV3,
    ActivationTreatmentGeneratorV3,
)
from src.alpha_foundry.control_evidence import (
    FlatControlPolicyV1,
    OfficialSearchControlServiceV1,
)
from src.alpha_foundry.mutators import SeedMutator
from src.alpha_foundry.retrieval.action_template_v1 import (
    RetrieverActionTemplateServiceV1,
)
from src.alpha_foundry.retrieval.feature_producer_v1 import (
    RetrieverFeatureSourceServiceV1,
)
from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy
from src.alpha_foundry.retrieval.service_v6 import RetrieverDecisionV6Service
from src.alpha_foundry.search import AlphaFoundrySearch
from src.alpha_foundry.search_lifecycle import EventSourcedSearchLifecycle
from src.alpha_foundry.seed_bank import AlphaSeed, SeedBank
from src.research_ledger.events import EventDraft, EventValidationError
from src.research_ledger.events.artifacts import hash_artifact
from src.research_ledger.hash_utils import canonical_json_hash
from test_activation_retriever import plan
from test_generation_consumption_v1 import _SkipEvaluator, _manifest
from test_retriever_feature_producer_v1 import _record as _record_feature_source
from test_search_lifecycle import _semantics


def _run(tmp_path: Path):
    store, snapshot, target, _, first_action, first_source = _record_feature_source(
        tmp_path
    )
    control_policy = FlatControlPolicyV1.create(
        max_candidates_per_seed=3, max_candidates=3, trial_budget=3
    )
    retriever_policy = ActivationRetrieverPolicy()
    base = plan()
    frozen_plan = ActivationExperimentPlan.create(
        experiment_id="source-bound-generation-fixture",
        phase="confirmatory",
        registered_at=base.registered_at,
        provenance=replace(
            base.provenance,
            control_policy_hash=control_policy.policy_hash,
            treatment_policy_hash=retriever_policy.policy_hash,
            discovery_watermark=first_source.source.eligible_event_watermark,
            eligible_event_chain_head=first_source.source.eligible_event_watermark,
            train_snapshot_hash=snapshot.snapshot.snapshot_hash,
            valid_snapshot_hash=snapshot.snapshot.snapshot_hash,
        ),
        design=replace(base.design, candidate_budget=3, compute_budget=3),
        analysis=base.analysis,
        readiness_requirements=base.readiness_requirements,
        decision_policy_hash=base.decision_policy_hash,
        truth_table_hash=base.truth_table_hash,
    )
    treatment_run_id = activation_arm_execution_run_id(
        plan_hash=frozen_plan.plan_hash,
        run_group_id="group-00",
        arm="treatment",
    )
    action_service = RetrieverActionTemplateServiceV1(store=store, flags=store.flags)
    actions = tuple(
        action_service.freeze(
            execution_run_id=treatment_run_id,
            parent_factor_spec_id=target.factor_spec_id,
            template_id=template_id,
            eligible_event_watermark=first_source.source.eligible_event_watermark,
            data_snapshot_hash=snapshot.snapshot.snapshot_hash,
            retrieval_policy_hash=retriever_policy.policy_hash,
        )
        for template_id in ("rank_wrap", "delay_1", "zscore_wrap")
    )
    source = RetrieverFeatureSourceServiceV1(store, flags=store.flags).record(
        execution_run_id=treatment_run_id,
        snapshot_event_hash=snapshot.event.event_hash,
        action_event_hashes=tuple(item.event.event_hash for item in actions),
        eligible_event_watermark=first_source.source.eligible_event_watermark,
        retrieval_policy=retriever_policy,
    )
    evidence_service = ActivationEvidenceService(store)
    evidence_service.register_plan(frozen_plan)
    runner = PairedActivationRunner(ActivationArtifactStore(tmp_path / "activation"))
    holder = {}

    def execute(request, scope):
        lifecycle = EventSourcedSearchLifecycle(
            store=store,
            flags=store.flags,
            semantics=_semantics(),
            evaluator=_SkipEvaluator(),
            data_snapshot_hash=snapshot.snapshot.snapshot_hash,
        )
        if request.arm == "control":
            search = AlphaFoundrySearch(
                seed_bank=SeedBank(
                    [AlphaSeed("flat-control", "delay(volume, 11)", "fixture")]
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
            return _manifest(request, snapshot.snapshot.snapshot_hash, result)
        recorded = RetrieverDecisionV6Service(store).record(
            control_evidence_event_hash=holder["control"].event.event_hash,
            feature_source_event_hash=source.event.event_hash,
            seed=request.seed,
            candidate_budget=request.candidate_budget,
            control_run_id=holder["control"].event.run_id,
            run_id=request.execution_run_id,
        )
        generated = ActivationTreatmentGeneratorV3().run(
            request=request,
            scope=scope,
            recorded_decision=recorded,
            parent_seeds=(
                AlphaSeed(target.factor_spec_id, "rank(close)", "fixture"),
            ),
            lifecycle=lifecycle,
        )
        relative = evidence_service.record_generation_consumption_v3(
            generated.evidence
        )
        holder.update(recorded=recorded, generated=generated, relative=relative)
        return _manifest(
            request, snapshot.snapshot.snapshot_hash, generated.search_result
        )

    runner.run_pair(
        runner.register(frozen_plan),
        run_group_id="group-00",
        mechanism_family="momentum",
        dag_region="leaf",
        executor=execute,
    )
    return store, source, holder


def test_v3_consumes_v6_source_and_removes_only_verified_feature_failure(
    tmp_path: Path,
) -> None:
    store, source, holder = _run(tmp_path)
    recorded = holder["recorded"]
    evidence = holder["generated"].evidence
    assert evidence.base.selected_action_ids == recorded.decision.selected_action_ids
    assert evidence.base.consumed_action_ids == evidence.base.selected_action_ids
    assert evidence.feature_source_event_hash == source.event.event_hash
    assert evidence.feature_source_hash == source.source.source_hash
    assert evidence.feature_snapshot_hash == source.source.snapshot_hash
    assert evidence.feature_scorecard_event_hashes == source.source.scorecard_event_hashes
    assert "RETRIEVER_FEATURE_SOURCE_UNVERIFIED" not in (
        evidence.base.source_failure_codes
    )
    assert evidence.base.source_failure_codes == ()
    assert evidence.base.source_complete is True
    assert len(
        store.query_events(
            event_type="ActivationGenerationConsumptionV3Recorded"
        )
    ) == 1
    assert store.verify_chain()


def test_v3_reader_rejects_feature_source_substitution(tmp_path: Path) -> None:
    store, _, holder = _run(tmp_path)
    event = store.query_events(
        event_type="ActivationGenerationConsumptionV3Recorded"
    )[0]
    evidence = holder["generated"].evidence
    artifact_store = ActivationArtifactStore(store.artifact_root)
    forged = evidence.to_dict()
    forged["feature_source_hash"] = "sha256:" + "f" * 64
    forged["evidence_hash"] = canonical_json_hash(
        forged, exclude_keys=("evidence_hash",)
    )
    relative = artifact_store.put("generation_consumption_v3", forged)
    forged_id = (
        "activation-generation-v3-"
        + forged["evidence_hash"].removeprefix("sha256:")[:24]
    )
    payload = event.to_dict()["payload"]
    payload.update(
        generation_id=forged_id,
        feature_source_hash=forged["feature_source_hash"],
        evidence_hash=forged["evidence_hash"],
        artifact_refs=[
            {
                "relative_path": relative,
                "artifact_hash": hash_artifact(
                    store.artifact_root.joinpath(*relative.split("/"))
                ),
                "media_type": (
                    "application/vnd.vibe.activation-generation-consumption-v3+json"
                ),
            }
        ],
    )
    with pytest.raises(EventValidationError, match="evidence is invalid"):
        store.append_event(
            EventDraft(
                event_type="ActivationGenerationConsumptionV3Recorded",
                entity_id=forged_id,
                run_id=evidence.base.execution_run_id,
                payload_schema_version="activation_generation_consumption_recorded.v3",
                payload=payload,
                idempotency_key="activation-generation-v3:forged-source",
            )
        )


def test_v3_service_rejects_v2_evidence(tmp_path: Path) -> None:
    store, _, holder = _run(tmp_path)
    with pytest.raises(TypeError, match="source-bound"):
        ActivationEvidenceService(store).record_generation_consumption_v3(
            holder["generated"].evidence.base
        )
