"""BaoStock-backed source-only adapter for the A-share golden-cohort cycle.

This adapter deliberately records provider failures as failures.  BaoStock's
``ResultData`` objects use ``error_code``/``error_msg`` instead of exceptions;
turning a non-zero code into an empty frame would make a failed fetch look like
an eligible-universe observation.
"""

from __future__ import annotations

import importlib.metadata
import inspect
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from src.alpha_quality.pit_adapter_v1 import (
    AsharePITAdapterDescriptorV1,
    AsharePITSnapshotRequestV1,
    AsharePITSourceBundleV1,
    AsharePITSourceManifestV1,
)
from src.research_ledger.events.artifacts import AtomicContentAddressedArtifactWriter
from src.research_ledger.hash_utils import canonical_json_hash


_PRODUCTION_AUTHORITY_CAPABILITY = object()


class BaoStockSourceUnavailable(RuntimeError):
    """A sanitized typed failure from BaoStock or its local SDK boundary."""

    def __init__(
        self,
        *,
        interface: str,
        error_code: str,
        message: str,
        receipt_hash: str | None = None,
    ) -> None:
        self.interface = interface
        self.error_code = error_code
        self.message = message
        self.receipt_hash = receipt_hash
        super().__init__(f"BaoStock {interface} failed: {error_code}:{message}")


def _sanitize_message(value: object) -> str:
    text = " ".join(str(value).split()).lower()
    if not text:
        return "provider_message_not_supplied"
    if "permission" in text or "权限" in text:
        return "permission_denied"
    if "limit" in text or "频率" in text:
        return "rate_limited"
    if "timeout" in text or "connect" in text or "network" in text:
        return "transport_error"
    return "provider_error"


def _rows(result: Any, interface: str) -> pd.DataFrame:
    code = str(getattr(result, "error_code", ""))
    message = _sanitize_message(getattr(result, "error_msg", ""))
    if code != "0":
        raise BaoStockSourceUnavailable(interface=interface, error_code=code or "missing", message=message)
    fields = tuple(str(value) for value in getattr(result, "fields", ()) or ())
    data: list[list[Any]] = []
    while bool(result.next()):
        row = result.get_row_data()
        if not isinstance(row, (tuple, list)) or len(row) != len(fields):
            raise BaoStockSourceUnavailable(interface=interface, error_code="schema", message="invalid_row_schema")
        data.append(list(row))
    return pd.DataFrame(data, columns=fields)


def _raw_payload(
    *,
    interface: str,
    parameters: Mapping[str, Any],
    frame: pd.DataFrame,
    retrieved_at: str,
    provider_version: str,
) -> dict[str, Any]:
    normalized_parameters = {str(key): str(value) for key, value in sorted(parameters.items())}
    content = {
        "schema_version": "baostock_raw_partition.v1",
        "interface": interface,
        "provider_version": provider_version,
        "parameters": normalized_parameters,
        "parameters_hash": canonical_json_hash(normalized_parameters),
        "retrieved_at": retrieved_at,
        "error_code": "0",
        "sanitized_message": "success",
        "status": "success",
        "row_count": int(len(frame)),
        "columns": [str(column) for column in frame.columns],
        "rows": [[None if pd.isna(value) else str(value) for value in row] for row in frame.itertuples(index=False, name=None)],
    }
    return {**content, "partition_hash": canonical_json_hash(content)}


def _failure_payload(
    *,
    interface: str,
    parameters: Mapping[str, Any],
    error_code: str,
    message: str,
    retrieved_at: str,
    provider_version: str,
) -> dict[str, Any]:
    normalized_parameters = {str(key): str(value) for key, value in sorted(parameters.items())}
    content = {
        "schema_version": "baostock_raw_partition.v1",
        "interface": interface,
        "provider_version": provider_version,
        "parameters": normalized_parameters,
        "parameters_hash": canonical_json_hash(normalized_parameters),
        "retrieved_at": retrieved_at,
        "error_code": error_code,
        "sanitized_message": message,
        "status": "typed_unavailable",
        "row_count": 0,
        "columns": [],
        "rows": [],
    }
    return {**content, "partition_hash": canonical_json_hash(content)}


