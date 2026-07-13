"""Deterministic validation and mask derivation for A-share PIT source bundles."""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from src.alpha_quality.pit_adapter_v1 import (
    AsharePITSnapshotRequestV1,
    AsharePITSourceBundleV1,
    RegisteredAsharePITAdapterV1,
)
from src.research_ledger.hash_utils import canonical_json_hash


ASHARE_PIT_REQUIRED_FIELDS = (
    "amount",
    "close",
    "high",
    "low",
    "open",
    "volume",
)
ASHARE_PIT_REQUIRED_TRADE_STATES = (
    "at_limit_down",
    "at_limit_up",
    "is_st",
    "is_suspended",
    "listing_age_days",
)


@dataclass(frozen=True)
class AsharePITValidationPolicyV1:
    required_fields: tuple[str, ...] = ASHARE_PIT_REQUIRED_FIELDS
    required_trade_states: tuple[str, ...] = ASHARE_PIT_REQUIRED_TRADE_STATES
    minimum_listing_age_days: int = 60
    minimum_amount: float = 0.0
    signal_available_by_local_time: str = "15:00:00"
    exclude_st_from_buys: bool = True
    schema_version: Literal["ashare_pit_validation_policy.v1"] = (
        "ashare_pit_validation_policy.v1"
    )

    def __post_init__(self) -> None:
        if self.schema_version != "ashare_pit_validation_policy.v1":
            raise ValueError("unsupported A-share PIT validation policy")
        if self.required_fields != tuple(sorted(set(self.required_fields))):
            raise ValueError("PIT required fields must be sorted and unique")
        if self.required_trade_states != tuple(
            sorted(set(self.required_trade_states))
        ):
            raise ValueError("PIT trade states must be sorted and unique")
        if (
            isinstance(self.minimum_listing_age_days, bool)
            or not isinstance(self.minimum_listing_age_days, int)
            or self.minimum_listing_age_days < 1
        ):
            raise ValueError("PIT minimum listing age must be positive")
        if not math.isfinite(self.minimum_amount) or self.minimum_amount < 0.0:
            raise ValueError("PIT minimum amount must be finite and nonnegative")
        try:
            pd.Timestamp("2000-01-01T" + self.signal_available_by_local_time)
        except ValueError as exc:
            raise ValueError("PIT signal availability time is invalid") from exc
        if not isinstance(self.exclude_st_from_buys, bool):
            raise ValueError("PIT ST policy must be boolean")

    @property
    def policy_hash(self) -> str:
        return canonical_json_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "required_fields": list(self.required_fields),
            "required_trade_states": list(self.required_trade_states),
            "minimum_listing_age_days": self.minimum_listing_age_days,
            "minimum_amount": self.minimum_amount,
            "signal_available_by_local_time": self.signal_available_by_local_time,
            "exclude_st_from_buys": self.exclude_st_from_buys,
            "sell_membership_rule": "membership_not_required_for_liquidation.v1",
            "buy_membership_rule": "daily_membership_required.v1",
            "limit_direction_rule": "limit_up_blocks_buy_limit_down_blocks_sell.v1",
        }


@dataclass(frozen=True)
class ValidatedAsharePITSourceV2:
    derived_masks: Mapping[str, pd.DataFrame]
    evidence: Mapping[str, Any]
    validation_policy_hash: str

    def __post_init__(self) -> None:
        masks = {
            str(name): frame.copy(deep=True)
            for name, frame in self.derived_masks.items()
        }
        if tuple(masks) != tuple(sorted(masks)) or set(masks) != {
            "can_buy",
            "can_observe",
            "can_sell",
            "eligible_universe",
        }:
            raise ValueError("validated PIT derived mask set is invalid")
        for frame in masks.values():
            if any(dtype != np.dtype(bool) for dtype in frame.dtypes):
                raise ValueError("validated PIT masks must use strict bool dtype")
        object.__setattr__(self, "derived_masks", MappingProxyType(masks))
        object.__setattr__(self, "evidence", _freeze(self.evidence))


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


def _canonical_date_index(frame: pd.DataFrame) -> tuple[str, ...]:
    index = pd.DatetimeIndex(pd.to_datetime(frame.index))
    if index.tz is not None:
        raise ValueError("PIT frame date index must be timezone-naive")
    return tuple(pd.Timestamp(item).date().isoformat() for item in index)


def _exact_axes(
    frame: pd.DataFrame,
    *,
    expected_dates: tuple[str, ...],
    expected_symbols: tuple[str, ...],
) -> bool:
    try:
        dates = _canonical_date_index(frame)
    except (TypeError, ValueError):
        return False
    return dates == expected_dates and tuple(str(item) for item in frame.columns) == (
        expected_symbols
    )


