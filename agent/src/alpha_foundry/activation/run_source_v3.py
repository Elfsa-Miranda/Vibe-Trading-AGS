"""Formal Activation run-source authority for Phase 11.

Unlike the legacy v2 audit, this verifier accepts only the current protected
Retriever v7 / pre-arm Flat schedule and the production evaluator's
dossier-bound QualityDecision v4 event family.  It derives candidate yield from
exact ledger events and never from a run summary.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal

from src.alpha_foundry.activation.model import TERMINAL_STATUSES
from src.alpha_foundry.activation.runner import activation_arm_execution_run_id
from src.research_ledger.events import ResearchEventEnvelope, ResearchEventStore
from src.research_ledger.hash_utils import canonical_json_hash


ArmV3 = Literal["flat", "topology"]
_QUALIFIED_TIERS = {"candidate_zoo", "paper_candidate", "forward_track"}


@dataclass(frozen=True)
class FormalActivationRunSourceV3:
    schema_version: Literal["formal_activation_run_source.v3"]
    plan_hash: str
    pair_id: str
    run_group_id: str
    arm: ArmV3
    execution_run_id: str
    source_watermark_event_hash: str
    retrieval_authority_event_hashes: tuple[str, ...]
    terminal_event_hashes: tuple[str, ...]
    evaluation_event_hashes: tuple[str, ...]
    quality_decision_event_hashes: tuple[str, ...]
    terminal_dossier_event_hashes: tuple[str, ...]
    resource_event_hashes: tuple[str, ...]
    derived_terminal_status_counts: tuple[tuple[str, int], ...]
    derived_candidate_ids: tuple[str, ...]
    derived_effective_candidate_ids: tuple[str, ...]
    source_failure_codes: tuple[str, ...]
    source_complete: bool
    audit_hash: str

    def __post_init__(self) -> None:
        for values in (
            self.retrieval_authority_event_hashes,
            self.terminal_event_hashes,
            self.evaluation_event_hashes,
            self.quality_decision_event_hashes,
            self.terminal_dossier_event_hashes,
            self.resource_event_hashes,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("formal Activation source hashes must be sorted and unique")
        if self.source_failure_codes != tuple(sorted(set(self.source_failure_codes))):
            raise ValueError("formal Activation source failures must be sorted and unique")
        if self.source_complete != (not self.source_failure_codes):
            raise ValueError("formal Activation source completeness must derive from failures")
        if self.audit_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("formal Activation source audit hash mismatch")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_hash": self.plan_hash,
            "pair_id": self.pair_id,
            "run_group_id": self.run_group_id,
            "arm": self.arm,
            "execution_run_id": self.execution_run_id,
            "source_watermark_event_hash": self.source_watermark_event_hash,
            "retrieval_authority_event_hashes": list(self.retrieval_authority_event_hashes),
            "terminal_event_hashes": list(self.terminal_event_hashes),
            "evaluation_event_hashes": list(self.evaluation_event_hashes),
            "quality_decision_event_hashes": list(self.quality_decision_event_hashes),
            "terminal_dossier_event_hashes": list(self.terminal_dossier_event_hashes),
            "resource_event_hashes": list(self.resource_event_hashes),
            "derived_terminal_status_counts": [list(item) for item in self.derived_terminal_status_counts],
            "derived_candidate_ids": list(self.derived_candidate_ids),
            "derived_effective_candidate_ids": list(self.derived_effective_candidate_ids),
            "source_failure_codes": list(self.source_failure_codes),
            "source_complete": self.source_complete,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "audit_hash": self.audit_hash}


class FormalActivationRunSourceAuditorV3:
    """Rebuild an arm manifest from a verified event-chain prefix."""

    def __init__(self, store: ResearchEventStore) -> None:
        self.store = store

    def audit(
        self,
        *,
        plan_hash: str,
        pair_id: str,
        run_group_id: str,
        arm: ArmV3,
        candidate_budget: int,
        retrieval_authority_event_hashes: tuple[str, ...],
        terminal_event_hashes: tuple[str, ...],
        evaluation_event_hashes: tuple[str, ...],
        quality_decision_event_hashes: tuple[str, ...],
        terminal_dossier_event_hashes: tuple[str, ...],
        resource_event_hashes: tuple[str, ...],
    ) -> FormalActivationRunSourceV3:
        events = self.store.query_events()
        failures: set[str] = set()
        if not self.store.verify_chain():
            failures.add("RESEARCH_EVENT_CHAIN_INVALID")
        by_hash = {event.event_hash: event for event in events}
        order = {event.event_hash: index for index, event in enumerate(events)}
        execution_arm: Literal["control", "treatment"] = (
            "control" if arm == "flat" else "treatment"
        )
        execution_run_id = activation_arm_execution_run_id(
            plan_hash=plan_hash,
            run_group_id=run_group_id,
            arm=execution_arm,
        )
        retrieval = self._resolve(
            by_hash, retrieval_authority_event_hashes,
            {"PreArmFlatScheduleFrozen"} if arm == "flat" else {"RetrieverDecisionV7Recorded"},
            "CURRENT_RETRIEVAL_AUTHORITY_MISSING", failures,
        )
        terminals = self._resolve(
            by_hash, terminal_event_hashes, {"TrialTerminated"},
            "TERMINAL_SOURCE_MISSING", failures,
        )
        evaluations = self._resolve(
            by_hash, evaluation_event_hashes, {"EvaluationRecorded"},
            "EVALUATION_SOURCE_MISSING", failures, required=False,
        )
        decisions = self._resolve(
            by_hash, quality_decision_event_hashes, {"QualityDecisionV4Recorded"},
            "PRODUCTION_QUALITY_DECISION_SOURCE_MISSING", failures, required=False,
        )
        dossiers = self._resolve(
            by_hash, terminal_dossier_event_hashes, {"TrialTerminalDossierRecorded"},
            "TERMINAL_DOSSIER_SOURCE_MISSING", failures, required=False,
        )
        resources = self._resolve(
            by_hash,
            resource_event_hashes,
            {"ActivationResourceMeasuredV2"},
            "PRODUCTION_ARM_RESOURCE_SOURCE_MISSING",
            failures,
        )
        if any(event.run_id != execution_run_id for event in terminals + evaluations + decisions + dossiers):
            failures.add("ARM_EXECUTION_RUN_ID_MISMATCH")
        if arm == "topology" and any(
            event.run_id != execution_run_id
            or event.payload.get("plan_hash") != plan_hash
            or event.payload.get("pair_id") != pair_id
            or event.payload.get("shadow_only") is not True
            for event in retrieval
        ):
            failures.add("TOPOLOGY_RETRIEVER_V7_BINDING_MISMATCH")
        if arm == "flat" and any(
            event.payload.get("plan_hash") != plan_hash
            or event.payload.get("pair_id") != pair_id
            for event in retrieval
        ):
            failures.add("FLAT_PREARM_SCHEDULE_BINDING_MISMATCH")
        if len(resources) != 1:
            failures.add("EVERY_ARM_REQUIRES_ONE_RESOURCE_EVENT")
        for resource in resources:
            if (
                resource.run_id != run_group_id
                or resource.payload.get("plan_hash") != plan_hash
                or resource.payload.get("pair_id") != pair_id
                or resource.payload.get("run_group_id") != run_group_id
                or resource.payload.get("arm") != execution_arm
            ):
                failures.add("PRODUCTION_ARM_RESOURCE_BINDING_MISMATCH")
            if resource.payload.get("source_complete") is not True:
                failures.add("PRODUCTION_ARM_RESOURCE_SOURCE_INCOMPLETE")
            if any(
                order.get(resource.event_hash, -1)
                <= order.get(outcome.event_hash, -1)
                for outcome in terminals + evaluations + decisions + dossiers
            ):
                failures.add("PRODUCTION_ARM_RESOURCE_PRECEDES_ARM_OUTCOME")

        starts = {
            str(event.payload["trial_id"]): event
            for event in events
            if event.event_type == "TrialStarted" and event.run_id == execution_run_id
        }
        terminals.sort(key=lambda event: order.get(event.event_hash, -1))
        counts = Counter({status: 0 for status in TERMINAL_STATUSES})
        candidates: list[str] = []
        terminal_trials: set[str] = set()
        factor_by_trial: dict[str, str] = {}
        evaluation_by_hash = {event.event_hash: event for event in evaluations}
        evaluation_hashes = {event.event_hash for event in evaluations}
        cited_evaluations: set[str] = set()
        for event in terminals:
            trial_id = str(event.payload.get("trial_id", ""))
            status = str(event.payload.get("status", ""))
            start = starts.get(trial_id)
            if start is None or trial_id in terminal_trials or status not in TERMINAL_STATUSES:
                failures.add("TRIAL_TERMINAL_COVERAGE_INVALID")
                continue
            terminal_trials.add(trial_id)
            candidates.append(str(start.payload["candidate_id"]))
            counts[status] += 1
            evaluation_hash = event.payload.get("evaluation_event_hash")
            if evaluation_hash is not None:
                normalized_evaluation_hash = str(evaluation_hash)
                cited_evaluations.add(normalized_evaluation_hash)
                evaluation = evaluation_by_hash.get(normalized_evaluation_hash)
                if evaluation is None:
                    failures.add("EVALUATION_TERMINAL_REFERENCE_MISMATCH")
                elif str(evaluation.payload.get("trial_id", "")) != trial_id:
                    failures.add("EVALUATION_TRIAL_BINDING_MISMATCH")
                else:
                    factor_spec_id = str(
                        evaluation.payload.get("factor_spec_id", "")
                    )
                    if factor_spec_id:
                        factor_by_trial[trial_id] = factor_spec_id
                    else:
                        failures.add("EVALUATION_FACTOR_BINDING_MISSING")
        if cited_evaluations != evaluation_hashes:
            failures.add("EVALUATION_TERMINAL_REFERENCE_MISMATCH")
        if len(candidates) != candidate_budget or len(starts) != candidate_budget:
            failures.add("FIXED_CANDIDATE_BUDGET_INCOMPLETE")
        if set(starts) != terminal_trials:
            failures.add("EVERY_ATTEMPT_REQUIRES_EXACTLY_ONE_TERMINAL")

        effective: list[str] = []
        seen_factors: set[str] = set()
        for event in decisions:
            factor_spec_id = str(event.payload.get("factor_spec_id", ""))
            if factor_spec_id in seen_factors:
                failures.add("DUPLICATE_QUALITY_DECISION_SOURCE")
            seen_factors.add(factor_spec_id)
            if event.payload.get("decision") in _QUALIFIED_TIERS:
                effective.append(factor_spec_id)
        terminal_factors = set(factor_by_trial.values())
        if not set(effective).issubset(terminal_factors):
            failures.add("QUALITY_DECISION_NOT_BOUND_TO_TERMINAL_FACTOR")

        terminal_by_hash = {event.event_hash: event for event in terminals}
        decision_by_hash = {event.event_hash: event for event in decisions}
        dossier_trials: set[str] = set()
        cited_decisions: set[str] = set()
        for event in dossiers:
            trial_id = str(event.payload.get("trial_id", ""))
            dossier_trials.add(trial_id)
            terminal_hash = str(event.payload.get("terminal_event_hash", ""))
            terminal = terminal_by_hash.get(terminal_hash)
            if (
                terminal is None
                or str(terminal.payload.get("trial_id", "")) != trial_id
                or event.payload.get("evaluation_event_hash")
                != terminal.payload.get("evaluation_event_hash")
            ):
                failures.add("TERMINAL_DOSSIER_BINDING_MISMATCH")
            expected_factor = factor_by_trial.get(trial_id)
            dossier_factor = str(event.payload.get("factor_spec_id", ""))
            if expected_factor is not None and dossier_factor != expected_factor:
                failures.add("TERMINAL_DOSSIER_FACTOR_MISMATCH")
            decision_hash = event.payload.get("quality_decision_event_hash")
            if decision_hash is not None:
                normalized_decision_hash = str(decision_hash)
                cited_decisions.add(normalized_decision_hash)
                decision = decision_by_hash.get(normalized_decision_hash)
                if (
                    decision is None
                    or str(decision.payload.get("factor_spec_id", "")) != dossier_factor
                ):
                    failures.add("QUALITY_DECISION_DOSSIER_BINDING_MISMATCH")
        identity_only_trials = {
            trial_id
            for trial_id, terminal in (
                (str(event.payload.get("trial_id", "")), event) for event in terminals
            )
            if terminal.payload.get("evaluation_event_hash") is None
            and (
                terminal.payload.get("status") == "duplicate"
                or any(
                    failure.event_type == "GenerationFailureRecorded"
                    and failure.run_id == execution_run_id
                    and failure.payload.get("trial_id") == trial_id
                    for failure in events
                )
            )
        }
        required_dossier_trials = terminal_trials - identity_only_trials
        if (
            dossier_trials != required_dossier_trials
            or len(dossiers) != len(required_dossier_trials)
        ):
            failures.add("EVERY_EVALUATED_TERMINAL_REQUIRES_ONE_DOSSIER")
        if cited_decisions != set(decision_by_hash):
            failures.add("QUALITY_DECISION_DOSSIER_BINDING_MISMATCH")
        all_requested = (
            *retrieval_authority_event_hashes,
            *terminal_event_hashes,
            *evaluation_event_hashes,
            *quality_decision_event_hashes,
            *terminal_dossier_event_hashes,
            *resource_event_hashes,
        )
        if len(all_requested) != len(set(all_requested)):
            failures.add("DUPLICATE_CROSS_FAMILY_SOURCE_HASH")
        referenced_order = [order[value] for value in all_requested if value in order]
        watermark = events[max(referenced_order)].event_hash if referenced_order else _zero_hash()
        codes = tuple(sorted(failures))
        status_pairs = tuple((status, counts[status]) for status in TERMINAL_STATUSES)
        normalized_retrieval = tuple(sorted(set(retrieval_authority_event_hashes)))
        normalized_terminals = tuple(sorted(set(terminal_event_hashes)))
        normalized_evaluations = tuple(sorted(set(evaluation_event_hashes)))
        normalized_decisions = tuple(sorted(set(quality_decision_event_hashes)))
        normalized_dossiers = tuple(sorted(set(terminal_dossier_event_hashes)))
        normalized_resources = tuple(sorted(set(resource_event_hashes)))
        content = {
            "schema_version": "formal_activation_run_source.v3",
            "plan_hash": plan_hash,
            "pair_id": pair_id,
            "run_group_id": run_group_id,
            "arm": arm,
            "execution_run_id": execution_run_id,
            "source_watermark_event_hash": watermark,
            "retrieval_authority_event_hashes": list(normalized_retrieval),
            "terminal_event_hashes": list(normalized_terminals),
            "evaluation_event_hashes": list(normalized_evaluations),
            "quality_decision_event_hashes": list(normalized_decisions),
            "terminal_dossier_event_hashes": list(normalized_dossiers),
            "resource_event_hashes": list(normalized_resources),
            "derived_terminal_status_counts": [list(item) for item in status_pairs],
            "derived_candidate_ids": candidates,
            "derived_effective_candidate_ids": sorted(effective),
            "source_failure_codes": list(codes),
            "source_complete": not codes,
        }
        return FormalActivationRunSourceV3(
            schema_version="formal_activation_run_source.v3",
            plan_hash=plan_hash,
            pair_id=pair_id,
            run_group_id=run_group_id,
            arm=arm,
            execution_run_id=execution_run_id,
            source_watermark_event_hash=watermark,
            retrieval_authority_event_hashes=normalized_retrieval,
            terminal_event_hashes=normalized_terminals,
            evaluation_event_hashes=normalized_evaluations,
            quality_decision_event_hashes=normalized_decisions,
            terminal_dossier_event_hashes=normalized_dossiers,
            resource_event_hashes=normalized_resources,
            derived_terminal_status_counts=status_pairs,
            derived_candidate_ids=tuple(candidates),
            derived_effective_candidate_ids=tuple(sorted(effective)),
            source_failure_codes=codes,
            source_complete=not codes,
            audit_hash=canonical_json_hash(content),
        )

    @staticmethod
    def _resolve(
        by_hash: dict[str, ResearchEventEnvelope],
        requested: tuple[str, ...],
        allowed_types: set[str],
        missing_code: str,
        failures: set[str],
        *,
        required: bool = True,
    ) -> list[ResearchEventEnvelope]:
        if not requested:
            if required:
                failures.add(missing_code)
            return []
        result: list[ResearchEventEnvelope] = []
        for event_hash in requested:
            event = by_hash.get(event_hash)
            if event is None or event.event_type not in allowed_types:
                failures.add(missing_code)
            else:
                result.append(event)
        return result


def _zero_hash() -> str:
    return "sha256:" + "0" * 64


__all__ = [
    "FormalActivationRunSourceAuditorV3",
    "FormalActivationRunSourceV3",
]
