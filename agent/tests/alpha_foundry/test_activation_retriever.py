from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import random

import pytest

from src.alpha_foundry.activation import (
    ActivationAnalysisPolicy,
    ActivationAnalyzer,
    ActivationArtifactStore,
    ActivationCompatibility,
    ActivationDesign,
    ActivationExperimentPlan,
    ActivationProvenance,
    ActivationRunManifest,
    ActiveRetrieverCapability,
    ActiveRetrieverResolver,
    ActivationEvidenceService,
    PairedActivationRunner,
    RetrieverActivationPolicy,
    RetrieverModeResolution,
    activation_arm_execution_run_id,
    holm_adjust,
)
from src.alpha_quality.flags import AGS_FLAG_DEFAULTS, ResolvedAGSFlags
from src.research_ledger.hash_utils import canonical_json_hash
from src.research_ledger.events import EventDraft, EventTransitionError, ResearchEventStore


def h(value: str) -> str:
    return canonical_json_hash({"value": value})


def plan(*, groups: int = 8, phase: str = "confirmatory") -> ActivationExperimentPlan:
    policy = RetrieverActivationPolicy()
    ids = tuple(f"group-{index:02d}" for index in range(groups))
    provenance = ActivationProvenance(
        code_version="test",
        code_hash=h("code"),
        feature_flags={name: name == "VIBE_TRADING_AGS_ENABLED" for name in AGS_FLAG_DEFAULTS},
        runtime_manifest_hash=h("runtime"),
        generator_version="generator.v1",
        generator_hash=h("generator"),
        grammar_version="grammar.v1",
        grammar_hash=h("grammar"),
        control_policy_hash=h("control"),
        treatment_policy_hash=h("treatment"),
        discovery_projection_hash=h("projection"),
        discovery_watermark=h("watermark"),
        eligible_event_chain_head=h("chain"),
        train_snapshot_hash=h("train"),
        valid_snapshot_hash=h("valid"),
        universe_hash=h("universe"),
        market_hash=h("market"),
        calendar_hash=h("calendar"),
        period_hash=h("period"),
        regime_hash=h("regime"),
        candidate_definition_hash=h("candidate"),
        deduplication_hash=h("dedup"),
        decision_criteria_hash=h("criteria"),
    )
    design = ActivationDesign(
        candidate_budget=4,
        compute_budget=8,
        worker_limit=1,
        timeout_seconds=30.0,
        seeds=tuple(range(100, 100 + groups)),
        run_group_ids=ids,
        pilot_excluded_run_group_ids=("pilot-00",),
        mechanism_families=("momentum",),
        dag_regions=("leaf",),
        pairing_keys=("dag_region", "mechanism_family", "run_group_id", "seed"),
        independent_run_group_definition="one frozen seed and snapshot is one cluster",
        rng_namespace_rule="plan/run/arm/rng",
        cache_namespace_rule="plan/run/arm/cache",
    )
    analysis = ActivationAnalysisPolicy(
        primary_estimand="effective_non_duplicate_candidate_yield",
        primary_threshold=0.25,
        primary_threshold_unit="candidates_per_fixed_budget",
        secondary_estimands=("duplicate_rate", "failure_rate"),
        noninferiority_margins={"duplicate_rate": 0.1, "failure_rate": 0.1},
        confidence_level=0.95,
        bootstrap_method="paired_cluster_percentile.v1",
        cluster_unit="independent_run_group",
        bootstrap_resamples=300,
        bootstrap_seed=991,
        multiple_testing_family=("duplicate_rate", "failure_rate"),
        multiple_testing_method="holm.v1",
        minimum_effective_pairs=2,
        fixed_stopping_rule="analyze exactly the frozen run-group set once",
        missing_pair_rule="invalidate",
        trial_failure_rule="count_in_arm",
        infrastructure_failure_rule="count_as_failure",
        target_power=0.8,
        assumed_cluster_standard_deviation=1.0,
        minimum_detectable_effect=1.0,
        required_independent_groups=groups,
        maximum_ci_width=4.0,
    )
    return ActivationExperimentPlan.create(
        experiment_id="activation-test",
        phase=phase,  # type: ignore[arg-type]
        registered_at="2026-07-11T00:00:00Z",
        provenance=provenance,
        design=design,
        analysis=analysis,
        readiness_requirements=("NO_FINAL_FORWARD_ACCESS", "SOURCE_BOUND_EVIDENCE"),
        decision_policy_hash=policy.policy_hash,
        truth_table_hash=policy.truth_table_hash,
    )