@dataclass(frozen=True)
class BaoStockAshareEligibleUniverseAdapterV1:
    """Narrow golden-cohort source adapter; it does not grant PIT authority."""

    client: Any
    dataset_vintage: str
    source_as_of: str
    artifact_writer: AtomicContentAddressedArtifactWriter | None = None
    golden_symbol_count: int = 25
    provider_version: str = "unverified"
    _authority_capability: object | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.golden_symbol_count <= 30:
            raise ValueError("BaoStock golden symbol count must be bounded")

    @classmethod
    def from_environment(
        cls,
        *,
        artifact_writer: AtomicContentAddressedArtifactWriter | None = None,
    ) -> "BaoStockAshareEligibleUniverseAdapterV1":
        try:
            import baostock as bs  # type: ignore[import-untyped]
            version = importlib.metadata.version("baostock")
        except (ImportError, importlib.metadata.PackageNotFoundError) as exc:
            raise BaoStockSourceUnavailable(interface="sdk", error_code="unavailable", message="sdk_unavailable") from exc
        login = bs.login()
        if str(getattr(login, "error_code", "")) != "0":
            raise BaoStockSourceUnavailable(interface="login", error_code=str(getattr(login, "error_code", "missing")), message=_sanitize_message(getattr(login, "error_msg", "")))
        now = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).isoformat()
        return cls(
            client=bs,
            dataset_vintage=f"baostock-{version}-{now[:10]}",
            source_as_of=now,
            artifact_writer=artifact_writer,
            golden_symbol_count=25,
            provider_version=version,
            _authority_capability=_PRODUCTION_AUTHORITY_CAPABILITY,
        )

    def descriptor(self) -> AsharePITAdapterDescriptorV1:
        return AsharePITAdapterDescriptorV1(
            adapter_id="baostock-ashare-eligible-golden-cohort-v1",
            provider="baostock",
            adapter_version="1.0.0",
            market="CN_A_SHARE",
            calendar_id="SSE_SZSE_BAOSTOCK",
            timezone="Asia/Shanghai",
            membership_dataset="a-share-eligible-golden-cohort-v1",
            security_master_dataset="query_all_stock-plus-query_stock_basic",
            corporate_action_dataset="akshare-supplement-required",
            trade_state_dataset="query_history_k_data_plus-tradestatus-isst",
            price_dataset="query_history_k_data_plus-unadjusted-ohlcv-amount",
            availability_semantics="provider_release_timestamp.v1",
            adjustment_semantics="raw_prices_plus_dated_actions.v1",
        )

    def _fetch(self, interface: str, parameters: Mapping[str, Any]) -> tuple[pd.DataFrame, str]:
        try:
            result = getattr(self.client, interface)(**dict(parameters))
        except Exception as exc:
            return self._raise_with_failure_receipt(
                interface=interface,
                parameters=parameters,
                error_code="transport",
                message=_sanitize_message(exc),
                cause=exc,
            )
        try:
            frame = _rows(result, interface)
        except BaoStockSourceUnavailable as exc:
            return self._raise_with_failure_receipt(
                interface=interface,
                parameters=parameters,
                error_code=exc.error_code,
                message=exc.message,
                cause=exc,
            )
        payload = _raw_payload(
            interface=interface,
            parameters=parameters,
            frame=frame,
            retrieved_at=self.source_as_of,
            provider_version=self.provider_version,
        )
        partition_hash = str(payload["partition_hash"])
        if self.artifact_writer is not None:
            self.artifact_writer.write_json(
                namespace="baostock-raw-v1",
                payload=payload,
                schema_version="baostock_raw_partition.v1",
                semantic_hash_field="partition_hash",
                closed_keys=frozenset(payload),
                media_type="application/json",
            )
        return frame, partition_hash

    def _raise_with_failure_receipt(
        self,
        *,
        interface: str,
        parameters: Mapping[str, Any],
        error_code: str,
        message: str,
        cause: Exception,
    ) -> tuple[pd.DataFrame, str]:
        payload = _failure_payload(
            interface=interface,
            parameters=parameters,
            error_code=error_code,
            message=message,
            retrieved_at=self.source_as_of,
            provider_version=self.provider_version,
        )
        receipt_hash = str(payload["partition_hash"])
        if self.artifact_writer is not None:
            self.artifact_writer.write_json(
                namespace="baostock-raw-v1",
                payload=payload,
                schema_version="baostock_raw_partition.v1",
                semantic_hash_field="partition_hash",
                closed_keys=frozenset(payload),
                media_type="application/json",
            )
        raise BaoStockSourceUnavailable(
            interface=interface,
            error_code=error_code,
            message=message,
            receipt_hash=receipt_hash,
        ) from cause

    def load(self, request: AsharePITSnapshotRequestV1) -> AsharePITSourceBundleV1:
        if request.adapter_id != self.descriptor().adapter_id:
            raise BaoStockSourceUnavailable(interface="request", error_code="identity", message="adapter_id_mismatch")
        dates = request.calendar_dates
        start, end = dates[0], dates[-1]
        calendar, calendar_hash = self._fetch("query_trade_dates", {"start_date": start, "end_date": end})
        if not {"calendar_date", "is_trading_day"}.issubset(calendar.columns):
            raise BaoStockSourceUnavailable(interface="query_trade_dates", error_code="schema", message="required_fields_missing")
        actual_dates = tuple(calendar.loc[calendar["is_trading_day"].astype(str) == "1", "calendar_date"].astype(str))
        if actual_dates != dates:
            raise BaoStockSourceUnavailable(interface="query_trade_dates", error_code="scope", message="calendar_mismatch")
        daily_codes: dict[str, set[str]] = {}
        receipts = [calendar_hash]
        for day in dates:
            frame, receipt = self._fetch("query_all_stock", {"day": day})
            if not {"code", "tradeStatus"}.issubset(frame.columns):
                raise BaoStockSourceUnavailable(interface="query_all_stock", error_code="schema", message="required_fields_missing")
            daily_codes[day] = set(frame.loc[frame["tradeStatus"].astype(str) == "1", "code"].astype(str))
            receipts.append(receipt)
        # This is an explicitly bounded verification cohort, frozen from the
        # first historical session rather than today's survivor list.  It is
        # not the full A_SHARE_ELIGIBLE_UNIVERSE_V1.
        symbols = tuple(sorted(daily_codes[dates[0]])[: self.golden_symbol_count])
        if len(symbols) != self.golden_symbol_count:
            raise BaoStockSourceUnavailable(interface="query_all_stock", error_code="scope", message="insufficient_historical_symbols")
        index = pd.DatetimeIndex(dates, name="date")
        market = {name: pd.DataFrame(np.nan, index=index, columns=symbols, dtype=float) for name in ("amount", "close", "high", "low", "open", "volume")}
        suspended = pd.DataFrame(True, index=index, columns=symbols, dtype=bool)
        is_st = pd.DataFrame(True, index=index, columns=symbols, dtype=bool)
        basic_rows: list[dict[str, str | None]] = []
        for symbol in symbols:
            basic, receipt = self._fetch("query_stock_basic", {"code": symbol})
            receipts.append(receipt)
            if basic.empty or not {"ipoDate", "outDate"}.issubset(basic.columns):
                raise BaoStockSourceUnavailable(interface="query_stock_basic", error_code="schema", message="listing_fields_missing")
            basic_rows.append({"symbol": symbol, "listing_date": str(basic.iloc[0]["ipoDate"]), "delisting_date": str(basic.iloc[0]["outDate"]) or None})
            history, receipt = self._fetch("query_history_k_data_plus", {"code": symbol, "fields": "date,code,open,high,low,close,preclose,volume,amount,adjustflag,tradestatus,isST", "start_date": start, "end_date": end, "frequency": "d", "adjustflag": "3"})
            receipts.append(receipt)
            required = {"date", "open", "high", "low", "close", "volume", "amount", "tradestatus", "isST"}
            if not required.issubset(history.columns):
                raise BaoStockSourceUnavailable(interface="query_history_k_data_plus", error_code="schema", message="required_fields_missing")
            for _, row in history.iterrows():
                day = str(row["date"])
                if day not in dates:
                    continue
                for source, target in (("open", "open"), ("high", "high"), ("low", "low"), ("close", "close"), ("volume", "volume"), ("amount", "amount")):
                    market[target].loc[pd.Timestamp(day), symbol] = pd.to_numeric(row[source], errors="coerce")
                suspended.loc[pd.Timestamp(day), symbol] = str(row["tradestatus"]) != "1"
                is_st.loc[pd.Timestamp(day), symbol] = str(row["isST"]) != "0"
        membership = pd.DataFrame([[symbol in daily_codes[day] for symbol in symbols] for day in dates], index=index, columns=symbols, dtype=bool)
        security = pd.DataFrame(basic_rows).set_index("symbol").reindex(symbols)
        security["record_available_at"] = self.source_as_of
        listing_age = pd.DataFrame({symbol: [(pd.Timestamp(day) - pd.Timestamp(security.loc[symbol, "listing_date"])).days for day in dates] for symbol in symbols}, index=index, dtype=float)
        availability = {name: pd.DataFrame(self.source_as_of, index=index, columns=symbols, dtype=object) for name in market}
        empty_actions = pd.DataFrame(
            columns=["symbol", "effective_date", "announced_at", "factor"]
        )
        empty_actions.index.name = "action_id"
        return AsharePITSourceBundleV1(
            market_fields=dict(sorted(market.items())), field_available_at=dict(sorted(availability.items())),
            trade_state_fields={"at_limit_down": pd.DataFrame(False, index=index, columns=symbols, dtype=bool), "at_limit_up": pd.DataFrame(False, index=index, columns=symbols, dtype=bool), "is_st": is_st, "is_suspended": suspended, "listing_age_days": listing_age},
            daily_membership=membership, security_master=security, corporate_actions=empty_actions, calendar_dates=dates,
            source_manifest=AsharePITSourceManifestV1(adapter_id=request.adapter_id, request_hash=request.request_hash, dataset_vintage=self.dataset_vintage, source_as_of=self.source_as_of, query_receipt_hashes=tuple(sorted(set(receipts))), source_partition_hashes=tuple(sorted(set(receipts)))),
        )


def production_factory_attestation(adapter: object) -> Mapping[str, Any] | None:
    """Attest only the SDK-created adapter; field audits remain authoritative."""
    if type(adapter) is not BaoStockAshareEligibleUniverseAdapterV1 or adapter._authority_capability is not _PRODUCTION_AUTHORITY_CAPABILITY:
        return None
    try:
        import baostock as bs  # type: ignore[import-untyped]
        distribution = importlib.metadata.distribution("baostock")
        module_path = str(getattr(bs, "__file__", ""))
        source = inspect.getsource(bs).replace("\r\n", "\n")
        if not module_path or not source:
            return None
        content = {
            "schema_version": "baostock_factory_attestation.v1",
            "factory_origin": "installed_baostock_distribution",
            "provider_version": distribution.version,
            "module": "baostock",
            "module_source_hash": canonical_json_hash({"source": source}),
        }
    except (ImportError, OSError, TypeError, ValueError, importlib.metadata.PackageNotFoundError):
        return None
    return {**content, "factory_hash": canonical_json_hash(content)}


__all__ = ["BaoStockAshareEligibleUniverseAdapterV1", "BaoStockSourceUnavailable", "production_factory_attestation"]
