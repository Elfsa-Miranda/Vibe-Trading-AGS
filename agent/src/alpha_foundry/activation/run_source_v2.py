"""Authoritative-chain audit for source-bound Activation run evidence v2."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Any, Mapping

from src.alpha_foundry.activation.artifacts import ActivationArtifactStore
from src.alpha_foundry.activation.model import ActivationRunManifest, TERMINAL_STATUSES
from src.alpha_foundry.activation.runner import activation_arm_execution_run_id
from src.research_ledger.events import ResearchEventEnvelope, ResearchEventStore
from src.research_ledger.hash_utils import canonical_json_hash


_QUALIFIED_TIERS = {"candidate_zoo", "paper_candidate", "forward_track"}
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class ActivationRunSourceAuditV2:
    schema_version: str
    plan_hash: str
    summary_manifest_hash: str
    source_watermark_event_hash: str
    retriever_decision_event_hashes: tuple[str, ...]
    terminal_event_hashes: tuple[str, ...]
    evaluation_event_hashes: tuple[str, ...]
    quality_decision_event_hashes: tuple[str, ...]
    derived_terminal_status_counts: tuple[tuple[str, int], ...]
    derived_candidate_ids: tuple[str, ...]
    derived_effective_candidate_ids: tuple[str, ...]
    source_failure_codes: tuple[str, ...]
    source_complete: bool
    audit_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "activation_run_source.v2":
            raise ValueError("unsupported Activation run source schema")
        for values in (
            self.retriever_decision_event_hashes,
            self.terminal_event_hashes,
            self.evaluation_event_hashes,
            self.quality_decision_event_hashes,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("Activation source event hashes must be sorted and unique")
            if any(_HASH_RE.fullmatch(value) is None for value in values):
                raise ValueError("Activation source event hashes must be canonical sha256")
        for value in (
            self.plan_hash,
            self.summary_manifest_hash,
            self.source_watermark_event_hash,
            self.audit_hash,
        ):
            if _HASH_RE.fullmatch(value) is None:
                raise ValueError("Activation source identities must be canonical sha256")
        if self.source_failure_codes != tuple(sorted(set(self.source_failure_codes))):
            raise ValueError("Activation source failure codes must be sorted and unique")
        if self.source_complete != (not self.source_failure_codes):
            raise ValueError("Activation source completeness must derive from exact failures")
        if self.audit_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("Activation run source audit hash mismatch")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_hash": self.plan_hash,
            "summary_manifest_hash": self.summary_manifest_hash,
            "source_watermark_event_hash": self.source_watermark_event_hash,
            "retriever_decision_event_hashes": list(
                self.retriever_decision_event_hashes
            ),
            "terminal_event_hashes": list(self.terminal_event_hashes),
            "evaluation_event_hashes": list(self.evaluation_event_hashes),
            "quality_decision_event_hashes": list(
                self.quality_decision_event_hashes
            ),
            "derived_terminal_status_counts": [
                [name, count] for name, count in self.derived_terminal_status_counts
            ],
            "derived_candidate_ids": list(self.derived_candidate_ids),
            "derived_effective_candidate_ids": list(
                self.derived_effective_candidate_ids
            ),
            "source_failure_codes": list(self.source_failure_codes),
            "source_complete": self.source_complete,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "audit_hash": self.audit_hash}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ActivationRunSourceAuditV2":
        expected = {
            "schema_version", "plan_hash", "summary_manifest_hash",
            "source_watermark_event_hash", "retriever_decision_event_hashes",
            "terminal_event_hashes", "evaluation_event_hashes",
            "quality_decision_event_hashes", "derived_terminal_status_counts",
            "derived_candidate_ids", "derived_effective_candidate_ids",
            "source_failure_codes", "source_complete", "audit_hash",
        }
        if set(raw) != expected:
            raise ValueError("Activation run source audit has an invalid closed schema")
        status_items = raw["derived_terminal_status_counts"]
        if not isinstance(status_items, list) or any(
            not isinstance(item, list) or len(item) != 2 for item in status_items
        ):
            raise ValueError("Activation run source terminal counts are invalid")
        list_fields = (
            "retriever_decision_event_hashes", "terminal_event_hashes",
            "evaluation_event_hashes", "quality_decision_event_hashes",
            "derived_candidate_ids", "derived_effective_candidate_ids",
            "source_failure_codes",
        )
        if any(not isinstance(raw[name], list) for name in list_fields):
            raise ValueError("Activation run source audit lists are invalid")
        if not isinstance(raw["source_complete"], bool):
            raise ValueError("Activation run source completeness must be boolean")
        return cls(
            schema_version=str(raw["schema_version"]),
            plan_hash=str(raw["plan_hash"]),
            summary_manifest_hash=str(raw["summary_manifest_hash"]),
            source_watermark_event_hash=str(raw["source_watermark_event_hash"]),
            retriever_decision_event_hashes=tuple(
                str(item) for item in raw["retriever_decision_event_hashes"]
            ),
            terminal_event_hashes=tuple(
                str(item) for item in raw["terminal_event_hashes"]
            ),
            evaluation_event_hashes=tuple(
                str(item) for item in raw["evaluation_event_hashes"]
            ),
            quality_decision_event_hashes=tuple(
                str(item) for item in raw["quality_decision_event_hashes"]
            ),
            derived_terminal_status_counts=tuple(
                (str(item[0]), int(item[1])) for item in status_items
            ),
            derived_candidate_ids=tuple(
                str(item) for item in raw["derived_candidate_ids"]
            ),
            derived_effective_candidate_ids=tuple(
                str(item) for item in raw["derived_effective_candidate_ids"]
            ),
            source_failure_codes=tuple(
                str(item) for item in raw["source_failure_codes"]
            ),
            source_complete=raw["source_complete"],
            audit_hash=str(raw["audit_hash"]),
        )


class ActivationRunSourceAuditorV2:
    """Derive run provenance from a verified ledger; caller summaries are comparands."""

    def __init__(self, store: ResearchEventStore) -> None:
        self.store = store

    def audit(
        self,
        summary: ActivationRunManifest,
        *,
        retriever_decision_event_hashes: tuple[str, ...],
        terminal_event_hashes: tuple[str, ...],
        evaluation_event_hashes: tuple[str, ...],
        quality_decision_event_hashes: tuple[str, ...],
    ) -> ActivationRunSourceAuditV2:
        if not isinstance(summary, ActivationRunManifest):
            raise TypeError("Activation source audit requires a typed v1 summary")
        for values in (
            retriever_decision_event_hashes,
            terminal_event_hashes,
            evaluation_event_hashes,
            quality_decision_event_hashes,
        ):
            if any(_HASH_RE.fullmatch(value) is None for value in values):
                raise ValueError("Activation source references must be canonical sha256")
        requested = tuple(
            tuple(sorted(set(values)))
            for values in (
                retriever_decision_event_hashes,
                terminal_event_hashes,
                evaluation_event_hashes,
                quality_decision_event_hashes,
            )
        )
        failures: set[str] = set()
        if any(len(original) != len(normalized) for original, normalized in zip(
            (
                retriever_decision_event_hashes,
                terminal_event_hashes,
                evaluation_event_hashes,
                quality_decision_event_hashes,
            ),
            requested,
            strict=True,
        )):
            failures.add("DUPLICATE_SOURCE_EVENT_HASH")
        try:
            events = self.store.query_events()
            chain_valid = self.store.verify_chain()
        except (OSError, TypeError, ValueError):
            events = []
            chain_valid = False
        if not chain_valid:
            failures.add("RESEARCH_EVENT_CHAIN_INVALID")
        by_hash = {event.event_hash: event for event in events}
        order = {event.event_hash: index for index, event in enumerate(events)}

        plan = self._plan(summary.plan_hash, events, failures)
        retrievers = self._events(
            requested[0], by_hash, "RetrieverDecisionV3Recorded",
            "RETRIEVER_V3_SOURCE_MISSING", failures,
        )
        self._check_retrievers(summary, retrievers, plan, failures)

        terminals = self._events(
            requested[1], by_hash, "TrialTerminated",
            "TERMINAL_SOURCE_MISSING", failures,
        )
        evaluations = self._events(
            requested[2], by_hash, "EvaluationRecorded",
            "EVALUATION_SOURCE_MISSING", failures,
        )
        decisions = self._events(
            requested[3], by_hash, "QualityDecisionV3Recorded",
            "QUALITY_DECISION_V3_SOURCE_MISSING", failures,
        )
        terminals.sort(key=lambda event: order.get(event.event_hash, -1))
        counts, candidate_ids, candidate_by_trial, trial_to_factor = self._check_trials(
            summary, terminals, evaluations, events, failures
        )
        effective = self._check_quality(
            summary, decisions, trial_to_factor, candidate_by_trial, failures
        )

        if counts != dict(summary.terminal_status_counts):
            failures.add("TERMINAL_STATUS_SUMMARY_MISMATCH")
        if candidate_ids != summary.candidate_ids:
            failures.add("CANDIDATE_ID_SUMMARY_MISMATCH")
        if effective != summary.effective_candidate_ids:
            failures.add("EFFECTIVE_CANDIDATE_SUMMARY_MISMATCH")
        if len(candidate_ids) != summary.candidate_budget:
            failures.add("FIXED_CANDIDATE_BUDGET_INCOMPLETE")

        # These sources do not yet exist in the production event spine. Keep the
        # audit useful but explicitly ineligible instead of trusting summary JSON.
        failures.add("RESOURCE_METRICS_SOURCE_UNBOUND")
        failures.add("QUALITY_DECISION_UPSTREAM_AUTHORITY_UNPROVEN")

        codes = tuple(sorted(failures))
        watermark = (
            events[-1].event_hash
            if events
            else "sha256:" + "0" * 64
        )
        status_pairs = tuple((name, counts.get(name, 0)) for name in TERMINAL_STATUSES)
        content = {
            "schema_version": "activation_run_source.v2",
            "plan_hash": summary.plan_hash,
            "summary_manifest_hash": summary.manifest_hash,
            "source_watermark_event_hash": watermark,
            "retriever_decision_event_hashes": list(requested[0]),
            "terminal_event_hashes": list(requested[1]),
            "evaluation_event_hashes": list(requested[2]),
            "quality_decision_event_hashes": list(requested[3]),
            "derived_terminal_status_counts": [list(item) for item in status_pairs],
            "derived_candidate_ids": list(candidate_ids),
            "derived_effective_candidate_ids": list(effective),
            "source_failure_codes": list(codes),
            "source_complete": not codes,
        }
        return ActivationRunSourceAuditV2(
            schema_version="activation_run_source.v2",
            plan_hash=summary.plan_hash,
            summary_manifest_hash=summary.manifest_hash,
            source_watermark_event_hash=watermark,
            retriever_decision_event_hashes=requested[0],
            terminal_event_hashes=requested[1],
            evaluation_event_hashes=requested[2],
            quality_decision_event_hashes=requested[3],
            derived_terminal_status_counts=status_pairs,
            derived_candidate_ids=candidate_ids,
            derived_effective_candidate_ids=effective,
            source_failure_codes=codes,
            source_complete=not codes,
            audit_hash=canonical_json_hash(content),
        )

    def _plan(
        self,
        plan_hash: str,
        events: list[ResearchEventEnvelope],
        failures: set[str],
    ) -> Mapping[str, Any] | None:
        matches = [
            event for event in events
            if event.event_type == "ActivationPlanRegistered"
            and event.payload["plan_hash"] == plan_hash
        ]
        if len(matches) != 1:
            failures.add("REGISTERED_PLAN_SOURCE_MISSING")
            return None
        try:
            return ActivationArtifactStore(self.store.artifact_root).get("plan", plan_hash)
        except (OSError, TypeError, ValueError):
            failures.add("REGISTERED_PLAN_ARTIFACT_INVALID")
            return None

    @staticmethod
    def _events(
        hashes: tuple[str, ...],
        by_hash: Mapping[str, ResearchEventEnvelope],
        expected_type: str,
        missing_code: str,
        failures: set[str],
    ) -> list[ResearchEventEnvelope]:
        if not hashes:
            failures.add(missing_code)
            return []
        result: list[ResearchEventEnvelope] = []
        for event_hash in hashes:
            event = by_hash.get(event_hash)
            if event is None or event.event_type != expected_type:
                failures.add(missing_code)
            else:
                result.append(event)
        return result

    @staticmethod
    def _check_retrievers(
        summary: ActivationRunManifest,
        events: list[ResearchEventEnvelope],
        plan: Mapping[str, Any] | None,
        failures: set[str],
    ) -> None:
        for event in events:
            payload = event.payload
            if event.run_id != summary.run_group_id:
                failures.add("RETRIEVER_RUN_GROUP_MISMATCH")
            for name in ("seed", "data_snapshot_hash", "candidate_budget"):
                if payload[name] != getattr(summary, name):
                    failures.add("RETRIEVER_FROZEN_INPUT_MISMATCH")
        if summary.arm == "treatment":
            if any(event.payload["policy_hash"] != summary.policy_hash for event in events):
                failures.add("TREATMENT_POLICY_SOURCE_MISMATCH")
        else:
            # Retriever v3 records the topology policy and official output hash,
            # but not the flat control policy hash that produced that output.
            failures.add("CONTROL_POLICY_SOURCE_UNBOUND")
        if plan is not None:
            provenance = plan.get("provenance")
            if not isinstance(provenance, Mapping):
                failures.add("REGISTERED_PLAN_PROVENANCE_INVALID")
            else:
                expected = provenance.get(
                    "control_policy_hash" if summary.arm == "control"
                    else "treatment_policy_hash"
                )
                if expected != summary.policy_hash:
                    failures.add("RUN_POLICY_PLAN_MISMATCH")

    @staticmethod
    def _check_trials(
        summary: ActivationRunManifest,
        terminals: list[ResearchEventEnvelope],
        evaluations: list[ResearchEventEnvelope],
        all_events: list[ResearchEventEnvelope],
        failures: set[str],
    ) -> tuple[
        dict[str, int], tuple[str, ...], dict[str, str], dict[str, str]
    ]:
        execution_run_id = activation_arm_execution_run_id(
            plan_hash=summary.plan_hash,
            run_group_id=summary.run_group_id,
            arm=summary.arm,
        )
        starts = {
            str(event.payload["trial_id"]): event
            for event in all_events
            if event.event_type == "TrialStarted"
            and event.run_id == execution_run_id
        }
        evaluation_by_hash = {event.event_hash: event for event in evaluations}
        cited_evaluations: set[str] = set()
        counts = Counter({name: 0 for name in TERMINAL_STATUSES})
        candidate_ids: list[str] = []
        candidate_by_trial: dict[str, str] = {}
        trial_to_factor: dict[str, str] = {}
        seen_trials: set[str] = set()
        for terminal in terminals:
            trial_id = str(terminal.payload["trial_id"])
            if terminal.run_id != execution_run_id or trial_id in seen_trials:
                failures.add("TERMINAL_RUN_OR_DUPLICATE_TRIAL_MISMATCH")
                continue
            seen_trials.add(trial_id)
            start = starts.get(trial_id)
            if start is None or start.payload["data_scope"] not in {"train", "valid", "train_valid"}:
                failures.add("TRIAL_START_SOURCE_MISSING")
                continue
            candidate_id = str(start.payload["candidate_id"])
            candidate_ids.append(candidate_id)
            candidate_by_trial[trial_id] = candidate_id
            counts[str(terminal.payload["status"])] += 1
            evaluation_hash = terminal.payload["evaluation_event_hash"]
            if evaluation_hash is not None:
                cited_evaluations.add(str(evaluation_hash))
                evaluation = evaluation_by_hash.get(str(evaluation_hash))
                if (
                    evaluation is None
                    or evaluation.run_id != execution_run_id
                    or evaluation.payload["trial_id"] != trial_id
                    or evaluation.payload["data_scope"] not in {"valid", "train_valid"}
                ):
                    failures.add("TERMINAL_EVALUATION_SOURCE_MISMATCH")
                else:
                    trial_to_factor[trial_id] = str(evaluation.payload["factor_spec_id"])
        if cited_evaluations != set(evaluation_by_hash):
            failures.add("EVALUATION_SOURCE_SET_MISMATCH")
        if len(candidate_ids) != len(set(candidate_ids)):
            failures.add("DUPLICATE_CANDIDATE_IDENTITY_IN_RUN")
        return dict(counts), tuple(candidate_ids), candidate_by_trial, trial_to_factor

    @staticmethod
    def _check_quality(
        summary: ActivationRunManifest,
        decisions: list[ResearchEventEnvelope],
        trial_to_factor: Mapping[str, str],
        candidate_by_trial: Mapping[str, str],
        failures: set[str],
    ) -> tuple[str, ...]:
        execution_run_id = activation_arm_execution_run_id(
            plan_hash=summary.plan_hash,
            run_group_id=summary.run_group_id,
            arm=summary.arm,
        )
        by_factor: dict[str, ResearchEventEnvelope] = {}
        for event in decisions:
            factor_id = str(event.payload["factor_spec_id"])
            if event.run_id != execution_run_id or factor_id in by_factor:
                failures.add("QUALITY_DECISION_RUN_OR_FACTOR_MISMATCH")
            by_factor[factor_id] = event
        if set(by_factor) != set(trial_to_factor.values()):
            failures.add("QUALITY_DECISION_SOURCE_SET_MISMATCH")
        factor_by_trial = dict(trial_to_factor)
        qualified_factors = {
            factor_id for factor_id, event in by_factor.items()
            if event.payload["decision"] in _QUALIFIED_TIERS
        }
        effective: list[str] = []
        for trial_id, factor_id in factor_by_trial.items():
            candidate_id = candidate_by_trial.get(trial_id)
            if candidate_id is not None and factor_id in qualified_factors:
                effective.append(candidate_id)
        return tuple(effective)


__all__ = ["ActivationRunSourceAuditV2", "ActivationRunSourceAuditorV2"]
