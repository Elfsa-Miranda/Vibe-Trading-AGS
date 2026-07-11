from __future__ import annotations

import pandas as pd
import pytest

from src.alpha_quality.execution_return import CostModel, compute_execution_return
from src.alpha_quality.forward_returns import compute_forward_return


def test_signal_date_weight_and_lagged_forward_return_align_exactly_once() -> None:
    dates = pd.date_range("2025-01-01", periods=4, freq="D")
    close = pd.DataFrame({"AAA": [100.0, 110.0, 132.0, 99.0]}, index=dates)
    signal_date_returns = compute_forward_return(
        close,
        horizon=1,
        execution_lag=1,
    )
    weights = pd.DataFrame({"AAA": [1.0, 0.0, 0.0, 0.0]}, index=dates)
    turnover = pd.Series(0.0, index=dates)

    net, costs = compute_execution_return(
        weights,
        signal_date_returns,
        turnover,
        CostModel(bps_per_one_way_turnover=0.0),
        timing_policy="signal_date_forward_return.v2",
    )

    # Signal at t0 enters at t1 and exits at t2: 132 / 110 - 1 = 20%.
    assert net.loc[dates[0]] == pytest.approx(0.20)
    # A second weight shift would incorrectly move the t0 signal to the t1
    # return window (t2 -> t3), producing -25% here.
    assert net.loc[dates[1]] == pytest.approx(0.0)
    assert costs.eq(0.0).all()


def test_legacy_default_remains_frozen_for_feature_off_compatibility() -> None:
    dates = pd.date_range("2025-01-01", periods=4, freq="D")
    close = pd.DataFrame({"AAA": [100.0, 110.0, 132.0, 99.0]}, index=dates)
    returns = compute_forward_return(close, horizon=1, execution_lag=1)
    weights = pd.DataFrame({"AAA": [1.0, 0.0, 0.0, 0.0]}, index=dates)
    net, _ = compute_execution_return(
        weights,
        returns,
        pd.Series(0.0, index=dates),
        CostModel(bps_per_one_way_turnover=0.0),
    )
    assert net.loc[dates[0]] == pytest.approx(0.0)
    assert net.loc[dates[1]] == pytest.approx(-0.25)
