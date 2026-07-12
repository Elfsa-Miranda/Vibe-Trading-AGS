from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from src.alpha_quality.portfolio_v2 import (
    ExecutionCostPolicyV1,
    WeightingPolicyV2,
    compute_strict_execution_v2,
    construct_target_weights_v2,
)


def _factor(values: list[float], columns: list[str] | None = None) -> pd.DataFrame:
    symbols = columns or [chr(ord("A") + index) for index in range(len(values))]
    return pd.DataFrame(
        [values],
        index=pd.DatetimeIndex(["2020-01-01"]),
        columns=symbols,
    )


def _mask(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(True, index=frame.index, columns=frame.columns, dtype=bool)


def _quantile_policy() -> WeightingPolicyV2:
    return WeightingPolicyV2(
        method="quantile_long_short.v2",
        long_quantile=0.34,
        short_quantile=0.34,
        gross_leverage=1.0,
        minimum_names_per_side=1,
    )


def test_constant_factor_produces_no_trade_not_column_order_portfolio() -> None:
    first = _factor([1.0] * 5)
    reversed_frame = first.loc[:, list(reversed(first.columns))]

    left = construct_target_weights_v2(first, _mask(first), _quantile_policy())
    right = construct_target_weights_v2(
        reversed_frame,
        _mask(reversed_frame),
        _quantile_policy(),
    )

    assert left.weights.eq(0.0).all().all()
    assert right.weights.eq(0.0).all().all()
    assert set(left.reason_by_date.values()) == {"NO_TRADE_CONSTANT_FACTOR"}
    assert set(right.reason_by_date.values()) == {"NO_TRADE_CONSTANT_FACTOR"}


@pytest.mark.parametrize(
    "policy",
    [
        _quantile_policy(),
        WeightingPolicyV2(
            method="continuous_rank_long_short.v2",
            long_quantile=0.2,
            short_quantile=0.2,
            gross_leverage=1.0,
            minimum_names_per_side=1,
        ),
    ],
)
def test_weighting_is_invariant_to_symbol_column_permutation(
    policy: WeightingPolicyV2,
) -> None:
    frame = _factor([0.0, 0.0, 1.0, 2.0, 3.0, 3.0])
    permuted = frame.loc[:, ["F", "B", "D", "A", "E", "C"]]

    first = construct_target_weights_v2(frame, _mask(frame), policy)
    second = construct_target_weights_v2(permuted, _mask(permuted), policy)

    pd.testing.assert_frame_equal(
        first.weights.sort_index(axis=1),
        second.weights.sort_index(axis=1),
    )


def test_tied_quantile_boundary_is_neutral_not_ticker_selected() -> None:
    frame = _factor([0.0, 1.0, 2.0, 3.0, 3.0])
    policy = WeightingPolicyV2(
        method="quantile_long_short.v2",
        long_quantile=0.2,
        short_quantile=0.2,
        gross_leverage=1.0,
        minimum_names_per_side=1,
    )

    result = construct_target_weights_v2(frame, _mask(frame), policy)

    assert result.weights.eq(0.0).all().all()
    assert set(result.reason_by_date.values()) == {"BOUNDARY_TIE_NO_TRADE"}


def test_long_short_sets_never_overlap_and_exposure_matches_policy() -> None:
    frame = _factor([0.0, 0.0, 1.0, 2.0, 3.0, 3.0])
    result = construct_target_weights_v2(frame, _mask(frame), _quantile_policy())
    row = result.weights.iloc[0]

    assert set(row.index[row > 0]).isdisjoint(set(row.index[row < 0]))
    assert float(row.abs().sum()) == pytest.approx(1.0)
    assert float(row.sum()) == pytest.approx(0.0)


def _cost_policy(**changes: float | bool) -> ExecutionCostPolicyV1:
    values: dict[str, float | bool] = {
        "commission_bps": 2.0,
        "sell_stamp_duty_bps": 5.0,
        "buy_slippage_bps": 1.0,
        "sell_slippage_bps": 2.0,
        "require_terminal_flat": True,
    }
    values.update(changes)
    return ExecutionCostPolicyV1(**values)  # type: ignore[arg-type]


def _execution_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    index = pd.date_range("2020-01-01", periods=3, freq="D")
    columns = ["A", "B"]
    holdings = pd.DataFrame(
        [[0.5, -0.5], [0.5, -0.5], [0.0, 0.0]],
        index=index,
        columns=columns,
    )
    trades = holdings.diff()
    trades.iloc[0] = holdings.iloc[0]
    returns = pd.DataFrame(
        [[0.1, -0.1], [0.0, 0.0], [np.nan, np.nan]],
        index=index,
        columns=columns,
    )
    return holdings, trades, returns


def test_initial_build_and_terminal_exit_both_pay_directional_costs() -> None:
    holdings, trades, returns = _execution_frames()
    policy = _cost_policy()

    result = compute_strict_execution_v2(
        actual_holdings=holdings,
        filled_trades=trades,
        forward_returns=returns,
        cost_policy=policy,
    )

    expected_each_side = (0.5 * 3.0 + 0.5 * 9.0) / 10_000.0
    assert result.cost_return.iloc[0] == pytest.approx(expected_each_side)
    assert result.cost_return.iloc[-1] == pytest.approx(expected_each_side)
    assert result.traded_notional.iloc[0] == pytest.approx(1.0)
    assert result.traded_notional.iloc[-1] == pytest.approx(1.0)
    assert result.gross_return.iloc[0] == pytest.approx(0.1)
    assert result.net_return.iloc[0] == pytest.approx(0.1 - expected_each_side)


@pytest.mark.parametrize("partial", [False, True])
def test_missing_required_return_is_unavailable_not_zero_or_partial_sum(
    partial: bool,
) -> None:
    holdings, trades, returns = _execution_frames()
    returns.iloc[0] = [np.nan, 0.25] if partial else [np.nan, np.nan]

    result = compute_strict_execution_v2(
        actual_holdings=holdings,
        filled_trades=trades,
        forward_returns=returns,
        cost_policy=_cost_policy(),
    )

    assert math.isnan(result.gross_return.iloc[0])
    assert math.isnan(result.net_return.iloc[0])
    assert result.missing_return_exposure.iloc[0] == pytest.approx(
        0.5 if partial else 1.0
    )
    assert result.gross_return.iloc[-1] == 0.0


@pytest.mark.parametrize(
    "changes",
    [
        {"commission_bps": -1.0},
        {"sell_stamp_duty_bps": float("nan")},
        {"buy_slippage_bps": float("inf")},
        {"sell_slippage_bps": 1001.0},
    ],
)
def test_unknown_negative_or_nonfinite_cost_cannot_degrade_to_zero(
    changes: dict[str, float],
) -> None:
    with pytest.raises(ValueError, match="finite"):
        _cost_policy(**changes)


def test_execution_requires_exact_axes_trade_reconciliation_and_exit() -> None:
    holdings, trades, returns = _execution_frames()
    with pytest.raises(ValueError, match="axes must match"):
        compute_strict_execution_v2(
            actual_holdings=holdings,
            filled_trades=trades,
            forward_returns=returns.drop(columns=["B"]),
            cost_policy=_cost_policy(),
        )

    wrong_trades = trades.copy()
    wrong_trades.iloc[0, 0] = 0.0
    with pytest.raises(ValueError, match="reconcile"):
        compute_strict_execution_v2(
            actual_holdings=holdings,
            filled_trades=wrong_trades,
            forward_returns=returns,
            cost_policy=_cost_policy(),
        )

    nonflat = holdings.copy()
    nonflat.iloc[-1] = nonflat.iloc[-2]
    nonflat_trades = nonflat.diff()
    nonflat_trades.iloc[0] = nonflat.iloc[0]
    with pytest.raises(ValueError, match="terminal liquidation"):
        compute_strict_execution_v2(
            actual_holdings=nonflat,
            filled_trades=nonflat_trades,
            forward_returns=returns,
            cost_policy=_cost_policy(),
        )
