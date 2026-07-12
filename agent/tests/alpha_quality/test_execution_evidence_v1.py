from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from src.alpha_quality.evaluation_contract import EXECUTION_POLICY_REFERENCES
from src.alpha_quality.execution_evidence_v1 import (
    EXECUTION_EVENT_TYPE,
    ExecutionEvidenceArtifactStoreV1,
    ExecutionEvidenceServiceV1,
    simulate_ashare_execution_v1,
)
from src.alpha_quality.execution_policy_v1 import AshareExecutionPolicyV1
from src.alpha_quality.predictive_evidence_v4 import PITPredictiveEvidenceServiceV4
from src.alpha_quality.production_evaluator_v1 import (
    ProductionCandidateEvaluatorFactoryV1,
    ProductionEvaluationRequestV1,
)
from src.research_ledger.events import EventDraft, EventValidationError
from tests.alpha_quality.test_predictive_evidence_v4 import _setup


def _inputs() -> dict[str, object]:
    dates = pd.date_range("2025-01-01", periods=6, freq="D")
    symbols = ["A", "B", "C"]
    factor = pd.DataFrame(
        [
            [3.0, 2.0, 1.0],
            [1.0, 3.0, 2.0],
            [1.0, 2.0, 3.0],
            [3.0, 2.0, 1.0],
            [3.0, 2.0, 1.0],
            [3.0, 2.0, 1.0],
        ],
        index=dates,
        columns=symbols,
    )
    close = pd.DataFrame(
        np.array(
            [
                [10.0, 20.0, 30.0],
                [10.1, 20.1, 30.1],
                [10.2, 20.2, 30.2],
                [10.3, 20.3, 30.3],
                [10.4, 20.4, 30.4],
                [10.5, 20.5, 30.5],
            ]
        ),
        index=dates,
        columns=symbols,
    )
    truth = pd.DataFrame(True, index=dates, columns=symbols, dtype=bool)
    return {
        "factor": factor,
        "close": close,
        "amount": pd.DataFrame(100_000_000.0, index=dates, columns=symbols),
        "membership": truth.copy(),
        "can_buy": truth.copy(),
        "can_sell": truth.copy(),
        "can_observe": truth.copy(),
        "corporate_actions": pd.DataFrame(
            columns=["announced_at", "effective_date", "factor", "symbol"]
        ),
        "entry_lag": 1,
        "rebalance_cadence": 1,
    }


def test_limit_up_blocks_buy_only_and_limit_down_blocks_sell_only() -> None:
    inputs = _inputs()
    dates = inputs["factor"].index  # type: ignore[union-attr]
    inputs["can_buy"].loc[dates[1], "A"] = False  # type: ignore[index,union-attr]
    result = simulate_ashare_execution_v1(**inputs)  # type: ignore[arg-type]
    assert result.states["buy_blocked_notional"].loc[dates[1], "A"] > 0.0
    assert result.states["filled_trades"].loc[dates[1], "A"] == 0.0

    inputs = _inputs()
    inputs["can_sell"].loc[dates[2], "A"] = False  # type: ignore[index,union-attr]
    result = simulate_ashare_execution_v1(**inputs)  # type: ignore[arg-type]
    assert result.states["sell_blocked_notional"].loc[dates[2], "A"] > 0.0
    assert result.states["actual_holdings"].loc[dates[2], "A"] > 0.0
    assert result.states["filled_trades"].loc[dates[2], "B"] > 0.0


def test_t_plus_one_sellable_quantity_transition() -> None:
    inputs = _inputs()
    dates = inputs["factor"].index  # type: ignore[union-attr]
    result = simulate_ashare_execution_v1(**inputs)  # type: ignore[arg-type]
    assert result.states["newly_bought_quantity"].loc[dates[1], "A"] > 0.0
    assert result.states["sellable_quantity"].loc[dates[1], "A"] == 0.0
    assert result.states["filled_trades"].loc[dates[2], "A"] < 0.0


