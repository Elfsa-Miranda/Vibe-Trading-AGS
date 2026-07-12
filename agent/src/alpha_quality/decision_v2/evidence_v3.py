"""Producer-scoped Decision evidence v3 and the first authoritative ledger mint."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping, cast

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


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_RECORD_MINT_AUTHORITY = object()
LEDGER_EVIDENCE_MEDIA_TYPE: Literal[
    "application/vnd.vibe.decision-evidence-v3+json"
] = "application/vnd.vibe.decision-evidence-v3+json"
LEDGER_PRODUCER_SCHEMA: Literal[
    "decision_ledger_evidence_service.v3"
] = "decision_ledger_evidence_service.v3"
SCORECARD_PRODUCER_SCHEMA: Literal[
    "decision_scorecard_evidence_service.v3"
] = "decision_scorecard_evidence_service.v3"
SCORECARD_IDENTITY_TRANSFORM_PIPELINE_HASH = canonical_json_hash(
    {"schema_version": "transform_pipeline.v1", "steps": []}
)
SCORECARD_UNIVERSE_MASK_POLICY_HASH = canonical_json_hash(
    {
        "schema_version": "universe_mask_policy.v1",
        "source_field": "universe_mask",
        "interpretation": "finite_nonzero_is_member",
    }
)
SCORECARD_TRADABILITY_MASK_POLICY_HASH = canonical_json_hash(
    {
        "schema_version": "tradability_mask_policy.v1",
        "source_field": "tradable_mask",
        "interpretation": "finite_nonzero_is_tradable",
    }
)
_MUTABLE_LEDGER_SOURCE_EVENT_TYPES = frozenset(
    {
        "TrialStarted",
        "GenerationFailureRecorded",
        "EvaluationRecorded",
        "TrialTerminated",
    }
)
LEDGER_PRODUCER_POLICY_HASH = canonical_json_hash(
    {
        "schema_version": "decision_ledger_evidence_policy.v1",
        "accepted_terminal_statuses": ["reject", "success"],
        "accepted_data_scopes": ["train_valid", "valid"],
        "completeness_unit": "all_started_trials_in_run_through_watermark",
        "terminal_selection": "exactly_one_eligible_factor_terminal_per_run",
        "source_artifact_validation": "path_and_blob_hash",
        "post_mint_same_run_invalidation_event_types": sorted(
            _MUTABLE_LEDGER_SOURCE_EVENT_TYPES
        ),
        "infrastructure_failure_sources": [
            "GenerationFailureRecorded.infrastructure_failure",
            "TrialTerminated.infrastructure_failure",
        ],
    }
)
SCORECARD_PRODUCER_POLICY_HASH = canonical_json_hash(
    {
        "schema_version": "decision_scorecard_evidence_policy.v1",
        "formula_source": "canonical_factor_definition_event.v1",
        "data_source": "frozen_train_valid_snapshot_event.v1",
        "split_source": "registered_evaluation_policy_event.v1",
        "backend": "core_dsl_evaluator.v1",
        "test_isolation": "backend_receives_dates_through_valid_end_only.v1",
        "metric_engine": "alpha_quality_scorecard.v2",
        "mask_policy": "snapshot_masks_or_explicit_unavailable.v1",
        "supported_transform_pipeline_hash": (
            SCORECARD_IDENTITY_TRANSFORM_PIPELINE_HASH
        ),
        "supported_universe_mask_policy_hash": (
            SCORECARD_UNIVERSE_MASK_POLICY_HASH
        ),
        "supported_tradability_mask_policy_hash": (
            SCORECARD_TRADABILITY_MASK_POLICY_HASH
        ),
        "authority": "computed_but_pit_and_mask_provenance_unverified.v1",
    }
)

_RECORD_KEYS = frozenset(
    {
        "schema_version",
        "evidence_kind",
        "factor_spec_id",
        "evidence_run_id",
        "producer_schema_version",
        "producer_policy_hash",
        "source_event_hashes",
        "source_artifact_hashes",
        "evidence_payload",
        "evidence_hash",
    }
)
_LEDGER_PAYLOAD_KEYS = frozenset(
    {
        "factor_definition_event_hash",
        "evaluation_event_hash",
        "terminal_event_hash",
        "ledger_watermark_event_hash",
        "data_scope",
        "complete",
        "terminal_train_valid",
        "reduced_durability",
        "infrastructure_failure_event_hashes",
    }
)
_SCORECARD_PAYLOAD_KEYS = frozenset(
    {
        "factor_definition_event_hash",
        "evaluation_policy_event_hash",
        "snapshot_event_hash",
        "source_watermark_event_hash",
        "data_scope",
        "scorecard_hash",
        "factor_output_content_hash",
        "computed_scorecard",
        "factor_output_manifest",
        "authority_status",
        "decision_grade",
        "caps",
    }
)


def _require_hash(value: str, name: str) -> None:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical sha256 hash")


def _sorted_hashes(values: tuple[str, ...], name: str, *, allow_empty: bool) -> None:
    if (not allow_empty and not values) or values != tuple(sorted(set(values))):
        raise ValueError(f"{name} must be a sorted unique hash tuple")
    for value in values:
        _require_hash(value, name)


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, init=False)
class DecisionEvidenceRecordV3:
    schema_version: Literal["decision_evidence_record.v3"]
    evidence_kind: Literal["ledger", "scorecard"]
    factor_spec_id: str
    evidence_run_id: str
    producer_schema_version: Literal[
        "decision_ledger_evidence_service.v3",
        "decision_scorecard_evidence_service.v3",
    ]
    producer_policy_hash: str
    source_event_hashes: tuple[str, ...]
    source_artifact_hashes: tuple[str, ...]
    evidence_payload: Mapping[str, Any]
    evidence_hash: str

    def __init__(
        self,
        *,
        schema_version: Literal["decision_evidence_record.v3"],
        evidence_kind: Literal["ledger", "scorecard"],
        factor_spec_id: str,
        evidence_run_id: str,
        producer_schema_version: Literal[
            "decision_ledger_evidence_service.v3",
            "decision_scorecard_evidence_service.v3",
        ],
        producer_policy_hash: str,
        source_event_hashes: tuple[str, ...],
        source_artifact_hashes: tuple[str, ...],
        evidence_payload: Mapping[str, Any],
        evidence_hash: str,
        _authority: object,
    ) -> None:
        if _authority is not _RECORD_MINT_AUTHORITY:
            raise TypeError(
                "DecisionEvidenceRecordV3 must be loaded or minted by its producer"
            )
        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "evidence_kind", evidence_kind)
        object.__setattr__(self, "factor_spec_id", factor_spec_id)
        object.__setattr__(self, "evidence_run_id", evidence_run_id)
        object.__setattr__(self, "producer_schema_version", producer_schema_version)
        object.__setattr__(self, "producer_policy_hash", producer_policy_hash)
        object.__setattr__(self, "source_event_hashes", tuple(source_event_hashes))
        object.__setattr__(
            self,
            "source_artifact_hashes",
            tuple(source_artifact_hashes),
        )
        object.__setattr__(self, "evidence_payload", _freeze(evidence_payload))
        object.__setattr__(self, "evidence_hash", evidence_hash)
        self._validate()

    def _validate(self) -> None:
        if self.schema_version != "decision_evidence_record.v3":
            raise ValueError("unsupported Decision evidence v3 schema")
        if self.evidence_kind not in {"ledger", "scorecard"}:
            raise ValueError("unsupported producer-bound evidence kind")
        if not self.factor_spec_id or not self.evidence_run_id:
            raise ValueError("Decision evidence factor and run are required")
        expected_producer = {
            "ledger": (LEDGER_PRODUCER_SCHEMA, LEDGER_PRODUCER_POLICY_HASH),
            "scorecard": (
                SCORECARD_PRODUCER_SCHEMA,
                SCORECARD_PRODUCER_POLICY_HASH,
            ),
        }[self.evidence_kind]
        if self.producer_schema_version != expected_producer[0]:
            raise ValueError("Decision evidence producer schema differs")
        if self.producer_policy_hash != expected_producer[1]:
            raise ValueError("Decision evidence producer policy differs")
        _sorted_hashes(self.source_event_hashes, "source_event_hashes", allow_empty=False)
        _sorted_hashes(
            self.source_artifact_hashes,
            "source_artifact_hashes",
            allow_empty=True,
        )
        payload = dict(self.evidence_payload)
        expected_payload_keys = (
            _LEDGER_PAYLOAD_KEYS
            if self.evidence_kind == "ledger"
            else _SCORECARD_PAYLOAD_KEYS
        )
        if set(payload) != expected_payload_keys:
            raise ValueError("Decision evidence payload is not closed")
        if self.evidence_kind == "scorecard":
            self._validate_scorecard_payload(payload)
            object.__setattr__(self, "evidence_payload", _freeze(payload))
            _require_hash(self.evidence_hash, "evidence_hash")
            if self.evidence_hash != canonical_json_hash(self._content_dict()):
                raise ValueError("Decision evidence v3 hash does not match content")
            return
        for name in (
            "factor_definition_event_hash",
            "evaluation_event_hash",
            "terminal_event_hash",
            "ledger_watermark_event_hash",
        ):
            _require_hash(str(payload[name]), name)
        if payload["data_scope"] not in {"valid", "train_valid"}:
            raise ValueError("Decision ledger evidence scope must be train/valid")
        for name in ("complete", "terminal_train_valid", "reduced_durability"):
            if not isinstance(payload[name], bool):
                raise ValueError(f"Decision ledger evidence {name} must be boolean")
        failure_hashes = tuple(payload["infrastructure_failure_event_hashes"])
        _sorted_hashes(
            failure_hashes,
            "infrastructure_failure_event_hashes",
            allow_empty=True,
        )
        payload["infrastructure_failure_event_hashes"] = failure_hashes
        object.__setattr__(self, "source_event_hashes", tuple(self.source_event_hashes))
        object.__setattr__(
            self,
            "source_artifact_hashes",
            tuple(self.source_artifact_hashes),
        )
        object.__setattr__(self, "evidence_payload", _freeze(payload))
        _require_hash(self.evidence_hash, "evidence_hash")
        if self.evidence_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("Decision evidence v3 hash does not match content")

    def _validate_scorecard_payload(self, payload: dict[str, Any]) -> None:
        for name in (
            "factor_definition_event_hash",
            "evaluation_policy_event_hash",
            "snapshot_event_hash",
            "source_watermark_event_hash",
            "scorecard_hash",
            "factor_output_content_hash",
        ):
            _require_hash(str(payload[name]), name)
        if payload["data_scope"] != "train_valid":
            raise ValueError("Decision scorecard evidence must be train/valid only")
        if payload["decision_grade"] is not False:
            raise ValueError("unverified scorecard evidence cannot be decision grade")
        if payload["authority_status"] != (
            "computed_but_pit_and_mask_provenance_unverified"
        ):
            raise ValueError("Decision scorecard authority status is invalid")
        if not isinstance(payload["computed_scorecard"], Mapping) or not isinstance(
            payload["factor_output_manifest"],
            Mapping,
        ):
            raise ValueError("Decision scorecard computed content is invalid")
        scorecard = _plain(payload["computed_scorecard"])
        factor_output = _plain(payload["factor_output_manifest"])
        if canonical_json_hash(scorecard) != payload["scorecard_hash"]:
            raise ValueError("Decision scorecard hash differs from computed content")
        if canonical_json_hash(factor_output) != payload["factor_output_content_hash"]:
            raise ValueError("Decision factor-output hash differs from manifest")
        if (
            scorecard.get("schema_version") != "alpha_quality_scorecard.v2"
            or scorecard.get("factor_spec_id") != self.factor_spec_id
            or scorecard.get("decision_grade") is not False
            or scorecard.get("authority_status")
            != "fixture_only_not_decision_evidence"
            or scorecard.get("execution_evidence_status")
            != "separate_producer_required"
            or factor_output.get("factor_spec_id") != self.factor_spec_id
            or factor_output.get("decision_grade") is not False
            or factor_output.get("storage_status")
            != "fixture_only_partition_artifact_unavailable"
            or scorecard.get("snapshot_hash")
            != factor_output.get("snapshot_hash")
            or scorecard.get("split_plan_hash")
            != factor_output.get("split_plan_hash")
            or scorecard.get("evaluation_time_policy_hash")
            != factor_output.get("evaluation_time_policy_hash")
            or scorecard.get("factor_output_content_hash")
            != payload["factor_output_content_hash"]
        ):
            raise ValueError("Decision scorecard computed identity is inconsistent")
        predictive = scorecard.get("predictive")
        if not isinstance(predictive, list) or not predictive or any(
            not isinstance(item, Mapping)
            or item.get("split") not in {"train", "valid"}
            for item in predictive
        ):
            raise ValueError("Decision scorecard contains a non-discovery split")
        caps = tuple(str(item) for item in payload["caps"])
        if caps != tuple(sorted(set(caps))) or not caps:
            raise ValueError("Decision scorecard caps must be sorted and non-empty")
        payload["caps"] = caps

    @classmethod
    def _from_artifact_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "DecisionEvidenceRecordV3":
        if set(value) != _RECORD_KEYS:
            raise ValueError("Decision evidence v3 artifact is not closed")
        if not isinstance(value["evidence_payload"], Mapping):
            raise ValueError("Decision evidence v3 payload must be an object")
        return cls(
            schema_version=cast(
                Literal["decision_evidence_record.v3"],
                value["schema_version"],
            ),
            evidence_kind=cast(
                Literal["ledger", "scorecard"],
                value["evidence_kind"],
            ),
            factor_spec_id=str(value["factor_spec_id"]),
            evidence_run_id=str(value["evidence_run_id"]),
            producer_schema_version=cast(
                Literal[
                    "decision_ledger_evidence_service.v3",
                    "decision_scorecard_evidence_service.v3",
                ],
                value["producer_schema_version"],
            ),
            producer_policy_hash=str(value["producer_policy_hash"]),
            source_event_hashes=tuple(str(item) for item in value["source_event_hashes"]),
            source_artifact_hashes=tuple(
                str(item) for item in value["source_artifact_hashes"]
            ),
            evidence_payload=dict(value["evidence_payload"]),
            evidence_hash=str(value["evidence_hash"]),
            _authority=_RECORD_MINT_AUTHORITY,
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evidence_kind": self.evidence_kind,
            "factor_spec_id": self.factor_spec_id,
            "evidence_run_id": self.evidence_run_id,
            "producer_schema_version": self.producer_schema_version,
            "producer_policy_hash": self.producer_policy_hash,
            "source_event_hashes": list(self.source_event_hashes),
            "source_artifact_hashes": list(self.source_artifact_hashes),
            "evidence_payload": _plain(self.evidence_payload),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "evidence_hash": self.evidence_hash}


def _mint_ledger_record(
    *,
    factor_spec_id: str,
    evidence_run_id: str,
    source_event_hashes: tuple[str, ...],
    source_artifact_hashes: tuple[str, ...],
    evidence_payload: Mapping[str, Any],
) -> DecisionEvidenceRecordV3:
    content = {
        "schema_version": "decision_evidence_record.v3",
        "evidence_kind": "ledger",
        "factor_spec_id": factor_spec_id,
        "evidence_run_id": evidence_run_id,
        "producer_schema_version": LEDGER_PRODUCER_SCHEMA,
        "producer_policy_hash": LEDGER_PRODUCER_POLICY_HASH,
        "source_event_hashes": list(source_event_hashes),
        "source_artifact_hashes": list(source_artifact_hashes),
        "evidence_payload": _plain(evidence_payload),
    }
    return DecisionEvidenceRecordV3(
        schema_version="decision_evidence_record.v3",
        evidence_kind="ledger",
        factor_spec_id=factor_spec_id,
        evidence_run_id=evidence_run_id,
        producer_schema_version=LEDGER_PRODUCER_SCHEMA,
        producer_policy_hash=LEDGER_PRODUCER_POLICY_HASH,
        source_event_hashes=source_event_hashes,
        source_artifact_hashes=source_artifact_hashes,
        evidence_payload=evidence_payload,
        evidence_hash=canonical_json_hash(content),
        _authority=_RECORD_MINT_AUTHORITY,
    )


def _mint_scorecard_record(
    *,
    factor_spec_id: str,
    evidence_run_id: str,
    source_event_hashes: tuple[str, ...],
    source_artifact_hashes: tuple[str, ...],
    evidence_payload: Mapping[str, Any],
) -> DecisionEvidenceRecordV3:
    content = {
        "schema_version": "decision_evidence_record.v3",
        "evidence_kind": "scorecard",
        "factor_spec_id": factor_spec_id,
        "evidence_run_id": evidence_run_id,
        "producer_schema_version": SCORECARD_PRODUCER_SCHEMA,
        "producer_policy_hash": SCORECARD_PRODUCER_POLICY_HASH,
        "source_event_hashes": list(source_event_hashes),
        "source_artifact_hashes": list(source_artifact_hashes),
        "evidence_payload": _plain(evidence_payload),
    }
    return DecisionEvidenceRecordV3(
        schema_version="decision_evidence_record.v3",
        evidence_kind="scorecard",
        factor_spec_id=factor_spec_id,
        evidence_run_id=evidence_run_id,
        producer_schema_version=SCORECARD_PRODUCER_SCHEMA,
        producer_policy_hash=SCORECARD_PRODUCER_POLICY_HASH,
        source_event_hashes=source_event_hashes,
        source_artifact_hashes=source_artifact_hashes,
        evidence_payload=evidence_payload,
        evidence_hash=canonical_json_hash(content),
        _authority=_RECORD_MINT_AUTHORITY,
    )


class DecisionEvidenceArtifactStoreV3:
    namespace = "decision-evidence-v3"

    def __init__(self, root: str | Path) -> None:
        self.writer = AtomicContentAddressedArtifactWriter(root, max_bytes=2 * 1024 * 1024)

    def write(self, record: DecisionEvidenceRecordV3) -> ContentAddressedArtifact:
        return self.writer.write_json(
            namespace=self.namespace,
            payload=record.to_dict(),
            schema_version="decision_evidence_record.v3",
            semantic_hash_field="evidence_hash",
            closed_keys=_RECORD_KEYS,
            media_type=LEDGER_EVIDENCE_MEDIA_TYPE,
        )

    def read(
        self,
        relative_path: str,
        *,
        expected_evidence_hash: str,
        expected_blob_hash: str,
    ) -> DecisionEvidenceRecordV3:
        reference = {
            "relative_path": relative_path,
            "artifact_hash": expected_blob_hash,
            "media_type": LEDGER_EVIDENCE_MEDIA_TYPE,
        }
        normalized = validate_artifact_references(self.writer.root, [reference])[0]
        digest = expected_evidence_hash.removeprefix("sha256:")
        expected_relative_path = (
            f"{self.namespace}/{digest[:2]}/{digest}.json"
        )
        if normalized["relative_path"] != expected_relative_path:
            raise ValueError("Decision evidence artifact path is not content addressed")
        target = self.writer.root.joinpath(*normalized["relative_path"].split("/"))

        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite Decision evidence JSON: {value}")

        def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, item in items:
                if key in result:
                    raise ValueError("duplicate Decision evidence artifact key")
                result[key] = item
            return result

        raw = json.loads(
            target.read_text(encoding="utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
        if not isinstance(raw, Mapping):
            raise ValueError("Decision evidence artifact must be an object")
        record = DecisionEvidenceRecordV3._from_artifact_dict(raw)
        if record.evidence_hash != expected_evidence_hash:
            raise ValueError("Decision evidence artifact identity differs")
        return record


@dataclass(frozen=True)
class RecordedDecisionEvidenceV3:
    record: DecisionEvidenceRecordV3
    event: ResearchEventEnvelope
    artifact: ContentAddressedArtifact


class DecisionLedgerEvidenceServiceV3:
    def __init__(self, store: Any) -> None:
        from src.research_ledger.events.store import ResearchEventStore

        if not isinstance(store, ResearchEventStore):
            raise TypeError("Decision ledger evidence requires ResearchEventStore")
        if not store.flags.enabled("VIBE_TRADING_DECISION_V2"):
            raise RuntimeError("Decision evidence capability is disabled")
        self.store = store
        self.artifacts = DecisionEvidenceArtifactStoreV3(store.artifact_root)

    def record(
        self,
        *,
        factor_spec_id: str,
        run_id: str,
    ) -> RecordedDecisionEvidenceV3:
        existing = [
            event
            for event in self.store.query_events(event_type="DecisionEvidenceV3Recorded")
            if event.payload["evidence_kind"] == "ledger"
            and event.payload["factor_spec_id"] == factor_spec_id
            and event.run_id == run_id
        ]
        if len(existing) > 1:
            raise EventTransitionError("multiple ledger evidence events exist")
        if existing:
            event = existing[0]
            chain = self.store.query_events()
            event_index = next(
                index
                for index, item in enumerate(chain)
                if item.event_hash == event.event_hash
            )
            if any(
                item.run_id == run_id
                and item.event_type in _MUTABLE_LEDGER_SOURCE_EVENT_TYPES
                for item in chain[event_index + 1 :]
            ):
                raise EventTransitionError(
                    "Decision ledger evidence run changed after its frozen watermark"
                )
            reference = event.payload["artifact_refs"][0]
            record = self.artifacts.read(
                str(reference["relative_path"]),
                expected_evidence_hash=str(event.payload["evidence_hash"]),
                expected_blob_hash=str(reference["artifact_hash"]),
            )
            rebuilt = self.rebuild_record(
                self.store,
                factor_spec_id=factor_spec_id,
                run_id=run_id,
                ledger_watermark_event_hash=str(
                    record.evidence_payload["ledger_watermark_event_hash"]
                ),
                expected_evaluation_event_hash=str(
                    record.evidence_payload["evaluation_event_hash"]
                ),
                expected_terminal_event_hash=str(
                    record.evidence_payload["terminal_event_hash"]
                ),
            )
            if rebuilt != record:
                raise EventValidationError(
                    "existing Decision evidence differs from source replay"
                )
            return RecordedDecisionEvidenceV3(
                record=record,
                event=event,
                artifact=ContentAddressedArtifact(
                    semantic_hash=record.evidence_hash,
                    relative_path=str(reference["relative_path"]),
                    blob_hash=str(reference["artifact_hash"]),
                    media_type=str(reference["media_type"]),
                ),
            )
        events = self.store.query_events()
        if not events:
            raise EventTransitionError("ledger evidence requires a non-empty event chain")
        watermark = events[-1].event_hash
        record = self.rebuild_record(
            self.store,
            factor_spec_id=factor_spec_id,
            run_id=run_id,
            ledger_watermark_event_hash=watermark,
        )
        artifact = self.artifacts.write(record)
        digest = record.evidence_hash.removeprefix("sha256:")
        evidence_id = "decision-evidence-v3-" + digest[:24]
        payload = self.event_payload(evidence_id, record, artifact.reference())
        event = self.store._append_producer_event(
            EventDraft(
                event_type="DecisionEvidenceV3Recorded",
                entity_id=evidence_id,
                run_id=run_id,
                payload_schema_version="decision_evidence_recorded.v3",
                idempotency_key="decision-evidence-v3:" + record.evidence_hash,
                payload=payload,
            )
        )
        return RecordedDecisionEvidenceV3(record=record, event=event, artifact=artifact)

    @staticmethod
    def event_payload(
        evidence_id: str,
        record: DecisionEvidenceRecordV3,
        artifact_reference: Mapping[str, str],
    ) -> dict[str, Any]:
        payload = record.evidence_payload
        return {
            "evidence_id": evidence_id,
            "evidence_hash": record.evidence_hash,
            "evidence_kind": record.evidence_kind,
            "factor_spec_id": record.factor_spec_id,
            "evidence_run_id": record.evidence_run_id,
            "producer_schema_version": record.producer_schema_version,
            "producer_policy_hash": record.producer_policy_hash,
            "source_event_hashes": list(record.source_event_hashes),
            "source_artifact_hashes": list(record.source_artifact_hashes),
            "evidence_payload_hash": canonical_json_hash(_plain(payload)),
            "factor_definition_event_hash": payload["factor_definition_event_hash"],
            "evaluation_event_hash": payload["evaluation_event_hash"],
            "terminal_event_hash": payload["terminal_event_hash"],
            "ledger_watermark_event_hash": payload["ledger_watermark_event_hash"],
            "artifact_refs": [dict(artifact_reference)],
        }

    @staticmethod
    def rebuild_record(
        store: Any,
        *,
        factor_spec_id: str,
        run_id: str,
        ledger_watermark_event_hash: str,
        expected_evaluation_event_hash: str | None = None,
        expected_terminal_event_hash: str | None = None,
    ) -> DecisionEvidenceRecordV3:
        events = store.query_events()
        indexes = {event.event_hash: index for index, event in enumerate(events)}
        watermark_index = indexes.get(ledger_watermark_event_hash)
        if watermark_index is None:
            raise EventValidationError("ledger evidence watermark is unknown")
        prefix = events[: watermark_index + 1]
        definitions = [
            event
            for event in prefix
            if event.event_type == "FactorDefinitionRecorded"
            and event.entity_id == factor_spec_id
        ]
        if len(definitions) != 1:
            raise EventValidationError("ledger evidence requires one factor definition")
        definition = definitions[0]
        evaluations = {
            event.event_hash: event
            for event in prefix
            if event.event_type == "EvaluationRecorded"
            and event.run_id == run_id
            and event.payload["factor_spec_id"] == factor_spec_id
            and event.payload["data_scope"] in {"valid", "train_valid"}
        }
        eligible_pairs = [
            (evaluations[str(event.payload["evaluation_event_hash"])], event)
            for event in prefix
            if event.event_type == "TrialTerminated"
            and event.run_id == run_id
            and event.payload["status"] in {"success", "reject"}
            and event.payload["evaluation_event_hash"] in evaluations
            and event.payload["trial_id"]
            == evaluations[str(event.payload["evaluation_event_hash"])].payload["trial_id"]
        ]
        if len(eligible_pairs) != 1:
            raise EventValidationError(
                "ledger evidence requires exactly one eligible factor terminal"
            )
        evaluation, terminal = eligible_pairs[0]
        if (
            expected_evaluation_event_hash is not None
            and evaluation.event_hash != expected_evaluation_event_hash
        ) or (
            expected_terminal_event_hash is not None
            and terminal.event_hash != expected_terminal_event_hash
        ):
            raise EventValidationError(
                "ledger evidence artifact selected a non-authoritative terminal"
            )
        validate_artifact_references(
            store.artifact_root,
            list(evaluation.payload["artifact_refs"]),
        )
        started = {
            str(event.payload["trial_id"])
            for event in prefix
            if event.event_type == "TrialStarted" and event.run_id == run_id
        }
        terminated = {
            str(event.payload["trial_id"])
            for event in prefix
            if event.event_type == "TrialTerminated" and event.run_id == run_id
        }
        infrastructure = tuple(
            sorted(
                event.event_hash
                for event in prefix
                if event.run_id == run_id
                and (
                    (
                        event.event_type == "TrialTerminated"
                        and event.payload["status"] == "infrastructure_failure"
                    )
                    or (
                        event.event_type == "GenerationFailureRecorded"
                        and event.payload["failure_kind"] == "infrastructure_failure"
                    )
                )
            )
        )
        artifact_hashes = tuple(
            sorted(
                {
                    str(reference["artifact_hash"])
                    for reference in evaluation.payload["artifact_refs"]
                }
            )
        )
        source_hashes = tuple(
            sorted(
                {
                    definition.event_hash,
                    evaluation.event_hash,
                    terminal.event_hash,
                    ledger_watermark_event_hash,
                    *infrastructure,
                }
            )
        )
        evidence_payload = {
            "factor_definition_event_hash": definition.event_hash,
            "evaluation_event_hash": evaluation.event_hash,
            "terminal_event_hash": terminal.event_hash,
            "ledger_watermark_event_hash": ledger_watermark_event_hash,
            "data_scope": str(evaluation.payload["data_scope"]),
            "complete": started == terminated,
            "terminal_train_valid": True,
            "reduced_durability": any(
                "REDUCED_DURABILITY" in event.warnings
                for event in (definition, evaluation, terminal)
            ),
            "infrastructure_failure_event_hashes": infrastructure,
        }
        return _mint_ledger_record(
            factor_spec_id=factor_spec_id,
            evidence_run_id=run_id,
            source_event_hashes=source_hashes,
            source_artifact_hashes=artifact_hashes,
            evidence_payload=evidence_payload,
        )


__all__ = [
    "DecisionEvidenceArtifactStoreV3",
    "DecisionEvidenceRecordV3",
    "DecisionLedgerEvidenceServiceV3",
    "LEDGER_EVIDENCE_MEDIA_TYPE",
    "LEDGER_PRODUCER_POLICY_HASH",
    "LEDGER_PRODUCER_SCHEMA",
    "SCORECARD_PRODUCER_POLICY_HASH",
    "SCORECARD_PRODUCER_SCHEMA",
    "SCORECARD_IDENTITY_TRANSFORM_PIPELINE_HASH",
    "SCORECARD_TRADABILITY_MASK_POLICY_HASH",
    "SCORECARD_UNIVERSE_MASK_POLICY_HASH",
    "RecordedDecisionEvidenceV3",
]
