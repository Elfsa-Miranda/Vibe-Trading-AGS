"""Terminal train/valid factual evidence separated from raw audit lineage."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from src.alpha_foundry.dag.model import FactorDAGProjection
from src.research_ledger.events.model import VerifiedEventSubsequence
from src.research_ledger.hash_utils import canonical_json_hash


_FACTUAL_VIEW_AUTHORITY = object()
FACTUAL_PROJECTOR_POLICY_HASH = canonical_json_hash(
    {
        "schema_version": "factual_memory_projector_policy.v1",
        "allowed_terminal_statuses": ["reject", "success"],
        "allowed_data_scopes": ["train_valid", "valid"],
    }
)


@dataclass(frozen=True)
class DiscoveryFactorEvidence:
    factor_spec_id: str
    definition_event_hash: str
    evaluation_event_hash: str
    terminal_event_hash: str
    scorecard_hash: str
    data_scope: str


@dataclass(frozen=True, init=False)
class FactualMemoryView:
    dag: FactorDAGProjection
    evidence_by_factor_spec_id: Mapping[str, DiscoveryFactorEvidence]
    source_subsequence_hash: str
    projector_policy_hash: str
    _authority: object = field(init=False, repr=False, compare=False)

    def __init__(
        self,
        *,
        dag: FactorDAGProjection,
        evidence_by_factor_spec_id: Mapping[str, DiscoveryFactorEvidence],
        source_subsequence_hash: str,
        projector_policy_hash: str,
        _authority: object,
    ) -> None:
        if _authority is not _FACTUAL_VIEW_AUTHORITY:
            raise TypeError("factual memory must be built from terminal discovery events")
        object.__setattr__(self, "dag", dag)
        object.__setattr__(self, "source_subsequence_hash", source_subsequence_hash)
        object.__setattr__(self, "projector_policy_hash", projector_policy_hash)
        object.__setattr__(
            self,
            "evidence_by_factor_spec_id",
            MappingProxyType(dict(evidence_by_factor_spec_id)),
        )
        object.__setattr__(self, "_authority", _authority)

    @classmethod
    def from_terminal_discovery_events(
        cls,
        dag: FactorDAGProjection,
        events: VerifiedEventSubsequence,
    ) -> "FactualMemoryView":
        if not isinstance(events, VerifiedEventSubsequence) or not events.is_authorized():
            raise TypeError("factual memory requires a store-verified event subsequence")
        ordered = list(events.events)
        evaluations = {
            event.event_hash: event
            for event in ordered
            if event.event_type == "EvaluationRecorded"
            and event.payload["data_scope"] in {"valid", "train_valid"}
        }
        eligible: dict[str, DiscoveryFactorEvidence] = {}
        for terminal in ordered:
            if terminal.event_type != "TrialTerminated" or terminal.payload["status"] not in {"success", "reject"}:
                continue
            evaluation_hash = terminal.payload["evaluation_event_hash"]
            if evaluation_hash is None:
                # Rejected trials may cite their evaluation through hardened
                # process outcomes rather than the v1 terminal payload.
                outcomes = [
                    event for event in ordered
                    if event.event_type == "ProcessOutcomeRecordedV2"
                    and event.payload["terminal_event_hash"] == terminal.event_hash
                ]
                if len(outcomes) != 1:
                    continue
                evaluation_hash = outcomes[0].payload["evaluation_event_hash"]
            evaluation = evaluations.get(str(evaluation_hash))
            if evaluation is None or evaluation.payload["trial_id"] != terminal.payload["trial_id"]:
                continue
            factor_id = str(evaluation.payload["factor_spec_id"])
            node = dag.factor_nodes.get(factor_id)
            if node is None:
                continue
            eligible[factor_id] = DiscoveryFactorEvidence(
                factor_spec_id=factor_id,
                definition_event_hash=node.definition_event_hash,
                evaluation_event_hash=evaluation.event_hash,
                terminal_event_hash=terminal.event_hash,
                scorecard_hash=str(evaluation.payload["scorecard_hash"]),
                data_scope=str(evaluation.payload["data_scope"]),
            )
        return cls(
            dag=dag,
            evidence_by_factor_spec_id=eligible,
            source_subsequence_hash=events.subsequence_hash,
            projector_policy_hash=FACTUAL_PROJECTOR_POLICY_HASH,
            _authority=_FACTUAL_VIEW_AUTHORITY,
        )

    def is_authorized(self) -> bool:
        return self._authority is _FACTUAL_VIEW_AUTHORITY

    def factor_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.evidence_by_factor_spec_id))

    def definition_event_hash(self, factor_spec_id: str) -> str:
        return self.evidence_by_factor_spec_id[factor_spec_id].definition_event_hash

    @property
    def content_hash(self) -> str:
        return canonical_json_hash(
            {
                "schema_version": "factual_memory_view.v2",
                "source_subsequence_hash": self.source_subsequence_hash,
                "projector_policy_hash": self.projector_policy_hash,
                "dag_projection_hash": self.dag.projection_hash,
                "evidence": [
                    {
                        "factor_spec_id": item.factor_spec_id,
                        "definition_event_hash": item.definition_event_hash,
                        "evaluation_event_hash": item.evaluation_event_hash,
                        "terminal_event_hash": item.terminal_event_hash,
                        "scorecard_hash": item.scorecard_hash,
                        "data_scope": item.data_scope,
                    }
                    for _, item in sorted(self.evidence_by_factor_spec_id.items())
                ],
            }
        )


__all__ = [
    "DiscoveryFactorEvidence",
    "FACTUAL_PROJECTOR_POLICY_HASH",
    "FactualMemoryView",
]
