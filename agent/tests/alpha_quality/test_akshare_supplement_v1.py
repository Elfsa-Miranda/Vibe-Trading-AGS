from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.alpha_quality.adapters.akshare_supplement_v1 import (
    AKShareSupplementAdapterV1,
    CrossProviderConflictError,
    require_cross_provider_agreement,
)
from src.research_ledger.events.artifacts import AtomicContentAddressedArtifactWriter


def test_akshare_failure_is_typed_unavailable() -> None:
    def failed(**_: object) -> pd.DataFrame:
        raise TimeoutError("proxy timeout")

    result = AKShareSupplementAdapterV1({"limit_up_pool": failed}, akshare_version="fixture").fetch("limit_up_pool", date="20240102")

    assert result.status == "typed_unavailable"
    assert result.sanitized_failure == "transport_error"
    assert result.artifact_hash is None


def test_akshare_success_records_source_metadata_and_artifact(tmp_path: Path) -> None:
    adapter = AKShareSupplementAdapterV1(
        {"delisting_sh": lambda: pd.DataFrame({"code": ["600000"], "date": ["2024-01-02"]})},
        artifact_writer=AtomicContentAddressedArtifactWriter(tmp_path),
        akshare_version="fixture-1.0",
    )
    result = adapter.fetch("delisting_sh")

    assert result.status == "success"
    assert result.upstream_source == "sse"
    assert result.endpoint_identity == "AKShare stock_info_sh_delist"
    assert result.artifact_hash and result.artifact_hash.startswith("sha256:")
    assert list((tmp_path / "akshare-raw-v1").rglob("*.json"))


def test_cross_provider_conflict_is_explicit() -> None:
    with pytest.raises(CrossProviderConflictError):
        require_cross_provider_agreement(field_name="limit_up", baostock_value=10.0, akshare_value=10.1)
