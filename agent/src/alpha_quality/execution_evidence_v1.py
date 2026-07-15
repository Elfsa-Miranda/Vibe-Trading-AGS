"""Producer-bound, stateful A-share execution evidence."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from src.alpha_quality.evaluation_contract.contract import (
    CONTRACT_MEDIA_TYPE,
    ResolvedEvaluationContractArtifactStoreV1,
)
from src.alpha_quality.execution_policy_v1 import (
    AshareExecutionPolicyV1,
    DEFAULT_ASHARE_EXECUTION_POLICY,
    DEFAULT_LONG_ONLY_WEIGHTING_POLICY,
    DEFAULT_MISSING_OUTCOME_POLICY,
    LongOnlyWeightingPolicyV1,
    MissingOutcomeScenarioPolicyV1,
)
from src.alpha_quality.pit_artifact_v2 import (
    PIT_MANIFEST_MEDIA_TYPE,
    AsharePITParquetStoreV1,
    AsharePITTableReferenceV1,
    FrozenAsharePITSnapshotArtifactStoreV2,
)
from src.alpha_quality.portfolio_v2 import (
    WeightingPolicyV2,
    construct_target_weights_v2,
)
from src.alpha_quality.predictive_evidence_v4 import (
    FACTOR_OUTPUT_MEDIA_TYPE,
    FactorOutputArtifactStoreV3,
)
from src.research_ledger.events.artifacts import (
    AtomicContentAddressedArtifactWriter,
    ContentAddressedArtifact,
    validate_artifact_references,
)
from src.research_ledger.events.model import (
    EventDraft,
    EventTransitionError,
    EventValidationError,
    ResearchEventEnvelope,
)
from src.research_ledger.hash_utils import canonical_json, canonical_json_hash


EXECUTION_EVENT_TYPE = "ExecutionEvidenceRecorded"
EXECUTION_ARTIFACT_MEDIA_TYPE = "application/vnd.vibe.execution-artifact-v1+json"
EXECUTION_DECISION_MEDIA_TYPE = (
    "application/vnd.vibe.execution-decision-evidence-v1+json"
)
EXECUTION_PRODUCER_SCHEMA = "ashare_execution_evidence_service.v1"
EXECUTION_PRODUCER_POLICY_HASH = canonical_json_hash(
    {
        "schema_version": "ashare_execution_evidence_producer_policy.v1",
        "initial_holdings": "zero",
        "settlement": "t_plus_one",
        "blocked_trade": "holdings_persist",
        "cost_basis": "filled_trades_only",
        "missing_returns": "official_metric_nan_plus_registered_scenarios",
        "terminal_flat": "market_feasible_only",
        "channels": [
            "cross_sectional_predictive_proxy",
            "theoretical_long_short",
            "ashare_implementable_long_only",
            "hedged_implementable",
        ],
    }
)
EXECUTION_POLICY_HASHES = MappingProxyType(
    {
        "weighting_policy_hash": DEFAULT_LONG_ONLY_WEIGHTING_POLICY.policy_hash,
        "execution_policy_hash": DEFAULT_ASHARE_EXECUTION_POLICY.policy_hash,
        "missing_outcome_policy_hash": DEFAULT_MISSING_OUTCOME_POLICY.policy_hash,
    }
)
_STATE_NAMES = (
    "actual_holdings",
    "buy_blocked_notional",
    "filled_trades",
    "newly_bought_quantity",
    "sell_blocked_notional",
    "sellable_quantity",
    "submitted_trades",
    "target_weights",
    "unavailable_holding",
)
_AGGREGATE_COLUMNS = (
    "cost_return",
    "gross_return",
    "initial_entry_cost",
    "missing_return_exposure",
    "net_return",
    "priced_exposure",
    "terminal_exit_cost",
    "unfilled_notional",
    "unpriced_exposure",
)
_ARTIFACT_KEYS = frozenset(
    {
        "schema_version",
        "run_id",
        "factor_spec_id",
        "factor_output_hash",
        "factor_output_event_hash",
        "resolved_contract_hash",
        "contract_event_hash",
        "pit_snapshot_hash",
        "pit_snapshot_event_hash",
        "evaluation_policy_event_hash",
        "weighting_policy_hash",
        "execution_policy_hash",
        "missing_outcome_policy_hash",
        "state_table_ref",
        "aggregate_table_ref",
        "output_channels",
        "scenario_evidence",
        "availability",
        "terminal_flat",
        "material_unpriced_exposure",
        "caps",
        "warnings",
        "source_event_hashes",
        "producer_schema_version",
        "producer_policy_hash",
        "execution_artifact_hash",
    }
)
_DECISION_KEYS = frozenset(
    {
        "schema_version",
        "run_id",
        "factor_spec_id",
        "execution_artifact_hash",
        "availability",
        "implementability_claim",
        "material_unpriced_exposure",
        "terminal_flat",
        "promotion_effect",
        "caps",
        "limitations",
        "source_event_hashes",
        "producer_schema_version",
        "producer_policy_hash",
        "execution_decision_evidence_hash",
    }
)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _strict_json(path: Path) -> Mapping[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite execution artifact JSON: {value}")

    def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate execution artifact key")
            result[key] = value
        return result

    raw = json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicates,
    )
    if not isinstance(raw, Mapping):
        raise ValueError("execution artifact must be an object")
    return raw


def _exact_frame(
    frame: pd.DataFrame,
    index: pd.DatetimeIndex,
    columns: tuple[str, ...],
    name: str,
    *,
    boolean: bool = False,
) -> pd.DataFrame:
    if (
        not isinstance(frame, pd.DataFrame)
        or not frame.index.equals(index)
        or tuple(str(item) for item in frame.columns) != columns
        or frame.index.has_duplicates
        or frame.columns.has_duplicates
    ):
        raise ValueError(f"{name} axes must match exactly")
    if boolean:
        if any(dtype != np.dtype(bool) for dtype in frame.dtypes):
            raise ValueError(f"{name} must use strict bool dtype")
        return frame.astype(bool).copy(deep=True)
    if any(
        not pd.api.types.is_numeric_dtype(dtype) or pd.api.types.is_bool_dtype(dtype)
        for dtype in frame.dtypes
    ):
        raise ValueError(f"{name} must be numeric")
    result = frame.astype(float).copy(deep=True)
    if np.isinf(result.to_numpy()).any():
        raise ValueError(f"{name} contains Infinity")
    return result


@dataclass(frozen=True)
class StatefulExecutionResultV1:
    states: Mapping[str, pd.DataFrame]
    aggregate: pd.DataFrame
    output_channels: Mapping[str, Any]
    scenario_evidence: Mapping[str, Any]
    availability: Literal["available", "partial", "unavailable"]
    terminal_flat: bool
    material_unpriced_exposure: bool
    caps: tuple[str, ...]
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if set(self.states) != set(_STATE_NAMES):
            raise ValueError("execution result state inventory differs")
        copied = {name: frame.copy(deep=True) for name, frame in self.states.items()}
        object.__setattr__(self, "states", MappingProxyType(copied))
        object.__setattr__(self, "aggregate", self.aggregate.copy(deep=True))
        object.__setattr__(self, "output_channels", _freeze(self.output_channels))
        object.__setattr__(self, "scenario_evidence", _freeze(self.scenario_evidence))


def _target_long_only(
    factor: pd.DataFrame,
    membership: pd.DataFrame,
    can_observe: pd.DataFrame,
    policy: LongOnlyWeightingPolicyV1,
    *,
    entry_lag: int,
    rebalance_cadence: int,
) -> pd.DataFrame:
    scheduled = pd.DataFrame(np.nan, index=factor.index, columns=factor.columns)
    target = pd.DataFrame(0.0, index=factor.index, columns=factor.columns)
    current = pd.Series(0.0, index=factor.columns)
    for signal_position, signal_date in enumerate(factor.index):
        if signal_position % rebalance_cadence == 0:
            order_position = signal_position + entry_lag
            if order_position < len(factor.index):
                order_date = factor.index[order_position]
                valid = (
                    membership.loc[order_date]
                    & can_observe.loc[signal_date]
                    & factor.loc[signal_date].notna()
                )
                row = factor.loc[signal_date, valid].astype(float)
                selected_count = max(
                    policy.minimum_names,
                    int(math.ceil(len(row) * policy.top_fraction)),
                )
                selected = [
                    symbol
                    for symbol, _ in sorted(
                        ((str(symbol), float(value)) for symbol, value in row.items()),
                        key=lambda item: (-item[1], item[0]),
                    )[:selected_count]
                ]
                proposed = pd.Series(0.0, index=factor.columns)
                if selected:
                    equal = min(policy.maximum_weight, 1.0 / len(selected))
                    proposed.loc[selected] = equal
                    if proposed.sum() > 0.0:
                        proposed /= proposed.sum()
                scheduled.loc[order_date] = proposed
        if scheduled.loc[signal_date].notna().all():
            current = scheduled.loc[signal_date].copy()
        target.loc[signal_date] = current
    target.iloc[-1] = 0.0
    return target


def _corporate_action_multiplier(
    index: pd.DatetimeIndex,
    columns: tuple[str, ...],
    corporate_actions: pd.DataFrame,
) -> pd.DataFrame:
    multiplier = pd.DataFrame(1.0, index=index, columns=columns)
    if corporate_actions.empty:
        return multiplier
    required = {"effective_date", "factor", "symbol"}
    if not required.issubset(corporate_actions.columns):
        raise ValueError("corporate action fields are incomplete")
    for _, row in corporate_actions.iterrows():
        symbol = str(row["symbol"])
        date = pd.Timestamp(row["effective_date"]).normalize()
        factor = float(row["factor"])
        if symbol in columns and date in index:
            if not math.isfinite(factor) or factor <= 0.0:
                raise ValueError("corporate action factor must be finite and positive")
            multiplier.loc[date, symbol] *= factor
    return multiplier


def simulate_ashare_execution_v1(
    *,
    factor: pd.DataFrame,
    close: pd.DataFrame,
    amount: pd.DataFrame,
    membership: pd.DataFrame,
    can_buy: pd.DataFrame,
    can_sell: pd.DataFrame,
    can_observe: pd.DataFrame,
    corporate_actions: pd.DataFrame,
    entry_lag: int,
    rebalance_cadence: int,
    weighting_policy: LongOnlyWeightingPolicyV1 = DEFAULT_LONG_ONLY_WEIGHTING_POLICY,
    execution_policy: AshareExecutionPolicyV1 = DEFAULT_ASHARE_EXECUTION_POLICY,
    missing_outcome_policy: MissingOutcomeScenarioPolicyV1 = (
        DEFAULT_MISSING_OUTCOME_POLICY
    ),
) -> StatefulExecutionResultV1:
    if entry_lag < 1 or rebalance_cadence < 1:
        raise ValueError("execution timing must use positive lag and cadence")
    index = pd.DatetimeIndex(factor.index)
    if (
        index.tz is not None
        or index.has_duplicates
        or not index.is_monotonic_increasing
    ):
        raise ValueError("execution dates must be sorted timezone-naive dates")
    columns = tuple(str(item) for item in factor.columns)
    factor = _exact_frame(factor, index, columns, "factor")
    close = _exact_frame(close, index, columns, "close")
    amount = _exact_frame(amount, index, columns, "amount")
    membership = _exact_frame(membership, index, columns, "membership", boolean=True)
    can_buy = _exact_frame(can_buy, index, columns, "can_buy", boolean=True)
    can_sell = _exact_frame(can_sell, index, columns, "can_sell", boolean=True)
    can_observe = _exact_frame(can_observe, index, columns, "can_observe", boolean=True)
    target = _target_long_only(
        factor,
        membership,
        can_observe,
        weighting_policy,
        entry_lag=entry_lag,
        rebalance_cadence=rebalance_cadence,
    )
    action_multiplier = _corporate_action_multiplier(
        index,
        columns,
        corporate_actions,
    )
    daily_returns = close.mul(action_multiplier).div(close.shift(1)) - 1.0
    daily_returns.iloc[0] = np.nan

    states = {
        name: pd.DataFrame(0.0, index=index, columns=columns) for name in _STATE_NAMES
    }
    aggregate = pd.DataFrame(
        np.nan,
        index=index,
        columns=tuple(sorted(_AGGREGATE_COLUMNS)),
    )
    aggregate.loc[
        :,
        [
            "cost_return",
            "initial_entry_cost",
            "terminal_exit_cost",
            "unfilled_notional",
            "priced_exposure",
            "unpriced_exposure",
            "missing_return_exposure",
        ],
    ] = 0.0
    current = pd.Series(0.0, index=columns)
    previous_nonzero = False
    capacity_missing = False
    scenario_zero: list[float] = []
    scenario_loss: list[float] = []
    buy_rate = (
        execution_policy.commission_bps + execution_policy.buy_slippage_bps
    ) / 10_000.0
    sell_rate = (
        execution_policy.commission_bps
        + execution_policy.sell_stamp_duty_bps
        + execution_policy.sell_slippage_bps
    ) / 10_000.0

    for position, date in enumerate(index):
        prior = current.copy()
        sellable_at_open = prior.copy()
        desired = target.loc[date].astype(float)
        submitted = desired - prior
        filled = pd.Series(0.0, index=columns)
        buy_blocked = pd.Series(0.0, index=columns)
        sell_blocked = pd.Series(0.0, index=columns)
        for symbol in columns:
            request = float(submitted[symbol])
            if request > 0.0:
                capacity_value = amount.loc[date, symbol]
                capacity_available = bool(
                    math.isfinite(float(capacity_value)) and float(capacity_value) > 0.0
                )
                if (
                    execution_policy.require_amount_for_capacity
                    and not capacity_available
                ):
                    capacity_missing = True
                allowed = bool(
                    membership.loc[date, symbol]
                    and can_buy.loc[date, symbol]
                    and capacity_available
                )
                if allowed:
                    capacity_weight = (
                        float(capacity_value)
                        * execution_policy.maximum_participation_rate
                        / execution_policy.portfolio_notional
                    )
                    filled[symbol] = min(request, max(0.0, capacity_weight))
                buy_blocked[symbol] = request - filled[symbol]
            elif request < 0.0:
                requested_sell = -request
                allowed = bool(can_sell.loc[date, symbol])
                if allowed:
                    filled[symbol] = -min(
                        requested_sell,
                        max(0.0, float(sellable_at_open[symbol])),
                    )
                sell_blocked[symbol] = requested_sell - (-filled[symbol])
        current = (prior + filled).clip(lower=0.0)
        newly_bought = filled.clip(lower=0.0)
        sellable_close = (current - newly_bought).clip(lower=0.0)
        unavailable = current.where(
            (~can_observe.loc[date]) | close.loc[date].isna(),
            0.0,
        )
        states["target_weights"].loc[date] = desired
        states["submitted_trades"].loc[date] = submitted
        states["filled_trades"].loc[date] = filled
        states["actual_holdings"].loc[date] = current
        states["sellable_quantity"].loc[date] = sellable_close
        states["newly_bought_quantity"].loc[date] = newly_bought
        states["buy_blocked_notional"].loc[date] = buy_blocked
        states["sell_blocked_notional"].loc[date] = sell_blocked
        states["unavailable_holding"].loc[date] = unavailable

        buys = float(filled.clip(lower=0.0).sum())
        sells = float((-filled.clip(upper=0.0)).sum())
        cost = buys * buy_rate + sells * sell_rate
        aggregate.loc[date, "cost_return"] = cost
        aggregate.loc[date, "unfilled_notional"] = float(
            (submitted - filled).abs().sum()
        )
        if not previous_nonzero and current.abs().sum() > 0.0:
            aggregate.loc[date, "initial_entry_cost"] = cost
        previous_nonzero = bool(current.abs().sum() > 0.0)
        if position == len(index) - 1:
            aggregate.loc[date, "terminal_exit_cost"] = sells * sell_rate

        required = prior > 1e-15
        returns = daily_returns.loc[date]
        missing = required & returns.isna()
        missing_exposure = float(prior.where(missing, 0.0).sum())
        priced = float(prior.where(required & returns.notna(), 0.0).sum())
        aggregate.loc[date, "missing_return_exposure"] = missing_exposure
        aggregate.loc[date, "priced_exposure"] = priced
        aggregate.loc[date, "unpriced_exposure"] = float(unavailable.abs().sum())
        known_contribution = float(
            (prior.where(~missing, 0.0) * returns.fillna(0.0)).sum()
        )
        scenario_zero.append(known_contribution)
        scenario_loss.append(known_contribution - missing_exposure)
        if missing.any():
            gross = np.nan
        elif required.any():
            gross = float((prior * returns.fillna(0.0)).sum())
        else:
            gross = 0.0
        aggregate.loc[date, "gross_return"] = gross
        aggregate.loc[date, "net_return"] = (
            np.nan if not math.isfinite(float(gross)) else float(gross) - cost
        )

    terminal_flat = bool(np.allclose(current.to_numpy(), 0.0, atol=1e-12, rtol=0.0))
    maximum_unpriced = float(aggregate["unpriced_exposure"].max())
    material_unpriced = maximum_unpriced > execution_policy.material_unpriced_exposure
    any_position = bool(states["actual_holdings"].abs().to_numpy().sum() > 0.0)
    caps: set[str] = set()
    if capacity_missing:
        caps.add("CAPACITY_EVIDENCE_UNAVAILABLE")
    if aggregate["missing_return_exposure"].max() > 0.0:
        caps.add("MISSING_REQUIRED_RETURN")
    if material_unpriced:
        caps.add("MATERIAL_UNPRICED_EXPOSURE")
    if execution_policy.require_terminal_flat and not terminal_flat:
        caps.add("TERMINAL_LIQUIDATION_INCOMPLETE")
    availability: Literal["available", "partial", "unavailable"]
    if not any_position:
        availability = "unavailable"
        caps.add("NO_EXECUTED_POSITION")
    elif caps:
        availability = "partial"
    else:
        availability = "available"

    theoretical_policy = WeightingPolicyV2(
        method="continuous_rank_long_short.v2",
        minimum_names_per_side=1,
    )
    theoretical = construct_target_weights_v2(
        factor,
        membership & can_observe,
        theoretical_policy,
    ).weights.shift(entry_lag)
    theoretical_required = theoretical.ne(0.0)
    theoretical_missing = theoretical_required & daily_returns.isna()
    theoretical_return = (theoretical * daily_returns).sum(axis=1, min_count=1)
    theoretical_return.loc[theoretical_missing.any(axis=1)] = np.nan
    channels = {
        "cross_sectional_predictive_proxy": {
            "availability": "referenced_from_predictive_producer",
            "implementability": "not_applicable",
        },
        "theoretical_long_short": {
            "availability": "available",
            "implementability": "research_theoretical_not_cash_implementable",
            "return_series_hash": canonical_json_hash(
                {
                    str(pd.Timestamp(date).date()): (
                        None if pd.isna(value) else float(value)
                    )
                    for date, value in theoretical_return.items()
                }
            ),
        },
        "ashare_implementable_long_only": {
            "availability": availability,
            "implementability": (
                "supported" if availability == "available" else "inconclusive"
            ),
            "terminal_flat": terminal_flat,
            "maximum_unpriced_exposure": maximum_unpriced,
        },
        "hedged_implementable": {
            "availability": "unavailable",
            "implementability": "borrow_and_hedge_evidence_unavailable",
        },
    }
    scenarios = {
        "schema_version": "missing_outcome_scenario_evidence.v1",
        "policy_hash": missing_outcome_policy.policy_hash,
        "scenario_semantics": "policy_scenarios_not_identified_bounds.v1",
        "scenario_return_sums": {
            "total_loss": float(math.fsum(scenario_loss)),
            "zero_return": float(math.fsum(scenario_zero)),
        },
        "missing_exposure_dates": int(
            (aggregate["missing_return_exposure"] > 0.0).sum()
        ),
    }
    return StatefulExecutionResultV1(
        states=states,
        aggregate=aggregate,
        output_channels=channels,
        scenario_evidence=scenarios,
        availability=availability,
        terminal_flat=terminal_flat,
        material_unpriced_exposure=material_unpriced,
        caps=tuple(sorted(caps)),
        warnings=("EOD_PROXY_LIMITATION",),
    )


def _pack_states(states: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    packed = pd.concat(
        {state: frame.astype(float) for state, frame in sorted(states.items())},
        axis=1,
    )
    packed.columns = [f"{state}|{symbol}" for state, symbol in packed.columns]
    return packed.reindex(sorted(packed.columns), axis=1)


def _unpack_states(
    packed: pd.DataFrame,
    symbols: tuple[str, ...],
) -> Mapping[str, pd.DataFrame]:
    expected = tuple(
        sorted(f"{state}|{symbol}" for state in _STATE_NAMES for symbol in symbols)
    )
    if tuple(packed.columns) != expected:
        raise ValueError("execution state table columns differ")
    result: dict[str, pd.DataFrame] = {}
    for state in _STATE_NAMES:
        frame = packed[[f"{state}|{symbol}" for symbol in symbols]].copy()
        frame.columns = list(symbols)
        result[state] = frame
    return MappingProxyType(result)


@dataclass(frozen=True)
class ExecutionArtifactV1:
    schema_version: Literal["execution_artifact.v1"]
    run_id: str
    factor_spec_id: str
    factor_output_hash: str
    factor_output_event_hash: str
    resolved_contract_hash: str
    contract_event_hash: str
    pit_snapshot_hash: str
    pit_snapshot_event_hash: str
    evaluation_policy_event_hash: str
    weighting_policy_hash: str
    execution_policy_hash: str
    missing_outcome_policy_hash: str
    state_table_ref: Mapping[str, Any]
    aggregate_table_ref: Mapping[str, Any]
    output_channels: Mapping[str, Any]
    scenario_evidence: Mapping[str, Any]
    availability: Literal["available", "partial", "unavailable"]
    terminal_flat: bool
    material_unpriced_exposure: bool
    caps: tuple[str, ...]
    warnings: tuple[str, ...]
    source_event_hashes: tuple[str, ...]
    producer_schema_version: str
    producer_policy_hash: str
    execution_artifact_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "execution_artifact.v1":
            raise ValueError("unsupported execution artifact")
        if self.producer_schema_version != EXECUTION_PRODUCER_SCHEMA:
            raise ValueError("unknown execution artifact producer")
        if self.producer_policy_hash != EXECUTION_PRODUCER_POLICY_HASH:
            raise ValueError("execution producer policy differs")
        for name in ("caps", "warnings", "source_event_hashes"):
            values = tuple(getattr(self, name))
            if values != tuple(sorted(set(values))):
                raise ValueError(f"execution {name} are not canonical")
        for reference in (self.state_table_ref, self.aggregate_table_ref):
            AsharePITTableReferenceV1.from_dict(reference)
        object.__setattr__(self, "state_table_ref", _freeze(self.state_table_ref))
        object.__setattr__(
            self, "aggregate_table_ref", _freeze(self.aggregate_table_ref)
        )
        object.__setattr__(self, "output_channels", _freeze(self.output_channels))
        object.__setattr__(self, "scenario_evidence", _freeze(self.scenario_evidence))
        if self.execution_artifact_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("execution artifact hash differs")

    def _content_dict(self) -> dict[str, Any]:
        return {
            name: value
            for name, value in {
                "schema_version": self.schema_version,
                "run_id": self.run_id,
                "factor_spec_id": self.factor_spec_id,
                "factor_output_hash": self.factor_output_hash,
                "factor_output_event_hash": self.factor_output_event_hash,
                "resolved_contract_hash": self.resolved_contract_hash,
                "contract_event_hash": self.contract_event_hash,
                "pit_snapshot_hash": self.pit_snapshot_hash,
                "pit_snapshot_event_hash": self.pit_snapshot_event_hash,
                "evaluation_policy_event_hash": self.evaluation_policy_event_hash,
                "weighting_policy_hash": self.weighting_policy_hash,
                "execution_policy_hash": self.execution_policy_hash,
                "missing_outcome_policy_hash": self.missing_outcome_policy_hash,
                "state_table_ref": _plain(self.state_table_ref),
                "aggregate_table_ref": _plain(self.aggregate_table_ref),
                "output_channels": _plain(self.output_channels),
                "scenario_evidence": _plain(self.scenario_evidence),
                "availability": self.availability,
                "terminal_flat": self.terminal_flat,
                "material_unpriced_exposure": self.material_unpriced_exposure,
                "caps": list(self.caps),
                "warnings": list(self.warnings),
                "source_event_hashes": list(self.source_event_hashes),
                "producer_schema_version": self.producer_schema_version,
                "producer_policy_hash": self.producer_policy_hash,
            }.items()
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._content_dict(),
            "execution_artifact_hash": self.execution_artifact_hash,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ExecutionArtifactV1":
        if set(raw) != _ARTIFACT_KEYS:
            raise ValueError("execution artifact schema is not closed")
        return cls(
            schema_version=str(raw["schema_version"]),  # type: ignore[arg-type]
            run_id=str(raw["run_id"]),
            factor_spec_id=str(raw["factor_spec_id"]),
            factor_output_hash=str(raw["factor_output_hash"]),
            factor_output_event_hash=str(raw["factor_output_event_hash"]),
            resolved_contract_hash=str(raw["resolved_contract_hash"]),
            contract_event_hash=str(raw["contract_event_hash"]),
            pit_snapshot_hash=str(raw["pit_snapshot_hash"]),
            pit_snapshot_event_hash=str(raw["pit_snapshot_event_hash"]),
            evaluation_policy_event_hash=str(raw["evaluation_policy_event_hash"]),
            weighting_policy_hash=str(raw["weighting_policy_hash"]),
            execution_policy_hash=str(raw["execution_policy_hash"]),
            missing_outcome_policy_hash=str(raw["missing_outcome_policy_hash"]),
            state_table_ref=dict(raw["state_table_ref"]),
            aggregate_table_ref=dict(raw["aggregate_table_ref"]),
            output_channels=dict(raw["output_channels"]),
            scenario_evidence=dict(raw["scenario_evidence"]),
            availability=str(raw["availability"]),  # type: ignore[arg-type]
            terminal_flat=bool(raw["terminal_flat"]),
            material_unpriced_exposure=bool(raw["material_unpriced_exposure"]),
            caps=tuple(str(item) for item in raw["caps"]),
            warnings=tuple(str(item) for item in raw["warnings"]),
            source_event_hashes=tuple(str(item) for item in raw["source_event_hashes"]),
            producer_schema_version=str(raw["producer_schema_version"]),
            producer_policy_hash=str(raw["producer_policy_hash"]),
            execution_artifact_hash=str(raw["execution_artifact_hash"]),
        )


@dataclass(frozen=True)
class ExecutionDecisionEvidenceV1:
    schema_version: Literal["execution_decision_evidence.v1"]
    run_id: str
    factor_spec_id: str
    execution_artifact_hash: str
    availability: str
    implementability_claim: Literal["supported", "inconclusive", "unavailable"]
    material_unpriced_exposure: bool
    terminal_flat: bool
    promotion_effect: Literal["none"]
    caps: tuple[str, ...]
    limitations: tuple[str, ...]
    source_event_hashes: tuple[str, ...]
    producer_schema_version: str
    producer_policy_hash: str
    execution_decision_evidence_hash: str

    def __post_init__(self) -> None:
        for name in ("caps", "limitations", "source_event_hashes"):
            values = tuple(getattr(self, name))
            if values != tuple(sorted(set(values))):
                raise ValueError(f"execution decision {name} are not canonical")
        if self.promotion_effect != "none":
            raise ValueError("execution subproducer cannot promote")
        if self.execution_decision_evidence_hash != canonical_json_hash(
            self._content_dict()
        ):
            raise ValueError("execution decision evidence hash differs")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "factor_spec_id": self.factor_spec_id,
            "execution_artifact_hash": self.execution_artifact_hash,
            "availability": self.availability,
            "implementability_claim": self.implementability_claim,
            "material_unpriced_exposure": self.material_unpriced_exposure,
            "terminal_flat": self.terminal_flat,
            "promotion_effect": self.promotion_effect,
            "caps": list(self.caps),
            "limitations": list(self.limitations),
            "source_event_hashes": list(self.source_event_hashes),
            "producer_schema_version": self.producer_schema_version,
            "producer_policy_hash": self.producer_policy_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._content_dict(),
            "execution_decision_evidence_hash": self.execution_decision_evidence_hash,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ExecutionDecisionEvidenceV1":
        if set(raw) != _DECISION_KEYS:
            raise ValueError("execution decision evidence schema is not closed")
        return cls(
            schema_version=str(raw["schema_version"]),  # type: ignore[arg-type]
            run_id=str(raw["run_id"]),
            factor_spec_id=str(raw["factor_spec_id"]),
            execution_artifact_hash=str(raw["execution_artifact_hash"]),
            availability=str(raw["availability"]),
            implementability_claim=str(raw["implementability_claim"]),  # type: ignore[arg-type]
            material_unpriced_exposure=bool(raw["material_unpriced_exposure"]),
            terminal_flat=bool(raw["terminal_flat"]),
            promotion_effect=str(raw["promotion_effect"]),  # type: ignore[arg-type]
            caps=tuple(str(item) for item in raw["caps"]),
            limitations=tuple(str(item) for item in raw["limitations"]),
            source_event_hashes=tuple(str(item) for item in raw["source_event_hashes"]),
            producer_schema_version=str(raw["producer_schema_version"]),
            producer_policy_hash=str(raw["producer_policy_hash"]),
            execution_decision_evidence_hash=str(
                raw["execution_decision_evidence_hash"]
            ),
        )


class ExecutionEvidenceArtifactStoreV1:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve(strict=True)
        self.writer = AtomicContentAddressedArtifactWriter(
            self.root, max_bytes=8 * 1024**2
        )
        self.tables = AsharePITParquetStoreV1(self.root)

    def write_tables(
        self,
        factor_spec_id: str,
        result: StatefulExecutionResultV1,
    ) -> tuple[AsharePITTableReferenceV1, AsharePITTableReferenceV1]:
        state = self.tables.write(
            "execution-state:" + factor_spec_id,
            "numeric_frame",
            _pack_states(result.states),
            index_name="date",
        )
        aggregate = self.tables.write(
            "execution-aggregate:" + factor_spec_id,
            "numeric_frame",
            result.aggregate,
            index_name="date",
        )
        return state, aggregate

    def write_artifact(self, artifact: ExecutionArtifactV1) -> ContentAddressedArtifact:
        return self.writer.write_json(
            namespace="execution-artifact-v1",
            payload=artifact.to_dict(),
            schema_version="execution_artifact.v1",
            semantic_hash_field="execution_artifact_hash",
            closed_keys=_ARTIFACT_KEYS,
            media_type=EXECUTION_ARTIFACT_MEDIA_TYPE,
        )

    def write_decision(
        self,
        evidence: ExecutionDecisionEvidenceV1,
    ) -> ContentAddressedArtifact:
        return self.writer.write_json(
            namespace="execution-decision-evidence-v1",
            payload=evidence.to_dict(),
            schema_version="execution_decision_evidence.v1",
            semantic_hash_field="execution_decision_evidence_hash",
            closed_keys=_DECISION_KEYS,
            media_type=EXECUTION_DECISION_MEDIA_TYPE,
        )

    def read_artifact(
        self,
        relative_path: str,
        *,
        expected_hash: str,
        expected_blob_hash: str,
    ) -> ExecutionArtifactV1:
        raw = self._read_json(
            relative_path,
            expected_hash=expected_hash,
            expected_blob_hash=expected_blob_hash,
            namespace="execution-artifact-v1",
            media_type=EXECUTION_ARTIFACT_MEDIA_TYPE,
            keys=_ARTIFACT_KEYS,
            semantic_field="execution_artifact_hash",
        )
        artifact = ExecutionArtifactV1.from_dict(raw)
        self.read_tables(artifact)
        return artifact

    def read_decision(
        self,
        relative_path: str,
        *,
        expected_hash: str,
        expected_blob_hash: str,
    ) -> ExecutionDecisionEvidenceV1:
        raw = self._read_json(
            relative_path,
            expected_hash=expected_hash,
            expected_blob_hash=expected_blob_hash,
            namespace="execution-decision-evidence-v1",
            media_type=EXECUTION_DECISION_MEDIA_TYPE,
            keys=_DECISION_KEYS,
            semantic_field="execution_decision_evidence_hash",
        )
        return ExecutionDecisionEvidenceV1.from_dict(raw)

    def _read_json(
        self,
        relative_path: str,
        *,
        expected_hash: str,
        expected_blob_hash: str,
        namespace: str,
        media_type: str,
        keys: frozenset[str],
        semantic_field: str,
    ) -> Mapping[str, Any]:
        normalized = validate_artifact_references(
            self.root,
            [
                {
                    "relative_path": relative_path,
                    "artifact_hash": expected_blob_hash,
                    "media_type": media_type,
                }
            ],
        )[0]
        digest = expected_hash.removeprefix("sha256:")
        expected = f"{namespace}/{digest[:2]}/{digest}.json"
        if normalized["relative_path"] != expected:
            raise ValueError("execution JSON path is not content addressed")
        raw = _strict_json(self.root.joinpath(*expected.split("/")))
        if set(raw) != keys or raw[semantic_field] != expected_hash:
            raise ValueError("execution JSON identity differs")
        return raw

    def read_tables(
        self,
        artifact: ExecutionArtifactV1,
    ) -> tuple[Mapping[str, pd.DataFrame], pd.DataFrame]:
        state = self.tables.read(
            AsharePITTableReferenceV1.from_dict(artifact.state_table_ref)
        )
        aggregate = self.tables.read(
            AsharePITTableReferenceV1.from_dict(artifact.aggregate_table_ref)
        )
        state.index = pd.DatetimeIndex(state.index)
        aggregate.index = pd.DatetimeIndex(aggregate.index)
        symbols = tuple(
            sorted(
                column.split("|", 1)[1]
                for column in state.columns
                if column.startswith("actual_holdings|")
            )
        )
        states = _unpack_states(state, symbols)
        if tuple(aggregate.columns) != tuple(sorted(_AGGREGATE_COLUMNS)):
            raise ValueError("execution aggregate columns differ")
        if not aggregate.index.equals(state.index):
            raise ValueError("execution state and aggregate dates differ")
        return states, aggregate.astype(float)


@dataclass(frozen=True)
class RecordedExecutionEvidenceV1:
    artifact: ExecutionArtifactV1
    decision_evidence: ExecutionDecisionEvidenceV1
    event: ResearchEventEnvelope


class ExecutionEvidenceServiceV1:
    def __init__(self, store: Any) -> None:
        from src.research_ledger.events.store import ResearchEventStore

        if not isinstance(store, ResearchEventStore):
            raise TypeError("execution evidence requires ResearchEventStore")
        required = (
            "VIBE_TRADING_ALPHA_FOUNDRY",
            "VIBE_TRADING_ALPHA_SCORECARD",
            "VIBE_TRADING_RESEARCH_EVENTS",
            "VIBE_TRADING_FACTOR_DAG",
        )
        if any(not store.flags.enabled(name) for name in required):
            raise RuntimeError("execution evidence capability is disabled")
        self.store = store
        self.artifacts = ExecutionEvidenceArtifactStoreV1(store.artifact_root)

    def record(
        self,
        *,
        run_id: str,
        factor_output_event_hash: str,
        observed_predictive_event_hash: str,
    ) -> RecordedExecutionEvidenceV1:
        existing = [
            event
            for event in self.store.query_events(event_type=EXECUTION_EVENT_TYPE)
            if event.run_id == run_id
            and event.payload["factor_output_event_hash"] == factor_output_event_hash
        ]
        if existing:
            if len(existing) != 1:
                raise EventTransitionError("execution evidence is ambiguous")
            recorded = self._read_event(existing[0])
            if (
                recorded.artifact.factor_output_event_hash != factor_output_event_hash
                or existing[0].payload["observed_predictive_event_hash"]
                != observed_predictive_event_hash
            ):
                raise EventTransitionError("execution retry sources differ")
            rebuilt_artifact, rebuilt_decision, _ = self.rebuild(
                self.store,
                run_id=run_id,
                factor_output_event_hash=factor_output_event_hash,
                observed_predictive_event_hash=observed_predictive_event_hash,
                persist_tables=False,
            )
            if (
                rebuilt_artifact != recorded.artifact
                or rebuilt_decision != recorded.decision_evidence
            ):
                raise EventValidationError("execution retry does not replay exactly")
            return recorded
        artifact, decision, result = self.rebuild(
            self.store,
            run_id=run_id,
            factor_output_event_hash=factor_output_event_hash,
            observed_predictive_event_hash=observed_predictive_event_hash,
            persist_tables=True,
        )
        artifact_ref = self.artifacts.write_artifact(artifact)
        decision_ref = self.artifacts.write_decision(decision)
        event = self.store._append_producer_event(
            EventDraft(
                event_type=EXECUTION_EVENT_TYPE,
                entity_id="execution-evidence-"
                + artifact.execution_artifact_hash.removeprefix("sha256:")[:24],
                run_id=run_id,
                payload_schema_version="execution_evidence_recorded.v1",
                idempotency_key="execution-evidence:"
                + artifact.execution_artifact_hash,
                payload=self.event_payload(
                    artifact,
                    decision,
                    artifact_ref.reference(),
                    decision_ref.reference(),
                ),
            )
        )
        if result.availability != artifact.availability:
            raise EventValidationError("execution result availability changed")
        return RecordedExecutionEvidenceV1(artifact, decision, event)

    @staticmethod
    def rebuild(
        store: Any,
        *,
        run_id: str,
        factor_output_event_hash: str,
        observed_predictive_event_hash: str,
        persist_tables: bool,
    ) -> tuple[
        ExecutionArtifactV1, ExecutionDecisionEvidenceV1, StatefulExecutionResultV1
    ]:
        events = store.query_events()
        by_hash = {event.event_hash: event for event in events}
        factor_event = by_hash.get(factor_output_event_hash)
        observed_event = by_hash.get(observed_predictive_event_hash)
        if (
            factor_event is None
            or factor_event.event_type != "FactorOutputRecordedV3"
            or observed_event is None
            or observed_event.event_type != "ObservedPanelPredictiveEvidenceRecorded"
            or factor_event.run_id != run_id
            or observed_event.run_id != run_id
            or observed_event.payload["factor_output_event_hash"]
            != factor_event.event_hash
        ):
            raise EventTransitionError("execution predictive sources differ")
        factor_ref = next(
            item
            for item in factor_event.payload["artifact_refs"]
            if item["media_type"] == FACTOR_OUTPUT_MEDIA_TYPE
        )
        factor_store = FactorOutputArtifactStoreV3(store.artifact_root)
        factor_artifact = factor_store.read_manifest(
            str(factor_ref["relative_path"]),
            expected_hash=str(factor_event.payload["factor_output_hash"]),
            expected_blob_hash=str(factor_ref["artifact_hash"]),
        )
        factor = factor_store.read_factor_frame(factor_artifact)
        contract_event = by_hash[factor_artifact.contract_event_hash]
        contract_ref = next(
            item
            for item in contract_event.payload["artifact_refs"]
            if item["media_type"] == CONTRACT_MEDIA_TYPE
        )
        contract = ResolvedEvaluationContractArtifactStoreV1(store.artifact_root).read(
            str(contract_ref["relative_path"]),
            expected_contract_hash=str(contract_event.payload["contract_hash"]),
            expected_blob_hash=str(contract_ref["artifact_hash"]),
        )
        expected_policies = dict(EXECUTION_POLICY_HASHES)
        for name, expected in expected_policies.items():
            if getattr(contract.policy_references, name) != expected:
                raise EventValidationError("execution contract policy differs")
        snapshot_event = by_hash[factor_artifact.pit_snapshot_event_hash]
        snapshot_ref = next(
            item
            for item in snapshot_event.payload["artifact_refs"]
            if item["media_type"] == PIT_MANIFEST_MEDIA_TYPE
        )
        snapshot_store = FrozenAsharePITSnapshotArtifactStoreV2(store.artifact_root)
        snapshot = snapshot_store.read_manifest(
            str(snapshot_ref["relative_path"]),
            expected_snapshot_hash=str(snapshot_event.payload["snapshot_hash"]),
            expected_blob_hash=str(snapshot_ref["artifact_hash"]),
        )
        bundle = snapshot_store.read_bundle(snapshot)
        masks = snapshot_store.read_derived_masks(snapshot)
        policy_event = by_hash[factor_artifact.evaluation_policy_event_hash]
        from src.alpha_quality.evaluation_registry_v1 import (
            EvaluationPolicyArtifactStoreV1,
        )

        policy_ref = policy_event.payload["artifact_refs"][0]
        policy_bundle = EvaluationPolicyArtifactStoreV1(store.artifact_root).read(
            str(policy_ref["relative_path"]),
            expected_bundle_hash=str(policy_event.payload["bundle_hash"]),
            expected_blob_hash=str(policy_ref["artifact_hash"]),
        )
        _, time_policy, split_plan = policy_bundle.resolved_components()
        close = bundle.market_fields.get("close")
        amount = bundle.market_fields.get("amount")
        if close is None or amount is None:
            raise EventValidationError("execution requires close and amount")
        result = simulate_ashare_execution_v1(
            factor=factor,
            close=close,
            amount=amount,
            membership=bundle.daily_membership,
            can_buy=masks["can_buy"],
            can_sell=masks["can_sell"],
            can_observe=masks["can_observe"],
            corporate_actions=bundle.corporate_actions,
            entry_lag=time_policy.entry_lag_trading_days,
            rebalance_cadence=time_policy.rebalance_cadence,
        )
        artifact_store = ExecutionEvidenceArtifactStoreV1(store.artifact_root)
        if persist_tables:
            state_ref, aggregate_ref = artifact_store.write_tables(
                factor_artifact.factor_spec_id,
                result,
            )
        else:
            execution_events = [
                event
                for event in events
                if event.event_type == EXECUTION_EVENT_TYPE and event.run_id == run_id
            ]
            if len(execution_events) != 1:
                raise EventValidationError("recorded execution tables are unavailable")
            artifact_event_ref = next(
                item
                for item in execution_events[0].payload["artifact_refs"]
                if item["media_type"] == EXECUTION_ARTIFACT_MEDIA_TYPE
            )
            recorded = artifact_store.read_artifact(
                str(artifact_event_ref["relative_path"]),
                expected_hash=str(
                    execution_events[0].payload["execution_artifact_hash"]
                ),
                expected_blob_hash=str(artifact_event_ref["artifact_hash"]),
            )
            state_ref = AsharePITTableReferenceV1.from_dict(recorded.state_table_ref)
            aggregate_ref = AsharePITTableReferenceV1.from_dict(
                recorded.aggregate_table_ref
            )
            states, aggregate = artifact_store.read_tables(recorded)
            for name in _STATE_NAMES:
                pd.testing.assert_frame_equal(
                    result.states[name], states[name], check_freq=False
                )
            pd.testing.assert_frame_equal(
                result.aggregate,
                aggregate,
                check_freq=False,
            )
        sources = tuple(
            sorted(
                {
                    factor_event.event_hash,
                    observed_event.event_hash,
                    contract_event.event_hash,
                    snapshot_event.event_hash,
                    policy_event.event_hash,
                }
            )
        )
        artifact_content = {
            "schema_version": "execution_artifact.v1",
            "run_id": run_id,
            "factor_spec_id": factor_artifact.factor_spec_id,
            "factor_output_hash": factor_artifact.factor_output_hash,
            "factor_output_event_hash": factor_event.event_hash,
            "resolved_contract_hash": contract.contract_hash,
            "contract_event_hash": contract_event.event_hash,
            "pit_snapshot_hash": snapshot.snapshot_hash,
            "pit_snapshot_event_hash": snapshot_event.event_hash,
            "evaluation_policy_event_hash": policy_event.event_hash,
            **expected_policies,
            "state_table_ref": state_ref.to_dict(),
            "aggregate_table_ref": aggregate_ref.to_dict(),
            "output_channels": _plain(result.output_channels),
            "scenario_evidence": _plain(result.scenario_evidence),
            "availability": result.availability,
            "terminal_flat": result.terminal_flat,
            "material_unpriced_exposure": result.material_unpriced_exposure,
            "caps": list(result.caps),
            "warnings": list(result.warnings),
            "source_event_hashes": list(sources),
            "producer_schema_version": EXECUTION_PRODUCER_SCHEMA,
            "producer_policy_hash": EXECUTION_PRODUCER_POLICY_HASH,
        }
        artifact = ExecutionArtifactV1.from_dict(
            {
                **artifact_content,
                "execution_artifact_hash": canonical_json_hash(artifact_content),
            }
        )
        claim: Literal["supported", "inconclusive", "unavailable"] = (
            "supported"
            if result.availability == "available"
            else (
                "unavailable"
                if result.availability == "unavailable"
                else "inconclusive"
            )
        )
        decision_content = {
            "schema_version": "execution_decision_evidence.v1",
            "run_id": run_id,
            "factor_spec_id": factor_artifact.factor_spec_id,
            "execution_artifact_hash": artifact.execution_artifact_hash,
            "availability": result.availability,
            "implementability_claim": claim,
            "material_unpriced_exposure": result.material_unpriced_exposure,
            "terminal_flat": result.terminal_flat,
            "promotion_effect": "none",
            "caps": list(result.caps),
            "limitations": [
                "EOD_PROXY_LIMITATION",
                "HEDGED_IMPLEMENTABILITY_UNAVAILABLE",
                "SCENARIOS_ARE_NOT_IDENTIFIED_BOUNDS",
            ],
            "source_event_hashes": list(sources),
            "producer_schema_version": EXECUTION_PRODUCER_SCHEMA,
            "producer_policy_hash": EXECUTION_PRODUCER_POLICY_HASH,
        }
        decision = ExecutionDecisionEvidenceV1.from_dict(
            {
                **decision_content,
                "execution_decision_evidence_hash": canonical_json_hash(
                    decision_content
                ),
            }
        )
        return artifact, decision, result

    @staticmethod
    def supports_contract(store: Any, contract_event_hash: str) -> bool:
        events = {event.event_hash: event for event in store.query_events()}
        event = events.get(contract_event_hash)
        if event is None or event.event_type != "ResolvedEvaluationContractRegistered":
            raise EventValidationError("execution contract event is unavailable")
        reference = next(
            item
            for item in event.payload["artifact_refs"]
            if item["media_type"] == CONTRACT_MEDIA_TYPE
        )
        contract = ResolvedEvaluationContractArtifactStoreV1(store.artifact_root).read(
            str(reference["relative_path"]),
            expected_contract_hash=str(event.payload["contract_hash"]),
            expected_blob_hash=str(reference["artifact_hash"]),
        )
        return all(
            getattr(contract.policy_references, name) == expected
            for name, expected in EXECUTION_POLICY_HASHES.items()
        )

    @staticmethod
    def event_payload(
        artifact: ExecutionArtifactV1,
        decision: ExecutionDecisionEvidenceV1,
        artifact_reference: Mapping[str, str],
        decision_reference: Mapping[str, str],
    ) -> dict[str, Any]:
        return {
            "evidence_id": "execution-evidence-"
            + artifact.execution_artifact_hash.removeprefix("sha256:")[:24],
            "factor_spec_id": artifact.factor_spec_id,
            "execution_artifact_hash": artifact.execution_artifact_hash,
            "execution_decision_evidence_hash": (
                decision.execution_decision_evidence_hash
            ),
            "factor_output_event_hash": artifact.factor_output_event_hash,
            "observed_predictive_event_hash": next(
                item
                for item in artifact.source_event_hashes
                if item
                not in {
                    artifact.factor_output_event_hash,
                    artifact.contract_event_hash,
                    artifact.pit_snapshot_event_hash,
                    artifact.evaluation_policy_event_hash,
                }
            ),
            "resolved_contract_hash": artifact.resolved_contract_hash,
            "availability": artifact.availability,
            "implementability_claim": decision.implementability_claim,
            "material_unpriced_exposure": artifact.material_unpriced_exposure,
            "terminal_flat": artifact.terminal_flat,
            "promotion_effect": decision.promotion_effect,
            "caps": list(decision.caps),
            "source_event_hashes": list(artifact.source_event_hashes),
            "producer_schema_version": artifact.producer_schema_version,
            "producer_policy_hash": artifact.producer_policy_hash,
            "artifact_refs": [dict(artifact_reference), dict(decision_reference)],
        }

    def _read_event(
        self,
        event: ResearchEventEnvelope,
    ) -> RecordedExecutionEvidenceV1:
        artifact_ref = next(
            item
            for item in event.payload["artifact_refs"]
            if item["media_type"] == EXECUTION_ARTIFACT_MEDIA_TYPE
        )
        decision_ref = next(
            item
            for item in event.payload["artifact_refs"]
            if item["media_type"] == EXECUTION_DECISION_MEDIA_TYPE
        )
        artifact = self.artifacts.read_artifact(
            str(artifact_ref["relative_path"]),
            expected_hash=str(event.payload["execution_artifact_hash"]),
            expected_blob_hash=str(artifact_ref["artifact_hash"]),
        )
        decision = self.artifacts.read_decision(
            str(decision_ref["relative_path"]),
            expected_hash=str(event.payload["execution_decision_evidence_hash"]),
            expected_blob_hash=str(decision_ref["artifact_hash"]),
        )
        if canonical_json(
            self.event_payload(artifact, decision, artifact_ref, decision_ref)
        ) != canonical_json(_plain(event.payload)):
            raise EventValidationError("existing execution event differs")
        return RecordedExecutionEvidenceV1(artifact, decision, event)


__all__ = [
    "EXECUTION_ARTIFACT_MEDIA_TYPE",
    "EXECUTION_DECISION_MEDIA_TYPE",
    "EXECUTION_EVENT_TYPE",
    "EXECUTION_POLICY_HASHES",
    "ExecutionArtifactV1",
    "ExecutionDecisionEvidenceV1",
    "ExecutionEvidenceArtifactStoreV1",
    "ExecutionEvidenceServiceV1",
    "RecordedExecutionEvidenceV1",
    "StatefulExecutionResultV1",
    "simulate_ashare_execution_v1",
]