def _false_masks(
    dates: tuple[str, ...],
    symbols: tuple[str, ...],
) -> dict[str, pd.DataFrame]:
    index = pd.DatetimeIndex(dates, name="date")
    return {
        name: pd.DataFrame(False, index=index, columns=symbols, dtype=bool)
        for name in ("can_buy", "can_observe", "can_sell", "eligible_universe")
    }


def _signal_cutoff(date_value: str, local_time: str) -> pd.Timestamp:
    return pd.Timestamp(
        f"{date_value}T{local_time}",
        tz=ZoneInfo("Asia/Shanghai"),
    )


def validate_ashare_pit_source_v2(
    *,
    bundle: AsharePITSourceBundleV1,
    request: AsharePITSnapshotRequestV1,
    registration: RegisteredAsharePITAdapterV1,
    policy: AsharePITValidationPolicyV1 | None = None,
) -> ValidatedAsharePITSourceV2:
    policy = policy or AsharePITValidationPolicyV1()
    hard_failures: set[str] = set()
    caps: set[str] = set()
    warnings: set[str] = set()
    contaminated = False

    if (
        bundle.source_manifest.adapter_id != request.adapter_id
        or bundle.source_manifest.request_hash != request.request_hash
        or registration.descriptor.adapter_id != request.adapter_id
    ):
        hard_failures.add("ADAPTER_REQUEST_SOURCE_IDENTITY_MISMATCH")
        contaminated = True
    if registration.authority_class != "built_in_production":
        caps.add("ADAPTER_AUTHORITY_UNVERIFIED")

    expected_dates = request.calendar_dates
    if bundle.calendar_dates != expected_dates:
        hard_failures.add("CALENDAR_SCOPE_MISMATCH")
        if any(date > request.valid_cutoff for date in bundle.calendar_dates):
            hard_failures.add("SNAPSHOT_SCOPE_CUTOFF_VIOLATION")
            contaminated = True
    missing_fields = sorted(set(policy.required_fields) - set(bundle.market_fields))
    missing_availability = sorted(
        set(policy.required_fields) - set(bundle.field_available_at)
    )
    missing_trade_states = sorted(
        set(policy.required_trade_states) - set(bundle.trade_state_fields)
    )
    if missing_fields:
        caps.add("REQUIRED_MARKET_FIELDS_UNAVAILABLE")
    if missing_availability:
        caps.add("FIELD_AVAILABILITY_EVIDENCE_UNAVAILABLE")
    if missing_trade_states:
        caps.add("TRADE_STATE_EVIDENCE_UNAVAILABLE")

    membership_symbols = tuple(str(item) for item in bundle.daily_membership.columns)
    if membership_symbols != tuple(sorted(set(membership_symbols))) or not membership_symbols:
        hard_failures.add("MEMBERSHIP_SYMBOL_AXIS_INVALID")
        membership_symbols = tuple(sorted(set(membership_symbols)))
    masks = _false_masks(expected_dates, membership_symbols)
    axes_ok = _exact_axes(
        bundle.daily_membership,
        expected_dates=expected_dates,
        expected_symbols=membership_symbols,
    )
    if not axes_ok:
        hard_failures.add("DAILY_MEMBERSHIP_AXES_MISMATCH")
    elif not bool(bundle.daily_membership.any(axis=1).all()):
        caps.add("DAILY_MEMBERSHIP_GAP")

    required_frames: list[tuple[str, pd.DataFrame]] = []
    for name in policy.required_fields:
        if name in bundle.market_fields:
            required_frames.append((f"market:{name}", bundle.market_fields[name]))
        if name in bundle.field_available_at:
            required_frames.append(
                (f"availability:{name}", bundle.field_available_at[name])
            )
    for name in policy.required_trade_states:
        if name in bundle.trade_state_fields:
            required_frames.append((f"trade:{name}", bundle.trade_state_fields[name]))
    for name, frame in required_frames:
        if not _exact_axes(
            frame,
            expected_dates=expected_dates,
            expected_symbols=membership_symbols,
        ):
            hard_failures.add("FRAME_AXES_MISMATCH:" + name)
    if hard_failures or missing_fields or missing_availability or missing_trade_states:
        evidence = _evidence(
            registration=registration,
            policy=policy,
            bundle=bundle,
            request=request,
            hard_failures=hard_failures,
            caps=caps,
            warnings=warnings,
            contaminated=contaminated,
            membership_symbols=membership_symbols,
            masks=masks,
        )
        return ValidatedAsharePITSourceV2(
            derived_masks=dict(sorted(masks.items())),
            evidence=evidence,
            validation_policy_hash=policy.policy_hash,
        )

    index = pd.DatetimeIndex(expected_dates, name="date")
    membership = bundle.daily_membership.copy(deep=True)
    membership.index = index
    availability_complete = pd.DataFrame(
        True,
        index=index,
        columns=membership_symbols,
        dtype=bool,
    )
    observable_values = pd.DataFrame(
        True,
        index=index,
        columns=membership_symbols,
        dtype=bool,
    )
    for field in policy.required_fields:
        values = bundle.market_fields[field].copy(deep=True)
        values.index = index
        available = bundle.field_available_at[field].copy(deep=True)
        available.index = index
        finite = pd.DataFrame(
            np.isfinite(values.to_numpy(dtype=float)),
            index=index,
            columns=membership_symbols,
        )
        observable_values &= finite
        for row_index, date_value in enumerate(expected_dates):
            cutoff = _signal_cutoff(
                date_value,
                policy.signal_available_by_local_time,
            )
            for column_index, symbol in enumerate(membership_symbols):
                raw = available.iat[row_index, column_index]
                if not finite.iat[row_index, column_index]:
                    continue
                if raw is None or pd.isna(raw):
                    availability_complete.iat[row_index, column_index] = False
                    continue
                try:
                    timestamp = pd.Timestamp(raw)
                except (TypeError, ValueError):
                    availability_complete.iat[row_index, column_index] = False
                    continue
                if timestamp.tzinfo is None:
                    availability_complete.iat[row_index, column_index] = False
                    continue
                if timestamp.tz_convert("Asia/Shanghai") > cutoff:
                    hard_failures.add("POST_SIGNAL_FIELD_AVAILABILITY")
                    contaminated = True
                    availability_complete.iat[row_index, column_index] = False
    if not bool(availability_complete.where(observable_values, True).all().all()):
        caps.add("FIELD_AVAILABILITY_EVIDENCE_INCOMPLETE")

    security = bundle.security_master
    required_security_columns = {
        "listing_date",
        "delisting_date",
        "record_available_at",
    }
    if set(security.columns) != required_security_columns:
        caps.add("SECURITY_MASTER_SCHEMA_UNAVAILABLE")
    else:
        members = set(
            str(symbol)
            for symbol in membership.columns[membership.any(axis=0)]
        )
        if not members.issubset(set(str(item) for item in security.index)):
            caps.add("SECURITY_MASTER_MEMBER_GAP")
        else:
            for symbol in sorted(members):
                row = security.loc[symbol]
                first_member = expected_dates[
                    int(np.flatnonzero(membership[symbol].to_numpy())[0])
                ]
                listing = pd.Timestamp(row["listing_date"]).date().isoformat()
                delisting_raw = row["delisting_date"]
                delisting = (
                    None
                    if delisting_raw is None or pd.isna(delisting_raw)
                    else pd.Timestamp(delisting_raw).date().isoformat()
                )
                record_available = pd.Timestamp(row["record_available_at"])
                if record_available.tzinfo is None:
                    caps.add("SECURITY_MASTER_AVAILABILITY_UNVERIFIED")
                elif record_available.tz_convert("Asia/Shanghai") > _signal_cutoff(
                    first_member,
                    policy.signal_available_by_local_time,
                ):
                    hard_failures.add("POST_SIGNAL_SECURITY_MASTER_AVAILABILITY")
                    contaminated = True
                if listing > first_member:
                    hard_failures.add("MEMBERSHIP_PRECEDES_LISTING")
                    contaminated = True
                if delisting is not None and any(
                    date > delisting and bool(membership.loc[pd.Timestamp(date), symbol])
                    for date in expected_dates
                ):
                    hard_failures.add("MEMBERSHIP_AFTER_DELISTING")
                    contaminated = True

    actions = bundle.corporate_actions
    required_action_columns = {
        "symbol",
        "effective_date",
        "announced_at",
        "factor",
    }
    if set(actions.columns) != required_action_columns:
        caps.add("CORPORATE_ACTION_SCHEMA_UNAVAILABLE")
    else:
        for _, row in actions.iterrows():
            effective = pd.Timestamp(row["effective_date"]).date().isoformat()
            announced = pd.Timestamp(row["announced_at"])
            factor = float(row["factor"])
            if effective > request.valid_cutoff:
                hard_failures.add("CORPORATE_ACTION_AFTER_VALID_CUTOFF")
                contaminated = True
            if announced.tzinfo is None or not math.isfinite(factor) or factor <= 0.0:
                caps.add("CORPORATE_ACTION_RECORD_INVALID")
            elif announced.tz_convert("Asia/Shanghai") > _signal_cutoff(
                effective,
                policy.signal_available_by_local_time,
            ):
                hard_failures.add("POST_EFFECTIVE_CORPORATE_ACTION_ANNOUNCEMENT")
                contaminated = True

    state = {
        name: frame.copy(deep=True)
        for name, frame in bundle.trade_state_fields.items()
    }
    for name, frame in state.items():
        frame.index = index
    amount = bundle.market_fields["amount"].copy(deep=True)
    amount.index = index
    can_observe = observable_values & availability_complete
    listing_age_ok = state["listing_age_days"].astype(float) >= float(
        policy.minimum_listing_age_days
    )
    can_buy = (
        can_observe
        & membership
        & ~state["is_suspended"].astype(bool)
        & ~state["at_limit_up"].astype(bool)
        & listing_age_ok
        & amount.astype(float).gt(policy.minimum_amount)
    )
    if policy.exclude_st_from_buys:
        can_buy &= ~state["is_st"].astype(bool)
    can_sell = (
        can_observe
        & ~state["is_suspended"].astype(bool)
        & ~state["at_limit_down"].astype(bool)
    )
    masks = {
        "can_buy": can_buy.astype(bool),
        "can_observe": can_observe.astype(bool),
        "can_sell": can_sell.astype(bool),
        "eligible_universe": membership.astype(bool),
    }
    evidence = _evidence(
        registration=registration,
        policy=policy,
        bundle=bundle,
        request=request,
        hard_failures=hard_failures,
        caps=caps,
        warnings=warnings,
        contaminated=contaminated,
        membership_symbols=membership_symbols,
        masks=masks,
    )
    return ValidatedAsharePITSourceV2(
        derived_masks=dict(sorted(masks.items())),
        evidence=evidence,
        validation_policy_hash=policy.policy_hash,
    )


