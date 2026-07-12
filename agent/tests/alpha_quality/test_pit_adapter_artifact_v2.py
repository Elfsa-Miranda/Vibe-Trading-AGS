from __future__ import annotations

import inspect
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.alpha_quality.pit_adapter_v1 import (
    AsharePITAdapterDescriptorV1,
    AsharePITAdapterRegistryV1,
    AsharePITSnapshotRequestV1,
    AsharePITSourceBundleV1,
    AsharePITSourceManifestV1,
)
from src.alpha_quality.adapters.tushare_csi300_pit_v1 import (
    TushareCSI300PITAdapterV1,
)
from src.alpha_quality.pit_artifact_v2 import (
    FrozenAsharePITSnapshotArtifactStoreV2,
    FrozenAsharePITSnapshotV2,
)
from src.alpha_quality.pit_snapshot_v2 import validate_ashare_pit_source_v2
from src.research_ledger.hash_utils import canonical_json_hash


_HASH = "sha256:" + "a" * 64


def _request() -> AsharePITSnapshotRequestV1:
    return AsharePITSnapshotRequestV1(
        adapter_id="fixture-pit-v1",
        calendar_dates=("2025-01-02", "2025-01-03"),
        required_fields=("amount", "close", "high", "low", "open", "volume"),
        valid_cutoff="2025-01-03",
        evaluation_policy_event_hash=_HASH,
    )


def _bundle(request: AsharePITSnapshotRequestV1) -> AsharePITSourceBundleV1:
    dates = pd.DatetimeIndex(request.calendar_dates)
    symbols = ["000001.SZ", "600000.SH"]
    base = pd.DataFrame(
        [[10.0, 20.0], [11.0, 19.0]],
        index=dates,
        columns=symbols,
    )
    market = {
        "amount": base * 1000.0,
        "close": base,
        "high": base + 1.0,
        "low": base - 1.0,
        "open": base - 0.5,
        "volume": base * 100.0,
    }
    available = {
        field: pd.DataFrame(
            [[f"{day}T15:00:00+08:00"] * len(symbols) for day in request.calendar_dates],
            index=dates,
            columns=symbols,
        )
        for field in market
    }
    truth = pd.DataFrame(True, index=dates, columns=symbols, dtype=bool)
    falsehood = pd.DataFrame(False, index=dates, columns=symbols, dtype=bool)
    trade_states = {
        "at_limit_down": falsehood.copy(),
        "at_limit_up": falsehood.copy(),
        "is_st": falsehood.copy(),
        "is_suspended": falsehood.copy(),
        "listing_age_days": pd.DataFrame(
            1000.0,
            index=dates,
            columns=symbols,
        ),
    }
    security_master = pd.DataFrame(
        {
            "delisting_date": [None, None],
            "listing_date": ["1991-04-03", "1999-11-10"],
            "record_available_at": [
                "2020-01-01T00:00:00+08:00",
                "2020-01-01T00:00:00+08:00",
            ],
        },
        index=pd.Index(symbols, name="symbol"),
    )
    corporate_actions = pd.DataFrame(
        {
            "announced_at": ["2024-12-01T18:00:00+08:00"],
            "effective_date": ["2025-01-02"],
            "factor": [1.0],
            "symbol": ["000001.SZ"],
        },
        index=pd.Index(["action-1"], name="action_id"),
    )
    return AsharePITSourceBundleV1(
        market_fields=dict(sorted(market.items())),
        field_available_at=dict(sorted(available.items())),
        trade_state_fields=dict(sorted(trade_states.items())),
        daily_membership=truth,
        security_master=security_master,
        corporate_actions=corporate_actions,
        calendar_dates=request.calendar_dates,
        source_manifest=AsharePITSourceManifestV1(
            adapter_id=request.adapter_id,
            request_hash=request.request_hash,
            dataset_vintage="fixture-20250104",
            source_as_of="2025-01-04T00:00:00+08:00",
            query_receipt_hashes=(_HASH,),
            source_partition_hashes=("sha256:" + "b" * 64,),
        ),
    )


