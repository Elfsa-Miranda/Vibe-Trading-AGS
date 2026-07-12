"""Built-in Tushare adapter for dynamic CSI300 point-in-time source facts."""

from __future__ import annotations

import importlib.metadata
import inspect
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, cast
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from src.alpha_quality.pit_adapter_v1 import (
    AsharePITAdapterDescriptorV1,
    AsharePITSnapshotRequestV1,
    AsharePITSourceBundleV1,
    AsharePITSourceManifestV1,
)
from src.research_ledger.hash_utils import canonical_json_hash


_PRODUCTION_AUTHORITY_CAPABILITY = object()


class AsharePITSourceUnavailable(RuntimeError):
    """Raised when provider facts cannot satisfy the closed PIT contract."""


def is_production_bound_adapter(adapter: object) -> bool:
    return (
        type(adapter) is TushareCSI300PITAdapterV1
        and adapter._authority_capability is _PRODUCTION_AUTHORITY_CAPABILITY
        and adapter._factory_attestation is not None
    )


def production_factory_attestation(adapter: object) -> Mapping[str, Any] | None:
    """Return attestation only for the exact controlled factory product."""
    if not is_production_bound_adapter(adapter):
        return None
    assert isinstance(adapter, TushareCSI300PITAdapterV1)
    try:
        import tushare as ts  # type: ignore[import-untyped]
    except ImportError:
        return None
    current = _installed_factory_attestation(ts, adapter._client)
    if current is None or dict(adapter._factory_attestation or {}) != current:
        return None
    return MappingProxyType(current)


def _installed_factory_attestation(ts: Any, client: Any) -> dict[str, Any] | None:
    """Reject monkeypatched factories and clients even when names are imitated."""
    factory_raw = getattr(ts, "pro_api", None)
    if not callable(factory_raw):
        return None
    factory = cast(Callable[..., Any], factory_raw)
    client_type = type(client)
    if (
        getattr(factory, "__module__", None) != "tushare.pro.data_pro"
        or getattr(factory, "__qualname__", None) != "pro_api"
        or client_type.__module__ != "tushare.pro.client"
        or client_type.__qualname__ != "DataApi"
    ):
        return None
    try:
        distribution = importlib.metadata.distribution("tushare")
        package_root = (
            Path(str(distribution.locate_file(""))) / "tushare"
        ).resolve(strict=True)
        factory_path = Path(inspect.getsourcefile(factory) or "").resolve(strict=True)
        client_path = Path(inspect.getsourcefile(client_type) or "").resolve(strict=True)
        if not factory_path.is_relative_to(package_root) or not client_path.is_relative_to(
            package_root
        ):
            return None
        provider_version = distribution.version
        if str(getattr(ts, "__version__", "")) != provider_version:
            return None
        content = {
            "schema_version": "tushare_factory_attestation.v1",
            "factory_origin": "installed_tushare_distribution",
            "provider": "tushare",
            "provider_version": provider_version,
            "factory_module": factory.__module__,
            "factory_qualname": factory.__qualname__,
            "factory_source_hash": canonical_json_hash(
                {"source": inspect.getsource(factory).replace("\r\n", "\n")}
            ),
            "client_type_module": client_type.__module__,
            "client_type_qualname": client_type.__qualname__,
            "client_source_hash": canonical_json_hash(
                {"source": inspect.getsource(client_type).replace("\r\n", "\n")}
            ),
        }
    except (ImportError, OSError, TypeError, ValueError):
        return None
    return {**content, "factory_hash": canonical_json_hash(content)}


def _frame_hash(frame: pd.DataFrame, name: str) -> str:
    normalized = frame.copy(deep=True)
    normalized.columns = [str(item) for item in normalized.columns]
    normalized = normalized.sort_index().sort_index(axis=1)
    return canonical_json_hash(
        {
            "schema_version": "tushare_source_frame.v1",
            "name": name,
            "columns": list(normalized.columns),
            "rows": [
                {
                    "index": str(index),
                    "values": [
                        None if pd.isna(value) else str(value)
                        for value in row.tolist()
                    ],
                }
                for index, row in normalized.iterrows()
            ],
        }
    )


