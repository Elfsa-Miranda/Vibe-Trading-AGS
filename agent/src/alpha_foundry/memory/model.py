"""Immutable derived models for factual and episodic process memory."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


_EPISODIC_PROJECTION_AUTHORITY = object()


@dataclass(frozen=True)
class ProcessMemoryObservation:
    parent_context_hash: str
    parent_factor_spec_id: str
    child_factor_spec_id: str
    derivation_event_hash: str
    evaluation_event_hash: str
    scorecard_hash: str
    ast_diff_hash: str
    motif_version: str
    motif: str
    base_expected_utility: float
    observed_validation_utility: float | None
    residual: float | None
    terminal_status: str
    failure_codes: tuple[str, ...]
    regime_config_hash: str | None
    data_snapshot_hash: str
    eligible_event_watermark: str
    run_group_id: str
    policy_hash: str
    available_at: str


@dataclass(frozen=True)
class ProcessPosterior:
    parent_context_hash: str
    motif: str
    effective_count: int
    observation_count: int
    mean_residual: float
    residual_standard_error: float | None
    confidence: float
    positive_adjustment: float
    hard_veto: bool


@dataclass(frozen=True, init=False)
class EpisodicProjection:
    schema_version: Literal["episodic_process_projection.v2"]
    source_watermark_event_hash: str | None
    source_subsequence_hash: str
    projector_policy_hash: str
    observations: tuple[ProcessMemoryObservation, ...]
    posteriors: tuple[ProcessPosterior, ...]
    projection_hash: str
    _authority: object = field(init=False, repr=False, compare=False)

    def __init__(
        self,
        *,
        schema_version: Literal["episodic_process_projection.v2"],
        source_watermark_event_hash: str | None,
        source_subsequence_hash: str,
        projector_policy_hash: str,
        observations: tuple[ProcessMemoryObservation, ...],
        posteriors: tuple[ProcessPosterior, ...],
        projection_hash: str,
        _authority: object,
    ) -> None:
        if _authority is not _EPISODIC_PROJECTION_AUTHORITY:
            raise TypeError("episodic projection must be built by EpisodicProjector")
        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "source_watermark_event_hash", source_watermark_event_hash)
        object.__setattr__(self, "source_subsequence_hash", source_subsequence_hash)
        object.__setattr__(self, "projector_policy_hash", projector_policy_hash)
        object.__setattr__(self, "observations", tuple(observations))
        object.__setattr__(self, "posteriors", tuple(posteriors))
        object.__setattr__(self, "projection_hash", projection_hash)
        object.__setattr__(self, "_authority", _authority)


def _build_episodic_projection(**values: object) -> EpisodicProjection:
    return EpisodicProjection(**values, _authority=_EPISODIC_PROJECTION_AUTHORITY)  # type: ignore[arg-type]


def is_authorized_episodic_projection(value: EpisodicProjection) -> bool:
    return value._authority is _EPISODIC_PROJECTION_AUTHORITY


__all__ = [
    "EpisodicProjection", "ProcessMemoryObservation", "ProcessPosterior",
    "is_authorized_episodic_projection",
]