class FixturePITAdapterV1:
    def descriptor(self) -> AsharePITAdapterDescriptorV1:
        return AsharePITAdapterDescriptorV1(
            adapter_id="fixture-pit-v1",
            provider="fixture-provider",
            adapter_version="1.0.0",
            market="CN_A_SHARE",
            calendar_id="XSHG_XSHE",
            timezone="Asia/Shanghai",
            membership_dataset="fixture-membership-v1",
            security_master_dataset="fixture-security-master-v1",
            corporate_action_dataset="fixture-actions-v1",
            trade_state_dataset="fixture-trade-state-v1",
            price_dataset="fixture-price-v1",
            availability_semantics="provider_release_timestamp.v1",
            adjustment_semantics="raw_prices_plus_dated_actions.v1",
        )

    def load(self, request: AsharePITSnapshotRequestV1) -> AsharePITSourceBundleV1:
        return _bundle(request)


def test_registry_derives_implementation_identity_and_denies_self_promotion() -> None:
    adapter = FixturePITAdapterV1()
    registry = AsharePITAdapterRegistryV1({"fixture-pit-v1": adapter})
    registration = registry.registration("fixture-pit-v1")

    assert registration.authority_class == "external_unverified"
    assert registration.descriptor.descriptor_hash.startswith("sha256:")
    assert registration.implementation_hash == canonical_json_hash(
        {
            "module": type(adapter).__module__,
            "qualname": type(adapter).__qualname__,
            "source": inspect.getsource(type(adapter)).replace("\r\n", "\n"),
        }
    )
    assert registry.registry_hash.startswith("sha256:")


def test_builtin_type_with_injected_client_is_still_not_production_authority() -> None:
    adapter = TushareCSI300PITAdapterV1.for_test(object())
    registry = AsharePITAdapterRegistryV1(
        {"tushare-csi300-pit-v1": adapter}
    )

    assert (
        registry.registration("tushare-csi300-pit-v1").authority_class
        == "external_unverified"
    )


def test_environment_factory_mints_private_builtin_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tushare

    provider_client = object()
    monkeypatch.setenv("TUSHARE_TOKEN", "fixture-nonsecret-token")
    monkeypatch.setattr(tushare, "pro_api", lambda token: provider_client)
    adapter = TushareCSI300PITAdapterV1.from_environment()
    registry = AsharePITAdapterRegistryV1(
        {"tushare-csi300-pit-v1": adapter}
    )

    assert (
        registry.registration("tushare-csi300-pit-v1").authority_class
        == "built_in_production"
    )


class _CompleteFakeTushareClient:
    symbols = ("000001.SZ", "600000.SH")
    dates = ("20250102", "20250103")

    def trade_cal(self, **kwargs):
        return pd.DataFrame({"cal_date": list(self.dates), "is_open": [1, 1]})

    def index_weight(self, **kwargs):
        return pd.DataFrame(
            {
                "index_code": ["399300.SZ", "399300.SZ"],
                "con_code": list(self.symbols),
                "trade_date": ["20250101", "20250101"],
                "weight": [50.0, 50.0],
            }
        )

    def stock_basic(self, **kwargs):
        if kwargs["list_status"] != "L":
            return pd.DataFrame(
                columns=["ts_code", "name", "list_date", "delist_date", "list_status"]
            )
        return pd.DataFrame(
            {
                "ts_code": list(self.symbols),
                "name": ["平安银行", "浦发银行"],
                "list_date": ["19910403", "19991110"],
                "delist_date": [None, None],
                "list_status": ["L", "L"],
            }
        )

    def daily(self, **kwargs):
        offset = 0.0 if kwargs["ts_code"] == self.symbols[0] else 5.0
        return pd.DataFrame(
            {
                "ts_code": [kwargs["ts_code"]] * 2,
                "trade_date": list(self.dates),
                "open": [10.0 + offset, 11.0 + offset],
                "high": [11.0 + offset, 12.0 + offset],
                "low": [9.0 + offset, 10.0 + offset],
                "close": [10.5 + offset, 11.5 + offset],
                "vol": [1000.0, 1100.0],
                "amount": [10000.0, 12000.0],
            }
        )

    def stk_limit(self, **kwargs):
        return pd.DataFrame(
            {
                "ts_code": [kwargs["ts_code"]] * 2,
                "trade_date": list(self.dates),
                "up_limit": [20.0, 20.0],
                "down_limit": [5.0, 5.0],
            }
        )

    def suspend_d(self, **kwargs):
        return pd.DataFrame(columns=["ts_code", "trade_date", "suspend_type"])

    def namechange(self, **kwargs):
        return pd.DataFrame(
            columns=["ts_code", "name", "start_date", "end_date", "ann_date", "change_reason"]
        )

    def adj_factor(self, **kwargs):
        return pd.DataFrame(
            {
                "ts_code": [kwargs["ts_code"]] * 2,
                "trade_date": list(self.dates),
                "adj_factor": [1.0, 1.0],
            }
        )

    def dividend(self, **kwargs):
        return pd.DataFrame(columns=["ts_code", "ann_date", "ex_date", "div_proc"])


