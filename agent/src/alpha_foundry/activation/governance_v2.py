"""Fail-closed readiness and non-compensatory Activation governance V2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from src.alpha_foundry.activation.protocol_v2 import (
    DEFAULT_ACTIVATION_HASH_SPEC,
    PreregisteredActivationStatisticalProtocolV2,
)
from src.alpha_foundry.activation.coordinator_v2 import ActivationPairCoordinatorV2
from src.alpha_foundry.activation.pair_projector_v2 import ActivationEvidenceProjector
from src.alpha_foundry.activation.statistical_v2 import ActivationStatisticalAnalysisV2
from src.alpha_foundry.activation.statistical_v2 import ActivationStatisticalAnalyzerV2
from src.research_ledger.events import EventDraft, ResearchEventEnvelope, ResearchEventStore


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


_GOVERNANCE_AUTHORITY = object()


class ActivationGovernanceService:
    """Consume protocol + analyzer output; never recompute candidate evidence."""

    def decide(
        self,
        *,
        protocol: PreregisteredActivationStatisticalProtocolV2,
        analysis: ActivationStatisticalAnalysisV2,
    ) -> ActivationGovernanceDecisionV2:
        if not isinstance(protocol, PreregisteredActivationStatisticalProtocolV2):
            raise TypeError("governance requires the registered statistical protocol")
        if not isinstance(analysis, ActivationStatisticalAnalysisV2):
            raise TypeError("governance requires analyzer-minted statistics")
        if analysis.protocol_hash != protocol.protocol_hash:
            raise ValueError("governance protocol and analysis differ")

        invalid = set(analysis.protocol_violation_codes)
        if not analysis.source_authority_pass:
            invalid.add("INCOMPLETE_SOURCE_BINDING")
        if invalid:
            verdict = "invalidated"
            reasons = tuple(sorted(invalid))
        else:
            ni_values = (
                analysis.safety_noninferiority_pass,
                analysis.resource_noninferiority_pass,
                analysis.failure_noninferiority_pass,
            )
            hard_ni_failure = any(value is False for value in ni_values)
            all_gates = (
                analysis.adequate_power
                and analysis.required_pair_complete
                and all(value is True for value in ni_values)
                and analysis.diversity_coverage_pass is True
            )
            if (
                all_gates
                and analysis.confidence_lower is not None
                and analysis.confidence_lower > protocol.sesoi
            ):
                verdict = "approved"
                reasons = ("PRIMARY_LCB_ABOVE_SESOI_AND_ALL_GATES_PASS",)
            elif hard_ni_failure:
                verdict = "rejected"
                reasons = ("SAFETY_RESOURCE_OR_FAILURE_NI_HARD_FAIL",)
            elif (
                analysis.adequate_power
                and analysis.required_pair_complete
                and analysis.confidence_upper is not None
                and analysis.confidence_upper <= protocol.sesoi
            ):
                verdict = "rejected"
                reasons = ("PRIMARY_UCB_AT_OR_BELOW_SESOI",)
            else:
                verdict = "inconclusive"
                unresolved: set[str] = set()
                if not analysis.adequate_power:
                    unresolved.add("POWER_INSUFFICIENT")
                if not analysis.required_pair_complete:
                    unresolved.add("COMPLETION_INSUFFICIENT")
                if any(value is None for value in ni_values):
                    unresolved.add("NI_INTERVAL_UNRESOLVED")
                if (
                    analysis.confidence_lower is None
                    or analysis.confidence_upper is None
                    or (
                        analysis.confidence_lower <= protocol.sesoi
                        < analysis.confidence_upper
                    )
                ):
                    unresolved.add("CI_OVERLAPS_SESOI")
                if analysis.diversity_coverage_pass is not True:
                    unresolved.add("DIVERSITY_COVERAGE_UNRESOLVED")
                reasons = tuple(sorted(unresolved or {"EVIDENCE_INCONCLUSIVE"}))
        values = {
            "schema_version": "activation_governance_decision.v2",
            "protocol_hash": protocol.protocol_hash,
            "analysis_hash": analysis.analysis_hash,
            "verdict": verdict,
            "reason_codes": reasons,
            "official_search_policy": (
                "frozen_research_only_topology"
                if verdict == "approved"
                else "flat_with_topology_shadow"
            ),
            "active_research_only_influence": verdict == "approved",
        }
        return ActivationGovernanceDecisionV2(
            _authority=_GOVERNANCE_AUTHORITY,
            **values,
            governance_hash=DEFAULT_ACTIVATION_HASH_SPEC.hash_payload(
                "activation-governance-decision.v2",
                {**values, "reason_codes": list(reasons)},
            ),
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
    "ActivationReadinessServiceV4",
    "ActivationReadinessV4",
]
