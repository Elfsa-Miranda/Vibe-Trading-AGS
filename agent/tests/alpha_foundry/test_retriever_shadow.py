from __future__ import annotations

from dataclasses import replace
import random

import pytest

from src.alpha_foundry.dag import FactorDAGProjector, FactorDAGQuery
from src.alpha_foundry.dsl.canonical import thaw_canonical_ast
from src.alpha_foundry.memory import EpisodicProjector, FactualMemoryView
from src.alpha_foundry.memory.model import ProcessPosterior
from src.alpha_foundry.retrieval import (
    DiscoveryEvidenceView,
    FactorOutputPanel,
    OutputPoint,
    RetrievalCandidate,
    RetrieverPolicy,
    SemanticEmbeddingEvidence,
    ShadowRetriever,
)
from src.alpha_quality.flags import ResolvedAGSFlags
from src.alpha_quality.scope import DiscoveryEvidenceProjector
from src.research_ledger.events import (
    EventDraft, EventTransitionError, EventValidationError, ResearchEventStore,
)
from src.research_ledger.hash_utils import canonical_json_hash
from test_process_memory import _record_valid_outcome


def _flags() -> ResolvedAGSFlags:
    return ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_ALPHA_FOUNDRY": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
            "VIBE_TRADING_FACTOR_DAG": "1",
            "VIBE_TRADING_PROCESS_MEMORY": "1",
            "VIBE_TRADING_TOPOLOGY_RETRIEVER": "1",
        }
    )


def _views(tmp_path):
    process_store, _, _, _, _ = _record_valid_outcome(tmp_path)
    store = ResearchEventStore(
        process_store.db_path,
        artifact_root=process_store.artifact_root,
        flags=_flags(),
        code_version="retriever-test",
    )
    events = store.query_events()
    episodic = EpisodicProjector().project(events)
    observation = episodic.observations[0]
    evidence = DiscoveryEvidenceProjector(flags=_flags()).project(
        store,
        data_snapshot_hash=observation.data_snapshot_hash,
    )
    return store, FactorDAGQuery(evidence.factual.dag), evidence


def _panel(
    factor_id: str, values: tuple[float, ...], snapshot_hash: str
) -> FactorOutputPanel:
    return FactorOutputPanel.build(
        factor_id,
        [
            OutputPoint("2025-01-01", f"S{index}", value)
            for index, value in enumerate(values)
        ],
        data_snapshot_hash=snapshot_hash,
    )


def _candidate(query: FactorDAGQuery, evidence: DiscoveryEvidenceView, *, missing_semantic: bool = False) -> RetrievalCandidate:
    factor_id = evidence.factual.factor_ids()[0]
    node = query.projection.factor_nodes[factor_id]
    candidate_ast = next(
        item for item in evidence.episodic.observations
        if item.child_factor_spec_id == factor_id
    )
    observation = candidate_ast
    ast = _definition_ast(evidence, factor_id)
    parent_ast = _definition_ast(evidence, observation.parent_factor_spec_id)
    semantic = (
        SemanticEmbeddingEvidence.build(
            model_id="fixture-embedding", model_version="1",
            candidate_vector=None, missing_reason="MODEL_UNAVAILABLE",
        )
        if missing_semantic
        else SemanticEmbeddingEvidence.build(
            model_id="fixture-embedding", model_version="1",
            candidate_vector=(1.0, 0.0), reference_vectors=((0.0, 1.0),),
        )
    )
    assert node.factor_spec_id == factor_id
    parent_factor_id = observation.parent_factor_spec_id
    return RetrievalCandidate(
        factor_spec_id=factor_id,
        action_id="shadow-action",
        motif=observation.motif,
        parent_context_hash=observation.parent_context_hash,
        base_ledger_score=0.8,
        output_panel=_panel(
            factor_id, (1.0, 2.0, 3.0, 4.0), evidence.data_snapshot_hash
        ),
        reference_panels=(
            _panel(
                parent_factor_id,
                (1.0, 3.0, 2.0, 4.0),
                evidence.data_snapshot_hash,
            ),
        ),
        canonical_ast=ast,
        reference_asts=(parent_ast,),
        semantic=semantic,
        estimated_cost=0.01,
        cost_evidence_hash=canonical_json_hash({"cost": "fixture"}),
    )