def _evidence(
    *,
    registration: RegisteredAsharePITAdapterV1,
    policy: AsharePITValidationPolicyV1,
    bundle: AsharePITSourceBundleV1,
    request: AsharePITSnapshotRequestV1,
    hard_failures: set[str],
    caps: set[str],
    warnings: set[str],
    contaminated: bool,
    membership_symbols: tuple[str, ...],
    masks: Mapping[str, pd.DataFrame],
) -> dict[str, Any]:
    incomplete = bool(caps or hard_failures)
    pit_status = "contaminated" if contaminated else (
        "unavailable" if incomplete else "complete"
    )
    decision_grade = (
        pit_status == "complete"
        and registration.authority_class == "built_in_production"
    )
    return {
        "schema_version": "ashare_pit_derived_evidence.v2",
        "validation_policy_hash": policy.policy_hash,
        "adapter_authority_class": registration.authority_class,
        "pit_contract_status": pit_status,
        "cutoff_status": (
            "contaminated" if "SNAPSHOT_SCOPE_CUTOFF_VIOLATION" in hard_failures
            else "within_registered_valid_end"
        ),
        "calendar_status": (
            "exact" if bundle.calendar_dates == request.calendar_dates else "mismatch"
        ),
        "membership_status": (
            "complete" if not any(
                "MEMBERSHIP" in item for item in hard_failures | caps
            ) else "unavailable"
        ),
        "availability_status": (
            "complete" if not any(
                "AVAILABILITY" in item for item in hard_failures | caps
            ) else ("contaminated" if contaminated else "unavailable")
        ),
        "security_master_status": (
            "complete" if not any(
                "SECURITY_MASTER" in item or "LISTING" in item or "DELISTING" in item
                for item in hard_failures | caps
            ) else ("contaminated" if contaminated else "unavailable")
        ),
        "corporate_action_status": (
            "complete" if not any(
                "CORPORATE_ACTION" in item for item in hard_failures | caps
            ) else ("contaminated" if contaminated else "unavailable")
        ),
        "trade_state_status": (
            "complete" if not any(
                "TRADE_STATE" in item for item in hard_failures | caps
            ) else "unavailable"
        ),
        "survivorship_status": (
            "controlled_by_daily_membership"
            if not any("MEMBERSHIP" in item for item in hard_failures | caps)
            else "unknown"
        ),
        "adjustment_status": "raw_prices_plus_dated_actions",
        "date_start": request.calendar_dates[0],
        "date_end": request.calendar_dates[-1],
        "date_count": len(request.calendar_dates),
        "symbol_count": len(membership_symbols),
        "member_observation_count": int(bundle.daily_membership.to_numpy().sum())
        if bundle.daily_membership.shape
        == (len(request.calendar_dates), len(membership_symbols))
        else 0,
        "can_observe_count": int(masks["can_observe"].to_numpy().sum()),
        "can_buy_count": int(masks["can_buy"].to_numpy().sum()),
        "can_sell_count": int(masks["can_sell"].to_numpy().sum()),
        "decision_grade": decision_grade,
        "hard_failures": sorted(hard_failures),
        "caps": sorted(caps),
        "warnings": sorted(warnings),
    }


__all__ = [
    "ASHARE_PIT_REQUIRED_FIELDS",
    "ASHARE_PIT_REQUIRED_TRADE_STATES",
    "AsharePITValidationPolicyV1",
    "ValidatedAsharePITSourceV2",
    "validate_ashare_pit_source_v2",
]