def _required_frame(value: Any, name: str) -> pd.DataFrame:
    if not isinstance(value, pd.DataFrame) or value.empty:
        raise AsharePITSourceUnavailable(f"Tushare {name} is unavailable")
    return value.copy(deep=True)


def _date(value: Any) -> str:
    return pd.Timestamp(str(value)).date().isoformat()


class TushareCSI300PITAdapterV1:
    """Fetch raw provider facts; all PIT conclusions are derived downstream."""

    def __init__(
        self,
        client: Any,
        *,
        dataset_vintage: str,
        source_as_of: str,
        _authority_capability: object | None = None,
        _factory_attestation: Mapping[str, Any] | None = None,
    ) -> None:
        if client is None:
            raise ValueError("Tushare PIT adapter requires a provider client")
        timestamp = pd.Timestamp(source_as_of)
        if timestamp.tzinfo is None:
            raise ValueError("Tushare source_as_of must include timezone")
        self._client = client
        self._dataset_vintage = str(dataset_vintage)
        self._source_as_of = timestamp.isoformat()
        self._authority_capability = _authority_capability
        self._factory_attestation = (
            None
            if _factory_attestation is None
            else MappingProxyType(dict(_factory_attestation))
        )

    @classmethod
    def from_environment(cls) -> "TushareCSI300PITAdapterV1":
        token = os.getenv("TUSHARE_TOKEN", "").strip()
        if not token or token == "your-tushare-token":
            raise AsharePITSourceUnavailable("TUSHARE_TOKEN is unavailable")
        try:
            import tushare as ts  # type: ignore[import-untyped]
        except ImportError as exc:
            raise AsharePITSourceUnavailable("tushare package is unavailable") from exc
        now = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai"))
        client = ts.pro_api(token)
        attestation = _installed_factory_attestation(ts, client)
        return cls(
            client,
            dataset_vintage=f"tushare-{getattr(ts, '__version__', 'unknown')}-{now.date().isoformat()}",
            source_as_of=now.isoformat(),
            _authority_capability=(
                _PRODUCTION_AUTHORITY_CAPABILITY if attestation is not None else None
            ),
            _factory_attestation=attestation,
        )

    @classmethod
    def for_test(
        cls,
        client: Any,
        *,
        dataset_vintage: str = "test-vintage",
        source_as_of: str = "2025-01-01T00:00:00+08:00",
    ) -> "TushareCSI300PITAdapterV1":
        return cls(
            client,
            dataset_vintage=dataset_vintage,
            source_as_of=source_as_of,
        )

    def descriptor(self) -> AsharePITAdapterDescriptorV1:
        return AsharePITAdapterDescriptorV1(
            adapter_id="tushare-csi300-pit-v1",
            provider="tushare-pro",
            adapter_version="1.0.0",
            market="CN_A_SHARE",
            calendar_id="XSHG_XSHE",
            timezone="Asia/Shanghai",
            membership_dataset="index_weight-399300.SZ",
            security_master_dataset="stock_basic-all-list-statuses",
            corporate_action_dataset="adj_factor-plus-dividend",
            trade_state_dataset="suspend_d-stk_limit-namechange",
            price_dataset="daily-raw-ohlcv-amount",
            availability_semantics="provider_release_timestamp.v1",
            adjustment_semantics="raw_prices_plus_dated_actions.v1",
        )

    def load(self, request: AsharePITSnapshotRequestV1) -> AsharePITSourceBundleV1:
        if request.adapter_id != self.descriptor().adapter_id:
            raise AsharePITSourceUnavailable("Tushare request targets another adapter")
        dates = request.calendar_dates
        start = dates[0]
        end = dates[-1]
        compact_start = start.replace("-", "")
        compact_end = end.replace("-", "")
        receipts: list[str] = []

        trade_calendar_raw = _required_frame(
            self._client.trade_cal(
                exchange="",
                start_date=compact_start,
                end_date=compact_end,
                is_open="1",
                fields="cal_date,is_open",
            ),
            "trade_cal",
        )
        provider_dates = tuple(
            sorted(_date(value) for value in trade_calendar_raw["cal_date"].tolist())
        )
        if provider_dates != dates:
            raise AsharePITSourceUnavailable(
                "registered calendar differs from Tushare open dates"
            )
        receipts.append(_frame_hash(trade_calendar_raw, "trade_cal"))

        lookback = (
            pd.Timestamp(start).date() - timedelta(days=400)
        ).strftime("%Y%m%d")
        weights_raw = _required_frame(
            self._client.index_weight(
                index_code="399300.SZ",
                start_date=lookback,
                end_date=compact_end,
                fields="index_code,con_code,trade_date,weight",
            ),
            "index_weight",
        )
        for column in ("con_code", "trade_date"):
            if column not in weights_raw:
                raise AsharePITSourceUnavailable("index_weight schema is incomplete")
        weights_raw["trade_date"] = weights_raw["trade_date"].map(_date)
        weights_raw = weights_raw[weights_raw["trade_date"] <= end]
        snapshot_dates = tuple(sorted(weights_raw["trade_date"].unique()))
        if not snapshot_dates or snapshot_dates[0] > start:
            raise AsharePITSourceUnavailable(
                "CSI300 membership has no snapshot at or before train start"
            )
        symbols = tuple(sorted(str(item) for item in weights_raw["con_code"].unique()))
        membership = pd.DataFrame(
            False,
            index=pd.DatetimeIndex(dates),
            columns=symbols,
            dtype=bool,
        )
        for date_value in dates:
            eligible = [item for item in snapshot_dates if item <= date_value]
            if not eligible:
                raise AsharePITSourceUnavailable("daily membership cannot be resolved")
            active_date = eligible[-1]
            active = set(
                str(item)
                for item in weights_raw.loc[
                    weights_raw["trade_date"] == active_date,
                    "con_code",
                ]
            )
            membership.loc[pd.Timestamp(date_value), list(active)] = True
        if not bool(membership.any(axis=1).all()):
            raise AsharePITSourceUnavailable("daily CSI300 membership contains a gap")
        receipts.append(_frame_hash(weights_raw, "index_weight"))

        security_parts: list[pd.DataFrame] = []
        for status in ("L", "D", "P"):
            value = self._client.stock_basic(
                exchange="",
                list_status=status,
                fields="ts_code,name,list_date,delist_date,list_status",
            )
            if isinstance(value, pd.DataFrame) and not value.empty:
                security_parts.append(value.copy(deep=True))
        if not security_parts:
            raise AsharePITSourceUnavailable("stock_basic is unavailable")
        security_raw = pd.concat(security_parts, ignore_index=True).drop_duplicates(
            subset=["ts_code"],
            keep="last",
        )
        security_raw = security_raw[security_raw["ts_code"].isin(symbols)]
        if set(security_raw["ts_code"]) != set(symbols):
            raise AsharePITSourceUnavailable("security master misses a historic member")
        receipts.append(_frame_hash(security_raw, "stock_basic"))
        security = pd.DataFrame(
            {
                "listing_date": security_raw.set_index("ts_code")["list_date"].map(_date),
                "delisting_date": security_raw.set_index("ts_code")["delist_date"].map(
                    lambda value: None if pd.isna(value) or not str(value) else _date(value)
                ),
                "record_available_at": security_raw.set_index("ts_code")["list_date"].map(
                    lambda value: _date(value) + "T00:00:00+08:00"
                ),
            }
        ).reindex(symbols)
        security.index.name = "symbol"

        daily_by_symbol: dict[str, pd.DataFrame] = {}
        limits_by_symbol: dict[str, pd.DataFrame] = {}
        suspensions: dict[str, set[str]] = {}
        name_changes: dict[str, pd.DataFrame] = {}
        action_rows: list[dict[str, Any]] = []
        for symbol in symbols:
            daily = self._client.daily(
                ts_code=symbol,
                start_date=compact_start,
                end_date=compact_end,
                fields="ts_code,trade_date,open,high,low,close,vol,amount",
            )
            if not isinstance(daily, pd.DataFrame):
                raise AsharePITSourceUnavailable(f"daily query failed for {symbol}")
            daily = daily.copy(deep=True)
            if not daily.empty:
                daily["trade_date"] = daily["trade_date"].map(_date)
                daily = daily.set_index("trade_date").sort_index()
            daily_by_symbol[symbol] = daily
            receipts.append(_frame_hash(daily, f"daily:{symbol}"))

            limits = self._client.stk_limit(
                ts_code=symbol,
                start_date=compact_start,
                end_date=compact_end,
                fields="ts_code,trade_date,up_limit,down_limit",
            )
            if not isinstance(limits, pd.DataFrame):
                raise AsharePITSourceUnavailable(f"stk_limit query failed for {symbol}")
            limits = limits.copy(deep=True)
            if not limits.empty:
                limits["trade_date"] = limits["trade_date"].map(_date)
                limits = limits.set_index("trade_date").sort_index()
            limits_by_symbol[symbol] = limits
            receipts.append(_frame_hash(limits, f"stk_limit:{symbol}"))

            suspend = self._client.suspend_d(
                ts_code=symbol,
                start_date=compact_start,
                end_date=compact_end,
                fields="ts_code,trade_date,suspend_type",
            )
            if not isinstance(suspend, pd.DataFrame):
                raise AsharePITSourceUnavailable(f"suspend_d query failed for {symbol}")
            suspensions[symbol] = (
                set(_date(item) for item in suspend["trade_date"].tolist())
                if not suspend.empty
                else set()
            )
            receipts.append(_frame_hash(suspend, f"suspend_d:{symbol}"))

            changes = self._client.namechange(
                ts_code=symbol,
                fields="ts_code,name,start_date,end_date,ann_date,change_reason",
            )
            if not isinstance(changes, pd.DataFrame):
                raise AsharePITSourceUnavailable(f"namechange query failed for {symbol}")
            name_changes[symbol] = changes.copy(deep=True)
            receipts.append(_frame_hash(changes, f"namechange:{symbol}"))

            adj = self._client.adj_factor(
                ts_code=symbol,
                start_date=lookback,
                end_date=compact_end,
                fields="ts_code,trade_date,adj_factor",
            )
            dividends = self._client.dividend(
                ts_code=symbol,
                fields="ts_code,ann_date,ex_date,div_proc",
            )
            if not isinstance(adj, pd.DataFrame) or not isinstance(
                dividends,
                pd.DataFrame,
            ):
                raise AsharePITSourceUnavailable(
                    f"corporate-action queries failed for {symbol}"
                )
            receipts.extend(
                (
                    _frame_hash(adj, f"adj_factor:{symbol}"),
                    _frame_hash(dividends, f"dividend:{symbol}"),
                )
            )
            if not adj.empty:
                adj = adj.copy(deep=True)
                adj["trade_date"] = adj["trade_date"].map(_date)
                adj = adj.sort_values("trade_date")
                adj["previous"] = pd.to_numeric(adj["adj_factor"], errors="coerce").shift(1)
                adj["factor"] = pd.to_numeric(adj["adj_factor"], errors="coerce") / adj["previous"]
                changes_adj = adj[
                    adj["previous"].notna()
                    & adj["factor"].notna()
                    & ~np.isclose(adj["factor"], 1.0)
                    & adj["trade_date"].ge(start)
                ]
                dividends = dividends.copy(deep=True)
                if not dividends.empty:
                    dividends["ex_date"] = dividends["ex_date"].map(
                        lambda value: None if pd.isna(value) else _date(value)
                    )
                for _, action in changes_adj.iterrows():
                    effective = str(action["trade_date"])
                    matches = dividends[dividends["ex_date"] == effective]
                    if matches.empty or matches["ann_date"].isna().all():
                        raise AsharePITSourceUnavailable(
                            f"adjustment change lacks dated announcement for {symbol}"
                        )
                    announced = min(_date(value) for value in matches["ann_date"].dropna())
                    action_rows.append(
                        {
                            "action_id": canonical_json_hash(
                                {
                                    "symbol": symbol,
                                    "effective": effective,
                                    "factor": float(action["factor"]),
                                }
                            ).removeprefix("sha256:")[:32],
                            "symbol": symbol,
                            "effective_date": effective,
                            "announced_at": announced + "T18:00:00+08:00",
                            "factor": float(action["factor"]),
                        }
                    )

        index = pd.DatetimeIndex(dates)
        fields = {
            name: pd.DataFrame(np.nan, index=index, columns=symbols, dtype=float)
            for name in ("amount", "close", "high", "low", "open", "volume")
        }
        limit_up = pd.DataFrame(np.nan, index=index, columns=symbols, dtype=float)
        limit_down = pd.DataFrame(np.nan, index=index, columns=symbols, dtype=float)
        for symbol in symbols:
            daily = daily_by_symbol[symbol]
            rename = {"vol": "volume"}
            for field in fields:
                source_name = next((key for key, value in rename.items() if value == field), field)
                if source_name in daily:
                    series = pd.to_numeric(daily[source_name], errors="coerce")
                    series.index = pd.DatetimeIndex(series.index)
                    fields[field].loc[series.index, symbol] = series
            limits = limits_by_symbol[symbol]
            for source_name, target in (
                ("up_limit", limit_up),
                ("down_limit", limit_down),
            ):
                if source_name in limits:
                    series = pd.to_numeric(limits[source_name], errors="coerce")
                    series.index = pd.DatetimeIndex(series.index)
                    target.loc[series.index, symbol] = series

        availability = {
            field: pd.DataFrame(
                [
                    [
                        None if pd.isna(fields[field].loc[pd.Timestamp(day), symbol])
                        else day + "T15:00:00+08:00"
                        for symbol in symbols
                    ]
                    for day in dates
                ],
                index=index,
                columns=symbols,
            )
            for field in fields
        }
        is_suspended = pd.DataFrame(
            [
                [day in suspensions[symbol] for symbol in symbols]
                for day in dates
            ],
            index=index,
            columns=symbols,
            dtype=bool,
        )
        member_without_price = membership & fields["close"].isna() & ~is_suspended
        if bool(member_without_price.any().any()):
            raise AsharePITSourceUnavailable(
                "non-suspended member is missing daily price evidence"
            )
        member_without_limits = (
            membership
            & fields["close"].notna()
            & ~is_suspended
            & (limit_up.isna() | limit_down.isna())
        )
        if bool(member_without_limits.any().any()):
            raise AsharePITSourceUnavailable(
                "tradable member is missing directional limit evidence"
            )
        is_st = pd.DataFrame(False, index=index, columns=symbols, dtype=bool)
        for symbol, changes in name_changes.items():
            for _, row in changes.iterrows():
                name = str(row.get("name", ""))
                if "ST" not in name.upper():
                    continue
                start_date = _date(row["start_date"])
                end_raw = row.get("end_date")
                end_date = end if pd.isna(end_raw) or not str(end_raw) else _date(end_raw)
                for day in dates:
                    if start_date <= day <= end_date:
                        is_st.loc[pd.Timestamp(day), symbol] = True
        listing_age = pd.DataFrame(0.0, index=index, columns=symbols)
        for symbol in symbols:
            listed = pd.Timestamp(security.loc[symbol, "listing_date"])
            listing_age[symbol] = [
                float((pd.Timestamp(day) - listed).days) for day in dates
            ]
        trade_states = {
            "at_limit_down": (fields["close"] <= limit_down).fillna(False).astype(bool),
            "at_limit_up": (fields["close"] >= limit_up).fillna(False).astype(bool),
            "is_st": is_st,
            "is_suspended": is_suspended,
            "listing_age_days": listing_age,
        }
        actions = pd.DataFrame(action_rows)
        if actions.empty:
            actions = pd.DataFrame(
                columns=["action_id", "symbol", "effective_date", "announced_at", "factor"]
            )
        actions = actions.set_index("action_id")
        actions.index.name = "action_id"
        receipt_hashes = tuple(sorted(set(receipts)))
        return AsharePITSourceBundleV1(
            market_fields=dict(sorted(fields.items())),
            field_available_at=dict(sorted(availability.items())),
            trade_state_fields=dict(sorted(trade_states.items())),
            daily_membership=membership,
            security_master=security,
            corporate_actions=actions,
            calendar_dates=dates,
            source_manifest=AsharePITSourceManifestV1(
                adapter_id=request.adapter_id,
                request_hash=request.request_hash,
                dataset_vintage=self._dataset_vintage,
                source_as_of=self._source_as_of,
                query_receipt_hashes=receipt_hashes,
                source_partition_hashes=receipt_hashes,
            ),
        )


__all__ = [
    "AsharePITSourceUnavailable",
    "TushareCSI300PITAdapterV1",
    "is_production_bound_adapter",
    "production_factory_attestation",
]
