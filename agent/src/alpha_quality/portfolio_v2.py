"""Deterministic weighting and strict execution-economics primitives."""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from src.research_ledger.hash_utils import canonical_json_hash


@dataclass(frozen=True)
class WeightingPolicyV2:
    method: Literal["quantile_long_short.v2", "continuous_rank_long_short.v2"]
    long_quantile: float = 0.2
    short_quantile: float = 0.2
    gross_leverage: float = 1.0
    minimum_names_per_side: int = 1
    tie_policy: Literal["whole_value_groups_no_boundary_split.v1"] = (
        "whole_value_groups_no_boundary_split.v1"
    )
    schema_version: Literal["weighting_policy.v2"] = "weighting_policy.v2"

    def __post_init__(self) -> None:
        if self.schema_version != "weighting_policy.v2":
            raise ValueError("unsupported weighting policy schema")
        if self.method not in {
            "quantile_long_short.v2",
            "continuous_rank_long_short.v2",
        }:
            raise ValueError("unsupported weighting method")
        for name in ("long_quantile", "short_quantile"):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0.0 < value < 0.5:
                raise ValueError(f"{name} must be finite and in (0, 0.5)")
        if self.long_quantile + self.short_quantile >= 1.0:
            raise ValueError("long and short quantiles must leave a neutral region")
        if not math.isfinite(self.gross_leverage) or not 0.0 < self.gross_leverage <= 4.0:
            raise ValueError("gross leverage must be finite and in (0, 4]")
        if (
            isinstance(self.minimum_names_per_side, bool)
            or not isinstance(self.minimum_names_per_side, int)
            or self.minimum_names_per_side < 1
        ):
            raise ValueError("minimum names per side must be a positive integer")

    @property
    def policy_hash(self) -> str:
        return canonical_json_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "method": self.method,
            "long_quantile": self.long_quantile,
            "short_quantile": self.short_quantile,
            "gross_leverage": self.gross_leverage,
            "minimum_names_per_side": self.minimum_names_per_side,
            "tie_policy": self.tie_policy,
        }


@dataclass(frozen=True)
class WeightConstructionResultV2:
    weights: pd.DataFrame
    reason_by_date: Mapping[str, str]
    gross_exposure_by_date: Mapping[str, float]
    net_exposure_by_date: Mapping[str, float]
    policy_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "weights", self.weights.copy(deep=True))
        object.__setattr__(
            self,
            "reason_by_date",
            MappingProxyType(dict(self.reason_by_date)),
        )
        object.__setattr__(
            self,
            "gross_exposure_by_date",
            MappingProxyType(dict(self.gross_exposure_by_date)),
        )
        object.__setattr__(
            self,
            "net_exposure_by_date",
            MappingProxyType(dict(self.net_exposure_by_date)),
        )


def _strict_factor_and_mask(
    factor: pd.DataFrame,
    valid_mask: pd.DataFrame,
) -> None:
    if not isinstance(factor, pd.DataFrame) or factor.empty:
        raise ValueError("factor must be a non-empty DataFrame")
    if not factor.index.equals(valid_mask.index) or not factor.columns.equals(
        valid_mask.columns
    ):
        raise ValueError("factor and validity mask axes must match exactly")
    if factor.index.has_duplicates or factor.columns.has_duplicates:
        raise ValueError("factor axes must be unique")
    if any(not pd.api.types.is_numeric_dtype(dtype) for dtype in factor.dtypes):
        raise ValueError("factor values must be numeric")
    values = factor.to_numpy(dtype=float, copy=False)
    if np.isinf(values).any():
        raise ValueError("factor contains Infinity")
    if any(dtype != np.dtype(bool) for dtype in valid_mask.dtypes):
        raise ValueError("validity mask must use strict bool dtype with no NA")


def _whole_groups(
    row: pd.Series,
    *,
    target: int,
    ascending: bool,
) -> list[str]:
    selected: list[str] = []
    values = sorted(row.unique(), reverse=not ascending)
    for value in values:
        group = sorted(str(symbol) for symbol in row.index[row == value])
        if len(selected) + len(group) > target:
            break
        selected.extend(group)
        if len(selected) == target:
            break
    return selected


