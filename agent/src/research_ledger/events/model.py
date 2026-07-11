"""Versioned immutable models for the AGS typed research-event spine."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping

from src.research_ledger.hash_utils import canonical_json_hash


class ResearchEventError(RuntimeError):
    """Base class for typed research-event failures."""


class EventValidationError(ResearchEventError):
    """Raised before append when an event or payload violates its schema."""


class EventIdempotencyConflict(ResearchEventError):
    """Raised when an idempotency key is reused for another business event."""


class EventTransitionError(ResearchEventError):
    """Raised when a trial lifecycle transition is not in the closed table."""


class EventMutationError(ResearchEventError):
    """Raised for public mutation/delete attempts."""


class ResearchEventAppendError(ResearchEventError):
    """Raised when a validated event cannot be committed atomically."""


class ArtifactReferenceError(EventValidationError):
    """Raised for unsafe, missing, escaped, or hash-mismatched artifacts."""


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class EventDraft:
    event_type: str
    entity_id: str
    run_id: str
    payload_schema_version: str
    payload: Mapping[str, Any]
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _freeze(self.payload))


@dataclass(frozen=True)
class ResearchEventEnvelope:
    schema_version: Literal["research_event.v1"]
    event_id: str
    event_type: str
    entity_id: str
    run_id: str
    payload_schema_version: str
    payload: Mapping[str, Any]
    payload_hash: str
    idempotency_key: str | None
    previous_event_hash: str | None
    event_hash: str
    created_at: str
    code_version: str
    feature_flags: Mapping[str, bool]
    warnings: tuple[str, ...]
    hard_failures: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _freeze(self.payload))
        object.__setattr__(self, "feature_flags", _freeze(self.feature_flags))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "hard_failures", tuple(self.hard_failures))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "entity_id": self.entity_id,
            "run_id": self.run_id,
            "payload_schema_version": self.payload_schema_version,
            "payload": _thaw(self.payload),
            "payload_hash": self.payload_hash,
            "idempotency_key": self.idempotency_key,
            "previous_event_hash": self.previous_event_hash,
            "event_hash": self.event_hash,
            "created_at": self.created_at,
            "code_version": self.code_version,
            "feature_flags": _thaw(self.feature_flags),
            "warnings": list(self.warnings),
            "hard_failures": list(self.hard_failures),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResearchEventEnvelope":
        return cls(
            schema_version=payload["schema_version"],
            event_id=str(payload["event_id"]),
            event_type=str(payload["event_type"]),
            entity_id=str(payload["entity_id"]),
            run_id=str(payload["run_id"]),
            payload_schema_version=str(payload["payload_schema_version"]),
            payload=dict(payload["payload"]),
            payload_hash=str(payload["payload_hash"]),
            idempotency_key=(
                None if payload.get("idempotency_key") is None else str(payload["idempotency_key"])
            ),
            previous_event_hash=(
                None
                if payload.get("previous_event_hash") is None
                else str(payload["previous_event_hash"])
            ),
            event_hash=str(payload["event_hash"]),
            created_at=str(payload["created_at"]),
            code_version=str(payload["code_version"]),
            feature_flags=dict(payload["feature_flags"]),
            warnings=tuple(str(item) for item in payload["warnings"]),
            hard_failures=tuple(str(item) for item in payload["hard_failures"]),
        )


@dataclass(frozen=True)
class LifecycleSummary:
    started_count: int
    terminal_count: int
    open_trial_ids: tuple[str, ...]
    terminal_status_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class ReplayState:
    schema_version: Literal["research_event_replay.v1"]
    event_count: int
    watermark_sequence: int
    watermark_event_hash: str | None
    event_type_counts: tuple[tuple[str, int], ...]
    open_trial_ids: tuple[str, ...]
    terminal_status_counts: tuple[tuple[str, int], ...]
    projection_hash: str


_VERIFIED_SUBSEQUENCE_AUTHORITY = object()


@dataclass(frozen=True, init=False)
class VerifiedEventSubsequence:
    """An ordered event subset proven against one fully verified global chain."""

    events: tuple[ResearchEventEnvelope, ...]
    full_event_count: int
    full_chain_head: str
    full_replay_hash: str
    subsequence_hash: str
    _authority: object

    def __init__(
        self,
        *,
        events: tuple[ResearchEventEnvelope, ...],
        full_event_count: int,
        full_chain_head: str,
        full_replay_hash: str,
        _authority: object,
    ) -> None:
        if _authority is not _VERIFIED_SUBSEQUENCE_AUTHORITY:
            raise TypeError("verified event subsequences must be issued by the event store")
        if full_event_count < len(events) or not full_chain_head or not full_replay_hash:
            raise ValueError("verified event subsequence has invalid full-chain provenance")
        content = {
            "schema_version": "verified_event_subsequence.v1",
            "event_hashes": [event.event_hash for event in events],
            "full_event_count": full_event_count,
            "full_chain_head": full_chain_head,
            "full_replay_hash": full_replay_hash,
        }
        object.__setattr__(self, "events", tuple(events))
        object.__setattr__(self, "full_event_count", full_event_count)
        object.__setattr__(self, "full_chain_head", full_chain_head)
        object.__setattr__(self, "full_replay_hash", full_replay_hash)
        object.__setattr__(self, "subsequence_hash", canonical_json_hash(content))
        object.__setattr__(self, "_authority", _authority)

    def is_authorized(self) -> bool:
        return self._authority is _VERIFIED_SUBSEQUENCE_AUTHORITY

    def __iter__(self):
        return iter(self.events)

    def __len__(self) -> int:
        return len(self.events)


def _issue_verified_event_subsequence(
    *,
    events: tuple[ResearchEventEnvelope, ...],
    full_event_count: int,
    full_chain_head: str,
    full_replay_hash: str,
) -> VerifiedEventSubsequence:
    return VerifiedEventSubsequence(
        events=events,
        full_event_count=full_event_count,
        full_chain_head=full_chain_head,
        full_replay_hash=full_replay_hash,
        _authority=_VERIFIED_SUBSEQUENCE_AUTHORITY,
    )


__all__ = [
    "ArtifactReferenceError",
    "EventDraft",
    "EventIdempotencyConflict",
    "EventMutationError",
    "EventTransitionError",
    "EventValidationError",
    "LifecycleSummary",
    "ReplayState",
    "ResearchEventAppendError",
    "ResearchEventEnvelope",
    "ResearchEventError",
    "VerifiedEventSubsequence",
]