def test_index_exit_blocks_new_buy_but_allows_exit_sell() -> None:
    inputs = _inputs()
    dates = inputs["factor"].index  # type: ignore[union-attr]
    inputs["membership"].loc[dates[2] :, "A"] = False  # type: ignore[index,union-attr]
    result = simulate_ashare_execution_v1(**inputs)  # type: ignore[arg-type]
    assert result.states["filled_trades"].loc[dates[2], "A"] < 0.0
    assert not (result.states["filled_trades"].loc[dates[2] :, "A"] > 0.0).any()


def test_suspension_preserves_holdings_and_records_unavailable_exposure() -> None:
    inputs = _inputs()
    dates = inputs["factor"].index  # type: ignore[union-attr]
    for name in ("can_buy", "can_sell", "can_observe"):
        inputs[name].loc[dates[2], "A"] = False  # type: ignore[index,union-attr]
    result = simulate_ashare_execution_v1(**inputs)  # type: ignore[arg-type]
    assert result.states["actual_holdings"].loc[dates[2], "A"] > 0.0
    assert result.states["unavailable_holding"].loc[dates[2], "A"] > 0.0
    assert result.aggregate.loc[dates[2], "unpriced_exposure"] > 0.0


def test_initial_and_terminal_costs_are_included() -> None:
    result = simulate_ashare_execution_v1(**_inputs())  # type: ignore[arg-type]
    assert result.aggregate["initial_entry_cost"].sum() > 0.0
    assert result.aggregate["terminal_exit_cost"].sum() > 0.0
    assert result.aggregate["cost_return"].sum() >= (
        result.aggregate["initial_entry_cost"].sum()
        + result.aggregate["terminal_exit_cost"].sum()
    )


def test_filled_trades_reconcile_from_actual_holdings() -> None:
    result = simulate_ashare_execution_v1(**_inputs())  # type: ignore[arg-type]
    holdings = result.states["actual_holdings"]
    expected = holdings.diff()
    expected.iloc[0] = holdings.iloc[0]
    pd.testing.assert_frame_equal(expected, result.states["filled_trades"])


def test_missing_required_return_does_not_silently_sum_subportfolio() -> None:
    inputs = _inputs()
    dates = inputs["factor"].index  # type: ignore[union-attr]
    inputs["close"].loc[dates[2], "A"] = np.nan  # type: ignore[index,union-attr]
    result = simulate_ashare_execution_v1(**inputs)  # type: ignore[arg-type]
    assert result.aggregate.loc[dates[2], "missing_return_exposure"] > 0.0
    assert pd.isna(result.aggregate.loc[dates[2], "gross_return"])
    assert pd.isna(result.aggregate.loc[dates[2], "net_return"])


def test_missing_outcome_scenarios_are_preregistered_and_hashed() -> None:
    result = simulate_ashare_execution_v1(**_inputs())  # type: ignore[arg-type]
    scenarios = result.scenario_evidence
    assert scenarios["policy_hash"].startswith("sha256:")
    assert tuple(sorted(scenarios["scenario_return_sums"])) == (
        "total_loss",
        "zero_return",
    )


def test_scenario_range_is_not_labelled_identified_bound() -> None:
    result = simulate_ashare_execution_v1(**_inputs())  # type: ignore[arg-type]
    assert result.scenario_evidence["scenario_semantics"] == (
        "policy_scenarios_not_identified_bounds.v1"
    )
    assert "bound" not in result.scenario_evidence


@pytest.mark.parametrize(
    "changes",
    [
        {"commission_bps": float("nan")},
        {"sell_slippage_bps": float("inf")},
        {"maximum_participation_rate": 0.0},
    ],
)
def test_unknown_or_nonfinite_cost_policy_is_rejected(
    changes: dict[str, float],
) -> None:
    with pytest.raises(ValueError):
        replace(AshareExecutionPolicyV1(), **changes)


def test_symbol_permutation_does_not_change_weights_or_execution() -> None:
    inputs = _inputs()
    original = simulate_ashare_execution_v1(**inputs)  # type: ignore[arg-type]
    reversed_inputs = dict(inputs)
    for name in (
        "factor",
        "close",
        "amount",
        "membership",
        "can_buy",
        "can_sell",
        "can_observe",
    ):
        reversed_inputs[name] = inputs[name].iloc[:, ::-1]  # type: ignore[index,union-attr]
    permuted = simulate_ashare_execution_v1(**reversed_inputs)  # type: ignore[arg-type]
    for name in original.states:
        pd.testing.assert_frame_equal(
            original.states[name].sort_index(axis=1),
            permuted.states[name].sort_index(axis=1),
        )
    pd.testing.assert_frame_equal(original.aggregate, permuted.aggregate)


