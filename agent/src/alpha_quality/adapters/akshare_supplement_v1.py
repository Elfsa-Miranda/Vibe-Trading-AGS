"""Narrow, receipt-producing AKShare supplement boundary.

It is intentionally not an A-share PIT adapter: its observations supplement
gaps left by BaoStock and are not allowed to promote a provider to strict PIT
authority merely because an endpoint returned rows.
"""

from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Mapping
from zoneinfo import ZoneInfo

import pandas as pd  # type: ignore[import-untyped]

from src.research_ledger.events.artifacts import AtomicContentAddressedArtifactWriter
from src.research_ledger.hash_utils import canonical_json_hash


SupplementStatus = Literal["success", "success_empty", "typed_unavailable"]


class CrossProviderConflictError(ValueError):
    """A supplement must never silently overwrite a BaoStock source fact."""


def require_cross_provider_agreement(
    *, field_name: str, baostock_value: object, akshare_value: object
) -> object:
    if baostock_value != akshare_value:
        raise CrossProviderConflictError(f"cross-provider conflict for {field_name}")
    return baostock_value


@dataclass(frozen=True)
class AKShareSupplementResultV1:
    schema_version: Literal["akshare_supplement_result.v1"]
    endpoint_id: str
    upstream_source: str
    endpoint_identity: str
    akshare_version: str
    retrieved_at: str
    status: SupplementStatus
    row_count: int
    schema: tuple[str, ...]
    known_instability: tuple[str, ...]
    sanitized_failure: str | None
    artifact_hash: str | None

    def __post_init__(self) -> None:
        if self.status == "typed_unavailable" and self.sanitized_failure is None:
            raise ValueError("AKShare unavailable result requires a typed failure")
        if self.status != "typed_unavailable" and self.sanitized_failure is not None:
            raise ValueError("AKShare success cannot carry a failure")
        if self.row_count < 0 or self.schema != tuple(sorted(set(self.schema))):
            raise ValueError("AKShare result shape is invalid")


_ENDPOINTS: Mapping[str, tuple[str, str, str, tuple[str, ...]]] = {
    "corporate_actions": ("cninfo", "stock_history_dividend_detail", "AKShare stock_history_dividend_detail", ("UPSTREAM_REVISION_HISTORY_UNVERIFIED",)),
    "delisting_sh": ("sse", "stock_info_sh_delist", "AKShare stock_info_sh_delist", ("UPSTREAM_HISTORICAL_AS_OF_UNVERIFIED",)),
    "delisting_sz": ("szse", "stock_info_sz_delist", "AKShare stock_info_sz_delist", ("UPSTREAM_HISTORICAL_AS_OF_UNVERIFIED",)),
    "limit_up_pool": ("eastmoney", "stock_zt_pool_em", "AKShare stock_zt_pool_em", ("POOL_IS_NOT_COMPLETE_LIMIT_PRICE_HISTORY",)),
    "notices": ("eastmoney", "stock_notice_report", "AKShare stock_notice_report", ("NOTICE_PAGINATION_AND_REVISION_UNVERIFIED",)),
}


def _sanitize_exception(exc: Exception) -> str:
    text = " ".join(str(exc).split()).lower()
    if any(token in text for token in ("timeout", "connect", "proxy", "network")):
        return "transport_error"
    if "permission" in text or "权限" in text:
        return "permission_denied"
    return "provider_or_schema_error"


@dataclass(frozen=True)
class AKShareSupplementAdapterV1:
    """Closed endpoint map with optional test injection; no generic call API."""

    calls: Mapping[str, Callable[..., Any]]
    artifact_writer: AtomicContentAddressedArtifactWriter | None = None
    akshare_version: str = "unverified"

    @classmethod
    def from_environment(
        cls, *, artifact_writer: AtomicContentAddressedArtifactWriter | None = None
    ) -> "AKShareSupplementAdapterV1":
        import akshare as ak  # type: ignore[import-untyped]

        return cls(
            calls={
                "corporate_actions": ak.stock_history_dividend_detail,
                "delisting_sh": ak.stock_info_sh_delist,
                "delisting_sz": ak.stock_info_sz_delist,
                "limit_up_pool": ak.stock_zt_pool_em,
                "notices": ak.stock_notice_report,
            },
            artifact_writer=artifact_writer,
            akshare_version=importlib.metadata.version("akshare"),
        )

    def fetch(self, endpoint_id: str, **parameters: Any) -> AKShareSupplementResultV1:
        if endpoint_id not in _ENDPOINTS or endpoint_id not in self.calls:
            raise ValueError("AKShare endpoint is not allowlisted")
        upstream, function_name, endpoint_identity, instability = _ENDPOINTS[endpoint_id]
        retrieved_at = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).isoformat()
        try:
            frame = self.calls[endpoint_id](**parameters)
            if not isinstance(frame, pd.DataFrame):
                raise TypeError("AKShare endpoint did not return a DataFrame")
        except Exception as exc:
            return AKShareSupplementResultV1(
                schema_version="akshare_supplement_result.v1", endpoint_id=endpoint_id,
                upstream_source=upstream, endpoint_identity=endpoint_identity,
                akshare_version=self.akshare_version, retrieved_at=retrieved_at,
                status="typed_unavailable", row_count=0, schema=(),
                known_instability=instability, sanitized_failure=_sanitize_exception(exc),
                artifact_hash=None,
            )
        schema = tuple(sorted(str(column) for column in frame.columns))
        payload = {
            "schema_version": "akshare_raw_partition.v1", "endpoint_id": endpoint_id,
            "upstream_source": upstream, "endpoint_identity": endpoint_identity,
            "akshare_version": self.akshare_version, "retrieved_at": retrieved_at,
            "parameters": {str(key): str(value) for key, value in sorted(parameters.items())},
            "row_count": int(len(frame)), "schema": list(schema),
            "rows": [[None if pd.isna(value) else str(value) for value in row] for row in frame.itertuples(index=False, name=None)],
        }
        payload["partition_hash"] = canonical_json_hash(payload)
        if self.artifact_writer is not None:
            self.artifact_writer.write_json(namespace="akshare-raw-v1", payload=payload, schema_version="akshare_raw_partition.v1", semantic_hash_field="partition_hash", closed_keys=frozenset(payload), media_type="application/json")
        return AKShareSupplementResultV1(
            schema_version="akshare_supplement_result.v1", endpoint_id=endpoint_id,
            upstream_source=upstream, endpoint_identity=endpoint_identity,
            akshare_version=self.akshare_version, retrieved_at=retrieved_at,
            status="success_empty" if frame.empty else "success", row_count=len(frame), schema=schema,
            known_instability=instability, sanitized_failure=None,
            artifact_hash=str(payload["partition_hash"]),
        )


__all__ = ["AKShareSupplementAdapterV1", "AKShareSupplementResultV1", "CrossProviderConflictError", "require_cross_provider_agreement"]
