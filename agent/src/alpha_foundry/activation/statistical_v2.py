"""Preregistered paired analysis and confirmatory planning for Activation V2."""

from __future__ import annotations

from dataclasses import dataclass
import itertools
import math
import random
from statistics import NormalDist, mean, pvariance
from typing import Any, Literal, Sequence

from src.alpha_foundry.activation.pair_projector_v2 import ActivationPairEvidenceV2
from src.alpha_foundry.activation.protocol_v2 import (
    DEFAULT_ACTIVATION_HASH_SPEC,
    CanonicalHashSpecV1,
    PreregisteredActivationStatisticalProtocolV2,
)


_ANALYSIS_AUTHORITY = object()


@dataclass(frozen=True)
class PilotDispersionEvidenceV2:
    schema_version: Literal["activation_pilot_dispersion.v2"]
    pilot_protocol_hash: str
    pair_evidence_hashes: tuple[str, ...]
    complete_pairs: int
    incomplete_pairs: int
    paired_variance: float
    conservative_variance_upper_bound: float
    zero_yield_rate: float
    completion_rate: float
    evidence_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "activation_pilot_dispersion.v2":
            raise ValueError("unsupported pilot dispersion schema")
        if self.pair_evidence_hashes != tuple(sorted(set(self.pair_evidence_hashes))):
            raise ValueError("pilot pair hashes must be sorted and unique")
        for value in (
            self.paired_variance,
            self.conservative_variance_upper_bound,
            self.zero_yield_rate,
            self.completion_rate,
        ):
            if not math.isfinite(value):
                raise ValueError("pilot dispersion contains non-finite evidence")
        expected = DEFAULT_ACTIVATION_HASH_SPEC.hash_payload(
            "activation-pilot-dispersion.v2", self._content_dict()
        )
        if self.evidence_hash != expected:
            raise ValueError("pilot dispersion hash mismatch")

    def _content_dict(self) -> dict[str, Any]:
        return {
            name: (list(self.pair_evidence_hashes) if name == "pair_evidence_hashes" else getattr(self, name))
            for name in self.__dataclass_fields__
            if name != "evidence_hash"
        }


@dataclass(frozen=True)
class PreregisteredConfirmatoryActivationPlanV2:
    schema_version: Literal["preregistered_confirmatory_activation_plan.v2"]
    research_cycle_id: str
    protocol_hash: str
    pilot_dispersion_hash: str
    fixed_sesoi: float
    alpha_level: float
    target_power: float
    required_pairs: int
    minimum_pairs: int
    maximum_pairs: int
    feasible: bool
    seeds: tuple[int, ...]
    strata: tuple[str, ...]
    arm_orders: tuple[tuple[str, str], ...]
    missing_pair_rule: str
    stopping_rule: str
    primary_test: str
    confidence_interval_method: str
    safety_resource_noninferiority_policy: str
    multiplicity_gatekeeping_policy: str
    power_engine_hash: str
    canonical_hash_spec: CanonicalHashSpecV1
    plan_hash: str

    def __post_init__(self) -> None:
        if self.required_pairs < 1 or self.minimum_pairs < 2:
            raise ValueError("confirmatory pair count is invalid")
        if self.feasible != (self.required_pairs <= self.maximum_pairs):
            raise ValueError("confirmatory feasibility must derive from maximum budget")
        if self.feasible and len(self.seeds) != self.required_pairs:
            raise ValueError("feasible confirmatory plan requires every frozen seed")
        if not self.feasible and self.seeds:
            raise ValueError("infeasible confirmatory plan cannot mint execution seeds")
        if len(self.arm_orders) != len(self.seeds) or any(
            order not in {("flat", "topology"), ("topology", "flat")}
            for order in self.arm_orders
        ):
            raise ValueError("confirmatory arm order is not fully counterbalanced")
        expected = self.canonical_hash_spec.hash_payload(
            "confirmatory-activation-plan.v2", self._content_dict()
        )
        if self.plan_hash != expected:
            raise ValueError("confirmatory plan hash mismatch")

    def _content_dict(self) -> dict[str, Any]:
        result = {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
            if name != "plan_hash"
        }
        result["seeds"] = list(self.seeds)
        result["strata"] = list(self.strata)
        result["arm_orders"] = [list(item) for item in self.arm_orders]
        result["canonical_hash_spec"] = self.canonical_hash_spec.to_dict()
        return result

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "plan_hash": self.plan_hash}


