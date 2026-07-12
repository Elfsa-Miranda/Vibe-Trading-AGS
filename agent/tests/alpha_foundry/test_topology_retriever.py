from __future__ import annotations

import pytest
from dataclasses import replace

from src.alpha_foundry.dsl.identity import build_expression_identity
from src.alpha_foundry.retrieval import RetrievalCandidate, SemanticEmbeddingEvidence
from src.alpha_foundry.retrieval.features import build_retrieval_features, output_diversity
from src.research_ledger.hash_utils import canonical_json_hash
from projection_fixtures import unit_episodic_projection
from test_retriever_shadow import _candidate, _panel, _views


def test_leaf_valdiv_uses_aligned_factor_outputs_and_absolute_correlation(tmp_path) -> None:
    _, query, evidence = _views(tmp_path)
    candidate = _candidate(query, evidence)
    sign_flip = _panel(
        "sign-flip", (-1.0, -2.0, -3.0, -4.0), evidence.data_snapshot_hash
    )
    value, effective, warnings = output_diversity(
        replace(candidate, reference_panels=(sign_flip,))
    )
    assert value == pytest.approx(0.0)
    assert effective == 4
    assert warnings == ()


def test_leaf_likelihood_is_retrieval_only_not_an_admission_gate(tmp_path) -> None:
    _, query, evidence = _views(tmp_path)
    candidate = _candidate(query, evidence, missing_semantic=True)
    from src.alpha_foundry.retrieval import ShadowRetriever
    from test_retriever_shadow import _flags

    decision = ShadowRetriever(flags=_flags()).decide(
        official_candidate_ids=("official-stays-authoritative",),
        evidence=evidence, query=query, candidates=(candidate,),
        seed=4, candidate_budget=1,
    )
    assert decision.shadow_only is True
    assert decision.components[0].topology_score == 0.0
    assert decision.selected_factor_spec_ids == (candidate.factor_spec_id,)
    assert ("official-stays-authoritative",) != decision.selected_factor_spec_ids


def test_nonleaf_policy_uses_independent_child_gain_sparsity_and_cost(tmp_path) -> None:
    _, query, evidence = _views(tmp_path)
    observation = evidence.episodic.observations[0]
    parent_id = observation.parent_factor_spec_id
    parent = RetrievalCandidate(
        factor_spec_id=parent_id,
        action_id="parent-action",
        motif=observation.motif,
        parent_context_hash=observation.parent_context_hash,
        base_ledger_score=0.7,
        output_panel=_panel(
            parent_id, (4.0, 1.0, 3.0, 2.0), evidence.data_snapshot_hash
        ),
        reference_panels=(
            _panel(
                "reference", (1.0, 2.0, 4.0, 3.0), evidence.data_snapshot_hash
            ),
        ),
        canonical_ast=build_expression_identity("rank(close)").canonical_ast,
        reference_asts=(build_expression_identity("rank(open)").canonical_ast,),
        semantic=SemanticEmbeddingEvidence.build(
            model_id="fixture", model_version="1", candidate_vector=(1.0, 0.0),
            reference_vectors=((0.0, 1.0),),
        ),
        estimated_cost=0.0,
        cost_evidence_hash=canonical_json_hash({"cost": 0}),
    )
    observations = tuple(
        replace(
            observation,
            child_factor_spec_id=f"child-{index}",
            run_group_id=f"group-{index}",
            residual=0.2 + index * 0.01,
        )
        for index in range(3)
    )
    features = build_retrieval_features(
        parent, query=query,
        episodic=unit_episodic_projection(
            evidence.episodic,
            observations=observations,
        ),
    )
    expensive = build_retrieval_features(
        replace(parent, estimated_cost=2.0), query=query,
        episodic=unit_episodic_projection(
            evidence.episodic,
            observations=observations,
        ),
    )
    assert features.node_kind == "nonleaf"
    assert features.independent_child_groups == 3
    assert features.child_gain == pytest.approx(0.21)
    assert 0.0 < expensive.topology_score < features.topology_score