def manifest(
    frozen: ActivationExperimentPlan,
    group: str,
    arm: str,
    *,
    gain: int = 1,
    complete: bool = True,
    contaminated: bool = False,
) -> ActivationRunManifest:
    index = frozen.design.run_group_ids.index(group)
    effective = 2 + (gain if arm == "treatment" else 0)
    candidate_ids = tuple(f"{group}-{arm}-{i}" for i in range(4))
    counts = {
        "success": effective,
        "reject": 0,
        "skip": 0,
        "invalid": 0,
        "duplicate": 4 - effective,
        "timeout": 0,
        "error": 0,
        "infrastructure_failure": 0,
    }
    return ActivationRunManifest.create(
        plan_hash=frozen.plan_hash,
        pair_id=f"{group}:momentum:leaf",
        run_group_id=group,
        arm=arm,  # type: ignore[arg-type]
        seed=frozen.design.seeds[index],
        data_snapshot_hash=frozen.provenance.train_snapshot_hash,
        mechanism_family="momentum",
        dag_region="leaf",
        policy_hash=(
            frozen.provenance.control_policy_hash
            if arm == "control"
            else frozen.provenance.treatment_policy_hash
        ),
        rng_namespace=f"{frozen.plan_hash}:{group}:{arm}:rng",
        cache_namespace=f"{frozen.plan_hash}:{group}:{arm}:cache",
        candidate_budget=4,
        compute_budget=8,
        terminal_status_counts=counts,
        candidate_ids=candidate_ids,
        effective_candidate_ids=candidate_ids[:effective],
        metrics={
            "duplicate_rate": (4 - effective) / 4,
            "failure_rate": 0.0,
            "propensity_unexplained_fraction": 0.0,
            "coverage_coverage_collapse": 0.0,
            "resource_wall_seconds": 1.0,
        },
        started_at="2026-07-11T00:00:00Z",
        ended_at="2026-07-11T00:00:01Z",
        complete=complete,
        contaminated=contaminated,
        contamination_reasons=("FINAL_TEST_TAINT",) if contaminated else (),
    )


def all_pairs(frozen: ActivationExperimentPlan, *, gain: int = 1):
    return tuple(
        item
        for group in frozen.design.run_group_ids
        for item in (
            manifest(frozen, group, "control", gain=gain),
            manifest(frozen, group, "treatment", gain=gain),
        )
    )


def enabled_flags(*, active: bool) -> ResolvedAGSFlags:
    values = {name: False for name in AGS_FLAG_DEFAULTS}
    for name in (
        "VIBE_TRADING_AGS_ENABLED",
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ):
        values[name] = True
    values["VIBE_TRADING_TOPOLOGY_RETRIEVER_ACTIVE"] = active
    return ResolvedAGSFlags(values)


def event_store(tmp_path: Path) -> ResearchEventStore:
    return ResearchEventStore(
        tmp_path / "activation.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=enabled_flags(active=False),
        code_version="activation-test",
    )


def test_plan_hash_covers_preregistered_fields() -> None:
    frozen = plan()
    changed = replace(frozen.analysis, primary_threshold=0.5)
    other = ActivationExperimentPlan.create(
        experiment_id=frozen.experiment_id,
        phase=frozen.phase,
        registered_at=frozen.registered_at,
        provenance=frozen.provenance,
        design=frozen.design,
        analysis=changed,
        readiness_requirements=frozen.readiness_requirements,
        decision_policy_hash=frozen.decision_policy_hash,
        truth_table_hash=frozen.truth_table_hash,
    )
    assert other.plan_hash != frozen.plan_hash


def test_formal_outcomes_require_registered_plan(tmp_path: Path) -> None:
    runner = PairedActivationRunner(ActivationArtifactStore(tmp_path))
    with pytest.raises((TypeError, AttributeError)):
        runner.run_pair(plan(), run_group_id="group-00", mechanism_family="momentum", dag_region="leaf", executor=lambda *_: None)  # type: ignore[arg-type,return-value]