@dataclass(frozen=True, init=False)
class ActivationStatisticalAnalysisV2:
    schema_version: Literal["activation_statistical_analysis.v2"]
    protocol_hash: str
    pair_evidence_hashes: tuple[str, ...]
    complete_pairs: int
    incomplete_pairs: int
    primary_effect: float | None
    confidence_lower: float | None
    confidence_upper: float | None
    one_sided_randomization_p_value: float | None
    sesoi: float
    adequate_power: bool
    required_pair_complete: bool
    safety_noninferiority_pass: bool | None
    resource_noninferiority_pass: bool | None
    failure_noninferiority_pass: bool | None
    diversity_coverage_pass: bool | None
    source_authority_pass: bool
    protocol_violation_codes: tuple[str, ...]
    analysis_hash: str
    _authority: object

    def __init__(self, *, _authority: object, **values: Any) -> None:
        if _authority is not _ANALYSIS_AUTHORITY:
            raise TypeError("Activation analysis must be minted by the analyzer")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_authority", _authority)
        self.__post_init__()

    def __post_init__(self) -> None:
        if self.schema_version != "activation_statistical_analysis.v2":
            raise ValueError("unsupported Activation analysis schema")
        if self.protocol_violation_codes != tuple(sorted(set(self.protocol_violation_codes))):
            raise ValueError("protocol violations must be sorted and unique")
        for value in (
            self.primary_effect,
            self.confidence_lower,
            self.confidence_upper,
            self.one_sided_randomization_p_value,
        ):
            if value is not None and not math.isfinite(value):
                raise ValueError("analysis contains non-finite statistics")
        if self.analysis_hash != DEFAULT_ACTIVATION_HASH_SPEC.hash_payload(
            "activation-statistical-analysis.v2", self._content_dict()
        ):
            raise ValueError("Activation analysis hash mismatch")

    def _content_dict(self) -> dict[str, Any]:
        result = {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
            if name not in {"analysis_hash", "_authority"}
        }
        result["pair_evidence_hashes"] = list(self.pair_evidence_hashes)
        result["protocol_violation_codes"] = list(self.protocol_violation_codes)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "analysis_hash": self.analysis_hash}