def _definition_ast(evidence: DiscoveryEvidenceView, factor_id: str):
    # The factual view intentionally exposes hashes, not a generic event store;
    # canonical ASTs come from its already-validated DAG fixture definitions.
    from src.alpha_foundry.dsl.identity import build_expression_identity

    observation = evidence.episodic.observations[0]
    formula = "zscore(rank(open))" if factor_id == observation.child_factor_spec_id else "rank(close)"
    return build_expression_identity(formula).canonical_ast


def test_shadow_decision_is_seeded_deterministic_and_records_propensity(tmp_path) -> None:
    store, query, evidence = _views(tmp_path)
    candidate = _candidate(query, evidence)
    retriever = ShadowRetriever(flags=_flags())
    first = retriever.decide(
        official_candidate_ids=("official",), evidence=evidence, query=query,
        candidates=(candidate,), seed=9, candidate_budget=1,
    )
    second = retriever.decide(
        official_candidate_ids=("official",), evidence=evidence, query=query,
        candidates=(candidate,), seed=9, candidate_budget=1,
    )
    assert first == second
    assert first.selected_factor_spec_ids == (candidate.factor_spec_id,)
    assert first.components[0].selection_propensity == 1.0
    assert first.eligible_event_watermark == evidence.source_watermark
    event = retriever.record(store, first, run_id="shadow-run")
    assert event.payload["shadow_only"] is True
    assert event.payload["decision_hash"] == first.decision_hash
    assert retriever.record(store, first, run_id="shadow-run").event_hash == event.event_hash
    assert store.verify_chain()
    assert store.replay().watermark_event_hash == event.event_hash

    stale = thaw_canonical_ast(event.payload)
    stale["decision_id"] = "retriever-v2-stale"
    stale["eligible_event_watermark"] = store.query_events()[0].event_hash
    stale_content = {
        "schema_version": "retriever_shadow_decision.v2",
        **{key: value for key, value in stale.items() if key not in {"decision_id", "decision_hash"}},
    }
    stale["decision_hash"] = canonical_json_hash(stale_content)
    with pytest.raises(EventTransitionError):
        store.append_event(
            EventDraft(
                event_type="RetrieverDecisionV2Recorded",
                entity_id="retriever-v2-stale", run_id="shadow-run",
                payload_schema_version="retriever_decision_recorded.v2",
                payload=stale, idempotency_key="retriever-v2:stale",
            )
        )

    second_component = replace(
        first.components[0], factor_spec_id="second-factor", action_id="second-action",
        selected=False, selection_propensity=0.0,
    )
    selected_one, propensity_one = retriever._sample_without_replacement(
        [first.components[0], second_component], seed=1, budget=1
    )
    selected_two, propensity_two = retriever._sample_without_replacement(
        [first.components[0], second_component], seed=2, budget=1
    )
    assert selected_one != selected_two
    assert next(iter(propensity_one.values())) == pytest.approx(0.5)
    assert next(iter(propensity_two.values())) == pytest.approx(0.5)


def test_reference_pools_must_align_and_reference_ast_is_authoritative(
    tmp_path,
) -> None:
    _, query, evidence = _views(tmp_path)
    candidate = _candidate(query, evidence)
    with pytest.raises(ValueError, match="reference pools must align"):
        replace(candidate, reference_asts=())

    forged_reference = replace(
        candidate,
        reference_asts=(candidate.canonical_ast,),
    )
    with pytest.raises(ValueError, match="reference AST does not match"):
        ShadowRetriever(flags=_flags()).decide(
            official_candidate_ids=(),
            evidence=evidence,
            query=query,
            candidates=(forged_reference,),
            seed=1,
            candidate_budget=1,
        )


