"""Producer-scoped registry and snapshot services for A-share PIT evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import pandas as pd  # type: ignore[import-untyped]

from src.alpha_quality.evaluation_registry_v1 import EvaluationPolicyArtifactStoreV1
from src.alpha_quality.flags import ResolvedAGSFlags
from src.alpha_quality.pit_adapter_v1 import (
    AsharePITAdapterRegistryV1,
    AsharePITSnapshotRequestV1,
    RegisteredAsharePITAdapterV1,
    _production_registration_proof,
)
from src.alpha_quality.pit_artifact_v2 import (
    AsharePITTableReferenceV1,
    FrozenAsharePITSnapshotArtifactStoreV2,
    FrozenAsharePITSnapshotV2,
    PIT_MANIFEST_MEDIA_TYPE,
)
from src.alpha_quality.pit_snapshot_v2 import (
    ASHARE_PIT_REQUIRED_FIELDS,
    AsharePITValidationPolicyV1,
    validate_ashare_pit_source_v2,
)
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
from src.research_ledger.hash_utils import canonical_json, canonical_json_hash


PIT_ADAPTER_REGISTRATION_EVENT_TYPE = "AsharePITAdapterRegistered"
PIT_SNAPSHOT_EVENT_TYPE = "AsharePITSnapshotRecorded"
PIT_ADAPTER_REGISTRATION_MEDIA_TYPE = (
    "application/vnd.vibe.registered-ashare-pit-adapter-v1+json"
)
PIT_ADAPTER_REGISTRATION_PRODUCER_SCHEMA = (
    "ashare_pit_adapter_registration_service.v1"
)
PIT_ADAPTER_REGISTRATION_POLICY_HASH = canonical_json_hash(
    {
        "schema_version": "ashare_pit_adapter_registration_policy.v1",
        "registry_boundary": "frozen_at_app_construction.v1",
        "implementation_identity": "module_qualname_and_normalized_source.v1",
        "production_authority": "exact_builtin_type_allowlist.v1",
        "caller_authority_override": "forbidden",
    }
)
PIT_SNAPSHOT_PRODUCER_SCHEMA = "ashare_pit_snapshot_service.v2"
PIT_SNAPSHOT_PRODUCER_POLICY_HASH = canonical_json_hash(
    {
        "schema_version": "ashare_pit_snapshot_producer_policy.v2",
        "request_source": "registered_evaluation_policy_train_through_valid.v1",
        "required_fields": list(ASHARE_PIT_REQUIRED_FIELDS),
        "validation_policy_hash": AsharePITValidationPolicyV1().policy_hash,
        "adapter_input": "registered_adapter_id_only.v1",
        "storage": "content_addressed_parquet_partitions_and_manifest.v1",
        "replay": "revalidate_partitions_without_adapter_recall.v1",
    }
)
_REGISTRATION_ARTIFACT_KEYS = frozenset(
    {
        "schema_version",
        "producer_schema_version",
        "producer_policy_hash",
        "registry_hash",
        "registration",
        "artifact_hash",
    }
)


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True)
class AsharePITAdapterRegistrationArtifactV1:
    registry_hash: str
    registration: Mapping[str, Any]
    artifact_hash: str
    schema_version: str = "ashare_pit_adapter_registration_artifact.v1"
    producer_schema_version: str = PIT_ADAPTER_REGISTRATION_PRODUCER_SCHEMA
    producer_policy_hash: str = PIT_ADAPTER_REGISTRATION_POLICY_HASH

    def __post_init__(self) -> None:
        if (
            self.schema_version
            != "ashare_pit_adapter_registration_artifact.v1"
            or self.producer_schema_version
            != PIT_ADAPTER_REGISTRATION_PRODUCER_SCHEMA
            or self.producer_policy_hash
            != PIT_ADAPTER_REGISTRATION_POLICY_HASH
        ):
            raise ValueError("PIT adapter registration producer contract differs")
        for value, name in (
            (self.registry_hash, "registry_hash"),
            (self.artifact_hash, "artifact_hash"),
        ):
            if not value.startswith("sha256:") or len(value) != 71:
                raise ValueError(f"PIT adapter {name} is invalid")
        registration = _registration_from_dict(self.registration)
        object.__setattr__(
            self,
            "registration",
            MappingProxyType(registration.to_dict()),
        )
        if self.artifact_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("PIT adapter registration artifact hash differs")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "producer_schema_version": self.producer_schema_version,
            "producer_policy_hash": self.producer_policy_hash,
            "registry_hash": self.registry_hash,
            "registration": _plain(self.registration),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "artifact_hash": self.artifact_hash}

    @classmethod
    def build(
        cls,
        *,
        registry_hash: str,
        registration: RegisteredAsharePITAdapterV1,
    ) -> "AsharePITAdapterRegistrationArtifactV1":
        content = {
            "schema_version": "ashare_pit_adapter_registration_artifact.v1",
            "producer_schema_version": PIT_ADAPTER_REGISTRATION_PRODUCER_SCHEMA,
            "producer_policy_hash": PIT_ADAPTER_REGISTRATION_POLICY_HASH,
            "registry_hash": registry_hash,
            "registration": registration.to_dict(),
        }
        return cls(
            registry_hash=registry_hash,
            registration=registration.to_dict(),
            artifact_hash=canonical_json_hash(content),
        )

    @classmethod
    def from_dict(
        cls,
        raw: Mapping[str, Any],
    ) -> "AsharePITAdapterRegistrationArtifactV1":
        if set(raw) != _REGISTRATION_ARTIFACT_KEYS or not isinstance(
            raw["registration"],
            Mapping,
        ):
            raise ValueError("PIT adapter registration artifact is not closed")
        return cls(
            schema_version=str(raw["schema_version"]),
            producer_schema_version=str(raw["producer_schema_version"]),
            producer_policy_hash=str(raw["producer_policy_hash"]),
            registry_hash=str(raw["registry_hash"]),
            registration=dict(raw["registration"]),
            artifact_hash=str(raw["artifact_hash"]),
        )


def _registration_from_dict(raw: Mapping[str, Any]) -> RegisteredAsharePITAdapterV1:
    from src.alpha_quality.pit_artifact_v2 import _descriptor_from_dict

    expected = {
        "schema_version",
        "descriptor",
        "descriptor_hash",
        "implementation_hash",
        "factory_origin",
        "factory_hash",
        "provider_version",
        "authority_class",
        "registration_hash",
    }
    if set(raw) != expected or not isinstance(raw["descriptor"], Mapping):
        raise ValueError("PIT adapter registration is not closed")
    authority_class = str(raw["authority_class"])
    registration_hash = str(raw["registration_hash"])
    registration = RegisteredAsharePITAdapterV1(
        descriptor=_descriptor_from_dict(raw["descriptor"]),
        implementation_hash=str(raw["implementation_hash"]),
        factory_origin=str(raw["factory_origin"]),
        factory_hash=str(raw["factory_hash"]),
        provider_version=str(raw["provider_version"]),
        authority_class=authority_class,  # type: ignore[arg-type]
        registration_hash=registration_hash,
        schema_version=str(raw["schema_version"]),  # type: ignore[arg-type]
        _authority_proof=(
            _production_registration_proof(registration_hash)
            if authority_class == "built_in_production"
            else None
        ),
    )
    if registration.to_dict() != _plain(raw):
        raise ValueError("PIT adapter registration values are not canonical")
    return registration


class AsharePITAdapterRegistrationArtifactStoreV1:
    namespace = "registered-ashare-pit-adapter-v1"

    def __init__(self, root: str | Path) -> None:
        self.writer = AtomicContentAddressedArtifactWriter(root, max_bytes=2 * 1024**2)

    def write(
        self,
        artifact: AsharePITAdapterRegistrationArtifactV1,
    ) -> ContentAddressedArtifact:
        return self.writer.write_json(
            namespace=self.namespace,
            payload=artifact.to_dict(),
            schema_version="ashare_pit_adapter_registration_artifact.v1",
            semantic_hash_field="artifact_hash",
            closed_keys=_REGISTRATION_ARTIFACT_KEYS,
            media_type=PIT_ADAPTER_REGISTRATION_MEDIA_TYPE,
        )

    def read(
        self,
        relative_path: str,
        *,
        expected_artifact_hash: str,
        expected_blob_hash: str,
    ) -> AsharePITAdapterRegistrationArtifactV1:
        normalized = validate_artifact_references(
            self.writer.root,
            [
                {
                    "relative_path": relative_path,
                    "artifact_hash": expected_blob_hash,
                    "media_type": PIT_ADAPTER_REGISTRATION_MEDIA_TYPE,
                }
            ],
        )[0]
        digest = expected_artifact_hash.removeprefix("sha256:")
        expected_relative = f"{self.namespace}/{digest[:2]}/{digest}.json"
        if normalized["relative_path"] != expected_relative:
            raise ValueError("PIT adapter artifact path is not content addressed")
        target = self.writer.root.joinpath(*expected_relative.split("/"))
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError("PIT adapter artifact must be an object")
        artifact = AsharePITAdapterRegistrationArtifactV1.from_dict(raw)
        if artifact.artifact_hash != expected_artifact_hash:
            raise ValueError("PIT adapter artifact identity differs")
        return artifact


@dataclass(frozen=True)
class RecordedAsharePITAdapterRegistrationV1:
    artifact_record: AsharePITAdapterRegistrationArtifactV1
    artifact: ContentAddressedArtifact
    event: ResearchEventEnvelope


@dataclass(frozen=True)
class RecordedAsharePITSnapshotV2:
    snapshot: FrozenAsharePITSnapshotV2
    artifact: ContentAddressedArtifact
    event: ResearchEventEnvelope


class AsharePITAdapterRegistrationServiceV1:
    def __init__(
        self,
        store: Any,
        *,
        flags: ResolvedAGSFlags,
        registry: AsharePITAdapterRegistryV1,
    ) -> None:
        from src.research_ledger.events.store import ResearchEventStore

        if not isinstance(store, ResearchEventStore):
            raise TypeError("PIT adapter registration requires ResearchEventStore")
        required = (
            "VIBE_TRADING_ALPHA_FOUNDRY",
            "VIBE_TRADING_ALPHA_SCORECARD",
            "VIBE_TRADING_RESEARCH_EVENTS",
        )
        if any(not flags.enabled(name) for name in required):
            raise RuntimeError("PIT adapter registration capability is disabled")
        self.store = store
        self.registry = registry
        self.artifacts = AsharePITAdapterRegistrationArtifactStoreV1(
            store.artifact_root
        )

    def register(
        self,
        *,
        adapter_id: str,
        run_id: str,
    ) -> RecordedAsharePITAdapterRegistrationV1:
        registration = self.registry.registration(adapter_id)
        record = AsharePITAdapterRegistrationArtifactV1.build(
            registry_hash=self.registry.registry_hash,
            registration=registration,
        )
        existing = [
            event
            for event in self.store.query_events(
                event_type=PIT_ADAPTER_REGISTRATION_EVENT_TYPE
            )
            if event.payload["adapter_id"] == adapter_id
        ]
        if len(existing) > 1:
            raise EventTransitionError("multiple PIT adapter registrations exist")
        if existing:
            event = existing[0]
            if event.payload["registration_hash"] != registration.registration_hash:
                raise EventTransitionError("PIT adapter registration is already frozen")
            reference = event.payload["artifact_refs"][0]
            reopened = self.artifacts.read(
                str(reference["relative_path"]),
                expected_artifact_hash=str(event.payload["registration_artifact_hash"]),
                expected_blob_hash=str(reference["artifact_hash"]),
            )
            expected = self.event_payload(
                self.registration_id(registration.registration_hash),
                reopened,
                str(event.payload["source_watermark_event_hash"])
                if event.payload["source_watermark_event_hash"] is not None
                else None,
                reference,
            )
            if reopened != record or canonical_json(expected) != canonical_json(
                _plain(event.payload)
            ):
                raise EventValidationError("PIT adapter registration differs from replay")
            return RecordedAsharePITAdapterRegistrationV1(reopened, _artifact(reference, reopened.artifact_hash), event)
        events = self.store.query_events()
        watermark = events[-1].event_hash if events else None
        artifact = self.artifacts.write(record)
        registration_id = self.registration_id(registration.registration_hash)
        event = self.store._append_producer_event(
            EventDraft(
                event_type=PIT_ADAPTER_REGISTRATION_EVENT_TYPE,
                entity_id=registration_id,
                run_id=run_id,
                payload_schema_version="ashare_pit_adapter_registered.v1",
                idempotency_key="ashare-pit-adapter-v1:"
                + registration.registration_hash,
                payload=self.event_payload(
                    registration_id,
                    record,
                    watermark,
                    artifact.reference(),
                ),
            )
        )
        return RecordedAsharePITAdapterRegistrationV1(record, artifact, event)

    @staticmethod
    def registration_id(registration_hash: str) -> str:
        return "ashare-pit-adapter-v1-" + registration_hash.removeprefix(
            "sha256:"
        )[:24]

    @staticmethod
    def event_payload(
        registration_id: str,
        record: AsharePITAdapterRegistrationArtifactV1,
        source_watermark_event_hash: str | None,
        artifact_reference: Mapping[str, str],
    ) -> dict[str, Any]:
        registration = _registration_from_dict(record.registration)
        return {
            "registration_id": registration_id,
            "adapter_id": registration.descriptor.adapter_id,
            "provider": registration.descriptor.provider,
            "adapter_version": registration.descriptor.adapter_version,
            "authority_class": registration.authority_class,
            "registry_hash": record.registry_hash,
            "registration_hash": registration.registration_hash,
            "implementation_hash": registration.implementation_hash,
            "factory_origin": registration.factory_origin,
            "factory_hash": registration.factory_hash,
            "provider_version": registration.provider_version,
            "descriptor_hash": registration.descriptor.descriptor_hash,
            "producer_schema_version": record.producer_schema_version,
            "producer_policy_hash": record.producer_policy_hash,
            "registration_artifact_hash": record.artifact_hash,
            "source_watermark_event_hash": source_watermark_event_hash,
            "artifact_refs": [dict(artifact_reference)],
        }


class AsharePITSnapshotServiceV2:
    def __init__(
        self,
        store: Any,
        *,
        flags: ResolvedAGSFlags,
        registry: AsharePITAdapterRegistryV1,
    ) -> None:
        from src.research_ledger.events.store import ResearchEventStore

        if not isinstance(store, ResearchEventStore):
            raise TypeError("A-share PIT snapshot requires ResearchEventStore")
        required = (
            "VIBE_TRADING_ALPHA_FOUNDRY",
            "VIBE_TRADING_ALPHA_SCORECARD",
            "VIBE_TRADING_RESEARCH_EVENTS",
        )
        if any(not flags.enabled(name) for name in required):
            raise RuntimeError("A-share PIT snapshot capability is disabled")
        self.store = store
        self.registry = registry
        self.artifacts = FrozenAsharePITSnapshotArtifactStoreV2(store.artifact_root)

    def record(
        self,
        *,
        adapter_registration_event_hash: str,
        evaluation_policy_event_hash: str,
        run_id: str,
    ) -> RecordedAsharePITSnapshotV2:
        existing = [
            event
            for event in self.store.query_events(event_type=PIT_SNAPSHOT_EVENT_TYPE)
            if event.run_id == run_id
        ]
        if len(existing) > 1:
            raise EventTransitionError("multiple A-share PIT snapshots exist for run")
        if existing:
            event = existing[0]
            if (
                event.payload["adapter_registration_event_hash"]
                != adapter_registration_event_hash
                or event.payload["evaluation_policy_event_hash"]
                != evaluation_policy_event_hash
            ):
                raise EventTransitionError("A-share PIT snapshot sources are frozen")
            reference = event.payload["artifact_refs"][0]
            snapshot = self.artifacts.read_manifest(
                str(reference["relative_path"]),
                expected_snapshot_hash=str(event.payload["snapshot_hash"]),
                expected_blob_hash=str(reference["artifact_hash"]),
            )
            rebuilt = self.rebuild_snapshot(
                self.store,
                snapshot=snapshot,
                adapter_registration_event_hash=adapter_registration_event_hash,
                evaluation_policy_event_hash=evaluation_policy_event_hash,
                run_id=run_id,
            )
            expected = self.event_payload(
                self.snapshot_id(snapshot.snapshot_hash),
                rebuilt,
                str(event.payload["source_watermark_event_hash"]),
                reference,
            )
            if rebuilt != snapshot or canonical_json(expected) != canonical_json(
                _plain(event.payload)
            ):
                raise EventValidationError("A-share PIT snapshot differs from replay")
            return RecordedAsharePITSnapshotV2(
                snapshot,
                _artifact(reference, snapshot.snapshot_hash),
                event,
            )
        registration_event, registration_record = self._registration_source(
            adapter_registration_event_hash
        )
        policy_event, request, policy_hashes = self._policy_source(
            evaluation_policy_event_hash,
            run_id,
            registration_record,
        )
        if any(
            event.run_id == run_id and event.event_type == "TrialStarted"
            for event in self.store.query_events()
        ):
            raise EventTransitionError("PIT snapshot must precede trial start")
        adapter_id = registration_record.descriptor.adapter_id
        bundle = self.registry.adapter(adapter_id).load(request).sealed_copy()
        validated = validate_ashare_pit_source_v2(
            bundle=bundle,
            request=request,
            registration=registration_record,
        )
        table_refs = self.artifacts.write_bundle_tables(
            bundle,
            derived_masks=validated.derived_masks,
        )
        derived_evidence = dict(_plain(validated.evidence))
        derived_evidence["adapter_registration_event_hash"] = (
            registration_event.event_hash
        )
        snapshot = FrozenAsharePITSnapshotV2.build(
            registration=registration_record,
            registry_hash=self.registry.registry_hash,
            request=request,
            source_manifest=bundle.source_manifest,
            evaluation_policy_event_hash=policy_event.event_hash,
            calendar_hash=policy_hashes["calendar_hash"],
            evaluation_time_policy_hash=policy_hashes[
                "evaluation_time_policy_hash"
            ],
            split_plan_hash=policy_hashes["split_plan_hash"],
            table_refs=table_refs,
            derived_evidence=derived_evidence,
        )
        artifact = self.artifacts.write_manifest(snapshot)
        events = self.store.query_events()
        watermark = events[-1].event_hash
        snapshot_id = self.snapshot_id(snapshot.snapshot_hash)
        event = self.store._append_producer_event(
            EventDraft(
                event_type=PIT_SNAPSHOT_EVENT_TYPE,
                entity_id=snapshot_id,
                run_id=run_id,
                payload_schema_version="ashare_pit_snapshot_recorded.v2",
                idempotency_key="ashare-pit-snapshot-v2:" + snapshot.snapshot_hash,
                payload=self.event_payload(
                    snapshot_id,
                    snapshot,
                    watermark,
                    artifact.reference(),
                    adapter_registration_event_hash=registration_event.event_hash,
                ),
            )
        )
        return RecordedAsharePITSnapshotV2(snapshot, artifact, event)

    def _registration_source(
        self,
        event_hash: str,
    ) -> tuple[ResearchEventEnvelope, RegisteredAsharePITAdapterV1]:
        matches = [
            event
            for event in self.store.query_events(
                event_type=PIT_ADAPTER_REGISTRATION_EVENT_TYPE
            )
            if event.event_hash == event_hash
        ]
        if len(matches) != 1:
            raise EventValidationError("PIT snapshot requires one adapter registration")
        event = matches[0]
        reference = event.payload["artifact_refs"][0]
        artifact = AsharePITAdapterRegistrationArtifactStoreV1(
            self.store.artifact_root
        ).read(
            str(reference["relative_path"]),
            expected_artifact_hash=str(event.payload["registration_artifact_hash"]),
            expected_blob_hash=str(reference["artifact_hash"]),
        )
        registration = _registration_from_dict(artifact.registration)
        runtime = self.registry.registration(registration.descriptor.adapter_id)
        if (
            runtime != registration
            or artifact.registry_hash != self.registry.registry_hash
        ):
            raise EventValidationError("runtime PIT registry differs from registration")
        return event, registration

    def _policy_source(
        self,
        event_hash: str,
        run_id: str,
        registration: RegisteredAsharePITAdapterV1,
    ) -> tuple[ResearchEventEnvelope, AsharePITSnapshotRequestV1, dict[str, str]]:
        matches = [
            event
            for event in self.store.query_events(event_type="EvaluationPolicyRegistered")
            if event.event_hash == event_hash
        ]
        if len(matches) != 1 or matches[0].run_id != run_id:
            raise EventValidationError("PIT snapshot requires same-run evaluation policy")
        event = matches[0]
        reference = event.payload["artifact_refs"][0]
        bundle = EvaluationPolicyArtifactStoreV1(self.store.artifact_root).read(
            str(reference["relative_path"]),
            expected_bundle_hash=str(event.payload["bundle_hash"]),
            expected_blob_hash=str(reference["artifact_hash"]),
        )
        calendar, time_policy, split_plan = bundle.resolved_components()
        start_index = calendar.dates.index(split_plan.train.start)
        end_index = calendar.dates.index(split_plan.valid.end)
        dates = calendar.dates[start_index : end_index + 1]
        request = AsharePITSnapshotRequestV1(
            adapter_id=registration.descriptor.adapter_id,
            calendar_dates=dates,
            required_fields=ASHARE_PIT_REQUIRED_FIELDS,
            valid_cutoff=split_plan.valid.end,
            evaluation_policy_event_hash=event.event_hash,
        )
        return event, request, {
            "calendar_hash": calendar.calendar_hash,
            "evaluation_time_policy_hash": time_policy.policy_hash,
            "split_plan_hash": split_plan.plan_hash,
        }

    @staticmethod
    def snapshot_id(snapshot_hash: str) -> str:
        return "ashare-pit-snapshot-v2-" + snapshot_hash.removeprefix("sha256:")[:24]

    @staticmethod
    def event_payload(
        snapshot_id: str,
        snapshot: FrozenAsharePITSnapshotV2,
        source_watermark_event_hash: str,
        artifact_reference: Mapping[str, str],
        *,
        adapter_registration_event_hash: str | None = None,
    ) -> dict[str, Any]:
        evidence = snapshot.derived_evidence
        return {
            "snapshot_id": snapshot_id,
            "snapshot_hash": snapshot.snapshot_hash,
            "adapter_id": str(snapshot.request["adapter_id"]),
            "adapter_registration_event_hash": (
                adapter_registration_event_hash
                if adapter_registration_event_hash is not None
                else str(evidence["adapter_registration_event_hash"])
            ),
            "evaluation_policy_event_hash": snapshot.evaluation_policy_event_hash,
            "source_watermark_event_hash": source_watermark_event_hash,
            "producer_schema_version": PIT_SNAPSHOT_PRODUCER_SCHEMA,
            "producer_policy_hash": PIT_SNAPSHOT_PRODUCER_POLICY_HASH,
            "validation_policy_hash": str(evidence["validation_policy_hash"]),
            "request_hash": canonical_json_hash(_plain(snapshot.request)),
            "source_manifest_hash": canonical_json_hash(
                _plain(snapshot.source_manifest)
            ),
            "registry_hash": snapshot.registry_hash,
            "registration_hash": str(snapshot.registration["registration_hash"]),
            "calendar_hash": snapshot.calendar_hash,
            "evaluation_time_policy_hash": snapshot.evaluation_time_policy_hash,
            "split_plan_hash": snapshot.split_plan_hash,
            "pit_contract_status": str(evidence["pit_contract_status"]),
            "survivorship_status": str(evidence["survivorship_status"]),
            "cutoff_status": str(evidence["cutoff_status"]),
            "decision_grade": bool(evidence["decision_grade"]),
            "hard_failures": list(evidence["hard_failures"]),
            "caps": list(evidence["caps"]),
            "warnings": list(evidence["warnings"]),
            "artifact_refs": [dict(artifact_reference)],
        }

    @staticmethod
    def rebuild_snapshot(
        store: Any,
        *,
        snapshot: FrozenAsharePITSnapshotV2,
        adapter_registration_event_hash: str,
        evaluation_policy_event_hash: str,
        run_id: str,
    ) -> FrozenAsharePITSnapshotV2:
        registration_events = [
            event
            for event in store.query_events(event_type=PIT_ADAPTER_REGISTRATION_EVENT_TYPE)
            if event.event_hash == adapter_registration_event_hash
        ]
        policy_events = [
            event
            for event in store.query_events(event_type="EvaluationPolicyRegistered")
            if event.event_hash == evaluation_policy_event_hash
        ]
        if (
            len(registration_events) != 1
            or len(policy_events) != 1
            or policy_events[0].run_id != run_id
        ):
            raise EventValidationError("PIT snapshot replay sources are missing")
        registration_event = registration_events[0]
        registration_ref = registration_event.payload["artifact_refs"][0]
        registration_artifact = AsharePITAdapterRegistrationArtifactStoreV1(
            store.artifact_root
        ).read(
            str(registration_ref["relative_path"]),
            expected_artifact_hash=str(
                registration_event.payload["registration_artifact_hash"]
            ),
            expected_blob_hash=str(registration_ref["artifact_hash"]),
        )
        registration = _registration_from_dict(registration_artifact.registration)
        policy_event = policy_events[0]
        policy_ref = policy_event.payload["artifact_refs"][0]
        policy_bundle = EvaluationPolicyArtifactStoreV1(store.artifact_root).read(
            str(policy_ref["relative_path"]),
            expected_bundle_hash=str(policy_event.payload["bundle_hash"]),
            expected_blob_hash=str(policy_ref["artifact_hash"]),
        )
        calendar, time_policy, split_plan = policy_bundle.resolved_components()
        dates = calendar.dates[
            calendar.dates.index(split_plan.train.start) :
            calendar.dates.index(split_plan.valid.end) + 1
        ]
        request = AsharePITSnapshotRequestV1(
            adapter_id=registration.descriptor.adapter_id,
            calendar_dates=dates,
            required_fields=ASHARE_PIT_REQUIRED_FIELDS,
            valid_cutoff=split_plan.valid.end,
            evaluation_policy_event_hash=policy_event.event_hash,
        )
        artifact_store = FrozenAsharePITSnapshotArtifactStoreV2(store.artifact_root)
        source_bundle = artifact_store.read_bundle(snapshot)
        validated = validate_ashare_pit_source_v2(
            bundle=source_bundle,
            request=request,
            registration=registration,
        )
        evidence = dict(_plain(validated.evidence))
        evidence["adapter_registration_event_hash"] = registration_event.event_hash
        expected_masks = artifact_store.read_derived_masks(snapshot)
        for name, frame in validated.derived_masks.items():
            stored = expected_masks.get(name)
            if stored is None:
                raise EventValidationError("PIT snapshot derived mask is missing")
            try:
                pd.testing.assert_frame_equal(frame, stored, check_freq=False)
            except AssertionError as exc:
                raise EventValidationError(
                    "PIT snapshot derived mask differs from replay"
                ) from exc
        refs = tuple(
            AsharePITTableReferenceV1.from_dict(raw)
            for raw in snapshot.table_refs
        )
        rebuilt = FrozenAsharePITSnapshotV2.build(
            registration=registration,
            registry_hash=registration_artifact.registry_hash,
            request=request,
            source_manifest=source_bundle.source_manifest,
            evaluation_policy_event_hash=policy_event.event_hash,
            calendar_hash=calendar.calendar_hash,
            evaluation_time_policy_hash=time_policy.policy_hash,
            split_plan_hash=split_plan.plan_hash,
            table_refs=refs,
            derived_evidence=evidence,
        )
        if rebuilt != snapshot:
            raise EventValidationError("PIT snapshot does not replay from partitions")
        return rebuilt


def _artifact(
    reference: Mapping[str, Any],
    semantic_hash: str,
) -> ContentAddressedArtifact:
    return ContentAddressedArtifact(
        semantic_hash=semantic_hash,
        relative_path=str(reference["relative_path"]),
        blob_hash=str(reference["artifact_hash"]),
        media_type=str(reference["media_type"]),
    )


__all__ = [
    "AsharePITAdapterRegistrationArtifactStoreV1",
    "AsharePITAdapterRegistrationServiceV1",
    "AsharePITSnapshotServiceV2",
    "PIT_ADAPTER_REGISTRATION_EVENT_TYPE",
    "PIT_SNAPSHOT_EVENT_TYPE",
    "RecordedAsharePITAdapterRegistrationV1",
    "RecordedAsharePITSnapshotV2",
]
