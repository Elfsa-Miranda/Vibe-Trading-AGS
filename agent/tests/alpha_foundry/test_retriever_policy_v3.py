from __future__ import annotations

from dataclasses import fields, replace

import pytest

from src.alpha_foundry.dsl.identity import build_expression_identity
from src.alpha_foundry.retrieval import (
    ActivationRetrieverPolicy,
    RetrievalCandidate,
    RetrieverPolicy,
    SemanticEmbeddingEvidence,
    ShadowRetriever,
)
from src.alpha_foundry.retrieval.features import build_retrieval_features
from src.research_ledger.hash_utils import canonical_json_hash
from test_retriever_shadow import _flags, _panel, _views


def test_historical_v2_policy_hash_and_payload_are_unchanged() -> None:
    policy = RetrieverPolicy()
    assert policy.to_dict() == {
        "policy_version": "topology_shadow_policy.v2",
        "epsilon": 1e-9,
        "memory_weight": 0.5,
        "residual_clip": 0.25,
        "veto_exploration_probability": 0.05,
        "softmax_temperature": 1.0,
        "maximum_candidate_budget": 10_000,
    }
    assert policy.policy_hash == (
        "sha256:c4a5e48f3bcb103fa72ad4af3ec2f306792223d58eeee164a4f308939a0962a0"
    )


def test_v3_policy_hash_covers_every_coefficient_and_normalization() -> None:
    policy = ActivationRetrieverPolicy()
    assert set(policy.to_dict()) == {item.name for item in fields(policy)}
    assert policy.policy_hash == canonical_json_hash(policy.to_dict())
    mutations = {
        "epsilon": 2e-9,
        "topology_floor": 2e-9,
        "leaf_valdiv_exponent": 1.1,
        "leaf_semdiv_exponent": 1.1,
        "leaf_syndiv_exponent": 1.1,
        "leaf_scale": 1.1,
        "leaf_score_cap": 1.1,
        "minimum_aligned_output_observations": 4,
        "nonleaf_independent_group_gate": 4,
        "nonleaf_evidence_gate_cap": 0.9,
        "nonleaf_child_gain_weight": 1.1,
        "nonleaf_sparsity_weight": 0.2,
        "nonleaf_sibling_weight": 0.1,
        "nonleaf_sibling_offset": 1.1,
        "nonleaf_depth_offset": 1.1,
        "nonleaf_uncertainty_offset": 1.1,
        "nonleaf_uncertainty_weight": 1.1,
        "nonleaf_cost_weight": 1.1,
        "memory_weight": 0.6,
        "residual_clip": 0.3,
        "veto_exploration_probability": 0.1,
        "softmax_temperature": 1.1,
        "maximum_candidate_budget": 9_999,
    }
    for name, value in mutations.items():
        assert replace(policy, **{name: value}).policy_hash != policy.policy_hash


@pytest.mark.parametrize(
    "name",
    [
        "missing_output_diversity",
        "missing_semantic_diversity",
        "missing_structural_diversity",
        "nonleaf_child_gain_floor",
        "nonleaf_score_floor",
    ],
)
def test_v3_missing_or_unobserved_evidence_cannot_receive_positive_floor(name: str) -> None:
    with pytest.raises(ValueError, match="fail closed"):
        ActivationRetrieverPolicy(**{name: 0.01})


def test_v3_leaf_score_uses_frozen_exponents_and_alignment(tmp_path) -> None:
    _, query, evidence = _views(tmp_path)
    factor_id = evidence.factual.factor_ids()[0]
    observation = evidence.episodic.observations[0]
    candidate = RetrievalCandidate(
        factor_spec_id=factor_id,
        action_id="v3-leaf-action",
        motif=observation.motif,
        parent_context_hash=observation.parent_context_hash,
        base_ledger_score=0.8,
        output_panel=_panel(
            factor_id, (1.0, 2.0, 3.0, 4.0), evidence.data_snapshot_hash
        ),
        reference_panels=(
            _panel("reference", (1.0, 3.0, 4.0, 2.0), evidence.data_snapshot_hash),
        ),
        canonical_ast=build_expression_identity("zscore(rank(open))").canonical_ast,
        reference_asts=(build_expression_identity("rank(close)").canonical_ast,),
        semantic=SemanticEmbeddingEvidence.build(
            model_id="fixture",
            model_version="1",
            candidate_vector=(1.0, 0.0),
            reference_vectors=((0.5, 0.5),),
        ),
        estimated_cost=0.0,
        cost_evidence_hash=canonical_json_hash({"cost": 0.0}),
    )
    base = ActivationRetrieverPolicy()
    squared = replace(base, leaf_valdiv_exponent=2.0)
    base_features = build_retrieval_features(
        candidate, query=query, episodic=evidence.episodic, policy=base
    )
    squared_features = build_retrieval_features(
        candidate, query=query, episodic=evidence.episodic, policy=squared
    )
    assert 0.0 < squared_features.topology_score < base_features.topology_score
    unavailable = build_retrieval_features(
        candidate,
        query=query,
        episodic=evidence.episodic,
        policy=replace(base, minimum_aligned_output_observations=5),
    )
    assert unavailable.valdiv == 0.0
    assert unavailable.topology_score == 0.0
    assert "MISSING_ALIGNED_REFERENCE_OUTPUTS" in unavailable.warnings


