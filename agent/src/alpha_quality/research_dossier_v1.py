"""Canonical research dossiers, run reports, release index, and audience views."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

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
from src.research_ledger.hash_utils import canonical_json_hash
from src.alpha_quality.pit_artifact_v2 import (
    PIT_MANIFEST_MEDIA_TYPE,
    FrozenAsharePITSnapshotArtifactStoreV2,
)


DOSSIER_EVENT_TYPE = "ResearchDossierRecorded"
RUN_REPORT_EVENT_TYPE = "ExperimentRunReportRecorded"
RELEASE_MANIFEST_EVENT_TYPE = "ResearchReleaseManifestRecorded"
DOSSIER_FAILURE_EVENT_TYPE = "ResearchDossierMaterializationFailed"
PRODUCER_SCHEMA_VERSION = "research_dossier_service.v1"
PRODUCER_POLICY_HASH = canonical_json_hash(
    {
        "schema_version": "research_dossier_policy.v1",
        "fact_layer": "protected_events_and_content_addressed_artifacts_only",
        "views": ["research", "pm", "model_risk", "executive", "external"],
        "view_recalculation": False,
        "decision_input": False,
        "adverse_findings_mandatory": True,
        "no_live_meaning": True,
    }
)

CANDIDATE_DOSSIER_MEDIA_TYPE = "application/vnd.vibe.candidate-research-dossier-v1+json"
RUN_REPORT_MEDIA_TYPE = "application/vnd.vibe.experiment-run-report-v1+json"
RELEASE_MANIFEST_MEDIA_TYPE = "application/vnd.vibe.research-release-manifest-v1+json"
AUDIENCE_VIEW_MEDIA_TYPE = "application/vnd.vibe.research-audience-view-v1+json"
Audience = Literal["research", "pm", "model_risk", "executive", "external"]
AUDIENCES: tuple[Audience, ...] = (
    "research", "pm", "model_risk", "executive", "external"
)


def _strict_json(path: Path) -> Mapping[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite dossier JSON: {value}")

    def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate dossier JSON key")
            result[key] = value
        return result

    raw = json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicates,
    )
    if not isinstance(raw, Mapping):
        raise ValueError("dossier artifact must be an object")
    return raw


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True)
class CandidateResearchDossierV1:
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        expected = {
            "schema_version", "run_id", "trial_id", "factor_spec_id",
            "research_identity", "resolved_contract", "data_authority",
            "search_and_selection", "predictive_evidence", "execution_evidence",
            "secondary_evidence", "claim_matrix", "quality_decision",
            "final_and_forward", "limitations", "material_adverse_findings",
            "next_evidence_required", "no_live_trading_meaning",
            "source_event_hashes", "source_watermark_event_hash", "dossier_hash",
        }
        if set(self.payload) != expected:
            raise ValueError("candidate dossier schema is not closed")
        if self.payload["schema_version"] != "candidate_research_dossier.v1":
            raise ValueError("unsupported candidate dossier")
        content = {key: value for key, value in self.payload.items() if key != "dossier_hash"}
        if canonical_json_hash(content) != self.payload["dossier_hash"]:
            raise ValueError("candidate dossier hash differs")
        if self.payload["no_live_trading_meaning"] is not True:
            raise ValueError("candidate dossier must preserve no-live meaning")
        if not self.payload["limitations"]:
            raise ValueError("candidate dossier requires explicit limitations")

    @property
    def dossier_hash(self) -> str:
        return str(self.payload["dossier_hash"])

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class ExperimentRunReportV1:
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.payload.get("schema_version") != "experiment_run_report.v1":
            raise ValueError("unsupported experiment run report")
        content = {key: value for key, value in self.payload.items() if key != "run_report_hash"}
        if canonical_json_hash(content) != self.payload.get("run_report_hash"):
            raise ValueError("experiment run report hash differs")

    @property
    def report_hash(self) -> str:
        return str(self.payload["run_report_hash"])

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class ResearchReleaseManifestV1:
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.payload.get("schema_version") != "research_release_manifest.v1":
            raise ValueError("unsupported research release manifest")
        content = {key: value for key, value in self.payload.items() if key != "manifest_hash"}
        if canonical_json_hash(content) != self.payload.get("manifest_hash"):
            raise ValueError("research release manifest hash differs")

    @property
    def manifest_hash(self) -> str:
        return str(self.payload["manifest_hash"])

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


class ResearchDossierArtifactStoreV1:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve(strict=True)
        self.writer = AtomicContentAddressedArtifactWriter(
            self.root, max_bytes=8 * 1024**2
        )

    def write_candidate(
        self, dossier: CandidateResearchDossierV1
    ) -> ContentAddressedArtifact:
        return self.writer.write_json(
            namespace="candidate-research-dossier-v1",
            payload=dossier.to_dict(),
            schema_version="candidate_research_dossier.v1",
            semantic_hash_field="dossier_hash",
            closed_keys=frozenset(dossier.payload),
            media_type=CANDIDATE_DOSSIER_MEDIA_TYPE,
        )

    def write_run(self, report: ExperimentRunReportV1) -> ContentAddressedArtifact:
        return self.writer.write_json(
            namespace="experiment-run-report-v1",
            payload=report.to_dict(),
            schema_version="experiment_run_report.v1",
            semantic_hash_field="run_report_hash",
            closed_keys=frozenset(report.payload),
            media_type=RUN_REPORT_MEDIA_TYPE,
        )

    def write_release(
        self, manifest: ResearchReleaseManifestV1
    ) -> ContentAddressedArtifact:
        return self.writer.write_json(
            namespace="research-release-manifest-v1",
            payload=manifest.to_dict(),
            schema_version="research_release_manifest.v1",
            semantic_hash_field="manifest_hash",
            closed_keys=frozenset(manifest.payload),
            media_type=RELEASE_MANIFEST_MEDIA_TYPE,
        )

    def write_view(self, view: Mapping[str, Any]) -> ContentAddressedArtifact:
        return self.writer.write_json(
            namespace="research-audience-view-v1",
            payload=dict(view),
            schema_version="research_audience_view.v1",
            semantic_hash_field="view_hash",
            closed_keys=frozenset(view),
            media_type=AUDIENCE_VIEW_MEDIA_TYPE,
        )

    def read(
        self,
        reference: Mapping[str, Any],
        *,
        semantic_hash: str,
        namespace: str,
        media_type: str,
        semantic_field: str,
    ) -> Mapping[str, Any]:
        normalized = validate_artifact_references(self.root, [reference])[0]
        if normalized["media_type"] != media_type:
            raise ValueError("dossier artifact media type differs")
        digest = semantic_hash.removeprefix("sha256:")
        expected = f"{namespace}/{digest[:2]}/{digest}.json"
        if normalized["relative_path"] != expected:
            raise ValueError("dossier artifact path is not content addressed")
        raw = _strict_json(self.root.joinpath(*expected.split("/")))
        if raw.get(semantic_field) != semantic_hash:
            raise ValueError("dossier artifact semantic identity differs")
        return raw


def render_audience_view(
    dossier: CandidateResearchDossierV1,
    audience: Audience,
) -> dict[str, Any]:
    if audience not in AUDIENCES:
        raise ValueError("unknown dossier audience")
    source = dossier.payload
    claims = source["claim_matrix"].get("claims", [])
    common = {
        "schema_version": "research_audience_view.v1",
        "audience": audience,
        "factor_spec_id": source["factor_spec_id"],
        "current_decision": source["quality_decision"].get("decision", "none"),
        "promotion_ceiling": source["resolved_contract"].get("maximum_promotion"),
        "claim_scope_and_grade": [
            {
                "claim_type": claim["claim_type"],
                "verdict": claim["verdict"],
                "evidence_grade": claim["evidence_grade"],
                "scope": claim["scope"],
            }
            for claim in claims
        ],
        "pit_bias": source["predictive_evidence"].get("pit_bias"),
        "implementation_blockers": source["execution_evidence"].get("caps", []),
        "selection_status": source["search_and_selection"],
        "final_and_forward": source["final_and_forward"],
        "limitations": source["limitations"],
        "material_adverse_findings": source["material_adverse_findings"],
        "research_only_no_live": True,
        "dossier_hash": source["dossier_hash"],
        "source_watermark_event_hash": source["source_watermark_event_hash"],
    }
    detail = {
        "research": {
            "predictive_evidence": source["predictive_evidence"],
            "execution_evidence": source["execution_evidence"],
            "secondary_evidence": source["secondary_evidence"],
            "next_evidence_required": source["next_evidence_required"],
        },
        "pm": {
            "prediction": source["predictive_evidence"],
            "implementation": source["execution_evidence"],
            "promotion": source["quality_decision"],
        },
        "model_risk": {
            "data_authority": source["data_authority"],
            "claims": source["claim_matrix"],
            "adverse_findings": source["material_adverse_findings"],
        },
        "executive": {
            "decision": source["quality_decision"],
            "adverse_findings": source["material_adverse_findings"],
            "next_evidence_required": source["next_evidence_required"],
        },
        "external": {
            "decision": source["quality_decision"],
            "scoped_claims": common["claim_scope_and_grade"],
            "limitations": source["limitations"],
        },
    }[audience]
    content = {**common, "detail": detail}
    return {**content, "view_hash": canonical_json_hash(content)}


def _event_of_type(
    events: Sequence[ResearchEventEnvelope],
    event_type: str,
    *,
    required: bool = True,
) -> ResearchEventEnvelope | None:
    matches = [event for event in events if event.event_type == event_type]
    if len(matches) > 1 or (required and len(matches) != 1):
        raise EventTransitionError(f"dossier requires one exact {event_type}")
    return matches[0] if matches else None


class ResearchDossierServiceV1:
    def __init__(self, store: Any) -> None:
        from src.research_ledger.events.store import ResearchEventStore

        if not isinstance(store, ResearchEventStore):
            raise TypeError("research dossier requires ResearchEventStore")
        if not store.flags.enabled("VIBE_TRADING_ALPHA_REPORT_API"):
            raise RuntimeError("research dossier capability is disabled")
        self.store = store
        self.artifacts = ResearchDossierArtifactStoreV1(store.artifact_root)

    def _candidate_sources(
        self, *, run_id: str, factor_spec_id: str
    ) -> dict[str, ResearchEventEnvelope | None]:
        events = self.store.query_events()
        relevant = [
            event for event in events
            if event.run_id == run_id
            and (
                event.entity_id == factor_spec_id
                or event.payload.get("factor_spec_id") == factor_spec_id
                or event.payload.get("trial_id")
                == next(
                    (
                        candidate.payload.get("metadata", {}).get("originating_trial_id")
                        for candidate in events
                        if candidate.event_type == "FactorDefinitionRecorded"
                        and candidate.entity_id == factor_spec_id
                    ),
                    None,
                )
            )
        ]
        definition = _event_of_type(relevant, "FactorDefinitionRecorded")
        assert definition is not None
        trial_id = str(definition.payload["metadata"]["originating_trial_id"])
        nodes = [
            event for event in events
            if event.event_type == "ProductionEvaluationNodeRecorded"
            and event.run_id == run_id
            and event.payload["factor_spec_id"] == factor_spec_id
        ]
        if not nodes:
            raise EventTransitionError("candidate dossier requires an analytical node")
        terminal_dossiers = [
            event for event in events
            if event.event_type == "TrialTerminalDossierRecorded"
            and event.run_id == run_id
            and event.payload["trial_id"] == trial_id
        ]
        if len(terminal_dossiers) != 1:
            raise EventTransitionError("candidate dossier requires terminal dossier")
        contracts = [
            event for event in events
            if event.event_type == "ResolvedEvaluationContractRegistered"
            and event.run_id == run_id
        ]
        if len(contracts) != 1:
            raise EventTransitionError("candidate dossier requires exact contract")
        snapshots = [
            event for event in events
            if event.event_type == "AsharePITSnapshotRecorded" and event.run_id == run_id
        ]
        if len(snapshots) != 1:
            raise EventTransitionError("candidate dossier requires exact PIT snapshot")
        selections = [
            event for event in events
            if event.event_type == "SelectionAssessmentRecorded" and event.run_id == run_id
        ]
        if len(selections) > 1:
            raise EventTransitionError("candidate dossier selection is ambiguous")
        return {
            "definition": definition,
            "contract": contracts[0],
            "snapshot": snapshots[0],
            "observed": _event_of_type(relevant, "ObservedPanelPredictiveEvidenceRecorded", required=False),
            "pit": _event_of_type(relevant, "PITPredictiveEvidenceRecorded", required=False),
            "execution": _event_of_type(relevant, "ExecutionEvidenceRecorded", required=False),
            "secondary": _event_of_type(relevant, "SecondaryEvidenceRecorded", required=False),
            "selection": selections[0] if selections else None,
            "claims": _event_of_type(relevant, "ClaimMatrixRecorded", required=False),
            "decision": _event_of_type(relevant, "QualityDecisionV4Recorded", required=False),
            "terminal_dossier": terminal_dossiers[0],
            "nodes": nodes[0],
        }

    def build_candidate(
        self, *, run_id: str, factor_spec_id: str
    ) -> CandidateResearchDossierV1:
        sources = self._candidate_sources(run_id=run_id, factor_spec_id=factor_spec_id)
        definition = sources["definition"]
        contract = sources["contract"]
        snapshot = sources["snapshot"]
        assert definition is not None and contract is not None and snapshot is not None
        observed = sources["observed"]
        pit = sources["pit"]
        execution = sources["execution"]
        secondary = sources["secondary"]
        selection = sources["selection"]
        claims = sources["claims"]
        decision = sources["decision"]
        terminal = sources["terminal_dossier"]
        assert terminal is not None
        snapshot_refs = [
            ref for ref in snapshot.payload["artifact_refs"]
            if ref["media_type"] == PIT_MANIFEST_MEDIA_TYPE
        ]
        if len(snapshot_refs) != 1:
            raise EventValidationError("candidate dossier PIT manifest is ambiguous")
        snapshot_manifest = FrozenAsharePITSnapshotArtifactStoreV2(
            self.store.artifact_root
        ).read_manifest(
            str(snapshot_refs[0]["relative_path"]),
            expected_snapshot_hash=str(snapshot.payload["snapshot_hash"]),
            expected_blob_hash=str(snapshot_refs[0]["artifact_hash"]),
        )
        source_events = [
            event for event in sources.values()
            if isinstance(event, ResearchEventEnvelope)
        ]
        analytical_node_hashes = sorted(terminal.payload["node_event_hashes"])
        source_hashes = sorted(
            {
                *(event.event_hash for event in source_events),
                *analytical_node_hashes,
            }
        )
        adverse: set[str] = set()
        limitations = {
            "RESEARCH_EVIDENCE_ONLY",
            "NO_LIVE_TRADING_AUTHORIZATION",
        }
        if pit is None or pit.payload["availability"] != "available":
            adverse.add("PIT_PREDICTIVE_AUTHORITY_UNAVAILABLE")
        if execution is None or execution.payload["implementability_claim"] != "supported":
            adverse.add("IMPLEMENTABILITY_NOT_SUPPORTED")
        if secondary is None:
            adverse.add("SECONDARY_EVIDENCE_UNAVAILABLE")
        elif secondary.payload["duplicate_detected"]:
            adverse.add("DUPLICATE_IDENTITY_DETECTED")
        current_decision = "none" if decision is None else decision.payload["decision"]
        if current_decision not in {"paper_candidate", "forward_track"}:
            limitations.add("FINAL_TEST_NOT_ESTABLISHED")
        limitations.add("FORWARD_SUCCESS_NOT_ESTABLISHED")
        content = {
            "schema_version": "candidate_research_dossier.v1",
            "run_id": run_id,
            "trial_id": definition.payload["metadata"]["originating_trial_id"],
            "factor_spec_id": factor_spec_id,
            "research_identity": {
                "expression_id": definition.payload["expression_id"],
                "canonical_ast_hash": definition.payload["canonical_ast_hash"],
                "grammar_version": definition.payload["grammar_version"],
                "grammar_hash": definition.payload["grammar_hash"],
                "canonical_formula": definition.payload["metadata"]["canonical_formula"],
                "semantics": definition.payload["metadata"]["semantics"],
                "definition_event_hash": definition.event_hash,
            },
            "resolved_contract": {
                key: contract.payload[key]
                for key in (
                    "contract_hash", "research_family_id", "profile_template_hash",
                    "profile_id", "profile_version", "profile_authority_class",
                    "maximum_promotion", "evaluation_policy_bundle_hash",
                    "tier_invariant_manifest_hash",
                )
            },
            "data_authority": {
                "snapshot_hash": snapshot.payload["snapshot_hash"],
                "adapter_id": snapshot.payload["adapter_id"],
                "source_manifest_hash": snapshot.payload["source_manifest_hash"],
                "provider": snapshot_manifest.registration["descriptor"]["provider"],
                "adapter_version": snapshot_manifest.registration["descriptor"]["adapter_version"],
                "dataset_vintage": snapshot_manifest.source_manifest["dataset_vintage"],
                "source_as_of": snapshot_manifest.source_manifest["source_as_of"],
                "pit_contract_status": snapshot.payload["pit_contract_status"],
                "survivorship_status": snapshot.payload["survivorship_status"],
                "cutoff_status": snapshot.payload["cutoff_status"],
                "pit_decision_grade": snapshot.payload["decision_grade"],
                "snapshot_event_hash": snapshot.event_hash,
            },
            "search_and_selection": (
                {
                    "availability": "unavailable",
                    "analytical_node_event_hashes": analytical_node_hashes,
                }
                if selection is None
                else {
                    **{
                        key: selection.payload[key]
                        for key in (
                            "trial_count", "candidate_count", "terminal_counts",
                            "unpublished_or_open_trial_count", "selection_policy_hash",
                            "multiplicity_policy_hash", "confirmatory_grade_eligible",
                        )
                    },
                    "analytical_node_event_hashes": analytical_node_hashes,
                }
            ),
            "predictive_evidence": {
                "observed": None if observed is None else {
                    "availability": observed.payload["availability"],
                    "claim_scope": observed.payload["claim_scope"],
                    "evidence_grade": observed.payload["evidence_grade"],
                    "evidence_hash": observed.payload["evidence_hash"],
                },
                "pit": None if pit is None else {
                    "availability": pit.payload["availability"],
                    "claim_scope": pit.payload["claim_scope"],
                    "evidence_grade": pit.payload["evidence_grade"],
                    "evidence_hash": pit.payload["evidence_hash"],
                },
                "pit_bias": None if pit is None else {
                    "caps": pit.payload["caps"],
                    "warnings": pit.payload["warnings"],
                    "availability": pit.payload["availability"],
                },
            },
            "execution_evidence": (
                {"availability": "unavailable", "caps": ["EXECUTION_EVIDENCE_UNAVAILABLE"]}
                if execution is None
                else {
                    key: execution.payload[key]
                    for key in (
                        "availability", "implementability_claim", "material_unpriced_exposure",
                        "terminal_flat", "caps", "execution_artifact_hash",
                    )
                }
            ),
            "secondary_evidence": (
                {"availability": "unavailable"}
                if secondary is None
                else secondary.payload["secondary_evidence_bundle"]
            ),
            "claim_matrix": (
                {"availability": "unavailable", "claims": []}
                if claims is None
                else {
                    "availability": "available",
                    "claim_matrix_hash": claims.payload["claim_matrix_hash"],
                    "claims": claims.payload["claims"],
                }
            ),
            "quality_decision": (
                {"decision": "none", "availability": "unavailable"}
                if decision is None
                else {
                    key: decision.payload[key]
                    for key in (
                        "decision", "tier", "decision_hash", "reasons", "warnings",
                        "caps", "limitations", "policy_hash",
                    )
                }
            ),
            "final_and_forward": {
                "final_test": "not_opened",
                "forward_monitoring": "not_started",
                "forward_success_established": False,
            },
            "limitations": sorted(limitations),
            "material_adverse_findings": sorted(adverse),
            "next_evidence_required": sorted(
                {
                    *("PRODUCER_EXECUTION_EVIDENCE" for _ in [0] if execution is None),
                    *("PRODUCER_SECONDARY_EVIDENCE" for _ in [0] if secondary is None),
                    "FROZEN_FINAL_TEST_ONLY_AFTER_PREFINAL_ELIGIBILITY",
                }
            ),
            "no_live_trading_meaning": True,
            "source_event_hashes": source_hashes,
            "source_watermark_event_hash": terminal.event_hash,
        }
        content = _plain(content)
        return CandidateResearchDossierV1(
            {**content, "dossier_hash": canonical_json_hash(content)}
        )

    def record_candidate(
        self, *, run_id: str, factor_spec_id: str
    ) -> tuple[CandidateResearchDossierV1, ResearchEventEnvelope]:
        dossier = self.build_candidate(run_id=run_id, factor_spec_id=factor_spec_id)
        artifact = self.artifacts.write_candidate(dossier)
        views = [self.artifacts.write_view(render_audience_view(dossier, audience)) for audience in AUDIENCES]
        dossier_id = "research-dossier-" + dossier.dossier_hash.removeprefix("sha256:")[:24]
        event = self.store._append_producer_event(
            EventDraft(
                event_type=DOSSIER_EVENT_TYPE,
                entity_id=dossier_id,
                run_id=run_id,
                payload_schema_version="research_dossier_recorded.v1",
                idempotency_key="research-dossier:" + dossier.dossier_hash,
                payload={
                    "dossier_id": dossier_id,
                    "factor_spec_id": factor_spec_id,
                    "dossier_hash": dossier.dossier_hash,
                    "terminal_dossier_event_hash": dossier.payload["source_watermark_event_hash"],
                    "quality_decision_event_hash": next(
                        (
                            event_hash for event_hash in dossier.payload["source_event_hashes"]
                            if any(
                                event.event_hash == event_hash
                                and event.event_type == "QualityDecisionV4Recorded"
                                for event in self.store.query_events()
                            )
                        ),
                        None,
                    ),
                    "view_hashes": sorted(
                        str(_strict_json(self.store.artifact_root.joinpath(*item.relative_path.split("/")))["view_hash"])
                        for item in views
                    ),
                    "source_event_hashes": dossier.payload["source_event_hashes"],
                    "promotion_effect": "none",
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                    "artifact_refs": [artifact.reference(), *(item.reference() for item in views)],
                },
            )
        )
        return dossier, event

    def record_run_report(self, *, run_id: str) -> tuple[ExperimentRunReportV1, ResearchEventEnvelope]:
        events = self.store.query_events()
        starts = [event for event in events if event.event_type == "TrialStarted" and event.run_id == run_id]
        terminals = [event for event in events if event.event_type == "TrialTerminated" and event.run_id == run_id]
        if not starts or len(terminals) != len(starts):
            raise EventTransitionError("run report requires every attempt terminal")
        dossiers = [event for event in events if event.event_type == DOSSIER_EVENT_TYPE and event.run_id == run_id]
        definitions = [event for event in events if event.event_type == "FactorDefinitionRecorded" and event.run_id == run_id]
        counts: dict[str, int] = {}
        for terminal in terminals:
            status = str(terminal.payload["status"])
            counts[status] = counts.get(status, 0) + 1
        decisions = [event for event in events if event.event_type == "QualityDecisionV4Recorded" and event.run_id == run_id]
        decision_counts: dict[str, int] = {}
        for decision in decisions:
            label = str(decision.payload["decision"])
            decision_counts[label] = decision_counts.get(label, 0) + 1
        ordered_sources = [
            event for event in events
            if event in [*starts, *terminals, *dossiers, *decisions]
        ]
        source_hashes = sorted({event.event_hash for event in ordered_sources})
        content = {
            "schema_version": "experiment_run_report.v1",
            "run_id": run_id,
            "closed": True,
            "trial_count": len(starts),
            "terminal_counts": dict(sorted(counts.items())),
            "candidate_count": len({event.entity_id for event in definitions}),
            "decision_counts": dict(sorted(decision_counts.items())),
            "candidate_dossier_hashes": sorted(str(event.payload["dossier_hash"]) for event in dossiers),
            "test_access_count": sum(event.event_type == "FinalTestAccessRecorded" and event.run_id == run_id for event in events),
            "forward_observation_count": sum(event.event_type in {"ForwardObservationRecorded", "ForwardObservationV2Recorded"} and event.run_id == run_id for event in events),
            "limitations": ["RUN_REPORT_IS_DERIVED_AND_NOT_A_DECISION_INPUT"],
            "source_event_hashes": source_hashes,
            "source_watermark_event_hash": ordered_sources[-1].event_hash,
        }
        report = ExperimentRunReportV1(
            {**content, "run_report_hash": canonical_json_hash(content)}
        )
        artifact = self.artifacts.write_run(report)
        report_id = "run-report-" + report.report_hash.removeprefix("sha256:")[:24]
        event = self.store._append_producer_event(
            EventDraft(
                event_type=RUN_REPORT_EVENT_TYPE,
                entity_id=report_id,
                run_id=run_id,
                payload_schema_version="experiment_run_report_recorded.v1",
                idempotency_key="experiment-run-report:" + report.report_hash,
                payload={
                    "report_id": report_id,
                    "run_report_hash": report.report_hash,
                    "trial_count": len(starts),
                    "candidate_dossier_hashes": content["candidate_dossier_hashes"],
                    "source_event_hashes": source_hashes,
                    "promotion_effect": "none",
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                    "artifact_refs": [artifact.reference()],
                },
            )
        )
        return report, event

    def record_release_manifest(self, *, run_id: str) -> tuple[ResearchReleaseManifestV1, ResearchEventEnvelope]:
        events = self.store.query_events()
        dossiers = [event for event in events if event.event_type == DOSSIER_EVENT_TYPE]
        runs = [event for event in events if event.event_type == RUN_REPORT_EVENT_TYPE]
        source_events = [
            event for event in events
            if event.event_type in {DOSSIER_EVENT_TYPE, RUN_REPORT_EVENT_TYPE}
        ]
        if not source_events:
            raise EventTransitionError("release manifest requires event-bound dossiers")
        final_artifacts = [event for event in events if event.event_type == "FinalTestArtifactRecorded"]
        forward_observations = [event for event in events if event.event_type in {"ForwardObservationRecorded", "ForwardObservationV2Recorded"}]
        content = {
            "schema_version": "research_release_manifest.v1",
            "engineering_status": "implemented_and_event_bound",
            "empirical_status": (
                "final_evidence_present" if final_artifacts else "train_valid_only"
            ),
            "preflight_status": "production_evaluator_completed",
            "superseded_status": "current_at_source_watermark",
            "candidate_dossiers": [
                {
                    "factor_spec_id": event.payload["factor_spec_id"],
                    "dossier_hash": event.payload["dossier_hash"],
                    "source_event_hash": event.event_hash,
                }
                for event in sorted(dossiers, key=lambda item: (item.run_id, item.entity_id))
            ],
            "run_reports": [
                {
                    "run_id": event.run_id,
                    "run_report_hash": event.payload["run_report_hash"],
                    "source_event_hash": event.event_hash,
                }
                for event in sorted(runs, key=lambda item: (item.run_id, item.entity_id))
            ],
            "final_artifact_count": len(final_artifacts),
            "forward_observation_count": len(forward_observations),
            "limitations": [
                "RELEASE_MANIFEST_IS_A_DERIVED_INDEX_NOT_RESEARCH_TRUTH",
                "NO_LIVE_TRADING_AUTHORIZATION",
            ],
            "source_event_hashes": sorted(event.event_hash for event in source_events),
            "source_watermark_event_hash": source_events[-1].event_hash,
        }
        manifest = ResearchReleaseManifestV1(
            {**content, "manifest_hash": canonical_json_hash(content)}
        )
        artifact = self.artifacts.write_release(manifest)
        manifest_id = "research-release-" + manifest.manifest_hash.removeprefix("sha256:")[:24]
        event = self.store._append_producer_event(
            EventDraft(
                event_type=RELEASE_MANIFEST_EVENT_TYPE,
                entity_id=manifest_id,
                run_id=run_id,
                payload_schema_version="research_release_manifest_recorded.v1",
                idempotency_key="research-release-manifest:" + manifest.manifest_hash,
                payload={
                    "manifest_id": manifest_id,
                    "manifest_hash": manifest.manifest_hash,
                    "engineering_status": content["engineering_status"],
                    "empirical_status": content["empirical_status"],
                    "source_event_hashes": content["source_event_hashes"],
                    "promotion_effect": "none",
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                    "artifact_refs": [artifact.reference()],
                },
            )
        )
        return manifest, event

    def record_failure(
        self,
        *,
        run_id: str,
        trial_id: str,
        factor_spec_id: str,
        terminal_dossier_event_hash: str,
        quality_decision_event_hash: str | None,
        failure_class: str,
    ) -> ResearchEventEnvelope:
        failure_id = "research-dossier-failure-" + trial_id
        sources = sorted(
            {
                terminal_dossier_event_hash,
                *(
                    ()
                    if quality_decision_event_hash is None
                    else (quality_decision_event_hash,)
                ),
            }
        )
        return self.store._append_producer_event(
            EventDraft(
                event_type=DOSSIER_FAILURE_EVENT_TYPE,
                entity_id=failure_id,
                run_id=run_id,
                payload_schema_version="research_dossier_materialization_failed.v1",
                idempotency_key="research-dossier-failure:" + trial_id,
                payload={
                    "failure_id": failure_id,
                    "trial_id": trial_id,
                    "factor_spec_id": factor_spec_id,
                    "terminal_dossier_event_hash": terminal_dossier_event_hash,
                    "quality_decision_event_hash": quality_decision_event_hash,
                    "failure_code": "RESEARCH_DOSSIER_MATERIALIZATION_FAILED",
                    "failure_class": failure_class,
                    "decision_effect": "none",
                    "source_event_hashes": sources,
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                },
            )
        )


class CanonicalDossierResolverV1:
    """Resolve only event-bound canonical artifacts; never arbitrary files."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.artifacts = ResearchDossierArtifactStoreV1(store.artifact_root)

    def candidate(self, factor_spec_id: str) -> Mapping[str, Any]:
        events = [
            event for event in self.store.query_events(event_type=DOSSIER_EVENT_TYPE)
            if event.payload["factor_spec_id"] == factor_spec_id
        ]
        if len(events) != 1:
            raise LookupError("canonical candidate dossier not found")
        event = events[0]
        refs = [ref for ref in event.payload["artifact_refs"] if ref["media_type"] == CANDIDATE_DOSSIER_MEDIA_TYPE]
        if len(refs) != 1:
            raise EventValidationError("canonical candidate artifact is ambiguous")
        return self.artifacts.read(
            refs[0], semantic_hash=str(event.payload["dossier_hash"]),
            namespace="candidate-research-dossier-v1",
            media_type=CANDIDATE_DOSSIER_MEDIA_TYPE,
            semantic_field="dossier_hash",
        )

    def view(self, factor_spec_id: str, audience: Audience) -> Mapping[str, Any]:
        dossier = CandidateResearchDossierV1(self.candidate(factor_spec_id))
        return render_audience_view(dossier, audience)


__all__ = [
    "AUDIENCES",
    "CANDIDATE_DOSSIER_MEDIA_TYPE",
    "DOSSIER_EVENT_TYPE",
    "RUN_REPORT_EVENT_TYPE",
    "RELEASE_MANIFEST_EVENT_TYPE",
    "CandidateResearchDossierV1",
    "CanonicalDossierResolverV1",
    "ExperimentRunReportV1",
    "ResearchDossierArtifactStoreV1",
    "ResearchDossierServiceV1",
    "ResearchReleaseManifestV1",
    "render_audience_view",
]
