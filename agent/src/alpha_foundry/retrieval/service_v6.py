"""Retriever v6 decision that accepts only authoritative feature/control events."""

from __future__ import annotations

from dataclasses import dataclass

from src.alpha_foundry.control_evidence import (
    OfficialSearchControlArtifactStoreV1,
    OfficialSearchControlEvidenceV1,
)
from src.alpha_foundry.dag import FactorDAGQuery
from src.alpha_foundry.retrieval.action_decision_v5 import (
    ActionShadowDecisionV5,
    ActionShadowRetrieverV5,
)
from src.alpha_foundry.retrieval.action_template_v1 import FrozenRetrieverActionTemplateV1
from src.alpha_foundry.retrieval.evidence_v6 import (
    RetrieverDecisionInputArtifactStoreV6,
    RetrieverDecisionInputV6,
)
from src.alpha_foundry.retrieval.feature_producer_v1 import (
    RetrieverFeatureSourceArtifactStoreV1,
    RetrieverFeatureSourceV1,
)
from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy
from src.alpha_quality.scope import DiscoveryEvidenceProjector
from src.research_ledger.events import EventDraft, ResearchEventEnvelope, ResearchEventStore
from src.research_ledger.hash_utils import canonical_json_hash


def source_bound_action_decision_content_v6(
    decision: ActionShadowDecisionV5,
    *,
    input_bundle_hash: str,
    control_evidence_event_hash: str,
    control_evidence_hash: str,
    control_policy_hash: str,
    feature_source_event_hash: str,
    feature_source_hash: str,
) -> dict[str, object]:
    return {
        "schema_version": "retriever_action_source_bound_decision.v6",
        "shadow_decision_hash": decision.decision_hash,
        "input_bundle_hash": input_bundle_hash,
        "control_evidence_event_hash": control_evidence_event_hash,
        "control_evidence_hash": control_evidence_hash,
        "control_policy_hash": control_policy_hash,
        "feature_source_event_hash": feature_source_event_hash,
        "feature_source_hash": feature_source_hash,
        "selected_action_ids": list(decision.selected_action_ids),
        "selected_parent_factor_spec_ids": list(decision.selected_parent_factor_spec_ids),
        "action_template_event_hashes": list(decision.action_template_event_hashes),
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
class RecordedRetrieverDecisionV6:
    event: ResearchEventEnvelope
    decision: ActionShadowDecisionV5
    input_bundle: RetrieverDecisionInputV6
    control_evidence: OfficialSearchControlEvidenceV1
    feature_source: RetrieverFeatureSourceV1
    actions: tuple[FrozenRetrieverActionTemplateV1, ...]


class RetrieverDecisionV6Service:
    def __init__(self, store: ResearchEventStore) -> None:
        self.store = store
        self.projector = DiscoveryEvidenceProjector(flags=store.flags)
        self.artifacts = RetrieverDecisionInputArtifactStoreV6(store.artifact_root)

    def record(
        self,
        *,
        control_evidence_event_hash: str,
        feature_source_event_hash: str,
        seed: int,
        candidate_budget: int,
        control_run_id: str,
        run_id: str,
    ) -> RecordedRetrieverDecisionV6:
        control_event, control = self._control(control_evidence_event_hash, control_run_id)
        feature_event, source = self._feature_source(feature_source_event_hash, run_id)
        events = self.store.query_events()
        order = {event.event_hash: index for index, event in enumerate(events)}
        if (
            order[feature_event.event_hash] >= order[control_event.event_hash]
            or source.snapshot_hash != control.data_snapshot_hash
            or source.execution_run_id != run_id
        ):
            raise ValueError("Retriever v6 feature source must precede control outcomes")
        policy = ActivationRetrieverPolicy(**dict(source.retrieval_policy))
        actions = self._actions(source, run_id=run_id)
        discovery = self.projector.project_at_watermark(
            self.store,
            data_snapshot_hash=source.snapshot_hash,
            watermark_event_hash=source.eligible_event_watermark,
        )
        official_ids = tuple(str(item["candidate_id"]) for item in control.candidates)
        bundle = RetrieverDecisionInputV6.build(
            control_evidence_event_hash=control_event.event_hash,
            control_evidence_hash=control.evidence_hash,
            feature_source_event_hash=feature_event.event_hash,
            feature_source_hash=source.source_hash,
            seed=seed,
            candidate_budget=candidate_budget,
        )
        decision = ActionShadowRetrieverV5(
            flags=self.store.flags,
            policy=policy,
        ).decide(
            official_candidate_ids=official_ids,
            evidence=discovery,
            query=FactorDAGQuery(discovery.factual.dag),
            candidates=source.candidates,
            actions=actions,
            action_template_event_hashes=source.action_event_hashes,
            seed=seed,
            candidate_budget=candidate_budget,
        )
        if (
            decision.official_output_hash != control.output_hash
            or decision.policy_hash != source.retrieval_policy_hash
            or decision.eligible_event_watermark != source.eligible_event_watermark
            or decision.data_snapshot_hash != source.snapshot_hash
            or decision.action_template_event_hashes != source.action_event_hashes
        ):
            raise ValueError("Retriever v6 official output differs from control")
        reference = self.artifacts.write(bundle)
        content = source_bound_action_decision_content_v6(
            decision,
            input_bundle_hash=bundle.bundle_hash,
            control_evidence_event_hash=control_event.event_hash,
            control_evidence_hash=control.evidence_hash,
            control_policy_hash=control.policy.policy_hash,
            feature_source_event_hash=feature_event.event_hash,
            feature_source_hash=source.source_hash,
        )
        decision_hash = canonical_json_hash(content)
        identifier = "retriever-v6-" + decision_hash.removeprefix("sha256:")[:20]
        event = self.store.append_event(
            EventDraft(
                event_type="RetrieverDecisionV6Recorded",
                entity_id=identifier,
                run_id=run_id,
                payload_schema_version="retriever_decision_recorded.v6",
                idempotency_key="retriever-v6:" + decision_hash,
                payload={
                    "decision_id": identifier,
                    "decision_hash": decision_hash,
                    **{key: value for key, value in content.items() if key != "schema_version"},
                    "artifact_refs": [reference],
                },
            )
        )
        return RecordedRetrieverDecisionV6(event, decision, bundle, control, source, actions)

    def _feature_source(
        self, event_hash: str, run_id: str
    ) -> tuple[ResearchEventEnvelope, RetrieverFeatureSourceV1]:
        matches = [
            event
            for event in self.store.query_events(event_type="RetrieverFeatureSourceRecorded")
            if event.event_hash == event_hash
        ]
        if len(matches) != 1 or matches[0].run_id != run_id:
            raise ValueError("Retriever v6 feature source event is missing or misattributed")
        event = matches[0]
        refs = [
            reference
            for reference in event.payload["artifact_refs"]
            if reference["media_type"] == RetrieverFeatureSourceArtifactStoreV1.media_type
        ]
        if len(refs) != 1:
            raise ValueError("Retriever v6 feature source artifact is missing")
        source = RetrieverFeatureSourceArtifactStoreV1(self.store.artifact_root).read(
            str(refs[0]["relative_path"]), str(event.payload["source_hash"])
        )
        if (
            event.payload["feature_source_id"] != event.entity_id
            or event.payload["execution_run_id"] != run_id
            or event.payload["source_hash"] != source.source_hash
        ):
            raise ValueError("Retriever v6 feature source event differs from artifact")
        return event, source

    def _actions(
        self, source: RetrieverFeatureSourceV1, *, run_id: str
    ) -> tuple[FrozenRetrieverActionTemplateV1, ...]:
        by_hash = {event.event_hash: event for event in self.store.query_events()}
        actions: list[FrozenRetrieverActionTemplateV1] = []
        for event_hash in source.action_event_hashes:
            event = by_hash.get(event_hash)
            if event is None or event.event_type != "RetrieverActionTemplateFrozen" or event.run_id != run_id:
                raise ValueError("Retriever v6 action event is missing or misattributed")
            actions.append(FrozenRetrieverActionTemplateV1.from_dict(event.payload))
        return tuple(actions)

    def _control(
        self, event_hash: str, run_id: str
    ) -> tuple[ResearchEventEnvelope, OfficialSearchControlEvidenceV1]:
        matches = [
            event
            for event in self.store.query_events(event_type="OfficialSearchControlRecorded")
            if event.event_hash == event_hash
        ]
        if len(matches) != 1 or matches[0].run_id != run_id:
            raise ValueError("Retriever v6 control event is missing or misattributed")
        event = matches[0]
        refs = [
            reference
            for reference in event.payload["artifact_refs"]
            if reference["media_type"] == OfficialSearchControlArtifactStoreV1.media_type
        ]
        if len(refs) != 1:
            raise ValueError("Retriever v6 control artifact is missing")
        control = OfficialSearchControlArtifactStoreV1(self.store.artifact_root).read(
            str(refs[0]["relative_path"]), str(event.payload["evidence_hash"])
        )
        if control.data_snapshot_hash != event.payload["data_snapshot_hash"]:
            raise ValueError("Retriever v6 control snapshot differs")
        return event, control


__all__ = [
    "RecordedRetrieverDecisionV6",
    "RetrieverDecisionV6Service",
    "source_bound_action_decision_content_v6",
]
