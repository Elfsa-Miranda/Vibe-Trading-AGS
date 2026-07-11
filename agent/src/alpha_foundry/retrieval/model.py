"""Closed discovery-only models for topology retrieval shadow evaluation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from src.alpha_foundry.memory.factual import FactualMemoryView
from src.alpha_foundry.memory.model import (
    EpisodicProjection, is_authorized_episodic_projection,
)
from src.research_ledger.events.model import VerifiedEventSubsequence
from src.research_ledger.hash_utils import canonical_json_hash


_DISCOVERY_TOKEN = object()
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_OUTPUT_POINTS = 100_000
_MAX_REFERENCE_ITEMS = 256
_MAX_EMBEDDING_DIMENSION = 4_096


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, init=False)
class DiscoveryEvidenceView:
    factual: FactualMemoryView
    episodic: EpisodicProjection
    source_watermark: str
    data_snapshot_hash: str
    scope: str
    full_chain_head: str = field(compare=False)
    full_replay_hash: str = field(compare=False)
    eligible_subsequence_hash: str = field(compare=False)
    _verified_subsequence: VerifiedEventSubsequence = field(
        repr=False, compare=False
    )

    def __init__(
        self,
        *,
        factual: FactualMemoryView,
        episodic: EpisodicProjection,
        data_snapshot_hash: str,
        verified_subsequence: VerifiedEventSubsequence,
        _token: object,
    ) -> None:
        if _token is not _DISCOVERY_TOKEN:
            raise TypeError("DiscoveryEvidenceView must be built from terminal discovery projections")
        if not isinstance(factual, FactualMemoryView) or not isinstance(
            episodic, EpisodicProjection
        ):
            raise TypeError("discovery views reject monitoring or final evidence types")
        if not factual.is_authorized() or not is_authorized_episodic_projection(episodic):
            raise TypeError("discovery views require authorized projection builders")
        if (
            not isinstance(verified_subsequence, VerifiedEventSubsequence)
            or not verified_subsequence.is_authorized()
            or not verified_subsequence.events
        ):
            raise TypeError("discovery views require a store-verified event subsequence")
        dag_watermark = factual.dag.source_watermark_event_hash
        if not dag_watermark or dag_watermark != episodic.source_watermark_event_hash:
            raise ValueError("discovery projections must share one non-empty event watermark")
        if dag_watermark != verified_subsequence.events[-1].event_hash:
            raise ValueError("discovery watermark differs from its verified subsequence")
        if any(factor_id not in factual.dag.factor_nodes for factor_id in factual.factor_ids()):
            raise ValueError("discovery factual evidence is not a subset of the DAG")
        if not _HASH_RE.fullmatch(data_snapshot_hash):
            raise ValueError("discovery data snapshot must be a content hash")
        object.__setattr__(self, "factual", factual)
        object.__setattr__(self, "episodic", episodic)
        object.__setattr__(self, "source_watermark", dag_watermark)
        object.__setattr__(self, "data_snapshot_hash", data_snapshot_hash)
        object.__setattr__(self, "scope", "discovery")
        object.__setattr__(self, "full_chain_head", verified_subsequence.full_chain_head)
        object.__setattr__(self, "full_replay_hash", verified_subsequence.full_replay_hash)
        object.__setattr__(
            self, "eligible_subsequence_hash", verified_subsequence.subsequence_hash
        )
        object.__setattr__(self, "_verified_subsequence", verified_subsequence)

    @classmethod
    def from_verified_subsequence(
        cls,
        *,
        factual: FactualMemoryView,
        episodic: EpisodicProjection,
        data_snapshot_hash: str,
        verified_subsequence: VerifiedEventSubsequence,
    ) -> "DiscoveryEvidenceView":
        return cls(
            factual=factual,
            episodic=episodic,
            data_snapshot_hash=data_snapshot_hash,
            verified_subsequence=verified_subsequence,
            _token=_DISCOVERY_TOKEN,
        )

    def with_episodic_projection(
        self,
        episodic: EpisodicProjection,
    ) -> "DiscoveryEvidenceView":
        return type(self).from_verified_subsequence(
            factual=self.factual,
            episodic=episodic,
            data_snapshot_hash=self.data_snapshot_hash,
            verified_subsequence=self._verified_subsequence,
        )


@dataclass(frozen=True)
class OutputPoint:
    date: str
    symbol: str
    value: float
    valid: bool = True

    def __post_init__(self) -> None:
        if not self.date or not self.symbol or not math.isfinite(self.value):
            raise ValueError("factor output point must be named and finite")


@dataclass(frozen=True)
class FactorOutputPanel:
    factor_spec_id: str
    data_snapshot_hash: str
    data_scope: str
    points: tuple[OutputPoint, ...]
    panel_hash: str

    @classmethod
    def build(
        cls,
        factor_spec_id: str,
        points: tuple[OutputPoint, ...] | list[OutputPoint],
        *,
        data_snapshot_hash: str,
        data_scope: str = "train_valid",
    ) -> "FactorOutputPanel":
        if data_scope not in {"train", "valid", "train_valid"}:
            raise ValueError("retrieval output panel must use train/valid scope")
        ordered = tuple(sorted(points, key=lambda point: (point.date, point.symbol)))
        keys = [(point.date, point.symbol) for point in ordered]
        if not ordered or len(ordered) > _MAX_OUTPUT_POINTS or len(keys) != len(set(keys)):
            raise ValueError("factor output panel must be non-empty and uniquely keyed")
        return cls(
            factor_spec_id, data_snapshot_hash, data_scope, ordered,
            _panel_hash(factor_spec_id, data_snapshot_hash, data_scope, ordered),
        )

    def __post_init__(self) -> None:
        if self.data_scope not in {"train", "valid", "train_valid"}:
            raise ValueError("retrieval output panel must use train/valid scope")
        if not _HASH_RE.fullmatch(self.data_snapshot_hash):
            raise ValueError("factor output panel snapshot must be a content hash")
        keys = [(point.date, point.symbol) for point in self.points]
        if (
            not self.points or len(self.points) > _MAX_OUTPUT_POINTS
            or keys != sorted(keys) or len(keys) != len(set(keys))
        ):
            raise ValueError("factor output panel must be sorted and uniquely keyed")
        expected = _panel_hash(
            self.factor_spec_id, self.data_snapshot_hash, self.data_scope,
            tuple(self.points),
        )
        if expected != self.panel_hash:
            raise ValueError("factor output panel hash is invalid")


def _panel_hash(
    factor_spec_id: str,
    data_snapshot_hash: str,
    data_scope: str,
    points: tuple[OutputPoint, ...],
) -> str:
    return canonical_json_hash(
        {
            "factor_spec_id": factor_spec_id,
            "data_snapshot_hash": data_snapshot_hash,
            "data_scope": data_scope,
            "points": [
                {"date": point.date, "symbol": point.symbol, "value": point.value, "valid": point.valid}
                for point in points
            ],
        }
    )


@dataclass(frozen=True)
class SemanticEmbeddingEvidence:
    model_id: str
    model_version: str
    candidate_vector: tuple[float, ...] | None
    reference_vectors: tuple[tuple[float, ...], ...]
    embedding_hash: str
    missing_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.model_id or not self.model_version:
            raise ValueError("semantic model identity is required")
        if self.candidate_vector is None:
            if not self.missing_reason or self.reference_vectors:
                raise ValueError("missing semantic embedding requires a reason and no references")
        elif (
            self.missing_reason is not None
            or
            not self.candidate_vector
            or len(self.candidate_vector) > _MAX_EMBEDDING_DIMENSION
            or len(self.reference_vectors) > _MAX_REFERENCE_ITEMS
            or any(not math.isfinite(value) for value in self.candidate_vector)
            or any(
                len(vector) != len(self.candidate_vector)
                or not vector
                or any(not math.isfinite(value) for value in vector)
                for vector in self.reference_vectors
            )
        ):
            raise ValueError("semantic embeddings must be aligned and finite")
        content = {
            "model_id": self.model_id,
            "model_version": self.model_version,
            "candidate_vector": self.candidate_vector,
            "reference_vectors": self.reference_vectors,
            "missing_reason": self.missing_reason,
        }
        if canonical_json_hash(content) != self.embedding_hash:
            raise ValueError("semantic embedding content hash is invalid")

    @classmethod
    def build(
        cls,
        *,
        model_id: str,
        model_version: str,
        candidate_vector: tuple[float, ...] | None,
        reference_vectors: tuple[tuple[float, ...], ...] = (),
        missing_reason: str | None = None,
    ) -> "SemanticEmbeddingEvidence":
        if not model_id or not model_version:
            raise ValueError("semantic model identity is required")
        if candidate_vector is None:
            if not missing_reason:
                raise ValueError("missing semantic embedding requires a reason")
            references: tuple[tuple[float, ...], ...] = ()
        else:
            if not candidate_vector or any(not math.isfinite(value) for value in candidate_vector):
                raise ValueError("semantic embedding must be finite and non-empty")
            references = tuple(tuple(vector) for vector in reference_vectors)
            if any(
                len(vector) != len(candidate_vector)
                or not vector
                or any(not math.isfinite(value) for value in vector)
                for vector in references
            ):
                raise ValueError("semantic reference embeddings must be aligned and finite")
        content = {
            "model_id": model_id,
            "model_version": model_version,
            "candidate_vector": candidate_vector,
            "reference_vectors": references,
            "missing_reason": missing_reason,
        }
        return cls(
            model_id, model_version, candidate_vector, references,
            canonical_json_hash(content), missing_reason,
        )


@dataclass(frozen=True)
class RetrievalCandidate:
    factor_spec_id: str
    action_id: str
    motif: str
    parent_context_hash: str
    base_ledger_score: float
    output_panel: FactorOutputPanel
    reference_panels: tuple[FactorOutputPanel, ...]
    canonical_ast: Mapping[str, Any]
    reference_asts: tuple[Mapping[str, Any], ...]
    semantic: SemanticEmbeddingEvidence
    estimated_cost: float
    cost_evidence_hash: str

    def __post_init__(self) -> None:
        if self.output_panel.factor_spec_id != self.factor_spec_id:
            raise ValueError("candidate factor and output panel identity differ")
        if any(
            panel.data_snapshot_hash != self.output_panel.data_snapshot_hash
            or panel.data_scope != self.output_panel.data_scope
            for panel in self.reference_panels
        ):
            raise ValueError("retrieval output panels must share snapshot and scope")
        if len(self.reference_panels) != len(self.reference_asts):
            raise ValueError(
                "retrieval numerical and structural reference pools must align"
            )
        if (
            self.semantic.candidate_vector is not None
            and len(self.semantic.reference_vectors) != len(self.reference_panels)
        ):
            raise ValueError(
                "retrieval numerical, structural, and semantic reference pools must align"
            )
        if len(self.reference_panels) > _MAX_REFERENCE_ITEMS or len(self.reference_asts) > _MAX_REFERENCE_ITEMS:
            raise ValueError("retrieval reference pool exceeds its resource limit")
        if _ast_node_count(self.canonical_ast) > 1_024 or any(
            _ast_node_count(ast) > 1_024 for ast in self.reference_asts
        ):
            raise ValueError("retrieval AST exceeds its resource limit")
        if not self.action_id or not self.motif or not self.parent_context_hash:
            raise ValueError("retrieval action identity is required")
        if not math.isfinite(self.base_ledger_score) or self.base_ledger_score <= 0:
            raise ValueError("base ledger score must be positive and finite")
        if not math.isfinite(self.estimated_cost) or self.estimated_cost < 0:
            raise ValueError("estimated cost must be finite and non-negative")
        if not _HASH_RE.fullmatch(self.cost_evidence_hash):
            raise ValueError("cost evidence must be a content hash")
        object.__setattr__(self, "canonical_ast", _freeze(self.canonical_ast))
        object.__setattr__(self, "reference_asts", _freeze(self.reference_asts))


def _ast_node_count(node: Mapping[str, Any]) -> int:
    return 1 + sum(_ast_node_count(child) for child in node.get("args", ()))


@dataclass(frozen=True)
class RetrievalComponent:
    factor_spec_id: str
    action_id: str
    motif: str
    node_kind: str
    output_panel_hash: str
    semantic_model_id: str
    semantic_model_version: str
    semantic_embedding_hash: str
    cost_evidence_hash: str
    valdiv: float
    semdiv: float
    syndiv: float
    topology_score: float
    base_score: float
    memory_adjustment: float
    action_score: float
    confidence: float
    selected: bool
    selection_propensity: float
    warnings: tuple[str, ...]
    veto_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_spec_id": self.factor_spec_id,
            "action_id": self.action_id,
            "motif": self.motif,
            "node_kind": self.node_kind,
            "output_panel_hash": self.output_panel_hash,
            "semantic_model_id": self.semantic_model_id,
            "semantic_model_version": self.semantic_model_version,
            "semantic_embedding_hash": self.semantic_embedding_hash,
            "cost_evidence_hash": self.cost_evidence_hash,
            "valdiv": self.valdiv,
            "semdiv": self.semdiv,
            "syndiv": self.syndiv,
            "topology_score": self.topology_score,
            "base_score": self.base_score,
            "memory_adjustment": self.memory_adjustment,
            "action_score": self.action_score,
            "confidence": self.confidence,
            "selected": self.selected,
            "selection_propensity": self.selection_propensity,
            "warnings": list(self.warnings),
            "veto_reason": self.veto_reason,
        }


@dataclass(frozen=True)
class ShadowDecision:
    schema_version: str
    selected_factor_spec_ids: tuple[str, ...]
    seed: int
    policy_version: str
    policy_hash: str
    policy_config: Mapping[str, Any]
    eligible_event_watermark: str
    data_snapshot_hash: str
    candidate_budget: int
    official_output_hash: str
    propensity_semantics: str
    components: tuple[RetrievalComponent, ...]
    decision_hash: str
    shadow_only: bool = True

    def __post_init__(self) -> None:
        if canonical_json_hash(self.policy_config) != self.policy_hash:
            raise ValueError("shadow decision policy hash is invalid")
        object.__setattr__(self, "policy_config", _freeze(self.policy_config))


@dataclass(frozen=True)
class ShadowRunResult:
    official_candidate_ids: tuple[str, ...]
    decision: ShadowDecision
    official_state_before_hash: str
    official_state_after_hash: str


__all__ = [
    "DiscoveryEvidenceView", "FactorOutputPanel", "OutputPoint",
    "RetrievalCandidate", "RetrievalComponent", "SemanticEmbeddingEvidence",
    "ShadowDecision", "ShadowRunResult",
]
