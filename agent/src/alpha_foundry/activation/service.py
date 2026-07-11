"""Append-only orchestration for Activation artifacts and ledger evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from src.alpha_foundry.activation.analysis import ActivationAnalyzer
from src.alpha_foundry.activation.artifacts import ActivationArtifactStore, ArtifactKind
from src.alpha_foundry.activation.model import (
    ActivationExperimentPlan,
    ActivationExperimentResult,
    ActivationRunManifest,
    RetrieverActivationDecision,
)
from src.alpha_foundry.activation.policy import RetrieverActivationPolicy
from src.alpha_foundry.activation.run_source_v2 import (
    ActivationRunSourceAuditV2,
    ActivationRunSourceAuditorV2,
)
from src.alpha_foundry.activation.resource_v1 import ActivationResourceEvidenceV1
from src.alpha_foundry.activation.generation_consumption_v1 import (
    ActivationGenerationConsumptionV1,
)
from src.research_ledger.events import EventDraft, ResearchEventStore


class ActivationEvidenceService:
    """The public finalization API accepts evidence, never a verdict."""

    def __init__(self, event_store: ResearchEventStore) -> None:
        self.event_store = event_store
        self.artifacts = ActivationArtifactStore(event_store.artifact_root)
        self.analyzer = ActivationAnalyzer()
        self.policy = RetrieverActivationPolicy()

    def register_plan(self, plan: ActivationExperimentPlan) -> str:
        relative = self.artifacts.put("plan", plan.to_dict())
        self.event_store.append_event(
            EventDraft(
                event_type="ActivationPlanRegistered",
                entity_id=plan.experiment_id,
                run_id=plan.experiment_id,
                payload_schema_version="activation_plan_registered.v1",
                idempotency_key=f"activation-plan:{plan.plan_hash}",
                payload={
                    "experiment_id": plan.experiment_id,
                    "plan_hash": plan.plan_hash,
                    "phase": plan.phase,
                    "registered_at": plan.registered_at,
                    "artifact_refs": [self._reference("plan", plan.plan_hash, relative)],
                },
            )
        )
        return relative

    def record_run(self, manifest: ActivationRunManifest) -> str:
        relative = self.artifacts.put("run", manifest.to_dict())
        manifest_id = "activation-run-" + manifest.manifest_hash.removeprefix("sha256:")[:24]
        self.event_store.append_event(
            EventDraft(
                event_type="ActivationRunRecorded",
                entity_id=manifest_id,
                run_id=manifest.run_group_id,
                payload_schema_version="activation_run_recorded.v1",
                idempotency_key=f"activation-run:{manifest.manifest_hash}",
                payload={
                    "manifest_id": manifest_id,
                    "plan_hash": manifest.plan_hash,
                    "manifest_hash": manifest.manifest_hash,
                    "pair_id": manifest.pair_id,
                    "run_group_id": manifest.run_group_id,
                    "arm": manifest.arm,
                    "terminal_status_counts": dict(manifest.terminal_status_counts),
                    "complete": manifest.complete,
                    "contaminated": manifest.contaminated,
                    "artifact_refs": [self._reference("run", manifest.manifest_hash, relative)],
                },
            )
        )
        return relative

    def record_run_source_audit(
        self,
        manifest: ActivationRunManifest,
        *,
        retriever_decision_event_hashes: tuple[str, ...],
        terminal_event_hashes: tuple[str, ...],
        evaluation_event_hashes: tuple[str, ...],
        quality_decision_event_hashes: tuple[str, ...],
    ) -> tuple[ActivationRunSourceAuditV2, str]:
        audit = ActivationRunSourceAuditorV2(self.event_store).audit(
            manifest,
            retriever_decision_event_hashes=retriever_decision_event_hashes,
            terminal_event_hashes=terminal_event_hashes,
            evaluation_event_hashes=evaluation_event_hashes,
            quality_decision_event_hashes=quality_decision_event_hashes,
        )
        relative = self.artifacts.put("run_source", audit.to_dict())
        reference = self._reference("run_source", audit.audit_hash, relative)
        reference["media_type"] = "application/vnd.vibe.activation-run-source-v2+json"
        audit_id = "activation-source-v2-" + audit.audit_hash.removeprefix("sha256:")[:24]
        self.event_store.append_event(
            EventDraft(
                event_type="ActivationRunSourceAudited",
                entity_id=audit_id,
                run_id=manifest.run_group_id,
                payload_schema_version="activation_run_source_audited.v2",
                idempotency_key="activation-run-source-v2:" + audit.audit_hash,
                payload={
                    "audit_id": audit_id,
                    "plan_hash": audit.plan_hash,
                    "summary_manifest_hash": audit.summary_manifest_hash,
                    "source_watermark_event_hash": audit.source_watermark_event_hash,
                    "audit_hash": audit.audit_hash,
                    "retriever_decision_event_hashes": list(
                        audit.retriever_decision_event_hashes
                    ),
                    "terminal_event_hashes": list(audit.terminal_event_hashes),
                    "evaluation_event_hashes": list(audit.evaluation_event_hashes),
                    "quality_decision_event_hashes": list(
                        audit.quality_decision_event_hashes
                    ),
                    "source_failure_codes": list(audit.source_failure_codes),
                    "source_complete": audit.source_complete,
                    "artifact_refs": [reference],
                },
            )
        )
        return audit, relative

    def record_resource_evidence(
        self,
        evidence: ActivationResourceEvidenceV1,
    ) -> str:
        if not isinstance(evidence, ActivationResourceEvidenceV1):
            raise TypeError("typed runner-owned Activation resource evidence is required")
        relative = self.artifacts.put("resource", evidence.to_dict())
        reference = self._reference("resource", evidence.evidence_hash, relative)
        reference["media_type"] = "application/vnd.vibe.activation-resource-v1+json"
        resource_id = (
            "activation-resource-v1-"
            + evidence.evidence_hash.removeprefix("sha256:")[:24]
        )
        self.event_store.append_event(
            EventDraft(
                event_type="ActivationResourceMeasured",
                entity_id=resource_id,
                run_id=evidence.run_group_id,
                payload_schema_version="activation_resource_measured.v1",
                idempotency_key="activation-resource-v1:" + evidence.evidence_hash,
                payload={
                    "resource_id": resource_id,
                    **{
                        key: value for key, value in evidence.to_dict().items()
                        if key != "schema_version"
                    },
                    "artifact_refs": [reference],
                },
            )
        )
        return relative

    def record_generation_consumption(
        self,
        evidence: ActivationGenerationConsumptionV1,
    ) -> str:
        if not isinstance(evidence, ActivationGenerationConsumptionV1):
            raise TypeError("typed generation-consumption evidence is required")
        relative = self.artifacts.put("generation_consumption", evidence.to_dict())
        reference = self._reference(
            "generation_consumption", evidence.evidence_hash, relative
        )
        reference["media_type"] = (
            "application/vnd.vibe.activation-generation-consumption-v1+json"
        )
        generation_id = (
            "activation-generation-v1-"
            + evidence.evidence_hash.removeprefix("sha256:")[:24]
        )
        self.event_store.append_event(
            EventDraft(
                event_type="ActivationGenerationConsumptionRecorded",
                entity_id=generation_id,
                run_id=evidence.execution_run_id,
                payload_schema_version=(
                    "activation_generation_consumption_recorded.v1"
                ),
                idempotency_key=(
                    "activation-generation-v1:" + evidence.evidence_hash
                ),
                payload={
                    "generation_id": generation_id,
                    "plan_hash": evidence.plan_hash,
                    "pair_id": evidence.pair_id,
                    "run_group_id": evidence.run_group_id,
                    "mechanism_family": evidence.mechanism_family,
                    "dag_region": evidence.dag_region,
                    "execution_run_id": evidence.execution_run_id,
                    "retriever_decision_event_hash": (
                        evidence.retriever_decision_event_hash
                    ),
                    "retriever_decision_hash": evidence.retriever_decision_hash,
                    "control_evidence_event_hash": (
                        evidence.control_evidence_event_hash
                    ),
                    "generator_policy_hash": evidence.generator_policy_hash,
                    "selected_parent_factor_spec_ids": list(
                        evidence.selected_parent_factor_spec_ids
                    ),
                    "consumed_parent_factor_spec_ids": list(
                        evidence.consumed_parent_factor_spec_ids
                    ),
                    "generated_candidate_count": len(
                        evidence.generated_candidates
                    ),
                    "candidate_budget": evidence.candidate_budget,
                    "compute_budget": evidence.compute_budget,
                    "source_failure_codes": list(evidence.source_failure_codes),
                    "source_complete": evidence.source_complete,
                    "evidence_hash": evidence.evidence_hash,
                    "artifact_refs": [reference],
                },
            )
        )
        return relative

    def finalize(
        self,
        plan: ActivationExperimentPlan,
        manifests: Iterable[ActivationRunManifest],
    ) -> tuple[ActivationExperimentResult, RetrieverActivationDecision]:
        result = self.analyzer.analyze(plan, manifests)
        return self._record_result_and_decision(plan, result)

    def invalidate_preflight(
        self,
        plan: ActivationExperimentPlan,
        *,
        reasons: tuple[str, ...],
    ) -> tuple[ActivationExperimentResult, RetrieverActivationDecision]:
        result = self.analyzer.invalidated_result(plan, reasons=reasons)
        return self._record_result_and_decision(plan, result)

    def _record_result_and_decision(
        self,
        plan: ActivationExperimentPlan,
        result: ActivationExperimentResult,
    ) -> tuple[ActivationExperimentResult, RetrieverActivationDecision]:
        result_relative = self.artifacts.put("result", result.to_dict())
        result_id = "activation-result-" + result.result_hash.removeprefix("sha256:")[:24]
        self.event_store.append_event(
            EventDraft(
                event_type="ActivationResultRecorded",
                entity_id=result_id,
                run_id=plan.experiment_id,
                payload_schema_version="activation_result_recorded.v1",
                idempotency_key=f"activation-result:{result.result_hash}",
                payload={
                    "result_id": result_id,
                    "plan_hash": plan.plan_hash,
                    "result_hash": result.result_hash,
                    "complete_pairs": result.complete_pairs,
                    "invalidation_reasons": list(result.invalidation_reasons),
                    "replayable": result.replayable,
                    "artifact_refs": [
                        self._reference("result", result.result_hash, result_relative)
                    ],
                },
            )
        )
        decision = self.policy.decide(plan, result)
        decision_relative = self.artifacts.put("decision", decision.to_dict())
        decision_id = "activation-decision-" + decision.decision_hash.removeprefix("sha256:")[:24]
        self.event_store.append_event(
            EventDraft(
                event_type="RetrieverActivationDecisionRecorded",
                entity_id=decision_id,
                run_id=plan.experiment_id,
                payload_schema_version="retriever_activation_decision_recorded.v1",
                idempotency_key=f"activation-decision:{decision.decision_hash}",
                payload={
                    "activation_decision_id": decision_id,
                    "plan_hash": plan.plan_hash,
                    "result_hash": result.result_hash,
                    "decision_hash": decision.decision_hash,
                    "policy_hash": decision.policy_hash,
                    "verdict": decision.verdict,
                    "reasons": list(decision.reasons),
                    "active_research_only": decision.active_research_only,
                    "artifact_refs": [
                        self._reference("decision", decision.decision_hash, decision_relative)
                    ],
                },
            )
        )
        return result, decision

    def _reference(
        self,
        kind: ArtifactKind,
        content_hash: str,
        relative: str,
    ) -> dict[str, str]:
        expected = self.artifacts.relative_path(kind, content_hash)
        if expected != relative:
            raise ValueError("activation artifact reference is not content addressed")
        absolute = Path(self.event_store.artifact_root, *relative.split("/"))
        return {
            "relative_path": relative,
            "artifact_hash": self.event_store.hash_artifact(absolute),
            "media_type": "application/json",
        }


__all__ = ["ActivationEvidenceService"]