def construct_target_weights_v2(
    factor: pd.DataFrame,
    valid_mask: pd.DataFrame,
    policy: WeightingPolicyV2,
) -> WeightConstructionResultV2:
    _strict_factor_and_mask(factor, valid_mask)
    weights = pd.DataFrame(0.0, index=factor.index, columns=factor.columns)
    reasons: dict[str, str] = {}
    for timestamp in factor.index:
        key = pd.Timestamp(timestamp).isoformat()
        valid = valid_mask.loc[timestamp] & factor.loc[timestamp].notna()
        row = factor.loc[timestamp, valid].astype(float)
        if len(row) < 2 * policy.minimum_names_per_side:
            reasons[key] = "INSUFFICIENT_CROSS_SECTION"
            continue
        if row.nunique(dropna=True) < 2:
            reasons[key] = "NO_TRADE_CONSTANT_FACTOR"
            continue

        if policy.method == "quantile_long_short.v2":
            long_target = int(math.floor(len(row) * policy.long_quantile))
            short_target = int(math.floor(len(row) * policy.short_quantile))
            if min(long_target, short_target) < policy.minimum_names_per_side:
                reasons[key] = "INSUFFICIENT_CROSS_SECTION"
                continue
            longs = _whole_groups(row, target=long_target, ascending=False)
            shorts = _whole_groups(row, target=short_target, ascending=True)
            if (
                len(longs) < policy.minimum_names_per_side
                or len(shorts) < policy.minimum_names_per_side
            ):
                reasons[key] = "BOUNDARY_TIE_NO_TRADE"
                continue
            if set(longs) & set(shorts):
                reasons[key] = "NO_TRADE_OVERLAPPING_SIDES"
                continue
            weights.loc[timestamp, longs] = policy.gross_leverage / 2.0 / len(longs)
            weights.loc[timestamp, shorts] = -policy.gross_leverage / 2.0 / len(shorts)
        else:
            ranks = row.rank(method="average")
            centered = ranks - ranks.mean()
            positives = centered[centered > 0]
            negatives = centered[centered < 0]
            if (
                len(positives) < policy.minimum_names_per_side
                or len(negatives) < policy.minimum_names_per_side
            ):
                reasons[key] = "INSUFFICIENT_CROSS_SECTION"
                continue
            scale = float(centered.abs().sum())
            if not math.isfinite(scale) or scale <= 0.0:
                reasons[key] = "NO_TRADE_CONSTANT_FACTOR"
                continue
            weights.loc[timestamp, row.index] = (
                centered / scale * policy.gross_leverage
            )
        reasons[key] = "WEIGHTS_CONSTRUCTED"

    gross = weights.abs().sum(axis=1)
    net = weights.sum(axis=1)
    return WeightConstructionResultV2(
        weights=weights,
        reason_by_date=reasons,
        gross_exposure_by_date={
            pd.Timestamp(index).isoformat(): float(value) for index, value in gross.items()
        },
        net_exposure_by_date={
            pd.Timestamp(index).isoformat(): float(value) for index, value in net.items()
        },
        policy_hash=policy.policy_hash,
    )


@dataclass(frozen=True)
class ExecutionCostPolicyV1:
    commission_bps: float
    sell_stamp_duty_bps: float
    buy_slippage_bps: float
    sell_slippage_bps: float
    require_terminal_flat: bool = True
    schema_version: Literal["execution_cost_policy.v1"] = "execution_cost_policy.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "execution_cost_policy.v1":
            raise ValueError("unsupported execution cost policy schema")
        for name in (
            "commission_bps",
            "sell_stamp_duty_bps",
            "buy_slippage_bps",
            "sell_slippage_bps",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0.0 <= value <= 1_000.0:
                raise ValueError(f"{name} must be finite and in [0, 1000]")
        if not isinstance(self.require_terminal_flat, bool):
            raise ValueError("require_terminal_flat must be boolean")

    @property
    def policy_hash(self) -> str:
        return canonical_json_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "commission_bps": self.commission_bps,
            "sell_stamp_duty_bps": self.sell_stamp_duty_bps,
            "buy_slippage_bps": self.buy_slippage_bps,
            "sell_slippage_bps": self.sell_slippage_bps,
            "require_terminal_flat": self.require_terminal_flat,
        }