class ActivationStatisticalAnalyzerV2:
    def summarize_pilot(
        self,
        *,
        protocol: PreregisteredActivationStatisticalProtocolV2,
        pairs: Sequence[ActivationPairEvidenceV2],
        preregistered_variance_floor: float,
    ) -> PilotDispersionEvidenceV2:
        self._require_pairs(protocol, pairs)
        if not math.isfinite(preregistered_variance_floor) or preregistered_variance_floor < 0:
            raise ValueError("variance floor must be frozen and non-negative")
        complete = [pair for pair in pairs if pair.source_complete]
        values = [pair.normalized_yield_difference for pair in complete]
        variance = pvariance(values) if len(values) >= 2 else 0.0
        # Conservative one-sided bound fixed before outcomes.  Pilot mean uplift
        # is intentionally absent and cannot affect SESOI or the power target.
        upper = max(preregistered_variance_floor, variance * 1.5)
        zero = (
            sum(pair.topology_qualified_yield == 0 for pair in complete) / len(complete)
            if complete
            else 1.0
        )
        content = {
            "schema_version": "activation_pilot_dispersion.v2",
            "pilot_protocol_hash": protocol.protocol_hash,
            "pair_evidence_hashes": sorted(pair.evidence_hash for pair in pairs),
            "complete_pairs": len(complete),
            "incomplete_pairs": len(pairs) - len(complete),
            "paired_variance": variance,
            "conservative_variance_upper_bound": upper,
            "zero_yield_rate": zero,
            "completion_rate": len(complete) / len(pairs),
        }
        return PilotDispersionEvidenceV2(
            schema_version="activation_pilot_dispersion.v2",
            pilot_protocol_hash=protocol.protocol_hash,
            pair_evidence_hashes=tuple(content["pair_evidence_hashes"]),  # type: ignore[arg-type]
            complete_pairs=len(complete),
            incomplete_pairs=len(pairs) - len(complete),
            paired_variance=variance,
            conservative_variance_upper_bound=upper,
            zero_yield_rate=zero,
            completion_rate=len(complete) / len(pairs),
            evidence_hash=DEFAULT_ACTIVATION_HASH_SPEC.hash_payload(
                "activation-pilot-dispersion.v2", content
            ),
        )

    def mint_confirmatory_plan(
        self,
        *,
        protocol: PreregisteredActivationStatisticalProtocolV2,
        pilot: PilotDispersionEvidenceV2,
        strata: tuple[str, ...],
    ) -> PreregisteredConfirmatoryActivationPlanV2:
        if pilot.pilot_protocol_hash != protocol.protocol_hash:
            raise ValueError("pilot and protocol differ")
        if not strata or strata != tuple(sorted(set(strata))):
            raise ValueError("confirmatory strata must be frozen and unique")
        variance = pilot.conservative_variance_upper_bound
        z_alpha = NormalDist().inv_cdf(1.0 - protocol.alpha_level)
        z_power = NormalDist().inv_cdf(protocol.target_power)
        required = max(
            protocol.minimum_pairs,
            math.ceil(((z_alpha + z_power) ** 2 * variance) / (protocol.sesoi**2)),
        )
        feasible = required <= protocol.maximum_pairs
        seeds = tuple(protocol.power_simulation_seed + index for index in range(required)) if feasible else ()
        orders = tuple(
            ("flat", "topology") if index % 2 == 0 else ("topology", "flat")
            for index in range(len(seeds))
        )
        engine = protocol.canonical_hash_spec.hash_payload(
            "activation-power-engine.v2",
            {
                "method": protocol.power_method,
                "simulation_seed": protocol.power_simulation_seed,
                "simulation_count": protocol.power_simulation_count,
                "fixed_sesoi": protocol.sesoi,
                "variance_upper_bound": variance,
            },
        )
        values: dict[str, Any] = {
            "schema_version": "preregistered_confirmatory_activation_plan.v2",
            "research_cycle_id": protocol.research_cycle_id,
            "protocol_hash": protocol.protocol_hash,
            "pilot_dispersion_hash": pilot.evidence_hash,
            "fixed_sesoi": protocol.sesoi,
            "alpha_level": protocol.alpha_level,
            "target_power": protocol.target_power,
            "required_pairs": required,
            "minimum_pairs": protocol.minimum_pairs,
            "maximum_pairs": protocol.maximum_pairs,
            "feasible": feasible,
            "seeds": seeds,
            "strata": strata,
            "arm_orders": orders,
            "missing_pair_rule": protocol.missing_pair_policy,
            "stopping_rule": "exact_required_pair_set_no_optional_stopping.v1",
            "primary_test": protocol.primary_test,
            "confidence_interval_method": protocol.confidence_interval_method,
            "safety_resource_noninferiority_policy": "closed_gate_from_protocol.v2",
            "multiplicity_gatekeeping_policy": protocol.multiplicity_or_gatekeeping_policy,
            "power_engine_hash": engine,
            "canonical_hash_spec": protocol.canonical_hash_spec,
        }
        serial = {
            **values,
            "seeds": list(seeds),
            "strata": list(strata),
            "arm_orders": [list(item) for item in orders],
            "canonical_hash_spec": protocol.canonical_hash_spec.to_dict(),
        }
        return PreregisteredConfirmatoryActivationPlanV2(
            **values,
            plan_hash=protocol.canonical_hash_spec.hash_payload(
                "confirmatory-activation-plan.v2", serial
            ),
        )

    def analyze(
        self,
        *,
        protocol: PreregisteredActivationStatisticalProtocolV2,
        registered_protocol_event_hash: str,
        pairs: Sequence[ActivationPairEvidenceV2],
        required_pairs: int,
    ) -> ActivationStatisticalAnalysisV2:
        if registered_protocol_event_hash != protocol.registration_event_hash:
            raise ValueError("analyzer requires the registered frozen protocol")
        self._require_pairs(protocol, pairs)
        complete = [pair for pair in pairs if pair.source_complete]
        values = [pair.normalized_yield_difference for pair in complete]
        violations = sorted(
            {
                code
                for pair in pairs
                for code in pair.source_failure_codes
                if code in {
                    "CONTAMINATION",
                    "REPLAY_MISMATCH",
                    "CROSS_ARM_LEAKAGE",
                    "CALLER_TRUTH",
                    "UNREGISTERED_POLICY",
                }
            }
        )
        effect = mean(values) if values else None
        lower, upper = self._bootstrap_ci(protocol, values)
        p_value = self._randomization_p(protocol, values)
        safety_ni = self._closed_gate(
            [pair.safety_noninferiority_pass for pair in complete]
        )
        resource_ni = self._closed_gate(
            [pair.resource_noninferiority_pass for pair in complete]
        )
        failure_margin = float(protocol.noninferiority_margins["failure_rate"])
        failure_ni = (
            all(
                (
                    pair.topology_failure_count / pair.candidate_budget
                    - pair.flat_failure_count / pair.candidate_budget
                )
                <= failure_margin
                for pair in complete
            )
            if complete
            else None
        )
        diversity = (
            all(pair.changed_selection_rate > 0.0 for pair in complete)
            if complete
            else None
        )
        content: dict[str, Any] = {
            "schema_version": "activation_statistical_analysis.v2",
            "protocol_hash": protocol.protocol_hash,
            "pair_evidence_hashes": sorted(pair.evidence_hash for pair in pairs),
            "complete_pairs": len(complete),
            "incomplete_pairs": len(pairs) - len(complete),
            "primary_effect": effect,
            "confidence_lower": lower,
            "confidence_upper": upper,
            "one_sided_randomization_p_value": p_value,
            "sesoi": protocol.sesoi,
            "adequate_power": len(complete) >= required_pairs,
            "required_pair_complete": len(complete) == required_pairs == len(pairs),
            "safety_noninferiority_pass": safety_ni,
            "resource_noninferiority_pass": resource_ni,
            "failure_noninferiority_pass": failure_ni,
            "diversity_coverage_pass": diversity,
            "source_authority_pass": all(pair.source_complete for pair in pairs),
            "protocol_violation_codes": violations,
        }
        return ActivationStatisticalAnalysisV2(
            _authority=_ANALYSIS_AUTHORITY,
            **{
                **content,
                "pair_evidence_hashes": tuple(content["pair_evidence_hashes"]),
                "protocol_violation_codes": tuple(violations),
                "analysis_hash": DEFAULT_ACTIVATION_HASH_SPEC.hash_payload(
                    "activation-statistical-analysis.v2", content
                ),
            },
        )

    @staticmethod
    def _require_pairs(
        protocol: PreregisteredActivationStatisticalProtocolV2,
        pairs: Sequence[ActivationPairEvidenceV2],
    ) -> None:
        if not isinstance(protocol, PreregisteredActivationStatisticalProtocolV2):
            raise TypeError("registered Activation statistical protocol is required")
        if not pairs or any(not isinstance(pair, ActivationPairEvidenceV2) for pair in pairs):
            raise TypeError("analyzer requires exact projected pair evidence")
        hashes = [pair.evidence_hash for pair in pairs]
        if len(hashes) != len(set(hashes)):
            raise ValueError("pair evidence is duplicated")

    @staticmethod
    def _randomization_p(
        protocol: PreregisteredActivationStatisticalProtocolV2,
        values: Sequence[float],
    ) -> float | None:
        if not values:
            return None
        centered = [value - protocol.sesoi for value in values]
        observed = mean(centered)
        if len(centered) <= 20:
            samples = (
                mean([sign * value for sign, value in zip(signs, centered, strict=True)])
                for signs in itertools.product((-1.0, 1.0), repeat=len(centered))
            )
            exceed = sum(sample >= observed for sample in samples)
            return exceed / (2 ** len(centered))
        rng = random.Random(protocol.power_simulation_seed)
        exceed = 0
        for _ in range(protocol.power_simulation_count):
            sample = mean([value if rng.getrandbits(1) else -value for value in centered])
            exceed += sample >= observed
        return (exceed + 1) / (protocol.power_simulation_count + 1)

    @staticmethod
    def _closed_gate(values: Sequence[bool | None]) -> bool | None:
        if any(value is False for value in values):
            return False
        if not values or any(value is None for value in values):
            return None
        return True

    @staticmethod
    def _bootstrap_ci(
        protocol: PreregisteredActivationStatisticalProtocolV2,
        values: Sequence[float],
    ) -> tuple[float | None, float | None]:
        if not values:
            return None, None
        rng = random.Random(protocol.bootstrap_seed)
        samples = sorted(
            mean([values[rng.randrange(len(values))] for _ in values])
            for _ in range(protocol.bootstrap_repetitions)
        )
        tail = (1.0 - protocol.confidence_level) / 2.0
        lower_index = max(0, math.floor(tail * (len(samples) - 1)))
        upper_index = min(
            len(samples) - 1,
            math.ceil((1.0 - tail) * (len(samples) - 1)),
        )
        return samples[lower_index], samples[upper_index]


__all__ = [
    "ActivationStatisticalAnalysisV2",
    "ActivationStatisticalAnalyzerV2",
    "PilotDispersionEvidenceV2",
    "PreregisteredConfirmatoryActivationPlanV2",
]
