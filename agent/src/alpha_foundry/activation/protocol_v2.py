"""Frozen Activation V2 hash, statistical protocol, and applicability contracts.

This module owns no candidate metrics and makes no activation decision.  It is
limited to outcome-free preregistration objects and their protected ledger
registrations.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping

from src.research_ledger.events import (
    EventDraft,
    EventTransitionError,
    ResearchEventEnvelope,
    ResearchEventStore,
)
from src.research_ledger.hash_utils import canonical_json


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_DOMAIN_RE = re.compile(r"^[a-z][a-z0-9._-]{2,127}$")
_ZERO_HASH = "sha256:" + "0" * 64
_HASH_PREAMBLE = b"vibe-trading.activation.canonical-hash.v1\x00"


def _require_hash(value: str, name: str) -> None:
    if _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical sha256 hash")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _domain_hash(domain: str, payload: Mapping[str, Any]) -> str:
    if _DOMAIN_RE.fullmatch(domain) is None:
        raise ValueError("hash domain is invalid")
    material = _HASH_PREAMBLE + domain.encode("utf-8") + b"\x00"
    material += canonical_json(payload).encode("utf-8")
    return "sha256:" + hashlib.sha256(material).hexdigest()


@dataclass(frozen=True)
class CanonicalHashSpecV1:
    algorithm: Literal["sha256"]
    canonicalization_version: Literal["canonical_json.v1"]
    domain_separation_version: Literal["activation_domain.v1"]
    output_prefix: Literal["sha256:"]
    spec_hash: str

    def __post_init__(self) -> None:
        expected = _domain_hash("activation-hash-spec.v1", self._content_dict())
        if self.spec_hash != expected:
            raise ValueError("canonical hash spec hash mismatch")

    @classmethod
    def create(cls) -> "CanonicalHashSpecV1":
        content = {
            "algorithm": "sha256",
            "canonicalization_version": "canonical_json.v1",
            "domain_separation_version": "activation_domain.v1",
            "output_prefix": "sha256:",
        }
        return cls(
            algorithm="sha256",
            canonicalization_version="canonical_json.v1",
            domain_separation_version="activation_domain.v1",
            output_prefix="sha256:",
            spec_hash=_domain_hash("activation-hash-spec.v1", content),
        )

    @property
    def authentication_claim(self) -> bool:
        """A plain content hash is deliberately not an authentication claim."""

        return False

    def _content_dict(self) -> dict[str, str]:
        return {
            "algorithm": self.algorithm,
            "canonicalization_version": self.canonicalization_version,
            "domain_separation_version": self.domain_separation_version,
            "output_prefix": self.output_prefix,
        }

    def to_dict(self) -> dict[str, str]:
        return {**self._content_dict(), "spec_hash": self.spec_hash}

    def hash_payload(
        self,
        domain: str,
        payload: Mapping[str, Any],
        *,
        exclude_keys: tuple[str, ...] = (),
    ) -> str:
        material = {key: value for key, value in payload.items() if key not in exclude_keys}
        return _domain_hash(domain, material)


DEFAULT_ACTIVATION_HASH_SPEC = CanonicalHashSpecV1.create()


@dataclass(frozen=True)
class PreregisteredActivationStatisticalProtocolV2:
    schema_version: Literal["preregistered_activation_statistical_protocol.v2"]
    research_cycle_id: str
    primary_endpoint_id: str
    primary_endpoint_definition: str
    normalization_policy_hash: str
    independent_unit: Literal["run_group_pair"]
    primary_estimand: str
    null_hypothesis: str
    alternative: Literal["topology_superior"]
    primary_test: Literal["paired_randomization_sign_flip.v1"]
    alpha_level: float
    target_power: float
    sesoi: float
    sesoi_units: str
    sesoi_justification: str
    confidence_interval_method: str
    confidence_level: float
    bootstrap_unit: Literal["run_group_pair"]
    bootstrap_repetitions: int
    bootstrap_seed: int
    pilot_use_policy: str
    power_method: str
    power_design_alternative: float
    power_design_rule: str
    power_simulation_seed: int
    power_simulation_count: int
    variance_upper_bound_rule: str
    minimum_pairs: int
    maximum_pairs: int
    missing_pair_policy: str
    timeout_pair_policy: str
    contaminated_pair_policy: str
    safety_noninferiority_endpoints: tuple[str, ...]
    resource_noninferiority_endpoints: tuple[str, ...]
    noninferiority_margins: Mapping[str, float]
    multiplicity_or_gatekeeping_policy: str
    registration_event_hash: str
    code_manifest_hash: str
    canonical_hash_spec: CanonicalHashSpecV1
    protocol_hash: str

    def __post_init__(self) -> None:
        if not self.research_cycle_id or not self.primary_endpoint_id:
            raise ValueError("activation protocol identity is required")
        for value, name in (
            (self.normalization_policy_hash, "normalization policy"),
            (self.registration_event_hash, "registration watermark"),
            (self.code_manifest_hash, "code manifest"),
        ):
            _require_hash(value, name)
        if not 0.0 < self.alpha_level < 0.5:
            raise ValueError("alpha level is out of bounds")
        if not 0.5 < self.target_power < 1.0:
            raise ValueError("target power is out of bounds")
        if not 0.5 < self.confidence_level < 1.0:
            raise ValueError("confidence level is out of bounds")
        if not math.isfinite(self.sesoi) or self.sesoi <= 0.0:
            raise ValueError("SESOI must be positive and finite")
        if (
            not math.isfinite(self.power_design_alternative)
            or self.power_design_alternative <= self.sesoi
            or self.power_design_rule
            != "sesoi_plus_one_sesoi_design_margin.v1"
        ):
            raise ValueError(
                "power design alternative must be frozen above the SESOI boundary"
            )
        if self.minimum_pairs < 2 or self.maximum_pairs < self.minimum_pairs:
            raise ValueError("activation pair bounds are invalid")
        if self.bootstrap_repetitions < 1000 or self.power_simulation_count < 1000:
            raise ValueError("simulation counts are below the frozen minimum")
        for values, name in (
            (self.safety_noninferiority_endpoints, "safety endpoints"),
            (self.resource_noninferiority_endpoints, "resource endpoints"),
        ):
            if not values or values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be sorted and unique")
        margins = dict(self.noninferiority_margins)
        required = set(self.safety_noninferiority_endpoints) | set(
            self.resource_noninferiority_endpoints
        )
        if set(margins) != required or any(
            not math.isfinite(float(value)) or float(value) < 0.0
            for value in margins.values()
        ):
            raise ValueError("noninferiority margins do not match frozen endpoints")
        object.__setattr__(
            self,
            "noninferiority_margins",
            MappingProxyType({key: float(margins[key]) for key in sorted(margins)}),
        )
        expected = self.canonical_hash_spec.hash_payload(
            "activation-statistical-protocol.v2", self._content_dict()
        )
        if self.protocol_hash != expected:
            raise ValueError("activation statistical protocol hash mismatch")

    @classmethod
    def create(
        cls,
        *,
        research_cycle_id: str,
        normalization_policy_hash: str,
        registration_event_hash: str,
        code_manifest_hash: str,
        sesoi: float,
        minimum_pairs: int = 12,
        maximum_pairs: int = 128,
        hash_spec: CanonicalHashSpecV1 = DEFAULT_ACTIVATION_HASH_SPEC,
    ) -> "PreregisteredActivationStatisticalProtocolV2":
        content: dict[str, Any] = {
            "schema_version": "preregistered_activation_statistical_protocol.v2",
            "research_cycle_id": research_cycle_id,
            "primary_endpoint_id": "effective_non_duplicate_candidate_yield",
            "primary_endpoint_definition": (
                "topology minus flat source-derived qualified unique candidates "
                "normalized by the common candidate budget"
            ),
            "normalization_policy_hash": normalization_policy_hash,
            "independent_unit": "run_group_pair",
            "primary_estimand": "mean paired normalized yield difference",
            "null_hypothesis": "paired effect is less than or equal to SESOI",
            "alternative": "topology_superior",
            "primary_test": "paired_randomization_sign_flip.v1",
            "alpha_level": 0.05,
            "target_power": 0.80,
            "sesoi": float(sesoi),
            "sesoi_units": "qualified_candidates_per_common_budget",
            "sesoi_justification": "frozen practical improvement threshold before pilot outcomes",
            "confidence_interval_method": "paired_percentile_bootstrap.v1",
            "confidence_level": 0.95,
            "bootstrap_unit": "run_group_pair",
            "bootstrap_repetitions": 10000,
            "bootstrap_seed": 732451,
            "pilot_use_policy": (
                "variance_dispersion_completion_resource_fidelity_only; observed uplift forbidden"
            ),
            "power_method": "paired_gaussian_working_model_simulation.v3",
            "power_design_alternative": 2.0 * float(sesoi),
            "power_design_rule": "sesoi_plus_one_sesoi_design_margin.v1",
            "power_simulation_seed": 732452,
            "power_simulation_count": 20000,
            "variance_upper_bound_rule": "max(pilot_upper_95_variance,preregistered_variance_floor)",
            "minimum_pairs": minimum_pairs,
            "maximum_pairs": maximum_pairs,
            "missing_pair_policy": "retain_as_incomplete_and_exclude_from_complete_pair_effect",
            "timeout_pair_policy": "retain_as_timeout_and_apply_failure_noninferiority_gate",
            "contaminated_pair_policy": "invalidate_experiment",
            "safety_noninferiority_endpoints": ("duplicate_rate", "failure_rate"),
            "resource_noninferiority_endpoints": ("peak_rss_mb", "wall_seconds"),
            "noninferiority_margins": {
                "duplicate_rate": 0.05,
                "failure_rate": 0.05,
                "peak_rss_mb": 128.0,
                "wall_seconds": 30.0,
            },
            "multiplicity_or_gatekeeping_policy": (
                "primary_then_safety_resource_failure_diversity_closed_gate.v1"
            ),
            "registration_event_hash": registration_event_hash,
            "code_manifest_hash": code_manifest_hash,
            "canonical_hash_spec": hash_spec,
        }
        serial = {
            **content,
            "canonical_hash_spec": hash_spec.to_dict(),
            "noninferiority_margins": dict(content["noninferiority_margins"]),
            "safety_noninferiority_endpoints": list(
                content["safety_noninferiority_endpoints"]
            ),
            "resource_noninferiority_endpoints": list(
                content["resource_noninferiority_endpoints"]
            ),
        }
        return cls(
            **content,
            protocol_hash=hash_spec.hash_payload(
                "activation-statistical-protocol.v2", serial
            ),
        )

    def _content_dict(self) -> dict[str, Any]:
        result = {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
            if name != "protocol_hash"
        }
        result["canonical_hash_spec"] = self.canonical_hash_spec.to_dict()
        result["safety_noninferiority_endpoints"] = list(
            self.safety_noninferiority_endpoints
        )
        result["resource_noninferiority_endpoints"] = list(
            self.resource_noninferiority_endpoints
        )
        result["noninferiority_margins"] = dict(self.noninferiority_margins)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "protocol_hash": self.protocol_hash}


@dataclass(frozen=True)
class ActivationApplicabilityRuleV1:
    profile: str
    claim_type: str
    trigger_condition: str
    required_evidence_producer: str
    allowed_na_rule: str
    activation_effect: str
    execution_stage: str
    source_hash: str

    def __post_init__(self) -> None:
        if any(not getattr(self, name) for name in self.__dataclass_fields__ if name != "source_hash"):
            raise ValueError("applicability rule fields are required")
        _require_hash(self.source_hash, "applicability rule source")

    def to_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True)
class ActivationApplicabilityMatrixV1:
    schema_version: Literal["activation_applicability_matrix.v1"]
    research_cycle_id: str
    rules: tuple[ActivationApplicabilityRuleV1, ...]
    frozen_before_event_hash: str
    code_manifest_hash: str
    canonical_hash_spec: CanonicalHashSpecV1
    matrix_hash: str

    def __post_init__(self) -> None:
        _require_hash(self.frozen_before_event_hash, "matrix freeze watermark")
        _require_hash(self.code_manifest_hash, "matrix code manifest")
        identities = tuple((rule.profile, rule.claim_type) for rule in self.rules)
        if identities != tuple(sorted(set(identities))):
            raise ValueError("applicability rules must be sorted and unique")
        required_claims = {
            "pit_snapshot_authority",
            "duplicate_identity",
            "execution_economics",
            "mechanism",
            "complement",
            "final_forward",
        }
        if {rule.claim_type for rule in self.rules} != required_claims:
            raise ValueError("activation applicability matrix is incomplete")
        by_claim = {rule.claim_type: rule for rule in self.rules}
        if by_claim["pit_snapshot_authority"].allowed_na_rule != "never":
            raise ValueError("PIT snapshot authority cannot be N/A")
        if by_claim["duplicate_identity"].allowed_na_rule != "never":
            raise ValueError("duplicate identity cannot be N/A")
        if by_claim["execution_economics"].activation_effect != "candidate_zoo_required":
            raise ValueError("execution economics must gate candidate_zoo")
        if by_claim["final_forward"].activation_effect != "not_applicable_no_feedback":
            raise ValueError("final/forward evidence must not feed discovery activation")
        expected = self.canonical_hash_spec.hash_payload(
            "activation-applicability-matrix.v1", self._content_dict()
        )
        if self.matrix_hash != expected:
            raise ValueError("activation applicability matrix hash mismatch")

    @classmethod
    def create_default(
        cls,
        *,
        research_cycle_id: str,
        profile: str,
        source_hash: str,
        frozen_before_event_hash: str,
        code_manifest_hash: str,
        hash_spec: CanonicalHashSpecV1 = DEFAULT_ACTIVATION_HASH_SPEC,
    ) -> "ActivationApplicabilityMatrixV1":
        definitions = {
            "pit_snapshot_authority": (
                "all formal activation pairs",
                "AsharePITSnapshotServiceV2",
                "never",
                "formal_pair_required",
                "pre_arm",
            ),
            "duplicate_identity": (
                "every valid factor definition",
                "SecondaryEvidenceServiceV1",
                "never",
                "qualified_yield_required",
                "evaluation",
            ),
            "execution_economics": (
                "candidate_zoo endpoint",
                "ExecutionEvidenceServiceV1",
                "producer_not_applicable_only",
                "candidate_zoo_required",
                "evaluation",
            ),
            "mechanism": (
                "resolved profile declares mechanism",
                "ApplicabilityAssessmentServiceV1",
                "producer_not_applicable_only",
                "profile_conditional",
                "evaluation",
            ),
            "complement": (
                "resolved target profile",
                "SecondaryEvidenceServiceV1",
                "producer_not_applicable_only",
                "profile_required_or_advisory",
                "evaluation",
            ),
            "final_forward": (
                "discovery activation cycle",
                "FinalForwardIsolationPolicyV1",
                "always_for_discovery_activation",
                "not_applicable_no_feedback",
                "forbidden",
            ),
        }
        rules = tuple(
            ActivationApplicabilityRuleV1(
                profile=profile,
                claim_type=claim,
                trigger_condition=values[0],
                required_evidence_producer=values[1],
                allowed_na_rule=values[2],
                activation_effect=values[3],
                execution_stage=values[4],
                source_hash=source_hash,
            )
            for claim, values in sorted(definitions.items())
        )
        content = {
            "schema_version": "activation_applicability_matrix.v1",
            "research_cycle_id": research_cycle_id,
            "rules": [rule.to_dict() for rule in rules],
            "frozen_before_event_hash": frozen_before_event_hash,
            "code_manifest_hash": code_manifest_hash,
            "canonical_hash_spec": hash_spec.to_dict(),
        }
        return cls(
            schema_version="activation_applicability_matrix.v1",
            research_cycle_id=research_cycle_id,
            rules=rules,
            frozen_before_event_hash=frozen_before_event_hash,
            code_manifest_hash=code_manifest_hash,
            canonical_hash_spec=hash_spec,
            matrix_hash=hash_spec.hash_payload(
                "activation-applicability-matrix.v1", content
            ),
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "research_cycle_id": self.research_cycle_id,
            "rules": [rule.to_dict() for rule in self.rules],
            "frozen_before_event_hash": self.frozen_before_event_hash,
            "code_manifest_hash": self.code_manifest_hash,
            "canonical_hash_spec": self.canonical_hash_spec.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "matrix_hash": self.matrix_hash}


_RECORDED_PROTOCOL_AUTHORITY = object()


@dataclass(frozen=True, init=False)
class RecordedActivationProtocolV2:
    protocol: PreregisteredActivationStatisticalProtocolV2
    event: ResearchEventEnvelope
    _authority: object

    def __init__(
        self,
        *,
        protocol: PreregisteredActivationStatisticalProtocolV2,
        event: ResearchEventEnvelope,
        _authority: object,
    ) -> None:
        if _authority is not _RECORDED_PROTOCOL_AUTHORITY:
            raise TypeError("registered Activation protocols are registry-minted")
        object.__setattr__(self, "protocol", protocol)
        object.__setattr__(self, "event", event)
        object.__setattr__(self, "_authority", _authority)
        if event.event_type != "ActivationStatisticalProtocolV2Registered":
            raise ValueError("registered Activation protocol event type differs")
        if (
            event.payload.get("protocol_hash") != protocol.protocol_hash
            or event.payload.get("research_cycle_id") != protocol.research_cycle_id
            or canonical_json(_plain(event.payload.get("protocol")))
            != canonical_json(protocol.to_dict())
        ):
            raise ValueError("registered Activation protocol payload differs")

    def verify_in(self, store: ResearchEventStore) -> None:
        """Prove this registry-minted protocol still belongs to the exact chain."""

        if not isinstance(store, ResearchEventStore) or not store.verify_chain():
            raise EventTransitionError("registered protocol requires a valid event chain")
        matches = [
            event
            for event in store.query_events(
                event_type="ActivationStatisticalProtocolV2Registered"
            )
            if event.event_hash == self.event.event_hash
        ]
        if len(matches) != 1 or canonical_json(_plain(matches[0].to_dict())) != canonical_json(
            _plain(self.event.to_dict())
        ):
            raise EventTransitionError(
                "registered Activation protocol is not in the analyzer event store"
            )


@dataclass(frozen=True)
class RecordedActivationApplicabilityMatrixV1:
    matrix: ActivationApplicabilityMatrixV1
    event: ResearchEventEnvelope


class ActivationProtocolRegistryV2:
    """Protected, append-only preregistration surface."""

    def __init__(self, store: ResearchEventStore) -> None:
        if not isinstance(store, ResearchEventStore):
            raise TypeError("activation protocol registry requires ResearchEventStore")
        required = (
            "VIBE_TRADING_ALPHA_FOUNDRY",
            "VIBE_TRADING_RESEARCH_EVENTS",
            "VIBE_TRADING_TOPOLOGY_RETRIEVER",
        )
        if any(not store.flags.enabled(name) for name in required):
            raise RuntimeError("activation protocol registry capability is disabled")
        self.store = store

    def register_protocol(
        self,
        *,
        run_id: str,
        protocol: PreregisteredActivationStatisticalProtocolV2,
    ) -> RecordedActivationProtocolV2:
        self._require_pre_outcome(run_id, protocol.registration_event_hash)
        protocol_id = "activation-protocol-" + protocol.protocol_hash[-24:]
        event = self.store._append_producer_event(
            EventDraft(
                event_type="ActivationStatisticalProtocolV2Registered",
                entity_id=protocol_id,
                run_id=run_id,
                payload_schema_version="activation_statistical_protocol_registered.v2",
                idempotency_key="activation-protocol-v2:" + protocol.research_cycle_id,
                payload={
                    "protocol_id": protocol_id,
                    "research_cycle_id": protocol.research_cycle_id,
                    "protocol_hash": protocol.protocol_hash,
                    "registration_event_hash": protocol.registration_event_hash,
                    "code_manifest_hash": protocol.code_manifest_hash,
                    "canonical_hash_spec": protocol.canonical_hash_spec.to_dict(),
                    "protocol": protocol.to_dict(),
                },
            )
        )
        return RecordedActivationProtocolV2(
            protocol=protocol,
            event=event,
            _authority=_RECORDED_PROTOCOL_AUTHORITY,
        )

    def register_applicability_matrix(
        self,
        *,
        run_id: str,
        matrix: ActivationApplicabilityMatrixV1,
    ) -> RecordedActivationApplicabilityMatrixV1:
        self._require_pre_outcome(run_id, matrix.frozen_before_event_hash)
        protocols = [
            event
            for event in self.store.query_events(
                event_type="ActivationStatisticalProtocolV2Registered"
            )
            if event.run_id == run_id
            and event.payload["research_cycle_id"] == matrix.research_cycle_id
        ]
        if len(protocols) != 1:
            raise EventTransitionError("applicability matrix requires one registered protocol")
        matrix_id = "activation-applicability-" + matrix.matrix_hash[-24:]
        event = self.store._append_producer_event(
            EventDraft(
                event_type="ActivationApplicabilityMatrixV1Registered",
                entity_id=matrix_id,
                run_id=run_id,
                payload_schema_version="activation_applicability_matrix_registered.v1",
                idempotency_key="activation-applicability-v1:" + matrix.research_cycle_id,
                payload={
                    "matrix_id": matrix_id,
                    "research_cycle_id": matrix.research_cycle_id,
                    "matrix_hash": matrix.matrix_hash,
                    "frozen_before_event_hash": matrix.frozen_before_event_hash,
                    "code_manifest_hash": matrix.code_manifest_hash,
                    "canonical_hash_spec": matrix.canonical_hash_spec.to_dict(),
                    "matrix": matrix.to_dict(),
                },
            )
        )
        return RecordedActivationApplicabilityMatrixV1(matrix, event)

    def _require_pre_outcome(self, run_id: str, watermark: str) -> None:
        events = self.store.query_events()
        if watermark == _ZERO_HASH:
            if events:
                raise EventTransitionError("genesis registration watermark requires an empty stream")
        elif watermark not in {event.event_hash for event in events}:
            raise EventTransitionError("registration watermark is not in the verified event stream")
        if any(
            event.run_id == run_id
            and event.event_type
            in {"TrialStarted", "EvaluationRecorded", "TrialTerminated", "ActivationRunRecorded"}
            for event in events
        ):
            raise EventTransitionError("activation contract must be frozen before outcome access")


__all__ = [
    "ActivationApplicabilityMatrixV1",
    "ActivationApplicabilityRuleV1",
    "ActivationProtocolRegistryV2",
    "CanonicalHashSpecV1",
    "DEFAULT_ACTIVATION_HASH_SPEC",
    "PreregisteredActivationStatisticalProtocolV2",
    "RecordedActivationApplicabilityMatrixV1",
    "RecordedActivationProtocolV2",
]
