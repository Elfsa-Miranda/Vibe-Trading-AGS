"""Narrow production recorder for source-bound retriever v3 decisions."""

from __future__ import annotations

from dataclasses import dataclass

from src.alpha_foundry.dag import FactorDAGQuery
from src.alpha_foundry.retrieval.evidence_v3 import (
    RetrieverInputArtifactStoreV3,
    RetrieverInputBundleV3,
)
from src.alpha_foundry.retrieval.model import (
    RetrievalCandidate,
    ShadowDecision,
)
from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy
from src.alpha_foundry.retrieval.shadow import ShadowRetriever
from src.alpha_quality.scope import DiscoveryEvidenceProjector
from src.research_ledger.events import EventDraft, ResearchEventEnvelope, ResearchEventStore
from src.research_ledger.hash_utils import canonical_json_hash


def source_bound_decision_content(
    decision: ShadowDecision,
    *,
    input_bundle_hash: str,
) -> dict[str, object]:
    return {
        "schema_version": "retriever_source_bound_decision.v3",
        "shadow_decision_hash": decision.decision_hash,
        "input_bundle_hash": input_bundle_hash,
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
class RecordedRetrieverDecisionV3:
    event: ResearchEventEnvelope
    decision: ShadowDecision
    input_bundle: RetrieverInputBundleV3


class RetrieverDecisionV3Service:
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
        self.artifacts = RetrieverInputArtifactStoreV3(store.artifact_root)

    def record(
        self,
        *,
        official_candidate_ids: tuple[str, ...],
        candidates: tuple[RetrievalCandidate, ...],
        data_snapshot_hash: str,
        seed: int,
        candidate_budget: int,
        run_id: str,
    ) -> RecordedRetrieverDecisionV3:
        evidence = self.projector.project(
            self.store,
            data_snapshot_hash=data_snapshot_hash,
        )
        query = FactorDAGQuery(evidence.factual.dag)
        bundle = RetrieverInputBundleV3.build(
            official_candidate_ids=official_candidate_ids,
            eligible_event_watermark=evidence.source_watermark,
            data_snapshot_hash=data_snapshot_hash,
            seed=seed,
            candidate_budget=candidate_budget,
            policy=self.policy,
            candidates=candidates,
        )
        decision = self.retriever.decide(
            official_candidate_ids=official_candidate_ids,
            evidence=evidence,
            query=query,
            candidates=candidates,
            seed=seed,
            candidate_budget=candidate_budget,
        )
        reference = self.artifacts.write(bundle)
        content = source_bound_decision_content(
            decision,
            input_bundle_hash=bundle.bundle_hash,
        )
        decision_hash = canonical_json_hash(content)
        identifier = "retriever-v3-" + decision_hash.removeprefix("sha256:")[:20]
        event = self.store.append_event(
            EventDraft(
                event_type="RetrieverDecisionV3Recorded",
                entity_id=identifier,
                run_id=run_id,
                payload_schema_version="retriever_decision_recorded.v3",
                idempotency_key=f"retriever-v3:{decision_hash}",
                payload={
                    "decision_id": identifier,
                    "decision_hash": decision_hash,
                    **{key: value for key, value in content.items() if key != "schema_version"},
                    "artifact_refs": [reference],
                },
            )
        )
        return RecordedRetrieverDecisionV3(event, decision, bundle)


__all__ = [
    "RecordedRetrieverDecisionV3", "RetrieverDecisionV3Service",
    "source_bound_decision_content",
]
