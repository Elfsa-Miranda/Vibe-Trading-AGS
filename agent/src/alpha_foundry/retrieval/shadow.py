"""Non-influential topology retriever with an isolated deterministic RNG."""

from __future__ import annotations

import math
import random
from dataclasses import replace
from typing import Any, Mapping

from src.alpha_foundry.dag.query import FactorDAGQuery
from src.alpha_foundry.dsl.canonical import thaw_canonical_ast
from src.alpha_foundry.retrieval.features import build_retrieval_features
from src.alpha_foundry.retrieval.model import (
    DiscoveryEvidenceView,
    RetrievalCandidate,
    RetrievalComponent,
    ShadowDecision,
    ShadowRunResult,
)
from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy, RetrieverPolicy
from src.alpha_quality.flags import ResolvedAGSFlags
from src.research_ledger.events import EventDraft, ResearchEventStore
from src.research_ledger.hash_utils import canonical_json_hash


class ShadowRetriever:
    def __init__(
        self,
        *,
        flags: ResolvedAGSFlags,
        policy: RetrieverPolicy | ActivationRetrieverPolicy | None = None,
    ) -> None:
        required = (
            "VIBE_TRADING_ALPHA_FOUNDRY",
            "VIBE_TRADING_RESEARCH_EVENTS",
            "VIBE_TRADING_FACTOR_DAG",
            "VIBE_TRADING_PROCESS_MEMORY",
            "VIBE_TRADING_TOPOLOGY_RETRIEVER",
        )
        if any(not flags.enabled(name) for name in required):
            raise RuntimeError("topology retriever capability is disabled")
        self.policy = policy or RetrieverPolicy()

    def decide(
        self,
        *,
        official_candidate_ids: tuple[str, ...],
        evidence: DiscoveryEvidenceView,
        query: FactorDAGQuery,
        candidates: tuple[RetrievalCandidate, ...],
        seed: int,
        candidate_budget: int,
    ) -> ShadowDecision:
        if not isinstance(evidence, DiscoveryEvidenceView):
            raise TypeError("retriever accepts DiscoveryEvidenceView only")
        if query.projection.projection_hash != evidence.factual.dag.projection_hash:
            raise ValueError("retriever DAG differs from its discovery evidence view")
        if query.projection.source_watermark_event_hash != evidence.source_watermark:
            raise ValueError("retriever discovery watermark is stale or widened")
        if not 1 <= candidate_budget <= self.policy.maximum_candidate_budget:
            raise ValueError("candidate budget is outside the frozen policy")
        if len(candidates) > self.policy.maximum_candidate_budget:
            raise ValueError("eligible candidate set exceeds the resource limit")
        factor_ids = [candidate.factor_spec_id for candidate in candidates]
        action_ids = [candidate.action_id for candidate in candidates]
        if len(factor_ids) != len(set(factor_ids)) or len(action_ids) != len(set(action_ids)):
            raise ValueError("retrieval candidates and actions must be unique")
        eligible_factors = set(evidence.factual.factor_ids())
        if any(factor_id not in eligible_factors for factor_id in factor_ids):
            raise ValueError("retriever accepts terminal train/valid factors only")
        for candidate in candidates:
            if candidate.output_panel.data_snapshot_hash != evidence.data_snapshot_hash:
                raise ValueError("retrieval output panel uses another data snapshot")
            node = query.projection.factor_nodes[candidate.factor_spec_id]
            if canonical_json_hash(thaw_canonical_ast(candidate.canonical_ast)) != node.canonical_ast_hash:
                raise ValueError("retrieval canonical AST does not match the DAG definition")
            for reference_panel, reference_ast in zip(
                candidate.reference_panels, candidate.reference_asts
            ):
                reference_node = query.projection.factor_nodes.get(
                    reference_panel.factor_spec_id
                )
                if reference_node is None:
                    raise ValueError(
                        "retrieval reference factor is absent from the frozen DAG"
                    )
                if (
                    canonical_json_hash(thaw_canonical_ast(reference_ast))
                    != reference_node.canonical_ast_hash
                ):
                    raise ValueError(
                        "retrieval reference AST does not match the DAG definition"
                    )

        posteriors = {
            (posterior.parent_context_hash, posterior.motif): posterior
            for posterior in evidence.episodic.posteriors
        }
        components: list[RetrievalComponent] = []
        for candidate in candidates:
            features = build_retrieval_features(
                candidate,
                query=query,
                episodic=evidence.episodic,
                policy=self.policy,
            )
            topology = features.topology_score
            topology_floor = (
                self.policy.topology_floor
                if isinstance(self.policy, ActivationRetrieverPolicy)
                else self.policy.epsilon
            )
            base_score = max(
                self.policy.epsilon,
                candidate.base_ledger_score * max(topology, topology_floor),
            )
            posterior = posteriors.get((candidate.parent_context_hash, candidate.motif))
            memory_adjustment = 0.0
            confidence = 0.0
            veto_reason: str | None = None
            warnings = list(features.warnings)
            if posterior is not None:
                confidence = posterior.confidence
                clipped_residual = max(
                    -self.policy.residual_clip,
                    min(self.policy.residual_clip, posterior.mean_residual),
                )
                memory_adjustment = (
                    self.policy.memory_weight * confidence * clipped_residual
                )
                if posterior.hard_veto:
                    escape = self._veto_escape(seed, candidate)
                    if escape:
                        warnings.append("NEGATIVE_MEMORY_EXPLORATION_ESCAPE")
                    else:
                        veto_reason = "HIGH_CONFIDENCE_NEGATIVE_MEMORY"
            action_score = math.log(base_score + self.policy.epsilon) + memory_adjustment
            components.append(
                RetrievalComponent(
                    factor_spec_id=candidate.factor_spec_id,
                    action_id=candidate.action_id,
                    motif=candidate.motif,
                    node_kind=features.node_kind,
                    output_panel_hash=candidate.output_panel.panel_hash,
                    semantic_model_id=candidate.semantic.model_id,
                    semantic_model_version=candidate.semantic.model_version,
                    semantic_embedding_hash=candidate.semantic.embedding_hash,
                    cost_evidence_hash=candidate.cost_evidence_hash,
                    valdiv=features.valdiv,
                    semdiv=features.semdiv,
                    syndiv=features.syndiv,
                    topology_score=topology,
                    base_score=base_score,
                    memory_adjustment=memory_adjustment,
                    action_score=action_score,
                    confidence=confidence,
                    selected=False,
                    selection_propensity=0.0,
                    warnings=tuple(sorted(set(warnings))),
                    veto_reason=veto_reason,
                )
            )

        selected, propensities = self._sample_without_replacement(
            components, seed=seed, budget=candidate_budget
        )
        selected_set = set(selected)
        completed = tuple(
            replace(
                component,
                selected=component.factor_spec_id in selected_set,
                selection_propensity=propensities.get(component.factor_spec_id, 0.0),
            )
            for component in components
        )
        official_output_hash = canonical_json_hash(
            {"official_candidate_ids": list(official_candidate_ids)}
        )
        schema_version = (
            "retriever_shadow_decision.v3"
            if isinstance(self.policy, ActivationRetrieverPolicy)
            else "retriever_shadow_decision.v2"
        )
        content = {
            "schema_version": schema_version,
            "selected_factor_spec_ids": list(selected),
            "seed": seed,
            "policy_version": self.policy.policy_version,
            "policy_hash": self.policy.policy_hash,
            "policy_config": self.policy.to_dict(),
            "eligible_event_watermark": evidence.source_watermark,
            "data_snapshot_hash": evidence.data_snapshot_hash,
            "candidate_budget": candidate_budget,
            "official_output_hash": official_output_hash,
            "propensity_semantics": "sequential_softmax_draw_probability.v1",
            "components": [component.to_dict() for component in completed],
            "shadow_only": True,
        }
        return ShadowDecision(
            schema_version=schema_version,
            selected_factor_spec_ids=selected,
            seed=seed,
            policy_version=self.policy.policy_version,
            policy_hash=self.policy.policy_hash,
            policy_config=self.policy.to_dict(),
            eligible_event_watermark=evidence.source_watermark,
            data_snapshot_hash=evidence.data_snapshot_hash,
            candidate_budget=candidate_budget,
            official_output_hash=official_output_hash,
            propensity_semantics="sequential_softmax_draw_probability.v1",
            components=completed,
            decision_hash=canonical_json_hash(content),
            shadow_only=True,
        )

    def observe_after_official_generation(
        self,
        *,
        official_candidate_ids: tuple[str, ...],
        official_rng_state: Any,
        official_cache_state: Mapping[str, Any],
        official_budget_remaining: int,
        evidence: DiscoveryEvidenceView,
        query: FactorDAGQuery,
        candidates: tuple[RetrievalCandidate, ...],
        seed: int,
        candidate_budget: int,
    ) -> ShadowRunResult:
        before = self._official_state_hash(
            official_candidate_ids, official_rng_state,
            official_cache_state, official_budget_remaining,
        )
        decision = self.decide(
            official_candidate_ids=official_candidate_ids,
            evidence=evidence,
            query=query,
            candidates=candidates,
            seed=seed,
            candidate_budget=candidate_budget,
        )
        after = self._official_state_hash(
            official_candidate_ids, official_rng_state,
            official_cache_state, official_budget_remaining,
        )
        if after != before:
            raise RuntimeError("shadow retrieval changed official RNG/cache/budget/output state")
        return ShadowRunResult(
            official_candidate_ids=official_candidate_ids,
            decision=decision,
            official_state_before_hash=before,
            official_state_after_hash=after,
        )

    def record(
        self,
        store: ResearchEventStore,
        decision: ShadowDecision,
        *,
        run_id: str,
    ):
        if decision.schema_version != "retriever_shadow_decision.v2":
            raise RuntimeError(
                "activation retriever decisions require the source-bound v3 recorder"
            )
        identifier = "retriever-v2-" + decision.decision_hash.removeprefix("sha256:")[:20]
        return store.append_event(
            EventDraft(
                event_type="RetrieverDecisionV2Recorded",
                entity_id=identifier,
                run_id=run_id,
                payload_schema_version="retriever_decision_recorded.v2",
                idempotency_key=f"retriever-v2:{decision.decision_hash}",
                payload={
                    "decision_id": identifier,
                    "decision_hash": decision.decision_hash,
                    "selected_factor_spec_ids": list(decision.selected_factor_spec_ids),
                    "seed": decision.seed,
                    "policy_version": decision.policy_version,
                    "policy_hash": decision.policy_hash,
                    "policy_config": dict(decision.policy_config),
                    "eligible_event_watermark": decision.eligible_event_watermark,
                    "data_snapshot_hash": decision.data_snapshot_hash,
                    "candidate_budget": decision.candidate_budget,
                    "official_output_hash": decision.official_output_hash,
                    "propensity_semantics": decision.propensity_semantics,
                    "components": [component.to_dict() for component in decision.components],
                    "shadow_only": decision.shadow_only,
                },
            )
        )

    def _veto_escape(self, seed: int, candidate: RetrievalCandidate) -> bool:
        digest = canonical_json_hash(
            {
                "seed": seed,
                "factor_spec_id": candidate.factor_spec_id,
                "action_id": candidate.action_id,
                "policy_hash": self.policy.policy_hash,
            }
        )
        value = int(digest.removeprefix("sha256:")[:16], 16) / float(16**16)
        return value < self.policy.veto_exploration_probability

    def _sample_without_replacement(
        self,
        components: list[RetrievalComponent],
        *,
        seed: int,
        budget: int,
    ) -> tuple[tuple[str, ...], dict[str, float]]:
        remaining = [component for component in components if component.veto_reason is None]
        rng = random.Random(seed)
        selected: list[str] = []
        propensities: dict[str, float] = {}
        for _ in range(min(budget, len(remaining))):
            maximum = max(component.action_score for component in remaining)
            weights = [
                math.exp(
                    (component.action_score - maximum) / self.policy.softmax_temperature
                )
                for component in remaining
            ]
            total = sum(weights)
            probabilities = [weight / total for weight in weights]
            draw = rng.random()
            cumulative = 0.0
            chosen_index = len(remaining) - 1
            for index, probability in enumerate(probabilities):
                cumulative += probability
                if draw <= cumulative:
                    chosen_index = index
                    break
            chosen = remaining.pop(chosen_index)
            selected.append(chosen.factor_spec_id)
            propensities[chosen.factor_spec_id] = probabilities[chosen_index]
        return tuple(selected), propensities

    @staticmethod
    def _official_state_hash(
        candidate_ids: tuple[str, ...],
        rng_state: Any,
        cache_state: Mapping[str, Any],
        budget_remaining: int,
    ) -> str:
        return canonical_json_hash(
            {
                "candidate_ids": list(candidate_ids),
                "rng_state": rng_state,
                "cache_state": dict(cache_state),
                "budget_remaining": budget_remaining,
            }
        )


__all__ = ["ShadowRetriever"]
