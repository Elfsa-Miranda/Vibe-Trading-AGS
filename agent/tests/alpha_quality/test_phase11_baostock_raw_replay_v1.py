from __future__ import annotations

import pytest

from scripts.run_phase11_baostock_research_only_v1 import (
    _adapter,
    _load_exact_baostock_replay,
)
from src.alpha_quality.adapters.baostock_eligible_universe_v1 import (
    BaoStockAshareEligibleUniverseAdapterV1,
)
from src.research_ledger.events.artifacts import AtomicContentAddressedArtifactWriter
from src.research_ledger.hash_utils import canonical_json_hash


def _raw_partition() -> dict[str, object]:
    content: dict[str, object] = {
        "schema_version": "baostock_raw_partition.v1",
        "interface": "query_trade_dates",
        "provider_version": "0.9.3",
        "parameters": {"end_date": "2025-01-03", "start_date": "2025-01-02"},
        "parameters_hash": canonical_json_hash(
            {"end_date": "2025-01-03", "start_date": "2025-01-02"}
        ),
        "retrieved_at": "2026-07-14T19:14:43.456366+08:00",
        "error_code": "0",
        "sanitized_message": "success",
        "status": "success",
        "row_count": 1,
        "columns": ["calendar_date", "is_trading_day"],
        "rows": [["2025-01-02", "1"]],
    }
    return {**content, "partition_hash": canonical_json_hash(content)}


def _write_partition(tmp_path):
    payload = _raw_partition()
    (tmp_path / "artifacts").mkdir()
    return AtomicContentAddressedArtifactWriter(tmp_path / "artifacts").write_json(
        namespace="baostock-raw-v1",
        payload=payload,
        schema_version="baostock_raw_partition.v1",
        semantic_hash_field="partition_hash",
        closed_keys=frozenset(payload),
        media_type="application/json",
    )


def test_exact_raw_replay_recomputes_blob_and_semantic_manifests(tmp_path) -> None:
    _write_partition(tmp_path)
    client, version, source_as_of = _load_exact_baostock_replay(tmp_path)

    result = client.query_trade_dates(
        start_date="2025-01-02", end_date="2025-01-03"
    )
    assert version == "0.9.3"
    assert source_as_of == "2026-07-14T19:14:43.456366+08:00"
    assert client.replay_verification["partition_count"] == 1
    assert client.replay_verification["blob_hash_manifest"].startswith("sha256:")
    assert client.replay_verification["semantic_hash_manifest"].startswith(
        "sha256:"
    )
    assert result.next() is True
    assert result.get_row_data() == ["2025-01-02", "1"]
    assert result.next() is False


def test_exact_raw_replay_rejects_noncanonical_blob(tmp_path) -> None:
    artifact = _write_partition(tmp_path)
    target = tmp_path / "artifacts" / artifact.relative_path
    target.write_bytes(target.read_bytes() + b" ")

    with pytest.raises(ValueError, match="canonical JSON"):
        _load_exact_baostock_replay(tmp_path)


def test_exact_raw_replay_does_not_log_in_or_change_adapter_identity(
    tmp_path, monkeypatch
) -> None:
    _write_partition(tmp_path)
    output = tmp_path / "output"
    output.mkdir()

    def reject_login(*args, **kwargs):
        raise AssertionError("exact raw replay must not call BaoStock login")

    monkeypatch.setattr(
        BaoStockAshareEligibleUniverseAdapterV1,
        "from_environment",
        reject_login,
    )
    adapter = _adapter(
        raw_writer=AtomicContentAddressedArtifactWriter(output),
        replay_raw_root=tmp_path,
    )

    assert adapter.descriptor().provider == "baostock"
    assert adapter.provider_version == "0.9.3"
    assert adapter.source_as_of == "2026-07-14T19:14:43.456366+08:00"
    frame, receipt = adapter._fetch(
        "query_trade_dates",
        {"start_date": "2025-01-02", "end_date": "2025-01-03"},
    )
    assert frame.to_dict("records") == [
        {"calendar_date": "2025-01-02", "is_trading_day": "1"}
    ]
    assert receipt.startswith("sha256:")
