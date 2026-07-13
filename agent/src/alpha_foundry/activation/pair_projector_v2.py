"""Exact-event pair evidence projection for Activation Enablement V2."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Mapping

from src.alpha_foundry.activation.run_source_v3 import (
    FormalActivationRunSourceAuditorV3,
    FormalActivationRunSourceV3,
)
from src.research_ledger.events import (
    EventDraft,
    EventTransitionError,
    ResearchEventEnvelope,
    ResearchEventStore,
)
from src.research_ledger.events.artifacts import (
    AtomicContentAddressedArtifactWriter,
    validate_artifact_references,
)
from src.research_ledger.hash_utils import canonical_json_hash


_ZERO_HASH = "sha256:" + "0" * 64
_PAIR_PROJECTION_AUTHORITY = object()
_PAIR_PROJECTION_SCHEMA = "activation_pair_projection_artifact.v2"
_PAIR_PROJECTION_MEDIA_TYPE = "application/vnd.vibe.activation-pair-projection-v2+json"
_PAIR_PROJECTION_KEYS = frozenset(
    {
        "schema_version",
        "evidence",
        "flat_refs",
        "topology_refs",
        "projection_hash",
    }
)


def _is_hash(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and len(value) == 71
        and all(character in "0123456789abcdef" for character in value[7:])
        and value != _ZERO_HASH
    )


@dataclass(frozen=True)
class ActivationArmEventRefsV2:
    retrieval_authority_event_hashes: tuple[str, ...]
    terminal_event_hashes: tuple[str, ...]
    evaluation_event_hashes: tuple[str, ...]
    quality_decision_event_hashes: tuple[str, ...]
    terminal_dossier_event_hashes: tuple[str, ...]

    def __post_init__(self) -> None:
        for name, values in self.__dict__.items():
            if values != tuple(sorted(set(values))):
                raise ValueError("arm event refs must be sorted and unique")
            if name in {
                "retrieval_authority_event_hashes",
                "terminal_event_hashes",
            } and not values:
                raise ValueError("arm retrieval and terminal refs cannot be empty")
            if any(not _is_hash(value) for value in values):
                raise ValueError("arm event refs require non-zero canonical hashes")

    def to_dict(self) -> dict[str, list[str]]:
        return {name: list(values) for name, values in self.__dict__.items()}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ActivationArmEventRefsV2":
        expected = set(cls.__dataclass_fields__)
        if set(value) != expected or any(not isinstance(value[name], list) for name in expected):
            raise ValueError("arm event refs artifact has an invalid closed schema")
        return cls(
            **{
                name: tuple(str(item) for item in value[name])
                for name in expected
            }
        )

    def all_hashes(self) -> tuple[str, ...]:
        return tuple(sorted({value for values in self.__dict__.values() for value in values}))


@dataclass(frozen=True)
class ActivationPairEvidenceV2:
    schema_version: str
    plan_hash: str
    pair_id: str
    run_group_id: str
    candidate_budget: int
    flat_source_audit_hash: str
    topology_source_audit_hash: str
    flat_qualified_yield: int
    topology_qualified_yield: int
    normalized_yield_difference: float
    flat_terminal_count: int
    topology_terminal_count: int
    flat_failure_count: int
    topology_failure_count: int
    candidate_set_jaccard: float
    changed_selection_rate: float
    safety_noninferiority_pass: bool | None
    resource_noninferiority_pass: bool | None
    source_failure_codes: tuple[str, ...]
    source_complete: bool
    evidence_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "activation_pair_evidence.v2":
            raise ValueError("unsupported Activation pair evidence schema")
        if self.candidate_budget < 1:
            raise ValueError("pair candidate budget must be positive")
        if self.source_failure_codes != tuple(sorted(set(self.source_failure_codes))):
            raise ValueError("pair source failures must be sorted and unique")
        if self.source_complete != (not self.source_failure_codes):
            raise ValueError("pair source completeness must derive from failures")
        for value in (
            self.normalized_yield_difference,
            self.candidate_set_jaccard,
            self.changed_selection_rate,
        ):
            if not math.isfinite(value):
                raise ValueError("pair evidence contains non-finite statistics")
        if not 0.0 <= self.candidate_set_jaccard <= 1.0:
            raise ValueError("candidate-set Jaccard is invalid")
        if not 0.0 <= self.changed_selection_rate <= 1.0:
            raise ValueError("changed-selection rate is invalid")
        if self.evidence_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("pair evidence hash mismatch")

    def _content_dict(self) -> dict[str, Any]:
        return {
            name: (list(self.source_failure_codes) if name == "source_failure_codes" else getattr(self, name))
            for name in self.__dataclass_fields__
            if name != "evidence_hash"
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "evidence_hash": self.evidence_hash}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ActivationPairEvidenceV2":
        expected = set(cls.__dataclass_fields__)
        if set(value) != expected or not isinstance(value["source_failure_codes"], list):
            raise ValueError("pair evidence artifact has an invalid closed schema")
        return cls(
            **{
                **dict(value),
                "source_failure_codes": tuple(
                    str(item) for item in value["source_failure_codes"]
                ),
            }
        )


@dataclass(frozen=True, init=False)
class RecordedActivationPairEvidenceV2:
    """Projector-minted evidence bound to one protected event and artifact."""

    evidence: ActivationPairEvidenceV2
    event: ResearchEventEnvelope
    projection_hash: str
    flat_refs: ActivationArmEventRefsV2
    topology_refs: ActivationArmEventRefsV2
    artifact_ref: Mapping[str, str]
    _authority: object

    def __init__(self, *, _authority: object, **values: Any) -> None:
        if _authority is not _PAIR_PROJECTION_AUTHORITY:
            raise TypeError("recorded pair evidence must be projector-minted")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_authority", _authority)

    def verify_in(self, store: ResearchEventStore) -> ActivationPairEvidenceV2:
        if not isinstance(store, ResearchEventStore) or not store.verify_chain():
            raise EventTransitionError("pair evidence requires one valid event store")
        matches = [
            event
            for event in store.query_events(
                event_type="ActivationPairEvidenceV2Recorded"
            )
            if event.event_hash == self.event.event_hash
        ]
        if len(matches) != 1 or matches[0] != self.event:
            raise EventTransitionError("pair evidence event is not in the analyzer store")
        event = matches[0]
        expected_sources = tuple(
            sorted(set(self.flat_refs.all_hashes() + self.topology_refs.all_hashes()))
        )
        ordered_events = store.query_events()
        event_order = {
            candidate.event_hash: index for index, candidate in enumerate(ordered_events)
        }
        projection_index = event_order[event.event_hash]
        if any(
            source_hash not in event_order
            or event_order[source_hash] >= projection_index
            for source_hash in expected_sources
        ):
            raise EventTransitionError("pair evidence source event prefix is incomplete")
        expected_payload = {
            "pair_evidence_id": event.entity_id,
            "pair_evidence_hash": self.evidence.evidence_hash,
            "projection_hash": self.projection_hash,
            "plan_hash": self.evidence.plan_hash,
            "pair_id": self.evidence.pair_id,
            "run_group_id": self.evidence.run_group_id,
            "flat_source_audit_hash": self.evidence.flat_source_audit_hash,
            "topology_source_audit_hash": self.evidence.topology_source_audit_hash,
            "source_event_hashes": list(expected_sources),
            "source_failure_codes": list(self.evidence.source_failure_codes),
            "source_complete": self.evidence.source_complete,
            "artifact_refs": [dict(self.artifact_ref)],
        }
        if (
            event.payload_hash != canonical_json_hash(expected_payload)
            or event.run_id != self.evidence.run_group_id
        ):
            raise EventTransitionError("pair evidence event binding differs")
        raw = self._read_artifact(store)
        expected_artifact = {
            "schema_version": _PAIR_PROJECTION_SCHEMA,
            "evidence": self.evidence.to_dict(),
            "flat_refs": self.flat_refs.to_dict(),
            "topology_refs": self.topology_refs.to_dict(),
            "projection_hash": self.projection_hash,
        }
        if raw != expected_artifact:
            raise EventTransitionError("pair evidence artifact binding differs")
        rebuilt = ActivationPairEvidenceV2.from_mapping(raw["evidence"])
        if rebuilt != self.evidence:
            raise EventTransitionError("pair evidence artifact cannot rebuild evidence")
        return rebuilt

    def _read_artifact(self, store: ResearchEventStore) -> Mapping[str, Any]:
        normalized = validate_artifact_references(
            store.artifact_root, [dict(self.artifact_ref)]
        )[0]
        digest = self.projection_hash.removeprefix("sha256:")
        expected_path = f"activation-pair-projection-v2/{digest[:2]}/{digest}.json"
        if (
            normalized["relative_path"] != expected_path
            or normalized["media_type"] != _PAIR_PROJECTION_MEDIA_TYPE
        ):
            raise EventTransitionError("pair evidence artifact reference is noncanonical")

        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite pair evidence JSON: {value}")

        def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate pair evidence artifact key")
                result[key] = value
            return result

        target = store.artifact_root.joinpath(*expected_path.split("/"))
        raw = json.loads(
            target.read_text(encoding="utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
        if not isinstance(raw, Mapping) or set(raw) != _PAIR_PROJECTION_KEYS:
            raise EventTransitionError("pair evidence artifact schema differs")
        if (
            raw.get("schema_version") != _PAIR_PROJECTION_SCHEMA
            or raw.get("projection_hash") != self.projection_hash
            or canonical_json_hash(raw, exclude_keys=("projection_hash",))
            != self.projection_hash
        ):
            raise EventTransitionError("pair evidence artifact identity differs")
        return raw


class ActivationEvidenceProjector:
    """Delegate arm authority to Run Source v3, then derive pair metrics."""

    def __init__(self, store: ResearchEventStore) -> None:
        if not isinstance(store, ResearchEventStore):
            raise TypeError("Activation projector requires ResearchEventStore")
        self.store = store
        self.run_source = FormalActivationRunSourceAuditorV3(store)
        self.writer = AtomicContentAddressedArtifactWriter(
            store.artifact_root, max_bytes=2 * 1024 * 1024
        )

    def project_pair(
        self,
        *,
        plan_hash: str,
        pair_id: str,
        run_group_id: str,
        candidate_budget: int,
        flat_refs: ActivationArmEventRefsV2,
        topology_refs: ActivationArmEventRefsV2,
    ) -> RecordedActivationPairEvidenceV2:
        if not isinstance(flat_refs, ActivationArmEventRefsV2) or not isinstance(
            topology_refs, ActivationArmEventRefsV2
        ):
            raise TypeError("pair projection accepts only typed exact event refs")
        flat = self._audit(
            plan_hash, pair_id, run_group_id, "flat", candidate_budget, flat_refs
        )
        topology = self._audit(
            plan_hash,
            pair_id,
            run_group_id,
            "topology",
            candidate_budget,
            topology_refs,
        )
        evidence = self._derive(
            flat=flat,
            topology=topology,
            candidate_budget=candidate_budget,
        )
        projection_content = {
            "schema_version": _PAIR_PROJECTION_SCHEMA,
            "evidence": evidence.to_dict(),
            "flat_refs": flat_refs.to_dict(),
            "topology_refs": topology_refs.to_dict(),
        }
        projection_hash = canonical_json_hash(projection_content)
        artifact_payload = {**projection_content, "projection_hash": projection_hash}
        artifact = self.writer.write_json(
            namespace="activation-pair-projection-v2",
            payload=artifact_payload,
            schema_version=_PAIR_PROJECTION_SCHEMA,
            semantic_hash_field="projection_hash",
            closed_keys=_PAIR_PROJECTION_KEYS,
            media_type=_PAIR_PROJECTION_MEDIA_TYPE,
        )
        identifier = "activation-pair-evidence-v2-" + projection_hash[-24:]
        event = self.store._append_producer_event(
            EventDraft(
                event_type="ActivationPairEvidenceV2Recorded",
                entity_id=identifier,
                run_id=run_group_id,
                payload_schema_version="activation_pair_evidence_recorded.v2",
                idempotency_key="activation-pair-evidence-v2:" + projection_hash,
                payload={
                    "pair_evidence_id": identifier,
                    "pair_evidence_hash": evidence.evidence_hash,
                    "projection_hash": projection_hash,
                    "plan_hash": evidence.plan_hash,
                    "pair_id": evidence.pair_id,
                    "run_group_id": evidence.run_group_id,
                    "flat_source_audit_hash": evidence.flat_source_audit_hash,
                    "topology_source_audit_hash": evidence.topology_source_audit_hash,
                    "source_event_hashes": list(
                        sorted(
                            set(
                                flat_refs.all_hashes()
                                + topology_refs.all_hashes()
                            )
                        )
                    ),
                    "source_failure_codes": list(evidence.source_failure_codes),
                    "source_complete": evidence.source_complete,
                    "artifact_refs": [artifact.reference()],
                },
            )
        )
        return RecordedActivationPairEvidenceV2(
            _authority=_PAIR_PROJECTION_AUTHORITY,
            evidence=evidence,
            event=event,
            projection_hash=projection_hash,
            flat_refs=flat_refs,
            topology_refs=topology_refs,
            artifact_ref=artifact.reference(),
        )

    def _audit(
        self,
        plan_hash: str,
        pair_id: str,
        run_group_id: str,
        arm: str,
        candidate_budget: int,
        refs: ActivationArmEventRefsV2,
    ) -> FormalActivationRunSourceV3:
        return self.run_source.audit(
            plan_hash=plan_hash,
            pair_id=pair_id,
            run_group_id=run_group_id,
            arm=arm,  # type: ignore[arg-type]
            candidate_budget=candidate_budget,
            retrieval_authority_event_hashes=refs.retrieval_authority_event_hashes,
            terminal_event_hashes=refs.terminal_event_hashes,
            evaluation_event_hashes=refs.evaluation_event_hashes,
            quality_decision_event_hashes=refs.quality_decision_event_hashes,
            terminal_dossier_event_hashes=refs.terminal_dossier_event_hashes,
        )

    @staticmethod
    def _derive(
        *,
        flat: FormalActivationRunSourceV3,
        topology: FormalActivationRunSourceV3,
        candidate_budget: int,
    ) -> ActivationPairEvidenceV2:
        failures = set(flat.source_failure_codes) | set(topology.source_failure_codes)
        if flat.plan_hash != topology.plan_hash or flat.pair_id != topology.pair_id:
            failures.add("PAIR_ARM_IDENTITY_MISMATCH")
        flat_candidates = set(flat.derived_candidate_ids)
        topology_candidates = set(topology.derived_candidate_ids)
        union = flat_candidates | topology_candidates
        intersection = flat_candidates & topology_candidates
        jaccard = len(intersection) / len(union) if union else 1.0
        changed = 1.0 - jaccard
        flat_yield = len(flat.derived_effective_candidate_ids)
        topology_yield = len(topology.derived_effective_candidate_ids)
        flat_counts = dict(flat.derived_terminal_status_counts)
        topology_counts = dict(topology.derived_terminal_status_counts)
        failure_names = {
            "reject",
            "skip",
            "invalid",
            "duplicate",
            "timeout",
            "error",
            "infrastructure_failure",
        }
        content: dict[str, Any] = {
            "schema_version": "activation_pair_evidence.v2",
            "plan_hash": flat.plan_hash,
            "pair_id": flat.pair_id,
            "run_group_id": flat.run_group_id,
            "candidate_budget": candidate_budget,
            "flat_source_audit_hash": flat.audit_hash,
            "topology_source_audit_hash": topology.audit_hash,
            "flat_qualified_yield": flat_yield,
            "topology_qualified_yield": topology_yield,
            "normalized_yield_difference": (
                (topology_yield - flat_yield) / candidate_budget
            ),
            "flat_terminal_count": len(flat.derived_candidate_ids),
            "topology_terminal_count": len(topology.derived_candidate_ids),
            "flat_failure_count": sum(flat_counts.get(name, 0) for name in failure_names),
            "topology_failure_count": sum(
                topology_counts.get(name, 0) for name in failure_names
            ),
            "candidate_set_jaccard": jaccard,
            "changed_selection_rate": changed,
            "safety_noninferiority_pass": None,
            "resource_noninferiority_pass": None,
            "source_failure_codes": sorted(failures),
            "source_complete": not failures,
        }
        typed = dict(content)
        typed["source_failure_codes"] = tuple(content["source_failure_codes"])
        return ActivationPairEvidenceV2(
            **typed,  # type: ignore[arg-type]
            evidence_hash=canonical_json_hash(content),
        )


__all__ = [
    "ActivationArmEventRefsV2",
    "ActivationEvidenceProjector",
    "ActivationPairEvidenceV2",
    "RecordedActivationPairEvidenceV2",
]
