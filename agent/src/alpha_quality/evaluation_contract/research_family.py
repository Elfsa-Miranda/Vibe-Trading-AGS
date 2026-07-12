"""Stable research-family identity independent of run timestamps and nonces."""

from __future__ import annotations

from src.alpha_quality.evaluation_contract.model import EvaluationPolicyReferencesV1
from src.research_ledger.hash_utils import canonical_json_hash


def build_research_family_id(
    *,
    profile_template_hash: str,
    evaluation_policy_bundle_hash: str,
    producer_registry_hash: str,
    policy_references: EvaluationPolicyReferencesV1,
) -> str:
    return canonical_json_hash(
        {
            "schema_version": "research_family_identity.v1",
            "profile_template_hash": profile_template_hash,
            "evaluation_policy_bundle_hash": evaluation_policy_bundle_hash,
            "producer_registry_hash": producer_registry_hash,
            "policy_references": policy_references.to_dict(),
        }
    )
