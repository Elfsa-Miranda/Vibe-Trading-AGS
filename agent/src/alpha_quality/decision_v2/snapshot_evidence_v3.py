"""Producer-scoped, source-replayed snapshot evidence for Decision v3."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd  # type: ignore[import-untyped]

from src.alpha_foundry.retrieval.feature_source_v1 import (
    FrozenTrainValidSnapshotArtifactStoreV1,
)
from src.alpha_quality.decision_v2.evidence_v3 import (
    DecisionEvidenceArtifactStoreV3,
    DecisionEvidenceRecordV3,
    RecordedDecisionEvidenceV3,
    _mint_snapshot_record,
)
from src.alpha_quality.evaluation_registry_v1 import EvaluationPolicyArtifactStoreV1
from src.research_ledger.events.artifacts import (
    ContentAddressedArtifact,
    validate_artifact_references,
)
from src.research_ledger.events.model import (
    EventDraft,
    EventTransitionError,
    EventValidationError,
    ResearchEventEnvelope,
)
from src.research_ledger.hash_utils import canonical_json, canonical_json_hash


SNAPSHOT_EVIDENCE_EVENT_TYPE = "SnapshotDecisionEvidenceV3Recorded"
_BASE_CAPS = frozenset(
    {
        "AVAILABILITY_TIME_EVIDENCE_UNAVAILABLE",
        "CALENDAR_PROVIDER_AUTHORITY_UNVERIFIED",
        "CORPORATE_ACTION_EVIDENCE_UNAVAILABLE",
        "LEGACY_CALLER_SNAPSHOT_PRODUCER",
        "PIT_SNAPSHOT_PROVENANCE_UNVERIFIED",
        "SURVIVORSHIP_STATUS_UNKNOWN",
    }
)


def _event_by_hash(
    events: list[ResearchEventEnvelope],
    event_hash: str,
    expected_type: str,
) -> ResearchEventEnvelope:
    matches = [
        event
        for event in events
        if event.event_hash == event_hash and event.event_type == expected_type
    ]
    if len(matches) != 1:
        raise EventValidationError(
            f"snapshot evidence requires one {expected_type} source"
        )
    return matches[0]


def _single_artifact_reference(
    event: ResearchEventEnvelope,
    *,
    media_type: str,
) -> Mapping[str, Any]:
    matches = [
        reference
        for reference in event.payload["artifact_refs"]
        if reference["media_type"] == media_type
    ]
    if len(matches) != 1:
        raise EventValidationError("snapshot evidence source artifact is ambiguous")
    return matches[0]


@dataclass(frozen=True)
class RebuiltSnapshotEvidenceV3:
    record: DecisionEvidenceRecordV3
    factor_definition_event: ResearchEventEnvelope
    policy_event: ResearchEventEnvelope
    snapshot_event: ResearchEventEnvelope


class DecisionSnapshotEvidenceServiceV3:
    """Rebuild legacy snapshot facts while refusing its caller-authored truth flags."""

    def __init__(self, store: Any) -> None:
        from src.research_ledger.events.store import ResearchEventStore

        if not isinstance(store, ResearchEventStore):
            raise TypeError("Decision snapshot evidence requires ResearchEventStore")
        required = (
            "VIBE_TRADING_ALPHA_FOUNDRY",
            "VIBE_TRADING_ALPHA_SCORECARD",
            "VIBE_TRADING_RESEARCH_EVENTS",
            "VIBE_TRADING_FACTOR_DAG",
            "VIBE_TRADING_DECISION_V2",
        )
        if any(not store.flags.enabled(name) for name in required):
            raise RuntimeError("Decision snapshot evidence capability is disabled")
        self.store = store
        self.artifacts = DecisionEvidenceArtifactStoreV3(store.artifact_root)

    def record(
        self,
        *,
        factor_spec_id: str,
        evaluation_policy_event_hash: str,
        snapshot_event_hash: str,
        run_id: str,
    ) -> RecordedDecisionEvidenceV3:
        if not all(
            isinstance(value, str) and value
            for value in (
                factor_spec_id,
                evaluation_policy_event_hash,
                snapshot_event_hash,
                run_id,
            )
        ):
            raise ValueError("snapshot evidence identities are required")
        existing = [
            event
            for event in self.store.query_events(event_type=SNAPSHOT_EVIDENCE_EVENT_TYPE)
            if event.run_id == run_id
            and event.payload["factor_spec_id"] == factor_spec_id
        ]
        if len(existing) > 1:
            raise EventTransitionError("multiple snapshot evidence events exist")
        if existing:
            event = existing[0]
            if (
                event.payload["evaluation_policy_event_hash"]
                != evaluation_policy_event_hash
                or event.payload["snapshot_event_hash"] != snapshot_event_hash
            ):
                raise EventTransitionError("snapshot evidence source is already frozen")
            reference = event.payload["artifact_refs"][0]
            record = self.artifacts.read(
                str(reference["relative_path"]),
                expected_evidence_hash=str(event.payload["evidence_hash"]),
                expected_blob_hash=str(reference["artifact_hash"]),
            )
            rebuilt_record = self.rebuild_record(
                self.store,
                factor_spec_id=factor_spec_id,
                evaluation_policy_event_hash=evaluation_policy_event_hash,
                snapshot_event_hash=snapshot_event_hash,
                run_id=run_id,
                source_watermark_event_hash=str(
                    record.evidence_payload["source_watermark_event_hash"]
                ),
            ).record
            expected_payload = self.event_payload(
                self.evidence_id(record.evidence_hash),
                record,
                reference,
            )
            if (
                rebuilt_record != record
                or event.entity_id != self.evidence_id(record.evidence_hash)
                or canonical_json(event.to_dict()["payload"])
                != canonical_json(expected_payload)
            ):
                raise EventValidationError(
                    "existing snapshot evidence differs from deterministic replay"
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
            raise EventTransitionError("snapshot evidence requires source events")
        watermark = events[-1].event_hash
        rebuilt_sources = self.rebuild_record(
            self.store,
            factor_spec_id=factor_spec_id,
            evaluation_policy_event_hash=evaluation_policy_event_hash,
            snapshot_event_hash=snapshot_event_hash,
            run_id=run_id,
            source_watermark_event_hash=watermark,
        )
        record = rebuilt_sources.record
        artifact = self.artifacts.write(record)
        evidence_id = self.evidence_id(record.evidence_hash)
        event = self.store._append_producer_event(
            EventDraft(
                event_type=SNAPSHOT_EVIDENCE_EVENT_TYPE,
                entity_id=evidence_id,
                run_id=run_id,
                payload_schema_version="snapshot_decision_evidence_recorded.v3",
                idempotency_key="snapshot-decision-evidence-v3:"
                + record.evidence_hash,
                payload=self.event_payload(
                    evidence_id,
                    record,
                    artifact.reference(),
                ),
            )
        )
        return RecordedDecisionEvidenceV3(record=record, event=event, artifact=artifact)

    @staticmethod
    def evidence_id(evidence_hash: str) -> str:
        return "snapshot-decision-evidence-v3-" + evidence_hash.removeprefix(
            "sha256:"
        )[:24]

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
            "evidence_kind": "snapshot",
            "factor_spec_id": record.factor_spec_id,
            "evidence_run_id": record.evidence_run_id,
            "producer_schema_version": record.producer_schema_version,
            "producer_policy_hash": record.producer_policy_hash,
            "source_event_hashes": list(record.source_event_hashes),
            "source_artifact_hashes": list(record.source_artifact_hashes),
            "evidence_payload_hash": canonical_json_hash(
                record.to_dict()["evidence_payload"]
            ),
            "factor_definition_event_hash": payload[
                "factor_definition_event_hash"
            ],
            "evaluation_policy_event_hash": payload[
                "evaluation_policy_event_hash"
            ],
            "snapshot_event_hash": payload["snapshot_event_hash"],
            "source_watermark_event_hash": payload["source_watermark_event_hash"],
            "snapshot_hash": payload["snapshot_hash"],
            "panel_content_hash": payload["panel_content_hash"],
            "cutoff_status": payload["cutoff_status"],
            "pit_authority_status": payload["pit_authority_status"],
            "survivorship_status": payload["survivorship_status"],
            "decision_grade": False,
            "caps": list(payload["caps"]),
            "artifact_refs": [dict(artifact_reference)],
        }

    @staticmethod
    def rebuild_record(
        store: Any,
        *,
        factor_spec_id: str,
        evaluation_policy_event_hash: str,
        snapshot_event_hash: str,
        run_id: str,
        source_watermark_event_hash: str,
    ) -> RebuiltSnapshotEvidenceV3:
        events = store.query_events()
        indexes = {event.event_hash: index for index, event in enumerate(events)}
        watermark_index = indexes.get(source_watermark_event_hash)
        if watermark_index is None:
            raise EventValidationError("snapshot evidence watermark is unknown")
        prefix = events[: watermark_index + 1]
        policy_event = _event_by_hash(
            prefix,
            evaluation_policy_event_hash,
            "EvaluationPolicyRegistered",
        )
        snapshot_event = _event_by_hash(
            prefix,
            snapshot_event_hash,
            "TrainValidDataSnapshotFrozen",
        )
        definitions = [
            event
            for event in prefix
            if event.event_type == "FactorDefinitionRecorded"
            and event.entity_id == factor_spec_id
        ]
        if len(definitions) != 1:
            raise EventValidationError("snapshot evidence requires one factor definition")
        definition = definitions[0]
        if definition.run_id != run_id or policy_event.run_id != run_id:
            raise EventValidationError("snapshot evidence factor/policy run differs")

        policy_reference = _single_artifact_reference(
            policy_event,
            media_type="application/vnd.vibe.registered-evaluation-policy-v1+json",
        )
        snapshot_reference = _single_artifact_reference(
            snapshot_event,
            media_type="application/vnd.vibe.frozen-train-valid-snapshot-v1+json",
        )
        validate_artifact_references(
            store.artifact_root,
            [policy_reference, snapshot_reference],
        )
        policy_bundle = EvaluationPolicyArtifactStoreV1(store.artifact_root).read(
            str(policy_reference["relative_path"]),
            expected_bundle_hash=str(policy_event.payload["bundle_hash"]),
            expected_blob_hash=str(policy_reference["artifact_hash"]),
        )
        calendar, _, split_plan = policy_bundle.resolved_components()
        snapshot = FrozenTrainValidSnapshotArtifactStoreV1(
            store.artifact_root
        ).read(
            str(snapshot_reference["relative_path"]),
            str(snapshot_event.payload["snapshot_hash"]),
        )
        contract = snapshot.snapshot_contract
        frame_hashes = contract["frame_content_hashes"]
        if not isinstance(frame_hashes, Mapping):
            raise EventValidationError("snapshot frame hashes are unavailable")
        inventory: dict[str, dict[str, Any]] = {}
        frame_date_sets: dict[str, set[str]] = {}
        frame_symbol_axes: dict[str, tuple[str, ...]] = {}
        all_dates: set[str] = set()
        for name, raw in snapshot.frames.items():
            dates = tuple(str(item) for item in raw["dates"])
            symbols = tuple(str(item) for item in raw["symbols"])
            if not dates or not symbols:
                raise EventValidationError("snapshot frame inventory is empty")
            canonical_dates = tuple(pd.Timestamp(item).date().isoformat() for item in dates)
            if canonical_dates != tuple(sorted(set(canonical_dates))):
                raise EventValidationError("snapshot frame dates are not canonical")
            if symbols != tuple(sorted(set(symbols))):
                raise EventValidationError("snapshot frame symbols are not canonical")
            all_dates.update(canonical_dates)
            frame_date_sets[str(name)] = set(canonical_dates)
            frame_symbol_axes[str(name)] = symbols
            inventory[str(name)] = {
                "date_count": len(canonical_dates),
                "symbol_count": len(symbols),
                "date_start": canonical_dates[0],
                "date_end": canonical_dates[-1],
            }
        observed_start = min(all_dates)
        observed_end = max(all_dates)
        valid_end = split_plan.valid.end
        caps = set(_BASE_CAPS)
        cutoff_status = "within_registered_valid_end"
        if observed_end > valid_end:
            cutoff_status = "contains_dates_after_registered_valid_end"
            caps.add("SNAPSHOT_SCOPE_CUTOFF_VIOLATION")
        calendar_dates = set(calendar.dates)
        if not all_dates.issubset(calendar_dates):
            caps.add("SNAPSHOT_CALENDAR_DATE_MISMATCH")
        train_valid_required = {
            date
            for date in calendar.dates
            if split_plan.train.start <= date <= split_plan.valid.end
        }
        if not train_valid_required.issubset(all_dates):
            caps.add("SNAPSHOT_REGISTERED_DATE_COVERAGE_INCOMPLETE")
        if any(
            not train_valid_required.issubset(frame_dates)
            for frame_dates in frame_date_sets.values()
        ):
            caps.add("SNAPSHOT_FRAME_COVERAGE_INCOMPLETE")
        if len(set(frame_symbol_axes.values())) != 1:
            caps.add("SNAPSHOT_FRAME_SYMBOL_AXES_INCONSISTENT")
        if "close" not in inventory:
            caps.add("CLOSE_FIELD_UNAVAILABLE")
        if "universe_mask" in inventory:
            membership_status = "present_but_unverified_caller_frame"
            caps.add("PIT_UNIVERSE_MASK_AUTHORITY_UNVERIFIED")
        else:
            membership_status = "unavailable"
            caps.add("PIT_UNIVERSE_MASK_UNAVAILABLE")
        if "tradable_mask" in inventory:
            tradability_status = "present_but_unverified_caller_frame"
            caps.add("TRADABILITY_MASK_AUTHORITY_UNVERIFIED")
        else:
            tradability_status = "unavailable"
            caps.add("TRADABILITY_MASK_UNAVAILABLE")
        evidence_payload = {
            "factor_definition_event_hash": definition.event_hash,
            "evaluation_policy_event_hash": policy_event.event_hash,
            "snapshot_event_hash": snapshot_event.event_hash,
            "source_watermark_event_hash": source_watermark_event_hash,
            "data_scope": "train_valid",
            "snapshot_hash": snapshot.snapshot_hash,
            "panel_content_hash": str(contract["panel_content_hash"]),
            "frame_content_hashes": {
                str(name): str(value)
                for name, value in sorted(frame_hashes.items())
            },
            "frame_inventory": inventory,
            "registered_valid_end": valid_end,
            "observed_date_start": observed_start,
            "observed_date_end": observed_end,
            "cutoff_status": cutoff_status,
            "pit_authority_status": "unverified_legacy_caller_snapshot",
            "survivorship_status": "unknown",
            "daily_membership_status": membership_status,
            "tradability_status": tradability_status,
            "availability_time_status": "unavailable",
            "corporate_action_status": "unavailable",
            "calendar_authority_status": "unverified_registered_date_content",
            "decision_grade": False,
            "caps": tuple(sorted(caps)),
        }
        source_events = tuple(
            sorted(
                {
                    definition.event_hash,
                    policy_event.event_hash,
                    snapshot_event.event_hash,
                    source_watermark_event_hash,
                }
            )
        )
        source_artifacts = tuple(
            sorted(
                {
                    str(policy_reference["artifact_hash"]),
                    str(snapshot_reference["artifact_hash"]),
                }
            )
        )
        return RebuiltSnapshotEvidenceV3(
            record=_mint_snapshot_record(
                factor_spec_id=factor_spec_id,
                evidence_run_id=run_id,
                source_event_hashes=source_events,
                source_artifact_hashes=source_artifacts,
                evidence_payload=evidence_payload,
            ),
            factor_definition_event=definition,
            policy_event=policy_event,
            snapshot_event=snapshot_event,
        )


__all__ = [
    "DecisionSnapshotEvidenceServiceV3",
    "RebuiltSnapshotEvidenceV3",
    "SNAPSHOT_EVIDENCE_EVENT_TYPE",
]
