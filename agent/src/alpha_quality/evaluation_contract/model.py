"""Closed immutable evaluation-profile and resolved-contract models."""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping

from src.research_ledger.hash_utils import canonical_json_hash


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

ClaimType = Literal[
    "canonical_factor_identity",
    "registered_evaluation_contract",
    "pit_snapshot_authority",
    "terminal_ledger_reproducibility",
    "observed_panel_predictive_association",
    "pit_scoped_predictive_signal",
    "ashare_implementability",
    "duplicate_identity",
    "novelty",
    "residual_prediction",
    "portfolio_marginal_value",
    "mechanism",
    "source_replay_completeness",
    "prefinal_eligibility",
    "final_test",
    "forward_monitoring",
]
RequirementMode = Literal[
    "tier_invariant",
    "required",
    "conditionally_required",
    "advisory",
    "not_applicable_by_rule",
]
PromotionLevel = Literal[
    "reject", "research_only", "candidate_zoo", "paper_candidate", "forward_track"
]

CLAIM_TYPES: tuple[ClaimType, ...] = (
    "canonical_factor_identity",
    "registered_evaluation_contract",
    "pit_snapshot_authority",
    "terminal_ledger_reproducibility",
    "observed_panel_predictive_association",
    "pit_scoped_predictive_signal",
    "ashare_implementability",
    "duplicate_identity",
    "novelty",
    "residual_prediction",
    "portfolio_marginal_value",
    "mechanism",
    "source_replay_completeness",
    "prefinal_eligibility",
    "final_test",
    "forward_monitoring",
)
TIER_INVARIANTS: frozenset[ClaimType] = frozenset(
    {
        "canonical_factor_identity",
        "registered_evaluation_contract",
        "pit_snapshot_authority",
        "terminal_ledger_reproducibility",
        "pit_scoped_predictive_signal",
        "ashare_implementability",
        "duplicate_identity",
        "source_replay_completeness",
    }
)
TIER_INVARIANT_MANIFEST_HASH = canonical_json_hash(
    {
        "schema_version": "evaluation_tier_invariant_manifest.v1",
        "candidate_zoo_invariants": sorted(TIER_INVARIANTS),
        "profile_override": "forbidden",
    }
)


def _require_hash(value: str, name: str) -> None:
    if _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a sha256 hash")


def _freeze_mapping(value: Mapping[str, str]) -> Mapping[str, str]:
    return MappingProxyType(dict(sorted(value.items())))


@dataclass(frozen=True)
class EvaluationProfileTemplateV1:
    schema_version: Literal["evaluation_profile_template.v1"]
    profile_id: str
    profile_version: str
    claim_requirements: Mapping[str, str]
    applicability_rule_hashes: Mapping[str, str]
    tier_invariants: frozenset[str]
    maximum_promotion: PromotionLevel
    registrar_manifest_hash: str
    producer_registry_hash: str
    authority_class: Literal["build_time_allowlisted", "custom_research_only"]
    template_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "evaluation_profile_template.v1":
            raise ValueError("unsupported evaluation profile template")
        if not self.profile_id or not self.profile_version:
            raise ValueError("profile identity must be non-empty")
        if set(self.claim_requirements) != set(CLAIM_TYPES):
            raise ValueError("profile claim requirements are not closed")
        allowed_modes = {
            "tier_invariant", "required", "conditionally_required", "advisory",
            "not_applicable_by_rule",
        }
        if any(value not in allowed_modes for value in self.claim_requirements.values()):
            raise ValueError("profile contains an unknown requirement mode")
        if self.maximum_promotion not in {
            "reject", "research_only", "candidate_zoo", "paper_candidate", "forward_track"
        }:
            raise ValueError("profile has an unknown promotion ceiling")
        if self.authority_class not in {
            "build_time_allowlisted", "custom_research_only"
        }:
            raise ValueError("profile has an unknown authority class")
        if set(self.tier_invariants) != set(TIER_INVARIANTS):
            raise ValueError("profile cannot alter registered tier invariants")
        for claim in TIER_INVARIANTS:
            if self.claim_requirements[claim] != "tier_invariant":
                raise ValueError("profile cannot disable a tier invariant")
            if claim in self.applicability_rule_hashes:
                raise ValueError("tier invariants cannot be not-applicable")
        conditional = {
            claim
            for claim, mode in self.claim_requirements.items()
            if mode in {"conditionally_required", "not_applicable_by_rule"}
        }
        if set(self.applicability_rule_hashes) != conditional:
            raise ValueError("conditional requirements need exact rule hashes")
        for value in self.applicability_rule_hashes.values():
            _require_hash(value, "applicability rule hash")
        for name in ("registrar_manifest_hash", "producer_registry_hash"):
            _require_hash(str(getattr(self, name)), name)
        if (
            self.authority_class == "custom_research_only"
            and self.maximum_promotion != "research_only"
        ):
            raise ValueError("custom profiles are capped at research_only")
        object.__setattr__(
            self, "claim_requirements", _freeze_mapping(self.claim_requirements)
        )
        object.__setattr__(
            self,
            "applicability_rule_hashes",
            _freeze_mapping(self.applicability_rule_hashes),
        )
        if self.template_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("evaluation profile template hash differs")

    @classmethod
    def build(
        cls,
        *,
        profile_id: str,
        profile_version: str,
        claim_requirements: Mapping[str, str],
        applicability_rule_hashes: Mapping[str, str],
        maximum_promotion: PromotionLevel,
        registrar_manifest_hash: str,
        producer_registry_hash: str,
        authority_class: Literal["build_time_allowlisted", "custom_research_only"],
    ) -> "EvaluationProfileTemplateV1":
        normalized_requirements = dict(sorted(claim_requirements.items()))
        normalized_rules = dict(sorted(applicability_rule_hashes.items()))
        content = {
            "schema_version": "evaluation_profile_template.v1",
            "profile_id": profile_id,
            "profile_version": profile_version,
            "claim_requirements": normalized_requirements,
            "applicability_rule_hashes": normalized_rules,
            "tier_invariants": sorted(TIER_INVARIANTS),
            "maximum_promotion": maximum_promotion,
            "registrar_manifest_hash": registrar_manifest_hash,
            "producer_registry_hash": producer_registry_hash,
            "authority_class": authority_class,
        }
        return cls(
            schema_version="evaluation_profile_template.v1",
            profile_id=profile_id,
            profile_version=profile_version,
            claim_requirements=normalized_requirements,
            applicability_rule_hashes=normalized_rules,
            tier_invariants=TIER_INVARIANTS,
            maximum_promotion=maximum_promotion,
            registrar_manifest_hash=registrar_manifest_hash,
            producer_registry_hash=producer_registry_hash,
            authority_class=authority_class,
            template_hash=canonical_json_hash(content),
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "claim_requirements": dict(self.claim_requirements),
            "applicability_rule_hashes": dict(self.applicability_rule_hashes),
            "tier_invariants": sorted(self.tier_invariants),
            "maximum_promotion": self.maximum_promotion,
            "registrar_manifest_hash": self.registrar_manifest_hash,
            "producer_registry_hash": self.producer_registry_hash,
            "authority_class": self.authority_class,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "template_hash": self.template_hash}