def test_v3_nonleaf_score_uses_only_policy_owned_coefficients(tmp_path) -> None:
    _, query, evidence = _views(tmp_path)
    observation = evidence.episodic.observations[0]
    parent_id = observation.parent_factor_spec_id
    parent = RetrievalCandidate(
        factor_spec_id=parent_id,
        action_id="v3-parent-action",
        motif=observation.motif,
        parent_context_hash=observation.parent_context_hash,
        base_ledger_score=0.7,
        output_panel=_panel(
            parent_id, (4.0, 1.0, 3.0, 2.0), evidence.data_snapshot_hash
        ),
        reference_panels=(
            _panel("reference", (1.0, 2.0, 4.0, 3.0), evidence.data_snapshot_hash),
        ),
        canonical_ast=build_expression_identity("rank(close)").canonical_ast,
        reference_asts=(build_expression_identity("rank(open)").canonical_ast,),
        semantic=SemanticEmbeddingEvidence.build(
            model_id="fixture",
            model_version="1",
            candidate_vector=(1.0, 0.0),
            reference_vectors=((0.0, 1.0),),
        ),
        estimated_cost=0.5,
        cost_evidence_hash=canonical_json_hash({"cost": 0.5}),
    )
    observations = tuple(
        replace(
            observation,
            child_factor_spec_id=f"v3-child-{index}",
            run_group_id=f"v3-group-{index}",
            residual=0.2 + index * 0.01,
        )
        for index in range(3)
    )
    episodic = replace(evidence.episodic, observations=observations)
    base = ActivationRetrieverPolicy()
    penalized = replace(base, nonleaf_cost_weight=4.0)
    gated = replace(base, nonleaf_independent_group_gate=6)
    base_score = build_retrieval_features(
        parent, query=query, episodic=episodic, policy=base
    ).topology_score
    penalized_score = build_retrieval_features(
        parent, query=query, episodic=episodic, policy=penalized
    ).topology_score
    gated_score = build_retrieval_features(
        parent, query=query, episodic=episodic, policy=gated
    ).topology_score
    assert 0.0 < penalized_score < base_score
    assert 0.0 < gated_score < base_score


def test_v3_shadow_decision_commits_full_policy_but_v2_recorder_rejects_it(tmp_path) -> None:
    store, query, evidence = _views(tmp_path)
    factor_id = evidence.factual.factor_ids()[0]
    observation = evidence.episodic.observations[0]
    parent_id = observation.parent_factor_spec_id
    candidate = RetrievalCandidate(
        factor_spec_id=factor_id,
        action_id="v3-shadow-action",
        motif=observation.motif,
        parent_context_hash=observation.parent_context_hash,
        base_ledger_score=0.8,
        output_panel=_panel(
            factor_id, (1.0, 2.0, 3.0, 4.0), evidence.data_snapshot_hash
        ),
        reference_panels=(
            _panel(parent_id, (1.0, 3.0, 2.0, 4.0), evidence.data_snapshot_hash),
        ),
        canonical_ast=build_expression_identity("zscore(rank(open))").canonical_ast,
        reference_asts=(build_expression_identity("rank(close)").canonical_ast,),
        semantic=SemanticEmbeddingEvidence.build(
            model_id="fixture",
            model_version="1",
            candidate_vector=(1.0, 0.0),
            reference_vectors=((0.0, 1.0),),
        ),
        estimated_cost=0.0,
        cost_evidence_hash=canonical_json_hash({"cost": 0.0}),
    )
    policy = ActivationRetrieverPolicy()
    retriever = ShadowRetriever(flags=_flags(), policy=policy)
    decision = retriever.decide(
        official_candidate_ids=("official",),
        evidence=evidence,
        query=query,
        candidates=(candidate,),
        seed=19,
        candidate_budget=1,
    )
    assert decision.schema_version == "retriever_shadow_decision.v3"
    assert decision.policy_config == policy.to_dict()
    assert decision.policy_hash == policy.policy_hash
    with pytest.raises(RuntimeError, match="source-bound v3 recorder"):
        retriever.record(store, decision, run_id="v3-shadow")
