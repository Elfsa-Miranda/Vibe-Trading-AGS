"""Fail-closed authority gate for source-bound quality decisions."""

from __future__ import annotations

from src.alpha_quality.decision_v2.model import (
    AlphaQualityDecisionV2,
    DecisionEvidenceRefs,
)
from src.alpha_quality.decision_v2.runner import QualityDecisionV2Runner
from src.research_ledger.hash_utils import canonical_json_hash


LEGACY_EVIDENCE_CAP = "LEGACY_CALLER_CONSTRUCTABLE_EVIDENCE"
LEGACY_BOOLEAN_POLICY_CAP = "LEGACY_BOOLEAN_POLICY_AUTHORITY_UNVERIFIED"


class QualityDecisionAuthorityGateV1:
    """Prevent legacy v2 evidence records from minting a research promotion.

    DecisionEvidenceRecord.v2 remains replayable for compatibility. Because its
    public factory accepts caller-authored facts, a non-reject result is capped
    at research_only until producer-bound v3 evidence services exist.
    """

    def __init__(self, legacy_runner: QualityDecisionV2Runner) -> None:
        if not isinstance(legacy_runner, QualityDecisionV2Runner):
            raise TypeError("authority gate requires the deterministic legacy runner")
        self.legacy_runner = legacy_runner

    def run(self, refs: DecisionEvidenceRefs) -> AlphaQualityDecisionV2:
        legacy = self.legacy_runner.run(refs)
        if legacy.decision == "reject":
            return legacy
        caps = tuple(
            sorted(
                set(legacy.caps)
                | {LEGACY_EVIDENCE_CAP, LEGACY_BOOLEAN_POLICY_CAP}
            )
        )
        reasons = tuple(
            reason
            for reason in legacy.reasons
            if reason not in {
                "TERMINAL_TRAIN_VALID_EVIDENCE_QUALIFIED",
                "FROZEN_FINAL_TEST_QUALIFIED",
                "FROZEN_FORWARD_PLAN_QUALIFIED",
            }
        )
        warnings = tuple(
            sorted(
                set(legacy.warnings)
                | {"PRODUCER_BOUND_DECISION_EVIDENCE_REQUIRED"}
            )
        )
        content = {
            "schema_version": "alpha_quality_decision.v2",
            "factor_spec_id": legacy.factor_spec_id,
            "decision": "research_only",
            "tier": 1,
            "policy_version": legacy.policy_version,
            "policy_hash": legacy.policy_hash,
            "evidence_hashes": list(legacy.evidence_hashes),
            "reasons": list(reasons),
            "warnings": list(warnings),
            "caps": list(caps),
            "limitations": list(legacy.limitations),
            "within_tier_score": legacy.within_tier_score,
            "forward_success_claim": False,
        }
        return AlphaQualityDecisionV2(
            schema_version="alpha_quality_decision.v2",
            factor_spec_id=legacy.factor_spec_id,
            decision="research_only",
            tier=1,
            policy_version=legacy.policy_version,
            policy_hash=legacy.policy_hash,
            evidence_hashes=legacy.evidence_hashes,
            reasons=reasons,
            warnings=warnings,
            caps=caps,
            limitations=legacy.limitations,
            within_tier_score=legacy.within_tier_score,
            forward_success_claim=False,
            decision_hash=canonical_json_hash(content),
        )


__all__ = [
    "LEGACY_BOOLEAN_POLICY_CAP",
    "LEGACY_EVIDENCE_CAP",
    "QualityDecisionAuthorityGateV1",
]
