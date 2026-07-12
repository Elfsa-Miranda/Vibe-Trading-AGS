"""Build-time allowlisted evaluation profiles and producer manifests."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from src.alpha_quality.evaluation_contract.model import (
    CLAIM_TYPES,
    TIER_INVARIANTS,
    EvaluationProfileTemplateV1,
    EvaluationPolicyReferencesV1,
    PromotionLevel,
)
from src.research_ledger.hash_utils import canonical_json_hash


PRODUCER_MANIFEST = MappingProxyType(
    {
        "evaluation_policy": "evaluation_policy_registry_service.v1",
        "resolved_contract": "resolved_evaluation_contract_service.v1",
        "applicability": "applicability_assessment_service.v1",
        "pit_snapshot": "ashare_pit_snapshot_service.v2",
        "factor_identity": "factor_spec.v1",
    }
)
PRODUCER_REGISTRY_HASH = canonical_json_hash(
    {"schema_version": "evaluation_producer_registry.v1", "producers": dict(PRODUCER_MANIFEST)}
)


def _policy_hash(policy_type: str, implementation: str) -> str:
    return canonical_json_hash(
        {
            "schema_version": "evaluation_policy_reference.v1",
            "policy_type": policy_type,
            "implementation": implementation,
        }
    )


DEFAULT_POLICY_REFERENCES = EvaluationPolicyReferencesV1(
    universe_policy_hash=_policy_hash("universe", "daily_pit_membership.v1"),
    weighting_policy_hash=_policy_hash("weighting", "cross_sectional_long_only.v1"),
    execution_policy_hash=_policy_hash("execution", "ashare_stateful_execution.v1"),
    missing_outcome_policy_hash=_policy_hash("missing_outcome", "typed_scenarios.v1"),
    complement_policy_hash=_policy_hash("complement", "portfolio_complement.v2"),
    inference_policy_hash=_policy_hash("inference", "dependence_aware.v1"),
    multiplicity_policy_hash=_policy_hash("multiplicity", "holm_confirmatory.v1"),
    reporting_policy_hash=_policy_hash("reporting", "canonical_dossier.v1"),
    executable_grammar_snapshot_hash=_policy_hash("grammar", "default_grammar.v1"),
    code_manifest_hash=_policy_hash("code", "repository_source_manifest.v1"),
)
REGISTERED_POLICY_REFERENCE_BUNDLES = (
    DEFAULT_POLICY_REFERENCES,
)
POLICY_REFERENCE_REGISTRY_HASH = canonical_json_hash(
    {
        "schema_version": "evaluation_policy_reference_registry.v1",
        "bundles": [bundle.to_dict() for bundle in REGISTERED_POLICY_REFERENCE_BUNDLES],
    }
)


def validate_registered_policy_references(
    references: EvaluationPolicyReferencesV1,
) -> None:
    if references not in REGISTERED_POLICY_REFERENCE_BUNDLES:
        raise ValueError("evaluation contract policy references are not registered")
REGISTRAR_MANIFEST_HASH = canonical_json_hash(
    {
        "schema_version": "evaluation_profile_registrar_manifest.v1",
        "registry": "build_time_allowlist",
        "dynamic_code": False,
    }
)


def applicability_rule_hash(rule_id: str) -> str:
    return canonical_json_hash(
        {"schema_version": "applicability_rule.v1", "rule_id": rule_id}
    )


APPLICABILITY_RULE_IDS = (
    "mechanism_claim_declared.v1",
    "non_control_factor.v1",
)
APPLICABILITY_RULE_HASHES = frozenset(
    applicability_rule_hash(rule_id) for rule_id in APPLICABILITY_RULE_IDS
)


def _requirements() -> dict[str, str]:
    result: dict[str, str] = {claim: "advisory" for claim in CLAIM_TYPES}
    for claim in TIER_INVARIANTS:
        result[claim] = "tier_invariant"
    result.update(
        {
            "observed_panel_predictive_association": "required",
            "novelty": "required",
            "residual_prediction": "conditionally_required",
            "portfolio_marginal_value": "conditionally_required",
            "mechanism": "conditionally_required",
            "prefinal_eligibility": "required",
            "final_test": "advisory",
            "forward_monitoring": "advisory",
        }
    )
    return result


_CONDITIONAL_RULES = {
    "residual_prediction": applicability_rule_hash("non_control_factor.v1"),
    "portfolio_marginal_value": applicability_rule_hash("non_control_factor.v1"),
    "mechanism": applicability_rule_hash("mechanism_claim_declared.v1"),
}


def _build_profile(
    *, profile_id: str, profile_version: str, maximum_promotion: PromotionLevel,
    authority_class: str, requirements: Mapping[str, str] | None = None,
    rules: Mapping[str, str] | None = None,
) -> EvaluationProfileTemplateV1:
    return EvaluationProfileTemplateV1.build(
        profile_id=profile_id,
        profile_version=profile_version,
        claim_requirements=dict(_requirements() if requirements is None else requirements),
        applicability_rule_hashes=dict(_CONDITIONAL_RULES if rules is None else rules),
        maximum_promotion=maximum_promotion,
        registrar_manifest_hash=REGISTRAR_MANIFEST_HASH,
        producer_registry_hash=PRODUCER_REGISTRY_HASH,
        authority_class=authority_class,  # type: ignore[arg-type]
    )


@dataclass(frozen=True)
class EvaluationProfileRegistryV1:
    _profiles: Mapping[tuple[str, str], EvaluationProfileTemplateV1]

    @classmethod
    def build_default(cls) -> "EvaluationProfileRegistryV1":
        profiles = {
            ("production_candidate", "1"): _build_profile(
                profile_id="production_candidate", profile_version="1",
                maximum_promotion="forward_track", authority_class="build_time_allowlisted",
            ),
            ("exploratory_association", "1"): _build_profile(
                profile_id="exploratory_association", profile_version="1",
                maximum_promotion="research_only", authority_class="build_time_allowlisted",
            ),
        }
        return cls(MappingProxyType(profiles))

    def resolve_exact(self, profile_id: str, profile_version: str) -> EvaluationProfileTemplateV1:
        try:
            return self._profiles[(profile_id, profile_version)]
        except KeyError as exc:
            raise KeyError("evaluation profile exact version is not allowlisted") from exc

    def build_custom(
        self,
        *,
        profile_id: str,
        profile_version: str,
        claim_requirements: Mapping[str, str],
        applicability_rule_hashes: Mapping[str, str],
        requested_maximum_promotion: PromotionLevel,
    ) -> EvaluationProfileTemplateV1:
        del requested_maximum_promotion
        return _build_profile(
            profile_id=profile_id,
            profile_version=profile_version,
            maximum_promotion="research_only",
            authority_class="custom_research_only",
            requirements=claim_requirements,
            rules=applicability_rule_hashes,
        )


DEFAULT_PROFILE_REGISTRY = EvaluationProfileRegistryV1.build_default()
