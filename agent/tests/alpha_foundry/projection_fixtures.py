"""Tests-only projection variants for pure scoring-unit tests.

These objects deliberately carry no runtime authority. Production retrieval
must consume a DiscoveryEvidenceView and will reject them during exact replay.
"""

from __future__ import annotations

from src.alpha_foundry.memory.model import (
    EpisodicProjection,
    ProcessMemoryObservation,
)
from src.research_ledger.hash_utils import canonical_json_hash


def unit_episodic_projection(
    source: EpisodicProjection,
    *,
    observations: tuple[ProcessMemoryObservation, ...],
) -> EpisodicProjection:
    projection = object.__new__(EpisodicProjection)
    object.__setattr__(projection, "schema_version", source.schema_version)
    object.__setattr__(
        projection,
        "source_watermark_event_hash",
        source.source_watermark_event_hash,
    )
    object.__setattr__(
        projection,
        "source_subsequence_hash",
        source.source_subsequence_hash,
    )
    object.__setattr__(
        projection,
        "projector_policy_hash",
        source.projector_policy_hash,
    )
    object.__setattr__(projection, "observations", observations)
    object.__setattr__(projection, "posteriors", source.posteriors)
    object.__setattr__(
        projection,
        "projection_hash",
        canonical_json_hash(
            {
                "tests_only": True,
                "source_projection_hash": source.projection_hash,
                "observation_count": len(observations),
            }
        ),
    )
    object.__setattr__(projection, "_authority", object())
    return projection


__all__ = ["unit_episodic_projection"]
