"""Retriever v7 selection bound to an outcome-free pre-arm flat schedule."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from src.alpha_foundry.activation.artifacts import ActivationArtifactStore
from src.alpha_foundry.activation.runner import activation_arm_execution_run_id
from src.alpha_foundry.dag import FactorDAGQuery
from src.alpha_foundry.flat_schedule_v1 import (
    PreArmFlatScheduleArtifactStoreV1,
    PreArmFlatScheduleV1,
)
from src.alpha_foundry.retrieval.action_decision_v5 import (
    ActionShadowDecisionV5,
    ActionShadowRetrieverV5,
)
from src.alpha_foundry.retrieval.action_template_v1 import FrozenRetrieverActionTemplateV1
from src.alpha_foundry.retrieval.evidence_v7 import (
    RetrieverDecisionInputArtifactStoreV7,
    RetrieverDecisionInputV7,
)
from src.alpha_foundry.retrieval.feature_producer_v1 import (
    RetrieverFeatureSourceArtifactStoreV1,
    RetrieverFeatureSourceV1,
)
from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy
from src.alpha_quality.scope import DiscoveryEvidenceProjector
from src.research_ledger.events import EventDraft, ResearchEventEnvelope, ResearchEventStore
from src.research_ledger.hash_utils import canonical_json_hash


def schedule_bound_action_decision_content_v7(
    decision: ActionShadowDecisionV5,
    *, input_bundle_hash: str, plan_hash: str, pair_id: str,
    schedule_event_hash: str, schedule_hash: str,
    feature_source_event_hash: str, feature_source_hash: str,
) -> dict[str, object]:
    return {
        "schema_version": "retriever_action_schedule_bound_decision.v7",
        "shadow_decision_hash": decision.decision_hash,
        "input_bundle_hash": input_bundle_hash,
        "plan_hash": plan_hash,
        "pair_id": pair_id,
        "schedule_event_hash": schedule_event_hash,
        "schedule_hash": schedule_hash,
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
class RecordedRetrieverDecisionV7:
    event: ResearchEventEnvelope
    decision: ActionShadowDecisionV5
    input_bundle: RetrieverDecisionInputV7
    schedule: PreArmFlatScheduleV1
    feature_source: RetrieverFeatureSourceV1
    actions: tuple[FrozenRetrieverActionTemplateV1, ...]


class RetrieverDecisionV7Service:
    def __init__(self, store: ResearchEventStore) -> None:
        self.store = store
        self.artifacts = RetrieverDecisionInputArtifactStoreV7(store.artifact_root)
        self.projector = DiscoveryEvidenceProjector(flags=store.flags)

    def record(
        self, *, schedule_event_hash: str, feature_source_event_hash: str,
    ) -> RecordedRetrieverDecisionV7:
        decision, bundle, schedule, source, actions = self.rebuild(
            schedule_event_hash=schedule_event_hash,
            feature_source_event_hash=feature_source_event_hash,
        )
        reference = self.artifacts.write(bundle)
        content = schedule_bound_action_decision_content_v7(
            decision,
            input_bundle_hash=bundle.bundle_hash,
            plan_hash=schedule.plan_hash,
            pair_id=schedule.pair_id,
            schedule_event_hash=bundle.schedule_event_hash,
            schedule_hash=schedule.schedule_hash,
            feature_source_event_hash=bundle.feature_source_event_hash,
            feature_source_hash=source.source_hash,
        )
        decision_hash = canonical_json_hash(content)
        identifier = "retriever-v7-" + decision_hash.removeprefix("sha256:")[:20]
        run_id = activation_arm_execution_run_id(
            plan_hash=schedule.plan_hash,
            run_group_id=schedule.run_group_id,
            arm="treatment",
        )
        event = self.store.append_event(
            EventDraft(
                event_type="RetrieverDecisionV7Recorded",
                entity_id=identifier,
                run_id=run_id,
                payload_schema_version="retriever_decision_recorded.v7",
                idempotency_key="retriever-v7:" + decision_hash,
                payload={
                    "decision_id": identifier,
                    "decision_hash": decision_hash,
                    **{key: value for key, value in content.items()
                       if key != "schema_version"},
                    "artifact_refs": [reference],
                },
            )
        )
        return RecordedRetrieverDecisionV7(
            event, decision, bundle, schedule, source, actions
        )

    def rebuild(
        self, *, schedule_event_hash: str, feature_source_event_hash: str,
        decision_event_hash: str | None = None,
    ) -> tuple[
        ActionShadowDecisionV5, RetrieverDecisionInputV7, PreArmFlatScheduleV1,
        RetrieverFeatureSourceV1, tuple[FrozenRetrieverActionTemplateV1, ...],
    ]:
        events = self.store.query_events()
        by_hash = {event.event_hash: event for event in events}
        order = {event.event_hash: index for index, event in enumerate(events)}
        schedule_event = by_hash.get(schedule_event_hash)
        feature_event = by_hash.get(feature_source_event_hash)
        if (
            schedule_event is None
            or schedule_event.event_type != "PreArmFlatScheduleFrozen"
            or feature_event is None
            or feature_event.event_type != "RetrieverFeatureSourceRecorded"
            or order[schedule_event.event_hash] >= order[feature_event.event_hash]
        ):
            raise ValueError("Retriever v7 schedule must precede treatment features")
        schedule = self._schedule(schedule_event)
        source = self._source(feature_event)
        plan = ActivationArtifactStore(self.store.artifact_root).get(
            "plan", schedule.plan_hash
        )
        design = plan.get("design")
        provenance = plan.get("provenance")
        if not isinstance(design, Mapping) or not isinstance(provenance, Mapping):
            raise ValueError("Retriever v7 registered plan is invalid")
        groups = design.get("run_group_ids")
        seeds = design.get("seeds")
        if not isinstance(groups, list) or not isinstance(seeds, list):
            raise ValueError("Retriever v7 plan seeds are invalid")
        try:
            seed = int(seeds[groups.index(schedule.run_group_id)])
        except (ValueError, IndexError, TypeError) as exc:
            raise ValueError("Retriever v7 run group is not preregistered") from exc
        run_id = activation_arm_execution_run_id(
            plan_hash=schedule.plan_hash,
            run_group_id=schedule.run_group_id,
            arm="treatment",
        )
        historical_decision = (
            None if decision_event_hash is None else by_hash.get(decision_event_hash)
        )
        if decision_event_hash is not None and (
            historical_decision is None
            or historical_decision.event_type != "RetrieverDecisionV7Recorded"
            or historical_decision.run_id != run_id
            or historical_decision.payload["schedule_event_hash"]
            != schedule_event_hash
            or historical_decision.payload["feature_source_event_hash"]
            != feature_source_event_hash
            or not order[schedule_event_hash]
            < order[feature_source_event_hash]
            < order[decision_event_hash]
        ):
            raise ValueError("Retriever v7 historical decision binding is invalid")
        if (
            source.execution_run_id != run_id
            or source.snapshot_hash != schedule.data_snapshot_hash
            or source.retrieval_policy_hash != provenance.get("treatment_policy_hash")
            or schedule.policy.policy_hash != provenance.get("control_policy_hash")
            or schedule.policy.max_candidates != design.get("candidate_budget")
            or schedule.policy.trial_budget != design.get("compute_budget")
        ):
            raise ValueError("Retriever v7 source, schedule and plan differ")
        arm_ids = {
            activation_arm_execution_run_id(
                plan_hash=schedule.plan_hash,
                run_group_id=schedule.run_group_id,
                arm=arm,
            )
            for arm in ("control", "treatment")
        }
        decision_order = (
            None if decision_event_hash is None else order.get(decision_event_hash)
        )
        if decision_event_hash is not None and decision_order is None:
            raise ValueError("Retriever v7 historical decision event is missing")
        if any(
            event.run_id in arm_ids
            and event.event_type in {"TrialStarted", "TrialTerminated", "EvaluationRecorded"}
            and (
                decision_order is None
                or order[event.event_hash] < decision_order
            )
            for event in events
        ):
            raise ValueError("Retriever v7 decision must precede both arm outcomes")
        policy = ActivationRetrieverPolicy(**dict(source.retrieval_policy))
        actions = self._actions(source, by_hash, run_id)
        evidence = self.projector.project_at_watermark(
            self.store,
            data_snapshot_hash=source.snapshot_hash,
            watermark_event_hash=source.eligible_event_watermark,
        )
        official_ids = tuple(str(item["candidate_id"]) for item in schedule.candidates)
        decision = ActionShadowRetrieverV5(
            flags=self.store.flags, policy=policy
        ).decide(
            official_candidate_ids=official_ids,
            evidence=evidence,
            query=FactorDAGQuery(evidence.factual.dag),
            candidates=source.candidates,
            actions=actions,
            action_template_event_hashes=source.action_event_hashes,
            seed=seed,
            candidate_budget=schedule.policy.max_candidates,
        )
        if decision.official_output_hash != schedule.output_hash:
            raise ValueError("Retriever v7 official output differs from schedule")
        bundle = RetrieverDecisionInputV7.build(
            schedule_event_hash=schedule_event.event_hash,
            schedule_hash=schedule.schedule_hash,
            feature_source_event_hash=feature_event.event_hash,
            feature_source_hash=source.source_hash,
        )
        return decision, bundle, schedule, source, actions

    def _schedule(self, event: ResearchEventEnvelope) -> PreArmFlatScheduleV1:
        refs = [reference for reference in event.payload["artifact_refs"]
                if reference["media_type"] == PreArmFlatScheduleArtifactStoreV1.media_type]
        if len(refs) != 1:
            raise ValueError("Retriever v7 schedule artifact is missing")
        return PreArmFlatScheduleArtifactStoreV1(self.store.artifact_root).read(
            str(refs[0]["relative_path"]), str(event.payload["schedule_hash"])
        )

    def _source(self, event: ResearchEventEnvelope) -> RetrieverFeatureSourceV1:
        refs = [reference for reference in event.payload["artifact_refs"]
                if reference["media_type"] == RetrieverFeatureSourceArtifactStoreV1.media_type]
        if len(refs) != 1:
            raise ValueError("Retriever v7 feature artifact is missing")
        return RetrieverFeatureSourceArtifactStoreV1(self.store.artifact_root).read(
            str(refs[0]["relative_path"]), str(event.payload["source_hash"])
        )

    @staticmethod
    def _actions(
        source: RetrieverFeatureSourceV1,
        by_hash: Mapping[str, ResearchEventEnvelope],
        run_id: str,
    ) -> tuple[FrozenRetrieverActionTemplateV1, ...]:
        events = tuple(by_hash.get(event_hash) for event_hash in source.action_event_hashes)
        if any(event is None or event.event_type != "RetrieverActionTemplateFrozen"
               or event.run_id != run_id for event in events):
            raise ValueError("Retriever v7 action event is invalid")
        return tuple(FrozenRetrieverActionTemplateV1.from_dict(event.payload)
                     for event in events if event is not None)


__all__ = [
    "RecordedRetrieverDecisionV7", "RetrieverDecisionV7Service",
    "schedule_bound_action_decision_content_v7",
]