def test_corporate_action_is_applied_once_without_fictitious_trade() -> None:
    inputs = _inputs()
    dates = inputs["factor"].index  # type: ignore[union-attr]
    inputs["factor"].loc[:, "A"] = 10.0  # type: ignore[index,union-attr]
    inputs["close"].loc[dates[2], "A"] = (  # type: ignore[index,union-attr]
        inputs["close"].loc[dates[1], "A"] / 2.0  # type: ignore[index,union-attr]
    )
    inputs["corporate_actions"] = pd.DataFrame(
        {
            "announced_at": ["2024-12-01T00:00:00Z"],
            "effective_date": [dates[2]],
            "factor": [2.0],
            "symbol": ["A"],
        }
    )
    result = simulate_ashare_execution_v1(**inputs)  # type: ignore[arg-type]
    assert result.aggregate.loc[dates[2], "gross_return"] == pytest.approx(0.0)
    assert result.states["filled_trades"].loc[dates[2], "A"] == pytest.approx(0.0)


def test_stale_or_delisted_price_creates_unavailable_holding() -> None:
    inputs = _inputs()
    dates = inputs["factor"].index  # type: ignore[union-attr]
    inputs["close"].loc[dates[2], "A"] = np.nan  # type: ignore[index,union-attr]
    inputs["can_observe"].loc[dates[2], "A"] = False  # type: ignore[index,union-attr]
    inputs["can_sell"].loc[dates[2], "A"] = False  # type: ignore[index,union-attr]
    result = simulate_ashare_execution_v1(**inputs)  # type: ignore[arg-type]
    assert result.material_unpriced_exposure
    assert "MATERIAL_UNPRICED_EXPOSURE" in result.caps
    assert (
        result.output_channels["ashare_implementable_long_only"]["implementability"]
        == "inconclusive"
    )


def test_rebalance_calendar_is_explicit_not_iterable_first() -> None:
    inputs = _inputs()
    inputs["rebalance_cadence"] = 2
    result = simulate_ashare_execution_v1(**inputs)  # type: ignore[arg-type]
    submitted = result.states["submitted_trades"].abs().sum(axis=1)
    assert submitted.iloc[2] == pytest.approx(0.0)
    assert submitted.iloc[3] > 0.0


def test_capacity_missing_is_typed_and_does_not_fail_open() -> None:
    inputs = _inputs()
    inputs["amount"].loc[:, :] = np.nan  # type: ignore[index,union-attr]
    result = simulate_ashare_execution_v1(**inputs)  # type: ignore[arg-type]
    assert result.availability == "unavailable"
    assert "CAPACITY_EVIDENCE_UNAVAILABLE" in result.caps
    assert result.states["filled_trades"].abs().sum().sum() == 0.0


def test_long_short_channel_is_not_labelled_cash_implementable() -> None:
    result = simulate_ashare_execution_v1(**_inputs())  # type: ignore[arg-type]
    assert result.output_channels["theoretical_long_short"]["implementability"] == (
        "research_theoretical_not_cash_implementable"
    )
    assert result.output_channels["hedged_implementable"]["availability"] == (
        "unavailable"
    )


def _recorded_execution(tmp_path):
    _, store, contract, snapshot, definition = _setup(
        tmp_path,
        EXECUTION_POLICY_REFERENCES,
    )
    predictive = PITPredictiveEvidenceServiceV4(store).record(
        run_id="predictive-run",
        contract_event_hash=contract.event.event_hash,
        factor_definition_event_hash=definition.event_hash,
        pit_snapshot_event_hash=snapshot.event.event_hash,
    )
    execution = ExecutionEvidenceServiceV1(store).record(
        run_id="predictive-run",
        factor_output_event_hash=predictive.factor_event.event_hash,
        observed_predictive_event_hash=predictive.observed_event.event_hash,
    )
    return store, predictive, execution


