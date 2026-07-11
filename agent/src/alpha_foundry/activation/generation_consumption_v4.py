"""Exact treatment generation bound to the outcome-free v7 schedule decision."""

from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping, cast

from src.alpha_foundry.activation.generation_consumption_v2 import (
    ActivationGenerationConsumptionV2,
    ActivationTreatmentGeneratorV2,
)
from src.alpha_foundry.activation.generation_consumption_v3 import (
    _DecisionAdapter,
    _InputAdapter,
    _SourceInput,
)
from src.alpha_foundry.activation.runner import ActivationArmRequest, TrainValidActivationScope
from src.alpha_foundry.mutators import SEED_MUTATION_TEMPLATE_REGISTRY_V1
from src.alpha_foundry.retrieval.service_v5 import RecordedRetrieverDecisionV5
from src.alpha_foundry.retrieval.service_v7 import RecordedRetrieverDecisionV7
from src.alpha_foundry.search import AlphaFoundrySearchResult
from src.alpha_foundry.search_lifecycle import EventSourcedSearchLifecycle
from src.alpha_foundry.seed_bank import AlphaSeed
from src.research_ledger.hash_utils import canonical_json_hash


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_FEATURE_FAILURE = "RETRIEVER_FEATURE_SOURCE_UNVERIFIED"


@dataclass(frozen=True)
class _EventAdapter:
    event_hash: str
    run_id: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class _ControlAdapter:
    policy: Any


