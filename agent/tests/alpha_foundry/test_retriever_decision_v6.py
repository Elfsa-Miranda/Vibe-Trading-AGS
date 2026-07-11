from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.alpha_foundry.control_evidence import OfficialSearchControlServiceV1
from src.alpha_foundry.mutators import SeedMutator
from src.alpha_foundry.retrieval.action_template_v1 import (
    RetrieverActionTemplateServiceV1,
)
from src.alpha_foundry.retrieval.feature_producer_v1 import (
    RetrieverFeatureSourceServiceV1,
)
from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy
from src.alpha_foundry.retrieval.service_v6 import RetrieverDecisionV6Service
from src.alpha_foundry.search import AlphaFoundrySearch
from src.alpha_foundry.search_lifecycle import (
    EventSourcedSearchLifecycle,
    SearchEvaluationOutcome,
)
from src.alpha_foundry.seed_bank import AlphaSeed, SeedBank
from src.research_ledger.events import EventDraft, EventValidationError
from src.research_ledger.hash_utils import canonical_json_hash
from test_retriever_feature_producer_v1 import _record as _record_feature_source
from test_search_lifecycle import _semantics


class _SkipEvaluator:
    def evaluate(self, **kwargs):
        return SearchEvaluationOutcome(
            status="skip",
            decision="none",
            reason_codes=("CONTROL_V6_FIXTURE",),
        )


def _control(store, snapshot_hash: str):
    run_id = "retriever-v6-control-run"
    lifecycle = EventSourcedSearchLifecycle(
        store=store,
        flags=store.flags,
        semantics=_semantics(),
        evaluator=_SkipEvaluator(),
        data_snapshot_hash=snapshot_hash,
    )
    search = AlphaFoundrySearch(
        seed_bank=SeedBank([AlphaSeed("control-v6", "close", "registry")]),
        mutator=SeedMutator(max_candidates_per_seed=2),
        max_candidates=2,
        trial_budget=2,
        lifecycle=lifecycle,
        run_id=run_id,
    )
    return OfficialSearchControlServiceV1(store).record(search, search.generate())


def _record(tmp_path: Path):
    store, snapshot, _, _, action, feature = _record_feature_source(tmp_path)
    control = _control(store, snapshot.snapshot.snapshot_hash)
    recorded = RetrieverDecisionV6Service(store).record(
        control_evidence_event_hash=control.event.event_hash,
        feature_source_event_hash=feature.event.event_hash,
        seed=71,
        candidate_budget=1,
        control_run_id=control.event.run_id,
        run_id=feature.event.run_id,
    )
    return store, snapshot, action, feature, control, recorded


def test_v6_decision_uses_only_authoritative_feature_source(tmp_path: Path) -> None:
    store, snapshot, action, feature, control, recorded = _record(tmp_path)
    assert recorded.event.event_type == "RetrieverDecisionV6Recorded"
    assert recorded.input_bundle.feature_source_hash == feature.source.source_hash
    assert recorded.decision.action_template_event_hashes == (
        action.event.event_hash,
    )
    assert recorded.decision.components[0].action_id == action.action.action_id
    assert recorded.decision.components[0].semdiv == 0.0
    assert recorded.decision.data_snapshot_hash == snapshot.snapshot.snapshot_hash
    assert recorded.decision.official_output_hash == control.evidence.output_hash
    assert recorded.event.payload["feature_source_event_hash"] == feature.event.event_hash
    assert store.verify_chain()


def test_v6_api_has_no_caller_candidate_channel(tmp_path: Path) -> None:
    store, _, _, feature, control, _ = _record(tmp_path)
    with pytest.raises(TypeError, match="unexpected keyword argument 'candidates'"):
        RetrieverDecisionV6Service(store).record(
            control_evidence_event_hash=control.event.event_hash,
            feature_source_event_hash=feature.event.event_hash,
            seed=72,
            candidate_budget=1,
            control_run_id=control.event.run_id,
            run_id=feature.event.run_id,
            candidates=feature.source.candidates,
        )


def test_v6_rejects_feature_source_created_after_control(tmp_path: Path) -> None:
    store, snapshot, target, _, _, feature = _record_feature_source(tmp_path)
    control = _control(store, snapshot.snapshot.snapshot_hash)
    policy = ActivationRetrieverPolicy()
    late_action = RetrieverActionTemplateServiceV1(
        store=store, flags=store.flags
    ).freeze(
        execution_run_id=feature.event.run_id,
        parent_factor_spec_id=target.factor_spec_id,
        template_id="zscore_wrap",
        eligible_event_watermark=feature.source.eligible_event_watermark,
        data_snapshot_hash=feature.source.snapshot_hash,
        retrieval_policy_hash=policy.policy_hash,
    )
    late_source = RetrieverFeatureSourceServiceV1(
        store, flags=store.flags
    ).record(
        execution_run_id=feature.event.run_id,
        snapshot_event_hash=feature.source.snapshot_event_hash,
        action_event_hashes=(late_action.event.event_hash,),
        eligible_event_watermark=feature.source.eligible_event_watermark,
        retrieval_policy=policy,
    )
    with pytest.raises(ValueError, match="must precede control"):
        RetrieverDecisionV6Service(store).record(
            control_evidence_event_hash=control.event.event_hash,
            feature_source_event_hash=late_source.event.event_hash,
            seed=73,
            candidate_budget=1,
            control_run_id=control.event.run_id,
            run_id=feature.event.run_id,
        )


def test_rehashed_v6_component_fabrication_fails_independent_replay(
    tmp_path: Path,
) -> None:
    store, _, _, _, _, recorded = _record(tmp_path)
    payload = recorded.event.to_dict()["payload"]
    payload["components"][0]["action_score"] += 100.0
    content = {
        "schema_version": "retriever_action_source_bound_decision.v6",
        **{
            key: value
            for key, value in payload.items()
            if key not in {"decision_id", "decision_hash", "artifact_refs"}
        },
    }
    payload["decision_hash"] = canonical_json_hash(content)
    payload["decision_id"] = (
        "retriever-v6-"
        + payload["decision_hash"].removeprefix("sha256:")[:20]
    )
    with pytest.raises(EventValidationError, match="deterministic rebuild"):
        store.append_event(
            EventDraft(
                event_type="RetrieverDecisionV6Recorded",
                entity_id=payload["decision_id"],
                run_id=recorded.event.run_id,
                payload_schema_version="retriever_decision_recorded.v6",
                payload=payload,
                idempotency_key="retriever-v6:forged-component",
            )
        )


def test_v6_input_artifact_tampering_invalidates_chain(tmp_path: Path) -> None:
    store, _, _, _, _, recorded = _record(tmp_path)
    reference = recorded.event.payload["artifact_refs"][0]
    path = store.artifact_root.joinpath(
        *str(reference["relative_path"]).split("/")
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["seed"] += 1
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert store.verify_chain() is False