@dataclass(frozen=True)
class EvaluationPolicyReferencesV1:
    universe_policy_hash: str
    weighting_policy_hash: str
    execution_policy_hash: str
    missing_outcome_policy_hash: str
    complement_policy_hash: str
    inference_policy_hash: str
    multiplicity_policy_hash: str
    reporting_policy_hash: str
    executable_grammar_snapshot_hash: str
    code_manifest_hash: str

    def __post_init__(self) -> None:
        for name, value in self.to_dict().items():
            _require_hash(value, name)

    def to_dict(self) -> dict[str, str]:
        return {
            "universe_policy_hash": self.universe_policy_hash,
            "weighting_policy_hash": self.weighting_policy_hash,
            "execution_policy_hash": self.execution_policy_hash,
            "missing_outcome_policy_hash": self.missing_outcome_policy_hash,
            "complement_policy_hash": self.complement_policy_hash,
            "inference_policy_hash": self.inference_policy_hash,
            "multiplicity_policy_hash": self.multiplicity_policy_hash,
            "reporting_policy_hash": self.reporting_policy_hash,
            "executable_grammar_snapshot_hash": self.executable_grammar_snapshot_hash,
            "code_manifest_hash": self.code_manifest_hash,
        }


@dataclass(frozen=True)
class ResolvedEvaluationContractV1:
    schema_version: Literal["resolved_evaluation_contract.v1"]
    run_id: str
    research_family_id: str
    profile_template: EvaluationProfileTemplateV1
    evaluation_policy_event_hash: str
    evaluation_policy_bundle_hash: str
    producer_registry_hash: str
    policy_references: EvaluationPolicyReferencesV1
    tier_invariant_manifest_hash: str
    preregistration_watermark: str
    contract_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "resolved_evaluation_contract.v1" or not self.run_id:
            raise ValueError("invalid resolved evaluation contract identity")
        for name in (
            "research_family_id", "evaluation_policy_event_hash",
            "evaluation_policy_bundle_hash", "producer_registry_hash",
            "tier_invariant_manifest_hash", "preregistration_watermark", "contract_hash",
        ):
            _require_hash(str(getattr(self, name)), name)
        if self.producer_registry_hash != self.profile_template.producer_registry_hash:
            raise ValueError("profile and contract producer registries differ")
        if self.tier_invariant_manifest_hash != TIER_INVARIANT_MANIFEST_HASH:
            raise ValueError("resolved contract tier invariant manifest differs")
        if self.contract_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("resolved evaluation contract hash differs")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "research_family_id": self.research_family_id,
            "profile_template": self.profile_template.to_dict(),
            "evaluation_policy_event_hash": self.evaluation_policy_event_hash,
            "evaluation_policy_bundle_hash": self.evaluation_policy_bundle_hash,
            "producer_registry_hash": self.producer_registry_hash,
            "policy_references": self.policy_references.to_dict(),
            "tier_invariant_manifest_hash": self.tier_invariant_manifest_hash,
            "preregistration_watermark": self.preregistration_watermark,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "contract_hash": self.contract_hash}