def test_only_matching_negative_memory_vetoes_and_positive_is_bounded(tmp_path) -> None:
    _, query, evidence = _views(tmp_path)
    candidate = _candidate(query, evidence)
    matching = ProcessPosterior(
        candidate.parent_context_hash, candidate.motif, 4, 4, -0.5, 0.0,
        1.0, 0.0, True,
    )
    unrelated = replace(matching, parent_context_hash=canonical_json_hash({"other": True}))
    no_escape = RetrieverPolicy(veto_exploration_probability=0.0)
    retriever = ShadowRetriever(flags=_flags(), policy=no_escape)

    unrelated_evidence = evidence.with_episodic_projection(
        replace(evidence.episodic, posteriors=(unrelated,)),
    )
    allowed = retriever.decide(
        official_candidate_ids=(), evidence=unrelated_evidence, query=query,
        candidates=(candidate,), seed=1, candidate_budget=1,
    )
    assert allowed.selected_factor_spec_ids == (candidate.factor_spec_id,)
    assert allowed.components[0].memory_adjustment == 0.0

    matching_evidence = evidence.with_episodic_projection(
        replace(evidence.episodic, posteriors=(matching,)),
    )
    vetoed = retriever.decide(
        official_candidate_ids=(), evidence=matching_evidence, query=query,
        candidates=(candidate,), seed=1, candidate_budget=1,
    )
    assert vetoed.selected_factor_spec_ids == ()
    assert vetoed.components[0].veto_reason == "HIGH_CONFIDENCE_NEGATIVE_MEMORY"


def test_shadow_does_not_change_official_rng_cache_budget_or_outputs(tmp_path) -> None:
    _, query, evidence = _views(tmp_path)
    candidate = _candidate(query, evidence)
    official_rng = random.Random(123)
    rng_state = official_rng.getstate()
    cache = {"official": {"hits": 7}}
    official = ("flat-a", "flat-b")
    result = ShadowRetriever(flags=_flags()).observe_after_official_generation(
        official_candidate_ids=official,
        official_rng_state=rng_state,
        official_cache_state=cache,
        official_budget_remaining=11,
        evidence=evidence,
        query=query,
        candidates=(candidate,),
        seed=77,
        candidate_budget=1,
    )
    assert result.official_candidate_ids == official
    assert result.official_state_before_hash == result.official_state_after_hash
    assert official_rng.getstate() == rng_state
    assert cache == {"official": {"hits": 7}}


def test_missing_embedding_is_explicit_and_cannot_promote_leaf_score(tmp_path) -> None:
    _, query, evidence = _views(tmp_path)
    candidate = _candidate(query, evidence, missing_semantic=True)
    decision = ShadowRetriever(flags=_flags()).decide(
        official_candidate_ids=(), evidence=evidence, query=query,
        candidates=(candidate,), seed=2, candidate_budget=1,
    )
    component = decision.components[0]
    assert component.semdiv == 0.0
    assert component.topology_score == 0.0
    assert any("MISSING_SEMANTIC_EMBEDDING" in warning for warning in component.warnings)


def test_final_generic_or_mismatched_views_and_flag_off_are_rejected(tmp_path) -> None:
    _, _, evidence = _views(tmp_path)
    with pytest.raises(TypeError, match="terminal discovery"):
        DiscoveryEvidenceView(
            factual=evidence.factual, episodic=evidence.episodic,
            data_snapshot_hash=evidence.data_snapshot_hash,
            verified_subsequence=evidence._verified_subsequence,
            _token=object(),
        )
    with pytest.raises(ValueError, match="watermark"):
        evidence.with_episodic_projection(
            replace(
                evidence.episodic,
                source_watermark_event_hash="sha256:" + "a" * 64,
            ),
        )
    with pytest.raises(RuntimeError, match="disabled"):
        ShadowRetriever(
            flags=ResolvedAGSFlags.from_settings(
                {"VIBE_TRADING_AGS_ENABLED": "1", "VIBE_TRADING_TOPOLOGY_RETRIEVER": "1"}
            )
        )
    event_only_flags = ResolvedAGSFlags.from_settings(
        {"VIBE_TRADING_AGS_ENABLED": "1", "VIBE_TRADING_RESEARCH_EVENTS": "1"}
    )
    store = ResearchEventStore(
        tmp_path / "disabled.sqlite", artifact_root=tmp_path / "disabled-artifacts",
        flags=event_only_flags, code_version="disabled-test",
    )
    with pytest.raises(EventValidationError, match="capability is disabled"):
        store.append_event(
            EventDraft(
                event_type="RetrieverDecisionV2Recorded", entity_id="disabled",
                run_id="disabled", payload_schema_version="retriever_decision_recorded.v2",
                payload={}, idempotency_key="disabled-retriever",
            )
        )
    assert store.query_events() == []
