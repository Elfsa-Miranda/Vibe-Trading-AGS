from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.alpha_foundry.dsl.identity import FactorIdentityService, FactorSpecSemantics
from src.alpha_quality.evaluation_contract.applicability import (
    ApplicabilityAssessmentServiceV1,
)
from src.alpha_quality.secondary_evidence_v1 import (
    ComparisonPoolMemberV1,
    ComparisonPoolServiceV1,
    SecondaryEvidencePolicyV1,
    SecondaryEvidenceServiceV1,
    assess_duplicate_identity,
    assess_portfolio_marginal_value,
    build_mechanism_assessment,
)
from src.alpha_quality.production_evaluator_v1 import (
    ProductionCandidateEvaluatorFactoryV1,
    ProductionEvaluationRequestV1,
)
from src.research_ledger.events import EventDraft, EventValidationError
from src.research_ledger.hash_utils import canonical_json_hash

from tests.alpha_quality.test_predictive_evidence_v4 import _record, _setup


def _hash(label: str) -> str:
    return canonical_json_hash({"label": label})


def _second_definition(store, first, *, formula: str = "rank(close)"):
    raw = first.payload["metadata"]["semantics"]
    fields = dict(raw["field_semantics"])
    if formula == "rank(open)":
        fields["open"] = "open_t"
    semantics = FactorSpecSemantics(
        transform_pipeline_hash=_hash("secondary-transform"),
        field_semantics=fields,
        signal_time=str(raw["signal_time"]),
        order_time=str(raw["order_time"]),
        entry_price_time=str(raw["entry_price_time"]),
        execution_lag=int(raw["execution_lag"]),
        return_horizon=int(raw["return_horizon"]),
        universe_mask_hash=str(raw["universe_mask_hash"]),
        tradability_mask_hash=str(raw["tradability_mask_hash"]),
    )
    result = FactorIdentityService(store=store, flags=store.flags).record_attempt(
        trial_id="secondary-pool-trial",
        run_id="predictive-run",
        candidate_id="secondary-pool-candidate",
        formula=formula,
        semantics=semantics,
    )
    return store.query_events(
        event_type="FactorDefinitionRecorded", entity_id=str(result.factor_spec_id)
    )[0]


def _applicability(store, contract, definition):
    return ApplicabilityAssessmentServiceV1(store, flags=store.flags).assess(
        run_id="predictive-run",
        contract_event_hash=contract.event.event_hash,
        factor_definition_event_hash=definition.event_hash,
        claim_type="mechanism",
    )


def test_exact_duplicate_runs_without_positive_predictive_metric(tmp_path: Path) -> None:
    _, store, _, _, candidate = _setup(tmp_path)
    pool_definition = _second_definition(store, candidate)
    assessment = assess_duplicate_identity(candidate, [pool_definition])
    assert assessment["duplicate_detected"] is True
    assert "EXACT_EXPRESSION_DUPLICATE" in assessment["duplicate_reasons"]


def test_duplicate_rejects_novelty_but_preserves_replication_claim(tmp_path: Path) -> None:
    _, store, _, _, candidate = _setup(tmp_path)
    assessment = assess_duplicate_identity(candidate, [_second_definition(store, candidate)])
    assert assessment["novelty_claim"] == "rejected"
    assert assessment["replication_claim"] == "preserved"


def test_sign_flipped_formula_is_an_identity_duplicate(tmp_path: Path) -> None:
    _, store, _, _, candidate = _setup(tmp_path)
    sign_flip = _second_definition(store, candidate, formula="neg(rank(close))")
    assessment = assess_duplicate_identity(candidate, [sign_flip])
    assert "SIGN_NORMALIZED_DUPLICATE" in assessment["duplicate_reasons"]
    assert assessment["novelty_claim"] == "rejected"


def test_complement_does_not_depend_on_current_candidate_decision() -> None:
    rng = np.random.default_rng(454)
    pool = pd.Series(rng.normal(0.0001, 0.01, 120))
    candidate = pd.Series(rng.normal(0.0015, 0.003, 120))
    result = assess_portfolio_marginal_value(
        pool_net_returns=pool,
        candidate_net_returns=candidate,
        policy=SecondaryEvidencePolicyV1(),
    )
    assert "decision" not in result
    assert result["status"] in {"complementary", "inconclusive", "nonpositive"}


