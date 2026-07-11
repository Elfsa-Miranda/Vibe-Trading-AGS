"""Immutable contracts for the independent topology activation experiment."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Mapping

from src.research_ledger.hash_utils import canonical_json_hash


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
TERMINAL_STATUSES = (
    "success", "reject", "skip", "invalid", "duplicate", "timeout", "error",
    "infrastructure_failure",
)


def _hash(value: str, name: str) -> None:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical sha256 hash")


def _codes(values: tuple[str, ...], name: str) -> None:
    if values != tuple(sorted(set(values))) or any(
        _CODE_RE.fullmatch(value) is None for value in values
    ):
        raise ValueError(f"{name} must be sorted unique safe codes")


def _finite(value: float, name: str) -> None:
    if isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class ActivationProvenance:
    code_version: str
    code_hash: str
    feature_flags: Mapping[str, bool]
    runtime_manifest_hash: str
    generator_version: str
    generator_hash: str
    grammar_version: str
    grammar_hash: str
    control_policy_hash: str
    treatment_policy_hash: str
    discovery_projection_hash: str
    discovery_watermark: str
    eligible_event_chain_head: str
    train_snapshot_hash: str
    valid_snapshot_hash: str
    universe_hash: str
    market_hash: str
    calendar_hash: str
    period_hash: str
    regime_hash: str
    candidate_definition_hash: str
    deduplication_hash: str
    decision_criteria_hash: str

    def __post_init__(self) -> None:
        if not self.code_version or not self.generator_version or not self.grammar_version:
            raise ValueError("activation provenance versions are required")
        for name, value in self.to_dict().items():
            if name.endswith("_hash") or name in {
                "discovery_watermark", "eligible_event_chain_head",
            }:
                _hash(str(value), name)
        if not self.feature_flags or any(
            not isinstance(value, bool) for value in self.feature_flags.values()
        ):
            raise ValueError("activation feature flag snapshot is invalid")
        object.__setattr__(self, "feature_flags", _freeze(self.feature_flags))

    def to_dict(self) -> dict[str, Any]:
        return {
            "code_version": self.code_version,
            "code_hash": self.code_hash,
            "feature_flags": dict(self.feature_flags),
            "runtime_manifest_hash": self.runtime_manifest_hash,
            "generator_version": self.generator_version,
            "generator_hash": self.generator_hash,
            "grammar_version": self.grammar_version,
            "grammar_hash": self.grammar_hash,
            "control_policy_hash": self.control_policy_hash,
            "treatment_policy_hash": self.treatment_policy_hash,
            "discovery_projection_hash": self.discovery_projection_hash,
            "discovery_watermark": self.discovery_watermark,
            "eligible_event_chain_head": self.eligible_event_chain_head,
            "train_snapshot_hash": self.train_snapshot_hash,
            "valid_snapshot_hash": self.valid_snapshot_hash,
            "universe_hash": self.universe_hash,
            "market_hash": self.market_hash,
            "calendar_hash": self.calendar_hash,
            "period_hash": self.period_hash,
            "regime_hash": self.regime_hash,
            "candidate_definition_hash": self.candidate_definition_hash,
            "deduplication_hash": self.deduplication_hash,
            "decision_criteria_hash": self.decision_criteria_hash,
        }


@dataclass(frozen=True)
class ActivationDesign:
    candidate_budget: int
    compute_budget: int
    worker_limit: int
    timeout_seconds: float
    seeds: tuple[int, ...]
    run_group_ids: tuple[str, ...]
    pilot_excluded_run_group_ids: tuple[str, ...]
    mechanism_families: tuple[str, ...]
    dag_regions: tuple[str, ...]
    pairing_keys: tuple[str, ...]
    independent_run_group_definition: str
    rng_namespace_rule: str
    cache_namespace_rule: str

    def __post_init__(self) -> None:
        if not 1 <= self.candidate_budget <= 100_000:
            raise ValueError("candidate budget is out of bounds")
        if self.compute_budget < self.candidate_budget or self.worker_limit < 1:
            raise ValueError("compute budget or worker limit is invalid")
        _finite(self.timeout_seconds, "timeout_seconds")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout must be positive")
        if not self.seeds or len(self.seeds) != len(set(self.seeds)):
            raise ValueError("activation seeds must be non-empty and unique")
        for name, values in (
            ("run groups", self.run_group_ids),
            ("mechanism families", self.mechanism_families),
            ("DAG regions", self.dag_regions),
            ("pairing keys", self.pairing_keys),
        ):
            if not values or values != tuple(sorted(set(values))):
                raise ValueError(f"activation {name} must be sorted and unique")
        if set(self.run_group_ids) & set(self.pilot_excluded_run_group_ids):
            raise ValueError("pilot run groups cannot enter confirmatory design")
        if len(self.seeds) != len(self.run_group_ids):
            raise ValueError("each independent run group requires one frozen seed")
        if not all(
            (self.independent_run_group_definition, self.rng_namespace_rule, self.cache_namespace_rule)
        ):
            raise ValueError("activation namespace and grouping rules are required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_budget": self.candidate_budget,
            "compute_budget": self.compute_budget,
            "worker_limit": self.worker_limit,
            "timeout_seconds": self.timeout_seconds,
            "seeds": list(self.seeds),
            "run_group_ids": list(self.run_group_ids),
            "pilot_excluded_run_group_ids": list(self.pilot_excluded_run_group_ids),
            "mechanism_families": list(self.mechanism_families),
            "dag_regions": list(self.dag_regions),
            "pairing_keys": list(self.pairing_keys),
            "independent_run_group_definition": self.independent_run_group_definition,
            "rng_namespace_rule": self.rng_namespace_rule,
            "cache_namespace_rule": self.cache_namespace_rule,
        }


@dataclass(frozen=True)
class ActivationAnalysisPolicy:
    primary_estimand: str
    primary_threshold: float
    primary_threshold_unit: str
    secondary_estimands: tuple[str, ...]
    noninferiority_margins: Mapping[str, float]
    confidence_level: float
    bootstrap_method: Literal["paired_cluster_percentile.v1"]
    cluster_unit: str
    bootstrap_resamples: int
    bootstrap_seed: int
    multiple_testing_family: tuple[str, ...]
    multiple_testing_method: Literal["holm.v1"]
    minimum_effective_pairs: int
    fixed_stopping_rule: str
    missing_pair_rule: Literal["invalidate"]
    trial_failure_rule: Literal["count_in_arm"]
    infrastructure_failure_rule: Literal["count_as_failure"]
    target_power: float
    assumed_cluster_standard_deviation: float
    minimum_detectable_effect: float
    required_independent_groups: int
    maximum_ci_width: float

    def __post_init__(self) -> None:
        for name in (
            "primary_threshold", "confidence_level", "target_power",
            "assumed_cluster_standard_deviation", "minimum_detectable_effect",
            "maximum_ci_width",
        ):
            _finite(float(getattr(self, name)), name)
        if not 0.5 < self.confidence_level < 1.0 or not 0.8 <= self.target_power < 1.0:
            raise ValueError("confidence and target power are invalid")
        if self.bootstrap_resamples < 200 or self.minimum_effective_pairs < 2:
            raise ValueError("bootstrap or effective-pair floor is too small")
        if self.required_independent_groups < self.minimum_effective_pairs:
            raise ValueError("power group requirement is below the analysis floor")
        if self.assumed_cluster_standard_deviation <= 0 or self.minimum_detectable_effect <= 0:
            raise ValueError("power assumptions must be positive")
        if self.secondary_estimands != tuple(sorted(set(self.secondary_estimands))):
            raise ValueError("secondary estimands must be sorted and unique")
        if self.multiple_testing_family != tuple(sorted(set(self.multiple_testing_family))):
            raise ValueError("multiplicity family must be sorted and unique")
        if set(self.noninferiority_margins) != set(self.multiple_testing_family):
            raise ValueError("every confirmatory secondary needs one frozen margin")
        if any(not math.isfinite(float(value)) or value < 0 for value in self.noninferiority_margins.values()):
            raise ValueError("noninferiority margins must be finite and non-negative")
        object.__setattr__(self, "noninferiority_margins", _freeze(self.noninferiority_margins))

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_estimand": self.primary_estimand,
            "primary_threshold": self.primary_threshold,
            "primary_threshold_unit": self.primary_threshold_unit,
            "secondary_estimands": list(self.secondary_estimands),
            "noninferiority_margins": dict(self.noninferiority_margins),
            "confidence_level": self.confidence_level,
            "bootstrap_method": self.bootstrap_method,
            "cluster_unit": self.cluster_unit,
            "bootstrap_resamples": self.bootstrap_resamples,
            "bootstrap_seed": self.bootstrap_seed,
            "multiple_testing_family": list(self.multiple_testing_family),
            "multiple_testing_method": self.multiple_testing_method,
            "minimum_effective_pairs": self.minimum_effective_pairs,
            "fixed_stopping_rule": self.fixed_stopping_rule,
            "missing_pair_rule": self.missing_pair_rule,
            "trial_failure_rule": self.trial_failure_rule,
            "infrastructure_failure_rule": self.infrastructure_failure_rule,
            "target_power": self.target_power,
            "assumed_cluster_standard_deviation": self.assumed_cluster_standard_deviation,
            "minimum_detectable_effect": self.minimum_detectable_effect,
            "required_independent_groups": self.required_independent_groups,
            "maximum_ci_width": self.maximum_ci_width,
        }


@dataclass(frozen=True)
class ActivationExperimentPlan:
    schema_version: Literal["activation_experiment_plan.v1"]
    experiment_id: str
    phase: Literal["pilot", "confirmatory"]
    registered_at: str
    provenance: ActivationProvenance
    design: ActivationDesign
    analysis: ActivationAnalysisPolicy
    readiness_requirements: tuple[str, ...]
    decision_policy_hash: str
    truth_table_hash: str
    limitations: tuple[str, ...]
    plan_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "activation_experiment_plan.v1" or not self.experiment_id:
            raise ValueError("invalid activation plan schema or identity")
        _hash(self.decision_policy_hash, "decision_policy_hash")
        _hash(self.truth_table_hash, "truth_table_hash")
        _hash(self.plan_hash, "plan_hash")
        _codes(self.readiness_requirements, "readiness requirements")
        _codes(self.limitations, "plan limitations")
        if self.plan_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("activation plan hash mismatch")

    @classmethod
    def create(
        cls,
        *,
        experiment_id: str,
        phase: Literal["pilot", "confirmatory"],
        registered_at: str,
        provenance: ActivationProvenance,
        design: ActivationDesign,
        analysis: ActivationAnalysisPolicy,
        readiness_requirements: tuple[str, ...],
        decision_policy_hash: str,
        truth_table_hash: str,
        limitations: tuple[str, ...] = (),
    ) -> "ActivationExperimentPlan":
        content = {
            "schema_version": "activation_experiment_plan.v1",
            "experiment_id": experiment_id,
            "phase": phase,
            "registered_at": registered_at,
            "provenance": provenance.to_dict(),
            "design": design.to_dict(),
            "analysis": analysis.to_dict(),
            "readiness_requirements": list(readiness_requirements),
            "decision_policy_hash": decision_policy_hash,
            "truth_table_hash": truth_table_hash,
            "limitations": list(limitations),
        }
        return cls(
            schema_version="activation_experiment_plan.v1",
            experiment_id=experiment_id,
            phase=phase,
            registered_at=registered_at,
            provenance=provenance,
            design=design,
            analysis=analysis,
            readiness_requirements=readiness_requirements,
            decision_policy_hash=decision_policy_hash,
            truth_table_hash=truth_table_hash,
            limitations=limitations,
            plan_hash=canonical_json_hash(content),
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "phase": self.phase,
            "registered_at": self.registered_at,
            "provenance": self.provenance.to_dict(),
            "design": self.design.to_dict(),
            "analysis": self.analysis.to_dict(),
            "readiness_requirements": list(self.readiness_requirements),
            "decision_policy_hash": self.decision_policy_hash,
            "truth_table_hash": self.truth_table_hash,
            "limitations": list(self.limitations),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "plan_hash": self.plan_hash}


@dataclass(frozen=True)
class ActivationRunManifest:
    schema_version: Literal["activation_run_manifest.v1"]
    plan_hash: str
    pair_id: str
    run_group_id: str
    arm: Literal["control", "treatment"]
    seed: int
    data_snapshot_hash: str
    mechanism_family: str
    dag_region: str
    policy_hash: str
    rng_namespace: str
    cache_namespace: str
    candidate_budget: int
    compute_budget: int
    terminal_status_counts: Mapping[str, int]
    candidate_ids: tuple[str, ...]
    effective_candidate_ids: tuple[str, ...]
    metrics: Mapping[str, float]
    started_at: str
    ended_at: str
    complete: bool
    contaminated: bool
    contamination_reasons: tuple[str, ...]
    manifest_hash: str

    @classmethod
    def create(
        cls,
        *,
        plan_hash: str,
        pair_id: str,
        run_group_id: str,
        arm: Literal["control", "treatment"],
        seed: int,
        data_snapshot_hash: str,
        mechanism_family: str,
        dag_region: str,
        policy_hash: str,
        rng_namespace: str,
        cache_namespace: str,
        candidate_budget: int,
        compute_budget: int,
        terminal_status_counts: Mapping[str, int],
        candidate_ids: tuple[str, ...],
        effective_candidate_ids: tuple[str, ...],
        metrics: Mapping[str, float],
        started_at: str,
        ended_at: str,
        complete: bool,
        contaminated: bool = False,
        contamination_reasons: tuple[str, ...] = (),
    ) -> "ActivationRunManifest":
        content = {
            "schema_version": "activation_run_manifest.v1",
            "plan_hash": plan_hash,
            "pair_id": pair_id,
            "run_group_id": run_group_id,
            "arm": arm,
            "seed": seed,
            "data_snapshot_hash": data_snapshot_hash,
            "mechanism_family": mechanism_family,
            "dag_region": dag_region,
            "policy_hash": policy_hash,
            "rng_namespace": rng_namespace,
            "cache_namespace": cache_namespace,
            "candidate_budget": candidate_budget,
            "compute_budget": compute_budget,
            "terminal_status_counts": dict(terminal_status_counts),
            "candidate_ids": list(candidate_ids),
            "effective_candidate_ids": list(effective_candidate_ids),
            "metrics": dict(metrics),
            "started_at": started_at,
            "ended_at": ended_at,
            "complete": complete,
            "contaminated": contaminated,
            "contamination_reasons": list(contamination_reasons),
        }
        return cls(
            schema_version="activation_run_manifest.v1",
            plan_hash=plan_hash,
            pair_id=pair_id,
            run_group_id=run_group_id,
            arm=arm,
            seed=seed,
            data_snapshot_hash=data_snapshot_hash,
            mechanism_family=mechanism_family,
            dag_region=dag_region,
            policy_hash=policy_hash,
            rng_namespace=rng_namespace,
            cache_namespace=cache_namespace,
            candidate_budget=candidate_budget,
            compute_budget=compute_budget,
            terminal_status_counts=terminal_status_counts,
            candidate_ids=candidate_ids,
            effective_candidate_ids=effective_candidate_ids,
            metrics=metrics,
            started_at=started_at,
            ended_at=ended_at,
            complete=complete,
            contaminated=contaminated,
            contamination_reasons=contamination_reasons,
            manifest_hash=canonical_json_hash(content),
        )

    def __post_init__(self) -> None:
        for name in ("plan_hash", "data_snapshot_hash", "policy_hash", "manifest_hash"):
            _hash(str(getattr(self, name)), name)
        if set(self.terminal_status_counts) != set(TERMINAL_STATUSES):
            raise ValueError("run manifest must count every terminal status")
        if any(isinstance(value, bool) or value < 0 for value in self.terminal_status_counts.values()):
            raise ValueError("terminal counts must be non-negative integers")
        if sum(self.terminal_status_counts.values()) != len(self.candidate_ids):
            raise ValueError("terminal counts do not cover every attempted candidate")
        if not set(self.effective_candidate_ids).issubset(self.candidate_ids):
            raise ValueError("effective candidates must be attempted candidates")
        if len(self.candidate_ids) > self.candidate_budget:
            raise ValueError("run exceeded candidate budget")
        if any(not math.isfinite(float(value)) for value in self.metrics.values()):
            raise ValueError("run metrics must be finite")
        _codes(self.contamination_reasons, "run contamination reasons")
        if self.contaminated and not self.contamination_reasons:
            raise ValueError("contaminated run requires exact reasons")
        object.__setattr__(self, "terminal_status_counts", _freeze(self.terminal_status_counts))
        object.__setattr__(self, "metrics", _freeze(self.metrics))
        if self.manifest_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("activation run manifest hash mismatch")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_hash": self.plan_hash,
            "pair_id": self.pair_id,
            "run_group_id": self.run_group_id,
            "arm": self.arm,
            "seed": self.seed,
            "data_snapshot_hash": self.data_snapshot_hash,
            "mechanism_family": self.mechanism_family,
            "dag_region": self.dag_region,
            "policy_hash": self.policy_hash,
            "rng_namespace": self.rng_namespace,
            "cache_namespace": self.cache_namespace,
            "candidate_budget": self.candidate_budget,
            "compute_budget": self.compute_budget,
            "terminal_status_counts": dict(self.terminal_status_counts),
            "candidate_ids": list(self.candidate_ids),
            "effective_candidate_ids": list(self.effective_candidate_ids),
            "metrics": dict(self.metrics),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "complete": self.complete,
            "contaminated": self.contaminated,
            "contamination_reasons": list(self.contamination_reasons),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "manifest_hash": self.manifest_hash}


@dataclass(frozen=True)
class PairedEffect:
    effect: float
    relative_effect: float | None
    ci_lower: float
    ci_upper: float
    raw_p_value: float | None = None
    holm_adjusted_p_value: float | None = None

    def __post_init__(self) -> None:
        for name in ("effect", "ci_lower", "ci_upper"):
            _finite(float(getattr(self, name)), name)
        if self.ci_lower > self.ci_upper:
            raise ValueError("paired effect confidence interval is reversed")
        for value in (self.relative_effect, self.raw_p_value, self.holm_adjusted_p_value):
            if value is not None and not math.isfinite(float(value)):
                raise ValueError("paired effect optional statistic is non-finite")

    def to_dict(self) -> dict[str, float | None]:
        return {
            "effect": self.effect,
            "relative_effect": self.relative_effect,
            "ci_lower": self.ci_lower,
            "ci_upper": self.ci_upper,
            "raw_p_value": self.raw_p_value,
            "holm_adjusted_p_value": self.holm_adjusted_p_value,
        }


@dataclass(frozen=True)
class ActivationExperimentResult:
    schema_version: Literal["activation_experiment_result.v1"]
    plan_hash: str
    complete_pairs: int
    excluded_pairs: Mapping[str, str]
    primary_effect: PairedEffect | None
    secondary_effects: Mapping[str, PairedEffect]
    noninferiority_results: Mapping[str, bool | None]
    propensity_diagnostics: Mapping[str, float]
    coverage_diagnostics: Mapping[str, float]
    resource_diagnostics: Mapping[str, float]
    effective_sample: int
    power_limitation: str | None
    invalidation_reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    replayable: bool
    result_hash: str

    @classmethod
    def create(
        cls,
        *,
        plan_hash: str,
        complete_pairs: int,
        excluded_pairs: Mapping[str, str],
        primary_effect: PairedEffect | None,
        secondary_effects: Mapping[str, PairedEffect],
        noninferiority_results: Mapping[str, bool | None],
        propensity_diagnostics: Mapping[str, float],
        coverage_diagnostics: Mapping[str, float],
        resource_diagnostics: Mapping[str, float],
        effective_sample: int,
        power_limitation: str | None,
        invalidation_reasons: tuple[str, ...] = (),
        warnings: tuple[str, ...] = (),
        replayable: bool = True,
    ) -> "ActivationExperimentResult":
        content = {
            "schema_version": "activation_experiment_result.v1",
            "plan_hash": plan_hash,
            "complete_pairs": complete_pairs,
            "excluded_pairs": dict(excluded_pairs),
            "primary_effect": None if primary_effect is None else primary_effect.to_dict(),
            "secondary_effects": {
                key: value.to_dict() for key, value in secondary_effects.items()
            },
            "noninferiority_results": dict(noninferiority_results),
            "propensity_diagnostics": dict(propensity_diagnostics),
            "coverage_diagnostics": dict(coverage_diagnostics),
            "resource_diagnostics": dict(resource_diagnostics),
            "effective_sample": effective_sample,
            "power_limitation": power_limitation,
            "invalidation_reasons": list(invalidation_reasons),
            "warnings": list(warnings),
            "replayable": replayable,
        }
        return cls(
            schema_version="activation_experiment_result.v1",
            plan_hash=plan_hash,
            complete_pairs=complete_pairs,
            excluded_pairs=excluded_pairs,
            primary_effect=primary_effect,
            secondary_effects=secondary_effects,
            noninferiority_results=noninferiority_results,
            propensity_diagnostics=propensity_diagnostics,
            coverage_diagnostics=coverage_diagnostics,
            resource_diagnostics=resource_diagnostics,
            effective_sample=effective_sample,
            power_limitation=power_limitation,
            invalidation_reasons=invalidation_reasons,
            warnings=warnings,
            replayable=replayable,
            result_hash=canonical_json_hash(content),
        )

    def __post_init__(self) -> None:
        _hash(self.plan_hash, "plan_hash")
        _hash(self.result_hash, "result_hash")
        _codes(self.invalidation_reasons, "result invalidation reasons")
        _codes(self.warnings, "result warnings")
        for mapping in (
            self.propensity_diagnostics, self.coverage_diagnostics,
            self.resource_diagnostics,
        ):
            if any(not math.isfinite(float(value)) for value in mapping.values()):
                raise ValueError("activation diagnostics must be finite")
        object.__setattr__(self, "excluded_pairs", _freeze(self.excluded_pairs))
        object.__setattr__(self, "secondary_effects", _freeze(self.secondary_effects))
        object.__setattr__(self, "noninferiority_results", _freeze(self.noninferiority_results))
        object.__setattr__(self, "propensity_diagnostics", _freeze(self.propensity_diagnostics))
        object.__setattr__(self, "coverage_diagnostics", _freeze(self.coverage_diagnostics))
        object.__setattr__(self, "resource_diagnostics", _freeze(self.resource_diagnostics))
        if self.result_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("activation result hash mismatch")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_hash": self.plan_hash,
            "complete_pairs": self.complete_pairs,
            "excluded_pairs": dict(self.excluded_pairs),
            "primary_effect": None if self.primary_effect is None else self.primary_effect.to_dict(),
            "secondary_effects": {
                key: value.to_dict() for key, value in self.secondary_effects.items()
            },
            "noninferiority_results": dict(self.noninferiority_results),
            "propensity_diagnostics": dict(self.propensity_diagnostics),
            "coverage_diagnostics": dict(self.coverage_diagnostics),
            "resource_diagnostics": dict(self.resource_diagnostics),
            "effective_sample": self.effective_sample,
            "power_limitation": self.power_limitation,
            "invalidation_reasons": list(self.invalidation_reasons),
            "warnings": list(self.warnings),
            "replayable": self.replayable,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "result_hash": self.result_hash}


@dataclass(frozen=True)
class RetrieverActivationDecision:
    schema_version: Literal["retriever_activation_decision.v1"]
    plan_hash: str
    result_hash: str
    policy_hash: str
    verdict: Literal["approved", "rejected", "inconclusive", "invalidated"]
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    active_research_only: bool
    decision_hash: str

    def __post_init__(self) -> None:
        for name in ("plan_hash", "result_hash", "policy_hash", "decision_hash"):
            _hash(str(getattr(self, name)), name)
        _codes(self.reasons, "activation decision reasons")
        _codes(self.warnings, "activation decision warnings")
        if self.active_research_only != (self.verdict == "approved"):
            raise ValueError("only an approved decision can allow research-only influence")
        if self.decision_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("activation decision hash mismatch")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_hash": self.plan_hash,
            "result_hash": self.result_hash,
            "policy_hash": self.policy_hash,
            "verdict": self.verdict,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "active_research_only": self.active_research_only,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "decision_hash": self.decision_hash}


__all__ = [
    "ActivationAnalysisPolicy", "ActivationDesign", "ActivationExperimentPlan",
    "ActivationExperimentResult", "ActivationProvenance", "ActivationRunManifest",
    "PairedEffect", "RetrieverActivationDecision", "TERMINAL_STATUSES",
]
