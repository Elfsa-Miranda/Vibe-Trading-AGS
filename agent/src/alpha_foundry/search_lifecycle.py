"""Typed terminal lifecycle for the production Alpha Foundry search path.

The historical ``AlphaFoundrySearch`` remains byte-compatible when this
capability is not supplied.  When enabled, every generated attempt is bound to
the authoritative research-event spine and terminates exactly once.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Mapping, Protocol, cast

from src.alpha_foundry.candidate_pool import CandidateExpression
from src.alpha_foundry.dsl.identity import FactorIdentityService, FactorSpecSemantics
from src.alpha_quality.flags import ResolvedAGSFlags
from src.research_ledger.events import (
    EventDraft,
    ResearchEventEnvelope,
    ResearchEventStore,
)
from src.research_ledger.hash_utils import canonical_json, canonical_json_hash, utc_now_iso


TerminalStatus = Literal[
    "success",
    "reject",
    "skip",
    "invalid",
    "duplicate",
    "timeout",
    "error",
    "infrastructure_failure",
]
FailureStatus = Literal["invalid", "timeout", "error", "infrastructure_failure"]
ResearchDecision = Literal[
    "reject",
    "research_only",
    "candidate_zoo",
    "none",
]

_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_TERMINAL_STATUSES = frozenset(
    {
        "success", "reject", "skip", "invalid", "duplicate", "timeout",
        "error", "infrastructure_failure",
    }
)
_FAILURE_STATUSES = frozenset({"invalid", "timeout", "error", "infrastructure_failure"})


class CandidateEvaluationTimeout(TimeoutError):
    """A bounded candidate evaluation exceeded its frozen timeout."""


class CandidateEvaluationInfrastructureError(RuntimeError):
    """The evaluator could not run because shared infrastructure failed."""


class CandidateEvaluator(Protocol):
    def evaluate(
        self,
        *,
        candidate: CandidateExpression,
        factor_spec_id: str,
        trial_id: str,
        run_id: str,
    ) -> "SearchEvaluationOutcome": ...


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _assert_finite(value: Any, path: str = "metadata") -> None:
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _assert_finite(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_finite(item, f"{path}[{index}]")
        return
    raise ValueError(f"{path} contains a non-JSON value")


@dataclass(frozen=True)
class SearchEvaluationOutcome:
    """Closed raw evaluator result; terminal truth is still built by the service."""

    status: TerminalStatus
    decision: ResearchDecision
    scorecard_hash: str | None = None
    reason_codes: tuple[str, ...] = ()
    artifact_refs: tuple[Mapping[str, str], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in _TERMINAL_STATUSES:
            raise ValueError("unknown search evaluation terminal status")
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise ValueError("search reason codes must be sorted and unique")
        if any(_CODE_RE.fullmatch(code) is None for code in self.reason_codes):
            raise ValueError("search reason code is invalid")
        if self.status == "success":
            if self.decision not in {"research_only", "candidate_zoo"}:
                raise ValueError("successful train/valid evaluation needs a research decision")
        elif self.status == "reject":
            if self.decision != "reject":
                raise ValueError("rejected evaluation cannot promote")
        elif self.decision not in {"none", "reject", "research_only"}:
            raise ValueError(f"{self.status} evaluation cannot promote")
        if self.status in {"success", "reject"}:
            if self.scorecard_hash is None or _HASH_RE.fullmatch(self.scorecard_hash) is None:
                raise ValueError("evaluated terminal outcome requires a scorecard hash")
        elif self.scorecard_hash is not None:
            raise ValueError("non-evaluated terminal outcome cannot cite a scorecard")
        _assert_finite(self.metadata)
        canonical_json(self.metadata)
        normalized_refs: list[Mapping[str, str]] = []
        for reference in self.artifact_refs:
            if set(reference) != {"relative_path", "artifact_hash", "media_type"}:
                raise ValueError("artifact reference has unknown or missing fields")
            if _HASH_RE.fullmatch(str(reference["artifact_hash"])) is None:
                raise ValueError("artifact reference hash is invalid")
            normalized_refs.append(
                MappingProxyType({str(key): str(item) for key, item in reference.items()})
            )
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        object.__setattr__(self, "artifact_refs", tuple(normalized_refs))
        object.__setattr__(self, "metadata", _freeze(self.metadata))


@dataclass(frozen=True)
class SearchAttemptResult:
    trial_id: str
    candidate_id: str
    factor_spec_id: str | None
    status: TerminalStatus
    decision: ResearchDecision
    reason_codes: tuple[str, ...]
    evaluation_event_hash: str | None
    terminal_event_hash: str
    data_snapshot_hash: str


class EventSourcedSearchLifecycle:
    """Own typed identity/evaluation/terminal transitions for official search."""

    def __init__(
        self,
        *,
        store: ResearchEventStore,
        flags: ResolvedAGSFlags,
        semantics: FactorSpecSemantics,
        evaluator: CandidateEvaluator,
        data_snapshot_hash: str,
    ) -> None:
        required = (
            "VIBE_TRADING_ALPHA_FOUNDRY",
            "VIBE_TRADING_RESEARCH_EVENTS",
            "VIBE_TRADING_FACTOR_DAG",
        )
        if any(not flags.enabled(name) for name in required):
            raise RuntimeError("event-sourced search lifecycle capability is disabled")
        if store.flags.as_dict() != flags.as_dict():
            raise ValueError("search lifecycle and event store flag snapshots differ")
        if _HASH_RE.fullmatch(data_snapshot_hash) is None:
            raise ValueError("search lifecycle data snapshot must be a content hash")
        if not callable(getattr(evaluator, "evaluate", None)):
            raise TypeError("search lifecycle requires a typed candidate evaluator")
        self.store = store
        self.flags = flags
        self.semantics = semantics
        self.evaluator = evaluator
        self.data_snapshot_hash = data_snapshot_hash
        self.identity = FactorIdentityService(store=store, flags=flags)

    def evaluate_candidate(
        self,
        candidate: CandidateExpression,
        *,
        run_id: str,
        attempt_index: int,
    ) -> SearchAttemptResult:
        if not run_id or attempt_index < 1:
            raise ValueError("search attempt requires a run and positive index")
        trial_id = self._trial_id(candidate, run_id=run_id, attempt_index=attempt_index)
        existing = self._existing_attempt(trial_id, candidate.candidate_id, run_id)
        if existing is not None:
            return existing

        try:
            identity_attempt = self.identity.record_attempt(
                trial_id=trial_id,
                run_id=run_id,
                candidate_id=candidate.candidate_id,
                formula=candidate.formula,
                semantics=self.semantics,
            )
        except Exception:
            return self._terminate_failure(
                trial_id=trial_id,
                candidate_id=candidate.candidate_id,
                run_id=run_id,
                status="infrastructure_failure",
                reason_code="IDENTITY_INFRASTRUCTURE_FAILURE",
            )

        if identity_attempt.status in {"invalid", "duplicate"}:
            existing_terminal = self._existing_attempt(
                trial_id, candidate.candidate_id, run_id
            )
            if existing_terminal is None:
                raise RuntimeError("identity terminal transition was not persisted")
            return existing_terminal
        if identity_attempt.factor_spec_id is None:
            return self._terminate_failure(
                trial_id=trial_id,
                candidate_id=candidate.candidate_id,
                run_id=run_id,
                status="infrastructure_failure",
                reason_code="FACTOR_IDENTITY_MISSING",
            )

        try:
            outcome = self.evaluator.evaluate(
                candidate=candidate,
                factor_spec_id=identity_attempt.factor_spec_id,
                trial_id=trial_id,
                run_id=run_id,
            )
            if not isinstance(outcome, SearchEvaluationOutcome):
                raise TypeError("candidate evaluator returned an untyped outcome")
        except CandidateEvaluationTimeout:
            return self._terminate_failure(
                trial_id=trial_id,
                candidate_id=candidate.candidate_id,
                run_id=run_id,
                status="timeout",
                reason_code="EVALUATION_TIMEOUT",
            )
        except CandidateEvaluationInfrastructureError:
            return self._terminate_failure(
                trial_id=trial_id,
                candidate_id=candidate.candidate_id,
                run_id=run_id,
                status="infrastructure_failure",
                reason_code="EVALUATION_INFRASTRUCTURE_FAILURE",
            )
        except Exception:
            return self._terminate_failure(
                trial_id=trial_id,
                candidate_id=candidate.candidate_id,
                run_id=run_id,
                status="error",
                reason_code="EVALUATION_ERROR",
            )

        try:
            return self._persist_outcome(
                candidate=candidate,
                factor_spec_id=identity_attempt.factor_spec_id,
                trial_id=trial_id,
                run_id=run_id,
                outcome=outcome,
            )
        except Exception:
            return self._terminate_failure(
                trial_id=trial_id,
                candidate_id=candidate.candidate_id,
                run_id=run_id,
                status="infrastructure_failure",
                reason_code="EVALUATION_PERSISTENCE_FAILURE",
            )

    def _persist_outcome(
        self,
        *,
        candidate: CandidateExpression,
        factor_spec_id: str,
        trial_id: str,
        run_id: str,
        outcome: SearchEvaluationOutcome,
    ) -> SearchAttemptResult:
        if outcome.status in _FAILURE_STATUSES:
            return self._terminate_failure(
                trial_id=trial_id,
                candidate_id=candidate.candidate_id,
                run_id=run_id,
                status=cast(FailureStatus, outcome.status),
                reason_code=(outcome.reason_codes[0] if outcome.reason_codes else outcome.status.upper()),
                decision=outcome.decision,
            )

        evaluation: ResearchEventEnvelope | None = None
        if outcome.status in {"success", "reject"}:
            evaluation_id = "evaluation-" + trial_id.removeprefix("trial-")
            metadata = {
                **dict(outcome.metadata),
                "data_snapshot_hash": self.data_snapshot_hash,
                "search_lifecycle_schema_version": "event_sourced_search_lifecycle.v1",
            }
            evaluation = self.store.append_event(
                EventDraft(
                    event_type="EvaluationRecorded",
                    entity_id=evaluation_id,
                    run_id=run_id,
                    payload_schema_version="evaluation_recorded.v1",
                    idempotency_key=f"search-evaluation:{trial_id}",
                    payload={
                        "evaluation_id": evaluation_id,
                        "trial_id": trial_id,
                        "factor_spec_id": factor_spec_id,
                        "data_scope": "train_valid",
                        "scorecard_hash": outcome.scorecard_hash,
                        "artifact_refs": [dict(item) for item in outcome.artifact_refs],
                        "metadata": metadata,
                    },
                )
            )

        terminal = self.store.append_event(
            EventDraft(
                event_type="TrialTerminated",
                entity_id=trial_id,
                run_id=run_id,
                payload_schema_version="trial_terminated.v1",
                idempotency_key=f"trial-terminal:{trial_id}",
                payload={
                    "trial_id": trial_id,
                    "status": outcome.status,
                    "reason_codes": list(outcome.reason_codes),
                    "decision": outcome.decision,
                    "evaluation_event_hash": (
                        None if evaluation is None else evaluation.event_hash
                    ),
                    "terminated_at": utc_now_iso(),
                },
            )
        )
        return SearchAttemptResult(
            trial_id=trial_id,
            candidate_id=candidate.candidate_id,
            factor_spec_id=factor_spec_id if evaluation is not None else None,
            status=outcome.status,
            decision=outcome.decision,
            reason_codes=outcome.reason_codes,
            evaluation_event_hash=None if evaluation is None else evaluation.event_hash,
            terminal_event_hash=terminal.event_hash,
            data_snapshot_hash=self.data_snapshot_hash,
        )

    def _terminate_failure(
        self,
        *,
        trial_id: str,
        candidate_id: str,
        run_id: str,
        status: FailureStatus,
        reason_code: str,
        decision: ResearchDecision = "research_only",
    ) -> SearchAttemptResult:
        existing = self._existing_attempt(trial_id, candidate_id, run_id)
        if existing is not None:
            return existing
        self._ensure_started(trial_id, candidate_id, run_id)
        failure_kind = status
        self.store.append_event(
            EventDraft(
                event_type="GenerationFailureRecorded",
                entity_id=trial_id,
                run_id=run_id,
                payload_schema_version="generation_failure_recorded.v1",
                idempotency_key=f"search-generation-failure:{trial_id}",
                payload={
                    "trial_id": trial_id,
                    "failure_code": reason_code,
                    "failure_kind": failure_kind,
                    "message": "candidate attempt failed before a valid terminal evaluation",
                    "occurred_at": utc_now_iso(),
                },
            )
        )
        terminal = self.store.append_event(
            EventDraft(
                event_type="TrialTerminated",
                entity_id=trial_id,
                run_id=run_id,
                payload_schema_version="trial_terminated.v1",
                idempotency_key=f"trial-terminal:{trial_id}",
                payload={
                    "trial_id": trial_id,
                    "status": status,
                    "reason_codes": [reason_code],
                    "decision": decision,
                    "evaluation_event_hash": None,
                    "terminated_at": utc_now_iso(),
                },
            )
        )
        return SearchAttemptResult(
            trial_id=trial_id,
            candidate_id=candidate_id,
            factor_spec_id=None,
            status=status,
            decision=decision,
            reason_codes=(reason_code,),
            evaluation_event_hash=None,
            terminal_event_hash=terminal.event_hash,
            data_snapshot_hash=self.data_snapshot_hash,
        )

    def _ensure_started(self, trial_id: str, candidate_id: str, run_id: str) -> None:
        starts = self.store.query_events(event_type="TrialStarted", entity_id=trial_id)
        if starts:
            if starts[0].run_id != run_id or starts[0].payload["candidate_id"] != candidate_id:
                raise ValueError("existing search trial does not match candidate or run")
            return
        self.store.append_event(
            EventDraft(
                event_type="TrialStarted",
                entity_id=trial_id,
                run_id=run_id,
                payload_schema_version="trial_started.v1",
                idempotency_key=f"trial-start:{trial_id}",
                payload={
                    "trial_id": trial_id,
                    "candidate_id": candidate_id,
                    "data_scope": "train_valid",
                    "objective": "alpha_foundry_terminal_evaluation",
                    "started_at": utc_now_iso(),
                },
            )
        )

    def _existing_attempt(
        self,
        trial_id: str,
        candidate_id: str,
        run_id: str,
    ) -> SearchAttemptResult | None:
        starts = self.store.query_events(event_type="TrialStarted", entity_id=trial_id)
        if not starts:
            return None
        start = starts[0]
        if start.run_id != run_id or str(start.payload["candidate_id"]) != candidate_id:
            raise ValueError("trial identity is already bound to another search attempt")
        terminals = self.store.query_events(event_type="TrialTerminated", entity_id=trial_id)
        if not terminals:
            return None
        terminal = terminals[0]
        evaluation_hash = terminal.payload["evaluation_event_hash"]
        factor_spec_id: str | None = None
        if evaluation_hash is not None:
            evaluations = [
                event
                for event in self.store.query_events(event_type="EvaluationRecorded")
                if event.event_hash == evaluation_hash
            ]
            if len(evaluations) != 1:
                raise RuntimeError("terminal search attempt has no unique evaluation")
            factor_spec_id = str(evaluations[0].payload["factor_spec_id"])
        return SearchAttemptResult(
            trial_id=trial_id,
            candidate_id=candidate_id,
            factor_spec_id=factor_spec_id,
            status=str(terminal.payload["status"]),  # type: ignore[arg-type]
            decision=str(terminal.payload["decision"]),  # type: ignore[arg-type]
            reason_codes=tuple(str(code) for code in terminal.payload["reason_codes"]),
            evaluation_event_hash=(None if evaluation_hash is None else str(evaluation_hash)),
            terminal_event_hash=terminal.event_hash,
            data_snapshot_hash=self.data_snapshot_hash,
        )

    def _trial_id(
        self,
        candidate: CandidateExpression,
        *,
        run_id: str,
        attempt_index: int,
    ) -> str:
        digest = canonical_json_hash(
            {
                "schema_version": "event_sourced_search_trial_id.v1",
                "run_id": run_id,
                "attempt_index": attempt_index,
                "candidate_id": candidate.candidate_id,
                "formula_hash": candidate.formula_hash,
                "data_snapshot_hash": self.data_snapshot_hash,
            }
        )
        return "trial-search-" + digest.removeprefix("sha256:")[:24]


__all__ = [
    "CandidateEvaluationInfrastructureError",
    "CandidateEvaluationTimeout",
    "CandidateEvaluator",
    "EventSourcedSearchLifecycle",
    "SearchAttemptResult",
    "SearchEvaluationOutcome",
]
