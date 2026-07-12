"""Immutable, derived read model for the event-sourced factor lineage DAG."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Mapping


class FactorDAGError(ValueError):
    """Raised when event history cannot form an unambiguous lineage DAG."""


_FACTOR_DAG_PROJECTION_AUTHORITY = object()


@dataclass(frozen=True)
class FactorNode:
    factor_spec_id: str
    expression_id: str
    canonical_ast_hash: str
    grammar_version: str
    grammar_hash: str
    originating_trial_id: str
    definition_event_hash: str


@dataclass(frozen=True)
class RegistryRootNode:
    root_id: str
    snapshot_id: str
    alpha_id: str
    status: Literal["canonical_dsl", "legacy_opaque"]
    expression_id: str | None
    legacy_formula_hash: str
    canonical_formula: str | None
    source_hash: str | None
    source_status: Literal["available", "unavailable"]
    source_reason: str | None
    bootstrap_event_hash: str


@dataclass(frozen=True)
class DerivationEdge:
    child_factor_spec_id: str
    parent_factor_spec_ids: tuple[str, ...]
    trial_terminal_event_hash: str
    derivation_kind: Literal["mutation", "crossover", "manual_registered"]
    event_hash: str


@dataclass(frozen=True)
class SimilarityEvidence:
    """Non-lineage relation deliberately excluded from parent/child traversal."""

    left_factor_spec_id: str
    right_factor_spec_id: str
    evidence_kind: str
    evidence_hash: str


@dataclass(frozen=True, init=False)
class FactorDAGProjection:
    schema_version: Literal["factor_dag_projection.v1"]
    source_event_count: int
    source_watermark_event_hash: str | None
    source_subsequence_hash: str
    projector_policy_hash: str
    factor_nodes: Mapping[str, FactorNode]
    registry_roots: Mapping[str, RegistryRootNode]
    derivation_edges: tuple[DerivationEdge, ...]
    depth_by_factor_spec_id: Mapping[str, int]
    projection_hash: str
    _authority: object = field(init=False, repr=False, compare=False)

    def __init__(
        self,
        *,
        schema_version: Literal["factor_dag_projection.v1"],
        source_event_count: int,
        source_watermark_event_hash: str | None,
        source_subsequence_hash: str,
        projector_policy_hash: str,
        factor_nodes: Mapping[str, FactorNode],
        registry_roots: Mapping[str, RegistryRootNode],
        derivation_edges: tuple[DerivationEdge, ...],
        depth_by_factor_spec_id: Mapping[str, int],
        projection_hash: str,
        _authority: object,
    ) -> None:
        if _authority is not _FACTOR_DAG_PROJECTION_AUTHORITY:
            raise TypeError("factor DAG projection must be built by FactorDAGProjector")
        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "source_event_count", source_event_count)
        object.__setattr__(self, "source_watermark_event_hash", source_watermark_event_hash)
        object.__setattr__(self, "source_subsequence_hash", source_subsequence_hash)
        object.__setattr__(self, "projector_policy_hash", projector_policy_hash)
        object.__setattr__(self, "factor_nodes", MappingProxyType(dict(factor_nodes)))
        object.__setattr__(self, "registry_roots", MappingProxyType(dict(registry_roots)))
        object.__setattr__(
            self,
            "depth_by_factor_spec_id",
            MappingProxyType(dict(depth_by_factor_spec_id)),
        )
        object.__setattr__(self, "derivation_edges", tuple(derivation_edges))
        object.__setattr__(self, "projection_hash", projection_hash)
        object.__setattr__(self, "_authority", _authority)


def _build_factor_dag_projection(
    *,
    schema_version: Literal["factor_dag_projection.v1"],
    source_event_count: int,
    source_watermark_event_hash: str | None,
    source_subsequence_hash: str,
    projector_policy_hash: str,
    factor_nodes: Mapping[str, FactorNode],
    registry_roots: Mapping[str, RegistryRootNode],
    derivation_edges: tuple[DerivationEdge, ...],
    depth_by_factor_spec_id: Mapping[str, int],
    projection_hash: str,
) -> FactorDAGProjection:
    return FactorDAGProjection(
        schema_version=schema_version,
        source_event_count=source_event_count,
        source_watermark_event_hash=source_watermark_event_hash,
        source_subsequence_hash=source_subsequence_hash,
        projector_policy_hash=projector_policy_hash,
        factor_nodes=factor_nodes,
        registry_roots=registry_roots,
        derivation_edges=derivation_edges,
        depth_by_factor_spec_id=depth_by_factor_spec_id,
        projection_hash=projection_hash,
        _authority=_FACTOR_DAG_PROJECTION_AUTHORITY,
    )


__all__ = [
    "DerivationEdge",
    "FactorDAGError",
    "FactorDAGProjection",
    "FactorNode",
    "RegistryRootNode",
    "SimilarityEvidence",
]
