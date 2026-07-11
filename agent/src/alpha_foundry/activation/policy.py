"""Deterministic non-compensatory retriever activation truth table."""

from __future__ import annotations

from dataclasses import dataclass

from src.alpha_foundry.activation.model import (
    ActivationExperimentPlan,
    ActivationExperimentResult,
    RetrieverActivationDecision,
)
from src.research_ledger.hash_utils import canonical_json_hash


@dataclass(frozen=True)
class RetrieverActivationPolicy:
    schema_version: str = "retriever_activation_policy.v1"
    policy_version: str = "falsification_first.v1"

    @property
    def truth_table_hash(self) -> str:
        return canonical_json_hash(
            {
                "schema_version": "retriever_activation_truth_table.v1",
                "order": ["invalidated", "inconclusive", "rejected", "approved"],
                "approved_requires": [
                    "registered_plan", "no_contamination", "minimum_pairs",
                    "power_attained", "primary_lower_ci_above_threshold",
                    "all_noninferiority_pass", "coverage_no_collapse",
                    "propensity_explainable", "replayable", "hashes_match",
                ],
            }
        )

    @property
    def policy_hash(self) -> str:
        return canonical_json_hash(
            {
                "schema_version": self.schema_version,
                "policy_version": self.policy_version,
                "truth_table_hash": self.truth_table_hash,
            }
        )

    def decide(
        self,
        plan: ActivationExperimentPlan,
        result: ActivationExperimentResult,
    ) -> RetrieverActivationDecision:
        if plan.decision_policy_hash != self.policy_hash:
            return self._decision(plan, result, "invalidated", ("POLICY_HASH_MISMATCH",))
        if plan.truth_table_hash != self.truth_table_hash:
            return self._decision(plan, result, "invalidated", ("TRUTH_TABLE_HASH_MISMATCH",))
        if result.plan_hash != plan.plan_hash:
            return self._decision(plan, result, "invalidated", ("PLAN_RESULT_HASH_MISMATCH",))
        if result.invalidation_reasons or not result.replayable:
            reasons = result.invalidation_reasons or ("RESULT_NOT_REPLAYABLE",)
            return self._decision(plan, result, "invalidated", reasons)
        if (
            result.complete_pairs < plan.analysis.minimum_effective_pairs
            or result.complete_pairs < plan.analysis.required_independent_groups
        ):
            return self._decision(plan, result, "inconclusive", ("INSUFFICIENT_EFFECTIVE_PAIRS",))
        if result.power_limitation is not None or result.primary_effect is None:
            return self._decision(plan, result, "inconclusive", ("POWER_OR_PRIMARY_EVIDENCE_INSUFFICIENT",))
        primary = result.primary_effect
        if primary.ci_upper - primary.ci_lower > plan.analysis.maximum_ci_width:
            return self._decision(plan, result, "inconclusive", ("PRIMARY_CI_TOO_WIDE",))
        unavailable = [
            name for name in plan.analysis.multiple_testing_family
            if result.noninferiority_results.get(name) is None
        ]
        if unavailable:
            return self._decision(
                plan, result, "inconclusive", ("SECONDARY_NONINFERIORITY_UNAVAILABLE",)
            )
        failed = [
            name for name in plan.analysis.multiple_testing_family
            if result.noninferiority_results.get(name) is False
        ]
        if failed:
            return self._decision(plan, result, "rejected", ("NONINFERIORITY_BOUND_FAILED",))
        if result.coverage_diagnostics.get("coverage_collapse", 0.0) > 0.0:
            return self._decision(plan, result, "rejected", ("COVERAGE_COLLAPSE",))
        if result.propensity_diagnostics.get("unexplained_fraction", 1.0) > 0.0:
            return self._decision(plan, result, "inconclusive", ("PROPENSITY_NOT_EXPLAINABLE",))
        if primary.ci_lower > plan.analysis.primary_threshold:
            return self._decision(plan, result, "approved", ("ALL_PREREGISTERED_GATES_PASSED",))
        if primary.ci_upper <= plan.analysis.primary_threshold:
            return self._decision(plan, result, "rejected", ("PRIMARY_IMPROVEMENT_NOT_ATTAINED",))
        return self._decision(plan, result, "inconclusive", ("PRIMARY_REGION_UNRESOLVED",))

    def _decision(
        self,
        plan: ActivationExperimentPlan,
        result: ActivationExperimentResult,
        verdict: str,
        reasons: tuple[str, ...],
    ) -> RetrieverActivationDecision:
        ordered_reasons = tuple(sorted(set(reasons)))
        content = {
            "schema_version": "retriever_activation_decision.v1",
            "plan_hash": plan.plan_hash,
            "result_hash": result.result_hash,
            "policy_hash": self.policy_hash,
            "verdict": verdict,
            "reasons": list(ordered_reasons),
            "warnings": list(result.warnings),
            "active_research_only": verdict == "approved",
        }
        return RetrieverActivationDecision(
            schema_version="retriever_activation_decision.v1",
            plan_hash=plan.plan_hash,
            result_hash=result.result_hash,
            policy_hash=self.policy_hash,
            verdict=verdict,  # type: ignore[arg-type]
            reasons=ordered_reasons,
            warnings=result.warnings,
            active_research_only=verdict == "approved",
            decision_hash=canonical_json_hash(content),
        )


__all__ = ["RetrieverActivationPolicy"]
