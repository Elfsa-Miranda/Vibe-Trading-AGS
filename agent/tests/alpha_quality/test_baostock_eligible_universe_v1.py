from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.alpha_quality.adapters.baostock_eligible_universe_v1 import (
    BaoStockAshareEligibleUniverseAdapterV1,
    BaoStockSourceUnavailable,
)
from src.alpha_quality.pit_adapter_v1 import AsharePITAdapterRegistryV1, AsharePITSnapshotRequestV1
from src.alpha_quality.pit_snapshot_v2 import AsharePITValidationPolicyV1, validate_ashare_pit_source_v2
from src.research_ledger.events.artifacts import AtomicContentAddressedArtifactWriter


_HASH = "sha256:" + "a" * 64


class _Result:
    def __init__(self, fields: list[str], rows: list[list[str]], code: str = "0") -> None:
        self.fields, self._rows, self.error_code, self.error_msg = fields, rows, code, "provider failure"
        self._index = -1

    def next(self) -> bool:
        self._index += 1
        return self._index < len(self._rows)

    def get_row_data(self) -> list[str]:
        return self._rows[self._index]


class _BaoClient:
    def query_trade_dates(self, **_: str) -> _Result:
        return _Result(["calendar_date", "is_trading_day"], [["2024-01-02", "1"], ["2024-01-03", "1"]])

    def query_all_stock(self, *, day: str) -> _Result:
        rows = [["sz.000001", "1"]]
        if day == "2024-01-03":
            rows.append(["sh.600000", "1"])
        return _Result(["code", "tradeStatus"], rows)

    def query_stock_basic(self, *, code: str) -> _Result:
        return _Result(["ipoDate", "outDate"], [["1991-04-03" if code == "sz.000001" else "1999-11-10", ""]])

    def query_history_k_data_plus(self, *, code: str, **_: str) -> _Result:
        rows = [["2024-01-02", code, "10", "11", "9", "10.5", "10", "100", "1000", "3", "1", "0"]]
        if code == "sh.600000":
            rows = [["2024-01-03", code, "20", "21", "19", "20.5", "20", "200", "2000", "3", "1", "0"]]
        return _Result(["date", "code", "open", "high", "low", "close", "preclose", "volume", "amount", "adjustflag", "tradestatus", "isST"], rows)


def _request() -> AsharePITSnapshotRequestV1:
    return AsharePITSnapshotRequestV1(
        adapter_id="baostock-ashare-eligible-golden-cohort-v1",
        calendar_dates=("2024-01-02", "2024-01-03"),
        required_fields=("amount", "close", "high", "low", "open", "volume"),
        valid_cutoff="2024-01-03",
        evaluation_policy_event_hash=_HASH,
    )


def test_nonzero_baostock_error_is_typed_failure_not_empty_success(tmp_path: Path) -> None:
    class FailedClient(_BaoClient):
        def query_trade_dates(self, **_: str) -> _Result:
            return _Result([], [], code="10001001")

    writer = AtomicContentAddressedArtifactWriter(tmp_path)
    adapter = BaoStockAshareEligibleUniverseAdapterV1(FailedClient(), "fixture-v1", "2024-01-04T00:00:00+08:00", writer, golden_symbol_count=1)
    with pytest.raises(BaoStockSourceUnavailable, match="10001001") as failure:
        adapter.load(_request())
    assert failure.value.receipt_hash and failure.value.receipt_hash.startswith("sha256:")
    raw = json.loads(next((writer.root / "baostock-raw-v1").rglob("*.json")).read_text(encoding="utf-8"))
    assert raw["status"] == "typed_unavailable"
    assert raw["error_code"] == "10001001"


