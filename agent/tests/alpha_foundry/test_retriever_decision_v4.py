from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src.alpha_foundry.control_evidence import OfficialSearchControlServiceV1
from src.alpha_foundry.mutators import SeedMutator
from src.alpha_foundry.retrieval.service_v4 import RetrieverDecisionV4Service
from src.alpha_foundry.search import AlphaFoundrySearch
from src.alpha_foundry.search_lifecycle import (
    EventSourcedSearchLifecycle,
    SearchEvaluationOutcome,
)
from src.alpha_foundry.seed_bank import AlphaSeed, SeedBank
from src.research_ledger.events import (
    EventDraft,
    EventTransitionError,
    EventValidationError,
)
from src.research_ledger.hash_utils import canonical_json_hash
from test_retriever_shadow import _candidate, _views
from test_search_lifecycle import _semantics


class _SkipEvaluator:
    def evaluate(self, **kwargs):
        return SearchEvaluationOutcome(
            status="skip",
            decision="none",
            reason_codes=("CONTROL_V4_FIXTURE",),
        )


class _SuccessEvaluator:
    def evaluate(self, *, trial_id, **kwargs):
        return SearchEvaluationOutcome(
            status="success",
            decision="candidate_zoo",
            scorecard_hash=canonical_json_hash({"scorecard": trial_id}),
            reason_codes=("CONTROL_V4_SUCCESS_FIXTURE",),
        )


def _record(tmp_path: Path):
    store, query, discovery = _views(tmp_path)
    control_run_id = "retriever-v4-control-run"
    decision_run_id = "retriever-v4-treatment-run"
    lifecycle = EventSourcedSearchLifecycle(
        store=store,
        flags=store.flags,
        semantics=_semantics(),
        evaluator=_SkipEvaluator(),
        data_snapshot_hash=discovery.data_snapshot_hash,
    )
    search = AlphaFoundrySearch(
        seed_bank=SeedBank([AlphaSeed("control-seed", "close", "registry")]),
        mutator=SeedMutator(max_candidates_per_seed=3),
        max_candidates=3,
        trial_budget=3,
        lifecycle=lifecycle,
        run_id=control_run_id,
    )
    control = OfficialSearchControlServiceV1(store).record(search, search.generate())
    candidate = _candidate(query, discovery)
    recorded = RetrieverDecisionV4Service(store).record(
        control_evidence_event_hash=control.event.event_hash,
        candidates=(candidate,),
        data_snapshot_hash=discovery.data_snapshot_hash,
        eligible_event_watermark=discovery.source_watermark,
        seed=41,
        candidate_budget=1,
        control_run_id=control_run_id,
        run_id=decision_run_id,
    )
    return store, discovery, control, candidate, recorded


def test_v4_binds_replayed_control_event_and_topology_decision(tmp_path: Path) -> None:
    store, discovery, control, candidate, recorded = _record(tmp_path)
    assert recorded.event.event_type == "RetrieverDecisionV4Recorded"
    assert recorded.event.payload["control_evidence_event_hash"] == control.event.event_hash
    assert recorded.event.payload["control_policy_hash"] == control.evidence.policy.policy_hash
    assert recorded.decision.official_output_hash == control.evidence.output_hash
    assert recorded.decision.selected_factor_spec_ids == (candidate.factor_spec_id,)
    assert recorded.decision.eligible_event_watermark == discovery.source_watermark
    assert control.event.run_id == "retriever-v4-control-run"
    assert recorded.event.run_id == "retriever-v4-treatment-run"
    assert store.verify_chain()


def test_v4_rejects_reference_ast_not_bound_to_reference_factor(
    tmp_path: Path,
) -> None:
    store, discovery, control, candidate, _ = _record(tmp_path)
    forged = replace(candidate, reference_asts=(candidate.canonical_ast,))
    with pytest.raises(ValueError, match="reference AST does not match"):
        RetrieverDecisionV4Service(store).record(
            control_evidence_event_hash=control.event.event_hash,
            candidates=(forged,),
            data_snapshot_hash=discovery.data_snapshot_hash,
            eligible_event_watermark=discovery.source_watermark,
            seed=42,
            candidate_budget=1,
            control_run_id=control.event.run_id,
            run_id="retriever-v4-forged-reference-run",
        )


