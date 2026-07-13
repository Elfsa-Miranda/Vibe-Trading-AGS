"""Run-bound resolved evaluation contract producer and artifact replay."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from src.alpha_quality.evaluation_contract.model import (
    TIER_INVARIANT_MANIFEST_HASH,
    EvaluationPolicyReferencesV1,
    EvaluationProfileTemplateV1,
    ResolvedEvaluationContractV1,
)
from src.alpha_quality.evaluation_contract.registry import (
    APPLICABILITY_RULE_HASHES,
    DEFAULT_PROFILE_REGISTRY,
    PRODUCER_REGISTRY_HASH,
    EvaluationProfileRegistryV1,
    validate_registered_policy_references,
)
from src.alpha_quality.evaluation_contract.research_family import build_research_family_id
from src.alpha_quality.evaluation_registry_v1 import EvaluationPolicyArtifactStoreV1
from src.alpha_quality.flags import ResolvedAGSFlags
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


CONTRACT_MEDIA_TYPE = "application/vnd.vibe.resolved-evaluation-contract-v1+json"
CONTRACT_PRODUCER_SCHEMA = "resolved_evaluation_contract_service.v1"
CONTRACT_PRODUCER_POLICY_HASH = canonical_json_hash(
    {
        "schema_version": "resolved_evaluation_contract_producer_policy.v1",
        "registration_order": "evaluation_policy_then_contract_before_trials",
        "profile_resolution": "exact_hash_no_latest",
        "policy_defaults": "forbidden",
        "tier_invariants": TIER_INVARIANT_MANIFEST_HASH,
    }
)
_CONTRACT_KEYS = frozenset(
    {
        "schema_version", "run_id", "research_family_id", "profile_template",
        "evaluation_policy_event_hash", "evaluation_policy_bundle_hash",
        "producer_registry_hash", "policy_references",
        "tier_invariant_manifest_hash", "preregistration_watermark", "contract_hash",
    }
)


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"resolved contract {name} must be an object")
    return value


def profile_from_dict(raw: Mapping[str, Any]) -> EvaluationProfileTemplateV1:
    expected = {
        "schema_version", "profile_id", "profile_version", "claim_requirements",
        "applicability_rule_hashes", "tier_invariants", "maximum_promotion",
        "registrar_manifest_hash", "producer_registry_hash", "authority_class",
        "template_hash",
    }
    if set(raw) != expected:
        raise ValueError("resolved contract profile snapshot is not closed")
    if not isinstance(raw["claim_requirements"], Mapping) or not isinstance(
        raw["applicability_rule_hashes"], Mapping
    ) or not isinstance(raw["tier_invariants"], list):
        raise ValueError("resolved contract profile snapshot is malformed")
    return EvaluationProfileTemplateV1(
        schema_version=str(raw["schema_version"]),  # type: ignore[arg-type]
        profile_id=str(raw["profile_id"]),
        profile_version=str(raw["profile_version"]),
        claim_requirements={str(k): str(v) for k, v in raw["claim_requirements"].items()},
        applicability_rule_hashes={
            str(k): str(v) for k, v in raw["applicability_rule_hashes"].items()
        },
        tier_invariants=frozenset(str(item) for item in raw["tier_invariants"]),
        maximum_promotion=str(raw["maximum_promotion"]),  # type: ignore[arg-type]
        registrar_manifest_hash=str(raw["registrar_manifest_hash"]),
        producer_registry_hash=str(raw["producer_registry_hash"]),
        authority_class=str(raw["authority_class"]),  # type: ignore[arg-type]
        template_hash=str(raw["template_hash"]),
    )


def policy_references_from_dict(raw: Mapping[str, Any]) -> EvaluationPolicyReferencesV1:
    expected = set(EvaluationPolicyReferencesV1.__dataclass_fields__)
    if set(raw) != expected:
        raise ValueError("resolved contract policy references are not closed")
    return EvaluationPolicyReferencesV1(**{key: str(raw[key]) for key in sorted(expected)})


def contract_from_dict(raw: Mapping[str, Any]) -> ResolvedEvaluationContractV1:
    if set(raw) != _CONTRACT_KEYS:
        raise ValueError("resolved evaluation contract artifact is not closed")
    return ResolvedEvaluationContractV1(
        schema_version=str(raw["schema_version"]),  # type: ignore[arg-type]
        run_id=str(raw["run_id"]),
        research_family_id=str(raw["research_family_id"]),
        profile_template=profile_from_dict(_mapping(raw["profile_template"], "profile")),
        evaluation_policy_event_hash=str(raw["evaluation_policy_event_hash"]),
        evaluation_policy_bundle_hash=str(raw["evaluation_policy_bundle_hash"]),
        producer_registry_hash=str(raw["producer_registry_hash"]),
        policy_references=policy_references_from_dict(
            _mapping(raw["policy_references"], "policy references")
        ),
        tier_invariant_manifest_hash=str(raw["tier_invariant_manifest_hash"]),
        preregistration_watermark=str(raw["preregistration_watermark"]),
        contract_hash=str(raw["contract_hash"]),
    )


class ResolvedEvaluationContractArtifactStoreV1:
    namespace = "resolved-evaluation-contract-v1"

    def __init__(self, root: str | Path) -> None:
        self.writer = AtomicContentAddressedArtifactWriter(root, max_bytes=2 * 1024 * 1024)

    def write(self, contract: ResolvedEvaluationContractV1) -> ContentAddressedArtifact:
        return self.writer.write_json(
            namespace=self.namespace,
            payload=contract.to_dict(),
            schema_version="resolved_evaluation_contract.v1",
            semantic_hash_field="contract_hash",
            closed_keys=_CONTRACT_KEYS,
            media_type=CONTRACT_MEDIA_TYPE,
        )

    def read(
        self, relative_path: str, *, expected_contract_hash: str, expected_blob_hash: str,
    ) -> ResolvedEvaluationContractV1:
        reference = {
            "relative_path": relative_path,
            "artifact_hash": expected_blob_hash,
            "media_type": CONTRACT_MEDIA_TYPE,
        }
        normalized = validate_artifact_references(self.writer.root, [reference])[0]
        target = self.writer.root.joinpath(*normalized["relative_path"].split("/"))

        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite resolved contract JSON: {value}")

        def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate resolved contract artifact key")
                result[key] = value
            return result

        raw = json.loads(
            target.read_text(encoding="utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
        if not isinstance(raw, Mapping):
            raise ValueError("resolved contract artifact must be an object")
        contract = contract_from_dict(raw)
        if contract.contract_hash != expected_contract_hash:
            raise ValueError("resolved contract identity differs")
        return contract


@dataclass(frozen=True)
class RecordedResolvedEvaluationContractV1:
    contract: ResolvedEvaluationContractV1
    event: ResearchEventEnvelope
    artifact: ContentAddressedArtifact


class ResolvedEvaluationContractServiceV1:
    def __init__(
        self,
        store: Any,
        *,
        flags: ResolvedAGSFlags,
        profile_registry: EvaluationProfileRegistryV1 = DEFAULT_PROFILE_REGISTRY,
    ) -> None:
        from src.research_ledger.events.store import ResearchEventStore

        if not isinstance(store, ResearchEventStore):
            raise TypeError("resolved contract requires ResearchEventStore")
        required = ("VIBE_TRADING_RESEARCH_EVENTS", "VIBE_TRADING_ALPHA_SCORECARD")
        if any(not flags.enabled(name) for name in required):
            raise RuntimeError("resolved evaluation contract capability is disabled")
        if flags.as_dict() != store.flags.as_dict():
            raise ValueError("resolved contract flag snapshots differ")
        if profile_registry is not DEFAULT_PROFILE_REGISTRY:
            raise ValueError("resolved contract requires the build-time profile registry")
        self.store = store
        self.flags = flags
        self.profile_registry = profile_registry
        self.artifacts = ResolvedEvaluationContractArtifactStoreV1(store.artifact_root)

    def register(
        self,
        *,
        run_id: str,
        evaluation_policy_event_hash: str,
        profile_id: str,
        profile_version: str,
        policy_references: EvaluationPolicyReferencesV1,
        custom_profile: EvaluationProfileTemplateV1 | None = None,
    ) -> RecordedResolvedEvaluationContractV1:
        profile = custom_profile or self.profile_registry.resolve_exact(
            profile_id, profile_version
        )
        if custom_profile is not None and profile.authority_class != "custom_research_only":
            raise ValueError("caller profile cannot impersonate build-time authority")
        if (profile.profile_id, profile.profile_version) != (profile_id, profile_version):
            raise ValueError("custom profile identity differs from requested exact profile")
        if not set(profile.applicability_rule_hashes.values()).issubset(
            APPLICABILITY_RULE_HASHES
        ):
            raise ValueError("profile uses an unknown closed applicability rule")
        validate_registered_policy_references(policy_references)
        policy_events = [
            event for event in self.store.query_events(event_type="EvaluationPolicyRegistered")
            if event.event_hash == evaluation_policy_event_hash and event.run_id == run_id
        ]
        if len(policy_events) != 1:
            raise EventTransitionError("resolved contract requires exact run policy event")
        policy_event = policy_events[0]
        policy_reference = policy_event.payload["artifact_refs"][0]
        policy_bundle = EvaluationPolicyArtifactStoreV1(self.store.artifact_root).read(
            str(policy_reference["relative_path"]),
            expected_bundle_hash=str(policy_event.payload["bundle_hash"]),
            expected_blob_hash=str(policy_reference["artifact_hash"]),
        )
        family_id = build_research_family_id(
            profile_template_hash=profile.template_hash,
            evaluation_policy_bundle_hash=policy_bundle.bundle_hash,
            producer_registry_hash=PRODUCER_REGISTRY_HASH,
            policy_references=policy_references,
        )
        content = {
            "schema_version": "resolved_evaluation_contract.v1",
            "run_id": run_id,
            "research_family_id": family_id,
            "profile_template": profile.to_dict(),
            "evaluation_policy_event_hash": policy_event.event_hash,
            "evaluation_policy_bundle_hash": policy_bundle.bundle_hash,
            "producer_registry_hash": PRODUCER_REGISTRY_HASH,
            "policy_references": policy_references.to_dict(),
            "tier_invariant_manifest_hash": TIER_INVARIANT_MANIFEST_HASH,
            "preregistration_watermark": policy_event.event_hash,
        }
        contract = ResolvedEvaluationContractV1(
            schema_version="resolved_evaluation_contract.v1",
            run_id=run_id,
            research_family_id=family_id,
            profile_template=profile,
            evaluation_policy_event_hash=policy_event.event_hash,
            evaluation_policy_bundle_hash=policy_bundle.bundle_hash,
            producer_registry_hash=PRODUCER_REGISTRY_HASH,
            policy_references=policy_references,
            tier_invariant_manifest_hash=TIER_INVARIANT_MANIFEST_HASH,
            preregistration_watermark=policy_event.event_hash,
            contract_hash=canonical_json_hash(content),
        )

        def replay_existing(
            event: ResearchEventEnvelope,
        ) -> RecordedResolvedEvaluationContractV1:
            if event.payload["contract_hash"] != contract.contract_hash:
                raise EventTransitionError("resolved contract run is already frozen")
            reference = event.payload["artifact_refs"][0]
            loaded = self.artifacts.read(
                str(reference["relative_path"]),
                expected_contract_hash=contract.contract_hash,
                expected_blob_hash=str(reference["artifact_hash"]),
            )
            expected = self.event_payload(loaded, reference)
            if event.entity_id != self.registration_id(run_id, loaded.contract_hash) or (
                canonical_json(event.to_dict()["payload"]) != canonical_json(expected)
            ):
                raise EventValidationError(
                    "existing resolved contract differs from replay"
                )
            artifact = ContentAddressedArtifact(
                semantic_hash=loaded.contract_hash,
                relative_path=str(reference["relative_path"]),
                blob_hash=str(reference["artifact_hash"]),
                media_type=str(reference["media_type"]),
            )
            return RecordedResolvedEvaluationContractV1(loaded, event, artifact)

        existing = [
            event for event in self.store.query_events(
                event_type="ResolvedEvaluationContractRegistered"
            ) if event.run_id == run_id
        ]
        if len(existing) > 1:
            raise EventTransitionError("multiple resolved contracts exist for run")
        if existing:
            return replay_existing(existing[0])
        run_events = [event for event in self.store.query_events() if event.run_id == run_id]
        if len(run_events) != 1 or run_events[0].event_hash != policy_event.event_hash:
            raced = [
                event
                for event in self.store.query_events(
                    event_type="ResolvedEvaluationContractRegistered"
                )
                if event.run_id == run_id
            ]
            if len(raced) == 1:
                return replay_existing(raced[0])
            raise EventTransitionError(
                "resolved contract must be the second run event before trials or data access"
            )
        artifact = self.artifacts.write(contract)
        event = self.store._append_producer_event(
            EventDraft(
                event_type="ResolvedEvaluationContractRegistered",
                entity_id=self.registration_id(run_id, contract.contract_hash),
                run_id=run_id,
                payload_schema_version="resolved_evaluation_contract_registered.v1",
                idempotency_key="resolved-evaluation-contract:" + run_id,
                payload=self.event_payload(contract, artifact.reference()),
            )
        )
        return RecordedResolvedEvaluationContractV1(contract, event, artifact)

    @staticmethod
    def registration_id(run_id: str, contract_hash: str) -> str:
        digest = canonical_json_hash(
            {
                "schema_version": "resolved_contract_registration_id.v1",
                "run_id": run_id,
                "contract_hash": contract_hash,
            }
        )
        return "resolved-contract-" + digest.removeprefix("sha256:")[:24]

    @staticmethod
    def event_payload(
        contract: ResolvedEvaluationContractV1,
        artifact_reference: Mapping[str, str],
    ) -> dict[str, Any]:
        return {
            "contract_id": ResolvedEvaluationContractServiceV1.registration_id(
                contract.run_id, contract.contract_hash
            ),
            "contract_hash": contract.contract_hash,
            "research_family_id": contract.research_family_id,
            "profile_template_hash": contract.profile_template.template_hash,
            "profile_id": contract.profile_template.profile_id,
            "profile_version": contract.profile_template.profile_version,
            "profile_authority_class": contract.profile_template.authority_class,
            "maximum_promotion": contract.profile_template.maximum_promotion,
            "evaluation_policy_event_hash": contract.evaluation_policy_event_hash,
            "evaluation_policy_bundle_hash": contract.evaluation_policy_bundle_hash,
            "producer_registry_hash": contract.producer_registry_hash,
            "tier_invariant_manifest_hash": contract.tier_invariant_manifest_hash,
            "producer_schema_version": CONTRACT_PRODUCER_SCHEMA,
            "producer_policy_hash": CONTRACT_PRODUCER_POLICY_HASH,
            "preregistration_watermark": contract.preregistration_watermark,
            "artifact_refs": [dict(artifact_reference)],
        }