@dataclass(frozen=True)
class StrictExecutionResultV2:
    gross_return: pd.Series
    net_return: pd.Series
    cost_return: pd.Series
    traded_notional: pd.Series
    missing_return_exposure: pd.Series
    cost_policy_hash: str
    schema_version: Literal["strict_execution_result.v2"] = (
        "strict_execution_result.v2"
    )


def _strict_numeric_frame(frame: pd.DataFrame, name: str) -> None:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError(f"{name} must be a non-empty DataFrame")
    if frame.index.has_duplicates or frame.columns.has_duplicates:
        raise ValueError(f"{name} axes must be unique")
    if any(not pd.api.types.is_numeric_dtype(dtype) for dtype in frame.dtypes):
        raise ValueError(f"{name} must be numeric")
    values = frame.to_numpy(dtype=float, copy=False)
    if np.isinf(values).any():
        raise ValueError(f"{name} contains Infinity")


def compute_strict_execution_v2(
    *,
    actual_holdings: pd.DataFrame,
    filled_trades: pd.DataFrame,
    forward_returns: pd.DataFrame,
    cost_policy: ExecutionCostPolicyV1,
) -> StrictExecutionResultV2:
    for frame, name in (
        (actual_holdings, "actual_holdings"),
        (filled_trades, "filled_trades"),
        (forward_returns, "forward_returns"),
    ):
        _strict_numeric_frame(frame, name)
    if not (
        actual_holdings.index.equals(filled_trades.index)
        and actual_holdings.columns.equals(filled_trades.columns)
        and actual_holdings.index.equals(forward_returns.index)
        and actual_holdings.columns.equals(forward_returns.columns)
    ):
        raise ValueError("holdings, trades, and returns axes must match exactly")
    if actual_holdings.isna().any().any() or filled_trades.isna().any().any():
        raise ValueError("holdings and filled trades cannot contain missing values")
    expected_trades = actual_holdings.diff()
    expected_trades.iloc[0] = actual_holdings.iloc[0]
    if not np.allclose(
        filled_trades.to_numpy(dtype=float),
        expected_trades.to_numpy(dtype=float),
        rtol=0.0,
        atol=1e-12,
    ):
        raise ValueError("filled trades do not reconcile from zero initial holdings")
    if cost_policy.require_terminal_flat and not np.allclose(
        actual_holdings.iloc[-1].to_numpy(dtype=float),
        0.0,
        rtol=0.0,
        atol=1e-12,
    ):
        raise ValueError("execution evidence must include terminal liquidation")

    required = actual_holdings.ne(0.0)
    missing_required = required & forward_returns.isna()
    missing_exposure = actual_holdings.where(missing_required, 0.0).abs().sum(axis=1)
    gross = (actual_holdings * forward_returns).sum(axis=1, min_count=1)
    no_position = ~required.any(axis=1)
    gross.loc[no_position] = 0.0
    gross.loc[missing_required.any(axis=1)] = np.nan

    buys = filled_trades.clip(lower=0.0).sum(axis=1)
    sells = (-filled_trades.clip(upper=0.0)).sum(axis=1)
    buy_rate = cost_policy.commission_bps + cost_policy.buy_slippage_bps
    sell_rate = (
        cost_policy.commission_bps
        + cost_policy.sell_stamp_duty_bps
        + cost_policy.sell_slippage_bps
    )
    cost_return = (buys * buy_rate + sells * sell_rate) / 10_000.0
    traded_notional = filled_trades.abs().sum(axis=1)
    net = gross - cost_return
    net.loc[gross.isna()] = np.nan
    return StrictExecutionResultV2(
        gross_return=gross.astype(float),
        net_return=net.astype(float),
        cost_return=cost_return.astype(float),
        traded_notional=traded_notional.astype(float),
        missing_return_exposure=missing_exposure.astype(float),
        cost_policy_hash=cost_policy.policy_hash,
    )


__all__ = [
    "ExecutionCostPolicyV1",
    "StrictExecutionResultV2",
    "WeightConstructionResultV2",
    "WeightingPolicyV2",
    "compute_strict_execution_v2",
    "construct_target_weights_v2",
]
