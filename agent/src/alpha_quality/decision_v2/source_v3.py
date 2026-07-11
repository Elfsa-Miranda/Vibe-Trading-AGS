"""Frozen source bundle and deterministic service for QualityDecision v3 events."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from src.alpha_foundry.artifacts import safe_artifact_path, safe_artifact_write_json
from src.alpha_quality.decision_v2.model import (
    AlphaQualityDecisionV2,
    DecisionEvidenceRecord,
    DecisionEvidenceRefs,
    EvidenceKind,
)
from src.alpha_quality.decision_v2.policy import DecisionV2Policy
from src.alpha_quality.decision_v2.repository import (
    DecisionEvidenceRepository,
    EvidenceResolutionError,
)
from src.alpha_quality.decision_v2.runner import QualityDecisionV2Runner
from src.alpha_quality.flags import ResolvedAGSFlags
from src.research_ledger.events import (
    EventDraft,
    EventTransitionError,
    ResearchEventEnvelope,
    ResearchEventStore,
)
from src.research_ledger.events.artifacts import hash_artifact
from src.research_ledger.hash_utils import canonical_json, canonical_json_hash, redact_secrets


_MAX_BUNDLE_BYTES = 2 * 1024 * 1024
_REF_KIND_BY_NAME: dict[str, EvidenceKind] = {
    "scorecard_hash": "scorecard",
    "execution_hash": "execution",
    "snapshot_hash": "snapshot",
    "ledger_watermark_hash": "ledger",
    "mechanism_evidence_hash": "mechanism",
    "complement_evidence_hash": "complement",
    "final_test_artifact_hash": "final_test",
    "forward_plan_hash": "forward_plan",
}


def decision_v2_policy_from_mapping(value: Mapping[str, object]) -> DecisionV2Policy:
    expected = {
        "schema_version",
        "policy_version",
        "require_snapshot",
        "require_execution",
        "require_mechanism",
        "require_complement",
        "rank_ic_score_scale",
    }
    if set(value) != expected or value["schema_version"] != "decision_v2_policy.v1":
        raise ValueError("quality decision bundle contains an invalid policy schema")
    policy_version = value["policy_version"]
    if not isinstance(policy_version, str):
        raise ValueError("quality decision policy version must be text")
    boolean_names = (
        "require_snapshot",
        "require_execution",
        "require_mechanism",
        "require_complement",
    )
    if any(not isinstance(value[name], bool) for name in boolean_names):
        raise ValueError("quality decision policy gates must be boolean")
    scale = value["rank_ic_score_scale"]
    if isinstance(scale, bool) or not isinstance(scale, (int, float)):
        raise ValueError("quality decision rank IC scale must be numeric")
    return DecisionV2Policy(
        schema_version="decision_v2_policy.v1",
        policy_version=policy_version,
        require_snapshot=value["require_snapshot"],  # type: ignore[arg-type]
        require_execution=value["require_execution"],  # type: ignore[arg-type]
        require_mechanism=value["require_mechanism"],  # type: ignore[arg-type]
        require_complement=value["require_complement"],  # type: ignore[arg-type]
        rank_ic_score_scale=float(scale),
    )


class FrozenDecisionEvidenceRepository(DecisionEvidenceRepository):
    """Read-only repository adapter over records embedded in one frozen bundle."""

    def __init__(self, records: tuple[DecisionEvidenceRecord, ...]) -> None:
        self._records = MappingProxyType(
            {record.evidence_hash: record for record in records}
        )
        if len(self._records) != len(records):
            raise ValueError("frozen decision evidence contains duplicate hashes")

    def resolve(
        self,
        evidence_hash: str,
        *,
        expected_kind: EvidenceKind,
        factor_spec_id: str,
    ) -> DecisionEvidenceRecord:
        record = self._records.get(evidence_hash)
        if record is None:
            raise EvidenceResolutionError("frozen decision evidence is unavailable")
        if record.evidence_kind != expected_kind or record.factor_spec_id != factor_spec_id:
            raise EvidenceResolutionError("frozen decision evidence binding is invalid")
        return record


@dataclass(frozen=True)
class QualityDecisionInputBundleV3:
    schema_version: str
    evidence_refs: DecisionEvidenceRefs
    policy_config: Mapping[str, object]
    evidence_records: tuple[DecisionEvidenceRecord, ...]
    bundle_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "quality_decision_input_bundle.v3":
            raise ValueError("unsupported quality decision input bundle")
        policy = decision_v2_policy_from_mapping(self.policy_config)
        object.__setattr__(self, "policy_config", MappingProxyType(policy.to_dict()))
        ordered = tuple(sorted(self.evidence_records, key=lambda record: record.evidence_hash))
        if ordered != self.evidence_records:
            raise ValueError("quality decision evidence records must be sorted")
        expected = {
            value: _REF_KIND_BY_NAME[name]
            for name, value in self.evidence_refs.to_dict().items()
            if name != "factor_spec_id" and value is not None
        }
        if len(expected) != len(self.evidence_refs.evidence_hashes):
            raise ValueError("one evidence hash cannot impersonate multiple evidence kinds")
        seen: set[str] = set()
        for record in ordered:
            if record.evidence_hash in seen:
                raise ValueError("quality decision evidence record is duplicated")
            seen.add(record.evidence_hash)
            if (
                expected.get(record.evidence_hash) != record.evidence_kind
                or record.factor_spec_id != self.evidence_refs.factor_spec_id
            ):
                raise ValueError("quality decision evidence record is not cited by refs")
        if self.bundle_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("quality decision input bundle hash mismatch")

    @classmethod
    def build(
        cls,
        *,
        refs: DecisionEvidenceRefs,
        policy: DecisionV2Policy,
        repository: DecisionEvidenceRepository,
    ) -> "QualityDecisionInputBundleV3":
        records: list[DecisionEvidenceRecord] = []
        for name, kind in _REF_KIND_BY_NAME.items():
            evidence_hash = getattr(refs, name)
            if evidence_hash is None:
                continue
            try:
                records.append(
                    repository.resolve(
                        evidence_hash,
                        expected_kind=kind,
                        factor_spec_id=refs.factor_spec_id,
                    )
                )
            except EvidenceResolutionError:
                continue
        ordered = tuple(sorted(records, key=lambda record: record.evidence_hash))
        content = {
            "schema_version": "quality_decision_input_bundle.v3",
            "evidence_refs": refs.to_dict(),
            "policy_config": policy.to_dict(),
            "evidence_records": [record.to_dict() for record in ordered],
        }
        return cls(
            schema_version="quality_decision_input_bundle.v3",
            evidence_refs=refs,
            policy_config=policy.to_dict(),
            evidence_records=ordered,
            bundle_hash=canonical_json_hash(content),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "QualityDecisionInputBundleV3":
        expected = {
            "schema_version", "evidence_refs", "policy_config",
            "evidence_records", "bundle_hash",
        }
        if (
            set(raw) != expected
            or not isinstance(raw["evidence_refs"], Mapping)
            or not isinstance(raw["policy_config"], Mapping)
            or not isinstance(raw["evidence_records"], list)
        ):
            raise ValueError("quality decision input bundle has an invalid closed schema")
        records: list[DecisionEvidenceRecord] = []
        for item in raw["evidence_records"]:
            if not isinstance(item, Mapping) or set(item) != {
                "schema_version", "evidence_kind", "factor_spec_id", "payload",
                "evidence_hash",
            }:
                raise ValueError("quality decision input bundle contains an invalid record")
            records.append(
                DecisionEvidenceRecord(
                    schema_version=item["schema_version"],
                    evidence_kind=item["evidence_kind"],
                    factor_spec_id=str(item["factor_spec_id"]),
                    payload=item["payload"],
                    evidence_hash=str(item["evidence_hash"]),
                )
            )
        return cls(
            schema_version=str(raw["schema_version"]),
            evidence_refs=DecisionEvidenceRefs.from_mapping(raw["evidence_refs"]),
            policy_config=dict(raw["policy_config"]),
            evidence_records=tuple(records),
            bundle_hash=str(raw["bundle_hash"]),
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "evidence_refs": self.evidence_refs.to_dict(),
            "policy_config": dict(self.policy_config),
            "evidence_records": [record.to_dict() for record in self.evidence_records],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "bundle_hash": self.bundle_hash}


class QualityDecisionInputArtifactStoreV3:
    media_type = "application/vnd.vibe.quality-decision-input-v3+json"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    @staticmethod
    def relative_path(bundle_hash: str) -> str:
        prefix, separator, digest = bundle_hash.partition(":")
        if prefix != "sha256" or separator != ":" or len(digest) != 64:
            raise ValueError("quality decision bundle hash is invalid")
        int(digest, 16)
        return f"quality-decision-input-v3/{digest[:2]}/{digest}.json"

    def write(self, bundle: QualityDecisionInputBundleV3) -> dict[str, str]:
        payload = bundle.to_dict()
        if canonical_json(payload) != canonical_json(redact_secrets(payload)):
            raise ValueError("quality decision source contains secret or private path data")
        if len(canonical_json(payload).encode("utf-8")) > _MAX_BUNDLE_BYTES:
            raise ValueError("quality decision input bundle exceeds byte budget")
        relative = self.relative_path(bundle.bundle_hash)
        target = safe_artifact_path(self.root, relative)
        if target.exists():
            if self.read(relative, expected_bundle_hash=bundle.bundle_hash) != bundle:
                raise ValueError("quality decision input artifact collision")
        else:
            safe_artifact_write_json(self.root, relative, payload)
        return {
            "relative_path": relative,
            "artifact_hash": hash_artifact(target),
            "media_type": self.media_type,
        }

    def read(
        self,
        relative_path: str,
        *,
        expected_bundle_hash: str,
    ) -> QualityDecisionInputBundleV3:
        target = safe_artifact_path(self.root, relative_path)
        raw = target.read_bytes()
        if len(raw) > _MAX_BUNDLE_BYTES:
            raise ValueError("quality decision input bundle exceeds byte budget")

        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite quality decision source: {value}")

        def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate quality decision source key")
                result[key] = value
            return result

        payload = json.loads(
            raw.decode("utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
        if not isinstance(payload, Mapping):
            raise ValueError("quality decision source must be an object")
        if canonical_json(payload) != canonical_json(redact_secrets(payload)):
            raise ValueError("quality decision source contains secret or private path data")
        bundle = QualityDecisionInputBundleV3.from_dict(payload)
        if bundle.bundle_hash != expected_bundle_hash:
            raise ValueError("quality decision source bundle identity mismatch")
        if self.relative_path(bundle.bundle_hash) != relative_path.replace("\\", "/"):
            raise ValueError("quality decision source path is not content addressed")
        return bundle


def source_bound_quality_decision_content(
    decision: AlphaQualityDecisionV2,
    *,
    input_bundle_hash: str,
) -> dict[str, Any]:
    return {
        "schema_version": "quality_decision_source_bound.v3",
        "quality_decision_hash": decision.decision_hash,
        "input_bundle_hash": input_bundle_hash,
        "factor_spec_id": decision.factor_spec_id,
        "decision": decision.decision,
        "tier": decision.tier,
        "policy_version": decision.policy_version,
        "policy_hash": decision.policy_hash,
        "evidence_hashes": list(decision.evidence_hashes),
        "reasons": list(decision.reasons),
        "warnings": list(decision.warnings),
        "caps": list(decision.caps),
        "limitations": list(decision.limitations),
        "within_tier_score": decision.within_tier_score,
        "forward_success_claim": decision.forward_success_claim,
    }


@dataclass(frozen=True)
class RecordedQualityDecisionV3:
    decision: AlphaQualityDecisionV2
    source_decision_hash: str
    event: ResearchEventEnvelope
    input_bundle: QualityDecisionInputBundleV3


class QualityDecisionV3Service:
    def __init__(
        self,
        *,
        store: ResearchEventStore,
        flags: ResolvedAGSFlags,
        policy: DecisionV2Policy,
        repository: DecisionEvidenceRepository,
    ) -> None:
        if not flags.enabled("VIBE_TRADING_DECISION_V2"):
            raise RuntimeError("Decision v2 capability is disabled")
        if flags.as_dict() != store.flags.as_dict():
            raise ValueError("quality decision service and store flags differ")
        self.store = store
        self.flags = flags
        self.policy = policy
        self.repository = repository
        self.artifacts = QualityDecisionInputArtifactStoreV3(store.artifact_root)

    def decide_and_record(
        self,
        refs: DecisionEvidenceRefs,
        *,
        run_id: str,
    ) -> RecordedQualityDecisionV3:
        if not self.store.query_events(
            event_type="FactorDefinitionRecorded",
            entity_id=refs.factor_spec_id,
        ):
            raise EventTransitionError("Decision v3 has no prior factor definition")
        bundle = QualityDecisionInputBundleV3.build(
            refs=refs,
            policy=self.policy,
            repository=self.repository,
        )
        decision = QualityDecisionV2Runner(
            flags=self.flags,
            policy=self.policy,
            repository=FrozenDecisionEvidenceRepository(bundle.evidence_records),
        ).run(bundle.evidence_refs)
        reference = self.artifacts.write(bundle)
        content = source_bound_quality_decision_content(
            decision,
            input_bundle_hash=bundle.bundle_hash,
        )
        source_hash = canonical_json_hash(content)
        identifier = "quality-decision-v3-" + source_hash.removeprefix("sha256:")[:24]
        event = self.store.append_event(
            EventDraft(
                event_type="QualityDecisionV3Recorded",
                entity_id=identifier,
                run_id=run_id,
                payload_schema_version="quality_decision_recorded.v3",
                idempotency_key="quality-decision-v3:" + source_hash,
                payload={
                    "decision_id": identifier,
                    "decision_hash": source_hash,
                    **{key: value for key, value in content.items() if key != "schema_version"},
                    "artifact_refs": [reference],
                },
            )
        )
        return RecordedQualityDecisionV3(decision, source_hash, event, bundle)


__all__ = [
    "FrozenDecisionEvidenceRepository", "QualityDecisionInputArtifactStoreV3",
    "QualityDecisionInputBundleV3", "QualityDecisionV3Service",
    "RecordedQualityDecisionV3", "decision_v2_policy_from_mapping",
    "source_bound_quality_decision_content",
]
