"""Exact-event pair evidence projection for Activation Enablement V2."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from src.alpha_foundry.activation.run_source_v3 import (
    FormalActivationRunSourceAuditorV3,
    FormalActivationRunSourceV3,
)
from src.research_ledger.events import ResearchEventStore
from src.research_ledger.hash_utils import canonical_json_hash


@dataclass(frozen=True)
class ActivationArmEventRefsV2:
    retrieval_authority_event_hashes: tuple[str, ...]
    terminal_event_hashes: tuple[str, ...]
    evaluation_event_hashes: tuple[str, ...]
    quality_decision_event_hashes: tuple[str, ...]
    terminal_dossier_event_hashes: tuple[str, ...]

    def __post_init__(self) -> None:
        for values in self.__dict__.values():
            if values != tuple(sorted(set(values))):
                raise ValueError("arm event refs must be sorted and unique")


@dataclass(frozen=True)
class ActivationPairEvidenceV2:
    schema_version: str
    plan_hash: str
    pair_id: str
    run_group_id: str
    candidate_budget: int
    flat_source_audit_hash: str
    topology_source_audit_hash: str
    flat_qualified_yield: int
    topology_qualified_yield: int
    normalized_yield_difference: float
    flat_terminal_count: int
    topology_terminal_count: int
    flat_failure_count: int
    topology_failure_count: int
    candidate_set_jaccard: float
    changed_selection_rate: float
    safety_noninferiority_pass: bool | None
    resource_noninferiority_pass: bool | None
    source_failure_codes: tuple[str, ...]
    source_complete: bool
    evidence_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "activation_pair_evidence.v2":
            raise ValueError("unsupported Activation pair evidence schema")
        if self.candidate_budget < 1:
            raise ValueError("pair candidate budget must be positive")
        if self.source_failure_codes != tuple(sorted(set(self.source_failure_codes))):
            raise ValueError("pair source failures must be sorted and unique")
        if self.source_complete != (not self.source_failure_codes):
            raise ValueError("pair source completeness must derive from failures")
        for value in (
            self.normalized_yield_difference,
            self.candidate_set_jaccard,
            self.changed_selection_rate,
        ):
            if not math.isfinite(value):
                raise ValueError("pair evidence contains non-finite statistics")
        if not 0.0 <= self.candidate_set_jaccard <= 1.0:
            raise ValueError("candidate-set Jaccard is invalid")
        if not 0.0 <= self.changed_selection_rate <= 1.0:
            raise ValueError("changed-selection rate is invalid")
        if self.evidence_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("pair evidence hash mismatch")

    def _content_dict(self) -> dict[str, Any]:
        return {
            name: (list(self.source_failure_codes) if name == "source_failure_codes" else getattr(self, name))
            for name in self.__dataclass_fields__
            if name != "evidence_hash"
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "evidence_hash": self.evidence_hash}


class ActivationEvidenceProjector:
    """Delegate arm authority to Run Source v3, then derive pair metrics."""

    def __init__(self, store: ResearchEventStore) -> None:
        if not isinstance(store, ResearchEventStore):
            raise TypeError("Activation projector requires ResearchEventStore")
        self.store = store
        self.run_source = FormalActivationRunSourceAuditorV3(store)

    def project_pair(
        self,
        *,
        plan_hash: str,
        pair_id: str,
        run_group_id: str,
        candidate_budget: int,
        flat_refs: ActivationArmEventRefsV2,
        topology_refs: ActivationArmEventRefsV2,
    ) -> ActivationPairEvidenceV2:
        if not isinstance(flat_refs, ActivationArmEventRefsV2) or not isinstance(
            topology_refs, ActivationArmEventRefsV2
        ):
            raise TypeError("pair projection accepts only typed exact event refs")
        flat = self._audit(
            plan_hash, pair_id, run_group_id, "flat", candidate_budget, flat_refs
        )
        topology = self._audit(
            plan_hash,
            pair_id,
            run_group_id,
            "topology",
            candidate_budget,
            topology_refs,
        )
        return self._derive(
            flat=flat,
            topology=topology,
            candidate_budget=candidate_budget,
        )

    def _audit(
        self,
        plan_hash: str,
        pair_id: str,
        run_group_id: str,
        arm: str,
        candidate_budget: int,
        refs: ActivationArmEventRefsV2,
    ) -> FormalActivationRunSourceV3:
        return self.run_source.audit(
            plan_hash=plan_hash,
            pair_id=pair_id,
            run_group_id=run_group_id,
            arm=arm,  # type: ignore[arg-type]
            candidate_budget=candidate_budget,
            retrieval_authority_event_hashes=refs.retrieval_authority_event_hashes,
            terminal_event_hashes=refs.terminal_event_hashes,
            evaluation_event_hashes=refs.evaluation_event_hashes,
            quality_decision_event_hashes=refs.quality_decision_event_hashes,
            terminal_dossier_event_hashes=refs.terminal_dossier_event_hashes,
        )

    @staticmethod
    def _derive(
        *,
        flat: FormalActivationRunSourceV3,
        topology: FormalActivationRunSourceV3,
        candidate_budget: int,
    ) -> ActivationPairEvidenceV2:
        failures = set(flat.source_failure_codes) | set(topology.source_failure_codes)
        if flat.plan_hash != topology.plan_hash or flat.pair_id != topology.pair_id:
            failures.add("PAIR_ARM_IDENTITY_MISMATCH")
        flat_candidates = set(flat.derived_candidate_ids)
        topology_candidates = set(topology.derived_candidate_ids)
        union = flat_candidates | topology_candidates
        intersection = flat_candidates & topology_candidates
        jaccard = len(intersection) / len(union) if union else 1.0
        changed = 1.0 - jaccard
        flat_yield = len(flat.derived_effective_candidate_ids)
        topology_yield = len(topology.derived_effective_candidate_ids)
        flat_counts = dict(flat.derived_terminal_status_counts)
        topology_counts = dict(topology.derived_terminal_status_counts)
        failure_names = {
            "reject",
            "skip",
            "invalid",
            "duplicate",
            "timeout",
            "error",
            "infrastructure_failure",
        }
        content = {
            "schema_version": "activation_pair_evidence.v2",
            "plan_hash": flat.plan_hash,
            "pair_id": flat.pair_id,
            "run_group_id": flat.run_group_id,
            "candidate_budget": candidate_budget,
            "flat_source_audit_hash": flat.audit_hash,
            "topology_source_audit_hash": topology.audit_hash,
            "flat_qualified_yield": flat_yield,
            "topology_qualified_yield": topology_yield,
            "normalized_yield_difference": (
                (topology_yield - flat_yield) / candidate_budget
            ),
            "flat_terminal_count": len(flat.derived_candidate_ids),
            "topology_terminal_count": len(topology.derived_candidate_ids),
            "flat_failure_count": sum(flat_counts.get(name, 0) for name in failure_names),
            "topology_failure_count": sum(
                topology_counts.get(name, 0) for name in failure_names
            ),
            "candidate_set_jaccard": jaccard,
            "changed_selection_rate": changed,
            "safety_noninferiority_pass": None,
            "resource_noninferiority_pass": None,
            "source_failure_codes": sorted(failures),
            "source_complete": not failures,
        }
        typed = dict(content)
        typed["source_failure_codes"] = tuple(content["source_failure_codes"])
        return ActivationPairEvidenceV2(
            **typed,  # type: ignore[arg-type]
            evidence_hash=canonical_json_hash(content),
        )


__all__ = [
    "ActivationArmEventRefsV2",
    "ActivationEvidenceProjector",
    "ActivationPairEvidenceV2",
]