def test_builtin_adapter_complete_provider_facts_can_be_decision_grade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tushare

    monkeypatch.setenv("TUSHARE_TOKEN", "fixture-nonsecret-token")
    monkeypatch.setattr(
        tushare,
        "pro_api",
        lambda token: _CompleteFakeTushareClient(),
    )
    adapter = TushareCSI300PITAdapterV1.from_environment()
    registry = AsharePITAdapterRegistryV1(
        {"tushare-csi300-pit-v1": adapter}
    )
    request = AsharePITSnapshotRequestV1(
        adapter_id="tushare-csi300-pit-v1",
        calendar_dates=("2025-01-02", "2025-01-03"),
        required_fields=("amount", "close", "high", "low", "open", "volume"),
        valid_cutoff="2025-01-03",
        evaluation_policy_event_hash=_HASH,
    )
    bundle = adapter.load(request)
    result = validate_ashare_pit_source_v2(
        bundle=bundle,
        request=request,
        registration=registry.registration("tushare-csi300-pit-v1"),
    )

    assert result.evidence["pit_contract_status"] == "complete"
    assert result.evidence["survivorship_status"] == (
        "controlled_by_daily_membership"
    )
    assert result.evidence["decision_grade"] is True
    assert result.evidence["hard_failures"] == ()
    assert result.evidence["caps"] == ()


def test_request_has_no_pit_survivorship_or_decision_truth_channel() -> None:
    parameters = set(inspect.signature(AsharePITSnapshotRequestV1).parameters)
    assert parameters.isdisjoint(
        {
            "panel",
            "pit_contract_present",
            "survivorship_bias",
            "decision_grade",
            "complete",
            "caps",
        }
    )


def test_source_bundle_deep_copies_frames() -> None:
    request = _request()
    bundle = _bundle(request)
    sealed = bundle.sealed_copy()
    source = bundle.market_fields["close"]
    source.iloc[0, 0] = 999.0

    assert float(bundle.market_fields["close"].iloc[0, 0]) == 999.0
    assert float(sealed.market_fields["close"].iloc[0, 0]) == 10.0
    with pytest.raises(TypeError):
        sealed.market_fields["forged"] = source


def test_parquet_snapshot_round_trip_revalidates_semantic_content(tmp_path: Path) -> None:
    request = _request()
    bundle = _bundle(request)
    registry = AsharePITAdapterRegistryV1(
        {"fixture-pit-v1": FixturePITAdapterV1()}
    )
    root = tmp_path / "artifacts"
    root.mkdir()
    store = FrozenAsharePITSnapshotArtifactStoreV2(root)
    table_refs = store.write_bundle_tables(bundle)
    derived = {
        "pit_contract_status": "unavailable",
        "decision_grade": False,
        "hard_failures": [],
        "caps": ["ADAPTER_AUTHORITY_UNVERIFIED"],
        "warnings": [],
    }
    snapshot = FrozenAsharePITSnapshotV2.build(
        registration=registry.registration("fixture-pit-v1"),
        registry_hash=registry.registry_hash,
        request=request,
        source_manifest=bundle.source_manifest,
        evaluation_policy_event_hash=request.evaluation_policy_event_hash,
        calendar_hash="sha256:" + "c" * 64,
        evaluation_time_policy_hash="sha256:" + "d" * 64,
        split_plan_hash="sha256:" + "e" * 64,
        table_refs=table_refs,
        derived_evidence=derived,
    )
    artifact = store.write_manifest(snapshot)
    reopened = store.read_manifest(
        artifact.relative_path,
        expected_snapshot_hash=snapshot.snapshot_hash,
        expected_blob_hash=artifact.blob_hash,
    )
    rebuilt_bundle = store.read_bundle(reopened)

    assert reopened == snapshot
    pd.testing.assert_frame_equal(
        rebuilt_bundle.market_fields["close"],
        bundle.market_fields["close"].rename_axis("date"),
    )
    assert len(table_refs) == (
        2 * len(bundle.market_fields) + len(bundle.trade_state_fields) + 3
    )


