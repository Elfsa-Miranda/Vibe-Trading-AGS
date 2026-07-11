"""Research-only active retriever capability, gated by immutable evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

from src.alpha_foundry.activation.artifacts import ActivationArtifactStore
from src.alpha_quality.flags import ResolvedAGSFlags
from src.research_ledger.events import ResearchEventEnvelope, ResearchEventStore


@dataclass(frozen=True)
class ActivationCompatibility:
    code_hash: str
    generator_hash: str
    grammar_hash: str
    treatment_policy_hash: str
    decision_policy_hash: str
    train_snapshot_hash: str
    valid_snapshot_hash: str


@dataclass(frozen=True)
class RetrieverModeResolution:
    mode: Literal["flat", "shadow", "active_research_only"]
    reason: str
    decision_hash: str | None = None


class ActiveRetrieverCapability:
    """Resolve candidate order only; this object has no quality or live-trading API."""

    def __init__(self, resolution: RetrieverModeResolution) -> None:
        if resolution.mode != "active_research_only":
            raise TypeError("active retriever capability requires approved compatible evidence")
        self.resolution = resolution

    def choose(
        self,
        *,
        flat_candidate_ids: tuple[str, ...],
        topology_candidate_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        del flat_candidate_ids, topology_candidate_ids
        raise RuntimeError(
            "source-bound topology generator-consumption evidence is required"
        )


class ActiveRetrieverResolver:
    def __init__(
        self,
        artifact_store: ActivationArtifactStore,
        *,
        event_store: ResearchEventStore | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.event_store = event_store

    def resolve(
        self,
        *,
        flags: ResolvedAGSFlags,
        plan_hash: str | None,
        result_hash: str | None,
        decision_hash: str | None,
        compatibility: ActivationCompatibility,
    ) -> RetrieverModeResolution:
        required_shadow = (
            "VIBE_TRADING_ALPHA_FOUNDRY",
            "VIBE_TRADING_RESEARCH_EVENTS",
            "VIBE_TRADING_FACTOR_DAG",
            "VIBE_TRADING_PROCESS_MEMORY",
            "VIBE_TRADING_TOPOLOGY_RETRIEVER",
        )
        if any(not flags.enabled(name) for name in required_shadow):
            return RetrieverModeResolution("flat", "TOPOLOGY_RETRIEVER_DISABLED")
        if not flags.enabled("VIBE_TRADING_TOPOLOGY_RETRIEVER_ACTIVE"):
            return RetrieverModeResolution("shadow", "ACTIVE_CAPABILITY_DISABLED")
        if not plan_hash or not result_hash or not decision_hash:
            return RetrieverModeResolution("shadow", "APPROVED_ARTIFACT_SET_MISSING")
        try:
            plan = self.artifact_store.get("plan", plan_hash)
            result = self.artifact_store.get("result", result_hash)
            decision = self.artifact_store.get("decision", decision_hash)
        except (OSError, TypeError, ValueError):
            return RetrieverModeResolution("shadow", "ACTIVATION_ARTIFACT_INVALID")
        if (
            result.get("schema_version") != "activation_experiment_result.v2"
            or decision.get("schema_version") != "retriever_activation_decision.v2"
            or result.get("run_provenance_schema_version")
            != "activation_run_source.v2"
        ):
            return RetrieverModeResolution(
                "shadow",
                "SOURCE_BOUND_ACTIVATION_V2_REQUIRED",
                decision_hash,
            )
        if decision.get("verdict") != "approved" or decision.get("active_research_only") is not True:
            return RetrieverModeResolution("shadow", "ACTIVATION_NOT_APPROVED", decision_hash)
        if decision.get("plan_hash") != plan_hash or decision.get("result_hash") != result_hash:
            return RetrieverModeResolution("shadow", "ACTIVATION_ARTIFACT_LINK_MISMATCH", decision_hash)
        if result.get("plan_hash") != plan_hash or result.get("replayable") is not True:
            return RetrieverModeResolution("shadow", "ACTIVATION_RESULT_NOT_REPLAYABLE", decision_hash)
        if result.get("invalidation_reasons"):
            return RetrieverModeResolution("shadow", "ACTIVATION_RESULT_INVALIDATED", decision_hash)
        if self.event_store is None or not self.event_store.verify_chain():
            return RetrieverModeResolution("shadow", "ACTIVATION_LEDGER_AUTHORITY_MISSING", decision_hash)
        evidence = self.event_store.query_events()
        required_events = {
            "ActivationPlanRegistered": plan_hash,
            "ActivationResultRecorded": result_hash,
            "RetrieverActivationDecisionRecorded": decision_hash,
        }
        for event_type, expected_hash in required_events.items():
            hash_field = {
                "ActivationPlanRegistered": "plan_hash",
                "ActivationResultRecorded": "result_hash",
                "RetrieverActivationDecisionRecorded": "decision_hash",
            }[event_type]
            if not any(
                event.event_type == event_type
                and event.payload.get(hash_field) == expected_hash
                for event in evidence
            ):
                return RetrieverModeResolution("shadow", "ACTIVATION_LEDGER_EVIDENCE_MISSING", decision_hash)
        source_failure = self._source_evidence_failure(result, evidence)
        if source_failure is not None:
            return RetrieverModeResolution("shadow", source_failure, decision_hash)
        consumption_failure = self._generation_consumption_failure(result, evidence)
        if consumption_failure is not None:
            return RetrieverModeResolution(
                "shadow", consumption_failure, decision_hash
            )
        if not self._approval_replays(plan, result, decision):
            return RetrieverModeResolution("shadow", "ACTIVATION_DECISION_REPLAY_FAILED", decision_hash)
        provenance = plan.get("provenance")
        if not isinstance(provenance, Mapping):
            return RetrieverModeResolution("shadow", "ACTIVATION_PROVENANCE_MISSING", decision_hash)
        expected = {
            "code_hash": compatibility.code_hash,
            "generator_hash": compatibility.generator_hash,
            "grammar_hash": compatibility.grammar_hash,
            "treatment_policy_hash": compatibility.treatment_policy_hash,
            "train_snapshot_hash": compatibility.train_snapshot_hash,
            "valid_snapshot_hash": compatibility.valid_snapshot_hash,
        }
        if any(provenance.get(name) != value for name, value in expected.items()):
            return RetrieverModeResolution("shadow", "ACTIVATION_PROVENANCE_MISMATCH", decision_hash)
        if decision.get("policy_hash") != compatibility.decision_policy_hash:
            return RetrieverModeResolution("shadow", "ACTIVATION_POLICY_MISMATCH", decision_hash)
        return RetrieverModeResolution("active_research_only", "APPROVED_COMPATIBLE", decision_hash)

    @staticmethod
    def _source_evidence_failure(
        result: Mapping[str, object],
        events: list[ResearchEventEnvelope],
    ) -> str | None:
        audit_hashes = result.get("run_source_audit_event_hashes")
        resource_hashes = result.get("resource_evidence_event_hashes")
        if (
            not isinstance(audit_hashes, list)
            or not isinstance(resource_hashes, list)
            or not audit_hashes
            or not resource_hashes
            or any(not isinstance(item, str) for item in audit_hashes)
            or any(not isinstance(item, str) for item in resource_hashes)
            or len(audit_hashes) != len(set(audit_hashes))
            or len(resource_hashes) != len(set(resource_hashes))
        ):
            return "ACTIVATION_RUN_SOURCE_EVIDENCE_MISSING"

        by_hash = {event.event_hash: event for event in events}
        order = {
            event.event_hash: index for index, event in enumerate(events)
        }
        result_events = [
            event for event in events
            if event.event_type == "ActivationResultRecorded"
            and event.payload.get("result_hash") == result.get("result_hash")
        ]
        if len(result_events) != 1:
            return "ACTIVATION_LEDGER_EVIDENCE_MISSING"
        result_order = order[result_events[0].event_hash]
        plan_hash = result.get("plan_hash")
        run_events = [
            event for event in events
            if event.event_type == "ActivationRunRecorded"
            and event.payload.get("plan_hash") == plan_hash
        ]
        manifest_hashes = {
            str(event.payload["manifest_hash"]) for event in run_events
        }
        audit_matches = [by_hash.get(str(event_hash)) for event_hash in audit_hashes]
        resource_matches = [
            by_hash.get(str(event_hash)) for event_hash in resource_hashes
        ]
        if (
            not manifest_hashes
            or any(event is None for event in audit_matches + resource_matches)
            or any(
                event.event_type != "ActivationRunSourceAudited"
                or event.payload.get("plan_hash") != plan_hash
                for event in audit_matches if event is not None
            )
            or any(
                event.event_type != "ActivationResourceMeasured"
                or event.payload.get("plan_hash") != plan_hash
                for event in resource_matches if event is not None
            )
        ):
            return "ACTIVATION_RUN_SOURCE_EVIDENCE_MISSING"
        audits = [event for event in audit_matches if event is not None]
        resources = [event for event in resource_matches if event is not None]
        if (
            {str(event.payload["summary_manifest_hash"]) for event in audits}
            != manifest_hashes
            or {str(event.payload["manifest_hash"]) for event in resources}
            != manifest_hashes
            or len(audits) != len(manifest_hashes)
            or len(resources) != len(manifest_hashes)
        ):
            return "ACTIVATION_RUN_SOURCE_COVERAGE_MISMATCH"
        if any(
            order[event.event_hash] >= result_order
            for event in audits + resources
        ):
            return "ACTIVATION_RUN_SOURCE_ORDER_INVALID"
        if any(
            event.payload.get("source_complete") is not True
            or bool(event.payload.get("source_failure_codes"))
            for event in audits + resources
        ):
            return "ACTIVATION_RUN_SOURCE_INCOMPLETE"
        return None

    @staticmethod
    def _generation_consumption_failure(
        result: Mapping[str, object],
        events: list[ResearchEventEnvelope],
    ) -> str | None:
        references = result.get("generation_consumption_event_hashes")
        if (
            not isinstance(references, list)
            or not references
            or any(not isinstance(item, str) for item in references)
            or len(references) != len(set(references))
        ):
            return "ACTIVATION_GENERATOR_CONSUMPTION_EVIDENCE_MISSING"
        by_hash = {event.event_hash: event for event in events}
        sources = [by_hash.get(str(event_hash)) for event_hash in references]
        if any(
            event is None
            or event.event_type != "ActivationGenerationConsumptionRecorded"
            or event.payload.get("plan_hash") != result.get("plan_hash")
            for event in sources
        ):
            return "ACTIVATION_GENERATOR_CONSUMPTION_EVIDENCE_MISSING"
        if any(
            event is None
            or event.payload.get("source_complete") is not True
            or bool(event.payload.get("source_failure_codes"))
            for event in sources
        ):
            return "ACTIVATION_GENERATOR_CONSUMPTION_EVIDENCE_INCOMPLETE"
        return None

    @staticmethod
    def _approval_replays(
        plan: Mapping[str, object],
        result: Mapping[str, object],
        decision: Mapping[str, object],
    ) -> bool:
        analysis = plan.get("analysis")
        primary = result.get("primary_effect")
        noninferiority = result.get("noninferiority_results")
        propensity = result.get("propensity_diagnostics")
        coverage = result.get("coverage_diagnostics")
        if not all(
            isinstance(item, Mapping)
            for item in (analysis, primary, noninferiority, propensity, coverage)
        ):
            return False
        assert isinstance(analysis, Mapping)
        assert isinstance(primary, Mapping)
        assert isinstance(noninferiority, Mapping)
        assert isinstance(propensity, Mapping)
        assert isinstance(coverage, Mapping)
        family = analysis.get("multiple_testing_family")
        if not isinstance(family, list):
            return False
        try:
            enough_pairs = int(str(result["complete_pairs"])) >= max(
                int(str(analysis["minimum_effective_pairs"])),
                int(str(analysis["required_independent_groups"])),
            )
            resolved_primary = (
                float(str(primary["ci_lower"])) > float(str(analysis["primary_threshold"]))
                and float(str(primary["ci_upper"])) - float(str(primary["ci_lower"]))
                <= float(str(analysis["maximum_ci_width"]))
            )
            secondaries_pass = all(noninferiority.get(str(name)) is True for name in family)
            diagnostics_pass = (
                float(str(propensity.get("unexplained_fraction", 1.0))) == 0.0
                and float(str(coverage.get("coverage_collapse", 1.0))) == 0.0
            )
        except (KeyError, TypeError, ValueError):
            return False
        return bool(
            enough_pairs
            and resolved_primary
            and secondaries_pass
            and diagnostics_pass
            and result.get("power_limitation") is None
            and result.get("replayable") is True
            and not result.get("invalidation_reasons")
            and decision.get("verdict") == "approved"
            and decision.get("active_research_only") is True
            and decision.get("reasons") == ["ALL_PREREGISTERED_GATES_PASSED"]
        )


__all__ = [
    "ActivationCompatibility", "ActiveRetrieverCapability", "ActiveRetrieverResolver",
    "RetrieverModeResolution",
]
