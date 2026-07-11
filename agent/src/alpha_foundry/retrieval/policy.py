"""Versioned bounded topology/memory fusion policy."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from src.research_ledger.hash_utils import canonical_json_hash


@dataclass(frozen=True)
class RetrieverPolicy:
    policy_version: str = "topology_shadow_policy.v2"
    epsilon: float = 1e-9
    memory_weight: float = 0.5
    residual_clip: float = 0.25
    veto_exploration_probability: float = 0.05
    softmax_temperature: float = 1.0
    maximum_candidate_budget: int = 10_000

    def __post_init__(self) -> None:
        if self.policy_version != "topology_shadow_policy.v2":
            raise ValueError("unsupported topology retriever policy")
        if not math.isfinite(self.epsilon) or not 0.0 < self.epsilon <= 1e-3:
            raise ValueError("retriever epsilon is out of bounds")
        if not math.isfinite(self.memory_weight) or not 0.0 <= self.memory_weight <= 1.0:
            raise ValueError("memory weight must be in [0, 1]")
        if not math.isfinite(self.residual_clip) or not 0.0 < self.residual_clip <= 1.0:
            raise ValueError("residual clip must be in (0, 1]")
        if not 0.0 <= self.veto_exploration_probability <= 0.25:
            raise ValueError("veto exploration probability must be in [0, .25]")
        if not math.isfinite(self.softmax_temperature) or not 0.0 < self.softmax_temperature <= 10.0:
            raise ValueError("softmax temperature must be in (0, 10]")
        if not 1 <= self.maximum_candidate_budget <= 100_000:
            raise ValueError("maximum candidate budget is out of bounds")

    @property
    def policy_hash(self) -> str:
        return canonical_json_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "epsilon": self.epsilon,
            "memory_weight": self.memory_weight,
            "residual_clip": self.residual_clip,
            "veto_exploration_probability": self.veto_exploration_probability,
            "softmax_temperature": self.softmax_temperature,
            "maximum_candidate_budget": self.maximum_candidate_budget,
        }


@dataclass(frozen=True)
class ActivationRetrieverPolicy:
    """Fully frozen topology policy for experiments and optional research influence.

    This additive v3 policy intentionally does not change the historical v2
    payload or hash.  Every coefficient and normalization used by the v3
    feature/scoring path is part of ``to_dict`` and therefore ``policy_hash``.
    """

    policy_version: str = "topology_activation_policy.v3"
    leaf_aggregation: str = "weighted_geometric_product.v1"
    output_diversity_normalization: str = "one_minus_max_abs_rank_correlation.v1"
    semantic_diversity_normalization: str = "one_minus_max_abs_cosine.v1"
    structural_diversity_normalization: str = "tree_edit_over_max_node_count.v1"
    nonleaf_aggregation: str = "gated_gain_sparse_penalized.v1"
    nonleaf_uncertainty_estimator: str = "independent_group_standard_error.v1"
    memory_fusion: str = "bounded_log_residual.v1"
    sampling_method: str = "sequential_softmax_without_replacement.v1"
    epsilon: float = 1e-9
    topology_floor: float = 1e-9
    leaf_valdiv_exponent: float = 1.0
    leaf_semdiv_exponent: float = 1.0
    leaf_syndiv_exponent: float = 1.0
    leaf_scale: float = 1.0
    leaf_score_cap: float = 1.0
    minimum_aligned_output_observations: int = 3
    missing_output_diversity: float = 0.0
    missing_semantic_diversity: float = 0.0
    missing_structural_diversity: float = 0.0
    nonleaf_independent_group_gate: int = 3
    nonleaf_evidence_gate_cap: float = 1.0
    nonleaf_child_gain_floor: float = 0.0
    nonleaf_child_gain_weight: float = 1.0
    nonleaf_sparsity_weight: float = 0.10
    nonleaf_sibling_weight: float = 0.05
    nonleaf_sibling_offset: float = 1.0
    nonleaf_depth_offset: float = 1.0
    nonleaf_uncertainty_offset: float = 1.0
    nonleaf_uncertainty_weight: float = 1.0
    nonleaf_cost_weight: float = 1.0
    nonleaf_score_floor: float = 0.0
    memory_weight: float = 0.5
    residual_clip: float = 0.25
    veto_exploration_probability: float = 0.05
    softmax_temperature: float = 1.0
    maximum_candidate_budget: int = 10_000

    def __post_init__(self) -> None:
        expected_versions = {
            "policy_version": "topology_activation_policy.v3",
            "leaf_aggregation": "weighted_geometric_product.v1",
            "output_diversity_normalization": "one_minus_max_abs_rank_correlation.v1",
            "semantic_diversity_normalization": "one_minus_max_abs_cosine.v1",
            "structural_diversity_normalization": "tree_edit_over_max_node_count.v1",
            "nonleaf_aggregation": "gated_gain_sparse_penalized.v1",
            "nonleaf_uncertainty_estimator": "independent_group_standard_error.v1",
            "memory_fusion": "bounded_log_residual.v1",
            "sampling_method": "sequential_softmax_without_replacement.v1",
        }
        for name, expected in expected_versions.items():
            if getattr(self, name) != expected:
                raise ValueError(f"unsupported activation retriever {name}")
        bounded = {
            "epsilon": (0.0, 1e-3, False),
            "topology_floor": (0.0, 1.0, False),
            "leaf_valdiv_exponent": (0.0, 8.0, False),
            "leaf_semdiv_exponent": (0.0, 8.0, False),
            "leaf_syndiv_exponent": (0.0, 8.0, False),
            "leaf_scale": (0.0, 8.0, False),
            "leaf_score_cap": (0.0, 8.0, False),
            "missing_output_diversity": (0.0, 1.0, True),
            "missing_semantic_diversity": (0.0, 1.0, True),
            "missing_structural_diversity": (0.0, 1.0, True),
            "nonleaf_child_gain_floor": (0.0, 8.0, True),
            "nonleaf_evidence_gate_cap": (0.0, 1.0, False),
            "nonleaf_child_gain_weight": (0.0, 8.0, True),
            "nonleaf_sparsity_weight": (0.0, 8.0, True),
            "nonleaf_sibling_weight": (0.0, 8.0, True),
            "nonleaf_sibling_offset": (0.0, 8.0, False),
            "nonleaf_depth_offset": (0.0, 8.0, False),
            "nonleaf_uncertainty_offset": (0.0, 8.0, False),
            "nonleaf_uncertainty_weight": (0.0, 8.0, True),
            "nonleaf_cost_weight": (0.0, 8.0, True),
            "nonleaf_score_floor": (0.0, 8.0, True),
            "memory_weight": (0.0, 1.0, True),
            "residual_clip": (0.0, 1.0, False),
            "veto_exploration_probability": (0.0, 0.25, True),
            "softmax_temperature": (0.0, 10.0, False),
        }
        for name, (lower, upper, allow_lower) in bounded.items():
            value = getattr(self, name)
            if not math.isfinite(value):
                raise ValueError(f"activation retriever {name} must be finite")
            lower_ok = value >= lower if allow_lower else value > lower
            if not lower_ok or value > upper:
                raise ValueError(f"activation retriever {name} is out of bounds")
        if self.leaf_score_cap < self.topology_floor:
            raise ValueError("leaf score cap cannot be below topology floor")
        for name in (
            "missing_output_diversity",
            "missing_semantic_diversity",
            "missing_structural_diversity",
            "nonleaf_child_gain_floor",
            "nonleaf_score_floor",
        ):
            if getattr(self, name) != 0.0:
                raise ValueError(f"activation retriever {name} must fail closed at zero")
        if not 3 <= self.minimum_aligned_output_observations <= 1_000_000:
            raise ValueError("minimum aligned output observations is out of bounds")
        if not 2 <= self.nonleaf_independent_group_gate <= 100_000:
            raise ValueError("nonleaf independent group gate is out of bounds")
        if not 1 <= self.maximum_candidate_budget <= 100_000:
            raise ValueError("maximum candidate budget is out of bounds")

    @property
    def policy_hash(self) -> str:
        return canonical_json_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "leaf_aggregation": self.leaf_aggregation,
            "output_diversity_normalization": self.output_diversity_normalization,
            "semantic_diversity_normalization": self.semantic_diversity_normalization,
            "structural_diversity_normalization": self.structural_diversity_normalization,
            "nonleaf_aggregation": self.nonleaf_aggregation,
            "nonleaf_uncertainty_estimator": self.nonleaf_uncertainty_estimator,
            "memory_fusion": self.memory_fusion,
            "sampling_method": self.sampling_method,
            "epsilon": self.epsilon,
            "topology_floor": self.topology_floor,
            "leaf_valdiv_exponent": self.leaf_valdiv_exponent,
            "leaf_semdiv_exponent": self.leaf_semdiv_exponent,
            "leaf_syndiv_exponent": self.leaf_syndiv_exponent,
            "leaf_scale": self.leaf_scale,
            "leaf_score_cap": self.leaf_score_cap,
            "minimum_aligned_output_observations": self.minimum_aligned_output_observations,
            "missing_output_diversity": self.missing_output_diversity,
            "missing_semantic_diversity": self.missing_semantic_diversity,
            "missing_structural_diversity": self.missing_structural_diversity,
            "nonleaf_independent_group_gate": self.nonleaf_independent_group_gate,
            "nonleaf_evidence_gate_cap": self.nonleaf_evidence_gate_cap,
            "nonleaf_child_gain_floor": self.nonleaf_child_gain_floor,
            "nonleaf_child_gain_weight": self.nonleaf_child_gain_weight,
            "nonleaf_sparsity_weight": self.nonleaf_sparsity_weight,
            "nonleaf_sibling_weight": self.nonleaf_sibling_weight,
            "nonleaf_sibling_offset": self.nonleaf_sibling_offset,
            "nonleaf_depth_offset": self.nonleaf_depth_offset,
            "nonleaf_uncertainty_offset": self.nonleaf_uncertainty_offset,
            "nonleaf_uncertainty_weight": self.nonleaf_uncertainty_weight,
            "nonleaf_cost_weight": self.nonleaf_cost_weight,
            "nonleaf_score_floor": self.nonleaf_score_floor,
            "memory_weight": self.memory_weight,
            "residual_clip": self.residual_clip,
            "veto_exploration_probability": self.veto_exploration_probability,
            "softmax_temperature": self.softmax_temperature,
            "maximum_candidate_budget": self.maximum_candidate_budget,
        }


__all__ = ["ActivationRetrieverPolicy", "RetrieverPolicy"]
