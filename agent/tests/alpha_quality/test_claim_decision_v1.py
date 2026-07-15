from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.alpha_foundry.dsl.identity import FactorIdentityService, FactorSpecSemantics
from src.alpha_quality.claim_decision_v1 import (
    TIER_INVARIANT_MANIFEST_HASH,
    ClaimDecisionServiceV1,
    SelectionAssessmentV1,
    build_claim_matrix,
    decide_narrow,
)
from src.alpha_quality.evaluation_contract.applicability import (
    ApplicabilityAssessmentServiceV1,
)
from src.alpha_quality.predictive_evidence_v4 import PITPredictiveEvidenceServiceV4
from src.alpha_quality.production_evaluator_v1 import (
    ProductionCandidateEvaluatorFactoryV1,
    ProductionEvaluationRequestV1,
)
from src.alpha_quality.secondary_evidence_v1 import (
    ComparisonPoolServiceV1,
    SecondaryEvidenceServiceV1,
)
from src.research_ledger.events import EventDraft, EventTransitionError, EventValidationError
from src.research_ledger.hash_utils import canonical_json_hash, utc_now_iso
from tests.alpha_quality.test_predictive_evidence_v4 import _setup


def _hash(label: str) -> str:
    return canonical_json_hash({"label": label})


def _event(event_type: str, payload: dict, label: str = "event"):
    return SimpleNamespace(
        event_type=event_type,
        event_hash=_hash(label),
        run_id="predictive-run",
        payload=payload,
    )


def _selection(*, confirmatory: bool = True) -> SelectionAssessmentV1:
    content = {
        "schema_version": "selection_assessment.v1",
        "research_family_id": _hash("family"),
        "run_id": "predictive-run",
        "trial_count": 1,
        "candidate_count": 1,
        "terminal_counts": {
            "success": 0,
            "reject": 0,
            "skip": 0,
            "invalid": 0,
            "duplicate": 0,
            "timeout": 0,
            "error": 0,
            "infrastructure_failure": 0,
        },
        "unpublished_or_open_trial_count": 0 if confirmatory else 1,
        "selection_policy_hash": _hash("selection"),
        "multiplicity_policy_hash": _hash("multiplicity"),
        "effective_independent_run_groups": 1,
        "final_access_count": 0,
        "confirmatory_grade_eligible": confirmatory,
        "source_event_hashes": [_hash("source")],
    }
    return SelectionAssessmentV1(
        research_family_id=content["research_family_id"],
        run_id="predictive-run",
        trial_count=1,
        candidate_count=1,
        terminal_counts=content["terminal_counts"],
        unpublished_or_open_trial_count=content["unpublished_or_open_trial_count"],
        selection_policy_hash=content["selection_policy_hash"],
        multiplicity_policy_hash=content["multiplicity_policy_hash"],
        effective_independent_run_groups=1,
        final_access_count=0,
        confirmatory_grade_eligible=confirmatory,
        source_event_hashes=tuple(content["source_event_hashes"]),
        assessment_hash=canonical_json_hash(content),
    )


def _sources(
    *,
    pit_available: bool = True,
    implementability: str = "supported",
    duplicate: bool = False,
    mechanism: str = "not_applicable",
    portfolio: str = "inconclusive",
):
    factor_id = "factor-1"
    observed = _event(
        "ObservedPanelPredictiveEvidenceRecorded",
        {
            "factor_spec_id": factor_id,
            "availability": "available",
            "claim_scope": "observed panel valid",
            "split_metrics": {"valid": {"rank_ic_mean": 0.03}},
            "warnings": ["OBSERVED_PANEL_SURVIVORSHIP_BIAS"],
        },
        "observed",
    )
    pit = _event(
        "PITPredictiveEvidenceRecorded",
        {
            "factor_spec_id": factor_id,
            "availability": "available" if pit_available else "unavailable",
            "pit_authority": {"decision_grade": pit_available},
            "claim_scope": "PIT CSI300 valid",
            "split_metrics": {"valid": {"rank_ic_mean": 0.02}},
            "warnings": [],
            "caps": [] if pit_available else ["PIT_AUTHORITY_UNAVAILABLE"],
        },
        "pit",
    )
    execution = _event(
        "ExecutionEvidenceRecorded",
        {
            "factor_spec_id": factor_id,
            "implementability_claim": implementability,
        },
        "execution",
    )
    secondary = _event(
        "SecondaryEvidenceRecorded",
        {
            "factor_spec_id": factor_id,
            "duplicate_detected": duplicate,
            "residual_status": "available",
            "portfolio_status": portfolio,
            "mechanism_status": mechanism,
        },
        "secondary",
    )
    return factor_id, observed, pit, execution, secondary


