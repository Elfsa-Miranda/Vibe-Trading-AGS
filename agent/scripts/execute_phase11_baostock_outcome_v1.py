"""Execute the frozen research-only BaoStock Phase 11 outcome run.

This is an orchestration entry point over existing production components.  It
does not define a new evaluator, Retriever, decision, event, or provider.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
import multiprocessing as mp
from pathlib import Path
import random
import shutil
import sqlite3
import statistics
import time
import traceback
from typing import Any, Mapping

import psutil  # type: ignore[import-untyped]

from src.alpha_foundry.activation import (
    ActivationAnalysisPolicy,
    ActivationDesign,
    ActivationEvidenceService,
    ActivationExperimentPlan,
    ActivationProvenance,
)
from src.alpha_foundry.activation.candidate_factory_v1 import (
    ProductionActivationCandidateFactoryV1,
    ProductionActivationCandidateRefsV1,
    ProductionActivationFactoryArmRequestV1,
)
from src.alpha_foundry.activation.formal_protocol_v3 import (
    FormalActivationStagePlanV3,
    freeze_pair_schedules_v3,
)
from src.alpha_foundry.activation.generation_consumption_v4 import (
    ActivationTreatmentGeneratorV4,
)
from src.alpha_foundry.activation.runner import (
    ActivationArmRequest,
    activation_arm_execution_run_id,
)
from src.alpha_foundry.candidate_pool import CandidateExpression, make_candidate
from src.alpha_foundry.control_evidence import FlatControlPolicyV1
from src.alpha_foundry.activation.policy import RetrieverActivationPolicy
from src.alpha_foundry.dsl.identity import FactorSpecSemantics
from src.alpha_foundry.flat_schedule_v1 import PreArmFlatScheduleServiceV1
from src.alpha_foundry.mutators import SeedMutator
from src.alpha_foundry.retrieval.action_template_v1 import (
    RetrieverActionTemplateServiceV1,
)
from src.alpha_foundry.retrieval.feature_producer_v1 import (
    RetrieverFeatureSourceServiceV1,
)
from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy
from src.alpha_foundry.retrieval.service_v7 import (
    RecordedRetrieverDecisionV7,
    RetrieverDecisionV7Service,
)
from src.alpha_foundry.search_lifecycle import SearchAttemptResult
from src.alpha_foundry.seed_bank import AlphaSeed, SeedBank
from src.alpha_quality.decision_v2 import DecisionEvidenceRepository
from src.alpha_quality.decision_v2.policy import DecisionV2Policy
from src.alpha_quality.decision_v2.source_v3 import QualityDecisionV3Service
from src.alpha_quality.flags import AGS_FLAG_DEFAULTS, ResolvedAGSFlags
from src.alpha_quality.production_evaluator_v1 import ProductionEvaluationRequestV1
from src.alpha_quality.secondary_evidence_v1 import ComparisonPoolServiceV1
from src.research_ledger.events import ResearchEventStore
from src.research_ledger.events.artifacts import hash_artifact
from src.research_ledger.hash_utils import canonical_json, canonical_json_hash


FAMILY = "ags-v32-phase11-flat-topology-baostock-research-only-v1"
BASELINE_FORMULA = "neg(delta(close,5))"
SEED_FORMULAS = (
    BASELINE_FORMULA,
    "rank(close)",
    "rank(open)",
    "rank(high)",
    "rank(low)",
    "rank(volume)",
    "rank(amount)",
    "delta(close,1)",
)
NONIDENTITY_TEMPLATES = ("rank_wrap", "decay_3", "delay_1", "zscore_wrap")
FEATURE_COMMIT = "d949434a485828a764cf5592b5fe144525e80563"


def _flags() -> ResolvedAGSFlags:
    enabled = {
        "VIBE_TRADING_AGS_ENABLED",
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_ALPHA_SCORECARD",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
        "VIBE_TRADING_DECISION_V2",
    }
    return ResolvedAGSFlags({name: name in enabled for name in AGS_FLAG_DEFAULTS})


def _store(db: Path, artifacts: Path) -> ResearchEventStore:
    return ResearchEventStore(
        db,
        artifact_root=artifacts,
        flags=_flags(),
        code_version=FEATURE_COMMIT,
    )


def _quality(store: ResearchEventStore) -> QualityDecisionV3Service:
    return QualityDecisionV3Service(
        store=store,
        flags=store.flags,
        policy=DecisionV2Policy(
            schema_version="decision_v2_policy.v1",
            policy_version="phase11-baostock-research-only-v1",
        ),
        repository=DecisionEvidenceRepository(
            store.artifact_root / "decision-evidence-v2"
        ),
    )


def _semantics() -> FactorSpecSemantics:
    return FactorSpecSemantics(
        transform_pipeline_hash=canonical_json_hash(
            {"schema_version": "phase11_fixed_transform.v1", "steps": []}
        ),
        field_semantics={
            name: "baostock_daily_pit_best_effort"
            for name in ("open", "high", "low", "close", "volume", "amount")
        },
        signal_time="close_t",
        order_time="open_t_plus_1",
        entry_price_time="open_t_plus_1",
        execution_lag=1,
        return_horizon=1,
        universe_mask_hash=canonical_json_hash(
            {"universe": "A_SHARE_ELIGIBLE_GOLDEN_COHORT_V1"}
        ),
        tradability_mask_hash=canonical_json_hash(
            {"tradability": "frozen_baostock_best_effort_v1"}
        ),
    )


def _event(store: ResearchEventStore, event_hash: str, event_type: str | None = None):
    matches = [
        event
        for event in store.query_events(event_type=event_type)
        if event.event_hash == event_hash
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {event_type or 'event'} {event_hash}")
    return matches[0]


@dataclass
class _FactoryLifecycle:
    factory: ProductionActivationCandidateFactoryV1
    contract_hash: str
    pit_snapshot_event_hash: str
    data_snapshot_hash: str
    semantics: FactorSpecSemantics
    refs: list[ProductionActivationCandidateRefsV1]

    def evaluate_candidate(
        self, candidate: CandidateExpression, *, run_id: str, attempt_index: int
    ) -> SearchAttemptResult:
        trial_id = (
            f"phase11-{run_id}-{attempt_index:02d}-"
            f"{candidate.candidate_id[-12:]}"
        )
        attempt = self.factory.record_generated_identity(
            candidate=candidate,
            semantics=self.semantics,
            trial_id=trial_id,
            run_id=run_id,
        )
        if attempt.status in {"invalid", "duplicate"}:
            refs = self.factory.identity_terminal_refs(
                attempt=attempt, trial_id=trial_id, run_id=run_id
            )
        else:
            definitions = [
                event
                for event in self.factory.store.query_events(
                    event_type="FactorDefinitionRecorded", entity_id=attempt.factor_spec_id
                )
                if event.run_id == run_id
            ]
            if len(definitions) != 1:
                raise RuntimeError("recorded identity omitted its definition event")
            definition_event_hash = definitions[0].event_hash
            pool, _ = ComparisonPoolServiceV1(self.factory.store).freeze(
                run_id=run_id,
                source_watermark_event_hash=definition_event_hash,
                members=(),
            )
            refs = self.factory.evaluate_recorded_candidate(
                request=ProductionEvaluationRequestV1(
                    run_id=run_id,
                    trial_id=trial_id,
                    factor_definition_event_hash=definition_event_hash,
                    resolved_contract_hash=self.contract_hash,
                    snapshot_event_hash=self.pit_snapshot_event_hash,
                    source_watermark_event_hash=definition_event_hash,
                    frozen_comparison_pool_hash=pool.comparison_pool_hash,
                )
            )
        self.refs.append(refs)
        terminal = _event(
            self.factory.store, refs.trial_terminal_event_hash, "TrialTerminated"
        )
        decision = "none"
        if refs.quality_decision_v3_event_hash is not None:
            decision = str(
                _event(
                    self.factory.store,
                    refs.quality_decision_v3_event_hash,
                    "QualityDecisionV3Recorded",
                ).payload["decision"]
            )
        return SearchAttemptResult(
            trial_id=trial_id,
            candidate_id=candidate.candidate_id,
            factor_spec_id=attempt.factor_spec_id,
            status=str(terminal.payload["status"]),  # type: ignore[arg-type]
            decision=decision,  # type: ignore[arg-type]
            reason_codes=tuple(str(item) for item in terminal.payload["reason_codes"]),
            evaluation_event_hash=refs.evaluation_event_hash,
            terminal_event_hash=refs.trial_terminal_event_hash,
            data_snapshot_hash=self.data_snapshot_hash,
        )


@dataclass(frozen=True)
class _Scope:
    train_snapshot_hash: str
    valid_snapshot_hash: str
    discovery_chain_head: str


def _bundle_sources(store: ResearchEventStore, input_event_hash: str) -> dict[str, str]:
    raw = dict(_event(store, input_event_hash, "ResearchOnlyActivationRunInputRegistered").payload["bundle"])
    contract = _event(store, str(raw["resolved_contract_event_hash"]), "ResolvedEvaluationContractRegistered")
    train_events = [
        event
        for event in store.query_events(event_type="TrainValidDataSnapshotFrozen")
        if event.payload["snapshot_hash"] == raw["train_snapshot_hash"]
    ]
    if len(train_events) != 1:
        raise ValueError("research-only bundle lacks one exact train snapshot event")
    return {
        "contract_hash": str(contract.payload["contract_hash"]),
        "pit_snapshot_event_hash": str(raw["pit_snapshot_event_hash"]),
        "train_snapshot_hash": str(raw["train_snapshot_hash"]),
        "valid_snapshot_hash": str(raw["valid_snapshot_hash"]),
        "train_valid_snapshot_event_hash": str(raw["train_valid_snapshot_event_hash"]),
        "retrieval_snapshot_event_hash": train_events[0].event_hash,
        "flat_policy_hash": str(raw["flat_policy_hash"]),
        "topology_policy_hash": str(raw["topology_policy_hash"]),
    }


def _bootstrap(store: ResearchEventStore, input_event_hash: str) -> dict[str, Any]:
    sources = _bundle_sources(store, input_event_hash)
    factory = ProductionActivationCandidateFactoryV1(
        store=store, quality_decision_v3=_quality(store)
    )
    factory.bind(run_id=FAMILY, run_input_bundle_event_hash=input_event_hash)
    lifecycle = _FactoryLifecycle(
        factory=factory,
        contract_hash=sources["contract_hash"],
        pit_snapshot_event_hash=sources["pit_snapshot_event_hash"],
        data_snapshot_hash=sources["train_snapshot_hash"],
        semantics=_semantics(),
        refs=[],
    )
    seeds: list[dict[str, str]] = []
    baseline: dict[str, Any] | None = None
    for index, formula in enumerate(SEED_FORMULAS, start=1):
        candidate = make_candidate("phase11-bootstrap", formula, mutation="identity")
        result = lifecycle.evaluate_candidate(candidate, run_id=FAMILY, attempt_index=index)
        definition = next(
            event
            for event in store.query_events(event_type="FactorDefinitionRecorded")
            if event.run_id == FAMILY and event.entity_id == result.factor_spec_id
        )
        seeds.append(
            {
                "factor_spec_id": str(result.factor_spec_id),
                "formula": formula,
                "definition_event_hash": definition.event_hash,
                "terminal_event_hash": result.terminal_event_hash,
            }
        )
        if index == 1:
            ref = lifecycle.refs[-1]
            baseline = {
                "formula": formula,
                "factor_spec_id": result.factor_spec_id,
                "terminal_event_hash": result.terminal_event_hash,
                "evaluation_event_hash": ref.evaluation_event_hash,
                "quality_decision_v3_event_hash": ref.quality_decision_v3_event_hash,
                "terminal_dossier_event_hash": ref.terminal_dossier_event_hash,
                "status": result.status,
                "decision": result.decision,
            }
    assert baseline is not None
    baseline_dossiers = [
        event
        for event in store.query_events(event_type="TrialTerminalDossierRecorded")
        if event.event_hash == baseline["terminal_dossier_event_hash"]
    ]
    baseline["exactly_one_terminal"] = len(
        [
            event
            for event in store.query_events(event_type="TrialTerminated")
            if event.event_hash == baseline["terminal_event_hash"]
        ]
    ) == 1
    baseline["exactly_one_dossier"] = len(baseline_dossiers) == 1
    baseline["replay_succeeds"] = store.verify_chain()
    baseline["effective_sample_positive"] = baseline["evaluation_event_hash"] is not None
    return {
        "baseline": baseline,
        "seeds": seeds,
        "eligible_event_watermark": seeds[-1]["terminal_event_hash"],
    }


def _compatibility_plan(
    *, stage: str, groups: tuple[str, ...], seeds: tuple[int, ...],
    source: Mapping[str, str], watermark: str,
) -> ActivationExperimentPlan:
    flat = FlatControlPolicyV1.create(
        max_candidates_per_seed=5, max_candidates=32, trial_budget=64
    )
    topology = ActivationRetrieverPolicy()
    hashes = lambda name: canonical_json_hash({"phase11": name, "feature_commit": FEATURE_COMMIT})
    provenance = ActivationProvenance(
        code_version=FEATURE_COMMIT,
        code_hash=hashes("code"),
        feature_flags=_flags().as_dict(),
        runtime_manifest_hash=hashes("runtime"),
        generator_version="alpha_foundry_search.v1",
        generator_hash=hash_artifact(Path(__file__).parents[1] / "src/alpha_foundry/search.py"),
        grammar_version="factor_dsl.v1",
        grammar_hash=hashes("grammar"),
        control_policy_hash=flat.policy_hash,
        treatment_policy_hash=topology.policy_hash,
        discovery_projection_hash=hashes("discovery-projection"),
        discovery_watermark=watermark,
        eligible_event_chain_head=watermark,
        train_snapshot_hash=source["train_snapshot_hash"],
        valid_snapshot_hash=source["valid_snapshot_hash"],
        universe_hash=hashes("A_SHARE_ELIGIBLE_GOLDEN_COHORT_V1"),
        market_hash=hashes("baostock-fixed-vintage"),
        calendar_hash=hashes("SSE_SZSE_BAOSTOCK"),
        period_hash=hashes("frozen-train-valid"),
        regime_hash=hashes("no-regime"),
        candidate_definition_hash=hashes("seed-bank-and-mutator"),
        deduplication_hash=hashes("canonical-factor-identity"),
        decision_criteria_hash=hash_artifact(Path(__file__).parents[1] / "src/alpha_quality/decision_v2/source_v3.py"),
    )
    design = ActivationDesign(
        candidate_budget=32,
        compute_budget=64,
        worker_limit=2,
        timeout_seconds=600.0,
        seeds=seeds,
        run_group_ids=groups,
        pilot_excluded_run_group_ids=(),
        mechanism_families=("global",),
        dag_regions=("all",),
        pairing_keys=("dag_region", "mechanism_family", "run_group_id", "seed"),
        independent_run_group_definition="one frozen seed and snapshot is one cluster",
        rng_namespace_rule="plan/run/arm/rng",
        cache_namespace_rule="plan/run/arm/cache",
    )
    analysis = ActivationAnalysisPolicy(
        primary_estimand="effective_non_duplicate_candidate_yield_at_narrow_quality_decision_v3",
        primary_threshold=0.5,
        primary_threshold_unit="candidates_per_32_attempt_fixed_budget",
        secondary_estimands=("cpu_seconds", "duplicate_rate", "failure_rate", "peak_rss_mb", "wall_seconds"),
        noninferiority_margins={
            "cpu_seconds": 30.0,
            "duplicate_rate": 0.05,
            "failure_rate": 0.05,
            "peak_rss_mb": 128.0,
            "wall_seconds": 30.0,
        },
        confidence_level=0.95,
        bootstrap_method="paired_cluster_percentile.v1",
        cluster_unit="episode_run_group",
        bootstrap_resamples=10000,
        bootstrap_seed=454499,
        multiple_testing_family=("cpu_seconds", "duplicate_rate", "failure_rate", "peak_rss_mb", "wall_seconds"),
        multiple_testing_method="holm.v1",
        minimum_effective_pairs=2,
        fixed_stopping_rule="analyze exactly the frozen run-group set once",
        missing_pair_rule="invalidate",
        trial_failure_rule="count_in_arm",
        infrastructure_failure_rule="count_as_failure",
        target_power=0.8,
        assumed_cluster_standard_deviation=1.0,
        minimum_detectable_effect=0.5,
        required_independent_groups=len(groups),
        maximum_ci_width=4.0,
    )
    return ActivationExperimentPlan.create(
        experiment_id=f"{FAMILY}:{stage}",
        phase="confirmatory",
        registered_at="2026-07-14T00:00:00Z",
        provenance=provenance,
        design=design,
        analysis=analysis,
        readiness_requirements=("NO_FINAL_FORWARD_ACCESS", "RESEARCH_ONLY_CEILING", "SOURCE_BOUND_EVIDENCE"),
        decision_policy_hash=RetrieverActivationPolicy().policy_hash,
        truth_table_hash=RetrieverActivationPolicy().truth_table_hash,
        limitations=("BAOSTOCK_BEST_EFFORT_AUTHORITY", "RESEARCH_ONLY_EMPIRICAL_ACTIVATION"),
    )


def _formal_plan(
    *, stage: str, parent: str | None, groups: tuple[str, ...], seeds: tuple[int, ...],
    source: Mapping[str, str], baseline_manifest_hash: str,
) -> FormalActivationStagePlanV3:
    return FormalActivationStagePlanV3.create(
        experiment_family_id=FAMILY,
        stage=stage,  # type: ignore[arg-type]
        registered_at="2026-07-14T00:00:00Z" if stage == "dry_run" else "2026-07-14T00:10:00Z",
        parent_stage_plan_hash=parent,
        pilot_result_hash=None,
        baseline_manifest_hash=baseline_manifest_hash,
        train_snapshot_hash=source["train_snapshot_hash"],
        valid_snapshot_hash=source["valid_snapshot_hash"],
        generator_hash=hash_artifact(Path(__file__).parents[1] / "src/alpha_foundry/search.py"),
        evaluator_hash=hash_artifact(Path(__file__).parents[1] / "src/alpha_quality/production_evaluator_v1.py"),
        dag_scheduler_hash=hash_artifact(Path(__file__).parents[1] / "src/alpha_quality/evaluator_dag_v1.py"),
        decision_criteria_hash=hash_artifact(Path(__file__).parents[1] / "src/alpha_quality/decision_v2/source_v3.py"),
        control_policy_hash=source["flat_policy_hash"],
        treatment_policy_hash=source["topology_policy_hash"],
        candidate_budget=32,
        compute_budget=64,
        worker_limit=2,
        timeout_seconds=600.0,
        run_group_ids=groups,
        seeds=seeds,
        primary_endpoint="effective_non_duplicate_candidate_yield_at_narrow_quality_decision_v3",
        primary_threshold=0.5,
        primary_threshold_unit="candidates_per_32_attempt_fixed_budget",
        secondary_noninferiority_margins={
            "cpu_seconds": 30.0,
            "duplicate_rate": 0.05,
            "failure_rate": 0.05,
            "peak_rss_mb": 128.0,
            "wall_seconds": 30.0,
        },
        effect_analysis_allowed=stage != "dry_run",
    )


def _prepare_group(
    store: ResearchEventStore, plan: ActivationExperimentPlan, group: str,
    bootstrap: Mapping[str, Any], source: Mapping[str, str],
    action_service: RetrieverActionTemplateServiceV1 | None = None,
    feature_service: RetrieverFeatureSourceServiceV1 | None = None,
    decision_service: RetrieverDecisionV7Service | None = None,
) -> dict[str, Any]:
    pair_id = f"{group}:global:all"
    seed_bank = SeedBank(
        [AlphaSeed(item["factor_spec_id"], item["formula"], "phase11-bootstrap") for item in bootstrap["seeds"]]
    )
    flat = PreArmFlatScheduleServiceV1(store).freeze(
        plan_hash=plan.plan_hash,
        pair_id=pair_id,
        run_group_id=group,
        data_snapshot_hash=source["train_snapshot_hash"],
        seed_bank=seed_bank,
        mutator=SeedMutator(max_candidates_per_seed=5),
        max_candidates=32,
        trial_budget=64,
    )
    topology_run = activation_arm_execution_run_id(
        plan_hash=plan.plan_hash, run_group_id=group, arm="treatment"
    )
    if action_service is None:
        action_service = RetrieverActionTemplateServiceV1(store=store, flags=store.flags)
    actions = tuple(
        action_service.freeze(
            execution_run_id=topology_run,
            parent_factor_spec_id=item["factor_spec_id"],
            template_id=template,
            eligible_event_watermark=bootstrap["eligible_event_watermark"],
            data_snapshot_hash=source["train_snapshot_hash"],
            retrieval_policy_hash=source["topology_policy_hash"],
        )
        for item in bootstrap["seeds"]
        for template in NONIDENTITY_TEMPLATES
    )
    if feature_service is None:
        feature_service = RetrieverFeatureSourceServiceV1(store, flags=store.flags)
    feature = feature_service.record(
        execution_run_id=topology_run,
        snapshot_event_hash=source["retrieval_snapshot_event_hash"],
        action_event_hashes=tuple(item.event.event_hash for item in actions),
        eligible_event_watermark=bootstrap["eligible_event_watermark"],
        retrieval_policy=ActivationRetrieverPolicy(),
    )
    if decision_service is None:
        decision_service = RetrieverDecisionV7Service(store)
    decision = decision_service.record(
        schedule_event_hash=flat.event.event_hash,
        feature_source_event_hash=feature.event.event_hash,
    )
    return {
        "pair_id": pair_id,
        "flat_event_hash": flat.event.event_hash,
        "topology_event_hash": decision.event.event_hash,
        "feature_event_hash": feature.event.event_hash,
    }


def _backup_db(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as origin, sqlite3.connect(target) as clone:
        origin.backup(clone)


def _child_arm(payload: Mapping[str, Any], queue: Any) -> None:
    try:
        db = Path(str(payload["db"]))
        artifacts = Path(str(payload["artifacts"]))
        store = _store(db, artifacts)
        source = _bundle_sources(store, str(payload["input_event_hash"]))
        factory = ProductionActivationCandidateFactoryV1(
            store=store, quality_decision_v3=_quality(store)
        )
        arm = str(payload["arm"])
        request = ProductionActivationFactoryArmRequestV1(
            schema_version="production_activation_factory_arm_request.v1",
            run_input_bundle_event_hash=str(payload["input_event_hash"]),
            plan_hash=str(payload["plan_hash"]),
            pair_id=str(payload["pair_id"]),
            run_group_id=str(payload["group"]),
            execution_run_id=str(payload["execution_run_id"]),
            arm=arm,  # type: ignore[arg-type]
            retriever_policy_hash=source[f"{'flat' if arm == 'flat' else 'topology'}_policy_hash"],
            retrieval_authority_event_hash=str(payload["retrieval_event_hash"]),
        )
        started = factory.start_arm(request)
        lifecycle = _FactoryLifecycle(
            factory=factory,
            contract_hash=source["contract_hash"],
            pit_snapshot_event_hash=source["pit_snapshot_event_hash"],
            data_snapshot_hash=source["train_snapshot_hash"],
            semantics=_semantics(),
            refs=[],
        )
        parent_seeds = tuple(
            AlphaSeed(item["factor_spec_id"], item["formula"], "phase11-bootstrap")
            for item in payload["seeds"]
        )
        if arm == "flat":
            search = factory.build_generator(
                seed_bank=SeedBank(parent_seeds),
                mutator=SeedMutator(max_candidates_per_seed=5),
                candidate_budget=32,
                compute_budget=64,
            )
            search.lifecycle = lifecycle  # existing search capability, closed runner binding
            search.run_id = request.execution_run_id
            result = search.generate()
            generation_event_hash = None
        else:
            service = RetrieverDecisionV7Service(store)
            decision, input_bundle, schedule, feature_source, actions = service.rebuild(
                schedule_event_hash=str(payload["flat_event_hash"]),
                feature_source_event_hash=str(payload["feature_event_hash"]),
                decision_event_hash=str(payload["retrieval_event_hash"]),
            )
            recorded = RecordedRetrieverDecisionV7(
                event=_event(store, str(payload["retrieval_event_hash"]), "RetrieverDecisionV7Recorded"),
                decision=decision,
                input_bundle=input_bundle,
                schedule=schedule,
                feature_source=feature_source,
                actions=actions,
            )
            activation_request = ActivationArmRequest(
                plan_hash=request.plan_hash,
                pair_id=request.pair_id,
                run_group_id=request.run_group_id,
                execution_run_id=request.execution_run_id,
                arm="treatment",
                seed=int(payload["seed"]),
                mechanism_family="global",
                dag_region="all",
                policy_hash=source["topology_policy_hash"],
                rng_namespace=f"{request.plan_hash}:{request.run_group_id}:treatment:rng",
                cache_namespace=f"{request.plan_hash}:{request.run_group_id}:treatment:cache",
                candidate_budget=32,
                compute_budget=64,
            )
            generated = ActivationTreatmentGeneratorV4().run(
                request=activation_request,
                scope=_Scope(
                    source["train_snapshot_hash"],
                    source["valid_snapshot_hash"],
                    str(payload["eligible_event_watermark"]),
                ),  # type: ignore[arg-type]
                recorded_decision=recorded,
                parent_seeds=parent_seeds,
                lifecycle=lifecycle,  # type: ignore[arg-type]
            )
            result = generated.search_result
            ActivationEvidenceService(store).record_generation_consumption_v4(
                generated.evidence
            )
            generation_event_hash = store.query_events(
                event_type="ActivationGenerationConsumptionV4Recorded"
            )[-1].event_hash
        completed = factory.complete_arm(
            request=request,
            arm_started_event_hash=started.event_hash,
            candidate_refs=tuple(lifecycle.refs),
        )
        queue.put(
            {
                "ok": True,
                "db": str(db),
                "attempts": len(result.attempts),
                "terminal_refs": list(completed.trial_terminal_event_refs),
                "evaluation_refs": list(completed.evaluation_event_refs),
                "quality_refs": list(completed.quality_decision_event_refs),
                "dossier_refs": list(completed.terminal_dossier_event_refs),
                "arm_started_event_hash": completed.arm_started_event_hash,
                "arm_completed_event_hash": completed.arm_completed_event_hash,
                "generation_event_hash": generation_event_hash,
                "chain_verified": store.verify_chain(),
            }
        )
    except BaseException as exc:
        queue.put({"ok": False, "error": repr(exc), "traceback": traceback.format_exc()})


def _run_spawn(payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    context = mp.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=_child_arm, args=(payload, queue))
    started = time.perf_counter()
    process.start()
    root = psutil.Process(process.pid)
    peak = 0
    cpu = 0.0
    timed_out = False
    while process.is_alive():
        elapsed = time.perf_counter() - started
        if elapsed > timeout:
            timed_out = True
            for child in root.children(recursive=True):
                child.kill()
            root.kill()
            break
        try:
            tree = [root, *root.children(recursive=True)]
            peak = max(peak, sum(item.memory_info().rss for item in tree if item.is_running()))
            cpu = max(
                cpu,
                sum(sum(item.cpu_times()[:2]) for item in tree if item.is_running()),
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        process.join(0.05)
    process.join(5.0)
    wall = time.perf_counter() - started
    result: dict[str, Any]
    if timed_out:
        result = {"ok": False, "timeout": True, "error": "PARENT_ENFORCED_TIMEOUT"}
    elif queue.empty():
        result = {"ok": False, "error": f"WORKER_EXIT_{process.exitcode}"}
    else:
        result = dict(queue.get())
    resource = {
        "wall_seconds": wall,
        "cpu_seconds": cpu,
        "peak_rss_mb": peak / (1024 * 1024),
        "timeout_seconds": timeout,
        "timed_out": timed_out,
        "exit_code": process.exitcode,
        "measurement": "parent_polled_complete_process_tree_psutil_v1",
    }
    resource["resource_hash"] = canonical_json_hash(resource)
    result["resource"] = resource
    return result


def _derive_arm(result: Mapping[str, Any]) -> dict[str, Any]:
    if not result.get("ok"):
        return {
            "complete": False,
            "attempts": 0,
            "yield": 0,
            "duplicate_rate": 0.0,
            "failure_rate": 1.0,
            **dict(result["resource"]),
            "error": result.get("error"),
        }
    store = _store(Path(str(result["db"])), Path(str(result["db"])).parents[1] / "artifacts")
    terminals = [_event(store, value, "TrialTerminated") for value in result["terminal_refs"]]
    decisions = [_event(store, value, "QualityDecisionV4Recorded") for value in result["quality_refs"]]
    statuses = [str(item.payload["status"]) for item in terminals]
    qualified = {
        str(item.payload["factor_spec_id"])
        for item in decisions
        if item.payload["decision"] in {"research_only", "candidate_zoo", "paper_candidate", "forward_track"}
    }
    failures = {"reject", "skip", "invalid", "timeout", "error", "infrastructure_failure"}
    return {
        "complete": bool(result["chain_verified"]) and len(terminals) == 32,
        "attempts": len(terminals),
        "yield": len(qualified),
        "duplicate_rate": statuses.count("duplicate") / 32,
        "failure_rate": sum(status in failures for status in statuses) / 32,
        "status_counts": dict(Counter(statuses)),
        "terminal_refs": list(result["terminal_refs"]),
        "evaluation_refs": list(result["evaluation_refs"]),
        "quality_refs": list(result["quality_refs"]),
        "dossier_refs": list(result["dossier_refs"]),
        "arm_started_event_hash": result["arm_started_event_hash"],
        "arm_completed_event_hash": result["arm_completed_event_hash"],
        **dict(result["resource"]),
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    return hash_artifact(path)


def _run_stage(
    *, root: Path, master_db: Path, artifacts: Path, plan: ActivationExperimentPlan,
    formal: FormalActivationStagePlanV3, bootstrap: Mapping[str, Any],
    prepared: Mapping[str, Mapping[str, Any]], input_event_hash: str,
) -> list[dict[str, Any]]:
    schedules = {item.run_group_id: item for item in freeze_pair_schedules_v3(formal)}
    records: list[dict[str, Any]] = []
    for group in formal.run_group_ids:
        pair: dict[str, Any] = {
            "run_group_id": group,
            "seed": schedules[group].seed,
            "arm_order": list(schedules[group].arm_order),
            "pair_id": prepared[group]["pair_id"],
            "formal_schedule_hash": schedules[group].schedule_hash,
            "arms": {},
        }
        for arm in schedules[group].arm_order:
            execution_arm = "control" if arm == "flat" else "treatment"
            db = root / "events" / f"{formal.stage}-{group}-{arm}.sqlite"
            _backup_db(master_db, db)
            raw = _run_spawn(
                {
                    "db": str(db),
                    "artifacts": str(artifacts),
                    "input_event_hash": input_event_hash,
                    "plan_hash": plan.plan_hash,
                    "pair_id": prepared[group]["pair_id"],
                    "group": group,
                    "arm": arm,
                    "execution_run_id": activation_arm_execution_run_id(
                        plan_hash=plan.plan_hash,
                        run_group_id=group,
                        arm=execution_arm,  # type: ignore[arg-type]
                    ),
                    "retrieval_event_hash": prepared[group][f"{arm}_event_hash"],
                    "flat_event_hash": prepared[group]["flat_event_hash"],
                    "feature_event_hash": prepared[group]["feature_event_hash"],
                    "seed": schedules[group].seed,
                    "seeds": bootstrap["seeds"],
                    "eligible_event_watermark": bootstrap["eligible_event_watermark"],
                },
                formal.timeout_seconds,
            )
            pair["arms"][arm] = _derive_arm(raw)
            pair["arms"][arm]["worker_result_hash"] = canonical_json_hash(raw)
            _write_json(
                root / "resources" / f"{formal.stage}-{group}-{arm}.json",
                pair["arms"][arm],
            )
        pair["complete"] = all(pair["arms"][arm]["complete"] for arm in ("flat", "topology"))
        pair["pair_hash"] = canonical_json_hash(pair)
        _write_json(root / "pairs" / f"{formal.stage}-{group}.json", pair)
        records.append(pair)
    return records


def _bootstrap_ci(values: list[float]) -> list[float] | None:
    if not values:
        return None
    rng = random.Random(454499)
    samples = sorted(
        statistics.mean(values[rng.randrange(len(values))] for _ in values)
        for _ in range(10000)
    )
    return [samples[249], samples[9750]]


def _summarize(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    complete = [item for item in pairs if item["complete"]]
    differences = [item["arms"]["topology"]["yield"] - item["arms"]["flat"]["yield"] for item in complete]
    result: dict[str, Any] = {
        "planned_pairs": len(pairs),
        "complete_pairs": len(complete),
        "invalid_incomplete_pairs": len(pairs) - len(complete),
        "flat_yield": sum(item["arms"]["flat"]["yield"] for item in complete),
        "topology_yield": sum(item["arms"]["topology"]["yield"] for item in complete),
        "paired_differences": differences,
        "paired_mean_difference": statistics.mean(differences) if differences else None,
        "paired_median_difference": statistics.median(differences) if differences else None,
        "uncertainty_95_percent_paired_cluster_bootstrap": _bootstrap_ci(differences),
    }
    for name, key in (
        ("duplicate_difference", "duplicate_rate"),
        ("failure_difference", "failure_rate"),
        ("cpu_difference", "cpu_seconds"),
        ("wall_difference", "wall_seconds"),
        ("peak_rss_difference", "peak_rss_mb"),
    ):
        values = [item["arms"]["topology"][key] - item["arms"]["flat"][key] for item in complete]
        result[name] = statistics.mean(values) if values else None
        result[name + "_paired_values"] = values
    result["threshold_result"] = (
        "invalidated_missing_pair"
        if len(complete) != len(pairs)
        else (
            "meets_0.5_candidates_per_32_attempts"
            if result["paired_mean_difference"] is not None and result["paired_mean_difference"] >= 0.5
            else "does_not_meet_0.5_candidates_per_32_attempts"
        )
    )
    return result


def _inventory(roots: list[Path]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for root in roots:
        db = root / "events.sqlite"
        count = 0
        snapshot_hash = None
        if db.exists():
            uri = f"file:{db.resolve().as_posix()}?mode=ro"
            with sqlite3.connect(uri, uri=True) as connection:
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "research_events" in tables:
                    count = int(connection.execute("SELECT COUNT(*) FROM research_events").fetchone()[0])
                    rows = connection.execute(
                        "SELECT payload FROM research_events WHERE event_type='AsharePITSnapshotRecorded' ORDER BY rowid DESC LIMIT 1"
                    ).fetchall()
                    if rows:
                        snapshot_hash = json.loads(rows[0][0]).get("snapshot_hash")
        result.append(
            {
                "run_artifact_root": str(root.resolve()),
                "event_count": count,
                "partition_count": sum(1 for _ in root.rglob("*.parquet")),
                "snapshot_hash": snapshot_hash,
                "failure_reason": "PROVIDER_AUDIT_REGISTERED_AFTER_SNAPSHOT",
                "analysis_eligible": False,
                "pilot_eligible": False,
            }
        )
    return result


def execute(root: Path, incomplete_roots: list[Path]) -> dict[str, Any]:
    if not root.exists() or not (root / "events.sqlite").exists():
        raise ValueError("fresh frozen input root is unavailable")
    master_dir = root / "events"
    master_dir.mkdir(exist_ok=True)
    master_db = master_dir / "master.sqlite"
    if master_db.exists():
        raise ValueError("outcome master database already exists; refusing append")
    shutil.move(str(root / "events.sqlite"), master_db)
    artifacts = root / "artifacts"
    store = _store(master_db, artifacts)
    manifest = json.loads((root / "replay_manifest.json").read_text(encoding="utf-8"))
    input_event_hash = str(manifest["input_event_hash"])
    inventory = _inventory(incomplete_roots)
    inventory_hash = _write_json(root / "excluded_incomplete_roots.json", {"roots": inventory})
    bootstrap = _bootstrap(store, input_event_hash)
    baseline_hash = canonical_json_hash(bootstrap["baseline"])
    sources = _bundle_sources(store, input_event_hash)
    dry_groups = ("dry-run-00", "dry-run-01")
    dry_seeds = (454320, 454321)
    pilot_groups = tuple(f"pilot-{index:02d}" for index in range(12))
    pilot_seeds = tuple(range(454400, 454412))
    dry_compat = _compatibility_plan(
        stage="dry-run", groups=dry_groups, seeds=dry_seeds,
        source=sources, watermark=bootstrap["eligible_event_watermark"],
    )
    pilot_compat = _compatibility_plan(
        stage="pilot", groups=pilot_groups, seeds=pilot_seeds,
        source=sources, watermark=bootstrap["eligible_event_watermark"],
    )
    evidence = ActivationEvidenceService(store)
    evidence.register_plan(dry_compat)
    evidence.register_plan(pilot_compat)
    dry_formal = _formal_plan(
        stage="dry_run", parent=None, groups=dry_groups, seeds=dry_seeds,
        source=sources, baseline_manifest_hash=baseline_hash,
    )
    pilot_formal = _formal_plan(
        stage="pilot", parent=dry_formal.plan_hash, groups=pilot_groups,
        seeds=pilot_seeds, source=sources, baseline_manifest_hash=baseline_hash,
    )
    _write_json(root / "plans" / "dry_run_formal_v3.json", dry_formal.to_dict())
    _write_json(root / "plans" / "pilot_formal_v3.json", pilot_formal.to_dict())
    action_service = RetrieverActionTemplateServiceV1(store=store, flags=store.flags)
    feature_service = RetrieverFeatureSourceServiceV1(store, flags=store.flags)
    decision_service = RetrieverDecisionV7Service(store)
    prepared_dry = {
        group: _prepare_group(
            store, dry_compat, group, bootstrap, sources,
            action_service, feature_service, decision_service,
        )
        for group in dry_groups
    }
    prepared_pilot = {
        group: _prepare_group(
            store, pilot_compat, group, bootstrap, sources,
            action_service, feature_service, decision_service,
        )
        for group in pilot_groups
    }
    dry = _run_stage(
        root=root, master_db=master_db, artifacts=artifacts, plan=dry_compat,
        formal=dry_formal, bootstrap=bootstrap, prepared=prepared_dry,
        input_event_hash=input_event_hash,
    )
    if not all(item["complete"] for item in dry):
        pilot: list[dict[str, Any]] = []
    else:
        pilot = _run_stage(
            root=root, master_db=master_db, artifacts=artifacts, plan=pilot_compat,
            formal=pilot_formal, bootstrap=bootstrap, prepared=prepared_pilot,
            input_event_hash=input_event_hash,
        )
    summary = {
        "schema_version": "baostock_research_only_phase11_outcome.v1",
        "feature_commit": FEATURE_COMMIT,
        "research_family_id": FAMILY,
        "fresh_run_id": canonical_json_hash({"root": root.name, "bundle": manifest["bundle_hash"]}),
        "input": manifest,
        "incomplete_roots_excluded": inventory,
        "incomplete_inventory_hash": inventory_hash,
        "baseline_smoke": bootstrap["baseline"],
        "bootstrap_seed_factors": bootstrap["seeds"],
        "dry_run_pairs": dry,
        "dry_run_result": "passed" if len(dry) == 2 and all(item["complete"] for item in dry) else "failed",
        "pilot_pairs": pilot,
        "pilot": _summarize(pilot) if pilot else {
            "planned_pairs": 12, "complete_pairs": 0,
            "invalid_incomplete_pairs": 12,
            "threshold_result": "not_run_dry_run_failed",
        },
        "formal_activation": "not_evaluated",
        "official_policy_effect": "none",
        "live_trading_meaning": "none",
        "maximum_promotion": "research_only",
        "test_final_forward_access_count": 0,
        "master_chain_verified": store.verify_chain(),
    }
    summary["outcome_hash"] = canonical_json_hash(summary)
    summary["outcome_artifact_hash"] = _write_json(root / "BAOSTOCK_RESEARCH_ONLY_PHASE11_OUTCOME.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--incomplete-root", action="append", default=[], type=Path)
    args = parser.parse_args()
    result = execute(args.root.resolve(), [item.resolve() for item in args.incomplete_root])
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    mp.freeze_support()
    raise SystemExit(main())
