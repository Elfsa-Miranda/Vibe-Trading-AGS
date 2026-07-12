"""Frozen calendar, timing, and split primitives for authoritative evaluation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Literal

from src.research_ledger.hash_utils import canonical_json_hash


ResearchSplit = Literal["train", "valid", "test"]
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _hash(value: str, name: str) -> None:
    if _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical sha256 hash")


def _date(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO calendar date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO calendar date") from exc
    normalized = parsed.isoformat()
    if normalized != value:
        raise ValueError(f"{name} must use canonical ISO date syntax")
    return normalized


def _bounds(value: tuple[str, str], name: str) -> tuple[str, str]:
    if not isinstance(value, tuple) or len(value) != 2:
        raise ValueError(f"{name} split requires exactly two bounds")
    return _date(value[0], f"{name} bound"), _date(value[1], f"{name} bound")


@dataclass(frozen=True)
class FrozenTradingCalendarV1:
    dates: tuple[str, ...]
    source_artifact_hash: str
    exchange: Literal["XSHG_XSHE"] = "XSHG_XSHE"
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    schema_version: Literal["frozen_trading_calendar.v1"] = (
        "frozen_trading_calendar.v1"
    )

    def __post_init__(self) -> None:
        if self.schema_version != "frozen_trading_calendar.v1":
            raise ValueError("unsupported trading calendar schema")
        _hash(self.source_artifact_hash, "source_artifact_hash")
        normalized = tuple(_date(value, "calendar date") for value in self.dates)
        if len(normalized) < 3:
            raise ValueError("trading calendar requires at least three dates")
        if normalized != tuple(sorted(set(normalized))):
            raise ValueError("trading calendar dates must be strictly increasing and unique")
        object.__setattr__(self, "dates", normalized)

    @property
    def calendar_hash(self) -> str:
        return canonical_json_hash(self._content_dict())

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "exchange": self.exchange,
            "timezone": self.timezone,
            "dates": list(self.dates),
            "source_artifact_hash": self.source_artifact_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "calendar_hash": self.calendar_hash}


@dataclass(frozen=True)
class EvaluationTimePolicyV1:
    return_horizons: tuple[int, ...]
    execution_horizon: int
    holding_period: int
    rebalance_cadence: int
    order_lag_trading_days: int = 1
    entry_lag_trading_days: int = 1
    signal_timestamp: Literal["close_t"] = "close_t"
    entry_price: Literal["close"] = "close"
    exit_price: Literal["close"] = "close"
    execution_return_policy: Literal["signal_date_forward_return.v2"] = (
        "signal_date_forward_return.v2"
    )
    schema_version: Literal["evaluation_time_policy.v1"] = (
        "evaluation_time_policy.v1"
    )

    def __post_init__(self) -> None:
        if self.schema_version != "evaluation_time_policy.v1":
            raise ValueError("unsupported evaluation timing policy schema")
        horizons = tuple(sorted(set(self.return_horizons)))
        if not horizons or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in horizons
        ):
            raise ValueError("return horizons must be non-empty positive integers")
        object.__setattr__(self, "return_horizons", horizons)
        if self.execution_horizon not in horizons:
            raise ValueError("execution horizon must be an explicit return horizon")
        for name in (
            "holding_period",
            "rebalance_cadence",
            "order_lag_trading_days",
            "entry_lag_trading_days",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.order_lag_trading_days > self.entry_lag_trading_days:
            raise ValueError("order lag cannot occur after entry")
        if self.holding_period != self.execution_horizon:
            raise ValueError("holding period must match the frozen execution horizon")
        if self.rebalance_cadence > self.holding_period:
            raise ValueError("rebalance cadence cannot exceed the holding period")
        if self.execution_return_policy != "signal_date_forward_return.v2":
            raise ValueError("production timing must use signal-date forward returns")

    @property
    def maximum_outcome_offset(self) -> int:
        return self.entry_lag_trading_days + max(self.return_horizons)

    @property
    def policy_hash(self) -> str:
        return canonical_json_hash(self._content_dict())

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "return_horizons": list(self.return_horizons),
            "execution_horizon": self.execution_horizon,
            "holding_period": self.holding_period,
            "rebalance_cadence": self.rebalance_cadence,
            "order_lag_trading_days": self.order_lag_trading_days,
            "entry_lag_trading_days": self.entry_lag_trading_days,
            "signal_timestamp": self.signal_timestamp,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "execution_return_policy": self.execution_return_policy,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "policy_hash": self.policy_hash}


@dataclass(frozen=True)
class SplitWindowV1:
    start: str
    end: str
    eligible_signal_start: str
    eligible_signal_end: str
    calendar_day_count: int
    eligible_signal_count: int

    def __post_init__(self) -> None:
        for name in ("start", "end", "eligible_signal_start", "eligible_signal_end"):
            _date(getattr(self, name), name)
        if not (
            self.start
            <= self.eligible_signal_start
            <= self.eligible_signal_end
            <= self.end
        ):
            raise ValueError("split window eligible signal bounds are invalid")
        if self.calendar_day_count < 1 or self.eligible_signal_count < 1:
            raise ValueError("split window counts must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "eligible_signal_start": self.eligible_signal_start,
            "eligible_signal_end": self.eligible_signal_end,
            "calendar_day_count": self.calendar_day_count,
            "eligible_signal_count": self.eligible_signal_count,
        }


@dataclass(frozen=True)
class FrozenSplitPlanV1:
    calendar_hash: str
    evaluation_time_policy_hash: str
    train: SplitWindowV1
    valid: SplitWindowV1
    test: SplitWindowV1
    purge_trading_days: int
    embargo_trading_days: int
    schema_version: Literal["frozen_split_plan.v1"] = "frozen_split_plan.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "frozen_split_plan.v1":
            raise ValueError("unsupported split plan schema")
        _hash(self.calendar_hash, "calendar_hash")
        _hash(self.evaluation_time_policy_hash, "evaluation_time_policy_hash")
        if self.purge_trading_days < 1 or self.embargo_trading_days < 1:
            raise ValueError("purge and embargo must be positive trading-day counts")
        if not (self.train.end < self.valid.start and self.valid.end < self.test.start):
            raise ValueError("split windows must be strictly ordered and non-overlapping")

    @classmethod
    def build(
        cls,
        *,
        calendar: FrozenTradingCalendarV1,
        time_policy: EvaluationTimePolicyV1,
        train: tuple[str, str],
        valid: tuple[str, str],
        test: tuple[str, str],
    ) -> "FrozenSplitPlanV1":
        positions = {value: index for index, value in enumerate(calendar.dates)}
        requested: dict[str, tuple[str, str]] = {
            "train": _bounds(train, "train"),
            "valid": _bounds(valid, "valid"),
            "test": _bounds(test, "test"),
        }
        for name, bounds in requested.items():
            if bounds[0] > bounds[1]:
                raise ValueError(f"{name} split window is reversed or empty")
            if bounds[0] not in positions or bounds[1] not in positions:
                raise ValueError(f"{name} split endpoints must exist in the frozen calendar")
        if not (
            requested["train"][1] < requested["valid"][0]
            and requested["valid"][1] < requested["test"][0]
        ):
            raise ValueError("split windows must be strictly ordered and non-overlapping")

        outcome_span = time_policy.maximum_outcome_offset
        train_gap = positions[requested["valid"][0]] - positions[requested["train"][1]] - 1
        valid_gap = positions[requested["test"][0]] - positions[requested["valid"][1]] - 1
        if train_gap < outcome_span or valid_gap < outcome_span:
            raise ValueError("split gaps do not satisfy the derived trading-day embargo")

        def window(bounds: tuple[str, str], name: str) -> SplitWindowV1:
            start_position = positions[bounds[0]]
            end_position = positions[bounds[1]]
            eligible_end = end_position - outcome_span
            if eligible_end < start_position:
                raise ValueError(f"{name} split has no outcome-contained signal date")
            return SplitWindowV1(
                start=bounds[0],
                end=bounds[1],
                eligible_signal_start=bounds[0],
                eligible_signal_end=calendar.dates[eligible_end],
                calendar_day_count=end_position - start_position + 1,
                eligible_signal_count=eligible_end - start_position + 1,
            )

        return cls(
            calendar_hash=calendar.calendar_hash,
            evaluation_time_policy_hash=time_policy.policy_hash,
            train=window(requested["train"], "train"),
            valid=window(requested["valid"], "valid"),
            test=window(requested["test"], "test"),
            purge_trading_days=outcome_span,
            embargo_trading_days=outcome_span,
        )

    @property
    def plan_hash(self) -> str:
        return canonical_json_hash(self._content_dict())

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "calendar_hash": self.calendar_hash,
            "evaluation_time_policy_hash": self.evaluation_time_policy_hash,
            "train": self.train.to_dict(),
            "valid": self.valid.to_dict(),
            "test": self.test.to_dict(),
            "purge_trading_days": self.purge_trading_days,
            "embargo_trading_days": self.embargo_trading_days,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "plan_hash": self.plan_hash}

    def signal_dates(
        self,
        split: ResearchSplit,
        *,
        calendar: FrozenTradingCalendarV1,
    ) -> tuple[str, ...]:
        self._require_calendar(calendar)
        window = getattr(self, split)
        start = calendar.dates.index(window.eligible_signal_start)
        end = calendar.dates.index(window.eligible_signal_end)
        return calendar.dates[start : end + 1]

    def outcome_dates(
        self,
        split: ResearchSplit,
        signal_date: str,
        *,
        horizon: int,
        calendar: FrozenTradingCalendarV1,
        time_policy: EvaluationTimePolicyV1,
    ) -> tuple[str, str, str]:
        self._require_calendar(calendar)
        self._require_policy(time_policy)
        if horizon not in time_policy.return_horizons:
            raise ValueError("horizon is not registered in the evaluation policy")
        signal = _date(signal_date, "signal_date")
        if signal not in self.signal_dates(split, calendar=calendar):
            raise ValueError("signal date is not eligible for the requested split")
        position = calendar.dates.index(signal)
        entry = position + time_policy.entry_lag_trading_days
        exit_ = entry + horizon
        window = getattr(self, split)
        if calendar.dates[entry] > window.end or calendar.dates[exit_] > window.end:
            raise ValueError("entry or exit escapes the authorized split")
        return signal, calendar.dates[entry], calendar.dates[exit_]

    def _require_calendar(self, calendar: FrozenTradingCalendarV1) -> None:
        if calendar.calendar_hash != self.calendar_hash:
            raise ValueError("split plan calendar hash mismatch")

    def _require_policy(self, time_policy: EvaluationTimePolicyV1) -> None:
        if time_policy.policy_hash != self.evaluation_time_policy_hash:
            raise ValueError("split plan evaluation policy hash mismatch")


def canonical_calendar(
    dates: Iterable[str],
    *,
    source_artifact_hash: str,
) -> FrozenTradingCalendarV1:
    return FrozenTradingCalendarV1(
        dates=tuple(dates),
        source_artifact_hash=source_artifact_hash,
    )


__all__ = [
    "EvaluationTimePolicyV1",
    "FrozenSplitPlanV1",
    "FrozenTradingCalendarV1",
    "ResearchSplit",
    "SplitWindowV1",
    "canonical_calendar",
]