def _claims(**kwargs):
    factor_id, observed, pit, execution, secondary = _sources(**kwargs)
    selection = _selection()
    claims, matrix_hash = build_claim_matrix(
        factor_spec_id=factor_id,
        observed=observed,
        pit=pit,
        execution=execution,
        secondary=secondary,
        selection=selection,
    )
    return {claim.claim_type: claim for claim in claims}, claims, matrix_hash, selection


def _contract(*, authority: str = "build_time_allowlisted", ceiling: str = "forward_track"):
    return {
        "profile_template_hash": _hash("profile"),
        "profile_authority_class": authority,
        "maximum_promotion": ceiling,
    }


def test_cost_failure_changes_implementability_not_predictive_claim() -> None:
    claims, _, _, _ = _claims(implementability="unavailable")
    assert claims["ashare_implementability"].verdict == "blocked"
    assert claims["pit_scoped_predictive_signal"].verdict == "supported"


def test_mechanism_falsified_changes_mechanism_not_predictive_claim() -> None:
    claims, _, _, _ = _claims(mechanism="falsified")
    assert claims["mechanism"].verdict == "rejected"
    assert claims["pit_scoped_predictive_signal"].verdict == "supported"


def test_duplicate_changes_novelty_not_replication_claim() -> None:
    claims, ordered, matrix_hash, selection = _claims(duplicate=True)
    assert claims["novelty"].verdict == "rejected"
    assert claims["replication"].verdict == "supported"
    decision = decide_narrow(
        factor_spec_id="factor-1",
        claims=ordered,
        claim_matrix_hash=matrix_hash,
        selection=selection,
        claim_event_hash=_hash("duplicate-claim-event"),
        selection_event_hash=_hash("duplicate-selection-event"),
        contract_payload=_contract(),
    )
    assert decision.decision == "reject"


def test_missing_pit_blocks_generalization_not_observed_panel_claim() -> None:
    claims, _, _, _ = _claims(pit_available=False)
    assert claims["pit_historical_universe_generalization"].verdict == "blocked"
    assert claims["observed_panel_predictive_association"].verdict == "supported"


def test_blocked_claim_has_complete_root_cause_chain() -> None:
    claims, _, _, _ = _claims(pit_available=False)
    blocked = claims["pit_scoped_predictive_signal"]
    assert blocked.blocker_codes
    assert blocked.root_cause_event_hashes


def test_report_or_llm_text_cannot_mint_claim_assessment(tmp_path: Path) -> None:
    _, store, _, _, _ = _setup(tmp_path, enable_decision=True)
    assert "report" not in inspect.signature(ClaimDecisionServiceV1.record).parameters
    assert "llm" not in inspect.signature(ClaimDecisionServiceV1.record).parameters
    with pytest.raises(EventValidationError, match="deterministic producer"):
        store.append_event(
            EventDraft(
                event_type="ClaimMatrixRecorded",
                entity_id="caller-claim",
                run_id="predictive-run",
                payload_schema_version="claim_matrix_recorded.v1",
                payload={"report": "candidate is great"},
            )
        )


