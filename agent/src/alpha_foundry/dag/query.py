"""Read-only lineage queries derived from a FactorDAGProjection."""

from __future__ import annotations

from collections.abc import Collection, Mapping

from src.alpha_foundry.dag.model import (
    DerivationEdge,
    FactorDAGProjection,
    SimilarityEvidence,
)


class FactorDAGQuery:
    def __init__(self, projection: FactorDAGProjection) -> None:
        self.projection = projection
        self._parents = {
            edge.child_factor_spec_id: edge.parent_factor_spec_ids
            for edge in projection.derivation_edges
        }
        self._children: dict[str, set[str]] = {}
        for edge in projection.derivation_edges:
            for parent in edge.parent_factor_spec_ids:
                self._children.setdefault(parent, set()).add(edge.child_factor_spec_id)

    def ancestors(self, factor_spec_id: str) -> tuple[str, ...]:
        self._require_factor(factor_spec_id)
        return self._walk(factor_spec_id, self._parents)

    def descendants(self, factor_spec_id: str) -> tuple[str, ...]:
        self._require_factor(factor_spec_id)
        return self._walk(factor_spec_id, self._children)

    def siblings(self, factor_spec_id: str) -> tuple[str, ...]:
        self._require_factor(factor_spec_id)
        siblings: set[str] = set()
        for parent in self._parents.get(factor_spec_id, ()):
            siblings.update(self._children.get(parent, ()))
        siblings.discard(factor_spec_id)
        return tuple(sorted(siblings))

    def depth(self, factor_spec_id: str) -> int:
        self._require_factor(factor_spec_id)
        return self.projection.depth_by_factor_spec_id[factor_spec_id]

    def branch_sparsity(self, factor_spec_id: str) -> float:
        self._require_factor(factor_spec_id)
        return 1.0 / (1.0 + len(self._children.get(factor_spec_id, ())))

    def lineage_edges(self) -> tuple[DerivationEdge, ...]:
        return self.projection.derivation_edges

    @staticmethod
    def accepts_similarity_as_lineage(_: SimilarityEvidence) -> bool:
        """A type-level guard: similarity evidence is never a lineage edge."""
        return False

    def _require_factor(self, factor_spec_id: str) -> None:
        if factor_spec_id not in self.projection.factor_nodes:
            raise KeyError(factor_spec_id)

    @staticmethod
    def _walk(start: str, adjacency: Mapping[str, Collection[str]]) -> tuple[str, ...]:
        pending = list(adjacency.get(start, ()))
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            pending.extend(adjacency.get(current, ()))
        return tuple(sorted(seen))


__all__ = ["FactorDAGQuery"]