@dataclass(frozen=True)
class ActivationGenerationConsumptionV4:
    base: ActivationGenerationConsumptionV2
    retriever_input_bundle_hash: str
    schedule_event_hash: str
    schedule_hash: str
    feature_source_event_hash: str
    feature_source_hash: str
    feature_snapshot_event_hash: str
    feature_snapshot_hash: str
    feature_policy_hash: str
    feature_scorecard_event_hashes: tuple[str, ...]
    evidence_hash: str

    def __post_init__(self) -> None:
        hashes = (
            self.retriever_input_bundle_hash, self.schedule_event_hash,
            self.schedule_hash, self.feature_source_event_hash,
            self.feature_source_hash, self.feature_snapshot_event_hash,
            self.feature_snapshot_hash, self.feature_policy_hash,
            self.evidence_hash, *self.feature_scorecard_event_hashes,
        )
        if any(_HASH_RE.fullmatch(value) is None for value in hashes):
            raise ValueError("schedule-bound generation contains an invalid hash")
        if (
            self.base.control_evidence_event_hash != self.schedule_event_hash
            or _FEATURE_FAILURE in self.base.source_failure_codes
            or len(self.feature_scorecard_event_hashes)
            != len(set(self.feature_scorecard_event_hashes))
        ):
            raise ValueError("schedule-bound generation authority is inconsistent")
        required_failures = self._required_failure_codes()
        if not required_failures.issubset(self.base.source_failure_codes):
            raise ValueError(
                "schedule-bound generation omits deterministic source failures"
            )
        if self.evidence_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("schedule-bound generation hash mismatch")

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)

    def _required_failure_codes(self) -> set[str]:
        failures: set[str] = set()
        records = self.base.generated_candidates
        record_action_ids = tuple(str(item["action_id"]) for item in records)
        record_parent_ids = tuple(
            str(item["parent_factor_spec_id"]) for item in records
        )
        if (
            self.base.consumed_action_ids != record_action_ids
            or self.base.consumed_parent_factor_spec_ids != record_parent_ids
            or record_action_ids != self.base.selected_action_ids
        ):
            failures.add("SELECTED_ACTION_SET_NOT_FULLY_CONSUMED")
        if len(records) != self.base.candidate_budget:
            failures.add("FIXED_CANDIDATE_BUDGET_INCOMPLETE")
        if len(records) != len(self.base.selected_actions):
            failures.add("SELECTED_ACTION_OUTPUT_BINDING_INCOMPLETE")
        else:
            for record, bound in zip(
                records, self.base.selected_actions, strict=True
            ):
                action = bound.action
                if (
                    record["action_id"] != action.action_id
                    or record["action_event_hash"] != bound.action_event_hash
                    or record["candidate_id"] != action.expected_candidate_id
                    or record["parent_factor_spec_id"]
                    != action.parent_factor_spec_id
                    or record["template_id"] != action.template_id
                    or record["formula_hash"] != action.expected_formula_hash
                ):
                    failures.add("SELECTED_ACTION_OUTPUT_BINDING_INCOMPLETE")
                    break
        return failures

    def _content_dict(self) -> dict[str, Any]:
        common = self.base.to_dict()
        common.pop("evidence_hash")
        common.pop("control_evidence_event_hash")
        common["schema_version"] = "activation_generation_consumption.v4"
        return {
            **common,
            "retriever_input_bundle_hash": self.retriever_input_bundle_hash,
            "schedule_event_hash": self.schedule_event_hash,
            "schedule_hash": self.schedule_hash,
            "feature_source_event_hash": self.feature_source_event_hash,
            "feature_source_hash": self.feature_source_hash,
            "feature_snapshot_event_hash": self.feature_snapshot_event_hash,
            "feature_snapshot_hash": self.feature_snapshot_hash,
            "feature_policy_hash": self.feature_policy_hash,
            "feature_scorecard_event_hashes": list(self.feature_scorecard_event_hashes),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "evidence_hash": self.evidence_hash}

    @classmethod
    def build(cls, *, base: ActivationGenerationConsumptionV2, **sources: Any) -> "ActivationGenerationConsumptionV4":
        values = dict(sources)
        common = base.to_dict()
        common.pop("evidence_hash")
        common.pop("control_evidence_event_hash")
        common["schema_version"] = "activation_generation_consumption.v4"
        content = {
            **common,
            **values,
            "feature_scorecard_event_hashes": list(
                values["feature_scorecard_event_hashes"]
            ),
        }
        return cls(base=base, evidence_hash=canonical_json_hash(content), **values)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ActivationGenerationConsumptionV4":
        source_fields = {
            "retriever_input_bundle_hash", "schedule_event_hash", "schedule_hash",
            "feature_source_event_hash", "feature_source_hash",
            "feature_snapshot_event_hash", "feature_snapshot_hash",
            "feature_policy_hash", "feature_scorecard_event_hashes",
        }
        common = {
            "schema_version", "plan_hash", "pair_id", "run_group_id",
            "mechanism_family", "dag_region", "execution_run_id",
            "retriever_decision_event_hash", "retriever_decision_hash",
            "generator_policy_hash", "selected_action_ids",
            "selected_action_event_hashes", "selected_actions",
            "selected_parent_factor_spec_ids", "selected_parents",
            "consumed_action_ids", "consumed_parent_factor_spec_ids",
            "generated_candidates", "candidate_budget", "compute_budget",
            "source_failure_codes", "source_complete", "evidence_hash",
        }
        if (
            set(raw) != common | source_fields
            or raw.get("schema_version") != "activation_generation_consumption.v4"
            or not isinstance(raw.get("feature_scorecard_event_hashes"), list)
        ):
            raise ValueError("schedule-bound generation has an invalid schema")
        base_raw = {key: raw[key] for key in common}
        base_raw["schema_version"] = "activation_generation_consumption.v2"
        base_raw["control_evidence_event_hash"] = raw["schedule_event_hash"]
        base_raw["evidence_hash"] = canonical_json_hash(
            base_raw, exclude_keys=("evidence_hash",)
        )
        base = ActivationGenerationConsumptionV2.from_dict(base_raw)
        return cls(
            base=base,
            retriever_input_bundle_hash=str(raw["retriever_input_bundle_hash"]),
            schedule_event_hash=str(raw["schedule_event_hash"]),
            schedule_hash=str(raw["schedule_hash"]),
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


@dataclass(frozen=True)
class ActivationTreatmentGenerationRunV4:
    search_result: AlphaFoundrySearchResult
    evidence: ActivationGenerationConsumptionV4


class ActivationTreatmentGeneratorV4:
    def run(
        self, *, request: ActivationArmRequest, scope: TrainValidActivationScope,
        recorded_decision: RecordedRetrieverDecisionV7,
        parent_seeds: Iterable[AlphaSeed], lifecycle: EventSourcedSearchLifecycle,
    ) -> ActivationTreatmentGenerationRunV4:
        source = recorded_decision.feature_source
        schedule = recorded_decision.schedule
        event_adapter = _EventAdapter(
            event_hash=recorded_decision.event.event_hash,
            run_id=recorded_decision.event.run_id,
            payload=MappingProxyType({
                "decision_hash": recorded_decision.event.payload["decision_hash"],
                "control_evidence_event_hash": recorded_decision.input_bundle.schedule_event_hash,
            }),
        )
        adapter = _DecisionAdapter(
            event=event_adapter,
            decision=recorded_decision.decision,
            input_bundle=_InputAdapter(
                action_template_event_hashes=source.action_event_hashes,
                retriever_input=_SourceInput(source.candidates),
            ),
            control_evidence=_ControlAdapter(schedule.policy),
            actions=recorded_decision.actions,
        )
        generated = ActivationTreatmentGeneratorV2().run(
            request=request, scope=scope,
            recorded_decision=cast(RecordedRetrieverDecisionV5, adapter),
            parent_seeds=parent_seeds, lifecycle=lifecycle,
        )
        raw = generated.evidence.to_dict()
        failures = set(str(item) for item in raw["source_failure_codes"])
        if _FEATURE_FAILURE not in failures:
            raise ValueError("compatibility run did not preserve feature-source cap")
        failures.remove(_FEATURE_FAILURE)
        generator_policy = {
            "schema_version": "activation_exact_action_generator_policy.v4",
            "generator_version": schedule.policy.generator_version,
            "mutator_version": "exact_selected_action_mutator.v2",
            "template_registry_hash": SEED_MUTATION_TEMPLATE_REGISTRY_V1.registry_hash,
            "selection_order": "retriever_v7_schedule_bound_selected_action_order.v1",
            "retriever_input_bundle_hash": recorded_decision.input_bundle.bundle_hash,
            "schedule_event_hash": recorded_decision.input_bundle.schedule_event_hash,
            "schedule_hash": schedule.schedule_hash,
            "feature_source_event_hash": recorded_decision.input_bundle.feature_source_event_hash,
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
        evidence = ActivationGenerationConsumptionV4.build(
            base=base,
            retriever_input_bundle_hash=recorded_decision.input_bundle.bundle_hash,
            schedule_event_hash=recorded_decision.input_bundle.schedule_event_hash,
            schedule_hash=schedule.schedule_hash,
            feature_source_event_hash=recorded_decision.input_bundle.feature_source_event_hash,
            feature_source_hash=source.source_hash,
            feature_snapshot_event_hash=source.snapshot_event_hash,
            feature_snapshot_hash=source.snapshot_hash,
            feature_policy_hash=source.feature_policy_hash,
            feature_scorecard_event_hashes=source.scorecard_event_hashes,
        )
        return ActivationTreatmentGenerationRunV4(generated.search_result, evidence)


__all__ = [
    "ActivationGenerationConsumptionV4", "ActivationTreatmentGenerationRunV4",
    "ActivationTreatmentGeneratorV4",
]
