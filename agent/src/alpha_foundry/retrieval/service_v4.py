"""Retriever v4 recorder bound to replayed official flat-control evidence."""

from __future__ import annotations

from dataclasses import dataclass

from src.alpha_foundry.control_evidence import (
    OfficialSearchControlArtifactStoreV1,
    OfficialSearchControlEvidenceV1,
)
from src.alpha_foundry.dag import FactorDAGQuery
from src.alpha_foundry.retrieval.evidence_v3 import RetrieverInputBundleV3
from src.alpha_foundry.retrieval.evidence_v4 import (
    RetrieverInputArtifactStoreV4,
    RetrieverInputBundleV4,
)
from src.alpha_foundry.retrieval.model import RetrievalCandidate, ShadowDecision
from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy
from src.alpha_foundry.retrieval.shadow import ShadowRetriever
from src.alpha_quality.scope import DiscoveryEvidenceProjector
from src.research_ledger.events import (
    EventDraft,
    ResearchEventEnvelope,
    ResearchEventStore,
)
from src.research_ledger.hash_utils import canonical_json_hash


def source_bound_decision_content_v4(
    decision: ShadowDecision,
    *,
    input_bundle_hash: str,
    control_evidence_event_hash: str,
    control_evidence_hash: str,
    control_policy_hash: str,
) -> dict[str, object]:
    return {
        "schema_version": "retriever_source_bound_decision.v4",
        "shadow_decision_hash": decision.decision_hash,
        "input_bundle_hash": input_bundle_hash,
        "control_evidence_event_hash": control_evidence_event_hash,
        "control_evidence_hash": control_evidence_hash,
        "control_policy_hash": control_policy_hash,
        "selected_factor_spec_ids": list(decision.selected_factor_spec_ids),
        "seed": decision.seed,
        "policy_version": decision.policy_version,
        "policy_hash": decision.policy_hash,
        "policy_config": dict(decision.policy_config),
        "eligible_event_watermark": decision.eligible_event_watermark,
        "data_snapshot_hash": decision.data_snapshot_hash,
        "candidate_budget": decision.candidate_budget,
        "official_output_hash": decision.official_output_hash,
        "propensity_semantics": decision.propensity_semantics,
        "components": [component.to_dict() for component in decision.components],
        "shadow_only": decision.shadow_only,
    }


@dataclass(frozen=True)
class RecordedRetrieverDecisionV4:
    event: ResearchEventEnvelope
    decision: ShadowDecision
    input_bundle: RetrieverInputBundleV4
    control_evidence: OfficialSearchControlEvidenceV1


