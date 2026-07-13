"""Fail-closed readiness and non-compensatory Activation governance V2."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Literal, Mapping

from src.alpha_foundry.activation.protocol_v2 import (
    DEFAULT_ACTIVATION_HASH_SPEC,
    PreregisteredActivationStatisticalProtocolV2,
    RecordedActivationProtocolV2,
)
from src.alpha_foundry.activation.coordinator_v2 import ActivationPairCoordinatorV2
from src.alpha_foundry.activation.pair_projector_v2 import ActivationEvidenceProjector
from src.alpha_foundry.activation.statistical_v2 import (
    ActivationStatisticalAnalyzerV2,
    RecordedActivationStatisticalArtifactV2,
)
from src.research_ledger.events import EventDraft, ResearchEventEnvelope, ResearchEventStore
from src.research_ledger.events import EventTransitionError
from src.research_ledger.events.artifacts import (
    AtomicContentAddressedArtifactWriter,
    validate_artifact_references,
)
from src.research_ledger.hash_utils import canonical_json_hash


@dataclass(frozen=True)
class ActivationReadinessV4:
    schema_version: Literal["activation_readiness.v4"]
    research_cycle_id: str
    production_candidate_factory_bound: bool
    quality_decision_v3_producer_bound: bool
    production_train_valid_input_bound: bool
    statistical_protocol_registered: bool
    provider_field_audits_sufficient: bool
    pair_schedule_frozen: bool
    same_factory_for_both_arms: bool
    source_replay_complete: bool
    no_final_forward_access: bool
    resource_isolation_verified: bool
    applicability_matrix_registered: bool
    governance_roles_separated: bool
    ready_for_pilot_outcome_access: bool
    blocker_codes: tuple[str, ...]
    readiness_hash: str

    def __post_init__(self) -> None:
        checks = tuple(
            getattr(self, name)
            for name in self.__dataclass_fields__
            if name not in {
                "schema_version",
                "research_cycle_id",
                "ready_for_pilot_outcome_access",
                "blocker_codes",
                "readiness_hash",
            }
        )
        if self.blocker_codes != tuple(sorted(set(self.blocker_codes))):
            raise ValueError("readiness blockers must be sorted and unique")
        if self.ready_for_pilot_outcome_access != (all(checks) and not self.blocker_codes):
            raise ValueError("readiness outcome must derive from every closed gate")
        if self.readiness_hash != DEFAULT_ACTIVATION_HASH_SPEC.hash_payload(
            "activation-readiness.v4", self._content_dict()
        ):
            raise ValueError("Activation readiness hash mismatch")

    def _content_dict(self) -> dict[str, Any]:
        return {
            name: (list(self.blocker_codes) if name == "blocker_codes" else getattr(self, name))
            for name in self.__dataclass_fields__
            if name != "readiness_hash"
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "readiness_hash": self.readiness_hash}


@dataclass(frozen=True, init=False)
class ActivationGovernanceDecisionV2:
    schema_version: Literal["activation_governance_decision.v2"]
    protocol_hash: str
    analysis_hash: str
    verdict: Literal["invalidated", "inconclusive", "rejected", "approved"]
    reason_codes: tuple[str, ...]
    official_search_policy: Literal[
        "flat_with_topology_shadow", "frozen_research_only_topology"
    ]
    active_research_only_influence: bool
    governance_hash: str
    _authority: object

    def __init__(self, *, _authority: object, **values: Any) -> None:
        if _authority is not _GOVERNANCE_AUTHORITY:
            raise TypeError("governance decisions must be service-minted")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_authority", _authority)
        self.__post_init__()

    def __post_init__(self) -> None:
        if self.reason_codes != tuple(sorted(set(self.reason_codes))) or not self.reason_codes:
            raise ValueError("governance requires exact sorted reasons")
        if self.active_research_only_influence != (self.verdict == "approved"):
            raise ValueError("only approved governance may enable research-only influence")
        if (self.official_search_policy == "frozen_research_only_topology") != (
            self.verdict == "approved"
        ):
            raise ValueError("official policy does not match governance verdict")
        if self.governance_hash != DEFAULT_ACTIVATION_HASH_SPEC.hash_payload(
            "activation-governance-decision.v2", self._content_dict()
        ):
            raise ValueError("governance hash mismatch")

    def _content_dict(self) -> dict[str, Any]:
        return {
            name: (list(self.reason_codes) if name == "reason_codes" else getattr(self, name))
            for name in self.__dataclass_fields__
            if name not in {"governance_hash", "_authority"}
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "governance_hash": self.governance_hash}

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ActivationGovernanceDecisionV2":
        expected = {name for name in cls.__dataclass_fields__ if name != "_authority"}
        if set(value) != expected or not isinstance(value["reason_codes"], list):
            raise ValueError("governance artifact has an invalid closed schema")
        return cls(
            _authority=_GOVERNANCE_AUTHORITY,
            **{
                **dict(value),
                "reason_codes": tuple(str(item) for item in value["reason_codes"]),
            },
        )


_GOVERNANCE_AUTHORITY = object()
_RECORDED_GOVERNANCE_AUTHORITY = object()
_GOVERNANCE_MEDIA_TYPE = "application/vnd.vibe.activation-governance-decision-v2+json"


@dataclass(frozen=True, init=False)
class RecordedActivationGovernanceDecisionV2:
    decision: ActivationGovernanceDecisionV2
    event: ResearchEventEnvelope
    artifact_hash: str
    artifact_ref: Mapping[str, str]
    source_event_hashes: tuple[str, ...]
    _authority: object

    def __init__(self, *, _authority: object, **values: Any) -> None:
        if _authority is not _RECORDED_GOVERNANCE_AUTHORITY:
            raise TypeError("governance decisions must be service-recorded")
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_authority", _authority)

    def verify_in(self, store: ResearchEventStore) -> ActivationGovernanceDecisionV2:
        if not isinstance(store, ResearchEventStore) or not store.verify_chain():
            raise EventTransitionError("governance decision requires a valid event chain")
        events = store.query_events()
        matches = [
            event
            for event in events
            if event.event_type == "ActivationGovernanceDecisionV2Recorded"
            and event.event_hash == self.event.event_hash
        ]
        if len(matches) != 1 or matches[0] != self.event:
            raise EventTransitionError("governance decision is not in the event store")
        event = matches[0]
        order = {candidate.event_hash: index for index, candidate in enumerate(events)}
        event_index = order[event.event_hash]
        if any(
            source not in order or order[source] >= event_index
            for source in self.source_event_hashes
        ):
            raise EventTransitionError("governance decision source prefix is incomplete")
        expected_payload = {
            "governance_decision_id": event.entity_id,
            "governance_hash": self.decision.governance_hash,
            "artifact_hash": self.artifact_hash,
            "protocol_hash": self.decision.protocol_hash,
            "analysis_hash": self.decision.analysis_hash,
            "verdict": self.decision.verdict,
            "source_event_hashes": list(self.source_event_hashes),
            "artifact_refs": [dict(self.artifact_ref)],
        }
        if event.payload_hash != canonical_json_hash(expected_payload):
            raise EventTransitionError("governance decision event binding differs")
        normalized = validate_artifact_references(
            store.artifact_root, [dict(self.artifact_ref)]
        )[0]
        digest = self.artifact_hash.removeprefix("sha256:")
        expected_path = f"activation-governance-decision-v2/{digest[:2]}/{digest}.json"
        if (
            normalized["relative_path"] != expected_path
            or normalized["media_type"] != _GOVERNANCE_MEDIA_TYPE
        ):
            raise EventTransitionError("governance decision artifact is noncanonical")

        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite governance artifact JSON: {value}")

        def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate governance artifact key")
                result[key] = value
            return result

        raw = json.loads(
            store.artifact_root.joinpath(*expected_path.split("/")).read_text(
                encoding="utf-8"
            ),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
        if (
            not isinstance(raw, Mapping)
            or set(raw) != {"schema_version", "decision", "artifact_hash"}
            or raw.get("schema_version")
            != "activation_governance_decision_artifact.v2"
            or raw.get("artifact_hash") != self.artifact_hash
            or canonical_json_hash(raw, exclude_keys=("artifact_hash",))
            != self.artifact_hash
            or not isinstance(raw.get("decision"), Mapping)
        ):
            raise EventTransitionError("governance decision artifact identity differs")
        rebuilt = ActivationGovernanceDecisionV2.from_mapping(raw["decision"])
        if rebuilt != self.decision:
            raise EventTransitionError("governance decision replay differs")
        return rebuilt


class ActivationGovernanceService:
    """Consume protocol + analyzer output; never recompute candidate evidence."""

    def __init__(self, store: ResearchEventStore) -> None:
        if not isinstance(store, ResearchEventStore):
            raise TypeError("Activation governance requires ResearchEventStore")
        self.store = store
        self.writer = AtomicContentAddressedArtifactWriter(
            store.artifact_root, max_bytes=2 * 1024 * 1024
        )

    def decide(
        self,
        *,
        registered_protocol: RecordedActivationProtocolV2,
        analysis: RecordedActivationStatisticalArtifactV2,
        readiness_event_hash: str,
    ) -> RecordedActivationGovernanceDecisionV2:
        if not isinstance(registered_protocol, RecordedActivationProtocolV2):
            raise TypeError("governance requires the registered statistical protocol")
        registered_protocol.verify_in(self.store)
        if not isinstance(analysis, RecordedActivationStatisticalArtifactV2):
            raise TypeError("governance requires recorded analyzer statistics")
        analysis.verify_in(self.store)
        analyzed = analysis.require_analysis()
        protocol = registered_protocol.protocol
        if analyzed.protocol_hash != protocol.protocol_hash:
            raise ValueError("governance protocol and analysis differ")
        readiness, readiness_pass = self._readiness(
            readiness_event_hash=readiness_event_hash,
            protocol=protocol,
            analysis=analysis,
        )

        invalid = set(analyzed.protocol_violation_codes)
        if not analyzed.source_authority_pass:
            invalid.add("INCOMPLETE_SOURCE_BINDING")
        if not readiness_pass:
            invalid.add("PREOUTCOME_READINESS_NOT_SATISFIED")
        if invalid:
            verdict = "invalidated"
            reasons = tuple(sorted(invalid))
        else:
            ni_values = (
                analyzed.safety_noninferiority_pass,
                analyzed.resource_noninferiority_pass,
                analyzed.failure_noninferiority_pass,
            )
            hard_ni_failure = any(value is False for value in ni_values)
            all_gates = (
                analyzed.adequate_power
                and analyzed.required_pair_complete
                and all(value is True for value in ni_values)
                and analyzed.diversity_coverage_pass is True
            )
            if (
                all_gates
                and analyzed.confidence_lower is not None
                and analyzed.confidence_lower > protocol.sesoi
            ):
                verdict = "approved"
                reasons = ("PRIMARY_LCB_ABOVE_SESOI_AND_ALL_GATES_PASS",)
            elif hard_ni_failure:
                verdict = "rejected"
                reasons = ("SAFETY_RESOURCE_OR_FAILURE_NI_HARD_FAIL",)
            elif (
                analyzed.adequate_power
                and analyzed.required_pair_complete
                and analyzed.confidence_upper is not None
                and analyzed.confidence_upper <= protocol.sesoi
            ):
                verdict = "rejected"
                reasons = ("PRIMARY_UCB_AT_OR_BELOW_SESOI",)
            else:
                verdict = "inconclusive"
                unresolved: set[str] = set()
                if not analyzed.adequate_power:
                    unresolved.add("POWER_INSUFFICIENT")
                if not analyzed.required_pair_complete:
                    unresolved.add("COMPLETION_INSUFFICIENT")
                if any(value is None for value in ni_values):
                    unresolved.add("NI_INTERVAL_UNRESOLVED")
                if (
                    analyzed.confidence_lower is None
                    or analyzed.confidence_upper is None
                    or (
                        analyzed.confidence_lower <= protocol.sesoi
                        < analyzed.confidence_upper
                    )
                ):
                    unresolved.add("CI_OVERLAPS_SESOI")
                if analyzed.diversity_coverage_pass is not True:
                    unresolved.add("DIVERSITY_COVERAGE_UNRESOLVED")
                reasons = tuple(sorted(unresolved or {"EVIDENCE_INCONCLUSIVE"}))
        values = {
            "schema_version": "activation_governance_decision.v2",
            "protocol_hash": protocol.protocol_hash,
            "analysis_hash": analyzed.analysis_hash,
            "verdict": verdict,
            "reason_codes": reasons,
            "official_search_policy": (
                "frozen_research_only_topology"
                if verdict == "approved"
                else "flat_with_topology_shadow"
            ),
            "active_research_only_influence": verdict == "approved",
        }
        decision = ActivationGovernanceDecisionV2(
            _authority=_GOVERNANCE_AUTHORITY,
            **values,
            governance_hash=DEFAULT_ACTIVATION_HASH_SPEC.hash_payload(
                "activation-governance-decision.v2",
                {**values, "reason_codes": list(reasons)},
            ),
        )
        return self._record_decision(
            decision=decision,
            run_id=registered_protocol.event.run_id,
            source_event_hashes=tuple(
                sorted(
                    {
                        registered_protocol.event.event_hash,
                        analysis.event.event_hash,
                        readiness.event_hash,
                    }
                )
            ),
        )

    def _readiness(
        self,
        *,
        readiness_event_hash: str,
        protocol: PreregisteredActivationStatisticalProtocolV2,
        analysis: RecordedActivationStatisticalArtifactV2,
    ) -> tuple[ResearchEventEnvelope, bool]:
        events = self.store.query_events()
        matches = [
            event
            for event in events
            if event.event_type == "ActivationReadinessV4Recorded"
            and event.event_hash == readiness_event_hash
        ]
        if len(matches) != 1:
            raise EventTransitionError("governance requires one exact readiness event")
        readiness = matches[0]
        order = {event.event_hash: index for index, event in enumerate(events)}
        by_hash = {event.event_hash: event for event in events}
        unresolved = list(analysis.source_event_hashes)
        outcome_ancestors: set[str] = set()
        source_closure_complete = True
        while unresolved:
            source = unresolved.pop()
            if source in outcome_ancestors:
                continue
            event = by_hash.get(source)
            if event is None:
                source_closure_complete = False
                continue
            outcome_ancestors.add(source)
            nested_sources = event.payload.get("source_event_hashes", [])
            if not isinstance(nested_sources, (list, tuple)) or not all(
                isinstance(item, str) for item in nested_sources
            ):
                source_closure_complete = False
                continue
            unresolved.extend(nested_sources)
        readiness_before_outcomes = all(
            order[readiness.event_hash] < order[source] for source in outcome_ancestors
        )
        nested = readiness.payload.get("readiness")
        passed = (
            readiness.payload.get("research_cycle_id") == protocol.research_cycle_id
            and readiness.payload.get("ready_for_pilot_outcome_access") is True
            and not readiness.payload.get("blocker_codes")
            and isinstance(nested, Mapping)
            and nested.get("ready_for_pilot_outcome_access") is True
            and not nested.get("blocker_codes")
            and source_closure_complete
            and readiness_before_outcomes
        )
        return readiness, passed

    def _record_decision(
        self,
        *,
        decision: ActivationGovernanceDecisionV2,
        run_id: str,
        source_event_hashes: tuple[str, ...],
    ) -> RecordedActivationGovernanceDecisionV2:
        content = {
            "schema_version": "activation_governance_decision_artifact.v2",
            "decision": decision.to_dict(),
        }
        artifact_hash = canonical_json_hash(content)
        artifact_payload = {**content, "artifact_hash": artifact_hash}
        artifact = self.writer.write_json(
            namespace="activation-governance-decision-v2",
            payload=artifact_payload,
            schema_version="activation_governance_decision_artifact.v2",
            semantic_hash_field="artifact_hash",
            closed_keys=frozenset(artifact_payload),
            media_type=_GOVERNANCE_MEDIA_TYPE,
        )
        identifier = "activation-governance-v2-" + decision.governance_hash[-24:]
        event = self.store._append_producer_event(
            EventDraft(
                event_type="ActivationGovernanceDecisionV2Recorded",
                entity_id=identifier,
                run_id=run_id,
                payload_schema_version="activation_governance_decision_recorded.v2",
                idempotency_key="activation-governance-v2:" + decision.governance_hash,
                payload={
                    "governance_decision_id": identifier,
                    "governance_hash": decision.governance_hash,
                    "artifact_hash": artifact_hash,
                    "protocol_hash": decision.protocol_hash,
                    "analysis_hash": decision.analysis_hash,
                    "verdict": decision.verdict,
                    "source_event_hashes": list(source_event_hashes),
                    "artifact_refs": [artifact.reference()],
                },
            )
        )
        return RecordedActivationGovernanceDecisionV2(
            _authority=_RECORDED_GOVERNANCE_AUTHORITY,
            decision=decision,
            event=event,
            artifact_hash=artifact_hash,
            artifact_ref=artifact.reference(),
            source_event_hashes=source_event_hashes,
        )

    def decide_from_mapping(self, value: Mapping[str, Any]) -> ActivationGovernanceDecisionV2:
        forbidden = {"report_json", "worker_summary", "statistics", "p_value", "score"}
        if forbidden.intersection(value):
            raise TypeError("governance cannot accept report, summary, or caller statistics")
        raise TypeError("governance accepts only typed protocol and analyzer evidence")


class ActivationReadinessServiceV4:
    def __init__(self, store: ResearchEventStore) -> None:
        if not isinstance(store, ResearchEventStore):
            raise TypeError("Activation readiness requires ResearchEventStore")
        self.store = store

    def assess(self, *, run_id: str, research_cycle_id: str) -> tuple[ActivationReadinessV4, ResearchEventEnvelope]:
        events = self.store.query_events()
        cycle = [event for event in events if event.run_id == run_id]
        by_hash = {event.event_hash: event for event in events}
        bindings = [event for event in cycle if event.event_type == "ProductionActivationCandidateFactoryV1Bound"]
        provider = [
            event
            for event in cycle
            if event.event_type == "ProviderAuthorityDecisionV1Recorded"
        ]
        protocols = [
            event
            for event in cycle
            if event.event_type == "ActivationStatisticalProtocolV2Registered"
            and event.payload["research_cycle_id"] == research_cycle_id
        ]
        matrices = [
            event
            for event in cycle
            if event.event_type == "ActivationApplicabilityMatrixV1Registered"
            and event.payload["research_cycle_id"] == research_cycle_id
        ]
        golden = [
            event
            for event in cycle
            if event.event_type == "ProductionGoldenSliceReadinessV1Recorded"
            and event.payload["research_cycle_id"] == research_cycle_id
        ]
        bundles = [
            event
            for event in cycle
            if event.event_type == "ProductionActivationRunInputBundleV1Registered"
            and event.payload["research_cycle_id"] == research_cycle_id
        ]
        schedules = [
            event
            for event in cycle
            if event.event_type == "ActivationPairExecutionScheduled"
        ]
        provider_ok = (
            len(provider) == 1
            and provider[0].payload["activation_eligible"] is True
            and provider[0].payload["authority_status"] == "verified_strict"
            and provider[0].payload["claim_scope_ceiling"] == "verified_strict"
        )
        golden_ok = (
            len(golden) == 1
            and golden[0].payload["ready"] is True
            and len(provider) == 1
            and golden[0].payload["provider_authority_decision_event_hash"]
            == provider[0].event_hash
        )
        bundle_ok = (
            len(bundles) == 1
            and golden_ok
            and bundles[0].payload["provider_authority_decision_event_hash"]
            == provider[0].event_hash
            and bundles[0].payload["golden_slice_readiness_event_hash"]
            == golden[0].event_hash
        )
        binding_refs_bundle = (
            len(bindings) == 1
            and len(bundles) == 1
            and bindings[0].payload["run_input_bundle_event_hash"]
            == bundles[0].event_hash
        )
        factory_ok = (
            binding_refs_bundle
            and not bindings[0].payload["blocker_codes"]
            and bindings[0].payload["returns_refs_only"] is True
        )
        quality_decision_ok = (
            factory_ok
            and bindings[0].payload["quality_decision_service"]
            == "src.alpha_quality.decision_v2.source_v3.QualityDecisionV3Service"
            and "QUALITY_DECISION_V3_PRODUCER_AUTHORITY_INCOMPLETE"
            not in bindings[0].payload["blocker_codes"]
        )
        schedule_ok = bool(schedules) and all(
            event.payload["arm_order"]
            in (["control", "treatment"], ["treatment", "control"])
            for event in schedules
        )
        replay_refs = {
            str(value)
            for event in (*provider, *golden, *bundles, *bindings)
            for key, value in event.payload.items()
            if key.endswith("_event_hash")
        }
        source_replay = (
            self.store.verify_chain()
            and len(protocols) == 1
            and len(matrices) == 1
            and len(provider) == 1
            and len(golden) == 1
            and len(bundles) == 1
            and len(bindings) == 1
            and replay_refs.issubset(by_hash)
        )
        checks = {
            "production_candidate_factory_bound": factory_ok,
            "quality_decision_v3_producer_bound": quality_decision_ok,
            "production_train_valid_input_bound": bundle_ok,
            "statistical_protocol_registered": len(protocols) == 1,
            "provider_field_audits_sufficient": provider_ok,
            "pair_schedule_frozen": schedule_ok,
            "same_factory_for_both_arms": (
                factory_ok
                and bool(bindings)
                and bindings[0].payload["same_factory_both_arms"] is True
                and bindings[0].payload["only_arm_difference"]
                == "retriever_policy_hash"
            ),
            "source_replay_complete": source_replay,
            "no_final_forward_access": not any(
                event.event_type.startswith(("Final", "Forward")) for event in cycle
            ),
            # The only existing isolated-worker v3 implementation is a closed
            # infrastructure probe.  No production-arm resource event exists,
            # so this gate must remain false rather than treating probe output
            # as effect evidence.
            "resource_isolation_verified": False,
            "applicability_matrix_registered": len(matrices) == 1,
            "governance_roles_separated": self._roles_are_separated(),
        }
        blockers = tuple(sorted(name.upper() for name, passed in checks.items() if not passed))
        content = {
            "schema_version": "activation_readiness.v4",
            "research_cycle_id": research_cycle_id,
            **checks,
            "ready_for_pilot_outcome_access": not blockers,
            "blocker_codes": list(blockers),
        }
        readiness = ActivationReadinessV4(
            schema_version="activation_readiness.v4",
            research_cycle_id=research_cycle_id,
            **checks,
            ready_for_pilot_outcome_access=not blockers,
            blocker_codes=blockers,
            readiness_hash=DEFAULT_ACTIVATION_HASH_SPEC.hash_payload(
                "activation-readiness.v4", content
            ),
        )
        readiness_id = "activation-readiness-v4-" + readiness.readiness_hash[-24:]
        event = self.store._append_producer_event(
            EventDraft(
                event_type="ActivationReadinessV4Recorded",
                entity_id=readiness_id,
                run_id=run_id,
                payload_schema_version="activation_readiness_recorded.v4",
                idempotency_key="activation-readiness-v4:" + research_cycle_id,
                payload={
                    "readiness_id": readiness_id,
                    "research_cycle_id": research_cycle_id,
                    "ready_for_pilot_outcome_access": readiness.ready_for_pilot_outcome_access,
                    "blocker_codes": list(readiness.blocker_codes),
                    "readiness_hash": readiness.readiness_hash,
                    "readiness": readiness.to_dict(),
                },
            )
        )
        return readiness, event

    @staticmethod
    def _roles_are_separated() -> bool:
        classes = (
            ActivationPairCoordinatorV2,
            ActivationEvidenceProjector,
            ActivationStatisticalAnalyzerV2,
            ActivationGovernanceService,
        )
        return (
            len({value.__module__ for value in classes}) == len(classes)
            and hasattr(ActivationPairCoordinatorV2, "mint_activation_decision")
            and not hasattr(ActivationEvidenceProjector, "decide")
            and not hasattr(ActivationStatisticalAnalyzerV2, "decide")
            and hasattr(ActivationGovernanceService, "decide")
        )


__all__ = [
    "ActivationGovernanceDecisionV2",
    "ActivationGovernanceService",
    "RecordedActivationGovernanceDecisionV2",
    "ActivationReadinessServiceV4",
    "ActivationReadinessV4",
]