def test_validator_derives_directional_masks_and_does_not_require_membership_to_sell() -> None:
    request = _request()
    bundle = _bundle(request)
    membership = bundle.daily_membership.copy()
    membership.loc[pd.Timestamp("2025-01-03"), "000001.SZ"] = False
    states = {name: frame.copy() for name, frame in bundle.trade_state_fields.items()}
    states["at_limit_up"].loc[pd.Timestamp("2025-01-02"), "000001.SZ"] = True
    states["at_limit_down"].loc[pd.Timestamp("2025-01-02"), "600000.SH"] = True
    modified = replace(
        bundle,
        daily_membership=membership,
        trade_state_fields=dict(sorted(states.items())),
    )
    registration = AsharePITAdapterRegistryV1(
        {"fixture-pit-v1": FixturePITAdapterV1()}
    ).registration("fixture-pit-v1")
    result = validate_ashare_pit_source_v2(
        bundle=modified,
        request=request,
        registration=registration,
    )

    can_buy = result.derived_masks["can_buy"]
    can_sell = result.derived_masks["can_sell"]
    assert not bool(can_buy.loc["2025-01-02", "000001.SZ"])
    assert bool(can_sell.loc["2025-01-02", "000001.SZ"])
    assert bool(can_buy.loc["2025-01-02", "600000.SH"])
    assert not bool(can_sell.loc["2025-01-02", "600000.SH"])
    assert not bool(can_buy.loc["2025-01-03", "000001.SZ"])
    assert bool(can_sell.loc["2025-01-03", "000001.SZ"])
    assert result.evidence["decision_grade"] is False
    assert result.evidence["caps"] == ("ADAPTER_AUTHORITY_UNVERIFIED",)


def test_post_signal_availability_is_typed_contamination_not_a_silent_pass() -> None:
    request = _request()
    bundle = _bundle(request)
    availability = {
        name: frame.copy() for name, frame in bundle.field_available_at.items()
    }
    availability["close"].loc[
        pd.Timestamp("2025-01-02"), "000001.SZ"
    ] = "2025-01-02T16:00:00+08:00"
    modified = replace(
        bundle,
        field_available_at=dict(sorted(availability.items())),
    )
    registration = AsharePITAdapterRegistryV1(
        {"fixture-pit-v1": FixturePITAdapterV1()}
    ).registration("fixture-pit-v1")
    result = validate_ashare_pit_source_v2(
        bundle=modified,
        request=request,
        registration=registration,
    )

    assert result.evidence["pit_contract_status"] == "contaminated"
    assert "POST_SIGNAL_FIELD_AVAILABILITY" in result.evidence["hard_failures"]
    assert not bool(
        result.derived_masks["can_observe"].loc[
            "2025-01-02", "000001.SZ"
        ]
    )
    assert result.evidence["decision_grade"] is False


def test_missing_daily_membership_date_fails_closed_with_false_masks() -> None:
    request = _request()
    bundle = _bundle(request)
    modified = replace(
        bundle,
        daily_membership=bundle.daily_membership.iloc[:1].copy(),
    )
    registration = AsharePITAdapterRegistryV1(
        {"fixture-pit-v1": FixturePITAdapterV1()}
    ).registration("fixture-pit-v1")
    result = validate_ashare_pit_source_v2(
        bundle=modified,
        request=request,
        registration=registration,
    )

    assert "DAILY_MEMBERSHIP_AXES_MISMATCH" in result.evidence["hard_failures"]
    assert all(frame.eq(False).all().all() for frame in result.derived_masks.values())
    assert result.evidence["decision_grade"] is False
