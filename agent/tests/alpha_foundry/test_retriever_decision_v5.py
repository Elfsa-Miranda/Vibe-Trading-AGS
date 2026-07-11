from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src.alpha_foundry.control_evidence import OfficialSearchControlServiceV1
from src.alpha_foundry.mutators import SeedMutator
from src.alpha_foundry.retrieval.action_template_v1 import (
    RetrieverActionTemplateServiceV1,
)
from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy
from src.alpha_foundry.retrieval.service_v5 import RetrieverDecisionV5Service
from src.alpha_foundry.search import AlphaFoundrySearch
from src.alpha_foundry.search_lifecycle import (
    EventSourcedSearchLifecycle,
    SearchEvaluationOutcome,
)
from src.alpha_foundry.seed_bank import AlphaSeed, SeedBank
from src.research_ledger.events import EventDraft, EventValidationError
from src.research_ledger.hash_utils import canonical_json_hash
from test_retriever_shadow import _candidate, _views
from test_search_lifecycle import _semantics


class _SkipEvaluator:
    def evaluate(self, **kwargs):
        return SearchEvaluationOutcome(
            status="skip",
            decision="none",
            reason_codes=("CONTROL_V5_FIXTURE",),
        )


def _record(
    tmp_path: Path,
    *,
    template_ids: tuple[str, ...] = ("rank_wrap", "zscore_wrap"),
    candidate_budget: int = 2,
):
    store, query, discovery = _views(tmp_path)
    policy = ActivationRetrieverPolicy()
    treatment_run_id = "retriever-v5-treatment-run"
    action_service = RetrieverActionTemplateServiceV1(
        store=store,
        flags=store.flags,
    )
    parent_id = discovery.factual.factor_ids()[0]
    frozen = tuple(
        action_service.freeze(
            execution_run_id=treatment_run_id,
            parent_factor_spec_id=parent_id,
            template_id=template_id,
            eligible_event_watermark=discovery.source_watermark,
            data_snapshot_hash=discovery.data_snapshot_hash,
            retrieval_policy_hash=policy.policy_hash,
        )
        for template_id in template_ids
    )
    # Deliberately use non-lexicographic hash order: this list is positional
    # evidence aligned with candidates/components, not an unordered hash set.
    frozen = tuple(
        sorted(frozen, key=lambda item: item.event.event_hash, reverse=True)
    )

    control_run_id = "retriever-v5-control-run"
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
    base = _candidate(query, discovery)
    candidates = tuple(
        replace(
            base,
            action_id=item.action.action_id,
            motif=str(item.action.expected_motif),
        )
        for item in frozen
    )
    recorded = RetrieverDecisionV5Service(store, policy=policy).record(
        control_evidence_event_hash=control.event.event_hash,
        action_template_event_hashes=tuple(item.event.event_hash for item in frozen),
        candidates=candidates,
        data_snapshot_hash=discovery.data_snapshot_hash,
        eligible_event_watermark=discovery.source_watermark,
        seed=41,
        candidate_budget=candidate_budget,
        control_run_id=control_run_id,
        run_id=treatment_run_id,
    )
    return store, discovery, frozen, control, candidates, recorded


def test_v5_selects_exact_actions_and_allows_same_parent_multiple_templates(
    tmp_path: Path,
) -> None:
    store, discovery, frozen, control, candidates, recorded = _record(tmp_path)
    decision = recorded.decision
    assert recorded.event.event_type == "RetrieverDecisionV5Recorded"
    assert set(decision.selected_action_ids) == {
        candidate.action_id for candidate in candidates
    }
    assert decision.selected_parent_factor_spec_ids == (
        candidates[0].factor_spec_id,
        candidates[0].factor_spec_id,
    )
    assert len({component.factor_spec_id for component in decision.components}) == 1
    assert len({component.action_id for component in decision.components}) == 2
    assert sorted(component.selection_propensity for component in decision.components) == [
        0.5,
        1.0,
    ]
    assert decision.action_template_event_hashes == tuple(
        item.event.event_hash for item in frozen
    )
    assert decision.eligible_event_watermark == discovery.source_watermark
    assert decision.official_output_hash == control.evidence.output_hash
    assert store.verify_chain()


