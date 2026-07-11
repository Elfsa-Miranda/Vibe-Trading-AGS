"""Exact consumption of selected v5 Retriever actions by the treatment search."""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from src.alpha_foundry.activation.runner import (
    ActivationArmRequest,
    TrainValidActivationScope,
    activation_arm_execution_run_id,
)
from src.alpha_foundry.candidate_pool import CandidateExpression, make_candidate
from src.alpha_foundry.dsl.identity import build_expression_identity
from src.alpha_foundry.mutators import (
    SEED_MUTATION_TEMPLATE_REGISTRY_V1,
    SeedMutator,
)
from src.alpha_foundry.retrieval.action_template_v1 import (
    FrozenRetrieverActionTemplateV1,
)
from src.alpha_foundry.retrieval.service_v5 import RecordedRetrieverDecisionV5
from src.alpha_foundry.search import AlphaFoundrySearch, AlphaFoundrySearchResult
from src.alpha_foundry.search_lifecycle import EventSourcedSearchLifecycle
from src.alpha_foundry.seed_bank import AlphaSeed, SeedBank
from src.research_ledger.hash_utils import canonical_json_hash


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class BoundSelectedActionV2:
    action_event_hash: str
    action: FrozenRetrieverActionTemplateV1

    def __post_init__(self) -> None:
        if _HASH_RE.fullmatch(self.action_event_hash) is None:
            raise ValueError("selected action event hash is invalid")
        if self.action.identity_action:
            raise ValueError("identity/no-op cannot be consumed as a selected action")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "BoundSelectedActionV2":
        if set(raw) != {"action_event_hash", "action"} or not isinstance(
            raw["action"], Mapping
        ):
            raise ValueError("selected action evidence has an invalid schema")
        return cls(
            action_event_hash=str(raw["action_event_hash"]),
            action=FrozenRetrieverActionTemplateV1.from_dict(raw["action"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_event_hash": self.action_event_hash,
            "action": self.action.to_dict(),
        }


class ExactSelectedActionMutatorV2(SeedMutator):
    """Render exactly one authoritative template for each action seed."""

    mutator_version = "exact_selected_action_mutator.v2"

    def __init__(self, actions: tuple[BoundSelectedActionV2, ...]) -> None:
        super().__init__(max_candidates_per_seed=1)
        if not actions:
            raise ValueError("exact action mutator requires a non-empty selection")
        action_ids = [item.action.action_id for item in actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("exact action mutator requires unique action IDs")
        self.actions = actions
        self._by_id = {item.action.action_id: item for item in actions}

    def action_seeds(
        self,
        parent_catalog: Mapping[str, AlphaSeed],
    ) -> list[AlphaSeed]:
        seeds: list[AlphaSeed] = []
        for bound in self.actions:
            parent_id = bound.action.parent_factor_spec_id
            parent = parent_catalog.get(parent_id)
            if parent is None:
                raise ValueError("selected action parent is missing from the seed catalog")
            seeds.append(
                AlphaSeed(
                    seed_id=bound.action.action_id,
                    formula=parent.formula,
                    source=parent.source,
                    parent_seed_id=parent_id,
                )
            )
        return seeds

    def mutate(self, seed: AlphaSeed) -> list[CandidateExpression]:
        bound = self._by_id.get(seed.seed_id)
        if bound is None:
            raise ValueError("exact action mutator received an unselected action seed")
        action = bound.action
        if seed.parent_seed_id != action.parent_factor_spec_id:
            raise ValueError("exact action seed parent differs from frozen action")
        template = SEED_MUTATION_TEMPLATE_REGISTRY_V1.get(action.template_id)
        formula = template.render(seed.formula)
        if formula != action.expected_formula or len(formula) > 512:
            raise ValueError("exact action render differs from frozen expected formula")
        candidate = make_candidate(
            action.parent_factor_spec_id,
            formula,
            mutation=action.template_id,
        )
        if (
            candidate.candidate_id != action.expected_candidate_id
            or candidate.formula_hash != action.expected_formula_hash
        ):
            raise ValueError("exact action candidate identity differs from frozen action")
        return [
            replace(
                candidate,
                metadata={
                    **candidate.metadata,
                    "retriever_action_id": action.action_id,
                    "retriever_action_event_hash": bound.action_event_hash,
                    "template_registry_hash": action.template_registry_hash,
                },
            )
        ]


@dataclass(frozen=True)
class ActivationGenerationConsumptionV2:
    schema_version: str
    plan_hash: str
    pair_id: str
    run_group_id: str
    mechanism_family: str
    dag_region: str
    execution_run_id: str
    retriever_decision_event_hash: str
    retriever_decision_hash: str
    control_evidence_event_hash: str
    generator_policy_hash: str
    selected_action_ids: tuple[str, ...]
    selected_action_event_hashes: tuple[str, ...]
    selected_actions: tuple[BoundSelectedActionV2, ...]
    selected_parent_factor_spec_ids: tuple[str, ...]
    selected_parents: tuple[Mapping[str, str], ...]
    consumed_action_ids: tuple[str, ...]
    consumed_parent_factor_spec_ids: tuple[str, ...]
    generated_candidates: tuple[Mapping[str, str], ...]
    candidate_budget: int
    compute_budget: int
    source_failure_codes: tuple[str, ...]
    source_complete: bool
    evidence_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "activation_generation_consumption.v2":
            raise ValueError("unsupported exact generation-consumption schema")
        if self.execution_run_id != activation_arm_execution_run_id(
            plan_hash=self.plan_hash,
            run_group_id=self.run_group_id,
            arm="treatment",
        ):
            raise ValueError("exact generation execution identity is invalid")
        if self.pair_id != f"{self.run_group_id}:{self.mechanism_family}:{self.dag_region}":
            raise ValueError("exact generation pair identity is invalid")
        if self.source_failure_codes != tuple(sorted(set(self.source_failure_codes))):
            raise ValueError("exact generation failures must be sorted and unique")
        if self.source_complete != (not self.source_failure_codes):
            raise ValueError("exact generation completeness must derive from failures")
        action_ids = tuple(item.action.action_id for item in self.selected_actions)
        action_hashes = tuple(item.action_event_hash for item in self.selected_actions)
        parent_ids = tuple(
            item.action.parent_factor_spec_id for item in self.selected_actions
        )
        if (
            not action_ids
            or action_ids != self.selected_action_ids
            or action_hashes != self.selected_action_event_hashes
            or parent_ids != self.selected_parent_factor_spec_ids
            or len(action_ids) != len(set(action_ids))
            or len(action_hashes) != len(set(action_hashes))
        ):
            raise ValueError("exact generation selected action binding is inconsistent")
        normalized_parents: list[Mapping[str, str]] = []
        for parent in self.selected_parents:
            if set(parent) != {
                "factor_spec_id", "formula", "expression_id", "source_hash",
            }:
                raise ValueError("exact generation parent schema is invalid")
            if build_expression_identity(str(parent["formula"])).expression_id != str(
                parent["expression_id"]
            ):
                raise ValueError("exact generation parent expression is invalid")
            normalized_parents.append(MappingProxyType(dict(parent)))
        if tuple(item["factor_spec_id"] for item in normalized_parents) != parent_ids:
            raise ValueError("exact generation parent evidence order differs")
        object.__setattr__(self, "selected_parents", tuple(normalized_parents))

        normalized_candidates: list[Mapping[str, str]] = []
        candidate_ids: set[str] = set()
        terminal_hashes: set[str] = set()
        for record in self.generated_candidates:
            if set(record) != {
                "action_id", "action_event_hash", "candidate_id",
                "parent_factor_spec_id", "template_id", "formula_hash",
                "terminal_event_hash",
            }:
                raise ValueError("exact generation candidate schema is invalid")
            candidate_id = str(record["candidate_id"])
            terminal_hash = str(record["terminal_event_hash"])
            if candidate_id in candidate_ids or terminal_hash in terminal_hashes:
                raise ValueError("exact generation candidates must be unique")
            candidate_ids.add(candidate_id)
            terminal_hashes.add(terminal_hash)
            normalized_candidates.append(MappingProxyType(dict(record)))
        object.__setattr__(self, "generated_candidates", tuple(normalized_candidates))
        if self.evidence_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("exact generation evidence hash mismatch")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_hash": self.plan_hash,
            "pair_id": self.pair_id,
            "run_group_id": self.run_group_id,
            "mechanism_family": self.mechanism_family,
            "dag_region": self.dag_region,
            "execution_run_id": self.execution_run_id,
            "retriever_decision_event_hash": self.retriever_decision_event_hash,
            "retriever_decision_hash": self.retriever_decision_hash,
            "control_evidence_event_hash": self.control_evidence_event_hash,
            "generator_policy_hash": self.generator_policy_hash,
            "selected_action_ids": list(self.selected_action_ids),
            "selected_action_event_hashes": list(self.selected_action_event_hashes),
            "selected_actions": [item.to_dict() for item in self.selected_actions],
            "selected_parent_factor_spec_ids": list(
                self.selected_parent_factor_spec_ids
            ),
            "selected_parents": [dict(item) for item in self.selected_parents],
            "consumed_action_ids": list(self.consumed_action_ids),
            "consumed_parent_factor_spec_ids": list(
                self.consumed_parent_factor_spec_ids
            ),
            "generated_candidates": [dict(item) for item in self.generated_candidates],
            "candidate_budget": self.candidate_budget,
            "compute_budget": self.compute_budget,
            "source_failure_codes": list(self.source_failure_codes),
            "source_complete": self.source_complete,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "evidence_hash": self.evidence_hash}

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
    ) -> "ActivationGenerationConsumptionV2":
        expected = {
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
        list_fields = (
            "selected_action_ids", "selected_action_event_hashes",
            "selected_actions", "selected_parent_factor_spec_ids",
            "selected_parents", "consumed_action_ids",
            "consumed_parent_factor_spec_ids", "generated_candidates",
            "source_failure_codes",
        )
        if (
            set(raw) != expected
            or any(not isinstance(raw[name], list) for name in list_fields)
            or not isinstance(raw["source_complete"], bool)
            or isinstance(raw["candidate_budget"], bool)
            or not isinstance(raw["candidate_budget"], int)
            or isinstance(raw["compute_budget"], bool)
            or not isinstance(raw["compute_budget"], int)
        ):
            raise ValueError("exact generation evidence has an invalid schema")
        return cls(
            schema_version=str(raw["schema_version"]),
            plan_hash=str(raw["plan_hash"]),
            pair_id=str(raw["pair_id"]),
            run_group_id=str(raw["run_group_id"]),
            mechanism_family=str(raw["mechanism_family"]),
            dag_region=str(raw["dag_region"]),
            execution_run_id=str(raw["execution_run_id"]),
            retriever_decision_event_hash=str(raw["retriever_decision_event_hash"]),
            retriever_decision_hash=str(raw["retriever_decision_hash"]),
            control_evidence_event_hash=str(raw["control_evidence_event_hash"]),
            generator_policy_hash=str(raw["generator_policy_hash"]),
            selected_action_ids=tuple(str(item) for item in raw["selected_action_ids"]),
            selected_action_event_hashes=tuple(
                str(item) for item in raw["selected_action_event_hashes"]
            ),
            selected_actions=tuple(
                BoundSelectedActionV2.from_dict(item)
                for item in raw["selected_actions"]
                if isinstance(item, Mapping)
            ),
            selected_parent_factor_spec_ids=tuple(
                str(item) for item in raw["selected_parent_factor_spec_ids"]
            ),
            selected_parents=tuple(
                dict(item) for item in raw["selected_parents"]
                if isinstance(item, Mapping)
            ),
            consumed_action_ids=tuple(
                str(item) for item in raw["consumed_action_ids"]
            ),
            consumed_parent_factor_spec_ids=tuple(
                str(item) for item in raw["consumed_parent_factor_spec_ids"]
            ),
            generated_candidates=tuple(
                dict(item) for item in raw["generated_candidates"]
                if isinstance(item, Mapping)
            ),
            candidate_budget=int(raw["candidate_budget"]),
            compute_budget=int(raw["compute_budget"]),
            source_failure_codes=tuple(
                str(item) for item in raw["source_failure_codes"]
            ),
            source_complete=raw["source_complete"],
            evidence_hash=str(raw["evidence_hash"]),
        )


@dataclass(frozen=True)
class ActivationTreatmentGenerationRunV2:
    search_result: AlphaFoundrySearchResult
    evidence: ActivationGenerationConsumptionV2


class ActivationTreatmentGeneratorV2:
    def run(
        self,
        *,
        request: ActivationArmRequest,
        scope: TrainValidActivationScope,
        recorded_decision: RecordedRetrieverDecisionV5,
        parent_seeds: Iterable[AlphaSeed],
        lifecycle: EventSourcedSearchLifecycle,
    ) -> ActivationTreatmentGenerationRunV2:
        if request.arm != "treatment":
            raise ValueError("exact generation-consumption is treatment-only")
        if recorded_decision.event.run_id != request.execution_run_id:
            raise ValueError("v5 Retriever decision is not attributed to treatment")
        if recorded_decision.decision.policy_hash != request.policy_hash:
            raise ValueError("treatment request and v5 Retriever policy differ")
        if (
            lifecycle.data_snapshot_hash != scope.train_snapshot_hash
            or recorded_decision.decision.data_snapshot_hash
            != scope.train_snapshot_hash
        ):
            raise ValueError("exact treatment generation snapshot differs")
        control_policy = recorded_decision.control_evidence.policy
        if (
            control_policy.max_candidates != request.candidate_budget
            or control_policy.trial_budget != request.compute_budget
        ):
            raise ValueError("control and exact treatment budgets differ")

        selected_ids = recorded_decision.decision.selected_action_ids
        if not selected_ids:
            raise ValueError("exact treatment generation requires selected actions")
        action_event_by_id = {
            action.action_id: event_hash
            for action, event_hash in zip(
                recorded_decision.actions,
                recorded_decision.input_bundle.action_template_event_hashes,
                strict=True,
            )
        }
        action_by_id = {action.action_id: action for action in recorded_decision.actions}
        selected = tuple(
            BoundSelectedActionV2(
                action_event_hash=action_event_by_id[action_id],
                action=action_by_id[action_id],
            )
            for action_id in selected_ids
        )
        if len({item.action.expected_candidate_id for item in selected}) != len(selected):
            raise ValueError("selected actions produce duplicate candidate identity")

        normalized_parents = tuple(parent_seeds)
        catalog = {seed.seed_id: seed for seed in normalized_parents}
        if len(catalog) != len(normalized_parents):
            raise ValueError("exact treatment parent catalog contains duplicates")
        source_by_action = {
            candidate.action_id: candidate
            for candidate in recorded_decision.input_bundle.retriever_input.candidates
        }
        selected_parent_records: list[Mapping[str, str]] = []
        for bound in selected:
            action = bound.action
            parent = catalog.get(action.parent_factor_spec_id)
            source = source_by_action.get(action.action_id)
            if parent is None or source is None:
                raise ValueError("selected action lacks parent or v5 input evidence")
            if build_expression_identity(parent.formula).canonical_ast != source.canonical_ast:
                raise ValueError("selected action parent differs from frozen canonical AST")
            selected_parent_records.append(
                {
                    "factor_spec_id": parent.seed_id,
                    "formula": parent.formula,
                    "expression_id": build_expression_identity(parent.formula).expression_id,
                    "source_hash": canonical_json_hash({"source": parent.source}),
                }
            )

        mutator = ExactSelectedActionMutatorV2(selected)
        search = AlphaFoundrySearch(
            seed_bank=SeedBank(mutator.action_seeds(catalog)),
            mutator=mutator,
            max_candidates=request.candidate_budget,
            trial_budget=request.compute_budget,
            lifecycle=lifecycle,
            run_id=request.execution_run_id,
        )
        result = search.generate()
        failures: set[str] = {"RETRIEVER_FEATURE_SOURCE_UNVERIFIED"}
        if len(result.attempts) != len(result.candidates):
            failures.add("TERMINAL_ATTEMPT_COVERAGE_INCOMPLETE")
        records = tuple(
            {
                "action_id": bound.action.action_id,
                "action_event_hash": bound.action_event_hash,
                "candidate_id": candidate.candidate_id,
                "parent_factor_spec_id": candidate.parent_seed_id,
                "template_id": str(candidate.metadata["mutation"]),
                "formula_hash": candidate.formula_hash,
                "terminal_event_hash": attempt.terminal_event_hash,
            }
            for bound, candidate, attempt in zip(
                selected,
                result.candidates,
                result.attempts,
                strict=True,
            )
        )
        for bound, candidate in zip(selected, result.candidates, strict=True):
            if (
                candidate.candidate_id != bound.action.expected_candidate_id
                or candidate.formula_hash != bound.action.expected_formula_hash
                or candidate.parent_seed_id != bound.action.parent_factor_spec_id
                or candidate.metadata.get("retriever_action_id")
                != bound.action.action_id
                or candidate.metadata.get("retriever_action_event_hash")
                != bound.action_event_hash
            ):
                failures.add("SELECTED_ACTION_OUTPUT_BINDING_INCOMPLETE")
        consumed_actions = tuple(item["action_id"] for item in records)
        consumed_parents = tuple(item["parent_factor_spec_id"] for item in records)
        if consumed_actions != selected_ids:
            failures.add("SELECTED_ACTION_SET_NOT_FULLY_CONSUMED")
        if len(records) != request.candidate_budget:
            failures.add("FIXED_CANDIDATE_BUDGET_INCOMPLETE")
        generator_policy = {
            "schema_version": "activation_exact_action_generator_policy.v2",
            "generator_version": control_policy.generator_version,
            "mutator_version": mutator.mutator_version,
            "template_registry_hash": (
                SEED_MUTATION_TEMPLATE_REGISTRY_V1.registry_hash
            ),
            "selection_order": "retriever_v5_selected_action_order.v1",
            "selected_action_ids": list(selected_ids),
            "selected_action_event_hashes": [
                item.action_event_hash for item in selected
            ],
            "max_candidates": request.candidate_budget,
            "trial_budget": request.compute_budget,
        }
        codes = tuple(sorted(failures))
        content = {
            "schema_version": "activation_generation_consumption.v2",
            "plan_hash": request.plan_hash,
            "pair_id": request.pair_id,
            "run_group_id": request.run_group_id,
            "mechanism_family": request.mechanism_family,
            "dag_region": request.dag_region,
            "execution_run_id": request.execution_run_id,
            "retriever_decision_event_hash": recorded_decision.event.event_hash,
            "retriever_decision_hash": str(
                recorded_decision.event.payload["decision_hash"]
            ),
            "control_evidence_event_hash": str(
                recorded_decision.event.payload["control_evidence_event_hash"]
            ),
            "generator_policy_hash": canonical_json_hash(generator_policy),
            "selected_action_ids": list(selected_ids),
            "selected_action_event_hashes": [
                item.action_event_hash for item in selected
            ],
            "selected_actions": [item.to_dict() for item in selected],
            "selected_parent_factor_spec_ids": [
                item.action.parent_factor_spec_id for item in selected
            ],
            "selected_parents": [dict(item) for item in selected_parent_records],
            "consumed_action_ids": list(consumed_actions),
            "consumed_parent_factor_spec_ids": list(consumed_parents),
            "generated_candidates": [dict(item) for item in records],
            "candidate_budget": request.candidate_budget,
            "compute_budget": request.compute_budget,
            "source_failure_codes": list(codes),
            "source_complete": not codes,
        }
        evidence = ActivationGenerationConsumptionV2(
            schema_version="activation_generation_consumption.v2",
            plan_hash=request.plan_hash,
            pair_id=request.pair_id,
            run_group_id=request.run_group_id,
            mechanism_family=request.mechanism_family,
            dag_region=request.dag_region,
            execution_run_id=request.execution_run_id,
            retriever_decision_event_hash=recorded_decision.event.event_hash,
            retriever_decision_hash=str(
                recorded_decision.event.payload["decision_hash"]
            ),
            control_evidence_event_hash=str(
                recorded_decision.event.payload["control_evidence_event_hash"]
            ),
            generator_policy_hash=canonical_json_hash(generator_policy),
            selected_action_ids=selected_ids,
            selected_action_event_hashes=tuple(
                item.action_event_hash for item in selected
            ),
            selected_actions=selected,
            selected_parent_factor_spec_ids=tuple(
                item.action.parent_factor_spec_id for item in selected
            ),
            selected_parents=tuple(selected_parent_records),
            consumed_action_ids=consumed_actions,
            consumed_parent_factor_spec_ids=consumed_parents,
            generated_candidates=records,
            candidate_budget=request.candidate_budget,
            compute_budget=request.compute_budget,
            source_failure_codes=codes,
            source_complete=not codes,
            evidence_hash=canonical_json_hash(content),
        )
        return ActivationTreatmentGenerationRunV2(result, evidence)


__all__ = [
    "ActivationGenerationConsumptionV2",
    "ActivationTreatmentGenerationRunV2",
    "ActivationTreatmentGeneratorV2",
    "BoundSelectedActionV2",
    "ExactSelectedActionMutatorV2",
]