def test_actual_execution_replay_matches_artifact_hash(tmp_path) -> None:
    store, _, recorded = _recorded_execution(tmp_path)
    assert (
        recorded.artifact.execution_artifact_hash
        == recorded.event.payload["execution_artifact_hash"]
    )
    states, aggregate = ExecutionEvidenceArtifactStoreV1(
        store.artifact_root
    ).read_tables(recorded.artifact)
    assert set(states) == {
        "actual_holdings",
        "buy_blocked_notional",
        "filled_trades",
        "newly_bought_quantity",
        "sell_blocked_notional",
        "sellable_quantity",
        "submitted_trades",
        "target_weights",
        "unavailable_holding",
    }
    assert "net_return" in aggregate
    retry = ExecutionEvidenceServiceV1(store).record(
        run_id="predictive-run",
        factor_output_event_hash=recorded.artifact.factor_output_event_hash,
        observed_predictive_event_hash=recorded.event.payload[
            "observed_predictive_event_hash"
        ],
    )
    assert retry.event.event_hash == recorded.event.event_hash
    assert store.verify_chain()


def test_cross_policy_execution_is_rejected_but_predictive_is_preserved(
    tmp_path,
) -> None:
    _, store, contract, snapshot, definition = _setup(tmp_path)
    predictive = PITPredictiveEvidenceServiceV4(store).record(
        run_id="predictive-run",
        contract_event_hash=contract.event.event_hash,
        factor_definition_event_hash=definition.event_hash,
        pit_snapshot_event_hash=snapshot.event.event_hash,
    )
    with pytest.raises(EventValidationError, match="contract policy differs"):
        ExecutionEvidenceServiceV1(store).record(
            run_id="predictive-run",
            factor_output_event_hash=predictive.factor_event.event_hash,
            observed_predictive_event_hash=predictive.observed_event.event_hash,
        )
    assert (
        len(store.query_events(event_type="ObservedPanelPredictiveEvidenceRecorded"))
        == 1
    )
    assert not store.query_events(event_type=EXECUTION_EVENT_TYPE)


def test_execution_artifact_tamper_breaks_event_replay(tmp_path) -> None:
    store, _, recorded = _recorded_execution(tmp_path)
    reference = next(
        item
        for item in recorded.event.payload["artifact_refs"]
        if item["media_type"] == "application/vnd.vibe.execution-artifact-v1+json"
    )
    store.artifact_root.joinpath(
        *str(reference["relative_path"]).split("/")
    ).write_bytes(b"tampered")
    assert store.verify_chain() is False


def test_generic_append_cannot_bypass_execution_producer(tmp_path) -> None:
    store, _, recorded = _recorded_execution(tmp_path)
    with pytest.raises(EventValidationError, match="deterministic producer"):
        store.append_event(
            EventDraft(
                event_type=EXECUTION_EVENT_TYPE,
                entity_id=recorded.event.entity_id,
                run_id=recorded.event.run_id,
                payload_schema_version=recorded.event.payload_schema_version,
                payload=dict(recorded.event.payload),
            )
        )


def test_production_evaluator_runs_pit_predictive_then_execution(tmp_path) -> None:
    _, store, contract, snapshot, definition = _setup(
        tmp_path,
        EXECUTION_POLICY_REFERENCES,
    )
    request = ProductionEvaluationRequestV1(
        run_id="predictive-run",
        trial_id="predictive-trial",
        factor_definition_event_hash=definition.event_hash,
        resolved_contract_hash=contract.contract.contract_hash,
        snapshot_event_hash=snapshot.event.event_hash,
        source_watermark_event_hash=definition.event_hash,
        frozen_comparison_pool_hash=None,
    )
    result = ProductionCandidateEvaluatorFactoryV1.create(store).evaluate(request)
    executions = store.query_events(event_type=EXECUTION_EVENT_TYPE)
    nodes = [
        event
        for event in store.query_events(event_type="ProductionEvaluationNodeRecorded")
        if event.payload["node_name"] == "execution"
    ]
    assert result.completion_status == "partially_completed"
    assert len(executions) == len(nodes) == 1
    assert executions[0].event_hash in result.evidence_event_hashes
    assert nodes[0].payload["status"] in {"completed", "unavailable"}
    assert nodes[0].payload["source_event_hashes"] == (executions[0].event_hash,)
    assert store.verify_chain()
