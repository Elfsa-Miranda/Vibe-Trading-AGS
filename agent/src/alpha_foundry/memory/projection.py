"""Replayable episodic projection over hardened terminal discovery evidence."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable

from src.alpha_foundry.dsl.diff import ast_diff_from_dict
from src.alpha_foundry.memory.model import (
    EpisodicProjection,
    ProcessMemoryObservation,
    ProcessPosterior,
    _build_episodic_projection,
)
from src.alpha_foundry.memory.motif import derive_motif
from src.research_ledger.events import ResearchEventEnvelope
from src.research_ledger.events.model import VerifiedEventSubsequence
from src.research_ledger.hash_utils import canonical_json_hash


class EpisodicProjector:
    def __init__(
        self,
        *,
        minimum_effective_count: int = 3,
        max_positive_adjustment: float = 0.25,
        negative_veto_threshold: float = -0.2,
        veto_confidence_threshold: float = 0.8,
    ) -> None:
        if minimum_effective_count < 2 or max_positive_adjustment < 0:
            raise ValueError("invalid episodic memory policy")
        if not -1.0 <= negative_veto_threshold < 0.0:
            raise ValueError("negative veto threshold must be in [-1, 0)")
        if not 0.0 < veto_confidence_threshold <= 1.0:
            raise ValueError("veto confidence threshold must be in (0, 1]")
        self.minimum_effective_count = minimum_effective_count
        self.max_positive_adjustment = max_positive_adjustment
        self.negative_veto_threshold = negative_veto_threshold
        self.veto_confidence_threshold = veto_confidence_threshold

    @property
    def policy_hash(self) -> str:
        return canonical_json_hash(
            {
                "schema_version": "episodic_projector_policy.v1",
                "minimum_effective_count": self.minimum_effective_count,
                "max_positive_adjustment": self.max_positive_adjustment,
                "negative_veto_threshold": self.negative_veto_threshold,
                "veto_confidence_threshold": self.veto_confidence_threshold,
            }
        )

    def project(
        self,
        events: Iterable[ResearchEventEnvelope] | VerifiedEventSubsequence,
    ) -> EpisodicProjection:
        ordered = list(events)
        source_subsequence_hash = (
            events.subsequence_hash
            if isinstance(events, VerifiedEventSubsequence)
            else canonical_json_hash(
                {
                    "schema_version": "unverified_event_sequence.v1",
                    "event_hashes": [event.event_hash for event in ordered],
                }
            )
        )
        by_hash = {event.event_hash: event for event in ordered}
        if len(by_hash) != len(ordered):
            raise ValueError("episodic replay contains duplicate event hashes")
        actions = {
            event.entity_id: event
            for event in ordered
            if event.event_type == "ProcessActionFrozenV2"
        }
        definitions = {
            event.entity_id: event
            for event in ordered
            if event.event_type == "FactorDefinitionRecorded"
        }
        observations: list[ProcessMemoryObservation] = []
        seen_actions: set[str] = set()
        for event in ordered:
            if event.event_type != "ProcessOutcomeRecordedV2":
                continue
            payload = event.payload
            action_id = str(payload["action_id"])
            if action_id in seen_actions:
                raise ValueError("episodic replay contains duplicate action outcomes")
            seen_actions.add(action_id)
            action = actions.get(action_id)
            terminal = by_hash.get(str(payload["terminal_event_hash"]))
            evaluation = by_hash.get(str(payload["evaluation_event_hash"]))
            derivation = by_hash.get(str(payload["derivation_event_hash"]))
            if action is None or terminal is None or evaluation is None or derivation is None:
                raise ValueError("episodic outcome lacks cited immutable evidence")
            if terminal.event_type != "TrialTerminated" or evaluation.event_type != "EvaluationRecorded" or derivation.event_type != "DerivationRecorded":
                raise ValueError("episodic outcome cites the wrong evidence type")
            child_id = str(payload["child_factor_spec_id"])
            parent_id = str(action.payload["parent_factor_spec_id"])
            for name in (
                "trial_id", "policy_hash", "utility_policy_hash",
                "data_snapshot_hash", "regime_config_hash", "run_group_id",
            ):
                if payload[name] != action.payload[name]:
                    raise ValueError("episodic outcome does not match its frozen action")
            if (
                terminal.payload["trial_id"] != payload["trial_id"]
                or terminal.payload["status"] not in {"success", "reject"}
                or evaluation.payload["trial_id"] != payload["trial_id"]
                or evaluation.payload["factor_spec_id"] != child_id
                or evaluation.payload["data_scope"] != payload["data_scope"]
                or evaluation.payload["scorecard_hash"] != payload["scorecard_hash"]
                or derivation.payload["child_factor_spec_id"] != child_id
                or derivation.payload["trial_terminal_event_hash"] != terminal.event_hash
                or parent_id not in derivation.payload["parent_factor_spec_ids"]
            ):
                raise ValueError("episodic outcome evidence binding is inconsistent")
            parent = definitions.get(parent_id)
            child = definitions.get(child_id)
            if parent is None or child is None:
                raise ValueError("episodic outcome lacks canonical definitions")
            diff = ast_diff_from_dict(
                payload["ast_diff"],
                parent_ast=parent.payload["metadata"]["canonical_ast"],
                child_ast=child.payload["metadata"]["canonical_ast"],
            )
            if canonical_json_hash(diff.to_dict()) != payload["ast_diff_hash"]:
                raise ValueError("episodic outcome AST diff hash is invalid")
            if (
                diff.parent_expression_id != parent.payload["expression_id"]
                or diff.child_expression_id != child.payload["expression_id"]
                or diff.grammar_version != child.payload["grammar_version"]
                or diff.grammar_hash != child.payload["grammar_hash"]
            ):
                raise ValueError("episodic outcome AST identity binding is inconsistent")
            motif = derive_motif(diff)
            base = float(action.payload["base_expected_utility"])
            utility = float(payload["observed_validation_utility"])
            if not math.isfinite(base) or not math.isfinite(utility):
                raise ValueError("episodic utilities must be finite")
            context = canonical_json_hash(
                {
                    "parent_factor_spec_id": parent_id,
                    "policy_hash": action.payload["policy_hash"],
                    "utility_policy_hash": action.payload["utility_policy_hash"],
                    "data_snapshot_hash": action.payload["data_snapshot_hash"],
                    "regime_config_hash": action.payload["regime_config_hash"],
                }
            )
            observations.append(
                ProcessMemoryObservation(
                    parent_context_hash=context,
                    parent_factor_spec_id=parent_id,
                    child_factor_spec_id=child_id,
                    derivation_event_hash=derivation.event_hash,
                    evaluation_event_hash=evaluation.event_hash,
                    scorecard_hash=str(payload["scorecard_hash"]),
                    ast_diff_hash=motif.ast_diff_hash,
                    motif_version=motif.motif_version,
                    motif=motif.motif,
                    base_expected_utility=base,
                    observed_validation_utility=utility,
                    residual=utility - base,
                    terminal_status=str(terminal.payload["status"]),
                    failure_codes=tuple(str(code) for code in terminal.payload["reason_codes"]),
                    regime_config_hash=action.payload["regime_config_hash"],
                    data_snapshot_hash=str(action.payload["data_snapshot_hash"]),
                    eligible_event_watermark=str(action.payload["eligible_event_watermark"]),
                    run_group_id=str(action.payload["run_group_id"]),
                    policy_hash=str(action.payload["policy_hash"]),
                    available_at=str(payload["available_at"]),
                )
            )
        posteriors = self._posteriors(observations)
        state = {
            "schema_version": "episodic_process_projection.v2",
            "source_subsequence_hash": source_subsequence_hash,
            "projector_policy_hash": self.policy_hash,
            "source_event_hashes": [event.event_hash for event in ordered],
            "observations": [observation.__dict__ for observation in observations],
            "posteriors": [posterior.__dict__ for posterior in posteriors],
        }
        return _build_episodic_projection(
            schema_version="episodic_process_projection.v2",
            source_watermark_event_hash=ordered[-1].event_hash if ordered else None,
            source_subsequence_hash=source_subsequence_hash,
            projector_policy_hash=self.policy_hash,
            observations=tuple(observations),
            posteriors=tuple(posteriors),
            projection_hash=canonical_json_hash(state),
        )

    def _posteriors(
        self, observations: list[ProcessMemoryObservation]
    ) -> list[ProcessPosterior]:
        grouped: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for observation in observations:
            if observation.residual is not None:
                grouped[(observation.parent_context_hash, observation.motif)][
                    observation.run_group_id
                ].append(observation.residual)
        result: list[ProcessPosterior] = []
        for (context, motif), run_groups in sorted(grouped.items()):
            group_means = [
                sum(values) / len(values) for _, values in sorted(run_groups.items())
            ]
            count = len(group_means)
            observation_count = sum(len(values) for values in run_groups.values())
            mean = sum(group_means) / count
            standard_error: float | None = None
            if count >= 2:
                variance = sum((value - mean) ** 2 for value in group_means) / (count - 1)
                standard_error = math.sqrt(variance / count)
            sample_gate = min(1.0, count / self.minimum_effective_count)
            uncertainty_gate = 0.0 if standard_error is None else 1.0 / (
                1.0 + standard_error / max(abs(mean), 0.05)
            )
            confidence = sample_gate * uncertainty_gate
            eligible = count >= self.minimum_effective_count
            positive = (
                min(self.max_positive_adjustment, max(0.0, mean) * confidence)
                if eligible
                else 0.0
            )
            hard_veto = (
                eligible
                and confidence >= self.veto_confidence_threshold
                and mean <= self.negative_veto_threshold
            )
            result.append(
                ProcessPosterior(
                    context,
                    motif,
                    count,
                    observation_count,
                    mean,
                    standard_error,
                    confidence,
                    positive,
                    hard_veto,
                )
            )
        return result


__all__ = ["EpisodicProjector"]
