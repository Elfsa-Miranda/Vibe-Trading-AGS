"""Pre-registered paired analysis for topology retriever activation."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from statistics import NormalDist, fmean, stdev
from typing import Iterable, Mapping

from src.alpha_foundry.activation.model import (
    ActivationExperimentPlan,
    ActivationExperimentResult,
    ActivationRunManifest,
    PairedEffect,
)


def holm_adjust(p_values: Mapping[str, float]) -> dict[str, float]:
    """Return deterministic Holm adjusted p-values for one frozen family."""
    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    adjusted: dict[str, float] = {}
    running = 0.0
    count = len(ordered)
    for rank, (name, value) in enumerate(ordered):
        if not 0.0 <= value <= 1.0:
            raise ValueError("p-values must be in [0, 1]")
        running = max(running, min(1.0, (count - rank) * value))
        adjusted[name] = running
    return adjusted


def _percentile(sorted_values: list[float], probability: float) -> float:
    if not sorted_values:
        raise ValueError("percentile requires observations")
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def _paired_effect(
    differences: list[float],
    controls: list[float],
    *,
    confidence_level: float,
    resamples: int,
    seed: int,
) -> PairedEffect:
    if len(differences) < 2 or len(differences) != len(controls):
        raise ValueError("paired effects require at least two aligned clusters")
    rng = random.Random(seed)
    n = len(differences)
    boot = sorted(
        fmean(differences[rng.randrange(n)] for _ in range(n))
        for _ in range(resamples)
    )
    alpha = 1.0 - confidence_level
    effect = fmean(differences)
    control_mean = fmean(controls)
    relative = None if math.isclose(control_mean, 0.0, abs_tol=1e-15) else effect / abs(control_mean)
    standard_error = stdev(differences) / math.sqrt(n)
    if math.isclose(standard_error, 0.0, abs_tol=1e-15):
        raw_p = 0.0 if not math.isclose(effect, 0.0, abs_tol=1e-15) else 1.0
    else:
        raw_p = 2.0 * (1.0 - NormalDist().cdf(abs(effect / standard_error)))
    return PairedEffect(
        effect=effect,
        relative_effect=relative,
        ci_lower=_percentile(boot, alpha / 2.0),
        ci_upper=_percentile(boot, 1.0 - alpha / 2.0),
        raw_p_value=max(0.0, min(1.0, raw_p)),
    )


class ActivationAnalyzer:
    """Analyze independent run groups; candidates and siblings are never clusters."""

    def analyze(
        self,
        plan: ActivationExperimentPlan,
        manifests: Iterable[ActivationRunManifest],
    ) -> ActivationExperimentResult:
        grouped: dict[str, dict[str, ActivationRunManifest]] = defaultdict(dict)
        invalidation: set[str] = set()
        excluded: dict[str, str] = {}
        observed = tuple(manifests)
        for manifest in observed:
            if manifest.plan_hash != plan.plan_hash:
                invalidation.add("PLAN_MANIFEST_HASH_MISMATCH")
                continue
            if manifest.run_group_id in plan.design.pilot_excluded_run_group_ids:
                invalidation.add("PILOT_GROUP_ENTERED_CONFIRMATORY_ANALYSIS")
            if manifest.run_group_id not in plan.design.run_group_ids:
                invalidation.add("UNREGISTERED_RUN_GROUP")
            if manifest.arm in grouped[manifest.pair_id]:
                invalidation.add("DUPLICATE_PAIR_ARM")
            grouped[manifest.pair_id][manifest.arm] = manifest

        pairs: list[tuple[ActivationRunManifest, ActivationRunManifest]] = []
        for pair_id in sorted(grouped):
            arms = grouped[pair_id]
            if set(arms) != {"control", "treatment"}:
                excluded[pair_id] = "MISSING_PAIR_ARM"
                invalidation.add("MISSING_PAIR_INVALIDATES_ANALYSIS")
                continue
            control, treatment = arms["control"], arms["treatment"]
            reason = self._pair_failure(plan, control, treatment)
            if reason is not None:
                excluded[pair_id] = reason
                invalidation.add(reason)
                continue
            pairs.append((control, treatment))

        expected_groups = set(plan.design.run_group_ids)
        observed_group_ids = [control.run_group_id for control, _ in pairs]
        if len(observed_group_ids) != len(set(observed_group_ids)):
            invalidation.add("RUN_GROUP_REUSED_AS_INDEPENDENT_PAIR")
        complete_groups = {control.run_group_id for control, _ in pairs}
        if complete_groups != expected_groups:
            invalidation.add("FIXED_STOPPING_SET_INCOMPLETE")

        if invalidation:
            return self.invalidated_result(
                plan,
                reasons=tuple(sorted(invalidation)),
                excluded_pairs=excluded,
                complete_pairs=len(pairs),
            )
        if len(pairs) < plan.analysis.required_independent_groups:
            return ActivationExperimentResult.create(
                plan_hash=plan.plan_hash,
                complete_pairs=len(pairs),
                excluded_pairs=excluded,
                primary_effect=None,
                secondary_effects={},
                noninferiority_results={
                    name: None for name in plan.analysis.multiple_testing_family
                },
                propensity_diagnostics=self._mean_diagnostics(pairs, "propensity_"),
                coverage_diagnostics=self._mean_diagnostics(pairs, "coverage_"),
                resource_diagnostics=self._mean_diagnostics(pairs, "resource_"),
                effective_sample=len(pairs),
                power_limitation="REQUIRED_INDEPENDENT_GROUPS_NOT_ATTAINED",
                warnings=("UNDERPOWERED_CONFIRMATORY_ANALYSIS",),
            )

        primary_controls = [float(len(control.effective_candidate_ids)) for control, _ in pairs]
        primary_diffs = [
            float(len(treatment.effective_candidate_ids) - len(control.effective_candidate_ids))
            for control, treatment in pairs
        ]
        primary = _paired_effect(
            primary_diffs,
            primary_controls,
            confidence_level=plan.analysis.confidence_level,
            resamples=plan.analysis.bootstrap_resamples,
            seed=plan.analysis.bootstrap_seed,
        )
        secondary: dict[str, PairedEffect] = {}
        for index, name in enumerate(plan.analysis.secondary_estimands):
            controls = [self._metric(control, name) for control, _ in pairs]
            differences = [
                self._metric(treatment, name) - self._metric(control, name)
                for control, treatment in pairs
            ]
            secondary[name] = _paired_effect(
                differences,
                controls,
                confidence_level=plan.analysis.confidence_level,
                resamples=plan.analysis.bootstrap_resamples,
                seed=plan.analysis.bootstrap_seed + index + 1,
            )
        family_p = {
            name: secondary[name].raw_p_value or 0.0
            for name in plan.analysis.multiple_testing_family
        }
        adjusted = holm_adjust(family_p)
        for name, adjusted_p in adjusted.items():
            item = secondary[name]
            secondary[name] = PairedEffect(
                effect=item.effect,
                relative_effect=item.relative_effect,
                ci_lower=item.ci_lower,
                ci_upper=item.ci_upper,
                raw_p_value=item.raw_p_value,
                holm_adjusted_p_value=adjusted_p,
            )
        noninferiority = {
            name: secondary[name].ci_upper <= plan.analysis.noninferiority_margins[name]
            for name in plan.analysis.multiple_testing_family
        }
        return ActivationExperimentResult.create(
            plan_hash=plan.plan_hash,
            complete_pairs=len(pairs),
            excluded_pairs=excluded,
            primary_effect=primary,
            secondary_effects=secondary,
            noninferiority_results=noninferiority,
            propensity_diagnostics=self._mean_diagnostics(pairs, "propensity_"),
            coverage_diagnostics=self._mean_diagnostics(pairs, "coverage_"),
            resource_diagnostics=self._mean_diagnostics(pairs, "resource_"),
            effective_sample=len(pairs),
            power_limitation=None,
        )

    @staticmethod
    def invalidated_result(
        plan: ActivationExperimentPlan,
        *,
        reasons: tuple[str, ...],
        excluded_pairs: Mapping[str, str] | None = None,
        complete_pairs: int = 0,
    ) -> ActivationExperimentResult:
        return ActivationExperimentResult.create(
            plan_hash=plan.plan_hash,
            complete_pairs=complete_pairs,
            excluded_pairs=excluded_pairs or {},
            primary_effect=None,
            secondary_effects={},
            noninferiority_results={
                name: None for name in plan.analysis.multiple_testing_family
            },
            propensity_diagnostics={},
            coverage_diagnostics={},
            resource_diagnostics={},
            effective_sample=complete_pairs,
            power_limitation="FORMAL_OUTCOME_ACCESS_PROHIBITED_BY_PREFLIGHT",
            invalidation_reasons=tuple(sorted(set(reasons))),
            replayable=False,
        )

    @staticmethod
    def _pair_failure(
        plan: ActivationExperimentPlan,
        control: ActivationRunManifest,
        treatment: ActivationRunManifest,
    ) -> str | None:
        if control.run_group_id != treatment.run_group_id or control.seed != treatment.seed:
            return "PAIRING_KEY_MISMATCH"
        if control.data_snapshot_hash != treatment.data_snapshot_hash:
            return "PAIR_SNAPSHOT_MISMATCH"
        if control.mechanism_family != treatment.mechanism_family or control.dag_region != treatment.dag_region:
            return "PAIR_STRATUM_MISMATCH"
        if control.candidate_budget != treatment.candidate_budget or control.compute_budget != treatment.compute_budget:
            return "PAIR_BUDGET_MISMATCH"
        if control.candidate_budget != plan.design.candidate_budget:
            return "PLAN_BUDGET_MISMATCH"
        if control.policy_hash != plan.provenance.control_policy_hash:
            return "CONTROL_POLICY_MISMATCH"
        if treatment.policy_hash != plan.provenance.treatment_policy_hash:
            return "TREATMENT_POLICY_MISMATCH"
        if control.rng_namespace == treatment.rng_namespace:
            return "RNG_NAMESPACE_NOT_ISOLATED"
        if control.cache_namespace == treatment.cache_namespace:
            return "CACHE_NAMESPACE_NOT_ISOLATED"
        if not control.complete or not treatment.complete:
            return "INCOMPLETE_PAIR_INVALIDATES_ANALYSIS"
        if control.contaminated or treatment.contaminated:
            return "CONTAMINATED_PAIR_INVALIDATES_ANALYSIS"
        return None

    @staticmethod
    def _metric(manifest: ActivationRunManifest, name: str) -> float:
        if name not in manifest.metrics:
            raise ValueError(f"run manifest lacks preregistered metric: {name}")
        return float(manifest.metrics[name])

    @staticmethod
    def _mean_diagnostics(
        pairs: list[tuple[ActivationRunManifest, ActivationRunManifest]],
        prefix: str,
    ) -> dict[str, float]:
        keys = sorted(
            {
                key
                for pair in pairs
                for manifest in pair
                for key in manifest.metrics
                if key.startswith(prefix)
            }
        )
        return {
            key.removeprefix(prefix): fmean(
                float(manifest.metrics[key])
                for pair in pairs
                for manifest in pair
                if key in manifest.metrics
            )
            for key in keys
        }


__all__ = ["ActivationAnalyzer", "holm_adjust"]