class RetrieverDecisionV4Service:
    def __init__(
        self,
        store: ResearchEventStore,
        *,
        policy: ActivationRetrieverPolicy | None = None,
    ) -> None:
        self.store = store
        self.policy = policy or ActivationRetrieverPolicy()
        self.retriever = ShadowRetriever(flags=store.flags, policy=self.policy)
        self.projector = DiscoveryEvidenceProjector(flags=store.flags)
        self.artifacts = RetrieverInputArtifactStoreV4(store.artifact_root)

    def record(
        self,
        *,
        control_evidence_event_hash: str,
        candidates: tuple[RetrievalCandidate, ...],
        data_snapshot_hash: str,
        eligible_event_watermark: str,
        seed: int,
        candidate_budget: int,
        control_run_id: str,
        run_id: str,
    ) -> RecordedRetrieverDecisionV4:
        control_event, control = self._control_evidence(
            control_evidence_event_hash,
            run_id=control_run_id,
            data_snapshot_hash=data_snapshot_hash,
        )
        self._assert_pre_control_watermark(
            eligible_event_watermark,
            control_event=control_event,
            control=control,
        )
        official_candidate_ids = tuple(
            str(item["candidate_id"]) for item in control.candidates
        )
        discovery = self.projector.project_at_watermark(
            self.store,
            data_snapshot_hash=data_snapshot_hash,
            watermark_event_hash=eligible_event_watermark,
        )
        query = FactorDAGQuery(discovery.factual.dag)
        v3_input = RetrieverInputBundleV3.build(
            official_candidate_ids=official_candidate_ids,
            eligible_event_watermark=discovery.source_watermark,
            data_snapshot_hash=data_snapshot_hash,
            seed=seed,
            candidate_budget=candidate_budget,
            policy=self.policy,
            candidates=candidates,
        )
        bundle = RetrieverInputBundleV4.build(
            control_evidence_event_hash=control_event.event_hash,
            control_evidence_hash=control.evidence_hash,
            retriever_input=v3_input,
        )
        decision = self.retriever.decide(
            official_candidate_ids=official_candidate_ids,
            evidence=discovery,
            query=query,
            candidates=candidates,
            seed=seed,
            candidate_budget=candidate_budget,
        )
        if decision.official_output_hash != control.output_hash:
            raise ValueError("retriever official output differs from replayed control")
        reference = self.artifacts.write(bundle)
        content = source_bound_decision_content_v4(
            decision,
            input_bundle_hash=bundle.bundle_hash,
            control_evidence_event_hash=control_event.event_hash,
            control_evidence_hash=control.evidence_hash,
            control_policy_hash=control.policy.policy_hash,
        )
        decision_hash = canonical_json_hash(content)
        identifier = "retriever-v4-" + decision_hash.removeprefix("sha256:")[:20]
        event = self.store.append_event(
            EventDraft(
                event_type="RetrieverDecisionV4Recorded",
                entity_id=identifier,
                run_id=run_id,
                payload_schema_version="retriever_decision_recorded.v4",
                idempotency_key="retriever-v4:" + decision_hash,
                payload={
                    "decision_id": identifier,
                    "decision_hash": decision_hash,
                    **{key: value for key, value in content.items() if key != "schema_version"},
                    "artifact_refs": [reference],
                },
            )
        )
        return RecordedRetrieverDecisionV4(event, decision, bundle, control)

    def _assert_pre_control_watermark(
        self,
        watermark_event_hash: str,
        *,
        control_event: ResearchEventEnvelope,
        control: OfficialSearchControlEvidenceV1,
    ) -> None:
        events = self.store.query_events()
        order = {event.event_hash: index for index, event in enumerate(events)}
        if watermark_event_hash not in order:
            raise ValueError("retriever v4 discovery watermark is unknown")
        watermark_order = order[watermark_event_hash]
        if order.get(control_event.event_hash, -1) <= watermark_order:
            raise ValueError(
                "retriever v4 discovery watermark must precede control evidence"
            )
        if any(
            order.get(event_hash, -1) <= watermark_order
            for event_hash in control.terminal_event_hashes
        ):
            raise ValueError(
                "retriever v4 discovery watermark includes control-arm outcomes"
            )

    def _control_evidence(
        self,
        event_hash: str,
        *,
        run_id: str,
        data_snapshot_hash: str,
    ) -> tuple[ResearchEventEnvelope, OfficialSearchControlEvidenceV1]:
        matches = [
            event for event in self.store.query_events(
                event_type="OfficialSearchControlRecorded"
            )
            if event.event_hash == event_hash
        ]
        if len(matches) != 1:
            raise ValueError("retriever v4 requires one official control event")
        event = matches[0]
        if event.run_id != run_id or event.payload["data_snapshot_hash"] != data_snapshot_hash:
            raise ValueError("retriever v4 control run or snapshot differs")
        references = [
            reference for reference in event.payload["artifact_refs"]
            if reference["media_type"]
            == OfficialSearchControlArtifactStoreV1.media_type
        ]
        if len(references) != 1:
            raise ValueError("official control event lacks its source artifact")
        evidence = OfficialSearchControlArtifactStoreV1(
            self.store.artifact_root
        ).read(
            str(references[0]["relative_path"]),
            str(event.payload["evidence_hash"]),
        )
        return event, evidence


__all__ = [
    "RecordedRetrieverDecisionV4", "RetrieverDecisionV4Service",
    "source_bound_decision_content_v4",
]
