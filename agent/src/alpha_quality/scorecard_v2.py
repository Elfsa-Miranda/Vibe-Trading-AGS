"""Train/valid-only scorecard v2 with no final or execution evidence surface."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal, cast

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from src.alpha_quality.evaluation_policy import (
    EvaluationTimePolicyV1,
    FrozenSplitPlanV1,
    FrozenTradingCalendarV1,
    SplitWindowV1,
)
from src.alpha_quality.factor_output_v2 import FrozenFactorOutputV2
from src.alpha_quality.ic_metrics import compute_ic_metrics
from src.alpha_quality.model import ICMetricSummary
from src.research_ledger.hash_utils import canonical_json_hash


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
DiscoverySplit = Literal["train", "valid"]


def _hash(value: str, name: str) -> None:
    if _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical sha256 hash")


def _discovery_split(value: str) -> DiscoverySplit:
    if value not in ("train", "valid"):
        raise ValueError("discovery split must be train or valid")
    return cast(DiscoverySplit, value)


def _frame_content_hash(
    frame: pd.DataFrame,
    *,
    dates: tuple[str, ...],
    symbols: tuple[str, ...],
) -> tuple[str, bytes]:
    if any(not pd.api.types.is_numeric_dtype(dtype) for dtype in frame.dtypes):
        raise ValueError("train/valid close values must be numeric")
    values = frame.to_numpy(dtype=np.dtype("<f8"), copy=True)
    if np.isinf(values).any():
        raise ValueError("train/valid close contains Infinity")
    if (values[np.isfinite(values)] <= 0.0).any():
        raise ValueError("train/valid close must be positive where available")
    values[np.isnan(values)] = np.float64(np.nan)
    payload = values.tobytes(order="C")
    return (
        canonical_json_hash(
            {
                "dtype": "float64-le",
                "shape": list(values.shape),
                "dates": list(dates),
                "symbols": list(symbols),
                "payload_sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
            }
        ),
        payload,
    )


@dataclass(frozen=True, init=False)
class FixtureTrainValidDataCapabilityV1:
    snapshot_hash: str
    calendar_hash: str
    split_plan_hash: str
    train_window: SplitWindowV1
    valid_window: SplitWindowV1
    time_policy: EvaluationTimePolicyV1
    dates: tuple[str, ...]
    symbols: tuple[str, ...]
    close_content_hash: str
    schema_version: str
    authority_status: str
    _train_calendar_dates: tuple[str, ...]
    _valid_calendar_dates: tuple[str, ...]
    _close_bytes: bytes

    @classmethod
    def from_fixture(
        cls,
        *,
        snapshot_hash: str,
        calendar: FrozenTradingCalendarV1,
        split_plan: FrozenSplitPlanV1,
        time_policy: EvaluationTimePolicyV1,
        close: pd.DataFrame,
        symbols: tuple[str, ...],
    ) -> "FixtureTrainValidDataCapabilityV1":
        _hash(snapshot_hash, "snapshot_hash")
        if split_plan.calendar_hash != calendar.calendar_hash:
            raise ValueError("split plan and calendar do not match")
        if split_plan.evaluation_time_policy_hash != time_policy.policy_hash:
            raise ValueError("split plan and time policy do not match")
        rebuilt_plan = FrozenSplitPlanV1.build(
            calendar=calendar,
            time_policy=time_policy,
            train=(split_plan.train.start, split_plan.train.end),
            valid=(split_plan.valid.start, split_plan.valid.end),
            test=(split_plan.test.start, split_plan.test.end),
        )
        if rebuilt_plan.plan_hash != split_plan.plan_hash:
            raise ValueError("split plan fields do not match calendar-derived policy")
        if symbols != tuple(sorted(set(symbols))) or not symbols:
            raise ValueError("train/valid symbols must be sorted, unique, and non-empty")
        train_dates = calendar.dates[
            calendar.dates.index(split_plan.train.start) :
            calendar.dates.index(split_plan.train.end) + 1
        ]
        valid_dates = calendar.dates[
            calendar.dates.index(split_plan.valid.start) :
            calendar.dates.index(split_plan.valid.end) + 1
        ]
        expected_dates = train_dates + valid_dates
        if not isinstance(close.index, pd.DatetimeIndex) or close.index.tz is not None:
            raise ValueError("train/valid close index must be timezone-naive dates")
        actual_dates = tuple(value.date().isoformat() for value in close.index)
        expected_index = pd.DatetimeIndex(expected_dates)
        if (
            not close.index.equals(expected_index)
            or actual_dates != expected_dates
            or tuple(close.columns) != symbols
        ):
            raise ValueError(
                "train/valid capability rejects test, gap, missing, or reordered axes"
            )
        content_hash, payload = _frame_content_hash(
            close,
            dates=expected_dates,
            symbols=symbols,
        )
        instance = object.__new__(cls)
        for name, value in {
            "snapshot_hash": snapshot_hash,
            "calendar_hash": calendar.calendar_hash,
            "split_plan_hash": split_plan.plan_hash,
            "train_window": split_plan.train,
            "valid_window": split_plan.valid,
            "time_policy": time_policy,
            "dates": expected_dates,
            "symbols": symbols,
            "close_content_hash": content_hash,
            "schema_version": "fixture_train_valid_data_capability.v1",
            "authority_status": "fixture_only_pit_authority_unavailable",
            "_train_calendar_dates": train_dates,
            "_valid_calendar_dates": valid_dates,
            "_close_bytes": payload,
        }.items():
            object.__setattr__(instance, name, value)
        return instance

    @property
    def decision_grade(self) -> bool:
        return False

    @property
    def capability_hash(self) -> str:
        return canonical_json_hash(
            {
                "schema_version": self.schema_version,
                "snapshot_hash": self.snapshot_hash,
                "calendar_hash": self.calendar_hash,
                "split_plan_hash": self.split_plan_hash,
                "time_policy_hash": self.time_policy.policy_hash,
                "dates": list(self.dates),
                "symbols": list(self.symbols),
                "close_content_hash": self.close_content_hash,
                "authority_status": self.authority_status,
                "decision_grade": False,
            }
        )

    def close_frame(self) -> pd.DataFrame:
        values = np.frombuffer(self._close_bytes, dtype=np.dtype("<f8")).copy()
        return pd.DataFrame(
            values.reshape((len(self.dates), len(self.symbols))),
            index=pd.DatetimeIndex(self.dates),
            columns=self.symbols,
        )

    def signal_dates(self, split: DiscoverySplit) -> tuple[str, ...]:
        split = _discovery_split(split)
        window = self.train_window if split == "train" else self.valid_window
        calendar_dates = (
            self._train_calendar_dates
            if split == "train"
            else self._valid_calendar_dates
        )
        start = calendar_dates.index(window.eligible_signal_start)
        end = calendar_dates.index(window.eligible_signal_end)
        return calendar_dates[start : end + 1]

    def outcome_dates(
        self,
        split: DiscoverySplit,
        signal_date: str,
        *,
        horizon: int,
    ) -> tuple[str, str, str]:
        if horizon not in self.time_policy.return_horizons:
            raise ValueError("horizon is not registered in the timing policy")
        eligible = self.signal_dates(split)
        if signal_date not in eligible:
            raise ValueError("signal date is not eligible for the discovery split")
        calendar_dates = (
            self._train_calendar_dates
            if split == "train"
            else self._valid_calendar_dates
        )
        position = calendar_dates.index(signal_date)
        entry = position + self.time_policy.entry_lag_trading_days
        exit_ = entry + horizon
        return signal_date, calendar_dates[entry], calendar_dates[exit_]

    def verify_content(self) -> None:
        expected_size = len(self.dates) * len(self.symbols) * 8
        if len(self._close_bytes) != expected_size:
            raise ValueError("train/valid close byte length is invalid")
        frame = self.close_frame()
        rebuilt, _ = _frame_content_hash(
            frame,
            dates=self.dates,
            symbols=self.symbols,
        )
        if rebuilt != self.close_content_hash:
            raise ValueError("train/valid close content hash does not match bytes")


@dataclass(frozen=True)
class PredictiveSplitMetricV2:
    split: DiscoverySplit
    horizon: int
    signal_dates: tuple[str, ...]
    summary: ICMetricSummary

    def to_dict(self) -> dict[str, Any]:
        return {
            "split": self.split,
            "horizon": self.horizon,
            "signal_dates": list(self.signal_dates),
            "summary": asdict(self.summary),
        }


@dataclass(frozen=True)
class CoverageSplitMetricV2:
    split: DiscoverySplit
    by_date: tuple[tuple[str, float | None], ...]
    mean_coverage: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "split": self.split,
            "by_date": {key: value for key, value in self.by_date},
            "mean_coverage": self.mean_coverage,
        }


@dataclass(frozen=True)
class AlphaQualityScorecardV2:
    factor_spec_id: str
    snapshot_hash: str
    split_plan_hash: str
    evaluation_time_policy_hash: str
    factor_output_content_hash: str
    train_valid_capability_hash: str
    predictive: tuple[PredictiveSplitMetricV2, ...]
    coverage: tuple[CoverageSplitMetricV2, ...]
    execution_evidence_status: Literal["separate_producer_required"]
    authority_status: Literal["fixture_only_not_decision_evidence"]
    schema_version: Literal["alpha_quality_scorecard.v2"] = "alpha_quality_scorecard.v2"

    @property
    def decision_grade(self) -> bool:
        return False

    @property
    def scorecard_hash(self) -> str:
        return canonical_json_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "factor_spec_id": self.factor_spec_id,
            "snapshot_hash": self.snapshot_hash,
            "split_plan_hash": self.split_plan_hash,
            "evaluation_time_policy_hash": self.evaluation_time_policy_hash,
            "factor_output_content_hash": self.factor_output_content_hash,
            "train_valid_capability_hash": self.train_valid_capability_hash,
            "predictive": [item.to_dict() for item in self.predictive],
            "coverage": [item.to_dict() for item in self.coverage],
            "execution_evidence_status": self.execution_evidence_status,
            "authority_status": self.authority_status,
            "decision_grade": False,
        }


def _forward_returns_for_split(
    capability: FixtureTrainValidDataCapabilityV1,
    *,
    split: DiscoverySplit,
    horizon: int,
) -> pd.DataFrame:
    close = capability.close_frame()
    signal_dates = capability.signal_dates(split)
    result = pd.DataFrame(
        np.nan,
        index=pd.DatetimeIndex(signal_dates),
        columns=capability.symbols,
    )
    for signal in signal_dates:
        _, entry, exit_ = capability.outcome_dates(
            split,
            signal,
            horizon=horizon,
        )
        entry_values = close.loc[pd.Timestamp(entry)]
        exit_values = close.loc[pd.Timestamp(exit_)]
        values = exit_values / entry_values - 1.0
        result.loc[pd.Timestamp(signal)] = values.replace([np.inf, -np.inf], np.nan)
    return result.astype(float)


def _coverage(
    *,
    split: DiscoverySplit,
    dates: tuple[str, ...],
    valid: pd.DataFrame,
    universe: pd.DataFrame,
) -> CoverageSplitMetricV2:
    values: list[tuple[str, float | None]] = []
    finite_values: list[float] = []
    for value in dates:
        timestamp = pd.Timestamp(value)
        denominator = int(universe.loc[timestamp].sum())
        if denominator == 0:
            values.append((value, None))
            continue
        numerator = int((valid.loc[timestamp] & universe.loc[timestamp]).sum())
        coverage = float(numerator / denominator)
        values.append((value, coverage))
        finite_values.append(coverage)
    return CoverageSplitMetricV2(
        split=split,
        by_date=tuple(values),
        mean_coverage=(
            None if not finite_values else float(sum(finite_values) / len(finite_values))
        ),
    )


def compute_fixture_scorecard_v2(
    *,
    factor_output: FrozenFactorOutputV2,
    capability: FixtureTrainValidDataCapabilityV1,
    minimum_cross_section: int = 5,
) -> AlphaQualityScorecardV2:
    factor_output.verify_content()
    capability.verify_content()
    if factor_output.decision_grade or capability.decision_grade:
        raise ValueError("fixture scorecard accepts fixture-only inputs")
    if factor_output.snapshot_hash != capability.snapshot_hash:
        raise ValueError("factor output and train/valid snapshot differ")
    if factor_output.split_plan_hash != capability.split_plan_hash:
        raise ValueError("factor output and split plan differ")
    if factor_output.evaluation_time_policy_hash != capability.time_policy.policy_hash:
        raise ValueError("factor output and timing policy differ")
    if factor_output.dates != capability.dates or factor_output.symbols != capability.symbols:
        raise ValueError("factor output and train/valid capability axes differ")
    if minimum_cross_section < 2:
        raise ValueError("minimum cross section must be at least two")

    factor = factor_output.factor_frame()
    valid = (
        factor_output.valid_mask_frame()
        & factor_output.tradable_mask_frame()
        & factor_output.universe_mask_frame()
    )
    universe = factor_output.universe_mask_frame()
    predictive: list[PredictiveSplitMetricV2] = []
    coverage: list[CoverageSplitMetricV2] = []
    for split in ("train", "valid"):
        signal_dates = capability.signal_dates(split)
        timestamps = pd.DatetimeIndex(signal_dates)
        coverage.append(
            _coverage(
                split=split,
                dates=signal_dates,
                valid=valid,
                universe=universe,
            )
        )
        for horizon in capability.time_policy.return_horizons:
            returns = _forward_returns_for_split(
                capability,
                split=split,
                horizon=horizon,
            )
            factor_split = factor.loc[timestamps]
            mask_split = valid.loc[timestamps]
            if not (
                factor_split.index.equals(returns.index)
                and factor_split.columns.equals(returns.columns)
                and mask_split.index.equals(returns.index)
                and mask_split.columns.equals(returns.columns)
            ):
                raise ValueError("scorecard metric axes are not exact")
            predictive.append(
                PredictiveSplitMetricV2(
                    split=split,
                    horizon=horizon,
                    signal_dates=signal_dates,
                    summary=compute_ic_metrics(
                        factor_split,
                        returns,
                        horizon=horizon,
                        valid_mask=mask_split,
                        min_cross_section=minimum_cross_section,
                    ),
                )
            )
    return AlphaQualityScorecardV2(
        factor_spec_id=factor_output.factor_spec_id,
        snapshot_hash=capability.snapshot_hash,
        split_plan_hash=capability.split_plan_hash,
        evaluation_time_policy_hash=capability.time_policy.policy_hash,
        factor_output_content_hash=factor_output.content_hash,
        train_valid_capability_hash=capability.capability_hash,
        predictive=tuple(predictive),
        coverage=tuple(coverage),
        execution_evidence_status="separate_producer_required",
        authority_status="fixture_only_not_decision_evidence",
    )


__all__ = [
    "AlphaQualityScorecardV2",
    "CoverageSplitMetricV2",
    "FixtureTrainValidDataCapabilityV1",
    "PredictiveSplitMetricV2",
    "compute_fixture_scorecard_v2",
]