def test_dynamic_daily_universe_is_not_backfilled_from_current_survivors(tmp_path: Path) -> None:
    writer = AtomicContentAddressedArtifactWriter(tmp_path)
    adapter = BaoStockAshareEligibleUniverseAdapterV1(_BaoClient(), "fixture-v1", "2024-01-04T00:00:00+08:00", writer, golden_symbol_count=1)
    bundle = adapter.load(_request())

    assert tuple(bundle.daily_membership.columns) == ("sz.000001",)
    assert bool(bundle.daily_membership.loc[pd.Timestamp("2024-01-02"), "sz.000001"])
    assert bool(bundle.daily_membership.loc[pd.Timestamp("2024-01-03"), "sz.000001"])
    assert all(value.startswith("sha256:") for value in bundle.source_manifest.source_partition_hashes)
    raw_paths = list((tmp_path / "baostock-raw-v1").rglob("*.json"))
    assert raw_paths
    raw = json.loads(raw_paths[0].read_text(encoding="utf-8"))
    assert raw["error_code"] == "0"
    assert raw["parameters_hash"].startswith("sha256:")
    assert raw["provider_version"] == "unverified"
    assert raw["row_count"] >= 0
    assert raw["retrieved_at"] == "2024-01-04T00:00:00+08:00"
    assert "TUSHARE" not in json.dumps(raw, sort_keys=True)
    assert str(tmp_path) not in json.dumps(raw, sort_keys=True)
    assert adapter.descriptor().adapter_id == "baostock-ashare-eligible-golden-cohort-v1"
    assert adapter.descriptor().membership_dataset == "a-share-eligible-golden-cohort-v1"


def test_zero_code_with_no_rows_is_a_recorded_empty_success(tmp_path: Path) -> None:
    adapter = BaoStockAshareEligibleUniverseAdapterV1(
        _BaoClient(),
        "fixture-v1",
        "2024-01-04T00:00:00+08:00",
        AtomicContentAddressedArtifactWriter(tmp_path),
        golden_symbol_count=1,
    )
    adapter.client.query_trade_dates = lambda **_: _Result(["calendar_date", "is_trading_day"], [])

    frame, receipt_hash = adapter._fetch(
        "query_trade_dates",
        {"start_date": "2024-01-02", "end_date": "2024-01-03"},
    )

    assert frame.empty
    assert receipt_hash.startswith("sha256:")
    raw_paths = list((tmp_path / "baostock-raw-v1").rglob("*.json"))
    assert len(raw_paths) == 1
    raw = json.loads(raw_paths[0].read_text(encoding="utf-8"))
    assert raw["error_code"] == "0"
    assert raw["row_count"] == 0


def test_existing_validator_keeps_baostock_result_non_strict() -> None:
    adapter = BaoStockAshareEligibleUniverseAdapterV1(_BaoClient(), "fixture-v1", "2024-01-01T00:00:00+08:00", golden_symbol_count=1)
    bundle = adapter.load(_request())
    registration = AsharePITAdapterRegistryV1({bundle.source_manifest.adapter_id: adapter}).registration(bundle.source_manifest.adapter_id)
    result = validate_ashare_pit_source_v2(bundle=bundle, request=_request(), registration=registration, policy=AsharePITValidationPolicyV1(minimum_listing_age_days=60, minimum_amount=1.0))

    assert result.evidence["decision_grade"] is False
    assert "ADAPTER_AUTHORITY_UNVERIFIED" in result.evidence["caps"]


def test_st_and_trade_status_exclude_an_otherwise_historical_member() -> None:
    class BlockedClient(_BaoClient):
        def query_history_k_data_plus(self, *, code: str, **kwargs: str) -> _Result:
            result = super().query_history_k_data_plus(code=code, **kwargs)
            result._rows[0][10] = "0"
            result._rows[0][11] = "1"
            return result

    adapter = BaoStockAshareEligibleUniverseAdapterV1(BlockedClient(), "fixture-v1", "2024-01-01T00:00:00+08:00", golden_symbol_count=1)
    bundle = adapter.load(_request())
    registration = AsharePITAdapterRegistryV1({bundle.source_manifest.adapter_id: adapter}).registration(bundle.source_manifest.adapter_id)
    result = validate_ashare_pit_source_v2(bundle=bundle, request=_request(), registration=registration, policy=AsharePITValidationPolicyV1(minimum_listing_age_days=60, minimum_amount=1.0))

    assert not bool(result.derived_masks["eligible_universe"].loc[pd.Timestamp("2024-01-02"), "sz.000001"])
