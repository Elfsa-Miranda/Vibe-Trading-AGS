"""PIT-aware factor output and dual-channel predictive evidence producers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from src.alpha_foundry.dsl.executable import ExecutableGrammarSnapshotV1
from src.alpha_foundry.dsl.identity import validate_factor_definition_payload
from src.alpha_quality.evaluation_contract.contract import (
    CONTRACT_MEDIA_TYPE,
    ResolvedEvaluationContractArtifactStoreV1,
)
from src.alpha_quality.evaluation_registry_v1 import EvaluationPolicyArtifactStoreV1
from src.alpha_quality.ic_metrics import compute_ic_metrics
from src.alpha_quality.pit_artifact_v2 import (
    PIT_MANIFEST_MEDIA_TYPE,
    AsharePITParquetStoreV1,
    AsharePITTableReferenceV1,
    FrozenAsharePITSnapshotArtifactStoreV2,
)
from src.alpha_quality.pit_service_v2 import AsharePITSnapshotServiceV2
from src.research_ledger.events.artifacts import (
    AtomicContentAddressedArtifactWriter,
    ContentAddressedArtifact,
    validate_artifact_references,
)
from src.research_ledger.events.model import (
    EventDraft,
    EventTransitionError,
    EventValidationError,
    ResearchEventEnvelope,
)
from src.research_ledger.hash_utils import canonical_json_hash


FACTOR_OUTPUT_EVENT_TYPE = "FactorOutputRecordedV3"
OBSERVED_PREDICTIVE_EVENT_TYPE = "ObservedPanelPredictiveEvidenceRecorded"
PIT_PREDICTIVE_EVENT_TYPE = "PITPredictiveEvidenceRecorded"
SCORECARD_V4_EVENT_TYPE = "ScorecardDecisionEvidenceV4Recorded"

FACTOR_OUTPUT_MEDIA_TYPE = "application/vnd.vibe.factor-output-v3+json"
PREDICTIVE_EVIDENCE_MEDIA_TYPE = "application/vnd.vibe.predictive-evidence-v1+json"
SCORECARD_V4_MEDIA_TYPE = "application/vnd.vibe.scorecard-evidence-v4+json"

PREDICTIVE_PRODUCER_SCHEMA = "pit_predictive_evidence_service.v4"
PREDICTIVE_PRODUCER_POLICY_HASH = canonical_json_hash(
    {
        "schema_version": "pit_predictive_producer_policy.v4",
        "factor_storage": "content_addressed_parquet.v3",
        "observed_scope": "frozen_supplied_panel_descriptive_only",
        "pit_scope": "daily_membership_availability_and_observability_required",
        "axes": "exact_no_intersection_repair",
        "test_forward_access": "structurally_unavailable",
        "minimum_cross_section": 2,
        "dependence": "registered_horizon_standard_or_newey_west",
    }
)

_FACTOR_KEYS = frozenset(
    {
        "schema_version",
        "factor_spec_id",
        "canonical_formula",
        "run_id",
        "resolved_contract_hash",
        "contract_event_hash",
        "pit_snapshot_hash",
        "pit_snapshot_event_hash",
        "evaluation_policy_event_hash",
        "split_plan_hash",
        "evaluation_time_policy_hash",
        "executable_grammar_snapshot_hash",
        "backend_version",
        "dates",
        "symbols",
        "shape",
        "factor_table_ref",
        "source_event_hashes",
        "pit_mask_status",
        "producer_schema_version",
        "producer_policy_hash",
        "factor_output_hash",
    }
)
_PREDICTIVE_KEYS = frozenset(
    {
        "schema_version",
        "evidence_type",
        "factor_spec_id",
        "run_id",
        "factor_output_hash",
        "factor_output_event_hash",
        "resolved_contract_hash",
        "contract_event_hash",
        "pit_snapshot_hash",
        "pit_snapshot_event_hash",
        "availability",
        "claim_scope",
        "evidence_grade",
        "split_metrics",
        "universe_bias_assessment",
        "pit_authority",
        "promotion_effect",
        "caps",
        "warnings",
        "source_event_hashes",
        "producer_schema_version",
        "producer_policy_hash",
        "evidence_hash",
    }
)
_SCORECARD_KEYS = frozenset(
    {
        "schema_version",
        "factor_spec_id",
        "run_id",
        "resolved_contract_hash",
        "factor_output_hash",
        "observed_evidence_hash",
        "pit_evidence_hash",
        "factor_output_event_hash",
        "observed_event_hash",
        "pit_event_hash",
        "pit_predictive_authority",
        "candidate_promotion_effect",
        "caps",
        "warnings",
        "source_event_hashes",
        "producer_schema_version",
        "producer_policy_hash",
        "scorecard_evidence_hash",
    }
)


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


def _strict_json_read(path: Path) -> Mapping[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite predictive artifact JSON: {value}")

    def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate predictive artifact key")
            result[key] = value
        return result

    raw = json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicates,
    )
    if not isinstance(raw, Mapping):
        raise ValueError("predictive artifact must be an object")
    return raw


def _event_by_hash(
    events: list[ResearchEventEnvelope], event_hash: str, event_type: str
) -> ResearchEventEnvelope:
    matches = [
        event
        for event in events
        if event.event_hash == event_hash and event.event_type == event_type
    ]
    if len(matches) != 1:
        raise EventValidationError(f"predictive producer requires one {event_type}")
    return matches[0]


def _artifact_ref(event: ResearchEventEnvelope, media_type: str) -> Mapping[str, Any]:
    refs = [
        ref for ref in event.payload["artifact_refs"] if ref["media_type"] == media_type
    ]
    if len(refs) != 1:
        raise EventValidationError("predictive source artifact is ambiguous")
    return refs[0]


def _exact_axes(
    frame: pd.DataFrame, dates: tuple[str, ...], symbols: tuple[str, ...], name: str
) -> None:
    if (
        not isinstance(frame.index, pd.DatetimeIndex)
        or frame.index.tz is not None
        or tuple(frame.index.date.astype(str)) != dates
        or tuple(frame.columns) != symbols
    ):
        raise EventValidationError(
            f"{name} axes differ; intersection repair is forbidden"
        )


@dataclass(frozen=True)
class FactorOutputArtifactV3:
    schema_version: Literal["factor_output_artifact.v3"]
    factor_spec_id: str
    canonical_formula: str
    run_id: str
    resolved_contract_hash: str
    contract_event_hash: str
    pit_snapshot_hash: str
    pit_snapshot_event_hash: str
    evaluation_policy_event_hash: str
    split_plan_hash: str
    evaluation_time_policy_hash: str
    executable_grammar_snapshot_hash: str
    backend_version: str
    dates: tuple[str, ...]
    symbols: tuple[str, ...]
    shape: tuple[int, int]
    factor_table_ref: Mapping[str, Any]
    source_event_hashes: tuple[str, ...]
    pit_mask_status: Literal["complete", "partial", "unavailable", "contaminated"]
    producer_schema_version: str
    producer_policy_hash: str
    factor_output_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "factor_output_artifact.v3":
            raise ValueError("unsupported factor output artifact")
        if self.producer_schema_version != PREDICTIVE_PRODUCER_SCHEMA:
            raise ValueError("unknown factor output producer")
        if self.producer_policy_hash != PREDICTIVE_PRODUCER_POLICY_HASH:
            raise ValueError("factor output producer policy differs")
        if self.dates != tuple(sorted(set(self.dates))) or not self.dates:
            raise ValueError("factor output dates are not canonical")
        if self.symbols != tuple(sorted(set(self.symbols))) or not self.symbols:
            raise ValueError("factor output symbols are not canonical")
        if self.shape != (len(self.dates), len(self.symbols)):
            raise ValueError("factor output shape differs from axes")
        if self.source_event_hashes != tuple(sorted(set(self.source_event_hashes))):
            raise ValueError("factor output source hashes are not canonical")
        reference = AsharePITTableReferenceV1.from_dict(self.factor_table_ref)
        if (
            reference.table_name != "factor-output:" + self.factor_spec_id
            or reference.table_role != "numeric_frame"
            or reference.row_count != len(self.dates)
            or reference.column_names != self.symbols
            or reference.index_name != "date"
        ):
            raise ValueError("factor output Parquet reference is invalid")
        object.__setattr__(self, "factor_table_ref", _freeze(reference.to_dict()))
        if self.factor_output_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("factor output artifact hash differs")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "factor_spec_id": self.factor_spec_id,
            "canonical_formula": self.canonical_formula,
            "run_id": self.run_id,
            "resolved_contract_hash": self.resolved_contract_hash,
            "contract_event_hash": self.contract_event_hash,
            "pit_snapshot_hash": self.pit_snapshot_hash,
            "pit_snapshot_event_hash": self.pit_snapshot_event_hash,
            "evaluation_policy_event_hash": self.evaluation_policy_event_hash,
            "split_plan_hash": self.split_plan_hash,
            "evaluation_time_policy_hash": self.evaluation_time_policy_hash,
            "executable_grammar_snapshot_hash": self.executable_grammar_snapshot_hash,
            "backend_version": self.backend_version,
            "dates": list(self.dates),
            "symbols": list(self.symbols),
            "shape": list(self.shape),
            "factor_table_ref": _plain(self.factor_table_ref),
            "source_event_hashes": list(self.source_event_hashes),
            "pit_mask_status": self.pit_mask_status,
            "producer_schema_version": self.producer_schema_version,
            "producer_policy_hash": self.producer_policy_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "factor_output_hash": self.factor_output_hash}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "FactorOutputArtifactV3":
        if set(raw) != _FACTOR_KEYS or not isinstance(raw["factor_table_ref"], Mapping):
            raise ValueError("factor output artifact schema is not closed")
        return cls(
            schema_version=str(raw["schema_version"]),  # type: ignore[arg-type]
            factor_spec_id=str(raw["factor_spec_id"]),
            canonical_formula=str(raw["canonical_formula"]),
            run_id=str(raw["run_id"]),
            resolved_contract_hash=str(raw["resolved_contract_hash"]),
            contract_event_hash=str(raw["contract_event_hash"]),
            pit_snapshot_hash=str(raw["pit_snapshot_hash"]),
            pit_snapshot_event_hash=str(raw["pit_snapshot_event_hash"]),
            evaluation_policy_event_hash=str(raw["evaluation_policy_event_hash"]),
            split_plan_hash=str(raw["split_plan_hash"]),
            evaluation_time_policy_hash=str(raw["evaluation_time_policy_hash"]),
            executable_grammar_snapshot_hash=str(
                raw["executable_grammar_snapshot_hash"]
            ),
            backend_version=str(raw["backend_version"]),
            dates=tuple(str(item) for item in raw["dates"]),
            symbols=tuple(str(item) for item in raw["symbols"]),
            shape=tuple(int(item) for item in raw["shape"]),  # type: ignore[arg-type]
            factor_table_ref=dict(raw["factor_table_ref"]),
            source_event_hashes=tuple(str(item) for item in raw["source_event_hashes"]),
            pit_mask_status=str(raw["pit_mask_status"]),  # type: ignore[arg-type]
            producer_schema_version=str(raw["producer_schema_version"]),
            producer_policy_hash=str(raw["producer_policy_hash"]),
            factor_output_hash=str(raw["factor_output_hash"]),
        )


class FactorOutputArtifactStoreV3:
    namespace = "factor-output-v3"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve(strict=True)
        self.writer = AtomicContentAddressedArtifactWriter(
            self.root, max_bytes=4 * 1024**2
        )
        self.tables = AsharePITParquetStoreV1(self.root)

    def write_factor_table(
        self, factor_spec_id: str, frame: pd.DataFrame
    ) -> AsharePITTableReferenceV1:
        return self.tables.write(
            "factor-output:" + factor_spec_id,
            "numeric_frame",
            frame,
            index_name="date",
        )

    def write_manifest(
        self, artifact: FactorOutputArtifactV3
    ) -> ContentAddressedArtifact:
        return self.writer.write_json(
            namespace=self.namespace,
            payload=artifact.to_dict(),
            schema_version="factor_output_artifact.v3",
            semantic_hash_field="factor_output_hash",
            closed_keys=_FACTOR_KEYS,
            media_type=FACTOR_OUTPUT_MEDIA_TYPE,
        )

    def read_manifest(
        self, relative_path: str, *, expected_hash: str, expected_blob_hash: str
    ) -> FactorOutputArtifactV3:
        normalized = validate_artifact_references(
            self.root,
            [
                {
                    "relative_path": relative_path,
                    "artifact_hash": expected_blob_hash,
                    "media_type": FACTOR_OUTPUT_MEDIA_TYPE,
                }
            ],
        )[0]
        digest = expected_hash.removeprefix("sha256:")
        expected = f"{self.namespace}/{digest[:2]}/{digest}.json"
        if normalized["relative_path"] != expected:
            raise ValueError("factor output manifest path is not content addressed")
        artifact = FactorOutputArtifactV3.from_dict(
            _strict_json_read(self.root.joinpath(*expected.split("/")))
        )
        if artifact.factor_output_hash != expected_hash:
            raise ValueError("factor output manifest identity differs")
        self.read_factor_frame(artifact)
        return artifact

    def read_factor_frame(self, artifact: FactorOutputArtifactV3) -> pd.DataFrame:
        frame = self.tables.read(
            AsharePITTableReferenceV1.from_dict(artifact.factor_table_ref)
        )
        frame.index = pd.DatetimeIndex(frame.index)
        _exact_axes(frame, artifact.dates, artifact.symbols, "factor output Parquet")
        return frame.astype(float)


@dataclass(frozen=True)
class PredictiveEvidenceV1:
    schema_version: Literal["predictive_evidence.v1"]
    evidence_type: Literal["observed_panel", "pit_scoped"]
    factor_spec_id: str
    run_id: str
    factor_output_hash: str
    factor_output_event_hash: str
    resolved_contract_hash: str
    contract_event_hash: str
    pit_snapshot_hash: str
    pit_snapshot_event_hash: str
    availability: Literal["available", "partial", "unavailable", "invalid"]
    claim_scope: str
    evidence_grade: Literal["descriptive", "exploratory", "confirmatory"]
    split_metrics: Mapping[str, Any]
    universe_bias_assessment: Mapping[str, Any]
    pit_authority: Mapping[str, Any]
    promotion_effect: Literal["none", "blocked_pending_execution"]
    caps: tuple[str, ...]
    warnings: tuple[str, ...]
    source_event_hashes: tuple[str, ...]
    producer_schema_version: str
    producer_policy_hash: str
    evidence_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "predictive_evidence.v1":
            raise ValueError("unsupported predictive evidence")
        if self.producer_schema_version != PREDICTIVE_PRODUCER_SCHEMA:
            raise ValueError("unknown predictive producer")
        if self.producer_policy_hash != PREDICTIVE_PRODUCER_POLICY_HASH:
            raise ValueError("predictive producer policy differs")
        if self.caps != tuple(sorted(set(self.caps))):
            raise ValueError("predictive caps are not canonical")
        if self.warnings != tuple(sorted(set(self.warnings))):
            raise ValueError("predictive warnings are not canonical")
        if self.source_event_hashes != tuple(sorted(set(self.source_event_hashes))):
            raise ValueError("predictive source hashes are not canonical")
        if self.evidence_type == "observed_panel" and self.promotion_effect != "none":
            raise ValueError("observed-panel evidence can never promote")
        object.__setattr__(self, "split_metrics", _freeze(self.split_metrics))
        object.__setattr__(
            self, "universe_bias_assessment", _freeze(self.universe_bias_assessment)
        )
        object.__setattr__(self, "pit_authority", _freeze(self.pit_authority))
        if self.evidence_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("predictive evidence hash differs")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evidence_type": self.evidence_type,
            "factor_spec_id": self.factor_spec_id,
            "run_id": self.run_id,
            "factor_output_hash": self.factor_output_hash,
            "factor_output_event_hash": self.factor_output_event_hash,
            "resolved_contract_hash": self.resolved_contract_hash,
            "contract_event_hash": self.contract_event_hash,
            "pit_snapshot_hash": self.pit_snapshot_hash,
            "pit_snapshot_event_hash": self.pit_snapshot_event_hash,
            "availability": self.availability,
            "claim_scope": self.claim_scope,
            "evidence_grade": self.evidence_grade,
            "split_metrics": _plain(self.split_metrics),
            "universe_bias_assessment": _plain(self.universe_bias_assessment),
            "pit_authority": _plain(self.pit_authority),
            "promotion_effect": self.promotion_effect,
            "caps": list(self.caps),
            "warnings": list(self.warnings),
            "source_event_hashes": list(self.source_event_hashes),
            "producer_schema_version": self.producer_schema_version,
            "producer_policy_hash": self.producer_policy_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "evidence_hash": self.evidence_hash}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PredictiveEvidenceV1":
        if set(raw) != _PREDICTIVE_KEYS:
            raise ValueError("predictive evidence artifact schema is not closed")
        for name in ("split_metrics", "universe_bias_assessment", "pit_authority"):
            if not isinstance(raw[name], Mapping):
                raise ValueError("predictive evidence nested schema is invalid")
        return cls(
            schema_version=str(raw["schema_version"]),  # type: ignore[arg-type]
            evidence_type=str(raw["evidence_type"]),  # type: ignore[arg-type]
            factor_spec_id=str(raw["factor_spec_id"]),
            run_id=str(raw["run_id"]),
            factor_output_hash=str(raw["factor_output_hash"]),
            factor_output_event_hash=str(raw["factor_output_event_hash"]),
            resolved_contract_hash=str(raw["resolved_contract_hash"]),
            contract_event_hash=str(raw["contract_event_hash"]),
            pit_snapshot_hash=str(raw["pit_snapshot_hash"]),
            pit_snapshot_event_hash=str(raw["pit_snapshot_event_hash"]),
            availability=str(raw["availability"]),  # type: ignore[arg-type]
            claim_scope=str(raw["claim_scope"]),
            evidence_grade=str(raw["evidence_grade"]),  # type: ignore[arg-type]
            split_metrics=dict(raw["split_metrics"]),
            universe_bias_assessment=dict(raw["universe_bias_assessment"]),
            pit_authority=dict(raw["pit_authority"]),
            promotion_effect=str(raw["promotion_effect"]),  # type: ignore[arg-type]
            caps=tuple(str(item) for item in raw["caps"]),
            warnings=tuple(str(item) for item in raw["warnings"]),
            source_event_hashes=tuple(str(item) for item in raw["source_event_hashes"]),
            producer_schema_version=str(raw["producer_schema_version"]),
            producer_policy_hash=str(raw["producer_policy_hash"]),
            evidence_hash=str(raw["evidence_hash"]),
        )


@dataclass(frozen=True)
class ScorecardDecisionEvidenceV4:
    schema_version: Literal["scorecard_decision_evidence.v4"]
    factor_spec_id: str
    run_id: str
    resolved_contract_hash: str
    factor_output_hash: str
    observed_evidence_hash: str
    pit_evidence_hash: str
    factor_output_event_hash: str
    observed_event_hash: str
    pit_event_hash: str
    pit_predictive_authority: Literal["available", "unavailable", "contaminated"]
    candidate_promotion_effect: Literal["blocked_pending_execution"]
    caps: tuple[str, ...]
    warnings: tuple[str, ...]
    source_event_hashes: tuple[str, ...]
    producer_schema_version: str
    producer_policy_hash: str
    scorecard_evidence_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "scorecard_decision_evidence.v4":
            raise ValueError("unsupported scorecard evidence v4")
        if self.candidate_promotion_effect != "blocked_pending_execution":
            raise ValueError("Phase 2 cannot claim complete candidate eligibility")
        if self.caps != tuple(sorted(set(self.caps))):
            raise ValueError("scorecard v4 caps are not canonical")
        if self.warnings != tuple(sorted(set(self.warnings))):
            raise ValueError("scorecard v4 warnings are not canonical")
        if self.source_event_hashes != tuple(sorted(set(self.source_event_hashes))):
            raise ValueError("scorecard v4 source hashes are not canonical")
        if self.scorecard_evidence_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("scorecard v4 evidence hash differs")

    def _content_dict(self) -> dict[str, Any]:
        return {
            name: value
            for name, value in {
                "schema_version": self.schema_version,
                "factor_spec_id": self.factor_spec_id,
                "run_id": self.run_id,
                "resolved_contract_hash": self.resolved_contract_hash,
                "factor_output_hash": self.factor_output_hash,
                "observed_evidence_hash": self.observed_evidence_hash,
                "pit_evidence_hash": self.pit_evidence_hash,
                "factor_output_event_hash": self.factor_output_event_hash,
                "observed_event_hash": self.observed_event_hash,
                "pit_event_hash": self.pit_event_hash,
                "pit_predictive_authority": self.pit_predictive_authority,
                "candidate_promotion_effect": self.candidate_promotion_effect,
                "caps": list(self.caps),
                "warnings": list(self.warnings),
                "source_event_hashes": list(self.source_event_hashes),
                "producer_schema_version": self.producer_schema_version,
                "producer_policy_hash": self.producer_policy_hash,
            }.items()
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._content_dict(),
            "scorecard_evidence_hash": self.scorecard_evidence_hash,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ScorecardDecisionEvidenceV4":
        if set(raw) != _SCORECARD_KEYS:
            raise ValueError("scorecard v4 artifact schema is not closed")
        return cls(
            schema_version=str(raw["schema_version"]),  # type: ignore[arg-type]
            factor_spec_id=str(raw["factor_spec_id"]),
            run_id=str(raw["run_id"]),
            resolved_contract_hash=str(raw["resolved_contract_hash"]),
            factor_output_hash=str(raw["factor_output_hash"]),
            observed_evidence_hash=str(raw["observed_evidence_hash"]),
            pit_evidence_hash=str(raw["pit_evidence_hash"]),
            factor_output_event_hash=str(raw["factor_output_event_hash"]),
            observed_event_hash=str(raw["observed_event_hash"]),
            pit_event_hash=str(raw["pit_event_hash"]),
            pit_predictive_authority=str(raw["pit_predictive_authority"]),  # type: ignore[arg-type]
            candidate_promotion_effect=str(raw["candidate_promotion_effect"]),  # type: ignore[arg-type]
            caps=tuple(str(item) for item in raw["caps"]),
            warnings=tuple(str(item) for item in raw["warnings"]),
            source_event_hashes=tuple(str(item) for item in raw["source_event_hashes"]),
            producer_schema_version=str(raw["producer_schema_version"]),
            producer_policy_hash=str(raw["producer_policy_hash"]),
            scorecard_evidence_hash=str(raw["scorecard_evidence_hash"]),
        )


class _JsonEvidenceStore:
    def __init__(
        self,
        root: str | Path,
        *,
        namespace: str,
        media_type: str,
        keys: frozenset[str],
        semantic_field: str,
    ) -> None:
        self.root = Path(root).resolve(strict=True)
        self.namespace = namespace
        self.media_type = media_type
        self.keys = keys
        self.semantic_field = semantic_field
        self.writer = AtomicContentAddressedArtifactWriter(
            self.root, max_bytes=4 * 1024**2
        )

    def write(self, payload: Mapping[str, Any]) -> ContentAddressedArtifact:
        return self.writer.write_json(
            namespace=self.namespace,
            payload=dict(payload),
            schema_version=str(payload["schema_version"]),
            semantic_hash_field=self.semantic_field,
            closed_keys=self.keys,
            media_type=self.media_type,
        )

    def read(
        self, relative_path: str, *, expected_hash: str, expected_blob_hash: str
    ) -> Mapping[str, Any]:
        normalized = validate_artifact_references(
            self.root,
            [
                {
                    "relative_path": relative_path,
                    "artifact_hash": expected_blob_hash,
                    "media_type": self.media_type,
                }
            ],
        )[0]
        digest = expected_hash.removeprefix("sha256:")
        expected = f"{self.namespace}/{digest[:2]}/{digest}.json"
        if normalized["relative_path"] != expected:
            raise ValueError("predictive artifact path is not content addressed")
        raw = _strict_json_read(self.root.joinpath(*expected.split("/")))
        if set(raw) != self.keys or raw[self.semantic_field] != expected_hash:
            raise ValueError("predictive artifact identity or schema differs")
        return raw


@dataclass(frozen=True)
class RecordedPredictiveEvidenceV4:
    factor_output: FactorOutputArtifactV3
    factor_event: ResearchEventEnvelope
    observed: PredictiveEvidenceV1
    observed_event: ResearchEventEnvelope
    pit: PredictiveEvidenceV1
    pit_event: ResearchEventEnvelope
    scorecard: ScorecardDecisionEvidenceV4
    scorecard_event: ResearchEventEnvelope


class PITPredictiveEvidenceServiceV4:
    def __init__(self, store: Any) -> None:
        from src.research_ledger.events.store import ResearchEventStore

        if not isinstance(store, ResearchEventStore):
            raise TypeError("predictive evidence requires ResearchEventStore")
        required = (
            "VIBE_TRADING_ALPHA_FOUNDRY",
            "VIBE_TRADING_ALPHA_SCORECARD",
            "VIBE_TRADING_RESEARCH_EVENTS",
            "VIBE_TRADING_FACTOR_DAG",
        )
        if any(not store.flags.enabled(name) for name in required):
            raise RuntimeError("predictive evidence capability is disabled")
        self.store = store
        self.factor_artifacts = FactorOutputArtifactStoreV3(store.artifact_root)
        self.predictive_artifacts = _JsonEvidenceStore(
            store.artifact_root,
            namespace="predictive-evidence-v1",
            media_type=PREDICTIVE_EVIDENCE_MEDIA_TYPE,
            keys=_PREDICTIVE_KEYS,
            semantic_field="evidence_hash",
        )
        self.scorecard_artifacts = _JsonEvidenceStore(
            store.artifact_root,
            namespace="scorecard-evidence-v4",
            media_type=SCORECARD_V4_MEDIA_TYPE,
            keys=_SCORECARD_KEYS,
            semantic_field="scorecard_evidence_hash",
        )

    def record(
        self,
        *,
        run_id: str,
        contract_event_hash: str,
        factor_definition_event_hash: str,
        pit_snapshot_event_hash: str,
    ) -> RecordedPredictiveEvidenceV4:
        definitions = [
            event
            for event in self.store.query_events(event_type="FactorDefinitionRecorded")
            if event.event_hash == factor_definition_event_hash
        ]
        if len(definitions) != 1:
            raise EventTransitionError("predictive factor definition is unavailable")
        factor_spec_id = definitions[0].entity_id
        existing = [
            event
            for event in self.store.query_events(event_type=SCORECARD_V4_EVENT_TYPE)
            if event.run_id == run_id
            and event.payload["factor_spec_id"] == factor_spec_id
            and event.payload["source_event_hashes"]
        ]
        if existing:
            if len(existing) != 1:
                raise EventTransitionError(
                    "predictive run has ambiguous scorecard evidence"
                )
            recorded = self._load_existing(existing[0])
            requested = {
                contract_event_hash,
                factor_definition_event_hash,
                pit_snapshot_event_hash,
            }
            if (
                recorded.factor_output.contract_event_hash != contract_event_hash
                or recorded.factor_output.pit_snapshot_event_hash
                != pit_snapshot_event_hash
                or not requested.issubset(recorded.factor_output.source_event_hashes)
            ):
                raise EventTransitionError(
                    "predictive retry sources differ from the recorded run"
                )
            rebuilt = self.rebuild(
                self.store,
                run_id=run_id,
                contract_event_hash=contract_event_hash,
                factor_definition_event_hash=factor_definition_event_hash,
                pit_snapshot_event_hash=pit_snapshot_event_hash,
                persist_factor=False,
            )
            rebuilt_observed = self._with_factor_event(
                rebuilt[1], recorded.factor_event.event_hash
            )
            rebuilt_pit = self._with_factor_event(
                rebuilt[2], recorded.factor_event.event_hash
            )
            if (
                rebuilt[0].factor_output_hash
                != recorded.factor_output.factor_output_hash
                or rebuilt_observed.evidence_hash != recorded.observed.evidence_hash
                or rebuilt_pit.evidence_hash != recorded.pit.evidence_hash
            ):
                raise EventValidationError("predictive retry does not replay exactly")
            return recorded
        rebuilt = self.rebuild(
            self.store,
            run_id=run_id,
            contract_event_hash=contract_event_hash,
            factor_definition_event_hash=factor_definition_event_hash,
            pit_snapshot_event_hash=pit_snapshot_event_hash,
            persist_factor=True,
        )
        with self.store._validation_cache_scope():
            return self._record_rebuilt(run_id=run_id, rebuilt=rebuilt)

    def _record_rebuilt(
        self,
        *,
        run_id: str,
        rebuilt: tuple[
            FactorOutputArtifactV3,
            PredictiveEvidenceV1,
            PredictiveEvidenceV1,
        ],
    ) -> RecordedPredictiveEvidenceV4:
        factor_manifest_artifact = self.factor_artifacts.write_manifest(rebuilt[0])
        factor_event = self.store._append_producer_event(
            EventDraft(
                event_type=FACTOR_OUTPUT_EVENT_TYPE,
                entity_id=self._id("factor-output-v3", rebuilt[0].factor_output_hash),
                run_id=run_id,
                payload_schema_version="factor_output_recorded.v3",
                idempotency_key="factor-output-v3:" + rebuilt[0].factor_output_hash,
                payload=self.factor_event_payload(
                    rebuilt[0], factor_manifest_artifact.reference()
                ),
            )
        )
        observed = self._with_factor_event(rebuilt[1], factor_event.event_hash)
        observed_artifact = self.predictive_artifacts.write(observed.to_dict())
        observed_event = self.store._append_producer_event(
            EventDraft(
                event_type=OBSERVED_PREDICTIVE_EVENT_TYPE,
                entity_id=self._id("observed-predictive", observed.evidence_hash),
                run_id=run_id,
                payload_schema_version="observed_panel_predictive_recorded.v1",
                idempotency_key="observed-predictive:" + observed.evidence_hash,
                payload=self.predictive_event_payload(
                    observed, observed_artifact.reference()
                ),
            )
        )
        pit = self._with_factor_event(rebuilt[2], factor_event.event_hash)
        pit_artifact = self.predictive_artifacts.write(pit.to_dict())
        pit_event = self.store._append_producer_event(
            EventDraft(
                event_type=PIT_PREDICTIVE_EVENT_TYPE,
                entity_id=self._id("pit-predictive", pit.evidence_hash),
                run_id=run_id,
                payload_schema_version="pit_predictive_recorded.v1",
                idempotency_key="pit-predictive:" + pit.evidence_hash,
                payload=self.predictive_event_payload(pit, pit_artifact.reference()),
            )
        )
        scorecard = self._scorecard(
            factor=rebuilt[0],
            observed=observed,
            pit=pit,
            factor_event_hash=factor_event.event_hash,
            observed_event_hash=observed_event.event_hash,
            pit_event_hash=pit_event.event_hash,
        )
        scorecard_artifact = self.scorecard_artifacts.write(scorecard.to_dict())
        scorecard_event = self.store._append_producer_event(
            EventDraft(
                event_type=SCORECARD_V4_EVENT_TYPE,
                entity_id=self._id("scorecard-v4", scorecard.scorecard_evidence_hash),
                run_id=run_id,
                payload_schema_version="scorecard_decision_evidence_recorded.v4",
                idempotency_key="scorecard-v4:" + scorecard.scorecard_evidence_hash,
                payload=self.scorecard_event_payload(
                    scorecard, scorecard_artifact.reference()
                ),
            )
        )
        return RecordedPredictiveEvidenceV4(
            rebuilt[0],
            factor_event,
            observed,
            observed_event,
            pit,
            pit_event,
            scorecard,
            scorecard_event,
        )

    @staticmethod
    def _id(prefix: str, value: str) -> str:
        return prefix + "-" + value.removeprefix("sha256:")[:24]

    @staticmethod
    def _with_factor_event(
        evidence: PredictiveEvidenceV1, event_hash: str
    ) -> PredictiveEvidenceV1:
        content = evidence._content_dict()
        content["factor_output_event_hash"] = event_hash
        content["source_event_hashes"] = sorted(
            set(content["source_event_hashes"]) | {event_hash}
        )
        return PredictiveEvidenceV1.from_dict(
            {**content, "evidence_hash": canonical_json_hash(content)}
        )

    @staticmethod
    def rebuild(
        store: Any,
        *,
        run_id: str,
        contract_event_hash: str,
        factor_definition_event_hash: str,
        pit_snapshot_event_hash: str,
        persist_factor: bool,
    ) -> tuple[FactorOutputArtifactV3, PredictiveEvidenceV1, PredictiveEvidenceV1]:
        events = store.query_events()
        contract_event = _event_by_hash(
            events, contract_event_hash, "ResolvedEvaluationContractRegistered"
        )
        definition = _event_by_hash(
            events, factor_definition_event_hash, "FactorDefinitionRecorded"
        )
        snapshot_event = _event_by_hash(
            events, pit_snapshot_event_hash, "AsharePITSnapshotRecorded"
        )
        shared_activation_sources = PITPredictiveEvidenceServiceV4._shared_activation_sources(
            events=events,
            run_id=run_id,
            contract_event_hash=contract_event.event_hash,
            snapshot_event_hash=snapshot_event.event_hash,
        )
        if definition.run_id != run_id or (
            not shared_activation_sources
            and any(event.run_id != run_id for event in (contract_event, snapshot_event))
        ):
            raise EventTransitionError("predictive evidence cannot mix runs")
        validate_factor_definition_payload(definition.payload)
        contract_ref = _artifact_ref(contract_event, CONTRACT_MEDIA_TYPE)
        contract = ResolvedEvaluationContractArtifactStoreV1(store.artifact_root).read(
            str(contract_ref["relative_path"]),
            expected_contract_hash=str(contract_event.payload["contract_hash"]),
            expected_blob_hash=str(contract_ref["artifact_hash"]),
        )
        snapshot_ref = _artifact_ref(snapshot_event, PIT_MANIFEST_MEDIA_TYPE)
        snapshot_store = FrozenAsharePITSnapshotArtifactStoreV2(store.artifact_root)
        snapshot = snapshot_store.read_manifest(
            str(snapshot_ref["relative_path"]),
            expected_snapshot_hash=str(snapshot_event.payload["snapshot_hash"]),
            expected_blob_hash=str(snapshot_ref["artifact_hash"]),
        )
        AsharePITSnapshotServiceV2.rebuild_snapshot(
            store,
            snapshot=snapshot,
            adapter_registration_event_hash=str(
                snapshot_event.payload["adapter_registration_event_hash"]
            ),
            evaluation_policy_event_hash=str(
                snapshot_event.payload["evaluation_policy_event_hash"]
            ),
            run_id=snapshot_event.run_id,
        )
        if (
            contract.evaluation_policy_event_hash
            != snapshot.evaluation_policy_event_hash
            or contract.evaluation_policy_bundle_hash
            != str(
                _event_by_hash(
                    events,
                    contract.evaluation_policy_event_hash,
                    "EvaluationPolicyRegistered",
                ).payload["bundle_hash"]
            )
        ):
            raise EventTransitionError(
                "predictive contract and PIT snapshot policy differ"
            )
        policy_event = _event_by_hash(
            events, contract.evaluation_policy_event_hash, "EvaluationPolicyRegistered"
        )
        policy_ref = _artifact_ref(
            policy_event, "application/vnd.vibe.registered-evaluation-policy-v1+json"
        )
        policy_bundle = EvaluationPolicyArtifactStoreV1(store.artifact_root).read(
            str(policy_ref["relative_path"]),
            expected_bundle_hash=str(policy_event.payload["bundle_hash"]),
            expected_blob_hash=str(policy_ref["artifact_hash"]),
        )
        calendar, time_policy, split_plan = policy_bundle.resolved_components()
        bundle = snapshot_store.read_bundle(snapshot)
        close = bundle.market_fields.get("close")
        if close is None:
            raise EventValidationError("PIT snapshot has no close source")
        dates = tuple(str(item) for item in snapshot.request["calendar_dates"])
        symbols = tuple(str(item) for item in bundle.daily_membership.columns)
        for name, frame in bundle.market_fields.items():
            _exact_axes(frame, dates, symbols, "PIT market field " + name)
        formula = str(definition.payload["metadata"]["canonical_formula"])
        executable = ExecutableGrammarSnapshotV1.from_runtime()
        if (
            definition.payload["grammar_version"] != executable.source_grammar_version
            or definition.payload["grammar_hash"] != executable.source_grammar_hash
            or contract.policy_references.executable_grammar_snapshot_hash
            != executable.snapshot_hash
        ):
            raise EventValidationError("factor has no exact executable grammar backend")
        try:
            factor = executable.evaluate(formula, dict(bundle.market_fields)).astype(
                float
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise EventValidationError(
                "factor output cannot be rebuilt from PIT fields"
            ) from exc
        factor = factor.replace([np.inf, -np.inf], np.nan)
        _exact_axes(factor, dates, symbols, "factor output")
        table_store = FactorOutputArtifactStoreV3(store.artifact_root)
        if not persist_factor:
            existing_factor_events = [
                event
                for event in events
                if event.event_type == FACTOR_OUTPUT_EVENT_TYPE
                and event.run_id == run_id
                and event.payload["factor_spec_id"] == definition.entity_id
            ]
            if len(existing_factor_events) != 1:
                raise EventValidationError(
                    "recorded factor output source is unavailable"
                )
            ref = _artifact_ref(existing_factor_events[0], FACTOR_OUTPUT_MEDIA_TYPE)
            recorded_factor = table_store.read_manifest(
                str(ref["relative_path"]),
                expected_hash=str(
                    existing_factor_events[0].payload["factor_output_hash"]
                ),
                expected_blob_hash=str(ref["artifact_hash"]),
            )
            table_ref = AsharePITTableReferenceV1.from_dict(
                recorded_factor.factor_table_ref
            )
            rebuilt_frame = table_store.tables.read(table_ref)
            rebuilt_frame.index = pd.DatetimeIndex(rebuilt_frame.index)
            try:
                pd.testing.assert_frame_equal(
                    factor, rebuilt_frame.astype(float), check_freq=False
                )
            except AssertionError as exc:
                raise EventValidationError(
                    "factor Parquet differs from backend replay"
                ) from exc
        else:
            table_ref = table_store.write_factor_table(definition.entity_id, factor)
        pit_status = str(snapshot.derived_evidence["pit_contract_status"])
        mask_status: Literal["complete", "partial", "unavailable", "contaminated"]
        if pit_status == "complete":
            mask_status = "complete"
        elif pit_status == "contaminated":
            mask_status = "contaminated"
        elif snapshot.derived_evidence["can_observe_count"]:
            mask_status = "partial"
        else:
            mask_status = "unavailable"
        factor_sources = tuple(
            sorted(
                {
                    contract_event.event_hash,
                    definition.event_hash,
                    snapshot_event.event_hash,
                    policy_event.event_hash,
                }
            )
        )
        factor_content = {
            "schema_version": "factor_output_artifact.v3",
            "factor_spec_id": definition.entity_id,
            "canonical_formula": formula,
            "run_id": run_id,
            "resolved_contract_hash": contract.contract_hash,
            "contract_event_hash": contract_event.event_hash,
            "pit_snapshot_hash": snapshot.snapshot_hash,
            "pit_snapshot_event_hash": snapshot_event.event_hash,
            "evaluation_policy_event_hash": policy_event.event_hash,
            "split_plan_hash": split_plan.plan_hash,
            "evaluation_time_policy_hash": time_policy.policy_hash,
            "executable_grammar_snapshot_hash": executable.snapshot_hash,
            "backend_version": executable.backend_version,
            "dates": list(dates),
            "symbols": list(symbols),
            "shape": [len(dates), len(symbols)],
            "factor_table_ref": table_ref.to_dict(),
            "source_event_hashes": list(factor_sources),
            "pit_mask_status": mask_status,
            "producer_schema_version": PREDICTIVE_PRODUCER_SCHEMA,
            "producer_policy_hash": PREDICTIVE_PRODUCER_POLICY_HASH,
        }
        factor_artifact = FactorOutputArtifactV3.from_dict(
            {
                **factor_content,
                "factor_output_hash": canonical_json_hash(factor_content),
            }
        )
        observed = PITPredictiveEvidenceServiceV4._predictive(
            factor_artifact=factor_artifact,
            factor_frame=factor,
            close=close.astype(float),
            masks=None,
            snapshot=snapshot,
            contract_event=contract_event,
            snapshot_event=snapshot_event,
            time_policy=time_policy,
            split_plan=split_plan,
            channel="observed_panel",
        )
        derived_masks = snapshot_store.read_derived_masks(snapshot)
        pit = PITPredictiveEvidenceServiceV4._predictive(
            factor_artifact=factor_artifact,
            factor_frame=factor,
            close=close.astype(float),
            masks=derived_masks,
            snapshot=snapshot,
            contract_event=contract_event,
            snapshot_event=snapshot_event,
            time_policy=time_policy,
            split_plan=split_plan,
            channel="pit_scoped",
        )
        return factor_artifact, observed, pit

    @staticmethod
    def _shared_activation_sources(
        *,
        events: list[ResearchEventEnvelope],
        run_id: str,
        contract_event_hash: str,
        snapshot_event_hash: str,
    ) -> bool:
        starts = [
            event
            for event in events
            if event.event_type == "ProductionActivationArmStartedV1Recorded"
            and event.run_id == run_id
        ]
        if len(starts) != 1:
            return False
        bundle_event_hash = starts[0].payload.get("run_input_bundle_event_hash")
        bundles = [
            event
            for event in events
            if event.event_hash == bundle_event_hash
            and event.event_type in {
                "ProductionActivationRunInputBundleV1Registered",
                "ResearchOnlyActivationRunInputRegistered",
            }
        ]
        if len(bundles) != 1 or not isinstance(
            bundles[0].payload.get("bundle"), Mapping
        ):
            return False
        bundle = bundles[0].payload["bundle"]
        return (
            bundle.get("resolved_contract_event_hash") == contract_event_hash
            and bundle.get("pit_snapshot_event_hash") == snapshot_event_hash
        )

    @staticmethod
    def _predictive(
        *,
        factor_artifact: FactorOutputArtifactV3,
        factor_frame: pd.DataFrame,
        close: pd.DataFrame,
        masks: Mapping[str, pd.DataFrame] | None,
        snapshot: Any,
        contract_event: ResearchEventEnvelope,
        snapshot_event: ResearchEventEnvelope,
        time_policy: Any,
        split_plan: Any,
        channel: Literal["observed_panel", "pit_scoped"],
    ) -> PredictiveEvidenceV1:
        metrics: dict[str, Any] = {}
        complete_pit = bool(snapshot.derived_evidence["decision_grade"])
        required_masks = {"can_observe", "eligible_universe"}
        mask_complete = masks is not None and required_masks.issubset(masks)
        availability: Literal["available", "partial", "unavailable", "invalid"]
        caps: set[str] = set()
        warnings = set(str(item) for item in snapshot.derived_evidence["warnings"])
        if channel == "pit_scoped" and not (complete_pit and mask_complete):
            availability = "unavailable"
            caps.add("PIT_PREDICTIVE_AUTHORITY_UNAVAILABLE")
        else:
            availability = "available"
            for split_name in ("train", "valid"):
                window = getattr(split_plan, split_name)
                dates = tuple(
                    date
                    for date in factor_artifact.dates
                    if window.eligible_signal_start
                    <= date
                    <= window.eligible_signal_end
                )
                index = pd.DatetimeIndex(dates)
                factor = factor_frame.loc[index, list(factor_artifact.symbols)]
                returns = PITPredictiveEvidenceServiceV4._forward_returns(
                    close,
                    signal_dates=dates,
                    all_dates=factor_artifact.dates,
                    entry_lag=time_policy.entry_lag_trading_days,
                    horizon=time_policy.execution_horizon,
                )
                mask = pd.DataFrame(
                    np.isfinite(factor.to_numpy()) & np.isfinite(returns.to_numpy()),
                    index=index,
                    columns=factor_artifact.symbols,
                )
                if channel == "pit_scoped":
                    assert masks is not None
                    for name in sorted(required_masks):
                        source = masks[name].loc[index, list(factor_artifact.symbols)]
                        _exact_axes(source, dates, factor_artifact.symbols, name)
                        if any(dtype != np.dtype(bool) for dtype in source.dtypes):
                            raise EventValidationError(name + " must be strict bool")
                        mask &= source
                _exact_axes(
                    factor, dates, factor_artifact.symbols, split_name + " factor"
                )
                _exact_axes(
                    returns, dates, factor_artifact.symbols, split_name + " returns"
                )
                _exact_axes(mask, dates, factor_artifact.symbols, split_name + " mask")
                summary = compute_ic_metrics(
                    factor,
                    returns,
                    horizon=time_policy.execution_horizon,
                    valid_mask=mask,
                    min_cross_section=2,
                )
                metrics[split_name] = {
                    "horizon": summary.horizon,
                    "rank_ic_mean": summary.rank_ic_mean,
                    "rank_ic_std": summary.rank_ic_std,
                    "rank_icir": summary.rank_icir,
                    "t_stat": summary.t_stat,
                    "t_stat_method": summary.t_stat_method,
                    "effective_dates": summary.n_obs,
                    "eligible_cells": int(mask.to_numpy().sum()),
                }
            if int(metrics["valid"]["effective_dates"]) == 0:
                availability = "partial"
                caps.add("PREDICTIVE_EFFECTIVE_SAMPLE_UNAVAILABLE")
        if channel == "observed_panel":
            bias_status = "none_detected" if complete_pit else "unknown"
            bias = {
                "schema_version": "universe_bias_assessment.v1",
                "status": bias_status,
                "universe_construction": (
                    "verified_pit_daily_membership"
                    if complete_pit
                    else "frozen_unverified_observed_panel"
                ),
                "direction_identifiability": "not_identified",
                "likely_effect_on_performance_claims": (
                    "unknown" if complete_pit else "optimistic_risk"
                ),
                "survivorship_status": str(
                    snapshot.derived_evidence["survivorship_status"]
                ),
            }
            grade = "exploratory"
            claim_scope = "frozen_observed_panel"
            promotion = "none"
        else:
            bias = {
                "schema_version": "universe_bias_assessment.v1",
                "status": (
                    "controlled_by_daily_membership" if complete_pit else "unknown"
                ),
                "universe_construction": "pit_daily_membership",
                "direction_identifiability": "not_identified",
                "likely_effect_on_performance_claims": "unknown",
                "survivorship_status": str(
                    snapshot.derived_evidence["survivorship_status"]
                ),
            }
            grade = "confirmatory" if complete_pit else "exploratory"
            claim_scope = "pit_daily_membership_train_valid"
            promotion = "blocked_pending_execution"
        sources = tuple(
            sorted(
                {
                    contract_event.event_hash,
                    snapshot_event.event_hash,
                    *factor_artifact.source_event_hashes,
                }
            )
        )
        content = {
            "schema_version": "predictive_evidence.v1",
            "evidence_type": channel,
            "factor_spec_id": factor_artifact.factor_spec_id,
            "run_id": factor_artifact.run_id,
            "factor_output_hash": factor_artifact.factor_output_hash,
            "factor_output_event_hash": "sha256:" + "0" * 64,
            "resolved_contract_hash": factor_artifact.resolved_contract_hash,
            "contract_event_hash": contract_event.event_hash,
            "pit_snapshot_hash": factor_artifact.pit_snapshot_hash,
            "pit_snapshot_event_hash": snapshot_event.event_hash,
            "availability": availability,
            "claim_scope": claim_scope,
            "evidence_grade": grade,
            "split_metrics": metrics,
            "universe_bias_assessment": bias,
            "pit_authority": {
                "pit_contract_status": str(
                    snapshot.derived_evidence["pit_contract_status"]
                ),
                "adapter_authority_class": str(
                    snapshot.derived_evidence["adapter_authority_class"]
                ),
                "decision_grade": complete_pit,
            },
            "promotion_effect": promotion,
            "caps": sorted(caps),
            "warnings": sorted(warnings),
            "source_event_hashes": list(sources),
            "producer_schema_version": PREDICTIVE_PRODUCER_SCHEMA,
            "producer_policy_hash": PREDICTIVE_PRODUCER_POLICY_HASH,
        }
        return PredictiveEvidenceV1.from_dict(
            {**content, "evidence_hash": canonical_json_hash(content)}
        )

    @staticmethod
    def _forward_returns(
        close: pd.DataFrame,
        *,
        signal_dates: tuple[str, ...],
        all_dates: tuple[str, ...],
        entry_lag: int,
        horizon: int,
    ) -> pd.DataFrame:
        positions = {date: index for index, date in enumerate(all_dates)}
        values: list[np.ndarray] = []
        for date in signal_dates:
            signal = positions[date]
            entry = signal + entry_lag
            exit_index = entry + horizon
            if exit_index >= len(all_dates):
                raise EventValidationError(
                    "registered eligible signal crosses snapshot end"
                )
            entry_values = close.loc[pd.Timestamp(all_dates[entry])].to_numpy(
                dtype=float
            )
            exit_values = close.loc[pd.Timestamp(all_dates[exit_index])].to_numpy(
                dtype=float
            )
            result = np.full_like(entry_values, np.nan, dtype=float)
            valid = (
                np.isfinite(entry_values)
                & np.isfinite(exit_values)
                & (entry_values != 0)
            )
            result[valid] = exit_values[valid] / entry_values[valid] - 1.0
            values.append(result)
        return pd.DataFrame(
            values,
            index=pd.DatetimeIndex(signal_dates),
            columns=close.columns,
        )

    @staticmethod
    def _scorecard(
        *,
        factor: FactorOutputArtifactV3,
        observed: PredictiveEvidenceV1,
        pit: PredictiveEvidenceV1,
        factor_event_hash: str,
        observed_event_hash: str,
        pit_event_hash: str,
    ) -> ScorecardDecisionEvidenceV4:
        authority = (
            "available"
            if pit.availability == "available"
            else (
                "contaminated"
                if pit.pit_authority["pit_contract_status"] == "contaminated"
                else "unavailable"
            )
        )
        caps = set(pit.caps)
        caps.add("PRODUCER_BOUND_EXECUTION_EVIDENCE_REQUIRED")
        sources = tuple(
            sorted(
                {
                    *factor.source_event_hashes,
                    factor_event_hash,
                    observed_event_hash,
                    pit_event_hash,
                }
            )
        )
        content = {
            "schema_version": "scorecard_decision_evidence.v4",
            "factor_spec_id": factor.factor_spec_id,
            "run_id": factor.run_id,
            "resolved_contract_hash": factor.resolved_contract_hash,
            "factor_output_hash": factor.factor_output_hash,
            "observed_evidence_hash": observed.evidence_hash,
            "pit_evidence_hash": pit.evidence_hash,
            "factor_output_event_hash": factor_event_hash,
            "observed_event_hash": observed_event_hash,
            "pit_event_hash": pit_event_hash,
            "pit_predictive_authority": authority,
            "candidate_promotion_effect": "blocked_pending_execution",
            "caps": sorted(caps),
            "warnings": sorted(set(observed.warnings) | set(pit.warnings)),
            "source_event_hashes": list(sources),
            "producer_schema_version": PREDICTIVE_PRODUCER_SCHEMA,
            "producer_policy_hash": PREDICTIVE_PRODUCER_POLICY_HASH,
        }
        return ScorecardDecisionEvidenceV4.from_dict(
            {**content, "scorecard_evidence_hash": canonical_json_hash(content)}
        )

    @staticmethod
    def factor_event_payload(
        artifact: FactorOutputArtifactV3, reference: Mapping[str, str]
    ) -> dict[str, Any]:
        return {
            "factor_output_id": PITPredictiveEvidenceServiceV4._id(
                "factor-output-v3", artifact.factor_output_hash
            ),
            "factor_output_hash": artifact.factor_output_hash,
            "factor_spec_id": artifact.factor_spec_id,
            "resolved_contract_hash": artifact.resolved_contract_hash,
            "contract_event_hash": artifact.contract_event_hash,
            "pit_snapshot_hash": artifact.pit_snapshot_hash,
            "pit_snapshot_event_hash": artifact.pit_snapshot_event_hash,
            "evaluation_policy_event_hash": artifact.evaluation_policy_event_hash,
            "factor_table_semantic_hash": str(
                artifact.factor_table_ref["semantic_hash"]
            ),
            "source_event_hashes": list(artifact.source_event_hashes),
            "producer_schema_version": artifact.producer_schema_version,
            "producer_policy_hash": artifact.producer_policy_hash,
            "artifact_refs": [dict(reference)],
        }

    @staticmethod
    def predictive_event_payload(
        evidence: PredictiveEvidenceV1, reference: Mapping[str, str]
    ) -> dict[str, Any]:
        return {
            "evidence_id": PITPredictiveEvidenceServiceV4._id(
                (
                    "observed-predictive"
                    if evidence.evidence_type == "observed_panel"
                    else "pit-predictive"
                ),
                evidence.evidence_hash,
            ),
            "evidence_hash": evidence.evidence_hash,
            "evidence_type": evidence.evidence_type,
            "factor_spec_id": evidence.factor_spec_id,
            "factor_output_hash": evidence.factor_output_hash,
            "factor_output_event_hash": evidence.factor_output_event_hash,
            "resolved_contract_hash": evidence.resolved_contract_hash,
            "contract_event_hash": evidence.contract_event_hash,
            "pit_snapshot_hash": evidence.pit_snapshot_hash,
            "pit_snapshot_event_hash": evidence.pit_snapshot_event_hash,
            "availability": evidence.availability,
            "claim_scope": evidence.claim_scope,
            "evidence_grade": evidence.evidence_grade,
            "promotion_effect": evidence.promotion_effect,
            "caps": list(evidence.caps),
            "warnings": list(evidence.warnings),
            "source_event_hashes": list(evidence.source_event_hashes),
            "producer_schema_version": evidence.producer_schema_version,
            "producer_policy_hash": evidence.producer_policy_hash,
            "artifact_refs": [dict(reference)],
        }

    @staticmethod
    def scorecard_event_payload(
        evidence: ScorecardDecisionEvidenceV4, reference: Mapping[str, str]
    ) -> dict[str, Any]:
        return {
            "evidence_id": PITPredictiveEvidenceServiceV4._id(
                "scorecard-v4", evidence.scorecard_evidence_hash
            ),
            "scorecard_evidence_hash": evidence.scorecard_evidence_hash,
            "factor_spec_id": evidence.factor_spec_id,
            "resolved_contract_hash": evidence.resolved_contract_hash,
            "factor_output_hash": evidence.factor_output_hash,
            "observed_evidence_hash": evidence.observed_evidence_hash,
            "pit_evidence_hash": evidence.pit_evidence_hash,
            "factor_output_event_hash": evidence.factor_output_event_hash,
            "observed_event_hash": evidence.observed_event_hash,
            "pit_event_hash": evidence.pit_event_hash,
            "pit_predictive_authority": evidence.pit_predictive_authority,
            "candidate_promotion_effect": evidence.candidate_promotion_effect,
            "caps": list(evidence.caps),
            "warnings": list(evidence.warnings),
            "source_event_hashes": list(evidence.source_event_hashes),
            "producer_schema_version": evidence.producer_schema_version,
            "producer_policy_hash": evidence.producer_policy_hash,
            "artifact_refs": [dict(reference)],
        }

    def _load_existing(
        self, scorecard_event: ResearchEventEnvelope
    ) -> RecordedPredictiveEvidenceV4:
        events = {event.event_hash: event for event in self.store.query_events()}
        factor_event = events[str(scorecard_event.payload["factor_output_event_hash"])]
        observed_event = events[str(scorecard_event.payload["observed_event_hash"])]
        pit_event = events[str(scorecard_event.payload["pit_event_hash"])]
        factor_ref = _artifact_ref(factor_event, FACTOR_OUTPUT_MEDIA_TYPE)
        factor = self.factor_artifacts.read_manifest(
            str(factor_ref["relative_path"]),
            expected_hash=str(factor_event.payload["factor_output_hash"]),
            expected_blob_hash=str(factor_ref["artifact_hash"]),
        )
        observed = self._read_predictive_event(observed_event)
        pit = self._read_predictive_event(pit_event)
        scorecard_ref = _artifact_ref(scorecard_event, SCORECARD_V4_MEDIA_TYPE)
        scorecard = ScorecardDecisionEvidenceV4.from_dict(
            self.scorecard_artifacts.read(
                str(scorecard_ref["relative_path"]),
                expected_hash=str(scorecard_event.payload["scorecard_evidence_hash"]),
                expected_blob_hash=str(scorecard_ref["artifact_hash"]),
            )
        )
        return RecordedPredictiveEvidenceV4(
            factor,
            factor_event,
            observed,
            observed_event,
            pit,
            pit_event,
            scorecard,
            scorecard_event,
        )

    def _read_predictive_event(
        self, event: ResearchEventEnvelope
    ) -> PredictiveEvidenceV1:
        ref = _artifact_ref(event, PREDICTIVE_EVIDENCE_MEDIA_TYPE)
        return PredictiveEvidenceV1.from_dict(
            self.predictive_artifacts.read(
                str(ref["relative_path"]),
                expected_hash=str(event.payload["evidence_hash"]),
                expected_blob_hash=str(ref["artifact_hash"]),
            )
        )


__all__ = [
    "FACTOR_OUTPUT_EVENT_TYPE",
    "FactorOutputArtifactStoreV3",
    "FactorOutputArtifactV3",
    "OBSERVED_PREDICTIVE_EVENT_TYPE",
    "PIT_PREDICTIVE_EVENT_TYPE",
    "PITPredictiveEvidenceServiceV4",
    "PredictiveEvidenceV1",
    "RecordedPredictiveEvidenceV4",
    "SCORECARD_V4_EVENT_TYPE",
    "ScorecardDecisionEvidenceV4",
]
