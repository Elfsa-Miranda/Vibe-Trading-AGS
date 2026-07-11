"""Source-bound proof that a treatment search consumed a v4 topology decision."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from src.alpha_foundry.activation.runner import (
    ActivationArmRequest,
    TrainValidActivationScope,
    activation_arm_execution_run_id,
)
from src.alpha_foundry.dsl.identity import build_expression_identity
from src.alpha_foundry.mutators import SeedMutator
from src.alpha_foundry.retrieval.service_v4 import RecordedRetrieverDecisionV4
from src.alpha_foundry.search import AlphaFoundrySearch, AlphaFoundrySearchResult
from src.alpha_foundry.search_lifecycle import EventSourcedSearchLifecycle
from src.alpha_foundry.seed_bank import AlphaSeed, SeedBank
from src.research_ledger.hash_utils import canonical_json_hash


@dataclass(frozen=True)
class ActivationGenerationConsumptionV1:
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
    selected_parent_factor_spec_ids: tuple[str, ...]
    selected_parents: tuple[Mapping[str, str], ...]
    consumed_parent_factor_spec_ids: tuple[str, ...]
    generated_candidates: tuple[Mapping[str, str], ...]
    candidate_budget: int
    compute_budget: int
    source_failure_codes: tuple[str, ...]
    source_complete: bool
    evidence_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "activation_generation_consumption.v1":
            raise ValueError("unsupported Activation generation-consumption schema")
        expected_execution = activation_arm_execution_run_id(
            plan_hash=self.plan_hash,
            run_group_id=self.run_group_id,
            arm="treatment",
        )
        if self.execution_run_id != expected_execution:
            raise ValueError("generation-consumption execution identity is invalid")
        if self.pair_id != (
            f"{self.run_group_id}:{self.mechanism_family}:{self.dag_region}"
        ):
            raise ValueError("generation-consumption pair identity is invalid")
        if self.source_failure_codes != tuple(sorted(set(self.source_failure_codes))):
            raise ValueError("generation-consumption failures must be sorted and unique")
        if self.source_complete != (not self.source_failure_codes):
            raise ValueError("generation-consumption completeness must derive from failures")
        if self.evidence_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("generation-consumption evidence hash mismatch")
        normalized: list[Mapping[str, str]] = []
        candidate_ids: set[str] = set()
        terminal_hashes: set[str] = set()
        for record in self.generated_candidates:
            if set(record) != {
                "candidate_id", "parent_factor_spec_id", "formula_hash",
                "terminal_event_hash",
            }:
                raise ValueError("generation-consumption candidate schema is invalid")
            candidate_id = str(record["candidate_id"])
            terminal_hash = str(record["terminal_event_hash"])
            if candidate_id in candidate_ids or terminal_hash in terminal_hashes:
                raise ValueError("generation-consumption candidates must be unique")
            candidate_ids.add(candidate_id)
            terminal_hashes.add(terminal_hash)
            normalized.append(MappingProxyType(dict(record)))
        object.__setattr__(self, "generated_candidates", tuple(normalized))
        normalized_parents: list[Mapping[str, str]] = []
        for record in self.selected_parents:
            if set(record) != {
                "factor_spec_id", "formula", "expression_id", "source_hash",
            }:
                raise ValueError("generation-consumption parent schema is invalid")
            if build_expression_identity(str(record["formula"])).expression_id != str(
                record["expression_id"]
            ):
                raise ValueError("generation-consumption parent formula hash is invalid")
            normalized_parents.append(MappingProxyType(dict(record)))
        if tuple(item["factor_spec_id"] for item in normalized_parents) != (
            self.selected_parent_factor_spec_ids
        ):
            raise ValueError("generation-consumption parent order differs from selection")
        object.__setattr__(self, "selected_parents", tuple(normalized_parents))

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
            "selected_parent_factor_spec_ids": list(
                self.selected_parent_factor_spec_ids
            ),
            "selected_parents": [dict(item) for item in self.selected_parents],
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
    def from_dict(cls, raw: Mapping[str, Any]) -> "ActivationGenerationConsumptionV1":
        expected = {
            "schema_version", "plan_hash", "pair_id", "run_group_id",
            "mechanism_family", "dag_region", "execution_run_id",
            "retriever_decision_event_hash",
            "retriever_decision_hash", "control_evidence_event_hash",
            "generator_policy_hash", "selected_parent_factor_spec_ids",
            "selected_parents", "consumed_parent_factor_spec_ids",
            "generated_candidates", "candidate_budget", "compute_budget",
            "source_failure_codes", "source_complete", "evidence_hash",
        }
        if set(raw) != expected or any(
            not isinstance(raw[name], list)
            for name in (
                "selected_parent_factor_spec_ids", "selected_parents",
                "consumed_parent_factor_spec_ids", "generated_candidates",
                "source_failure_codes",
            )
        ):
            raise ValueError("generation-consumption evidence has an invalid schema")
        if not isinstance(raw["source_complete"], bool):
            raise ValueError("generation-consumption completeness must be boolean")
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
            selected_parent_factor_spec_ids=tuple(
                str(item) for item in raw["selected_parent_factor_spec_ids"]
            ),
            selected_parents=tuple(dict(item) for item in raw["selected_parents"]),
            consumed_parent_factor_spec_ids=tuple(
                str(item) for item in raw["consumed_parent_factor_spec_ids"]
            ),
            generated_candidates=tuple(
                dict(item) for item in raw["generated_candidates"]
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
class ActivationTreatmentGenerationRunV1:
    search_result: AlphaFoundrySearchResult
    evidence: ActivationGenerationConsumptionV1


class ActivationTreatmentGeneratorV1:
    """Run the exact flat generator over only v4-selected topology parents."""

    def run(
        self,
        *,
        request: ActivationArmRequest,
        scope: TrainValidActivationScope,
        recorded_decision: RecordedRetrieverDecisionV4,
        parent_seeds: Iterable[AlphaSeed],
        lifecycle: EventSourcedSearchLifecycle,
    ) -> ActivationTreatmentGenerationRunV1:
        if request.arm != "treatment":
            raise ValueError("generation-consumption evidence is treatment-only")
        if recorded_decision.event.run_id != request.execution_run_id:
            raise ValueError("retriever decision is not attributed to the treatment run")
        if recorded_decision.decision.policy_hash != request.policy_hash:
            raise ValueError("treatment request and retriever policy differ")
        if lifecycle.data_snapshot_hash != scope.train_snapshot_hash:
            raise ValueError("treatment generation must use the frozen train snapshot")
        if recorded_decision.decision.data_snapshot_hash != scope.train_snapshot_hash:
            raise ValueError("retriever decision and treatment snapshot differ")
        control_policy = recorded_decision.control_evidence.policy
        if (
            control_policy.max_candidates != request.candidate_budget
            or control_policy.trial_budget != request.compute_budget
        ):
            raise ValueError("control and treatment generator budgets differ")

        selected = recorded_decision.decision.selected_factor_spec_ids
        if not selected:
            raise ValueError("treatment generation requires a non-empty selection")
        normalized_seeds = tuple(parent_seeds)
        catalog = {seed.seed_id: seed for seed in normalized_seeds}
        if len(catalog) != len(normalized_seeds):
            raise ValueError("treatment parent catalog contains duplicate identities")
        missing = [factor_id for factor_id in selected if factor_id not in catalog]
        if missing:
            raise ValueError("retriever-selected parent is missing from the seed catalog")
        candidate_evidence = {
            item.factor_spec_id: item
            for item in recorded_decision.input_bundle.retriever_input.candidates
        }
        ordered_seeds = [catalog[factor_id] for factor_id in selected]
        for seed in ordered_seeds:
            source = candidate_evidence.get(seed.seed_id)
            if source is None:
                raise ValueError("selected parent lacks frozen retriever input evidence")
            if build_expression_identity(seed.formula).canonical_ast != source.canonical_ast:
                raise ValueError("selected parent formula differs from its frozen canonical AST")

        search = AlphaFoundrySearch(
            seed_bank=SeedBank(ordered_seeds),
            mutator=SeedMutator(
                max_candidates_per_seed=control_policy.max_candidates_per_seed
            ),
            max_candidates=request.candidate_budget,
            trial_budget=request.compute_budget,
            lifecycle=lifecycle,
            run_id=request.execution_run_id,
        )
        result = search.generate()
        # RetrieverInputBundleV4 content-addresses caller-supplied panels,
        # embeddings, base scores, and cost hashes, but cannot independently
        # reproduce them from the frozen data snapshot and registered providers.
        # ProcessActionFrozenV2 also has no pre-generation motif/template field,
        # so action_id, context, and motif cannot be source-bound before the edit.
        # The search may be replayed to prove selected-parent consumption, but
        # that is not sufficient to make its treatment source activation-eligible.
        failures: set[str] = {
            "RETRIEVER_ACTION_SOURCE_UNVERIFIED",
            "RETRIEVER_FEATURE_SOURCE_UNVERIFIED",
        }
        if len(result.attempts) != len(result.candidates):
            failures.add("TERMINAL_ATTEMPT_COVERAGE_INCOMPLETE")
        records = tuple(
            {
                "candidate_id": candidate.candidate_id,
                "parent_factor_spec_id": candidate.parent_seed_id,
                "formula_hash": candidate.formula_hash,
                "terminal_event_hash": attempt.terminal_event_hash,
            }
            for candidate, attempt in zip(result.candidates, result.attempts)
        )
        consumed = tuple(dict.fromkeys(item["parent_factor_spec_id"] for item in records))
        if consumed != selected:
            failures.add("SELECTED_PARENT_SET_NOT_FULLY_CONSUMED")
        if len(records) != request.candidate_budget:
            failures.add("FIXED_CANDIDATE_BUDGET_INCOMPLETE")
        generator_policy_hash = canonical_json_hash(
            {
                "generator_version": control_policy.generator_version,
                "mutator_version": control_policy.mutator_version,
                "mutation_templates": list(control_policy.mutation_templates),
                "max_candidates_per_seed": control_policy.max_candidates_per_seed,
                "max_candidates": request.candidate_budget,
                "trial_budget": request.compute_budget,
            }
        )
        codes = tuple(sorted(failures))
        selected_parent_records = tuple(
            {
                "factor_spec_id": seed.seed_id,
                "formula": seed.formula,
                "expression_id": build_expression_identity(seed.formula).expression_id,
                "source_hash": canonical_json_hash({"source": seed.source}),
            }
            for seed in ordered_seeds
        )
        content = {
            "schema_version": "activation_generation_consumption.v1",
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
            "generator_policy_hash": generator_policy_hash,
            "selected_parent_factor_spec_ids": list(selected),
            "selected_parents": [dict(item) for item in selected_parent_records],
            "consumed_parent_factor_spec_ids": list(consumed),
            "generated_candidates": [dict(item) for item in records],
            "candidate_budget": request.candidate_budget,
            "compute_budget": request.compute_budget,
            "source_failure_codes": list(codes),
            "source_complete": not codes,
        }
        evidence = ActivationGenerationConsumptionV1(
            schema_version="activation_generation_consumption.v1",
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
            generator_policy_hash=generator_policy_hash,
            selected_parent_factor_spec_ids=selected,
            selected_parents=selected_parent_records,
            consumed_parent_factor_spec_ids=consumed,
            generated_candidates=records,
            candidate_budget=request.candidate_budget,
            compute_budget=request.compute_budget,
            source_failure_codes=codes,
            source_complete=not codes,
            evidence_hash=canonical_json_hash(content),
        )
        return ActivationTreatmentGenerationRunV1(result, evidence)


__all__ = [
    "ActivationGenerationConsumptionV1",
    "ActivationTreatmentGenerationRunV1",
    "ActivationTreatmentGeneratorV1",
]