def test_runner_freezes_pair_and_isolates_namespaces(tmp_path: Path) -> None:
    frozen = plan()
    runner = PairedActivationRunner(ActivationArtifactStore(tmp_path))
    registered = runner.register(frozen)

    requests = []

    def execute(request, scope):
        assert scope.discovery_chain_head == frozen.provenance.eligible_event_chain_head
        requests.append(request)
        return manifest(frozen, request.run_group_id, request.arm)

    control, treatment = runner.run_pair(
        registered,
        run_group_id="group-00",
        mechanism_family="momentum",
        dag_region="leaf",
        executor=execute,
    )
    assert control.seed == treatment.seed
    assert control.rng_namespace != treatment.rng_namespace
    assert control.cache_namespace != treatment.cache_namespace
    execution_run_ids = [request.execution_run_id for request in requests]
    assert len(set(execution_run_ids)) == 2
    assert execution_run_ids == [
        activation_arm_execution_run_id(
            plan_hash=frozen.plan_hash,
            run_group_id="group-00",
            arm="control",
        ),
        activation_arm_execution_run_id(
            plan_hash=frozen.plan_hash,
            run_group_id="group-00",
            arm="treatment",
        ),
    ]
    with pytest.raises(ValueError, match="not runner-derived"):
        replace(requests[0], execution_run_id="activation-arm-forged")
    assert sum(control.terminal_status_counts.values()) == len(control.candidate_ids)


def test_missing_or_failed_treatment_pair_is_not_silently_dropped() -> None:
    frozen = plan()
    observed = list(all_pairs(frozen))
    observed.pop()
    result = ActivationAnalyzer().analyze(frozen, observed)
    assert "MISSING_PAIR_INVALIDATES_ANALYSIS" in result.invalidation_reasons
    assert not result.replayable


def test_candidate_siblings_are_not_independent_clusters() -> None:
    frozen = plan(groups=8)
    result = ActivationAnalyzer().analyze(frozen, all_pairs(frozen))
    assert result.effective_sample == 8
    assert result.effective_sample != sum(len(item.candidate_ids) for item in all_pairs(frozen))


def test_one_run_group_cannot_be_reused_as_two_independent_pairs() -> None:
    frozen = plan()
    observed = list(all_pairs(frozen))
    duplicate_control = replace(
        observed[0],
        pair_id="duplicate-pair",
        manifest_hash=canonical_json_hash(
            {**observed[0]._content_dict(), "pair_id": "duplicate-pair"}
        ),
    )
    duplicate_treatment = replace(
        observed[1],
        pair_id="duplicate-pair",
        manifest_hash=canonical_json_hash(
            {**observed[1]._content_dict(), "pair_id": "duplicate-pair"}
        ),
    )
    result = ActivationAnalyzer().analyze(
        frozen, [*observed, duplicate_control, duplicate_treatment]
    )
    assert "RUN_GROUP_REUSED_AS_INDEPENDENT_PAIR" in result.invalidation_reasons


def test_seeded_paired_bootstrap_is_reproducible() -> None:
    frozen = plan()
    first = ActivationAnalyzer().analyze(frozen, all_pairs(frozen))
    second = ActivationAnalyzer().analyze(frozen, all_pairs(frozen))
    assert first.result_hash == second.result_hash
    assert first.primary_effect == second.primary_effect


def test_holm_matches_reference_fixture() -> None:
    assert holm_adjust({"a": 0.01, "b": 0.04, "c": 0.03}) == {
        "a": 0.03, "c": 0.06, "b": 0.06,
    }


def test_non_significance_does_not_establish_noninferiority() -> None:
    frozen = plan()
    manifests = list(all_pairs(frozen))
    for index, item in enumerate(manifests):
        metrics = dict(item.metrics)
        metrics["failure_rate"] = 0.3 if item.arm == "treatment" else 0.0
        fields = {
            key: (tuple(value) if key in {"candidate_ids", "effective_candidate_ids", "contamination_reasons"} else value)
            for key, value in item._content_dict().items()
            if key not in {"schema_version", "metrics"}
        }
        manifests[index] = ActivationRunManifest.create(
            **fields,
            metrics=metrics,
        )
    result = ActivationAnalyzer().analyze(frozen, manifests)
    assert result.noninferiority_results["failure_rate"] is False
    assert RetrieverActivationPolicy().decide(frozen, result).verdict == "rejected"


