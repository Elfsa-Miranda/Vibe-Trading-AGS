"""Phase 11 formal Flat-vs-Topology Activation protocol.

This module freezes the multi-stage experimental design and fail-closed handoff
to a later production run.  It does not turn infrastructure probes, pilot
results, or caller-authored summaries into an Activation approval.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import NormalDist
from types import MappingProxyType
from typing import Any, Literal, Mapping

from src.alpha_foundry.activation.isolated_worker_v3 import IsolatedWorkerResourceV3
from src.research_ledger.hash_utils import canonical_json_hash


StageV3 = Literal["dry_run", "pilot", "confirmatory"]
VerdictV3 = Literal["invalidated", "inconclusive", "rejected", "approved"]
ArmV3 = Literal["flat", "topology"]
_ZERO_HASH = "sha256:" + "0" * 64


def _hash(value: str, name: str) -> None:
    if not value.startswith("sha256:") or len(value) != 71:
        raise ValueError(f"{name} must be a canonical sha256 hash")
    try:
        int(value[7:], 16)
    except ValueError as exc:
        raise ValueError(f"{name} must be a canonical sha256 hash") from exc


def _sorted_unique(values: tuple[str, ...], name: str) -> None:
    if values != tuple(sorted(set(values))) or any(not value for value in values):
        raise ValueError(f"{name} must be sorted, unique and non-empty")


@dataclass(frozen=True)
class FormalActivationStagePlanV3:
    schema_version: Literal["formal_activation_stage_plan.v3"]
    experiment_family_id: str
    stage: StageV3
    registered_at: str
    parent_stage_plan_hash: str | None
    pilot_result_hash: str | None
    baseline_manifest_hash: str
    train_snapshot_hash: str
    valid_snapshot_hash: str
    generator_hash: str
    evaluator_hash: str
    dag_scheduler_hash: str
    decision_criteria_hash: str
    control_policy_hash: str
    treatment_policy_hash: str
    candidate_budget: int
    compute_budget: int
    worker_limit: int
    timeout_seconds: float
    run_group_ids: tuple[str, ...]
    seeds: tuple[int, ...]
    primary_endpoint: str
    primary_threshold: float
    primary_threshold_unit: str
    secondary_noninferiority_margins: Mapping[str, float]
    cluster_unit: Literal["episode_run_group"]
    missing_pair_rule: Literal["invalidate"]
    infrastructure_failure_rule: Literal["count_as_failure"]
    trial_failure_rule: Literal["count_in_arm"]
    independent_rng_rule: str
    independent_cache_rule: str
    effect_analysis_allowed: bool
    plan_hash: str

    @classmethod
    def create(
        cls,
        *,
        experiment_family_id: str,
        stage: StageV3,
        registered_at: str,
        parent_stage_plan_hash: str | None,
        pilot_result_hash: str | None,
        baseline_manifest_hash: str,
        train_snapshot_hash: str,
        valid_snapshot_hash: str,
        generator_hash: str,
        evaluator_hash: str,
        dag_scheduler_hash: str,
        decision_criteria_hash: str,
        control_policy_hash: str,
        treatment_policy_hash: str,
        candidate_budget: int,
        compute_budget: int,
        worker_limit: int,
        timeout_seconds: float,
        run_group_ids: tuple[str, ...],
        seeds: tuple[int, ...],
        primary_endpoint: str,
        primary_threshold: float,
        primary_threshold_unit: str,
        secondary_noninferiority_margins: Mapping[str, float],
        effect_analysis_allowed: bool,
    ) -> "FormalActivationStagePlanV3":
        content: dict[str, Any] = {
            "schema_version": "formal_activation_stage_plan.v3",
            "experiment_family_id": experiment_family_id,
            "stage": stage,
            "registered_at": registered_at,
            "parent_stage_plan_hash": parent_stage_plan_hash,
            "pilot_result_hash": pilot_result_hash,
            "baseline_manifest_hash": baseline_manifest_hash,
            "train_snapshot_hash": train_snapshot_hash,
            "valid_snapshot_hash": valid_snapshot_hash,
            "generator_hash": generator_hash,
            "evaluator_hash": evaluator_hash,
            "dag_scheduler_hash": dag_scheduler_hash,
            "decision_criteria_hash": decision_criteria_hash,
            "control_policy_hash": control_policy_hash,
            "treatment_policy_hash": treatment_policy_hash,
            "candidate_budget": candidate_budget,
            "compute_budget": compute_budget,
            "worker_limit": worker_limit,
            "timeout_seconds": timeout_seconds,
            "run_group_ids": list(run_group_ids),
            "seeds": list(seeds),
            "primary_endpoint": primary_endpoint,
            "primary_threshold": primary_threshold,
            "primary_threshold_unit": primary_threshold_unit,
            "secondary_noninferiority_margins": dict(secondary_noninferiority_margins),
            "cluster_unit": "episode_run_group",
            "missing_pair_rule": "invalidate",
            "infrastructure_failure_rule": "count_as_failure",
            "trial_failure_rule": "count_in_arm",
            "independent_rng_rule": "stage_plan_hash/run_group/arm/rng.v1",
            "independent_cache_rule": "stage_plan_hash/run_group/arm/cache.v1",
            "effect_analysis_allowed": effect_analysis_allowed,
        }
        return cls(
            schema_version="formal_activation_stage_plan.v3",
            experiment_family_id=experiment_family_id,
            stage=stage,
            registered_at=registered_at,
            parent_stage_plan_hash=parent_stage_plan_hash,
            pilot_result_hash=pilot_result_hash,
            baseline_manifest_hash=baseline_manifest_hash,
            train_snapshot_hash=train_snapshot_hash,
            valid_snapshot_hash=valid_snapshot_hash,
            generator_hash=generator_hash,
            evaluator_hash=evaluator_hash,
            dag_scheduler_hash=dag_scheduler_hash,
            decision_criteria_hash=decision_criteria_hash,
            control_policy_hash=control_policy_hash,
            treatment_policy_hash=treatment_policy_hash,
            candidate_budget=candidate_budget,
            compute_budget=compute_budget,
            worker_limit=worker_limit,
            timeout_seconds=timeout_seconds,
            run_group_ids=run_group_ids,
            seeds=seeds,
            primary_endpoint=primary_endpoint,
            primary_threshold=primary_threshold,
            primary_threshold_unit=primary_threshold_unit,
            secondary_noninferiority_margins=MappingProxyType(dict(secondary_noninferiority_margins)),
            cluster_unit="episode_run_group",
            missing_pair_rule="invalidate",
            infrastructure_failure_rule="count_as_failure",
            trial_failure_rule="count_in_arm",
            independent_rng_rule="stage_plan_hash/run_group/arm/rng.v1",
            independent_cache_rule="stage_plan_hash/run_group/arm/cache.v1",
            effect_analysis_allowed=effect_analysis_allowed,
            plan_hash=canonical_json_hash(content),
        )

    def __post_init__(self) -> None:
        if self.schema_version != "formal_activation_stage_plan.v3" or not self.experiment_family_id:
            raise ValueError("formal Activation stage plan identity is invalid")
        for name in (
            "baseline_manifest_hash", "train_snapshot_hash", "valid_snapshot_hash",
            "generator_hash", "evaluator_hash", "dag_scheduler_hash",
            "decision_criteria_hash", "control_policy_hash", "treatment_policy_hash",
            "plan_hash",
        ):
            _hash(str(getattr(self, name)), name)
        for name, value in (
            ("parent_stage_plan_hash", self.parent_stage_plan_hash),
            ("pilot_result_hash", self.pilot_result_hash),
        ):
            if value is not None:
                _hash(value, name)
        _sorted_unique(self.run_group_ids, "run groups")
        if len(self.seeds) != len(self.run_group_ids) or len(set(self.seeds)) != len(self.seeds):
            raise ValueError("every independent run group needs one unique seed")
        if (
            isinstance(self.candidate_budget, bool)
            or self.candidate_budget < 1
            or self.compute_budget < self.candidate_budget
            or self.worker_limit < 1
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0.0
        ):
            raise ValueError("formal Activation resource budget is invalid")
        if not math.isfinite(self.primary_threshold):
            raise ValueError("primary threshold must be finite")
        if any(not math.isfinite(value) or value < 0.0 for value in self.secondary_noninferiority_margins.values()):
            raise ValueError("non-inferiority margins must be finite and non-negative")
        if self.stage == "dry_run":
            if len(self.run_group_ids) != 2 or self.effect_analysis_allowed or self.parent_stage_plan_hash is not None:
                raise ValueError("dry-run must contain exactly two infrastructure-only groups")
        elif self.stage == "pilot":
            if len(self.run_group_ids) != 12 or not self.effect_analysis_allowed or self.parent_stage_plan_hash is None:
                raise ValueError("exploratory pilot must contain exactly twelve independent groups")
            if self.pilot_result_hash is not None:
                raise ValueError("pilot plan cannot cite a result that did not yet exist")
        elif (
            not self.effect_analysis_allowed
            or self.parent_stage_plan_hash is None
            or self.pilot_result_hash is None
            or len(self.run_group_ids) < 2
        ):
            raise ValueError("confirmatory plan requires a prior pilot result and fixed groups")
        if self.plan_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("formal Activation stage plan hash mismatch")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "experiment_family_id": self.experiment_family_id,
            "stage": self.stage,
            "registered_at": self.registered_at,
            "parent_stage_plan_hash": self.parent_stage_plan_hash,
            "pilot_result_hash": self.pilot_result_hash,
            "baseline_manifest_hash": self.baseline_manifest_hash,
            "train_snapshot_hash": self.train_snapshot_hash,
            "valid_snapshot_hash": self.valid_snapshot_hash,
            "generator_hash": self.generator_hash,
            "evaluator_hash": self.evaluator_hash,
            "dag_scheduler_hash": self.dag_scheduler_hash,
            "decision_criteria_hash": self.decision_criteria_hash,
            "control_policy_hash": self.control_policy_hash,
            "treatment_policy_hash": self.treatment_policy_hash,
            "candidate_budget": self.candidate_budget,
            "compute_budget": self.compute_budget,
            "worker_limit": self.worker_limit,
            "timeout_seconds": self.timeout_seconds,
            "run_group_ids": list(self.run_group_ids),
            "seeds": list(self.seeds),
            "primary_endpoint": self.primary_endpoint,
            "primary_threshold": self.primary_threshold,
            "primary_threshold_unit": self.primary_threshold_unit,
            "secondary_noninferiority_margins": dict(self.secondary_noninferiority_margins),
            "cluster_unit": self.cluster_unit,
            "missing_pair_rule": self.missing_pair_rule,
            "infrastructure_failure_rule": self.infrastructure_failure_rule,
            "trial_failure_rule": self.trial_failure_rule,
            "independent_rng_rule": self.independent_rng_rule,
            "independent_cache_rule": self.independent_cache_rule,
            "effect_analysis_allowed": self.effect_analysis_allowed,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "plan_hash": self.plan_hash}


@dataclass(frozen=True)
class FormalActivationPairScheduleV3:
    schema_version: Literal["formal_activation_pair_schedule.v3"]
    stage_plan_hash: str
    stage: StageV3
    pair_id: str
    run_group_id: str
    seed: int
    arm_order: tuple[ArmV3, ArmV3]
    flat_rng_namespace: str
    topology_rng_namespace: str
    flat_cache_namespace: str
    topology_cache_namespace: str
    candidate_budget: int
    compute_budget: int
    timeout_seconds: float
    schedule_hash: str

    @classmethod
    def from_plan(
        cls, plan: FormalActivationStagePlanV3, run_group_id: str
    ) -> "FormalActivationPairScheduleV3":
        index = plan.run_group_ids.index(run_group_id)
        order: tuple[ArmV3, ArmV3] = (
            ("flat", "topology") if index % 2 == 0 else ("topology", "flat")
        )
        prefix = f"{plan.plan_hash}/{run_group_id}"
        content = {
            "schema_version": "formal_activation_pair_schedule.v3",
            "stage_plan_hash": plan.plan_hash,
            "stage": plan.stage,
            "pair_id": f"{plan.stage}:{run_group_id}",
            "run_group_id": run_group_id,
            "seed": plan.seeds[index],
            "arm_order": list(order),
            "flat_rng_namespace": f"{prefix}/flat/rng",
            "topology_rng_namespace": f"{prefix}/topology/rng",
            "flat_cache_namespace": f"{prefix}/flat/cache",
            "topology_cache_namespace": f"{prefix}/topology/cache",
            "candidate_budget": plan.candidate_budget,
            "compute_budget": plan.compute_budget,
            "timeout_seconds": plan.timeout_seconds,
        }
        return cls(
            schema_version="formal_activation_pair_schedule.v3",
            stage_plan_hash=plan.plan_hash,
            stage=plan.stage,
            pair_id=f"{plan.stage}:{run_group_id}",
            run_group_id=run_group_id,
            seed=plan.seeds[index],
            arm_order=order,
            flat_rng_namespace=f"{prefix}/flat/rng",
            topology_rng_namespace=f"{prefix}/topology/rng",
            flat_cache_namespace=f"{prefix}/flat/cache",
            topology_cache_namespace=f"{prefix}/topology/cache",
            candidate_budget=plan.candidate_budget,
            compute_budget=plan.compute_budget,
            timeout_seconds=plan.timeout_seconds,
            schedule_hash=canonical_json_hash(content),
        )

    def __post_init__(self) -> None:
        _hash(self.stage_plan_hash, "stage_plan_hash")
        _hash(self.schedule_hash, "schedule_hash")
        if self.arm_order not in {("flat", "topology"), ("topology", "flat")}:
            raise ValueError("formal Activation arm order is not counterbalanced")
        if self.flat_rng_namespace == self.topology_rng_namespace or self.flat_cache_namespace == self.topology_cache_namespace:
            raise ValueError("formal Activation arms require independent RNG and cache namespaces")
        if self.schedule_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("formal Activation pair schedule hash mismatch")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "stage_plan_hash": self.stage_plan_hash,
            "stage": self.stage,
            "pair_id": self.pair_id,
            "run_group_id": self.run_group_id,
            "seed": self.seed,
            "arm_order": list(self.arm_order),
            "flat_rng_namespace": self.flat_rng_namespace,
            "topology_rng_namespace": self.topology_rng_namespace,
            "flat_cache_namespace": self.flat_cache_namespace,
            "topology_cache_namespace": self.topology_cache_namespace,
            "candidate_budget": self.candidate_budget,
            "compute_budget": self.compute_budget,
            "timeout_seconds": self.timeout_seconds,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "schedule_hash": self.schedule_hash}


def freeze_pair_schedules_v3(
    plan: FormalActivationStagePlanV3,
) -> tuple[FormalActivationPairScheduleV3, ...]:
    schedules = tuple(
        FormalActivationPairScheduleV3.from_plan(plan, group)
        for group in plan.run_group_ids
    )
    first_orders = sum(item.arm_order[0] == "flat" for item in schedules)
    if abs(first_orders - (len(schedules) - first_orders)) > 1:
        raise ValueError("formal Activation arm order is not balanced")
    return schedules


@dataclass(frozen=True)
class ConfirmatoryPowerTemplateV3:
    schema_version: Literal["activation_confirmatory_power_template.v3"]
    pilot_plan_hash: str
    primary_endpoint: str
    primary_threshold: float
    alpha_one_sided: float
    target_power: float
    minimum_effective_pairs: int
    maximum_pairs: int
    sample_size_formula: str
    effect_rule: str
    missing_pair_rule: Literal["invalidate"]
    template_hash: str

    @classmethod
    def create(
        cls,
        pilot: FormalActivationStagePlanV3,
        *,
        alpha_one_sided: float = 0.05,
        target_power: float = 0.8,
        minimum_effective_pairs: int = 12,
        maximum_pairs: int = 256,
    ) -> "ConfirmatoryPowerTemplateV3":
        if pilot.stage != "pilot":
            raise ValueError("power template requires a frozen exploratory pilot plan")
        content = {
            "schema_version": "activation_confirmatory_power_template.v3",
            "pilot_plan_hash": pilot.plan_hash,
            "primary_endpoint": pilot.primary_endpoint,
            "primary_threshold": pilot.primary_threshold,
            "alpha_one_sided": alpha_one_sided,
            "target_power": target_power,
            "minimum_effective_pairs": minimum_effective_pairs,
            "maximum_pairs": maximum_pairs,
            "sample_size_formula": "ceil(((z_1_minus_alpha+z_power)*pilot_paired_sd/planning_effect)^2), rounded_even",
            "effect_rule": "max(primary_threshold, min(abs(pilot_mean_effect), preregistered_mde))",
            "missing_pair_rule": "invalidate",
        }
        return cls(
            schema_version="activation_confirmatory_power_template.v3",
            pilot_plan_hash=pilot.plan_hash,
            primary_endpoint=pilot.primary_endpoint,
            primary_threshold=pilot.primary_threshold,
            alpha_one_sided=alpha_one_sided,
            target_power=target_power,
            minimum_effective_pairs=minimum_effective_pairs,
            maximum_pairs=maximum_pairs,
            sample_size_formula=str(content["sample_size_formula"]),
            effect_rule=str(content["effect_rule"]),
            missing_pair_rule="invalidate",
            template_hash=canonical_json_hash(content),
        )

    def required_pairs(
        self,
        *,
        pilot_mean_effect: float,
        pilot_paired_sd: float,
        preregistered_mde: float,
    ) -> int:
        for value in (pilot_mean_effect, pilot_paired_sd, preregistered_mde):
            if not math.isfinite(value):
                raise ValueError("pilot power input must be finite")
        if pilot_paired_sd <= 0.0 or preregistered_mde <= 0.0:
            raise ValueError("pilot variance and preregistered MDE must be positive")
        planning_effect = max(
            self.primary_threshold,
            min(abs(pilot_mean_effect), preregistered_mde),
        )
        if planning_effect <= 0.0:
            raise ValueError("planning effect must remain positive")
        z_alpha = NormalDist().inv_cdf(1.0 - self.alpha_one_sided)
        z_power = NormalDist().inv_cdf(self.target_power)
        required = math.ceil(((z_alpha + z_power) * pilot_paired_sd / planning_effect) ** 2)
        required = max(required, self.minimum_effective_pairs)
        if required % 2:
            required += 1
        if required > self.maximum_pairs:
            raise ValueError("confirmatory sample exceeds the preregistered resource ceiling")
        return required

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "pilot_plan_hash": self.pilot_plan_hash,
            "primary_endpoint": self.primary_endpoint,
            "primary_threshold": self.primary_threshold,
            "alpha_one_sided": self.alpha_one_sided,
            "target_power": self.target_power,
            "minimum_effective_pairs": self.minimum_effective_pairs,
            "maximum_pairs": self.maximum_pairs,
            "sample_size_formula": self.sample_size_formula,
            "effect_rule": self.effect_rule,
            "missing_pair_rule": self.missing_pair_rule,
            "template_hash": self.template_hash,
        }


@dataclass(frozen=True)
class Phase11ReadinessV3:
    schema_version: Literal["phase11_activation_readiness.v3"]
    baseline_manifest_hash: str
    baseline_effective_sample: int
    baseline_replayable: bool
    infrastructure_resource_hashes: tuple[str, ...]
    completed_requirements: tuple[str, ...]
    blocked_requirements: tuple[str, ...]
    exact_unblock_conditions: tuple[str, ...]
    ready_for_pilot_outcome_access: bool
    readiness_hash: str

    @classmethod
    def create(
        cls,
        *,
        baseline_manifest_hash: str,
        baseline_effective_sample: int,
        baseline_replayable: bool,
        infrastructure_resources: tuple[IsolatedWorkerResourceV3, ...],
        production_candidate_factory_available: bool,
        production_run_inputs_available: bool,
        run_budget_authorized: bool,
    ) -> "Phase11ReadinessV3":
        complete = {
            "BASELINE_EFFECTIVE_SAMPLE_REPLAYABLE",
            "COUNTERBALANCED_STAGE_SCHEDULES_FROZEN",
            "DRY_RUN_EXCLUDED_FROM_EFFECT_ANALYSIS",
            "NARROW_RETRIEVER_V7_QUALITY_DECISION_V3_REQUIRED",
            "PRODUCTION_CANDIDATE_EVALUATOR_DAG_AVAILABLE",
            "SHADOW_FLAT_ROLLBACK_DEFAULT",
        }
        blocked: set[str] = set()
        conditions: set[str] = set()
        if baseline_effective_sample < 1 or not baseline_replayable:
            complete.discard("BASELINE_EFFECTIVE_SAMPLE_REPLAYABLE")
            blocked.add("REAL_REPLAYABLE_BASELINE_UNAVAILABLE")
            conditions.add("PROVIDE_REPLAYABLE_BASELINE_EFFECTIVE_SAMPLE_AT_LEAST_ONE")
        resource_hashes = tuple(sorted(item.evidence_hash for item in infrastructure_resources))
        completed_probe = any(item.status == "completed" for item in infrastructure_resources)
        timeout_probe = any(item.status == "timeout" for item in infrastructure_resources)
        isolated_rss = all(item.peak_rss_mb > 0.0 for item in infrastructure_resources)
        if completed_probe and timeout_probe and isolated_rss:
            complete.update({"PARENT_TIMEOUT_ENFORCED", "PER_ARM_ISOLATED_RSS_MEASURED"})
        else:
            blocked.add("ISOLATED_WORKER_RESOURCE_PROOF_INCOMPLETE")
            conditions.add("PASS_COMPLETION_TIMEOUT_AND_ISOLATED_RSS_DRY_RUN_PROBES")
        if not production_candidate_factory_available:
            blocked.add("PRODUCTION_ACTIVATION_CANDIDATE_FACTORY_UNAVAILABLE")
            conditions.add("BIND_PRODUCTION_CANDIDATE_FACTORY_TO_FROZEN_GENERATOR_AND_EVALUATOR")
        if not production_run_inputs_available:
            blocked.add("PRODUCTION_TRAIN_VALID_RUN_INPUTS_UNAVAILABLE")
            conditions.add("PROVIDE_FROZEN_PRODUCER_BOUND_TRAIN_VALID_INPUT_ARTIFACTS")
        if not run_budget_authorized:
            blocked.add("PILOT_RUN_BUDGET_NOT_AUTHORIZED")
            conditions.add("AUTHORIZE_12_PAIRED_PILOT_GROUPS_AT_FROZEN_BUDGET")
        completed = tuple(sorted(complete))
        blockers = tuple(sorted(blocked))
        unblocks = tuple(sorted(conditions))
        content = {
            "schema_version": "phase11_activation_readiness.v3",
            "baseline_manifest_hash": baseline_manifest_hash,
            "baseline_effective_sample": baseline_effective_sample,
            "baseline_replayable": baseline_replayable,
            "infrastructure_resource_hashes": list(resource_hashes),
            "completed_requirements": list(completed),
            "blocked_requirements": list(blockers),
            "exact_unblock_conditions": list(unblocks),
            "ready_for_pilot_outcome_access": not blockers,
        }
        return cls(
            schema_version="phase11_activation_readiness.v3",
            baseline_manifest_hash=baseline_manifest_hash,
            baseline_effective_sample=baseline_effective_sample,
            baseline_replayable=baseline_replayable,
            infrastructure_resource_hashes=resource_hashes,
            completed_requirements=completed,
            blocked_requirements=blockers,
            exact_unblock_conditions=unblocks,
            ready_for_pilot_outcome_access=not blockers,
            readiness_hash=canonical_json_hash(content),
        )

    def __post_init__(self) -> None:
        _hash(self.baseline_manifest_hash, "baseline_manifest_hash")
        _hash(self.readiness_hash, "readiness_hash")
        _sorted_unique(self.infrastructure_resource_hashes, "infrastructure resources")
        _sorted_unique(self.completed_requirements, "completed requirements")
        if self.blocked_requirements:
            _sorted_unique(self.blocked_requirements, "blocked requirements")
        if self.exact_unblock_conditions:
            _sorted_unique(self.exact_unblock_conditions, "unblock conditions")
        if self.ready_for_pilot_outcome_access != (not self.blocked_requirements):
            raise ValueError("pilot readiness must derive from exact blockers")
        if self.readiness_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("Phase 11 readiness hash mismatch")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "baseline_manifest_hash": self.baseline_manifest_hash,
            "baseline_effective_sample": self.baseline_effective_sample,
            "baseline_replayable": self.baseline_replayable,
            "infrastructure_resource_hashes": list(self.infrastructure_resource_hashes),
            "completed_requirements": list(self.completed_requirements),
            "blocked_requirements": list(self.blocked_requirements),
            "exact_unblock_conditions": list(self.exact_unblock_conditions),
            "ready_for_pilot_outcome_access": self.ready_for_pilot_outcome_access,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "readiness_hash": self.readiness_hash}


@dataclass(frozen=True)
class FormalActivationDossierV3:
    schema_version: Literal["formal_activation_dossier.v3"]
    readiness_hash: str
    dry_run_plan_hash: str
    pilot_plan_hash: str
    confirmatory_template_hash: str
    dry_run_resource_hashes: tuple[str, ...]
    pilot_schedule_hashes: tuple[str, ...]
    pilot_complete_pairs: int
    confirmatory_complete_pairs: int
    verdict: VerdictV3
    active_research_only: bool
    official_search_policy: Literal["flat_with_topology_shadow"]
    reasons: tuple[str, ...]
    limitations: tuple[str, ...]
    dossier_hash: str

    @classmethod
    def blocked(
        cls,
        *,
        readiness: Phase11ReadinessV3,
        dry_run_plan: FormalActivationStagePlanV3,
        pilot_plan: FormalActivationStagePlanV3,
        confirmatory_template: ConfirmatoryPowerTemplateV3,
        dry_run_resources: tuple[IsolatedWorkerResourceV3, ...],
        pilot_schedules: tuple[FormalActivationPairScheduleV3, ...],
    ) -> "FormalActivationDossierV3":
        if readiness.ready_for_pilot_outcome_access:
            raise ValueError("blocked dossier requires a real unresolved prerequisite")
        reasons = readiness.blocked_requirements
        limitations = tuple(sorted({
            "DRY_RUN_INFRASTRUCTURE_ONLY_NOT_EFFECT_EVIDENCE",
            "NO_PILOT_OUTCOME_WAS_FABRICATED",
            "NO_CONFIRMATORY_PLAN_BEFORE_PILOT_RESULT",
            "NO_LLM_THIRD_ARM_IN_CURRENT_RESEARCH_CYCLE",
            "NO_LIVE_TRADING_MEANING",
        }))
        resource_hashes = tuple(sorted(item.evidence_hash for item in dry_run_resources))
        schedule_hashes = tuple(sorted(item.schedule_hash for item in pilot_schedules))
        content = {
            "schema_version": "formal_activation_dossier.v3",
            "readiness_hash": readiness.readiness_hash,
            "dry_run_plan_hash": dry_run_plan.plan_hash,
            "pilot_plan_hash": pilot_plan.plan_hash,
            "confirmatory_template_hash": confirmatory_template.template_hash,
            "dry_run_resource_hashes": list(resource_hashes),
            "pilot_schedule_hashes": list(schedule_hashes),
            "pilot_complete_pairs": 0,
            "confirmatory_complete_pairs": 0,
            "verdict": "inconclusive",
            "active_research_only": False,
            "official_search_policy": "flat_with_topology_shadow",
            "reasons": list(reasons),
            "limitations": list(limitations),
        }
        return cls(
            schema_version="formal_activation_dossier.v3",
            readiness_hash=readiness.readiness_hash,
            dry_run_plan_hash=dry_run_plan.plan_hash,
            pilot_plan_hash=pilot_plan.plan_hash,
            confirmatory_template_hash=confirmatory_template.template_hash,
            dry_run_resource_hashes=resource_hashes,
            pilot_schedule_hashes=schedule_hashes,
            pilot_complete_pairs=0,
            confirmatory_complete_pairs=0,
            verdict="inconclusive",
            active_research_only=False,
            official_search_policy="flat_with_topology_shadow",
            reasons=reasons,
            limitations=limitations,
            dossier_hash=canonical_json_hash(content),
        )

    def __post_init__(self) -> None:
        for name in (
            "readiness_hash", "dry_run_plan_hash", "pilot_plan_hash",
            "confirmatory_template_hash", "dossier_hash",
        ):
            _hash(str(getattr(self, name)), name)
        if self.verdict == "approved":
            raise ValueError(
                "approved dossier requires the not-yet-unblocked formal source authority gate"
            )
        if self.active_research_only:
            raise ValueError("unapproved Activation cannot influence research-only search")
        if self.dossier_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("formal Activation dossier hash mismatch")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "readiness_hash": self.readiness_hash,
            "dry_run_plan_hash": self.dry_run_plan_hash,
            "pilot_plan_hash": self.pilot_plan_hash,
            "confirmatory_template_hash": self.confirmatory_template_hash,
            "dry_run_resource_hashes": list(self.dry_run_resource_hashes),
            "pilot_schedule_hashes": list(self.pilot_schedule_hashes),
            "pilot_complete_pairs": self.pilot_complete_pairs,
            "confirmatory_complete_pairs": self.confirmatory_complete_pairs,
            "verdict": self.verdict,
            "active_research_only": self.active_research_only,
            "official_search_policy": self.official_search_policy,
            "reasons": list(self.reasons),
            "limitations": list(self.limitations),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "dossier_hash": self.dossier_hash}


__all__ = [
    "ConfirmatoryPowerTemplateV3",
    "FormalActivationDossierV3",
    "FormalActivationPairScheduleV3",
    "FormalActivationStagePlanV3",
    "Phase11ReadinessV3",
    "freeze_pair_schedules_v3",
]
