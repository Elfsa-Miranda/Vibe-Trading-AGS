"""Deterministic output, semantic, structural and topology retrieval features."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from src.alpha_foundry.dag.query import FactorDAGQuery
from src.alpha_foundry.memory.model import EpisodicProjection
from src.alpha_foundry.retrieval.model import RetrievalCandidate
from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy, RetrieverPolicy


@dataclass(frozen=True)
class RetrievalFeatures:
    node_kind: str
    valdiv: float
    semdiv: float
    syndiv: float
    topology_score: float
    effective_output_observations: int
    independent_child_groups: int
    child_gain: float | None
    uncertainty: float | None
    warnings: tuple[str, ...]


def build_retrieval_features(
    candidate: RetrievalCandidate,
    *,
    query: FactorDAGQuery,
    episodic: EpisodicProjection,
    policy: RetrieverPolicy | ActivationRetrieverPolicy | None = None,
) -> RetrievalFeatures:
    activation = policy if isinstance(policy, ActivationRetrieverPolicy) else None
    descendants = query.descendants(candidate.factor_spec_id)
    if not descendants:
        valdiv, effective, output_warnings = output_diversity(
            candidate,
            minimum_alignment=(
                3 if activation is None else activation.minimum_aligned_output_observations
            ),
            missing_fallback=(
                0.0 if activation is None else activation.missing_output_diversity
            ),
        )
        semdiv, semantic_warnings = semantic_diversity(
            candidate,
            missing_fallback=(
                0.0 if activation is None else activation.missing_semantic_diversity
            ),
        )
        syndiv, structural_warnings = structural_diversity(
            candidate,
            missing_fallback=(
                0.0 if activation is None else activation.missing_structural_diversity
            ),
        )
        topology_score = valdiv * semdiv * syndiv
        if activation is not None:
            topology_score = min(
                activation.leaf_score_cap,
                activation.leaf_scale
                * valdiv ** activation.leaf_valdiv_exponent
                * semdiv ** activation.leaf_semdiv_exponent
                * syndiv ** activation.leaf_syndiv_exponent,
            )
        return RetrievalFeatures(
            node_kind="leaf",
            valdiv=valdiv,
            semdiv=semdiv,
            syndiv=syndiv,
            topology_score=topology_score,
            effective_output_observations=effective,
            independent_child_groups=0,
            child_gain=None,
            uncertainty=None,
            warnings=tuple(sorted({*output_warnings, *semantic_warnings, *structural_warnings})),
        )

    residuals_by_group: dict[str, list[float]] = {}
    for observation in episodic.observations:
        if observation.parent_factor_spec_id != candidate.factor_spec_id or observation.residual is None:
            continue
        residuals_by_group.setdefault(observation.run_group_id, []).append(observation.residual)
    group_means = [sum(values) / len(values) for _, values in sorted(residuals_by_group.items())]
    warnings: list[str] = []
    child_gain: float | None = None
    uncertainty: float | None = None
    if group_means:
        child_gain = sum(group_means) / len(group_means)
        if len(group_means) >= 2:
            variance = sum((value - child_gain) ** 2 for value in group_means) / (len(group_means) - 1)
            uncertainty = math.sqrt(variance / len(group_means))
        else:
            warnings.append("INSUFFICIENT_INDEPENDENT_CHILD_GROUPS")
    else:
        warnings.append("MISSING_EVALUATED_CHILD_GAIN")
    sparsity = query.branch_sparsity(candidate.factor_spec_id)
    if activation is None:
        sibling_breadth = 1.0 / (1.0 + len(query.siblings(candidate.factor_spec_id)))
        depth_penalty = 1.0 / (1.0 + query.depth(candidate.factor_spec_id))
        evidence_gate = min(1.0, len(group_means) / 3.0)
        positive_gain = max(0.0, child_gain or 0.0) * evidence_gate
        uncertainty_penalty = 0.0 if uncertainty is None else uncertainty
        topology = (
            positive_gain + 0.10 * sparsity + 0.05 * sibling_breadth
        ) * depth_penalty / (1.0 + uncertainty_penalty + candidate.estimated_cost)
    else:
        sibling_breadth = 1.0 / (
            activation.nonleaf_sibling_offset
            + len(query.siblings(candidate.factor_spec_id))
        )
        depth_penalty = 1.0 / (
            activation.nonleaf_depth_offset + query.depth(candidate.factor_spec_id)
        )
        evidence_gate = min(
            activation.nonleaf_evidence_gate_cap,
            len(group_means) / activation.nonleaf_independent_group_gate,
        )
        positive_gain = max(
            activation.nonleaf_child_gain_floor,
            child_gain or 0.0,
        ) * evidence_gate
        uncertainty_penalty = 0.0 if uncertainty is None else uncertainty
        numerator = (
            activation.nonleaf_child_gain_weight * positive_gain
            + activation.nonleaf_sparsity_weight * sparsity
            + activation.nonleaf_sibling_weight * sibling_breadth
        )
        denominator = (
            activation.nonleaf_uncertainty_offset
            + activation.nonleaf_uncertainty_weight * uncertainty_penalty
            + activation.nonleaf_cost_weight * candidate.estimated_cost
        )
        topology = max(
            activation.nonleaf_score_floor,
            numerator * depth_penalty / denominator,
        )
    return RetrievalFeatures(
        node_kind="nonleaf",
        valdiv=0.0,
        semdiv=0.0,
        syndiv=0.0,
        topology_score=max(0.0, topology),
        effective_output_observations=0,
        independent_child_groups=len(group_means),
        child_gain=child_gain,
        uncertainty=uncertainty,
        warnings=tuple(sorted(warnings)),
    )


def output_diversity(
    candidate: RetrievalCandidate,
    *,
    minimum_alignment: int = 3,
    missing_fallback: float = 0.0,
) -> tuple[float, int, tuple[str, ...]]:
    candidate_values = {
        (point.date, point.symbol): point.value
        for point in candidate.output_panel.points if point.valid
    }
    correlations: list[float] = []
    effective = 0
    for reference in candidate.reference_panels:
        reference_values = {
            (point.date, point.symbol): point.value
            for point in reference.points if point.valid
        }
        keys = sorted(set(candidate_values) & set(reference_values))
        if len(keys) < minimum_alignment:
            continue
        correlation = _rank_correlation(
            [candidate_values[key] for key in keys],
            [reference_values[key] for key in keys],
        )
        if correlation is not None:
            correlations.append(abs(correlation))
            effective = max(effective, len(keys))
    if not correlations:
        return missing_fallback, 0, ("MISSING_ALIGNED_REFERENCE_OUTPUTS",)
    return max(0.0, 1.0 - max(correlations)), effective, ()


def semantic_diversity(
    candidate: RetrievalCandidate,
    *,
    missing_fallback: float = 0.0,
) -> tuple[float, tuple[str, ...]]:
    evidence = candidate.semantic
    if evidence.candidate_vector is None:
        return missing_fallback, (f"MISSING_SEMANTIC_EMBEDDING:{evidence.missing_reason}",)
    if not evidence.reference_vectors:
        return missing_fallback, ("MISSING_SEMANTIC_REFERENCE_POOL",)
    similarities = [
        abs(_cosine(evidence.candidate_vector, reference))
        for reference in evidence.reference_vectors
    ]
    return max(0.0, 1.0 - max(similarities)), ()


def structural_diversity(
    candidate: RetrievalCandidate,
    *,
    missing_fallback: float = 0.0,
) -> tuple[float, tuple[str, ...]]:
    if not candidate.reference_asts:
        return missing_fallback, ("MISSING_STRUCTURAL_REFERENCE_POOL",)
    distances = [
        _normalized_tree_distance(candidate.canonical_ast, reference)
        for reference in candidate.reference_asts
    ]
    return min(distances), ()


def _rank_correlation(left: list[float], right: list[float]) -> float | None:
    left_ranks = _average_ranks(left)
    right_ranks = _average_ranks(right)
    left_mean = sum(left_ranks) / len(left_ranks)
    right_mean = sum(right_ranks) / len(right_ranks)
    numerator = sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left_ranks, right_ranks)
    )
    left_scale = sum((value - left_mean) ** 2 for value in left_ranks)
    right_scale = sum((value - right_mean) ** 2 for value in right_ranks)
    if left_scale <= 0.0 or right_scale <= 0.0:
        return None
    return numerator / math.sqrt(left_scale * right_scale)


def _average_ranks(values: list[float]) -> list[float]:
    ordered = sorted(range(len(values)), key=lambda index: (values[index], index))
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[cursor]]:
            end += 1
        rank = (cursor + 1 + end) / 2.0
        for position in range(cursor, end):
            ranks[ordered[position]] = rank
        cursor = end
    return ranks


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 1.0
    return max(-1.0, min(1.0, numerator / (left_norm * right_norm)))


def _normalized_tree_distance(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    distance = _tree_distance(left, right)
    return min(1.0, distance / max(_node_count(left), _node_count(right), 1))


def _tree_distance(left: Mapping[str, Any], right: Mapping[str, Any]) -> int:
    if left == right:
        return 0
    if left.get("kind") != right.get("kind"):
        return max(_node_count(left), _node_count(right))
    if left.get("kind") != "call":
        return 1
    cost = 0 if left.get("op") == right.get("op") else 1
    left_args = list(left.get("args", ()))
    right_args = list(right.get("args", ()))
    for index in range(min(len(left_args), len(right_args))):
        cost += _tree_distance(left_args[index], right_args[index])
    for child in left_args[len(right_args):]:
        cost += _node_count(child)
    for child in right_args[len(left_args):]:
        cost += _node_count(child)
    return cost


def _node_count(node: Mapping[str, Any]) -> int:
    return 1 + sum(_node_count(child) for child in node.get("args", ()))


__all__ = [
    "RetrievalFeatures", "build_retrieval_features", "output_diversity",
    "semantic_diversity", "structural_diversity",
]