def test_policy_cannot_disable_tier_invariants() -> None:
    assert "policy" not in inspect.signature(decide_narrow).parameters
    assert TIER_INVARIANT_MANIFEST_HASH == canonical_json_hash(
        {
            "schema_version": "narrow_decision_tier_invariants.v1",
            "tier_0": [
                "invalid_or_ambiguous_formula",
                "physical_leakage",
                "non_control_duplicate",
                "cost_exceeds_execution_alpha",
            ],
            "tier_1": [
                "missing_pit_or_execution_authority",
                "non_reproducible_or_incomplete_selection",
                "decisive_mechanism_falsified_or_unavailable",
                "negative_portfolio_marginal_value",
            ],
            "tier_2": "terminal_train_valid_only",
            "final_forward_absent": "candidate_zoo_ceiling",
            "soft_scores_cross_tier": False,
        }
    )


def test_custom_profile_cannot_reach_candidate_zoo() -> None:
    _, claims, matrix_hash, selection = _claims(portfolio="complementary")
    decision = decide_narrow(
        factor_spec_id="factor-1",
        claims=claims,
        claim_matrix_hash=matrix_hash,
        selection=selection,
        claim_event_hash=_hash("claim-event"),
        selection_event_hash=_hash("selection-event"),
        contract_payload=_contract(authority="custom_research_only", ceiling="research_only"),
    )
    assert decision.decision == "research_only"


def test_qualified_train_valid_bundle_can_reach_candidate_zoo_only() -> None:
    _, claims, matrix_hash, selection = _claims(portfolio="complementary")
    decision = decide_narrow(
        factor_spec_id="factor-1",
        claims=claims,
        claim_matrix_hash=matrix_hash,
        selection=selection,
        claim_event_hash=_hash("qualified-claim-event"),
        selection_event_hash=_hash("qualified-selection-event"),
        contract_payload=_contract(),
    )
    assert decision.decision == "candidate_zoo"
    assert decision.tier == 2
    assert "FINAL_TEST_NOT_OPENED" in decision.limitations


def test_legacy_records_remain_capped_and_cannot_enter_narrow_sources(tmp_path: Path) -> None:
    _, store, _, _, _ = _setup(tmp_path, enable_decision=True)
    with pytest.raises(EventTransitionError, match="protected sources"):
        ClaimDecisionServiceV1(store).record(
            run_id="predictive-run",
            trial_id="predictive-trial",
            factor_spec_id="factor-1",
            contract_event_hash=_hash("missing"),
            observed_event_hash=_hash("legacy-scorecard"),
            pit_event_hash=_hash("legacy-pit"),
            execution_event_hash=None,
            secondary_event_hash=_hash("legacy-complement"),
        )


def test_quality_decision_consumes_only_protected_claim_events(tmp_path: Path) -> None:
    _, store, _, _, _ = _setup(tmp_path, enable_decision=True)
    with pytest.raises(EventValidationError, match="deterministic producer"):
        store.append_event(
            EventDraft(
                event_type="QualityDecisionV4Recorded",
                entity_id="caller-decision",
                run_id="predictive-run",
                payload_schema_version="quality_decision_recorded.v4",
                payload={"decision": "candidate_zoo", "score": 1.0},
            )
        )


