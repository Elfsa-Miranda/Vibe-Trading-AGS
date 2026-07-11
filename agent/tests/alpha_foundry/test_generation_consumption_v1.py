from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src.alpha_foundry.activation import (
    ActivationArtifactStore,
    ActivationEvidenceService,
    ActivationExperimentPlan,
    ActivationRunManifest,
    PairedActivationRunner,
)
from src.alpha_foundry.activation.generation_consumption_v1 import (
    ActivationTreatmentGeneratorV1,
)
from src.alpha_foundry.control_evidence import (
    FlatControlPolicyV1,
    OfficialSearchControlServiceV1,
)
from src.alpha_foundry.dsl.identity import build_expression_identity
from src.alpha_foundry.mutators import SeedMutator
from src.alpha_foundry.retrieval.service_v4 import RetrieverDecisionV4Service
from src.alpha_foundry.search import AlphaFoundrySearch
from src.alpha_foundry.search_lifecycle import (
    EventSourcedSearchLifecycle,
    SearchEvaluationOutcome,
)
from src.alpha_foundry.seed_bank import AlphaSeed, SeedBank
from src.research_ledger.events import EventDraft, EventValidationError
from src.research_ledger.hash_utils import canonical_json_hash
from test_activation_retriever import plan
from test_retriever_shadow import _candidate, _views
from test_search_lifecycle import _semantics


class _SkipEvaluator:
    def evaluate(self, **kwargs):
        return SearchEvaluationOutcome(
            status="skip",
            decision="none",
            reason_codes=("GENERATION_CONSUMPTION_FIXTURE",),
        )


def _manifest(request, snapshot_hash, result) -> ActivationRunManifest:
    counts = dict(result.terminal_status_counts)
    return ActivationRunManifest.create(
        plan_hash=request.plan_hash,
        pair_id=request.pair_id,
        run_group_id=request.run_group_id,
        arm=request.arm,
        seed=request.seed,
        data_snapshot_hash=snapshot_hash,
        mechanism_family=request.mechanism_family,
        dag_region=request.dag_region,
        policy_hash=request.policy_hash,
        rng_namespace=request.rng_namespace,
        cache_namespace=request.cache_namespace,
        candidate_budget=request.candidate_budget,
        compute_budget=request.compute_budget,
        terminal_status_counts={
            name: counts.get(name, 0)
            for name in (
                "success", "reject", "skip", "invalid", "duplicate",
                "timeout", "error", "infrastructure_failure",
            )
        },
        candidate_ids=tuple(item.candidate_id for item in result.candidates),
        effective_candidate_ids=(),
        metrics={
            "duplicate_rate": 0.0,
            "failure_rate": 0.0,
            "propensity_unexplained_fraction": 0.0,
            "coverage_coverage_collapse": 0.0,
            "resource_wall_seconds": 0.0,
        },
        started_at="2026-07-11T00:00:00Z",
        ended_at="2026-07-11T00:00:01Z",
        complete=True,
    )