def test_underpowered_result_is_inconclusive() -> None:
    frozen = plan(groups=8)
    result = ActivationAnalyzer().analyze(frozen, all_pairs(frozen)[:4])
    assert RetrieverActivationPolicy().decide(frozen, result).verdict != "approved"


def test_final_or_forward_taint_invalidates() -> None:
    frozen = plan()
    observed = list(all_pairs(frozen))
    observed[1] = manifest(frozen, "group-00", "treatment", contaminated=True)
    result = ActivationAnalyzer().analyze(frozen, observed)
    decision = RetrieverActivationPolicy().decide(frozen, result)
    assert decision.verdict == "invalidated"


def test_decision_is_rebuilt_and_cannot_be_caller_supplied() -> None:
    frozen = plan()
    result = ActivationAnalyzer().analyze(frozen, all_pairs(frozen))
    policy = RetrieverActivationPolicy()
    decision = policy.decide(frozen, result)
    assert decision.verdict == "approved"
    with pytest.raises(ValueError):
        replace(decision, verdict="approved", reasons=("CALLER_APPROVED",))


def test_active_flag_without_approved_artifacts_falls_back_to_shadow(tmp_path: Path) -> None:
    resolver = ActiveRetrieverResolver(ActivationArtifactStore(tmp_path))
    frozen = plan()
    compatibility = ActivationCompatibility(
        code_hash=frozen.provenance.code_hash,
        generator_hash=frozen.provenance.generator_hash,
        grammar_hash=frozen.provenance.grammar_hash,
        treatment_policy_hash=frozen.provenance.treatment_policy_hash,
        decision_policy_hash=frozen.decision_policy_hash,
        train_snapshot_hash=frozen.provenance.train_snapshot_hash,
        valid_snapshot_hash=frozen.provenance.valid_snapshot_hash,
    )
    resolution = resolver.resolve(
        flags=enabled_flags(active=True), plan_hash=None, result_hash=None,
        decision_hash=None, compatibility=compatibility,
    )
    assert resolution.mode == "shadow"


def test_synthetic_approved_resolution_cannot_inject_caller_topology_ids() -> None:
    capability = ActiveRetrieverCapability(
        RetrieverModeResolution(
            "active_research_only",
            "APPROVED_COMPATIBLE",
            h("synthetic-decision"),
        )
    )
    with pytest.raises(RuntimeError, match="generator-consumption evidence"):
        capability.choose(
            flat_candidate_ids=("flat-official",),
            topology_candidate_ids=("caller-arbitrary-factor",),
        )


def test_v1_self_reported_approval_cannot_enable_research_mode(tmp_path: Path) -> None:
    frozen = plan()
    ledger = event_store(tmp_path)
    service = ActivationEvidenceService(ledger)
    service.register_plan(frozen)
    for item in all_pairs(frozen):
        service.record_run(item)
    result, decision = service.finalize(frozen, all_pairs(frozen))
    artifacts = service.artifacts
    compatibility = ActivationCompatibility(
        code_hash=frozen.provenance.code_hash,
        generator_hash=frozen.provenance.generator_hash,
        grammar_hash=frozen.provenance.grammar_hash,
        treatment_policy_hash=frozen.provenance.treatment_policy_hash,
        decision_policy_hash=frozen.decision_policy_hash,
        train_snapshot_hash=frozen.provenance.train_snapshot_hash,
        valid_snapshot_hash=frozen.provenance.valid_snapshot_hash,
    )
    resolver = ActiveRetrieverResolver(artifacts, event_store=ledger)
    resolution = resolver.resolve(
        flags=enabled_flags(active=True), plan_hash=frozen.plan_hash,
        result_hash=result.result_hash, decision_hash=decision.decision_hash,
        compatibility=compatibility,
    )
    assert resolution.mode == "shadow"
    assert resolution.reason == "SOURCE_BOUND_ACTIVATION_V2_REQUIRED"
    with pytest.raises(TypeError, match="approved compatible evidence"):
        ActiveRetrieverCapability(resolution)
    mismatch = replace(compatibility, code_hash=h("other-code"))
    assert resolver.resolve(
        flags=enabled_flags(active=True), plan_hash=frozen.plan_hash,
        result_hash=result.result_hash, decision_hash=decision.decision_hash,
        compatibility=mismatch,
    ).mode == "shadow"


