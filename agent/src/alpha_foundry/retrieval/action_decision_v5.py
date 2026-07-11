"""Action-keyed topology Retriever decisions bound to frozen edit templates."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Mapping

from src.alpha_foundry.dag import FactorDAGQuery
from src.alpha_foundry.retrieval.action_template_v1 import (
    FrozenRetrieverActionTemplateV1,
)
from src.alpha_foundry.retrieval.model import (
    DiscoveryEvidenceView,
    RetrievalCandidate,
    RetrievalComponent,
)
from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy
from src.alpha_foundry.retrieval.shadow import ShadowRetriever
from src.alpha_quality.flags import ResolvedAGSFlags
from src.research_ledger.hash_utils import canonical_json_hash


def _freeze(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class ActionShadowDecisionV5:
    schema_version: str
    selected_action_ids: tuple[str, ...]
    selected_parent_factor_spec_ids: tuple[str, ...]
    action_template_event_hashes: tuple[str, ...]
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
        if self.schema_version != "retriever_action_shadow_decision.v5":
            raise ValueError("unsupported action-level Retriever decision")
        if canonical_json_hash(self.policy_config) != self.policy_hash:
            raise ValueError("action-level Retriever policy hash is invalid")
        if len(self.selected_action_ids) != len(self.selected_parent_factor_spec_ids):
            raise ValueError("selected actions and parents must align")
        if len(self.selected_action_ids) != len(set(self.selected_action_ids)):
            raise ValueError("selected action IDs must be unique")
        if len(self.action_template_event_hashes) != len(self.components):
            raise ValueError("action events and Retriever components must align")
        object.__setattr__(self, "policy_config", _freeze(self.policy_config))


class ActionShadowRetrieverV5:
    """Score parent evidence with v3 policy, but select exact frozen actions."""

    def __init__(
        self,
        *,
        flags: ResolvedAGSFlags,
        policy: ActivationRetrieverPolicy | None = None,
    ) -> None:
        self.policy = policy or ActivationRetrieverPolicy()
        self._parent_scorer = ShadowRetriever(flags=flags, policy=self.policy)

    def decide(
        self,
        *,
        official_candidate_ids: tuple[str, ...],
        evidence: DiscoveryEvidenceView,
        query: FactorDAGQuery,
        candidates: tuple[RetrievalCandidate, ...],
        actions: tuple[FrozenRetrieverActionTemplateV1, ...],
        action_template_event_hashes: tuple[str, ...],
        seed: int,
        candidate_budget: int,
    ) -> ActionShadowDecisionV5:
        if not candidates or len(candidates) != len(actions):
            raise ValueError("action-level candidates and templates must align")
        if len(actions) != len(action_template_event_hashes):
            raise ValueError("action-level templates and events must align")
        if not 1 <= candidate_budget <= min(
            len(candidates), self.policy.maximum_candidate_budget
        ):
            raise ValueError("action-level candidate budget is out of bounds")
        action_ids = [action.action_id for action in actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("action-level Retriever actions must be unique")

        components: list[RetrievalComponent] = []
        official_output_hash: str | None = None
        for candidate, action in zip(candidates, actions, strict=True):
            self._validate_binding(candidate, action, evidence=evidence)
            singleton = self._parent_scorer.decide(
                official_candidate_ids=official_candidate_ids,
                evidence=evidence,
                query=query,
                candidates=(candidate,),
                seed=seed,
                candidate_budget=1,
            )
            if official_output_hash is None:
                official_output_hash = singleton.official_output_hash
            elif official_output_hash != singleton.official_output_hash:
                raise RuntimeError("action-level official output hash is unstable")
            components.append(
                replace(
                    singleton.components[0],
                    selected=False,
                    selection_propensity=0.0,
                    warnings=tuple(
                        sorted(
                            set(singleton.components[0].warnings)
                            | {"FROZEN_ACTION_TEMPLATE_BOUND_V1"}
                        )
                    ),
                )
            )

        selected, propensities = self._sample_actions(
            components,
            seed=seed,
            budget=candidate_budget,
        )
        selected_set = set(selected)
        completed = tuple(
            replace(
                component,
                selected=component.action_id in selected_set,
                selection_propensity=propensities.get(component.action_id, 0.0),
            )
            for component in components
        )
        parents_by_action = {
            component.action_id: component.factor_spec_id for component in completed
        }
        selected_parents = tuple(parents_by_action[action_id] for action_id in selected)
        assert official_output_hash is not None
        content = {
            "schema_version": "retriever_action_shadow_decision.v5",
            "selected_action_ids": list(selected),
            "selected_parent_factor_spec_ids": list(selected_parents),
            "action_template_event_hashes": list(action_template_event_hashes),
            "seed": seed,
            "policy_version": self.policy.policy_version,
            "policy_hash": self.policy.policy_hash,
            "policy_config": self.policy.to_dict(),
            "eligible_event_watermark": evidence.source_watermark,
            "data_snapshot_hash": evidence.data_snapshot_hash,
            "candidate_budget": candidate_budget,
            "official_output_hash": official_output_hash,
            "propensity_semantics": "sequential_action_softmax_draw_probability.v1",
            "components": [component.to_dict() for component in completed],
            "shadow_only": True,
        }
        return ActionShadowDecisionV5(
            schema_version="retriever_action_shadow_decision.v5",
            selected_action_ids=selected,
            selected_parent_factor_spec_ids=selected_parents,
            action_template_event_hashes=action_template_event_hashes,
            seed=seed,
            policy_version=self.policy.policy_version,
            policy_hash=self.policy.policy_hash,
            policy_config=self.policy.to_dict(),
            eligible_event_watermark=evidence.source_watermark,
            data_snapshot_hash=evidence.data_snapshot_hash,
            candidate_budget=candidate_budget,
            official_output_hash=official_output_hash,
            propensity_semantics="sequential_action_softmax_draw_probability.v1",
            components=completed,
            decision_hash=canonical_json_hash(content),
            shadow_only=True,
        )

    def _validate_binding(
        self,
        candidate: RetrievalCandidate,
        action: FrozenRetrieverActionTemplateV1,
        *,
        evidence: DiscoveryEvidenceView,
    ) -> None:
        if action.identity_action:
            raise ValueError("identity/no-op action is ineligible for action-level retrieval")
        if (
            candidate.action_id != action.action_id
            or candidate.factor_spec_id != action.parent_factor_spec_id
            or candidate.motif != action.expected_motif
        ):
            raise ValueError("Retriever candidate differs from its frozen action template")
        if (
            action.eligible_event_watermark != evidence.source_watermark
            or action.data_snapshot_hash != evidence.data_snapshot_hash
            or action.retrieval_policy_hash != self.policy.policy_hash
        ):
            raise ValueError("frozen action scope or policy differs from Retriever decision")

    def _sample_actions(
        self,
        components: list[RetrievalComponent],
        *,
        seed: int,
        budget: int,
    ) -> tuple[tuple[str, ...], dict[str, float]]:
        remaining = [item for item in components if item.veto_reason is None]
        rng = random.Random(seed)
        selected: list[str] = []
        propensities: dict[str, float] = {}
        for _ in range(min(budget, len(remaining))):
            maximum = max(item.action_score for item in remaining)
            weights = [
                math.exp(
                    (item.action_score - maximum) / self.policy.softmax_temperature
                )
                for item in remaining
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
            selected.append(chosen.action_id)
            propensities[chosen.action_id] = probabilities[chosen_index]
        return tuple(selected), propensities


__all__ = ["ActionShadowDecisionV5", "ActionShadowRetrieverV5"]