def test_v5_rejects_candidate_action_or_motif_substitution(tmp_path: Path) -> None:
    store, discovery, frozen, control, candidates, _ = _record(
        tmp_path,
        candidate_budget=1,
    )
    forged = replace(candidates[0], motif=candidates[1].motif)
    with pytest.raises(ValueError, match="differs from its frozen action"):
        RetrieverDecisionV5Service(store).record(
            control_evidence_event_hash=control.event.event_hash,
            action_template_event_hashes=(frozen[0].event.event_hash,),
            candidates=(forged,),
            data_snapshot_hash=discovery.data_snapshot_hash,
            eligible_event_watermark=discovery.source_watermark,
            seed=42,
            candidate_budget=1,
            control_run_id=control.event.run_id,
            run_id=frozen[0].event.run_id,
        )


def test_v5_identity_noop_is_not_an_eligible_retrieval_action(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="identity/no-op"):
        _record(tmp_path, template_ids=("identity",), candidate_budget=1)


def test_rehashed_v5_component_fabrication_is_rejected_by_source_replay(
    tmp_path: Path,
) -> None:
    store, _, _, _, _, recorded = _record(tmp_path)
    payload = recorded.event.to_dict()["payload"]
    payload["components"][0]["action_score"] += 100.0
    content = {
        "schema_version": "retriever_action_source_bound_decision.v5",
        **{
            key: value
            for key, value in payload.items()
            if key not in {"decision_id", "decision_hash", "artifact_refs"}
        },
    }
    payload["decision_hash"] = canonical_json_hash(content)
    payload["decision_id"] = (
        "retriever-v5-"
        + payload["decision_hash"].removeprefix("sha256:")[:20]
    )
    with pytest.raises(EventValidationError, match="deterministic rebuild"):
        store.append_event(
            EventDraft(
                event_type="RetrieverDecisionV5Recorded",
                entity_id=payload["decision_id"],
                run_id=recorded.event.run_id,
                payload_schema_version="retriever_decision_recorded.v5",
                payload=payload,
                idempotency_key="retriever-v5:forged-component",
            )
        )


def test_v5_action_templates_must_be_frozen_before_control_outcomes(
    tmp_path: Path,
) -> None:
    store, query, discovery = _views(tmp_path)
    policy = ActivationRetrieverPolicy()
    control_run_id = "v5-order-control"
    lifecycle = EventSourcedSearchLifecycle(
        store=store,
        flags=store.flags,
        semantics=_semantics(),
        evaluator=_SkipEvaluator(),
        data_snapshot_hash=discovery.data_snapshot_hash,
    )
    search = AlphaFoundrySearch(
        seed_bank=SeedBank([AlphaSeed("late", "close", "registry")]),
        mutator=SeedMutator(max_candidates_per_seed=1),
        max_candidates=1,
        trial_budget=1,
        lifecycle=lifecycle,
        run_id=control_run_id,
    )
    control = OfficialSearchControlServiceV1(store).record(search, search.generate())
    late = RetrieverActionTemplateServiceV1(
        store=store,
        flags=store.flags,
    ).freeze(
        execution_run_id="v5-order-treatment",
        parent_factor_spec_id=discovery.factual.factor_ids()[0],
        template_id="rank_wrap",
        eligible_event_watermark=discovery.source_watermark,
        data_snapshot_hash=discovery.data_snapshot_hash,
        retrieval_policy_hash=policy.policy_hash,
    )
    base = _candidate(query, discovery)
    candidate = replace(
        base,
        action_id=late.action.action_id,
        motif=str(late.action.expected_motif),
    )
    with pytest.raises(ValueError, match="before control"):
        RetrieverDecisionV5Service(store).record(
            control_evidence_event_hash=control.event.event_hash,
            action_template_event_hashes=(late.event.event_hash,),
            candidates=(candidate,),
            data_snapshot_hash=discovery.data_snapshot_hash,
            eligible_event_watermark=discovery.source_watermark,
            seed=1,
            candidate_budget=1,
            control_run_id=control_run_id,
            run_id=late.event.run_id,
        )