def _record_claim_decision(tmp_path: Path, *, add_open_trial: bool = False):
    flags, store, contract, snapshot, definition = _setup(
        tmp_path, enable_decision=True
    )
    recorded = PITPredictiveEvidenceServiceV4(store).record(
        run_id="predictive-run",
        contract_event_hash=contract.event.event_hash,
        factor_definition_event_hash=definition.event_hash,
        pit_snapshot_event_hash=snapshot.event.event_hash,
    )
    applicability = ApplicabilityAssessmentServiceV1(store, flags=flags).assess(
        run_id="predictive-run",
        contract_event_hash=contract.event.event_hash,
        factor_definition_event_hash=definition.event_hash,
        claim_type="mechanism",
    )
    pool, _ = ComparisonPoolServiceV1(store).freeze(
        run_id="predictive-run",
        source_watermark_event_hash=applicability.event.event_hash,
        members=(),
    )
    secondary = SecondaryEvidenceServiceV1(store).record(
        run_id="predictive-run",
        factor_definition_event_hash=definition.event_hash,
        comparison_pool_hash=pool.comparison_pool_hash,
        factor_output_event_hash=recorded.factor_event.event_hash,
        execution_event_hash=None,
        applicability_event_hash=applicability.event.event_hash,
    )
    if add_open_trial:
        store.append_event(
            EventDraft(
                event_type="TrialStarted",
                entity_id="unpublished-trial",
                run_id="predictive-run",
                payload_schema_version="trial_started.v1",
                payload={
                    "trial_id": "unpublished-trial",
                    "candidate_id": "unpublished-candidate",
                    "data_scope": "train_valid",
                    "objective": "selection_population",
                    "started_at": utc_now_iso(),
                },
            )
        )
    result = ClaimDecisionServiceV1(store).record(
        run_id="predictive-run",
        trial_id="predictive-trial",
        factor_spec_id=definition.entity_id,
        contract_event_hash=contract.event.event_hash,
        observed_event_hash=recorded.observed_event.event_hash,
        pit_event_hash=recorded.pit_event.event_hash,
        execution_event_hash=None,
        secondary_event_hash=secondary.event_hash,
    )
    return store, result


def test_selection_assessment_counts_all_attempts(tmp_path: Path) -> None:
    store, (selection, _, _, _) = _record_claim_decision(tmp_path, add_open_trial=True)
    assert selection.payload["trial_count"] == 2
    assert selection.payload["candidate_count"] == 2
    assert selection.payload["unpublished_or_open_trial_count"] == 1
    assert store.verify_chain()


def test_selective_nonpublication_removes_confirmatory_grade(tmp_path: Path) -> None:
    _, (selection, claims, decision, _) = _record_claim_decision(
        tmp_path, add_open_trial=True
    )
    pit_claim = next(
        item for item in claims.payload["claims"]
        if item["claim_type"] == "pit_scoped_predictive_signal"
    )
    assert selection.payload["confirmatory_grade_eligible"] is False
    assert pit_claim["evidence_grade"] != "decision_grade"
    assert decision.payload["decision"] == "research_only"


def test_exact_bundle_replays_same_claim_and_decision_hashes(tmp_path: Path) -> None:
    store, first = _record_claim_decision(tmp_path)
    second = ClaimDecisionServiceV1(store).record(
        run_id="predictive-run",
        trial_id="predictive-trial",
        factor_spec_id=first[1].payload["factor_spec_id"],
        contract_event_hash=next(
            event.event_hash for event in store.query_events()
            if event.event_type == "ResolvedEvaluationContractRegistered"
        ),
        observed_event_hash=next(
            event.event_hash for event in store.query_events()
            if event.event_type == "ObservedPanelPredictiveEvidenceRecorded"
        ),
        pit_event_hash=next(
            event.event_hash for event in store.query_events()
            if event.event_type == "PITPredictiveEvidenceRecorded"
        ),
        execution_event_hash=None,
        secondary_event_hash=next(
            event.event_hash for event in store.query_events()
            if event.event_type == "SecondaryEvidenceRecorded"
        ),
    )
    assert second[1].payload["claim_matrix_hash"] == first[1].payload["claim_matrix_hash"]
    assert second[2].payload["decision_hash"] == first[2].payload["decision_hash"]