def test_rehashed_v2_labels_without_run_source_events_cannot_activate(
    tmp_path: Path,
) -> None:
    frozen = plan()
    ledger = event_store(tmp_path)
    service = ActivationEvidenceService(ledger)
    service.register_plan(frozen)
    observed = all_pairs(frozen)
    for item in observed:
        service.record_run(item)

    legacy_result = ActivationAnalyzer().analyze(frozen, observed)
    result_payload = legacy_result.to_dict()
    result_payload["schema_version"] = "activation_experiment_result.v2"
    result_payload["run_provenance_schema_version"] = "activation_run_source.v2"
    result_payload["result_hash"] = canonical_json_hash(
        result_payload, exclude_keys=("result_hash",)
    )
    result_relative = service.artifacts.put("result", result_payload)
    result_id = (
        "activation-result-"
        + result_payload["result_hash"].removeprefix("sha256:")[:24]
    )
    ledger.append_event(
        EventDraft(
            event_type="ActivationResultRecorded",
            entity_id=result_id,
            run_id=frozen.experiment_id,
            payload_schema_version="activation_result_recorded.v1",
            idempotency_key="activation-result:" + result_payload["result_hash"],
            payload={
                "result_id": result_id,
                "plan_hash": frozen.plan_hash,
                "result_hash": result_payload["result_hash"],
                "complete_pairs": result_payload["complete_pairs"],
                "invalidation_reasons": result_payload["invalidation_reasons"],
                "replayable": result_payload["replayable"],
                "artifact_refs": [
                    service._reference(  # noqa: SLF001 - adversarial raw append
                        "result", result_payload["result_hash"], result_relative
                    )
                ],
            },
        )
    )

    legacy_decision = RetrieverActivationPolicy().decide(frozen, legacy_result)
    decision_payload = legacy_decision.to_dict()
    decision_payload["schema_version"] = "retriever_activation_decision.v2"
    decision_payload["result_hash"] = result_payload["result_hash"]
    decision_payload["decision_hash"] = canonical_json_hash(
        decision_payload, exclude_keys=("decision_hash",)
    )
    decision_relative = service.artifacts.put("decision", decision_payload)
    decision_id = (
        "activation-decision-"
        + decision_payload["decision_hash"].removeprefix("sha256:")[:24]
    )
    ledger.append_event(
        EventDraft(
            event_type="RetrieverActivationDecisionRecorded",
            entity_id=decision_id,
            run_id=frozen.experiment_id,
            payload_schema_version="retriever_activation_decision_recorded.v1",
            idempotency_key=(
                "activation-decision:" + decision_payload["decision_hash"]
            ),
            payload={
                "activation_decision_id": decision_id,
                "plan_hash": frozen.plan_hash,
                "result_hash": result_payload["result_hash"],
                "decision_hash": decision_payload["decision_hash"],
                "policy_hash": decision_payload["policy_hash"],
                "verdict": decision_payload["verdict"],
                "reasons": decision_payload["reasons"],
                "active_research_only": decision_payload["active_research_only"],
                "artifact_refs": [
                    service._reference(  # noqa: SLF001 - adversarial raw append
                        "decision",
                        decision_payload["decision_hash"],
                        decision_relative,
                    )
                ],
            },
        )
    )
    compatibility = ActivationCompatibility(
        code_hash=frozen.provenance.code_hash,
        generator_hash=frozen.provenance.generator_hash,
        grammar_hash=frozen.provenance.grammar_hash,
        treatment_policy_hash=frozen.provenance.treatment_policy_hash,
        decision_policy_hash=frozen.decision_policy_hash,
        train_snapshot_hash=frozen.provenance.train_snapshot_hash,
        valid_snapshot_hash=frozen.provenance.valid_snapshot_hash,
    )
    resolution = ActiveRetrieverResolver(
        service.artifacts, event_store=ledger
    ).resolve(
        flags=enabled_flags(active=True),
        plan_hash=frozen.plan_hash,
        result_hash=result_payload["result_hash"],
        decision_hash=decision_payload["decision_hash"],
        compatibility=compatibility,
    )
    assert resolution.mode == "shadow"
    assert resolution.reason == "ACTIVATION_RUN_SOURCE_EVIDENCE_MISSING"


