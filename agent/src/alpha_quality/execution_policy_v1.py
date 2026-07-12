"""Build-time registered policies for stateful A-share execution evidence."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

from src.research_ledger.hash_utils import canonical_json_hash


@dataclass(frozen=True)
class LongOnlyWeightingPolicyV1:
    top_fraction: float = 1.0 / 3.0
    minimum_names: int = 1
    maximum_weight: float = 1.0
    schema_version: Literal["long_only_weighting_policy.v1"] = (
        "long_only_weighting_policy.v1"
    )

    def __post_init__(self) -> None:
        if self.schema_version != "long_only_weighting_policy.v1":
            raise ValueError("unsupported long-only weighting policy")
        if not math.isfinite(self.top_fraction) or not 0.0 < self.top_fraction <= 1.0:
            raise ValueError("top_fraction must be finite and in (0, 1]")
        if (
            isinstance(self.minimum_names, bool)
            or not isinstance(self.minimum_names, int)
            or self.minimum_names < 1
        ):
            raise ValueError("minimum_names must be a positive integer")
        if (
            not math.isfinite(self.maximum_weight)
            or not 0.0 < self.maximum_weight <= 1.0
        ):
            raise ValueError("maximum_weight must be finite and in (0, 1]")

    @property
    def policy_hash(self) -> str:
        return canonical_json_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "top_fraction": self.top_fraction,
            "minimum_names": self.minimum_names,
            "maximum_weight": self.maximum_weight,
            "selection": "stable_descending_factor_then_symbol.v1",
            "normalization": "equal_weight_long_only.v1",
        }


@dataclass(frozen=True)
class AshareExecutionPolicyV1:
    portfolio_notional: float = 1_000_000.0
    maximum_participation_rate: float = 0.05
    commission_bps: float = 3.0
    sell_stamp_duty_bps: float = 5.0
    buy_slippage_bps: float = 5.0
    sell_slippage_bps: float = 5.0
    require_amount_for_capacity: bool = True
    require_terminal_flat: bool = True
    material_unpriced_exposure: float = 0.01
    schema_version: Literal["ashare_execution_policy.v1"] = "ashare_execution_policy.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "ashare_execution_policy.v1":
            raise ValueError("unsupported A-share execution policy")
        for name in (
            "portfolio_notional",
            "maximum_participation_rate",
            "commission_bps",
            "sell_stamp_duty_bps",
            "buy_slippage_bps",
            "sell_slippage_bps",
            "material_unpriced_exposure",
        ):
            value = getattr(self, name)
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.portfolio_notional <= 0.0:
            raise ValueError("portfolio_notional must be positive")
        if not 0.0 < self.maximum_participation_rate <= 1.0:
            raise ValueError("maximum participation must be in (0, 1]")
        for name in (
            "commission_bps",
            "sell_stamp_duty_bps",
            "buy_slippage_bps",
            "sell_slippage_bps",
        ):
            if not 0.0 <= float(getattr(self, name)) <= 1_000.0:
                raise ValueError(f"{name} must be in [0, 1000]")
        if not 0.0 <= self.material_unpriced_exposure <= 1.0:
            raise ValueError("material unpriced exposure must be in [0, 1]")
        if not isinstance(self.require_amount_for_capacity, bool) or not isinstance(
            self.require_terminal_flat, bool
        ):
            raise ValueError("execution policy booleans must be strict bool")

    @property
    def policy_hash(self) -> str:
        return canonical_json_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "portfolio_notional": self.portfolio_notional,
            "maximum_participation_rate": self.maximum_participation_rate,
            "commission_bps": self.commission_bps,
            "sell_stamp_duty_bps": self.sell_stamp_duty_bps,
            "buy_slippage_bps": self.buy_slippage_bps,
            "sell_slippage_bps": self.sell_slippage_bps,
            "require_amount_for_capacity": self.require_amount_for_capacity,
            "require_terminal_flat": self.require_terminal_flat,
            "material_unpriced_exposure": self.material_unpriced_exposure,
            "initial_state": "zero_holdings.v1",
            "settlement": "ashare_t_plus_one.v1",
            "terminal_liquidation": "market_feasible_only_no_fictional_fill.v1",
            "capacity": "entry_date_amount_participation.v1",
            "corporate_actions": "dated_factor_once.v1",
        }


@dataclass(frozen=True)
class MissingOutcomeScenarioPolicyV1:
    scenario_names: tuple[str, ...] = ("zero_return", "total_loss")
    schema_version: Literal["missing_outcome_scenario_policy.v1"] = (
        "missing_outcome_scenario_policy.v1"
    )

    def __post_init__(self) -> None:
        if self.schema_version != "missing_outcome_scenario_policy.v1":
            raise ValueError("unsupported missing-outcome policy")
        if self.scenario_names != tuple(sorted(set(self.scenario_names))):
            raise ValueError("missing-outcome scenarios must be sorted and unique")
        if self.scenario_names != ("total_loss", "zero_return"):
            raise ValueError("unknown missing-outcome scenario")

    @property
    def policy_hash(self) -> str:
        return canonical_json_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scenario_names": list(self.scenario_names),
            "scenario_semantics": "policy_scenarios_not_identified_bounds.v1",
            "zero_return_value": 0.0,
            "total_loss_value": -1.0,
        }


DEFAULT_LONG_ONLY_WEIGHTING_POLICY = LongOnlyWeightingPolicyV1()
DEFAULT_ASHARE_EXECUTION_POLICY = AshareExecutionPolicyV1()
DEFAULT_MISSING_OUTCOME_POLICY = MissingOutcomeScenarioPolicyV1(
    scenario_names=("total_loss", "zero_return")
)


__all__ = [
    "AshareExecutionPolicyV1",
    "DEFAULT_ASHARE_EXECUTION_POLICY",
    "DEFAULT_LONG_ONLY_WEIGHTING_POLICY",
    "DEFAULT_MISSING_OUTCOME_POLICY",
    "LongOnlyWeightingPolicyV1",
    "MissingOutcomeScenarioPolicyV1",
]
