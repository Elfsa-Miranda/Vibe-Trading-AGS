"""Preregistered paired analysis and confirmatory planning for Activation V2."""

from __future__ import annotations

from dataclasses import dataclass
import itertools
import json
import math
import random
from statistics import NormalDist, mean, pvariance
from typing import Any, Literal, Mapping, Sequence

from src.alpha_foundry.activation.pair_projector_v2 import (
    ActivationArmEventRefsV2,
    ActivationPairEvidenceV2,
    RecordedActivationPairEvidenceV2,
)
from src.alpha_foundry.activation.protocol_v2 import (
    DEFAULT_ACTIVATION_HASH_SPEC,
    CanonicalHashSpecV1,
    PreregisteredActivationStatisticalProtocolV2,
    RecordedActivationProtocolV2,
)
from src.research_ledger.events import (
    EventDraft,
    EventTransitionError,
    ResearchEventEnvelope,
    ResearchEventStore,
)
from src.research_ledger.events.artifacts import (
    AtomicContentAddressedArtifactWriter,
    validate_artifact_references,
)
from src.research_ledger.hash_utils import canonical_json_hash


_ANALYSIS_AUTHORITY = object()
_STATISTICAL_ARTIFACT_AUTHORITY = object()
StatisticalArtifactKindV2 = Literal["pilot", "confirmatory_plan", "analysis"]


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

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "evidence_hash": self.evidence_hash}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PilotDispersionEvidenceV2":
        if set(value) != set(cls.__dataclass_fields__) or not isinstance(
            value["pair_evidence_hashes"], list
        ):
            raise ValueError("pilot dispersion artifact has an invalid closed schema")
        return cls(
            **{
                **dict(value),
                "pair_evidence_hashes": tuple(
                    str(item) for item in value["pair_evidence_hashes"]
                ),
            }
        )


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
    conservative_variance_upper_bound: float
    power_simulation_seed: int
    power_simulation_count: int
    power_design_alternative: float
    power_design_rule: str
    estimated_power: float
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
        if (
            not math.isfinite(self.conservative_variance_upper_bound)
            or self.conservative_variance_upper_bound < 0.0
            or not 0.0 <= self.estimated_power <= 1.0
            or self.power_simulation_count < 1000
        ):
            raise ValueError("confirmatory power evidence is invalid")
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

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any]
    ) -> "PreregisteredConfirmatoryActivationPlanV2":
        if (
            set(value) != set(cls.__dataclass_fields__)
            or not isinstance(value["seeds"], list)
            or not isinstance(value["strata"], list)
            or not isinstance(value["arm_orders"], list)
            or not isinstance(value["canonical_hash_spec"], Mapping)
        ):
            raise ValueError("confirmatory plan artifact has an invalid closed schema")
        return cls(
            **{
                **dict(value),
                "seeds": tuple(int(item) for item in value["seeds"]),
                "strata": tuple(str(item) for item in value["strata"]),
                "arm_orders": tuple(
                    (str(item[0]), str(item[1])) for item in value["arm_orders"]
                ),
                "canonical_hash_spec": CanonicalHashSpecV1(
                    **dict(value["canonical_hash_spec"])
                ),
            }
        )


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

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ActivationStatisticalAnalysisV2":
        expected = {
            name for name in cls.__dataclass_fields__ if name != "_authority"
        }
        if (
            set(value) != expected
            or not isinstance(value["pair_evidence_hashes"], list)
            or not isinstance(value["protocol_violation_codes"], list)
        ):
            raise ValueError("statistical analysis artifact has an invalid closed schema")
        return cls(
            _authority=_ANALYSIS_AUTHORITY,
            **{
                **dict(value),
                "pair_evidence_hashes": tuple(
                    str(item) for item in value["pair_evidence_hashes"]
                ),
                "protocol_violation_codes": tuple(
                    str(item) for item in value["protocol_violation_codes"]
                ),
            },
        )


