"""Formal, outcome-free Activation cycle bootstrap on the research event spine."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.alpha_foundry.activation.protocol_v2 import (
    ActivationApplicabilityMatrixV1,
    ActivationProtocolRegistryV2,
    PreregisteredActivationStatisticalProtocolV2,
)
from src.alpha_foundry.activation.release_manifest import (
    ReleaseManifestBuilderV2,
    ReleaseManifestV2,
)
from src.alpha_quality.flags import ResolvedAGSFlags
from src.research_ledger.events import EventDraft, ResearchEventEnvelope, ResearchEventStore
from src.research_ledger.events.artifacts import (
    AtomicContentAddressedArtifactWriter,
    ContentAddressedArtifact,
)
from src.research_ledger.hash_utils import canonical_json_hash


_CYCLE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
_ZERO_HASH = "sha256:" + "0" * 64
_REQUIRED_FLAGS = (
    "VIBE_TRADING_AGS_ENABLED",
    "VIBE_TRADING_ALPHA_FOUNDRY",
    "VIBE_TRADING_RESEARCH_EVENTS",
    "VIBE_TRADING_TOPOLOGY_RETRIEVER",
)
_PROVIDER_AUDIT_FIELDS = (
    "adjustment",
    "amount",
    "close",
    "corporate_action",
    "daily_csi300_membership",
    "delisting_state",
    "high",
    "limit_down",
    "limit_up",
    "listing_date",
    "low",
    "open",
    "reliable_price_availability",
    "st_status",
    "suspension",
    "trading_calendar",
    "volume",
)
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "research_cycle_id",
        "event_database_relative_path",
        "artifact_root_relative_path",
        "protocol_event_hash",
        "protocol_hash",
        "applicability_event_hash",
        "applicability_matrix_hash",
        "provider_audit_catalog_version",
        "provider_audit_fields",
        "provider_audit_catalog_hash",
        "flat_policy_hash",
        "topology_policy_hash",
        "resource_policy_hash",
        "release_manifest_hash",
        "release_manifest_scope_hash",
        "outcome_accessed",
        "promotion_effect",
        "cycle_manifest_hash",
    }
)


@dataclass(frozen=True)
class FormalActivationCycleConfigV1:
    research_cycle_id: str
    accepted_code_commit: str
    normalization_policy_hash: str
    flat_policy_hash: str
    topology_policy_hash: str
    resource_policy_hash: str
    sesoi: float = 0.10

    def __post_init__(self) -> None:
        if _CYCLE_ID_RE.fullmatch(self.research_cycle_id) is None:
            raise ValueError("research_cycle_id is invalid")
        for name in (
            "normalization_policy_hash",
            "flat_policy_hash",
            "topology_policy_hash",
            "resource_policy_hash",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
                raise ValueError(f"{name} must be a canonical sha256 hash")


@dataclass(frozen=True)
class FormalActivationCycleV1:
    research_cycle_id: str
    store: ResearchEventStore
    protocol_event: ResearchEventEnvelope
    applicability_event: ResearchEventEnvelope
    bootstrap_event: ResearchEventEnvelope
    manifest_artifact: ContentAddressedArtifact
    release_manifest: ReleaseManifestV2


class FormalActivationCycleBootstrapV1:
    """Create or reopen one cycle-specific event database and artifact root."""

    def __init__(
        self,
        repository_root: str | Path,
        *,
        flags: ResolvedAGSFlags,
        code_version: str,
    ) -> None:
        self._repository_root = Path(repository_root).resolve(strict=True)
        self._flags = flags
        self._code_version = code_version

    def bootstrap(self, config: FormalActivationCycleConfigV1) -> FormalActivationCycleV1:
        if any(not self._flags.enabled(name) for name in _REQUIRED_FLAGS):
            raise RuntimeError("formal Activation cycle capability is disabled")
        release_manifest = ReleaseManifestBuilderV2(
            self._repository_root,
            accepted_code_commit=config.accepted_code_commit,
        ).build()
        cycle_relative = Path("agent/research_evidence/activation_cycles") / config.research_cycle_id
        cycle_root = self._repository_root / cycle_relative
        store = ResearchEventStore(
            cycle_root / "events/research_events.sqlite3",
            artifact_root=cycle_root / "artifacts",
            flags=self._flags,
            code_version=self._code_version,
            durability_profile="authoritative",
        )
        existing = self._events_for_cycle(store, config.research_cycle_id)
        protocol = PreregisteredActivationStatisticalProtocolV2.create(
            research_cycle_id=config.research_cycle_id,
            normalization_policy_hash=config.normalization_policy_hash,
            registration_event_hash=_ZERO_HASH,
            code_manifest_hash=release_manifest.manifest_hash,
            sesoi=config.sesoi,
        )
        protocol_event = self._one(existing, "ActivationStatisticalProtocolV2Registered")
        registry = ActivationProtocolRegistryV2(store)
        if protocol_event is None:
            if existing:
                raise ValueError("formal cycle contains events before protocol registration")
            protocol_event = registry.register_protocol(
                run_id=config.research_cycle_id,
                protocol=protocol,
            ).event
        elif protocol_event.payload["protocol_hash"] != protocol.protocol_hash:
            raise ValueError("existing formal cycle protocol conflicts with requested config")

        matrix = ActivationApplicabilityMatrixV1.create_default(
            research_cycle_id=config.research_cycle_id,
            profile="candidate_zoo",
            source_hash=protocol_event.event_hash,
            frozen_before_event_hash=protocol_event.event_hash,
            code_manifest_hash=release_manifest.manifest_hash,
        )
        existing = self._events_for_cycle(store, config.research_cycle_id)
        applicability_event = self._one(existing, "ActivationApplicabilityMatrixV1Registered")
        if applicability_event is None:
            applicability_event = registry.register_applicability_matrix(
                run_id=config.research_cycle_id,
                matrix=matrix,
            ).event
        elif applicability_event.payload["matrix_hash"] != matrix.matrix_hash:
            raise ValueError("existing applicability matrix conflicts with requested config")

        catalog_content = {
            "schema_version": "activation_provider_audit_catalog.v1",
            "fields": list(_PROVIDER_AUDIT_FIELDS),
            "financial_statement_fields_allowed": False,
        }
        provider_audit_catalog_hash = canonical_json_hash(catalog_content)
        manifest_content: dict[str, Any] = {
            "schema_version": "formal_activation_cycle_manifest.v1",
            "research_cycle_id": config.research_cycle_id,
            "event_database_relative_path": (cycle_relative / "events/research_events.sqlite3").as_posix(),
            "artifact_root_relative_path": (cycle_relative / "artifacts").as_posix(),
            "protocol_event_hash": protocol_event.event_hash,
            "protocol_hash": protocol.protocol_hash,
            "applicability_event_hash": applicability_event.event_hash,
            "applicability_matrix_hash": matrix.matrix_hash,
            "provider_audit_catalog_version": "activation_provider_audit_catalog.v1",
            "provider_audit_fields": list(_PROVIDER_AUDIT_FIELDS),
            "provider_audit_catalog_hash": provider_audit_catalog_hash,
            "flat_policy_hash": config.flat_policy_hash,
            "topology_policy_hash": config.topology_policy_hash,
            "resource_policy_hash": config.resource_policy_hash,
            "release_manifest_hash": release_manifest.manifest_hash,
            "release_manifest_scope_hash": release_manifest.scope.scope_hash,
            "outcome_accessed": False,
            "promotion_effect": "none",
        }
        manifest_content["cycle_manifest_hash"] = canonical_json_hash(manifest_content)
        artifact = AtomicContentAddressedArtifactWriter(store.artifact_root).write_json(
            namespace="formal_cycle",
            payload=manifest_content,
            schema_version="formal_activation_cycle_manifest.v1",
            semantic_hash_field="cycle_manifest_hash",
            closed_keys=_MANIFEST_KEYS,
            media_type="application/json",
        )
        existing = self._events_for_cycle(store, config.research_cycle_id)
        bootstrap_event = self._one(existing, "FormalActivationCycleV1Bootstrapped")
        if bootstrap_event is None:
            bootstrap_id = "formal-cycle-" + manifest_content["cycle_manifest_hash"][-24:]
            bootstrap_event = store._append_producer_event(
                EventDraft(
                    event_type="FormalActivationCycleV1Bootstrapped",
                    entity_id=bootstrap_id,
                    run_id=config.research_cycle_id,
                    payload_schema_version="formal_activation_cycle_bootstrapped.v1",
                    idempotency_key="formal-activation-cycle-v1:" + config.research_cycle_id,
                    payload={
                        "cycle_bootstrap_id": bootstrap_id,
                        "research_cycle_id": config.research_cycle_id,
                        "protocol_event_hash": protocol_event.event_hash,
                        "applicability_event_hash": applicability_event.event_hash,
                        "provider_audit_catalog_hash": provider_audit_catalog_hash,
                        "flat_policy_hash": config.flat_policy_hash,
                        "topology_policy_hash": config.topology_policy_hash,
                        "resource_policy_hash": config.resource_policy_hash,
                        "release_manifest_hash": release_manifest.manifest_hash,
                        "release_manifest_scope_hash": release_manifest.scope.scope_hash,
                        "cycle_manifest_hash": manifest_content["cycle_manifest_hash"],
                        "artifact_namespace": "formal_cycle",
                        "promotion_effect": "none",
                        "artifact_refs": [artifact.reference()],
                    },
                )
            )
        elif bootstrap_event.payload["cycle_manifest_hash"] != manifest_content["cycle_manifest_hash"]:
            raise ValueError("existing formal cycle manifest conflicts with requested config")
        if not store.verify_chain():
            raise ValueError("formal Activation cycle event chain did not verify")
        return FormalActivationCycleV1(
            research_cycle_id=config.research_cycle_id,
            store=store,
            protocol_event=protocol_event,
            applicability_event=applicability_event,
            bootstrap_event=bootstrap_event,
            manifest_artifact=artifact,
            release_manifest=release_manifest,
        )

    @staticmethod
    def _events_for_cycle(store: ResearchEventStore, research_cycle_id: str) -> list[ResearchEventEnvelope]:
        return [event for event in store.query_events() if event.run_id == research_cycle_id]

    @staticmethod
    def _one(events: list[ResearchEventEnvelope], event_type: str) -> ResearchEventEnvelope | None:
        matched = [event for event in events if event.event_type == event_type]
        if len(matched) > 1:
            raise ValueError(f"formal cycle contains duplicate {event_type} events")
        return matched[0] if matched else None


__all__ = [
    "FormalActivationCycleBootstrapV1",
    "FormalActivationCycleConfigV1",
    "FormalActivationCycleV1",
]
