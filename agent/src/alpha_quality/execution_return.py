from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd


@dataclass(frozen=True)
class CostModel:
    bps_per_one_way_turnover: float = 10.0

    def estimate_bps(self, *, turnover: pd.Series, weights: pd.DataFrame) -> pd.Series:  # noqa: ARG002
        return turnover.fillna(0.0) * self.bps_per_one_way_turnover


def compute_execution_return(
    weights: pd.DataFrame,
    forward_returns: pd.DataFrame,
    turnover: pd.Series,
    cost_model: CostModel,
    *,
    timing_policy: Literal[
        "legacy_weight_shift.v1",
        "signal_date_forward_return.v2",
    ] = "legacy_weight_shift.v1",
) -> tuple[pd.Series, pd.Series]:
    """Apply a frozen timing policy to signal-date-indexed forward returns.

    ``compute_forward_return`` encodes the execution lag in each row's entry
    and exit prices. ``signal_date_forward_return.v2`` therefore does not shift
    weights again. The v1 policy is retained only for feature-off compatibility
    with historical scorecards and must not be used by new Activation evidence.
    """
    aligned_returns = forward_returns.reindex(index=weights.index, columns=weights.columns)
    if timing_policy == "legacy_weight_shift.v1":
        applied_weights = weights.shift(1)
    elif timing_policy == "signal_date_forward_return.v2":
        applied_weights = weights
    else:  # pragma: no cover - closed typing plus runtime defense
        raise ValueError("unknown execution timing policy")
    gross = (applied_weights * aligned_returns).sum(axis=1)
    cost_bps = cost_model.estimate_bps(turnover=turnover, weights=weights)
    return gross - cost_bps / 10000.0, cost_bps