StatisticalArtifactValueV2 = (
    PilotDispersionEvidenceV2
    | PreregisteredConfirmatoryActivationPlanV2
    | ActivationStatisticalAnalysisV2
)
_STATISTICAL_CONFIG: dict[StatisticalArtifactKindV2, tuple[str, str, str, str, str]] = {
    "pilot": (
        "ActivationPilotDispersionV2Recorded",
        "activation_pilot_dispersion_recorded.v2",
        "activation-pilot-dispersion-v2",
        "evidence_hash",
        "application/vnd.vibe.activation-pilot-dispersion-v2+json",
    ),
    "confirmatory_plan": (
        "ActivationConfirmatoryPlanV2Registered",
        "activation_confirmatory_plan_registered.v2",
        "activation-confirmatory-plan-v2",
        "plan_hash",
        "application/vnd.vibe.activation-confirmatory-plan-v2+json",
    ),
    "analysis": (
        "ActivationStatisticalAnalysisV2Recorded",
        "activation_statistical_analysis_recorded.v2",
        "activation-statistical-analysis-v2",
        "analysis_hash",
        "application/vnd.vibe.activation-statistical-analysis-v2+json",
    ),
}


@dataclass(frozen=True, init=False)
class RecordedActivationStatisticalArtifactV2:
    artifact_kind: StatisticalArtifactKindV2
    value: StatisticalArtifactValueV2
    event: ResearchEventEnvelope
    artifact_hash: str
    artifact_ref: Mapping[str, str]
    source_event_hashes: tuple[str, ...]
    _authority: object

    def __init__(self, *, _authority: object, **values: Any) -> None:
        if _authority is not _STATISTICAL_ARTIFACT_AUTHORITY:
            raise TypeError("statistical artifacts must be analyzer-minted")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_authority", _authority)

    @property
    def semantic_hash(self) -> str:
        return str(getattr(self.value, _STATISTICAL_CONFIG[self.artifact_kind][3]))

    def require_pilot(self) -> PilotDispersionEvidenceV2:
        if self.artifact_kind != "pilot" or not isinstance(
            self.value, PilotDispersionEvidenceV2
        ):
            raise TypeError("recorded pilot dispersion evidence is required")
        return self.value

    def require_plan(self) -> PreregisteredConfirmatoryActivationPlanV2:
        if self.artifact_kind != "confirmatory_plan" or not isinstance(
            self.value, PreregisteredConfirmatoryActivationPlanV2
        ):
            raise TypeError("recorded confirmatory plan is required")
        return self.value

    def require_analysis(self) -> ActivationStatisticalAnalysisV2:
        if self.artifact_kind != "analysis" or not isinstance(
            self.value, ActivationStatisticalAnalysisV2
        ):
            raise TypeError("recorded statistical analysis is required")
        return self.value

    def verify_in(self, store: ResearchEventStore) -> StatisticalArtifactValueV2:
        if not isinstance(store, ResearchEventStore) or not store.verify_chain():
            raise EventTransitionError("statistical artifact requires a valid event chain")
        event_type, _, namespace, _, media_type = _STATISTICAL_CONFIG[
            self.artifact_kind
        ]
        events = store.query_events()
        matches = [
            event
            for event in events
            if event.event_type == event_type and event.event_hash == self.event.event_hash
        ]
        if len(matches) != 1 or matches[0] != self.event:
            raise EventTransitionError("statistical artifact is not in the analyzer store")
        event = matches[0]
        order = {candidate.event_hash: index for index, candidate in enumerate(events)}
        event_index = order[event.event_hash]
        if (
            not self.source_event_hashes
            or self.source_event_hashes != tuple(sorted(set(self.source_event_hashes)))
            or any(
                source not in order or order[source] >= event_index
                for source in self.source_event_hashes
            )
        ):
            raise EventTransitionError("statistical artifact source prefix is incomplete")
        protocol_hash = str(getattr(self.value, "protocol_hash", ""))
        if isinstance(self.value, PilotDispersionEvidenceV2):
            protocol_hash = self.value.pilot_protocol_hash
        expected_payload = {
            "statistical_artifact_id": event.entity_id,
            "artifact_kind": self.artifact_kind,
            "semantic_hash": self.semantic_hash,
            "artifact_hash": self.artifact_hash,
            "protocol_hash": protocol_hash,
            "source_event_hashes": list(self.source_event_hashes),
            "artifact_refs": [dict(self.artifact_ref)],
        }
        if event.payload_hash != canonical_json_hash(expected_payload):
            raise EventTransitionError("statistical artifact event binding differs")
        normalized = validate_artifact_references(
            store.artifact_root, [dict(self.artifact_ref)]
        )[0]
        digest = self.artifact_hash.removeprefix("sha256:")
        expected_path = f"{namespace}/{digest[:2]}/{digest}.json"
        if (
            normalized["relative_path"] != expected_path
            or normalized["media_type"] != media_type
        ):
            raise EventTransitionError("statistical artifact reference is noncanonical")

        def reject_constant(item: str) -> None:
            raise ValueError(f"non-finite statistical artifact JSON: {item}")

        def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, item in items:
                if key in result:
                    raise ValueError("duplicate statistical artifact key")
                result[key] = item
            return result

        raw = json.loads(
            store.artifact_root.joinpath(*expected_path.split("/")).read_text(
                encoding="utf-8"
            ),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
        if (
            not isinstance(raw, Mapping)
            or set(raw)
            != {
                "schema_version",
                "artifact_kind",
                "semantic_hash",
                "value",
                "artifact_hash",
            }
            or raw.get("schema_version")
            != "activation_statistical_artifact_envelope.v2"
            or raw.get("artifact_kind") != self.artifact_kind
            or raw.get("semantic_hash") != self.semantic_hash
            or raw.get("artifact_hash") != self.artifact_hash
            or canonical_json_hash(raw, exclude_keys=("artifact_hash",))
            != self.artifact_hash
            or not isinstance(raw.get("value"), Mapping)
        ):
            raise EventTransitionError("statistical artifact must be one object")
        raw_value = raw["value"]
        rebuilt: StatisticalArtifactValueV2
        if self.artifact_kind == "pilot":
            rebuilt = PilotDispersionEvidenceV2.from_mapping(raw_value)
        elif self.artifact_kind == "confirmatory_plan":
            rebuilt = PreregisteredConfirmatoryActivationPlanV2.from_mapping(raw_value)
        else:
            rebuilt = ActivationStatisticalAnalysisV2.from_mapping(raw_value)
        if rebuilt != self.value:
            raise EventTransitionError("statistical artifact replay differs")
        return rebuilt


class ActivationStatisticalAnalyzerV2:
    def __init__(self, store: ResearchEventStore) -> None:
        if not isinstance(store, ResearchEventStore):
            raise TypeError("Activation analyzer requires ResearchEventStore")
        self.store = store
        self.writer = AtomicContentAddressedArtifactWriter(
            store.artifact_root, max_bytes=2 * 1024 * 1024
        )

    def summarize_pilot(
        self,
        *,
        registered_protocol: RecordedActivationProtocolV2,
        pairs: Sequence[RecordedActivationPairEvidenceV2],
        preregistered_variance_floor: float,
    ) -> RecordedActivationStatisticalArtifactV2:
        protocol = self._protocol(registered_protocol)
        evidence = self._require_pairs(protocol, pairs)
        if not math.isfinite(preregistered_variance_floor) or preregistered_variance_floor < 0:
            raise ValueError("variance floor must be frozen and non-negative")
        complete = [pair for pair in evidence if pair.source_complete]
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
            "pair_evidence_hashes": sorted(pair.evidence_hash for pair in evidence),
            "complete_pairs": len(complete),
            "incomplete_pairs": len(evidence) - len(complete),
            "paired_variance": variance,
            "conservative_variance_upper_bound": upper,
            "zero_yield_rate": zero,
            "completion_rate": len(complete) / len(evidence),
        }
        pilot = PilotDispersionEvidenceV2(
            schema_version="activation_pilot_dispersion.v2",
            pilot_protocol_hash=protocol.protocol_hash,
            pair_evidence_hashes=tuple(content["pair_evidence_hashes"]),  # type: ignore[arg-type]
            complete_pairs=len(complete),
            incomplete_pairs=len(evidence) - len(complete),
            paired_variance=variance,
            conservative_variance_upper_bound=upper,
            zero_yield_rate=zero,
            completion_rate=len(complete) / len(evidence),
            evidence_hash=DEFAULT_ACTIVATION_HASH_SPEC.hash_payload(
                "activation-pilot-dispersion.v2", content
            ),
        )
        return self._record_statistical_artifact(
            artifact_kind="pilot",
            value=pilot,
            protocol_hash=protocol.protocol_hash,
            run_id=protocol.research_cycle_id,
            source_event_hashes=tuple(sorted(pair.event.event_hash for pair in pairs)),
        )

    def mint_confirmatory_plan(
        self,
        *,
        registered_protocol: RecordedActivationProtocolV2,
        pilot: RecordedActivationStatisticalArtifactV2,
        strata: tuple[str, ...],
    ) -> RecordedActivationStatisticalArtifactV2:
        protocol = self._protocol(registered_protocol)
        pilot.verify_in(self.store)
        pilot_evidence = pilot.require_pilot()
        if pilot_evidence.pilot_protocol_hash != protocol.protocol_hash:
            raise ValueError("pilot and protocol differ")
        if not strata or strata != tuple(sorted(set(strata))):
            raise ValueError("confirmatory strata must be frozen and unique")
        variance = pilot_evidence.conservative_variance_upper_bound
        required, estimated_power, power_curve_hash = self._simulate_required_pairs(
            protocol=protocol,
            conservative_variance=variance,
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
                "power_design_alternative": protocol.power_design_alternative,
                "power_design_rule": protocol.power_design_rule,
                "variance_upper_bound": variance,
                "alpha_level": protocol.alpha_level,
                "target_power": protocol.target_power,
                "minimum_pairs": protocol.minimum_pairs,
                "maximum_pairs": protocol.maximum_pairs,
                "power_curve_hash": power_curve_hash,
            },
        )
        values: dict[str, Any] = {
            "schema_version": "preregistered_confirmatory_activation_plan.v2",
            "research_cycle_id": protocol.research_cycle_id,
            "protocol_hash": protocol.protocol_hash,
            "pilot_dispersion_hash": pilot_evidence.evidence_hash,
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
            "conservative_variance_upper_bound": variance,
            "power_simulation_seed": protocol.power_simulation_seed,
            "power_simulation_count": protocol.power_simulation_count,
            "power_design_alternative": protocol.power_design_alternative,
            "power_design_rule": protocol.power_design_rule,
            "estimated_power": estimated_power,
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
        plan = PreregisteredConfirmatoryActivationPlanV2(
            **values,
            plan_hash=protocol.canonical_hash_spec.hash_payload(
                "confirmatory-activation-plan.v2", serial
            ),
        )
        return self._record_statistical_artifact(
            artifact_kind="confirmatory_plan",
            value=plan,
            protocol_hash=protocol.protocol_hash,
            run_id=protocol.research_cycle_id,
            source_event_hashes=(pilot.event.event_hash,),
        )

    def analyze(
        self,
        *,
        registered_protocol: RecordedActivationProtocolV2,
        confirmatory_plan: RecordedActivationStatisticalArtifactV2,
        pairs: Sequence[RecordedActivationPairEvidenceV2],
    ) -> RecordedActivationStatisticalArtifactV2:
        protocol = self._protocol(registered_protocol)
        confirmatory_plan.verify_in(self.store)
        plan = confirmatory_plan.require_plan()
        if plan.protocol_hash != protocol.protocol_hash:
            raise ValueError("confirmatory plan and protocol differ")
        if not plan.feasible:
            raise ValueError("infeasible confirmatory plan cannot be analyzed")
        required_pairs = plan.required_pairs
        evidence = self._require_pairs(protocol, pairs)
        complete = [pair for pair in evidence if pair.source_complete]
        values = [pair.normalized_yield_difference for pair in complete]
        violations = sorted(
            {
                code
                for pair in evidence
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
        safety_ni, resource_ni, failure_ni = self._exact_noninferiority(
            protocol=protocol,
            recorded_pairs=pairs,
            evidence=evidence,
        )
        diversity = (
            all(pair.changed_selection_rate > 0.0 for pair in complete)
            if complete
            else None
        )
        content: dict[str, Any] = {
            "schema_version": "activation_statistical_analysis.v2",
            "protocol_hash": protocol.protocol_hash,
            "pair_evidence_hashes": sorted(pair.evidence_hash for pair in evidence),
            "complete_pairs": len(complete),
            "incomplete_pairs": len(evidence) - len(complete),
            "primary_effect": effect,
            "confidence_lower": lower,
            "confidence_upper": upper,
            "one_sided_randomization_p_value": p_value,
            "sesoi": protocol.sesoi,
            "adequate_power": len(complete) >= required_pairs,
            "required_pair_complete": len(complete) == required_pairs == len(evidence),
            "safety_noninferiority_pass": safety_ni,
            "resource_noninferiority_pass": resource_ni,
            "failure_noninferiority_pass": failure_ni,
            "diversity_coverage_pass": diversity,
            "source_authority_pass": all(pair.source_complete for pair in evidence),
            "protocol_violation_codes": violations,
        }
        analysis = ActivationStatisticalAnalysisV2(
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
        return self._record_statistical_artifact(
            artifact_kind="analysis",
            value=analysis,
            protocol_hash=protocol.protocol_hash,
            run_id=protocol.research_cycle_id,
            source_event_hashes=tuple(
                sorted(
                    {
                        confirmatory_plan.event.event_hash,
                        *(pair.event.event_hash for pair in pairs),
                    }
                )
            ),
        )

    def _record_statistical_artifact(
        self,
        *,
        artifact_kind: StatisticalArtifactKindV2,
        value: StatisticalArtifactValueV2,
        protocol_hash: str,
        run_id: str,
        source_event_hashes: tuple[str, ...],
    ) -> RecordedActivationStatisticalArtifactV2:
        event_type, payload_schema, namespace, semantic_field, media_type = (
            _STATISTICAL_CONFIG[artifact_kind]
        )
        semantic_hash = str(getattr(value, semantic_field))
        value_payload = value.to_dict()
        envelope_content = {
            "schema_version": "activation_statistical_artifact_envelope.v2",
            "artifact_kind": artifact_kind,
            "semantic_hash": semantic_hash,
            "value": value_payload,
        }
        artifact_hash = canonical_json_hash(envelope_content)
        payload = {**envelope_content, "artifact_hash": artifact_hash}
        artifact = self.writer.write_json(
            namespace=namespace,
            payload=payload,
            schema_version="activation_statistical_artifact_envelope.v2",
            semantic_hash_field="artifact_hash",
            closed_keys=frozenset(payload),
            media_type=media_type,
        )
        sources = tuple(sorted(set(source_event_hashes)))
        if not sources:
            raise EventTransitionError("statistical artifact requires protected sources")
        identifier = f"activation-{artifact_kind.replace('_', '-')}-v2-" + semantic_hash[-24:]
        event = self.store._append_producer_event(
            EventDraft(
                event_type=event_type,
                entity_id=identifier,
                run_id=run_id,
                payload_schema_version=payload_schema,
                idempotency_key=f"activation-{artifact_kind}-v2:" + semantic_hash,
                payload={
                    "statistical_artifact_id": identifier,
                    "artifact_kind": artifact_kind,
                    "semantic_hash": semantic_hash,
                    "artifact_hash": artifact_hash,
                    "protocol_hash": protocol_hash,
                    "source_event_hashes": list(sources),
                    "artifact_refs": [artifact.reference()],
                },
            )
        )
        return RecordedActivationStatisticalArtifactV2(
            _authority=_STATISTICAL_ARTIFACT_AUTHORITY,
            artifact_kind=artifact_kind,
            value=value,
            event=event,
            artifact_hash=artifact_hash,
            artifact_ref=artifact.reference(),
            source_event_hashes=sources,
        )

    def _protocol(
        self,
        registered: RecordedActivationProtocolV2,
    ) -> PreregisteredActivationStatisticalProtocolV2:
        if not isinstance(registered, RecordedActivationProtocolV2):
            raise TypeError("analyzer requires a registry-minted frozen protocol")
        registered.verify_in(self.store)
        return registered.protocol

    @staticmethod
    def _simulate_required_pairs(
        *,
        protocol: PreregisteredActivationStatisticalProtocolV2,
        conservative_variance: float,
    ) -> tuple[int, float, str]:
        """Frozen-seed Monte Carlo power under the conservative variance model.

        The null boundary is the fixed SESOI.  The simulation mean is the
        strictly larger, preregistered design alternative; it is never the
        pilot-observed uplift.  A Wilson lower bound, rather than the raw Monte
        Carlo proportion, must reach target power.
        """

        if conservative_variance < 0.0 or not math.isfinite(conservative_variance):
            raise ValueError("conservative power variance is invalid")
        count = protocol.power_simulation_count
        rng = random.Random(protocol.power_simulation_seed)
        standard_deviation = math.sqrt(conservative_variance)
        critical_z = NormalDist().inv_cdf(1.0 - protocol.alpha_level)
        rejections = [0] * (protocol.maximum_pairs + 1)
        for _ in range(count):
            cumulative = 0.0
            for pair_count in range(1, protocol.maximum_pairs + 1):
                cumulative += rng.gauss(
                    protocol.power_design_alternative, standard_deviation
                )
                if pair_count >= protocol.minimum_pairs:
                    critical_mean = protocol.sesoi + (
                        critical_z
                        * standard_deviation
                        / math.sqrt(pair_count)
                    )
                    if cumulative / pair_count > critical_mean:
                        rejections[pair_count] += 1
        curve: list[dict[str, float | int]] = []
        required = protocol.maximum_pairs + 1
        selected_power = rejections[protocol.maximum_pairs] / count
        for pair_count in range(protocol.minimum_pairs, protocol.maximum_pairs + 1):
            raw_power = rejections[pair_count] / count
            lower = ActivationStatisticalAnalyzerV2._wilson_lower_bound(
                successes=rejections[pair_count],
                trials=count,
                confidence=0.95,
            )
            curve.append(
                {
                    "pairs": pair_count,
                    "estimated_power": raw_power,
                    "power_lower_bound": lower,
                }
            )
            if required > protocol.maximum_pairs and lower >= protocol.target_power:
                required = pair_count
                selected_power = raw_power
        curve_hash = protocol.canonical_hash_spec.hash_payload(
            "activation-power-curve.v2",
            {
                "method": protocol.power_method,
                "fixed_sesoi": protocol.sesoi,
                "power_design_alternative": protocol.power_design_alternative,
                "power_design_rule": protocol.power_design_rule,
                "conservative_variance": conservative_variance,
                "alpha_level": protocol.alpha_level,
                "target_power": protocol.target_power,
                "simulation_seed": protocol.power_simulation_seed,
                "simulation_count": count,
                "curve": curve,
            },
        )
        return required, selected_power, curve_hash

    @staticmethod
    def _wilson_lower_bound(
        *, successes: int, trials: int, confidence: float
    ) -> float:
        if trials < 1 or not 0.5 < confidence < 1.0:
            raise ValueError("power interval configuration is invalid")
        proportion = successes / trials
        z_value = NormalDist().inv_cdf(confidence)
        denominator = 1.0 + z_value**2 / trials
        center = proportion + z_value**2 / (2.0 * trials)
        radius = z_value * math.sqrt(
            proportion * (1.0 - proportion) / trials
            + z_value**2 / (4.0 * trials**2)
        )
        return max(0.0, (center - radius) / denominator)

    def _require_pairs(
        self,
        protocol: PreregisteredActivationStatisticalProtocolV2,
        pairs: Sequence[RecordedActivationPairEvidenceV2],
    ) -> tuple[ActivationPairEvidenceV2, ...]:
        if not isinstance(protocol, PreregisteredActivationStatisticalProtocolV2):
            raise TypeError("registered Activation statistical protocol is required")
        if not pairs or any(
            not isinstance(pair, RecordedActivationPairEvidenceV2) for pair in pairs
        ):
            raise TypeError("analyzer requires recorded exact projected pair evidence")
        evidence = tuple(pair.verify_in(self.store) for pair in pairs)
        hashes = [pair.evidence_hash for pair in evidence]
        if len(hashes) != len(set(hashes)):
            raise ValueError("pair evidence is duplicated")
        if (
            len({pair.plan_hash for pair in evidence}) != 1
            or len({pair.pair_id for pair in evidence}) != len(evidence)
            or len({pair.run_group_id for pair in evidence}) != len(evidence)
        ):
            raise ValueError("pair evidence does not belong to one unique paired plan")
        return evidence

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

    def _exact_noninferiority(
        self,
        *,
        protocol: PreregisteredActivationStatisticalProtocolV2,
        recorded_pairs: Sequence[RecordedActivationPairEvidenceV2],
        evidence: Sequence[ActivationPairEvidenceV2],
    ) -> tuple[bool | None, bool | None, bool | None]:
        """Rebuild NI gates from exact terminal/resource events.

        Pair projection booleans are not authority. The recorded projection
        carries the exact arm refs, and the analyzer resolves those refs in its
        own verified event store before applying the preregistered margins.
        """
        if len(recorded_pairs) != len(evidence):
            raise EventTransitionError("pair records and verified evidence differ")
        by_hash = {
            event.event_hash: event for event in self.store.query_events()
        }
        safety: list[bool | None] = []
        resources: list[bool | None] = []
        failures: list[bool | None] = []
        for recorded, pair in zip(recorded_pairs, evidence, strict=True):
            if not pair.source_complete:
                continue
            flat = self._arm_noninferiority_observation(
                refs=recorded.flat_refs,
                by_hash=by_hash,
                candidate_budget=pair.candidate_budget,
            )
            topology = self._arm_noninferiority_observation(
                refs=recorded.topology_refs,
                by_hash=by_hash,
                candidate_budget=pair.candidate_budget,
            )
            if flat is None or topology is None:
                safety.append(None)
                resources.append(None)
                failures.append(None)
                continue
            safety_results = [
                topology[endpoint] - flat[endpoint]
                <= float(protocol.noninferiority_margins[endpoint])
                for endpoint in protocol.safety_noninferiority_endpoints
            ]
            resource_results = [
                topology[endpoint] - flat[endpoint]
                <= float(protocol.noninferiority_margins[endpoint])
                for endpoint in protocol.resource_noninferiority_endpoints
            ]
            safety.append(all(safety_results))
            resources.append(all(resource_results))
            failures.append(
                topology["failure_rate"] - flat["failure_rate"]
                <= float(protocol.noninferiority_margins["failure_rate"])
            )
        return (
            self._closed_gate(safety),
            self._closed_gate(resources),
            self._closed_gate(failures),
        )

    @staticmethod
    def _arm_noninferiority_observation(
        *,
        refs: ActivationArmEventRefsV2,
        by_hash: Mapping[str, ResearchEventEnvelope],
        candidate_budget: int,
    ) -> dict[str, float] | None:
        terminals = [by_hash.get(value) for value in refs.terminal_event_hashes]
        resources = [by_hash.get(value) for value in refs.resource_event_hashes]
        if (
            candidate_budget < 1
            or any(
                event is None or event.event_type != "TrialTerminated"
                for event in terminals
            )
            or len(resources) != 1
            or resources[0] is None
            or resources[0].event_type != "ActivationResourceMeasuredV2"
            or resources[0].payload.get("source_complete") is not True
        ):
            return None
        statuses = [str(event.payload["status"]) for event in terminals if event]
        failure_statuses = {
            "reject",
            "skip",
            "invalid",
            "timeout",
            "error",
            "infrastructure_failure",
        }
        resource = resources[0]
        assert resource is not None
        resource_values = {
            "cpu_seconds": resource.payload.get("cpu_seconds"),
            "wall_seconds": resource.payload.get("wall_seconds"),
            "peak_rss_mb": resource.payload.get("peak_rss_mb"),
        }
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) < 0.0
            for value in resource_values.values()
        ):
            return None
        return {
            "cpu_seconds": float(resource_values["cpu_seconds"]),
            "duplicate_rate": statuses.count("duplicate") / candidate_budget,
            "failure_rate": sum(
                status in failure_statuses for status in statuses
            )
            / candidate_budget,
            "wall_seconds": float(resource_values["wall_seconds"]),
            "peak_rss_mb": float(resource_values["peak_rss_mb"]),
        }

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
    "RecordedActivationStatisticalArtifactV2",
]
