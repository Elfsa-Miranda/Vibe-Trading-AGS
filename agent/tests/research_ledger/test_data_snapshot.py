from __future__ import annotations

import json

import pandas as pd

from src.research_ledger.data_snapshot import build_data_snapshot, build_data_snapshot_v2


def _panel() -> dict[str, object]:
    idx = pd.date_range("2024-01-01", periods=3, freq="D")
    close = pd.DataFrame(
        {"AAA": [1.0, 2.0, None], "BBB": [2.0, 3.0, 4.0]},
        index=idx,
    )
    volume = pd.DataFrame(
        {"AAA": [100.0, 110.0, 120.0], "BBB": [200.0, None, 220.0]},
        index=idx,
    )
    return {
        "close": close,
        "volume": volume,
        "_meta": {
            "pit_contract_present": True,
            "survivorship_bias": False,
            "calendar": "SSE_SZSE",
            "timezone": "Asia/Shanghai",
        },
    }


def test_data_snapshot_hash_is_stable_for_reordered_source_config() -> None:
    left = build_data_snapshot(
        _panel(),
        universe="fixture",
        period="2024-2024",
        source_config={"token": "sk-secret", "provider": "fixture"},
    )
    right = build_data_snapshot(
        _panel(),
        universe="fixture",
        period="2024-2024",
        source_config={"provider": "fixture", "token": "sk-secret"},
    )

    assert left.source_config_hash == right.source_config_hash
    assert left.snapshot_hash == right.snapshot_hash
    assert left.schema_version == "data_snapshot.v1"


def test_data_snapshot_redacts_secret_values() -> None:
    snapshot = build_data_snapshot(
        _panel(),
        universe="fixture",
        period="2024-2024",
        source_config={"api_key": "sk-secret", "nested": {"password": "open-sesame"}},
    )

    payload = json.dumps(snapshot.to_dict(), ensure_ascii=False)

    assert "sk-secret" not in payload
    assert "open-sesame" not in payload
    assert "[redacted]" in payload


def test_data_snapshot_represents_pit_and_survivorship_fields() -> None:
    snapshot = build_data_snapshot(
        _panel(),
        universe="fixture",
        period="2024-2024",
        source_config={"provider": "fixture"},
    )

    assert snapshot.pit_contract_present is True
    assert snapshot.survivorship_bias is False
    assert snapshot.row_counts == {"close": 3, "volume": 3}
    assert snapshot.missingness_summary["close"] > 0.0


def test_v2_snapshot_identity_covers_panel_values_not_only_shape() -> None:
    left_panel = _panel()
    right_panel = _panel()
    right_panel["close"].loc[pd.Timestamp("2024-01-01"), "AAA"] = 999.0  # type: ignore[index]

    legacy_left = build_data_snapshot(left_panel, "fixture", "2024", {"provider": "fixture"})
    legacy_right = build_data_snapshot(right_panel, "fixture", "2024", {"provider": "fixture"})
    assert legacy_left.snapshot_hash == legacy_right.snapshot_hash

    left = build_data_snapshot_v2(left_panel, "fixture", "2024", {"provider": "fixture"})
    right = build_data_snapshot_v2(right_panel, "fixture", "2024", {"provider": "fixture"})

    assert left.schema_version == "data_snapshot.v2"
    assert left.panel_content_hash != right.panel_content_hash
    assert left.snapshot_hash != right.snapshot_hash
    assert left.base_manifest.snapshot_hash == legacy_left.snapshot_hash


def test_v2_snapshot_content_hash_is_reproducible_and_strict_json() -> None:
    first = build_data_snapshot_v2(
        _panel(), "fixture", "2024", {"token": "sk-secret", "provider": "fixture"}
    )
    second = build_data_snapshot_v2(
        _panel(), "fixture", "2024", {"provider": "fixture", "token": "sk-secret"}
    )
    encoded = json.dumps(first.to_dict(), allow_nan=False, sort_keys=True)

    assert first.snapshot_hash == second.snapshot_hash
    assert first.frame_content_hashes == second.frame_content_hashes
    assert "sk-secret" not in encoded