def test_complement_requires_frozen_comparison_pool(tmp_path: Path) -> None:
    _, store, contract, _, definition, recorded = _record(tmp_path)
    applicability = _applicability(store, contract, definition)
    with pytest.raises(Exception, match="sources are incomplete"):
        SecondaryEvidenceServiceV1(store).record(
            run_id="predictive-run",
            factor_definition_event_hash=definition.event_hash,
            comparison_pool_hash=_hash("caller-pool"),
            factor_output_event_hash=recorded.factor_event.event_hash,
            execution_event_hash=None,
            applicability_event_hash=applicability.event.event_hash,
        )


def test_complement_lower_confidence_bound_must_exceed_sesoi() -> None:
    rng = np.random.default_rng(9)
    pool = pd.Series(rng.normal(0.0, 0.01, 180))
    candidate = pd.Series(rng.normal(0.003, 0.002, 180))
    policy = replace(SecondaryEvidencePolicyV1(), marginal_value_sesoi=-100.0)
    result = assess_portfolio_marginal_value(
        pool_net_returns=pool, candidate_net_returns=candidate, policy=policy
    )
    assert result["confidence_interval"][0] > policy.marginal_value_sesoi
    assert result["status"] == "complementary"


def test_interval_crossing_zero_is_inconclusive() -> None:
    rng = np.random.default_rng(3)
    pool = pd.Series(rng.normal(0.0, 0.01, 180))
    result = assess_portfolio_marginal_value(
        pool_net_returns=pool,
        candidate_net_returns=pool.copy(),
        policy=SecondaryEvidencePolicyV1(),
    )
    assert result["confidence_interval"][0] <= 0.0 <= result["confidence_interval"][1]
    assert result["status"] == "inconclusive"


def test_caller_precomputed_complement_status_cannot_mint_evidence(tmp_path: Path) -> None:
    _, store, _, _, definition = _setup(tmp_path)
    with pytest.raises(EventValidationError, match="deterministic producer"):
        store.append_event(
            EventDraft(
                event_type="SecondaryEvidenceRecorded",
                entity_id="caller-secondary",
                run_id="predictive-run",
                payload_schema_version="secondary_evidence_recorded.v1",
                payload={"factor_spec_id": definition.entity_id, "portfolio_status": "complementary"},
            )
        )


def test_applicability_rule_is_replayed_from_contract(tmp_path: Path) -> None:
    _, store, contract, _, definition = _setup(tmp_path)
    applicability = _applicability(store, contract, definition)
    assessment = build_mechanism_assessment(applicability.assessment)
    assert applicability.assessment.applicability_rule_id == "mechanism_claim_declared.v1"
    assert assessment["applicability"] == "not_applicable"


def test_not_applicable_cannot_be_self_declared(tmp_path: Path) -> None:
    _, store, contract, _, definition = _setup(tmp_path)
    produced = _applicability(store, contract, definition)
    with pytest.raises(EventValidationError, match="deterministic producer"):
        store.append_event(
            EventDraft(
                event_type="ApplicabilityAssessmentRecorded",
                entity_id="caller-na",
                run_id="predictive-run",
                payload_schema_version="applicability_assessment_recorded.v1",
                payload=produced.event.payload,
            )
        )


def test_mechanism_falsification_does_not_rewrite_predictive_claim(tmp_path: Path) -> None:
    _, store, contract, _, definition = _setup(tmp_path)
    applicability = _applicability(store, contract, definition)
    # This fixture has N/A applicability; build a producer-shaped applicable copy
    # solely to exercise the pure separation rule.
    raw = applicability.assessment.to_dict()
    raw["result"] = "applicable"
    raw["reason_code"] = "MECHANISM_DECLARED_IN_FACTOR_SEMANTICS"
    raw.pop("assessment_hash")
    raw["evaluated_input_hashes"] = tuple(raw["evaluated_input_hashes"])
    raw["source_event_hashes"] = tuple(raw["source_event_hashes"])
    from src.alpha_quality.evaluation_contract.applicability import ApplicabilityAssessmentV1

    applicable = ApplicabilityAssessmentV1(
        **raw,
        assessment_hash=canonical_json_hash(raw),
    )
    assessment = build_mechanism_assessment(
        applicable,
        formal_result={"status": "falsified", "result_hash": _hash("formal-result")},
    )
    assert assessment["status"] == "falsified"
    assert assessment["predictive_claim_effect"] == "none"