def test_treatment_generator_consumes_parent_but_fails_closed_on_v4_inputs(
    tmp_path: Path,
) -> None:
    store, query, discovery = _views(tmp_path)
    candidate = _candidate(query, discovery)
    control_policy = FlatControlPolicyV1.create(
        max_candidates_per_seed=3,
        max_candidates=3,
        trial_budget=3,
    )
    base = plan()
    frozen = ActivationExperimentPlan.create(
        experiment_id="generation-consumption-fixture",
        phase="confirmatory",
        registered_at=base.registered_at,
        provenance=replace(
            base.provenance,
            control_policy_hash=control_policy.policy_hash,
            treatment_policy_hash=RetrieverDecisionV4Service(store).policy.policy_hash,
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
    evidence_service.register_plan(frozen)
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

        recorded = RetrieverDecisionV4Service(store).record(
            control_evidence_event_hash=holder["control"].event.event_hash,
            candidates=(candidate,),
            data_snapshot_hash=discovery.data_snapshot_hash,
            eligible_event_watermark=discovery.source_watermark,
            seed=request.seed,
            candidate_budget=1,
            control_run_id=holder["control"].event.run_id,
            run_id=request.execution_run_id,
        )
        generated = ActivationTreatmentGeneratorV1().run(
            request=request,
            scope=scope,
            recorded_decision=recorded,
            parent_seeds=(
                AlphaSeed(candidate.factor_spec_id, "zscore(rank(open))", "fixture"),
            ),
            lifecycle=lifecycle,
        )
        holder.update(
            request=request,
            scope=scope,
            recorded=recorded,
            lifecycle=lifecycle,
            generated=generated,
            generation_relative=(
                evidence_service.record_generation_consumption(generated.evidence)
            ),
        )
        return _manifest(
            request, discovery.data_snapshot_hash, generated.search_result
        )

    runner.run_pair(
        runner.register(frozen),
        run_group_id="group-00",
        mechanism_family="momentum",
        dag_region="leaf",
        executor=execute,
    )
    evidence = holder["generated"].evidence
    assert evidence.source_complete is False
    assert evidence.source_failure_codes == (
        "RETRIEVER_ACTION_SOURCE_UNVERIFIED",
        "RETRIEVER_FEATURE_SOURCE_UNVERIFIED",
    )
    assert evidence.selected_parent_factor_spec_ids == (candidate.factor_spec_id,)
    assert evidence.consumed_parent_factor_spec_ids == (candidate.factor_spec_id,)
    assert len(evidence.generated_candidates) == 3
    assert {
        item["parent_factor_spec_id"] for item in evidence.generated_candidates
    } == {candidate.factor_spec_id}
    assert all(
        event.run_id == holder["request"].execution_run_id
        for event in store.query_events(event_type="TrialTerminated")
        if event.event_hash
        in {item["terminal_event_hash"] for item in evidence.generated_candidates}
    )
    events = store.query_events(
        event_type="ActivationGenerationConsumptionRecorded"
    )
    assert len(events) == 1
    assert events[0].run_id == holder["request"].execution_run_id
    assert events[0].payload["evidence_hash"] == evidence.evidence_hash
    assert store.verify_chain()

    with pytest.raises(ValueError, match="canonical AST"):
        ActivationTreatmentGeneratorV1().run(
            request=holder["request"],
            scope=holder["scope"],
            recorded_decision=holder["recorded"],
            parent_seeds=(
                AlphaSeed(candidate.factor_spec_id, "rank(close)", "forged"),
            ),
            lifecycle=holder["lifecycle"],
        )

    forged = evidence.to_dict()
    forged["selected_parents"][0]["formula"] = "rank(close)"
    forged["selected_parents"][0]["expression_id"] = build_expression_identity(
        "rank(close)"
    ).expression_id
    forged["evidence_hash"] = canonical_json_hash(
        forged, exclude_keys=("evidence_hash",)
    )
    artifact_store = ActivationArtifactStore(store.artifact_root)
    forged_relative = artifact_store.put("generation_consumption", forged)
    forged_id = (
        "activation-generation-v1-"
        + forged["evidence_hash"].removeprefix("sha256:")[:24]
    )
    original_payload = events[0].to_dict()["payload"]
    original_payload.update(
        generation_id=forged_id,
        evidence_hash=forged["evidence_hash"],
        artifact_refs=[
            evidence_service._reference(  # noqa: SLF001 - adversarial append
                "generation_consumption",
                forged["evidence_hash"],
                forged_relative,
            )
        ],
    )
    original_payload["artifact_refs"][0]["media_type"] = (
        "application/vnd.vibe.activation-generation-consumption-v1+json"
    )
    with pytest.raises(EventValidationError, match="evidence is invalid"):
        store.append_event(
            EventDraft(
                event_type="ActivationGenerationConsumptionRecorded",
                entity_id=forged_id,
                run_id=evidence.execution_run_id,
                payload_schema_version=(
                    "activation_generation_consumption_recorded.v1"
                ),
                payload=original_payload,
                idempotency_key="activation-generation-v1:forged-parent",
            )
        )
