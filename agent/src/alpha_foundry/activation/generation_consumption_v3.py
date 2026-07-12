"""Exact treatment generation bound to an authoritative v6 feature source."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping, cast

from src.alpha_foundry.activation.generation_consumption_v2 import (
    ActivationGenerationConsumptionV2,
    ActivationTreatmentGeneratorV2,
)
from src.alpha_foundry.activation.runner import ActivationArmRequest, TrainValidActivationScope
from src.alpha_foundry.mutators import SEED_MUTATION_TEMPLATE_REGISTRY_V1
from src.alpha_foundry.retrieval.action_template_v1 import FrozenRetrieverActionTemplateV1
from src.alpha_foundry.retrieval.model import RetrievalCandidate
from src.alpha_foundry.retrieval.service_v5 import RecordedRetrieverDecisionV5
from src.alpha_foundry.retrieval.service_v6 import RecordedRetrieverDecisionV6
from src.alpha_foundry.search import AlphaFoundrySearchResult
from src.alpha_foundry.search_lifecycle import EventSourcedSearchLifecycle
from src.alpha_foundry.seed_bank import AlphaSeed
from src.research_ledger.hash_utils import canonical_json_hash


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_FEATURE_FAILURE = "RETRIEVER_FEATURE_SOURCE_UNVERIFIED"


@dataclass(frozen=True)
class ActivationGenerationConsumptionV3:
    base: ActivationGenerationConsumptionV2
    retriever_input_bundle_hash: str
    feature_source_event_hash: str
    feature_source_hash: str
    feature_snapshot_event_hash: str
    feature_snapshot_hash: str
    feature_policy_hash: str
    feature_scorecard_event_hashes: tuple[str, ...]
    evidence_hash: str

    def __post_init__(self) -> None:
        hashes = (
            self.retriever_input_bundle_hash,
            self.feature_source_event_hash,
            self.feature_source_hash,
            self.feature_snapshot_event_hash,
            self.feature_snapshot_hash,
            self.feature_policy_hash,
            self.evidence_hash,
            *self.feature_scorecard_event_hashes,
        )
        if any(_HASH_RE.fullmatch(value) is None for value in hashes):
            raise ValueError("source-bound generation contains an invalid hash")
        if (
            _FEATURE_FAILURE in self.base.source_failure_codes
            or len(self.feature_scorecard_event_hashes)
            != len(set(self.feature_scorecard_event_hashes))
        ):
            raise ValueError("source-bound generation feature evidence is inconsistent")
        if self.evidence_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("source-bound generation evidence hash mismatch")

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)

    def _content_dict(self) -> dict[str, Any]:
        common = self.base.to_dict()
        common.pop("evidence_hash")
        common["schema_version"] = "activation_generation_consumption.v3"
        return {
            **common,
            "retriever_input_bundle_hash": self.retriever_input_bundle_hash,
            "feature_source_event_hash": self.feature_source_event_hash,
            "feature_source_hash": self.feature_source_hash,
            "feature_snapshot_event_hash": self.feature_snapshot_event_hash,
            "feature_snapshot_hash": self.feature_snapshot_hash,
            "feature_policy_hash": self.feature_policy_hash,
            "feature_scorecard_event_hashes": list(
                self.feature_scorecard_event_hashes
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "evidence_hash": self.evidence_hash}

    @classmethod
    def build(
        cls,
        *,
        base: ActivationGenerationConsumptionV2,
        retriever_input_bundle_hash: str,
        feature_source_event_hash: str,
        feature_source_hash: str,
        feature_snapshot_event_hash: str,
        feature_snapshot_hash: str,
        feature_policy_hash: str,
        feature_scorecard_event_hashes: tuple[str, ...],
    ) -> "ActivationGenerationConsumptionV3":
        common = base.to_dict()
        common.pop("evidence_hash")
        common["schema_version"] = "activation_generation_consumption.v3"
        content = {
            **common,
            "retriever_input_bundle_hash": retriever_input_bundle_hash,
            "feature_source_event_hash": feature_source_event_hash,
            "feature_source_hash": feature_source_hash,
            "feature_snapshot_event_hash": feature_snapshot_event_hash,
            "feature_snapshot_hash": feature_snapshot_hash,
            "feature_policy_hash": feature_policy_hash,
            "feature_scorecard_event_hashes": list(feature_scorecard_event_hashes),
        }
        return cls(
            base=base,
            retriever_input_bundle_hash=retriever_input_bundle_hash,
            feature_source_event_hash=feature_source_event_hash,
            feature_source_hash=feature_source_hash,
            feature_snapshot_event_hash=feature_snapshot_event_hash,
            feature_snapshot_hash=feature_snapshot_hash,
            feature_policy_hash=feature_policy_hash,
            feature_scorecard_event_hashes=feature_scorecard_event_hashes,
            evidence_hash=canonical_json_hash(content),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ActivationGenerationConsumptionV3":
        feature_fields = {
            "retriever_input_bundle_hash", "feature_source_event_hash",
            "feature_source_hash", "feature_snapshot_event_hash",
            "feature_snapshot_hash", "feature_policy_hash",
            "feature_scorecard_event_hashes",
        }
        expected_common = {
            "schema_version", "plan_hash", "pair_id", "run_group_id",
            "mechanism_family", "dag_region", "execution_run_id",
            "retriever_decision_event_hash", "retriever_decision_hash",
            "control_evidence_event_hash", "generator_policy_hash",
            "selected_action_ids", "selected_action_event_hashes",
            "selected_actions", "selected_parent_factor_spec_ids",
            "selected_parents", "consumed_action_ids",
            "consumed_parent_factor_spec_ids", "generated_candidates",
            "candidate_budget", "compute_budget", "source_failure_codes",
            "source_complete", "evidence_hash",
        }
        if (
            set(raw) != expected_common | feature_fields
            or raw.get("schema_version") != "activation_generation_consumption.v3"
            or not isinstance(raw.get("feature_scorecard_event_hashes"), list)
        ):
            raise ValueError("source-bound generation evidence has an invalid schema")
        base_raw = {key: raw[key] for key in expected_common}
        base_raw["schema_version"] = "activation_generation_consumption.v2"
        base_raw["evidence_hash"] = canonical_json_hash(
            base_raw, exclude_keys=("evidence_hash",)
        )
        base = ActivationGenerationConsumptionV2.from_dict(base_raw)
        result = cls(
            base=base,
            retriever_input_bundle_hash=str(raw["retriever_input_bundle_hash"]),
            feature_source_event_hash=str(raw["feature_source_event_hash"]),
            feature_source_hash=str(raw["feature_source_hash"]),
            feature_snapshot_event_hash=str(raw["feature_snapshot_event_hash"]),
            feature_snapshot_hash=str(raw["feature_snapshot_hash"]),
            feature_policy_hash=str(raw["feature_policy_hash"]),
            feature_scorecard_event_hashes=tuple(
                str(item) for item in raw["feature_scorecard_event_hashes"]
            ),
            evidence_hash=str(raw["evidence_hash"]),
        )
        return result


@dataclass(frozen=True)
class _SourceInput:
    candidates: tuple[RetrievalCandidate, ...]


@dataclass(frozen=True)
class _InputAdapter:
    action_template_event_hashes: tuple[str, ...]
    retriever_input: _SourceInput


@dataclass(frozen=True)
class _DecisionAdapter:
    event: Any
    decision: Any
    input_bundle: _InputAdapter
    control_evidence: Any
    actions: tuple[FrozenRetrieverActionTemplateV1, ...]


@dataclass(frozen=True)
class ActivationTreatmentGenerationRunV3:
    search_result: AlphaFoundrySearchResult
    evidence: ActivationGenerationConsumptionV3


class ActivationTreatmentGeneratorV3:
    def run(
        self,
        *,
        request: ActivationArmRequest,
        scope: TrainValidActivationScope,
        recorded_decision: RecordedRetrieverDecisionV6,
        parent_seeds: Iterable[AlphaSeed],
        lifecycle: EventSourcedSearchLifecycle,
    ) -> ActivationTreatmentGenerationRunV3:
        source = recorded_decision.feature_source
        adapter = _DecisionAdapter(
            event=recorded_decision.event,
            decision=recorded_decision.decision,
            input_bundle=_InputAdapter(
                action_template_event_hashes=source.action_event_hashes,
                retriever_input=_SourceInput(source.candidates),
            ),
            control_evidence=recorded_decision.control_evidence,
            actions=recorded_decision.actions,
        )
        generated = ActivationTreatmentGeneratorV2().run(
            request=request,
            scope=scope,
            recorded_decision=cast(RecordedRetrieverDecisionV5, adapter),
            parent_seeds=parent_seeds,
            lifecycle=lifecycle,
        )
        raw = generated.evidence.to_dict()
        failures = set(str(item) for item in raw["source_failure_codes"])
        if _FEATURE_FAILURE not in failures:
            raise ValueError("v2 compatibility run did not preserve feature-source cap")
        failures.remove(_FEATURE_FAILURE)
        generator_policy = {
            "schema_version": "activation_exact_action_generator_policy.v3",
            "generator_version": recorded_decision.control_evidence.policy.generator_version,
            "mutator_version": "exact_selected_action_mutator.v2",
            "template_registry_hash": SEED_MUTATION_TEMPLATE_REGISTRY_V1.registry_hash,
            "selection_order": "retriever_v6_source_bound_selected_action_order.v1",
            "retriever_input_bundle_hash": recorded_decision.input_bundle.bundle_hash,
            "feature_source_event_hash": recorded_decision.event.payload[
                "feature_source_event_hash"
            ],
            "feature_source_hash": source.source_hash,
            "selected_action_ids": list(generated.evidence.selected_action_ids),
            "selected_action_event_hashes": list(
                generated.evidence.selected_action_event_hashes
            ),
            "max_candidates": request.candidate_budget,
            "trial_budget": request.compute_budget,
        }
        raw["generator_policy_hash"] = canonical_json_hash(generator_policy)
        raw["source_failure_codes"] = sorted(failures)
        raw["source_complete"] = not failures
        raw["evidence_hash"] = canonical_json_hash(raw, exclude_keys=("evidence_hash",))
        base = ActivationGenerationConsumptionV2.from_dict(raw)
        evidence = ActivationGenerationConsumptionV3.build(
            base=base,
            retriever_input_bundle_hash=recorded_decision.input_bundle.bundle_hash,
            feature_source_event_hash=str(
                recorded_decision.event.payload["feature_source_event_hash"]
            ),
            feature_source_hash=source.source_hash,
            feature_snapshot_event_hash=source.snapshot_event_hash,
            feature_snapshot_hash=source.snapshot_hash,
            feature_policy_hash=source.feature_policy_hash,
            feature_scorecard_event_hashes=source.scorecard_event_hashes,
        )
        return ActivationTreatmentGenerationRunV3(generated.search_result, evidence)


__all__ = [
    "ActivationGenerationConsumptionV3",
    "ActivationTreatmentGenerationRunV3",
    "ActivationTreatmentGeneratorV3",
]