def test_pool_freeze_is_protected_and_replayable(tmp_path: Path) -> None:
    _, store, _, _, definition = _setup(tmp_path)
    pool, event = ComparisonPoolServiceV1(store).freeze(
        run_id="predictive-run",
        source_watermark_event_hash=definition.event_hash,
        members=(),
    )
    assert event.payload["comparison_pool_hash"] == pool.comparison_pool_hash
    assert store.verify_chain()
    with pytest.raises(EventValidationError, match="deterministic producer"):
        store.append_event(
            EventDraft(
                event_type="ComparisonPoolFrozen",
                entity_id=event.entity_id,
                run_id="predictive-run",
                payload_schema_version="comparison_pool_frozen.v1",
                payload=event.payload,
            )
        )


def test_pool_tamper_and_cross_factor_source_are_rejected(tmp_path: Path) -> None:
    _, store, _, _, definition = _setup(tmp_path)
    other = _second_definition(store, definition, formula="rank(open)")
    member = ComparisonPoolMemberV1(
        factor_spec_id=definition.entity_id,
        factor_definition_event_hash=other.event_hash,
    )
    with pytest.raises(Exception, match="definition binding"):
        ComparisonPoolServiceV1(store).freeze(
            run_id="predictive-run",
            source_watermark_event_hash=other.event_hash,
            members=(member,),
        )


def test_missing_execution_preserves_predictive_and_records_secondary(tmp_path: Path) -> None:
    _, store, contract, _, definition, recorded = _record(tmp_path)
    applicability = _applicability(store, contract, definition)
    pool, _ = ComparisonPoolServiceV1(store).freeze(
        run_id="predictive-run",
        source_watermark_event_hash=applicability.event.event_hash,
        members=(),
    )
    event = SecondaryEvidenceServiceV1(store).record(
        run_id="predictive-run",
        factor_definition_event_hash=definition.event_hash,
        comparison_pool_hash=pool.comparison_pool_hash,
        factor_output_event_hash=recorded.factor_event.event_hash,
        execution_event_hash=None,
        applicability_event_hash=applicability.event.event_hash,
    )
    assert event.payload["portfolio_status"] == "unavailable"
    assert event.payload["promotion_effect"] == "none"
    assert store.query_events(event_type="PITPredictiveEvidenceRecorded")
    assert store.verify_chain()


def test_sesoi_boundary_is_not_complementary() -> None:
    rng = np.random.default_rng(17)
    pool = pd.Series(rng.normal(0.0, 0.01, 140))
    result = assess_portfolio_marginal_value(
        pool_net_returns=pool,
        candidate_net_returns=pool.copy(),
        policy=replace(SecondaryEvidencePolicyV1(), marginal_value_sesoi=0.0),
    )
    assert result["status"] == "inconclusive"


def test_production_evaluator_consumes_frozen_pool_without_minting_decision(
    tmp_path: Path,
) -> None:
    _, store, contract, snapshot, definition = _setup(tmp_path)
    pool, _ = ComparisonPoolServiceV1(store).freeze(
        run_id="predictive-run",
        source_watermark_event_hash=definition.event_hash,
        members=(),
    )
    request = ProductionEvaluationRequestV1(
        run_id="predictive-run",
        trial_id="predictive-trial",
        factor_definition_event_hash=definition.event_hash,
        resolved_contract_hash=contract.contract.contract_hash,
        snapshot_event_hash=snapshot.event.event_hash,
        source_watermark_event_hash=definition.event_hash,
        frozen_comparison_pool_hash=pool.comparison_pool_hash,
    )
    result = ProductionCandidateEvaluatorFactoryV1.create(store).evaluate(request)
    secondary = store.query_events(event_type="SecondaryEvidenceRecorded")
    assert len(secondary) == 1
    assert secondary[0].event_hash in result.evidence_event_hashes
    assert result.quality_decision_event_hash is None
    assert store.verify_chain()
