from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.alpha_foundry.retrieval.feature_source_v1 import (
    FrozenTrainValidSnapshotArtifactStoreV1,
    TrainValidSnapshotServiceV1,
)
from src.alpha_quality.flags import ResolvedAGSFlags
from src.research_ledger.events import ResearchEventStore
from test_retriever_shadow import _flags


def _panel(*, first: float = 1.0):
    dates = pd.to_datetime(["2025-01-03", "2025-01-01", "2025-01-02"])
    return {
        "open": pd.DataFrame(
            {"BBB": [4.0, 2.0, 3.0], "AAA": [3.0, first, 2.0]},
            index=dates,
        ),
        "close": pd.DataFrame(
            {"BBB": [4.5, 2.5, 3.5], "AAA": [3.5, 1.5, None]},
            index=dates,
        ),
        "_meta": {
            "pit_contract_present": True,
            "survivorship_bias": False,
            "calendar": "SSE_SZSE",
            "timezone": "Asia/Shanghai",
        },
    }


def _store(tmp_path: Path) -> ResearchEventStore:
    return ResearchEventStore(
        tmp_path / "events.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=_flags(),
        code_version="frozen-train-valid-snapshot-test",
    )


def _freeze(tmp_path: Path, *, first: float = 1.0):
    store = _store(tmp_path)
    recorded = TrainValidSnapshotServiceV1(store, flags=store.flags).freeze(
        _panel(first=first),
        universe="fixture-universe",
        period="2025-01-01/2025-01-03",
        source_config={"provider": "fixture", "api_key": "sk-private"},
        run_id="snapshot-freeze-run",
    )
    return store, recorded


def test_train_valid_snapshot_rebuilds_raw_values_and_normalizes_axes(
    tmp_path: Path,
) -> None:
    store, recorded = _freeze(tmp_path)
    snapshot = recorded.snapshot
    assert snapshot.data_scope == "train_valid"
    assert list(snapshot.frames) == ["close", "open"]
    panel = snapshot.to_panel()
    assert list(panel["open"].index) == list(pd.date_range("2025-01-01", periods=3))
    assert list(panel["open"].columns) == ["AAA", "BBB"]
    assert panel["open"].loc[pd.Timestamp("2025-01-01"), "AAA"] == 1.0
    assert pd.isna(panel["close"].loc[pd.Timestamp("2025-01-02"), "AAA"])
    assert recorded.event.payload["snapshot_hash"] == snapshot.snapshot_hash
    assert recorded.event.payload["pit_contract_present"] is True
    assert recorded.event.payload["survivorship_bias"] is False
    assert store.verify_chain()


def test_snapshot_identity_changes_with_one_raw_value_and_retry_is_idempotent(
    tmp_path: Path,
) -> None:
    store, first = _freeze(tmp_path)
    retry = TrainValidSnapshotServiceV1(store, flags=store.flags).freeze(
        _panel(),
        universe="fixture-universe",
        period="2025-01-01/2025-01-03",
        source_config={"api_key": "another-secret", "provider": "fixture"},
        run_id="snapshot-freeze-run",
    )
    assert retry.event.event_hash == first.event.event_hash
    changed = TrainValidSnapshotServiceV1(store, flags=store.flags).freeze(
        _panel(first=999.0),
        universe="fixture-universe",
        period="2025-01-01/2025-01-03",
        source_config={"provider": "fixture"},
        run_id="snapshot-freeze-run-2",
    )
    assert changed.snapshot.snapshot_hash != first.snapshot.snapshot_hash
    assert changed.event.event_hash != first.event.event_hash
    assert store.verify_chain()


def test_snapshot_artifact_tampering_and_unsafe_values_fail_closed(
    tmp_path: Path,
) -> None:
    store, recorded = _freeze(tmp_path)
    reference = recorded.event.payload["artifact_refs"][0]
    target = store.artifact_root.joinpath(*str(reference["relative_path"]).split("/"))
    raw = target.read_text(encoding="utf-8")
    assert "sk-private" not in raw
    target.write_text(raw.replace("1.0", "999.0", 1), encoding="utf-8")
    assert store.verify_chain() is False
    with pytest.raises(ValueError, match="cannot be rebuilt|identity"):
        FrozenTrainValidSnapshotArtifactStoreV1(store.artifact_root).read(
            str(reference["relative_path"]),
            recorded.snapshot.snapshot_hash,
        )

    bad = _panel()
    bad["open"].iloc[0, 0] = float("inf")
    with pytest.raises(ValueError, match="infinite"):
        TrainValidSnapshotServiceV1(_store(tmp_path / "bad"), flags=_flags()).freeze(
            bad,
            universe="fixture",
            period="2025",
            source_config={"provider": "fixture"},
            run_id="bad-snapshot",
        )


def test_snapshot_api_has_no_final_or_forward_scope_and_is_feature_gated(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with pytest.raises(TypeError):
        TrainValidSnapshotServiceV1(store, flags=store.flags).freeze(  # type: ignore[call-arg]
            _panel(),
            universe="fixture",
            period="2025",
            source_config={"provider": "fixture"},
            run_id="scope-forge",
            data_scope="final_test",
        )
    disabled = ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_ALPHA_FOUNDRY": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
            "VIBE_TRADING_TOPOLOGY_RETRIEVER": "0",
        }
    )
    with pytest.raises(RuntimeError, match="disabled"):
        TrainValidSnapshotServiceV1(store, flags=disabled)