def test_artifacts_are_strict_content_addressed_and_reject_secrets(tmp_path: Path) -> None:
    frozen = plan()
    store = ActivationArtifactStore(tmp_path)
    relative = store.put("plan", frozen.to_dict())
    assert frozen.plan_hash.removeprefix("sha256:") in relative
    assert store.get("plan", frozen.plan_hash) == frozen.to_dict()
    unsafe = frozen.to_dict()
    unsafe["limitations"] = ["api_key=secret-value"]
    unsafe["plan_hash"] = canonical_json_hash(unsafe, exclude_keys=("plan_hash",))
    with pytest.raises(ValueError):
        store.put("plan", unsafe)


def test_preflight_blockers_produce_invalidated_not_approved() -> None:
    frozen = plan()
    result = ActivationAnalyzer.invalidated_result(
        frozen,
        reasons=(
            "REAL_TRAIN_VALID_SNAPSHOT_UNAVAILABLE",
            "RETRIEVER_DECISION_SOURCE_AUTHORITY_UNPROVEN",
        ),
    )
    decision = RetrieverActivationPolicy().decide(frozen, result)
    assert decision.verdict == "invalidated"
    assert not decision.active_research_only


def test_preflight_invalidation_is_append_only_and_replayable(tmp_path: Path) -> None:
    frozen = plan()
    store = event_store(tmp_path)
    service = ActivationEvidenceService(store)
    service.register_plan(frozen)
    result, decision = service.invalidate_preflight(
        frozen,
        reasons=("REAL_TRAIN_VALID_SNAPSHOT_UNAVAILABLE",),
    )
    assert result.replayable is False
    assert decision.verdict == "invalidated"
    assert [event.event_type for event in store.query_events()] == [
        "ActivationPlanRegistered",
        "ActivationResultRecorded",
        "RetrieverActivationDecisionRecorded",
    ]
    assert store.verify_chain()
    assert store.replay().event_count == 3


def test_null_simulation_does_not_false_activate() -> None:
    frozen = plan()
    approvals = 0
    for simulation_seed in range(100):
        rng = random.Random(simulation_seed)
        observed = tuple(
            item
            for group in frozen.design.run_group_ids
            for item in (
                manifest(frozen, group, "control"),
                manifest(frozen, group, "treatment", gain=rng.choice((-1, 0, 1))),
            )
        )
        result = ActivationAnalyzer().analyze(frozen, observed)
        approvals += RetrieverActivationPolicy().decide(frozen, result).verdict == "approved"
    assert approvals <= 5


def test_result_before_plan_and_approval_over_invalid_result_are_rejected(tmp_path: Path) -> None:
    frozen = plan()
    store = event_store(tmp_path)
    result = ActivationAnalyzer.invalidated_result(
        frozen, reasons=("REAL_TRAIN_VALID_SNAPSHOT_UNAVAILABLE",)
    )
    with pytest.raises(EventTransitionError):
        store.append_event(
            EventDraft(
                event_type="ActivationResultRecorded",
                entity_id="result-before-plan",
                run_id="activation-test",
                payload_schema_version="activation_result_recorded.v1",
                payload={
                    "result_id": "result-before-plan",
                    "plan_hash": frozen.plan_hash,
                    "result_hash": result.result_hash,
                    "complete_pairs": 0,
                    "invalidation_reasons": list(result.invalidation_reasons),
                    "replayable": False,
                    "artifact_refs": [],
                },
            )
        )
    service = ActivationEvidenceService(store)
    service.register_plan(frozen)
    service.invalidate_preflight(
        frozen, reasons=("REAL_TRAIN_VALID_SNAPSHOT_UNAVAILABLE",)
    )
    assert store.verify_chain()