def test_v4_rejects_watermark_that_contains_same_pair_control_outcomes(
    tmp_path: Path,
) -> None:
    store, query, discovery = _views(tmp_path)
    run_id = "retriever-v4-leaked-control-run"
    lifecycle = EventSourcedSearchLifecycle(
        store=store,
        flags=store.flags,
        semantics=_semantics(),
        evaluator=_SuccessEvaluator(),
        data_snapshot_hash=discovery.data_snapshot_hash,
    )
    search = AlphaFoundrySearch(
        seed_bank=SeedBank(
            [AlphaSeed("leaked-control-seed", "delay(volume, 7)", "registry")]
        ),
        mutator=SeedMutator(max_candidates_per_seed=1),
        max_candidates=1,
        trial_budget=1,
        lifecycle=lifecycle,
        run_id=run_id,
    )
    control = OfficialSearchControlServiceV1(store).record(search, search.generate())
    leaked_watermark = next(
        event.event_hash
        for event in reversed(store.query_events(event_type="TrialTerminated"))
        if event.run_id == run_id
    )
    with pytest.raises(ValueError, match="includes control-arm outcomes"):
        RetrieverDecisionV4Service(store).record(
            control_evidence_event_hash=control.event.event_hash,
            candidates=(_candidate(query, discovery),),
            data_snapshot_hash=discovery.data_snapshot_hash,
            eligible_event_watermark=leaked_watermark,
            seed=41,
            candidate_budget=1,
            control_run_id=run_id,
            run_id=run_id,
        )


def test_v4_accepts_no_caller_official_candidate_ids(tmp_path: Path) -> None:
    store, query, discovery = _views(tmp_path)
    with pytest.raises(TypeError):
        RetrieverDecisionV4Service(store).record(  # type: ignore[call-arg]
            control_evidence_event_hash=canonical_json_hash({"control": "missing"}),
            official_candidate_ids=("caller",),
            candidates=(_candidate(query, discovery),),
            data_snapshot_hash=discovery.data_snapshot_hash,
            seed=1,
            candidate_budget=1,
            run_id="caller-run",
        )


def test_v4_requires_explicit_frozen_discovery_watermark(tmp_path: Path) -> None:
    store, query, discovery = _views(tmp_path)
    with pytest.raises(TypeError):
        RetrieverDecisionV4Service(store).record(  # type: ignore[call-arg]
            control_evidence_event_hash=canonical_json_hash({"control": "missing"}),
            candidates=(_candidate(query, discovery),),
            data_snapshot_hash=discovery.data_snapshot_hash,
            seed=1,
            candidate_budget=1,
            run_id="caller-run",
        )


def test_rehashed_v4_control_binding_fabrication_is_rejected(tmp_path: Path) -> None:
    store, _, _, _, recorded = _record(tmp_path)
    payload = recorded.event.to_dict()["payload"]
    payload["decision_id"] = "retriever-v4-forged"
    payload["control_evidence_event_hash"] = canonical_json_hash(
        {"forged": "control-event"}
    )
    payload["control_evidence_hash"] = canonical_json_hash(
        {"forged": "control-evidence"}
    )
    content = {
        "schema_version": "retriever_source_bound_decision.v4",
        **{
            key: value for key, value in payload.items()
            if key not in {"decision_id", "decision_hash", "artifact_refs"}
        },
    }
    payload["decision_hash"] = canonical_json_hash(content)
    with pytest.raises(
        EventValidationError,
        match="identity must derive|deterministic rebuild|source evidence",
    ):
        store.append_event(
            EventDraft(
                event_type="RetrieverDecisionV4Recorded",
                entity_id="retriever-v4-forged",
                run_id="retriever-v4-run",
                payload_schema_version="retriever_decision_recorded.v4",
                payload=payload,
                idempotency_key="retriever-v4:forged",
            )
        )


def test_v4_decision_cannot_be_reenveloped_under_another_arm_run(
    tmp_path: Path,
) -> None:
    store, _, _, _, recorded = _record(tmp_path)
    payload = recorded.event.to_dict()["payload"]
    with pytest.raises(EventTransitionError, match="identity already exists"):
        store.append_event(
            EventDraft(
                event_type="RetrieverDecisionV4Recorded",
                entity_id=payload["decision_id"],
                run_id="forged-other-arm-run",
                payload_schema_version="retriever_decision_recorded.v4",
                payload=payload,
                idempotency_key="retriever-v4:forged-other-arm-run",
            )
        )


def test_v4_bundle_tamper_breaks_chain(tmp_path: Path) -> None:
    store, _, _, _, recorded = _record(tmp_path)
    reference = recorded.event.payload["artifact_refs"][0]
    target = store.artifact_root.joinpath(*str(reference["relative_path"]).split("/"))
    target.write_text("{}\n", encoding="utf-8")
    assert store.verify_chain() is False
