"""Deterministic scorecard evidence minted only from registered source events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from src.alpha_foundry.dsl.executable import ExecutableGrammarSnapshotV1
from src.alpha_foundry.dsl.identity import validate_factor_definition_payload
from src.alpha_foundry.retrieval.feature_source_v1 import (
    FrozenTrainValidSnapshotArtifactStoreV1,
)
from src.alpha_quality.decision_v2.evidence_v3 import (
    DecisionEvidenceArtifactStoreV3,
    DecisionEvidenceRecordV3,
    RecordedDecisionEvidenceV3,
    SCORECARD_PRODUCER_POLICY_HASH,
    SCORECARD_PRODUCER_SCHEMA,
    SCORECARD_IDENTITY_TRANSFORM_PIPELINE_HASH,
    SCORECARD_TRADABILITY_MASK_POLICY_HASH,
    SCORECARD_UNIVERSE_MASK_POLICY_HASH,
    _mint_scorecard_record,
)
from src.alpha_quality.evaluation_registry_v1 import (
    EvaluationPolicyArtifactStoreV1,
)
from src.alpha_quality.factor_output_v2 import FrozenFactorOutputV2
from src.alpha_quality.scorecard_v2 import (
    FixtureTrainValidDataCapabilityV1,
    compute_fixture_scorecard_v2,
)
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


SCORECARD_EVIDENCE_EVENT_TYPE = "ScorecardDecisionEvidenceV3Recorded"
_BASE_CAPS = frozenset(
    {
        "CALENDAR_PROVIDER_AUTHORITY_UNVERIFIED",
        "PIT_SNAPSHOT_PROVENANCE_UNVERIFIED",
        "PRODUCER_BOUND_COMPLEMENT_EVIDENCE_REQUIRED",
        "PRODUCER_BOUND_EXECUTION_EVIDENCE_REQUIRED",
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
            f"scorecard evidence requires one {expected_type} source"
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
        raise EventValidationError("scorecard evidence source artifact is ambiguous")
    return matches[0]


def _strict_frame_axes(
    frame: pd.DataFrame,
    *,
    dates: tuple[str, ...],
    symbols: tuple[str, ...],
    name: str,
) -> pd.DataFrame:
    expected_index = pd.DatetimeIndex(dates)
    if (
        not isinstance(frame.index, pd.DatetimeIndex)
        or frame.index.tz is not None
        or not frame.index.equals(expected_index)
        or tuple(frame.columns) != symbols
    ):
        raise EventValidationError(f"scorecard {name} axes differ from policy")
    return frame


def _mask_from_panel(
    panel: Mapping[str, Any],
    name: str,
    *,
    dates: tuple[str, ...],
    symbols: tuple[str, ...],
) -> pd.DataFrame | None:
    raw = panel.get(name)
    if raw is None:
        return None
    if not isinstance(raw, pd.DataFrame):
        raise EventValidationError(f"scorecard {name} source is not a frame")
    expected_index = pd.DatetimeIndex(dates)
    if (
        len(expected_index.difference(raw.index)) > 0
        or tuple(raw.columns) != symbols
    ):
        raise EventValidationError(f"scorecard {name} lacks policy axes")
    sliced = raw.loc[pd.DatetimeIndex(dates), list(symbols)]
    _strict_frame_axes(sliced, dates=dates, symbols=symbols, name=name)
    values = sliced.astype(float)
    if np.isinf(values.to_numpy()).any():
        raise EventValidationError(f"scorecard {name} contains Infinity")
    return values.notna() & values.ne(0.0)


@dataclass(frozen=True)
class RebuiltScorecardEvidenceV3:
    record: DecisionEvidenceRecordV3
    factor_definition_event: ResearchEventEnvelope
    policy_event: ResearchEventEnvelope
    snapshot_event: ResearchEventEnvelope


class DecisionScorecardEvidenceServiceV3:
    def __init__(self, store: Any) -> None:
        from src.research_ledger.events.store import ResearchEventStore

        if not isinstance(store, ResearchEventStore):
            raise TypeError("Decision scorecard evidence requires ResearchEventStore")
        required = (
            "VIBE_TRADING_ALPHA_FOUNDRY",
            "VIBE_TRADING_ALPHA_SCORECARD",
            "VIBE_TRADING_RESEARCH_EVENTS",
            "VIBE_TRADING_FACTOR_DAG",
            "VIBE_TRADING_DECISION_V2",
        )
        if any(not store.flags.enabled(name) for name in required):
            raise RuntimeError("Decision scorecard evidence capability is disabled")
        self.store = store
        self.artifacts = DecisionEvidenceArtifactStoreV3(store.artifact_root)

    def record(
        self,
        *,
        factor_spec_id: str,
        evaluation_policy_event_hash: str,
        snapshot_event_hash: str,
        trial_id: str,
        run_id: str,
    ) -> RecordedDecisionEvidenceV3:
        if not all(
            isinstance(value, str) and value
            for value in (factor_spec_id, trial_id, run_id)
        ):
            raise ValueError("scorecard evidence identities are required")
        existing = [
            event
            for event in self.store.query_events(event_type=SCORECARD_EVIDENCE_EVENT_TYPE)
            if event.run_id == run_id
            and event.payload["factor_spec_id"] == factor_spec_id
            and event.payload["trial_id"] == trial_id
        ]
        if len(existing) > 1:
            raise EventTransitionError("multiple scorecard evidence events exist")
        if existing:
            event = existing[0]
            if (
                event.payload["evaluation_policy_event_hash"]
                != evaluation_policy_event_hash
                or event.payload["snapshot_event_hash"] != snapshot_event_hash
            ):
                raise EventTransitionError("scorecard evidence source is already frozen")
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
                trial_id=trial_id,
                run_id=run_id,
                source_watermark_event_hash=str(
                    record.evidence_payload["source_watermark_event_hash"]
                ),
            ).record
            if rebuilt_record != record:
                raise EventValidationError(
                    "existing scorecard evidence differs from deterministic replay"
                )
            expected_id = self.evidence_id(record.evidence_hash)
            expected_payload = self.event_payload(
                expected_id,
                trial_id,
                record,
                reference,
            )
            if (
                event.entity_id != expected_id
                or canonical_json(event.to_dict()["payload"])
                != canonical_json(expected_payload)
            ):
                raise EventValidationError(
                    "existing scorecard evidence event differs from replay"
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
            raise EventTransitionError("scorecard evidence requires source events")
        watermark = events[-1].event_hash
        rebuilt_sources = self.rebuild_record(
            self.store,
            factor_spec_id=factor_spec_id,
            evaluation_policy_event_hash=evaluation_policy_event_hash,
            snapshot_event_hash=snapshot_event_hash,
            trial_id=trial_id,
            run_id=run_id,
            source_watermark_event_hash=watermark,
        )
        record = rebuilt_sources.record
        artifact = self.artifacts.write(record)
        evidence_id = self.evidence_id(record.evidence_hash)
        event = self.store._append_producer_event(
            EventDraft(
                event_type=SCORECARD_EVIDENCE_EVENT_TYPE,
                entity_id=evidence_id,
                run_id=run_id,
                payload_schema_version="scorecard_decision_evidence_recorded.v3",
                idempotency_key="scorecard-decision-evidence-v3:" + record.evidence_hash,
                payload=self.event_payload(
                    evidence_id,
                    trial_id,
                    record,
                    artifact.reference(),
                ),
            )
        )
        return RecordedDecisionEvidenceV3(record=record, event=event, artifact=artifact)

    @staticmethod
    def evidence_id(evidence_hash: str) -> str:
        return "scorecard-decision-evidence-v3-" + evidence_hash.removeprefix(
            "sha256:"
        )[:24]

    @staticmethod
    def event_payload(
        evidence_id: str,
        trial_id: str,
        record: DecisionEvidenceRecordV3,
        artifact_reference: Mapping[str, str],
    ) -> dict[str, Any]:
        payload = record.evidence_payload
        return {
            "evidence_id": evidence_id,
            "evidence_hash": record.evidence_hash,
            "evidence_kind": "scorecard",
            "factor_spec_id": record.factor_spec_id,
            "evidence_run_id": record.evidence_run_id,
            "trial_id": trial_id,
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
            "scorecard_hash": payload["scorecard_hash"],
            "factor_output_content_hash": payload["factor_output_content_hash"],
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
        trial_id: str,
        run_id: str,
        source_watermark_event_hash: str,
    ) -> RebuiltScorecardEvidenceV3:
        events = store.query_events()
        indexes = {event.event_hash: index for index, event in enumerate(events)}
        watermark_index = indexes.get(source_watermark_event_hash)
        if watermark_index is None:
            raise EventValidationError("scorecard evidence watermark is unknown")
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
            raise EventValidationError("scorecard evidence requires one factor definition")
        definition = definitions[0]
        starts = [
            event
            for event in prefix
            if event.event_type == "TrialStarted"
            and event.entity_id == trial_id
            and event.run_id == run_id
        ]
        if (
            len(starts) != 1
            or definition.run_id != run_id
            or policy_event.run_id != run_id
            or definition.payload["metadata"]["originating_trial_id"] != trial_id
        ):
            raise EventValidationError("scorecard evidence trial/run binding differs")
        if any(
            event.event_type == "TrialTerminated"
            and event.entity_id == trial_id
            for event in prefix
        ):
            raise EventValidationError("scorecard evidence must precede trial terminal")
        validate_factor_definition_payload(definition.payload)

        policy_reference = _single_artifact_reference(
            policy_event,
            media_type=(
                "application/vnd.vibe.registered-evaluation-policy-v1+json"
            ),
        )
        snapshot_reference = _single_artifact_reference(
            snapshot_event,
            media_type=(
                "application/vnd.vibe.frozen-train-valid-snapshot-v1+json"
            ),
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
        calendar, time_policy, split_plan = policy_bundle.resolved_components()
        snapshot = FrozenTrainValidSnapshotArtifactStoreV1(
            store.artifact_root
        ).read(
            str(snapshot_reference["relative_path"]),
            str(snapshot_event.payload["snapshot_hash"]),
        )
        panel = snapshot.to_panel()
        close = panel.get("close")
        if not isinstance(close, pd.DataFrame):
            raise EventValidationError("scorecard source snapshot has no close frame")
        symbols = tuple(str(item) for item in close.columns)
        if symbols != tuple(sorted(set(symbols))) or not symbols:
            raise EventValidationError("scorecard source symbols are not canonical")
        prevalid_dates = calendar.dates[
            calendar.dates.index(split_plan.train.start) :
            calendar.dates.index(split_plan.valid.end) + 1
        ]
        expected_prevalid_index = pd.DatetimeIndex(prevalid_dates)
        formula_panel: dict[str, pd.DataFrame] = {}
        for name, value in panel.items():
            if not isinstance(value, pd.DataFrame):
                continue
            missing = expected_prevalid_index.difference(value.index)
            if len(missing) > 0 or tuple(value.columns) != symbols:
                raise EventValidationError(
                    "scorecard source panel lacks exact pre-valid axes"
                )
            formula_panel[name] = value.loc[expected_prevalid_index, list(symbols)]
        metadata = definition.payload["metadata"]
        formula = str(metadata["canonical_formula"])
        executable = ExecutableGrammarSnapshotV1.from_runtime()
        if (
            definition.payload["grammar_version"] != executable.source_grammar_version
            or definition.payload["grammar_hash"] != executable.source_grammar_hash
        ):
            raise EventValidationError("factor grammar has no registered backend")
        try:
            factor_prevalid = executable.evaluate(formula, formula_panel).astype(float)
        except (KeyError, TypeError, ValueError) as exc:
            raise EventValidationError("factor cannot be executed from snapshot") from exc
        _strict_frame_axes(
            factor_prevalid,
            dates=prevalid_dates,
            symbols=symbols,
            name="factor output",
        )
        discovery_dates = (
            calendar.dates[
                calendar.dates.index(split_plan.train.start) :
                calendar.dates.index(split_plan.train.end) + 1
            ]
            + calendar.dates[
                calendar.dates.index(split_plan.valid.start) :
                calendar.dates.index(split_plan.valid.end) + 1
            ]
        )
        discovery_index = pd.DatetimeIndex(discovery_dates)
        close_discovery = close.loc[discovery_index, list(symbols)].astype(float)
        _strict_frame_axes(
            close_discovery,
            dates=discovery_dates,
            symbols=symbols,
            name="close",
        )
        factor = factor_prevalid.loc[discovery_index, list(symbols)].replace(
            [np.inf, -np.inf],
            np.nan,
        )
        finite_factor = pd.DataFrame(
            np.isfinite(factor.to_numpy()),
            index=discovery_index,
            columns=symbols,
        )
        finite_close = pd.DataFrame(
            np.isfinite(close_discovery.to_numpy()),
            index=discovery_index,
            columns=symbols,
        )
        provided_valid = _mask_from_panel(
            panel,
            "valid_mask",
            dates=discovery_dates,
            symbols=symbols,
        )
        universe = _mask_from_panel(
            panel,
            "universe_mask",
            dates=discovery_dates,
            symbols=symbols,
        )
        tradable = _mask_from_panel(
            panel,
            "tradable_mask",
            dates=discovery_dates,
            symbols=symbols,
        )
        caps = set(_BASE_CAPS)
        if universe is None:
            caps.add("PIT_UNIVERSE_MASK_UNAVAILABLE")
            universe = pd.DataFrame(False, index=discovery_index, columns=symbols)
        if tradable is None:
            caps.add("TRADABILITY_MASK_UNAVAILABLE")
            tradable = pd.DataFrame(False, index=discovery_index, columns=symbols)
        valid = finite_factor & finite_close
        if provided_valid is not None:
            valid &= provided_valid
        semantics = metadata["semantics"]
        if (
            semantics["transform_pipeline_hash"]
            != SCORECARD_IDENTITY_TRANSFORM_PIPELINE_HASH
            or semantics["universe_mask_hash"]
            != SCORECARD_UNIVERSE_MASK_POLICY_HASH
            or semantics["tradability_mask_hash"]
            != SCORECARD_TRADABILITY_MASK_POLICY_HASH
            or int(semantics["execution_lag"])
            != time_policy.entry_lag_trading_days
            or int(semantics["return_horizon"])
            != time_policy.execution_horizon
            or semantics["signal_time"] != time_policy.signal_timestamp
        ):
            raise EventValidationError(
                "factor semantics are unsupported by scorecard producer policy"
            )
        factor_output = FrozenFactorOutputV2.build_fixture_content(
            factor_spec_id=factor_spec_id,
            canonical_formula=formula,
            snapshot_hash=snapshot.snapshot_hash,
            split_plan_hash=split_plan.plan_hash,
            evaluation_time_policy_hash=time_policy.policy_hash,
            executable_grammar_snapshot_hash=executable.snapshot_hash,
            expected_dates=discovery_dates,
            expected_symbols=symbols,
            factor=factor,
            valid_mask=valid.astype(bool),
            tradable_mask=tradable.astype(bool),
            universe_mask=universe.astype(bool),
            metadata={
                "backend_version": executable.backend_version,
                "field_semantics_hash": canonical_json_hash(
                    dict(semantics["field_semantics"])
                ),
                "transform_pipeline_hash": str(
                    semantics["transform_pipeline_hash"]
                ),
            },
        )
        capability = FixtureTrainValidDataCapabilityV1.from_fixture(
            snapshot_hash=snapshot.snapshot_hash,
            calendar=calendar,
            split_plan=split_plan,
            time_policy=time_policy,
            close=close_discovery,
            symbols=symbols,
        )
        scorecard = compute_fixture_scorecard_v2(
            factor_output=factor_output,
            capability=capability,
            minimum_cross_section=2,
        )
        scorecard_content = scorecard.to_dict()
        factor_manifest = factor_output.to_manifest_dict()
        evidence_payload = {
            "factor_definition_event_hash": definition.event_hash,
            "evaluation_policy_event_hash": policy_event.event_hash,
            "snapshot_event_hash": snapshot_event.event_hash,
            "source_watermark_event_hash": source_watermark_event_hash,
            "data_scope": "train_valid",
            "scorecard_hash": scorecard.scorecard_hash,
            "factor_output_content_hash": factor_output.content_hash,
            "computed_scorecard": scorecard_content,
            "factor_output_manifest": factor_manifest,
            "authority_status": (
                "computed_but_pit_and_mask_provenance_unverified"
            ),
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
        return RebuiltScorecardEvidenceV3(
            record=_mint_scorecard_record(
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
    "DecisionScorecardEvidenceServiceV3",
    "RebuiltScorecardEvidenceV3",
    "SCORECARD_EVIDENCE_EVENT_TYPE",
]