def test_two_candidates_in_one_production_run_get_independent_decisions(
    tmp_path: Path,
) -> None:
    flags, store, contract, snapshot, _ = _setup(tmp_path, enable_decision=True)

    decisions = []
    for ordinal, formula in enumerate(("rank(high)", "rank(open)"), start=1):
        trial_id = f"predictive-trial-{ordinal}"
        identity = FactorIdentityService(store=store, flags=flags).record_attempt(
            trial_id=trial_id,
            run_id="predictive-run",
            candidate_id=f"predictive-candidate-{ordinal}",
            formula=formula,
            semantics=FactorSpecSemantics(
                transform_pipeline_hash=_hash("identity-transform"),
                field_semantics={
                    "close": "close_t",
                    "open": "open_t",
                    "high": "high_t",
                },
                signal_time="close_t",
                order_time="close_t_plus_1",
                entry_price_time="open_t_plus_1",
                execution_lag=1,
                return_horizon=1,
                universe_mask_hash=_hash("pit-universe-mask"),
                tradability_mask_hash=_hash("pit-tradability-mask"),
            ),
        )
        definition = store.query_events(
            event_type="FactorDefinitionRecorded",
            entity_id=str(identity.factor_spec_id),
        )[0]
        predictive = PITPredictiveEvidenceServiceV4(store).record(
            run_id="predictive-run",
            contract_event_hash=contract.event.event_hash,
            factor_definition_event_hash=definition.event_hash,
            pit_snapshot_event_hash=snapshot.event.event_hash,
        )
        applicability = ApplicabilityAssessmentServiceV1(store, flags=flags).assess(
            run_id="predictive-run",
            contract_event_hash=contract.event.event_hash,
            factor_definition_event_hash=definition.event_hash,
            claim_type="mechanism",
        )
        pool, _ = ComparisonPoolServiceV1(store).freeze(
            run_id="predictive-run",
            source_watermark_event_hash=applicability.event.event_hash,
            members=(),
        )
        secondary = SecondaryEvidenceServiceV1(store).record(
            run_id="predictive-run",
            factor_definition_event_hash=definition.event_hash,
            comparison_pool_hash=pool.comparison_pool_hash,
            factor_output_event_hash=predictive.factor_event.event_hash,
            execution_event_hash=None,
            applicability_event_hash=applicability.event.event_hash,
        )
        decisions.append(
            ClaimDecisionServiceV1(store).record(
                run_id="predictive-run",
                trial_id=trial_id,
                factor_spec_id=definition.entity_id,
                contract_event_hash=contract.event.event_hash,
                observed_event_hash=predictive.observed_event.event_hash,
                pit_event_hash=predictive.pit_event.event_hash,
                execution_event_hash=None,
                secondary_event_hash=secondary.event_hash,
            )
        )

    assert decisions[0][0].event_hash != decisions[1][0].event_hash
    assert decisions[0][2].event_hash != decisions[1][2].event_hash
    assert len(store.query_events(event_type="SelectionAssessmentRecorded")) == 2
    assert len(store.query_events(event_type="QualityDecisionV4Recorded")) == 2
    assert store.verify_chain()


def test_production_evaluator_emits_unique_narrow_decision(tmp_path: Path) -> None:
    _, store, contract, snapshot, definition = _setup(
        tmp_path, enable_decision=True
    )
    pool, _ = ComparisonPoolServiceV1(store).freeze(
        run_id="predictive-run",
        source_watermark_event_hash=definition.event_hash,
        members=(),
    )
    result = ProductionCandidateEvaluatorFactoryV1.create(store).evaluate(
        ProductionEvaluationRequestV1(
            run_id="predictive-run",
            trial_id="predictive-trial",
            factor_definition_event_hash=definition.event_hash,
            resolved_contract_hash=contract.contract.contract_hash,
            snapshot_event_hash=snapshot.event.event_hash,
            source_watermark_event_hash=definition.event_hash,
            frozen_comparison_pool_hash=pool.comparison_pool_hash,
        )
    )
    decisions = store.query_events(event_type="QualityDecisionV4Recorded")
    assert len(decisions) == 1
    assert result.completion_status == "completed"
    assert result.quality_decision_event_hash == decisions[0].event_hash
    assert decisions[0].payload["decision"] == "research_only"
    assert store.verify_chain()
