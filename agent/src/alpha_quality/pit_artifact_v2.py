"""Content-addressed Parquet artifacts for validated A-share PIT snapshots."""

from __future__ import annotations

import hashlib
import math
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping

import duckdb
import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from src.alpha_foundry.artifacts import safe_artifact_path
from src.alpha_quality.pit_adapter_v1 import (
    AsharePITSnapshotRequestV1,
    AsharePITSourceBundleV1,
    AsharePITSourceManifestV1,
    RegisteredAsharePITAdapterV1,
)
from src.research_ledger.events.artifacts import (
    ArtifactConflictError,
    AtomicContentAddressedArtifactWriter,
    ContentAddressedArtifact,
    hash_artifact,
    validate_artifact_references,
)
from src.research_ledger.hash_utils import canonical_json, canonical_json_hash


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PIT_MANIFEST_MEDIA_TYPE = "application/vnd.vibe.ashare-pit-snapshot-v2+json"
PIT_TABLE_MEDIA_TYPE = "application/vnd.apache.parquet"
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "registration",
        "registry_hash",
        "request",
        "source_manifest",
        "evaluation_policy_event_hash",
        "calendar_hash",
        "evaluation_time_policy_hash",
        "split_plan_hash",
        "table_refs",
        "derived_evidence",
        "snapshot_hash",
    }
)
_TABLE_REF_KEYS = frozenset(
    {
        "table_name",
        "table_role",
        "semantic_hash",
        "row_count",
        "column_names",
        "index_name",
        "artifact_ref",
    }
)


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


def _require_hash(value: str, name: str) -> None:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical sha256 hash")


def _duckdb_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _canonical_scalar(value: Any) -> Any:
    if value is None or value is pd.NA or pd.isna(value):
        return None
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return _canonical_scalar(value.item())
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("PIT table contains Infinity")
        return value
    if isinstance(value, (bool, int, str)):
        return value
    return str(value)


def _semantic_table_hash(
    table: pd.DataFrame,
    *,
    table_name: str,
    table_role: str,
    index_name: str,
) -> str:
    digest = hashlib.sha256()
    logical_dtypes: list[str]
    if table_role == "numeric_frame":
        logical_dtypes = ["float64"] * len(table.columns)
    elif table_role == "bool_frame":
        logical_dtypes = ["bool"] * len(table.columns)
    elif table_role == "availability_frame":
        logical_dtypes = ["nullable_timestamp_string"] * len(table.columns)
    elif table_role == "security_master":
        logical_dtypes = ["nullable_string"] * len(table.columns)
    else:
        logical_dtypes = [
            "float64" if str(column) == "factor" else "nullable_string"
            for column in table.columns
        ]
    header = {
        "schema_version": "ashare_pit_table.v1",
        "table_name": table_name,
        "table_role": table_role,
        "index_name": index_name,
        "columns": [str(item) for item in table.columns],
        "logical_dtypes": logical_dtypes,
        "row_count": len(table),
    }
    digest.update(canonical_json(header).encode("utf-8"))
    digest.update(b"\n")
    for index, row in table.iterrows():
        payload = {
            "index": _canonical_scalar(index),
            "values": [_canonical_scalar(value) for value in row.tolist()],
        }
        digest.update(canonical_json(payload).encode("utf-8"))
        digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


def _normalize_table(
    table: pd.DataFrame,
    *,
    table_role: str,
    index_name: str,
) -> pd.DataFrame:
    if not isinstance(table, pd.DataFrame):
        raise TypeError("PIT table must be a DataFrame")
    result = table.copy(deep=True)
    if result.index.has_duplicates or result.columns.has_duplicates:
        raise ValueError("PIT table axes must be unique")
    result.columns = [str(item) for item in result.columns]
    if tuple(result.columns) != tuple(sorted(result.columns)):
        result = result.sort_index(axis=1)
    if table_role in {"numeric_frame", "bool_frame", "availability_frame"}:
        dates = pd.DatetimeIndex(pd.to_datetime(result.index))
        if dates.tz is not None:
            raise ValueError("PIT dated table index must be timezone-naive dates")
        canonical = pd.DatetimeIndex(
            [pd.Timestamp(value).normalize() for value in dates],
            name=index_name,
        )
        if canonical.has_duplicates or not canonical.is_monotonic_increasing:
            raise ValueError("PIT dated table index must be sorted and unique")
        result.index = canonical
    else:
        result.index = pd.Index([str(item) for item in result.index], name=index_name)
        result = result.sort_index()
    if table_role == "numeric_frame":
        if any(not pd.api.types.is_numeric_dtype(dtype) for dtype in result.dtypes):
            raise ValueError("PIT numeric table contains a non-numeric column")
        result = result.astype(float)
        if np.isinf(result.to_numpy()).any():
            raise ValueError("PIT numeric table contains Infinity")
    elif table_role == "bool_frame":
        if any(dtype != np.dtype(bool) for dtype in result.dtypes):
            raise ValueError("PIT bool table must use strict bool dtype")
        result = result.astype(bool)
    elif table_role == "availability_frame":
        result = result.map(
            lambda value: (
                None
                if value is None or pd.isna(value)
                else pd.Timestamp(value).isoformat()
            )
        )
    elif table_role == "security_master":
        expected = {"listing_date", "delisting_date", "record_available_at"}
        if set(result.columns) != expected:
            raise ValueError("PIT security master schema is invalid")
        result = result.map(
            lambda value: None if value is None or pd.isna(value) else str(value)
        )
    elif table_role == "corporate_actions":
        expected = {"symbol", "effective_date", "announced_at", "factor"}
        if set(result.columns) != expected:
            raise ValueError("PIT corporate action schema is invalid")
        for name in ("symbol", "effective_date", "announced_at"):
            result[name] = result[name].map(
                lambda value: None if value is None or pd.isna(value) else str(value)
            )
        result["factor"] = pd.to_numeric(result["factor"], errors="raise").astype(
            float
        )
        if np.isinf(result["factor"].to_numpy()).any():
            raise ValueError("PIT corporate action factor contains Infinity")
    elif table_role not in {"security_master", "corporate_actions"}:
        raise ValueError("unknown PIT table role")
    return result


@dataclass(frozen=True)
class AsharePITTableReferenceV1:
    table_name: str
    table_role: Literal[
        "numeric_frame",
        "bool_frame",
        "availability_frame",
        "security_master",
        "corporate_actions",
    ]
    semantic_hash: str
    row_count: int
    column_names: tuple[str, ...]
    index_name: str
    artifact_ref: Mapping[str, str]

    def __post_init__(self) -> None:
        if not self.table_name or not self.index_name:
            raise ValueError("PIT table identity is required")
        _require_hash(self.semantic_hash, "semantic_hash")
        if (
            isinstance(self.row_count, bool)
            or not isinstance(self.row_count, int)
            or self.row_count < 0
        ):
            raise ValueError("PIT table row_count is invalid")
        if self.column_names != tuple(sorted(set(self.column_names))):
            raise ValueError("PIT table columns must be sorted and unique")
        expected_ref_keys = {"relative_path", "artifact_hash", "media_type"}
        if set(self.artifact_ref) != expected_ref_keys:
            raise ValueError("PIT table artifact reference is not closed")
        if self.artifact_ref["media_type"] != PIT_TABLE_MEDIA_TYPE:
            raise ValueError("PIT table media type is invalid")
        _require_hash(str(self.artifact_ref["artifact_hash"]), "artifact_hash")
        object.__setattr__(self, "artifact_ref", MappingProxyType(dict(self.artifact_ref)))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AsharePITTableReferenceV1":
        if set(raw) != _TABLE_REF_KEYS or not isinstance(raw["artifact_ref"], Mapping):
            raise ValueError("PIT table reference is not closed")
        return cls(
            table_name=str(raw["table_name"]),
            table_role=str(raw["table_role"]),  # type: ignore[arg-type]
            semantic_hash=str(raw["semantic_hash"]),
            row_count=int(raw["row_count"]),
            column_names=tuple(str(item) for item in raw["column_names"]),
            index_name=str(raw["index_name"]),
            artifact_ref=dict(raw["artifact_ref"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_name": self.table_name,
            "table_role": self.table_role,
            "semantic_hash": self.semantic_hash,
            "row_count": self.row_count,
            "column_names": list(self.column_names),
            "index_name": self.index_name,
            "artifact_ref": dict(self.artifact_ref),
        }


class AsharePITParquetStoreV1:
    namespace = "ashare-pit-tables-v1"

    def __init__(self, root: str | Path, *, max_bytes: int = 2 * 1024**3) -> None:
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir() or max_bytes <= 0:
            raise ValueError("PIT Parquet artifact root/budget is invalid")
        self.max_bytes = max_bytes

    def write(
        self,
        table_name: str,
        table_role: str,
        table: pd.DataFrame,
        *,
        index_name: str,
    ) -> AsharePITTableReferenceV1:
        normalized = _normalize_table(
            table,
            table_role=table_role,
            index_name=index_name,
        )
        semantic_hash = _semantic_table_hash(
            normalized,
            table_name=table_name,
            table_role=table_role,
            index_name=index_name,
        )
        digest = semantic_hash.removeprefix("sha256:")
        relative = f"{self.namespace}/{digest[:2]}/{digest}.parquet"
        target = safe_artifact_path(self.root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = self.root / ".artifact-staging"
        staging.mkdir(parents=True, exist_ok=True)
        temporary = staging / f"{uuid.uuid4().hex}.parquet.tmp"
        materialized = normalized.reset_index()
        connection = duckdb.connect(database=":memory:")
        try:
            connection.register("pit_table", materialized)
            connection.execute(
                "COPY pit_table TO "
                + _duckdb_literal(str(temporary))
                + " (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        finally:
            connection.close()
        try:
            if temporary.stat().st_size > self.max_bytes:
                raise ValueError("PIT Parquet artifact exceeds byte budget")
            if not target.exists():
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    pass
            reference = {
                "relative_path": relative,
                "artifact_hash": hash_artifact(target),
                "media_type": PIT_TABLE_MEDIA_TYPE,
            }
            result = AsharePITTableReferenceV1(
                table_name=table_name,
                table_role=table_role,  # type: ignore[arg-type]
                semantic_hash=semantic_hash,
                row_count=len(normalized),
                column_names=tuple(str(item) for item in normalized.columns),
                index_name=index_name,
                artifact_ref=reference,
            )
            self.read(result)
            return result
        finally:
            temporary.unlink(missing_ok=True)

    def read(self, reference: AsharePITTableReferenceV1) -> pd.DataFrame:
        normalized_ref = validate_artifact_references(
            self.root,
            [reference.artifact_ref],
        )[0]
        expected_digest = reference.semantic_hash.removeprefix("sha256:")
        expected_relative = (
            f"{self.namespace}/{expected_digest[:2]}/{expected_digest}.parquet"
        )
        if normalized_ref["relative_path"] != expected_relative:
            raise ValueError("PIT Parquet artifact path is not content addressed")
        target = self.root.joinpath(*expected_relative.split("/"))
        if target.stat().st_size > self.max_bytes:
            raise ValueError("PIT Parquet artifact exceeds byte budget")
        connection = duckdb.connect(database=":memory:")
        try:
            materialized = connection.execute(
                "SELECT * FROM read_parquet("
                + _duckdb_literal(str(target))
                + ")"
            ).fetchdf()
        finally:
            connection.close()
        if reference.index_name not in materialized.columns:
            raise ValueError("PIT Parquet index column is missing")
        table = materialized.set_index(reference.index_name)
        table = _normalize_table(
            table,
            table_role=reference.table_role,
            index_name=reference.index_name,
        )
        if (
            len(table) != reference.row_count
            or tuple(table.columns) != reference.column_names
            or _semantic_table_hash(
                table,
                table_name=reference.table_name,
                table_role=reference.table_role,
                index_name=reference.index_name,
            )
            != reference.semantic_hash
        ):
            raise ValueError("PIT Parquet semantic content differs")
        return table


@dataclass(frozen=True)
class FrozenAsharePITSnapshotV2:
    registration: Mapping[str, Any]
    registry_hash: str
    request: Mapping[str, Any]
    source_manifest: Mapping[str, Any]
    evaluation_policy_event_hash: str
    calendar_hash: str
    evaluation_time_policy_hash: str
    split_plan_hash: str
    table_refs: tuple[Mapping[str, Any], ...]
    derived_evidence: Mapping[str, Any]
    snapshot_hash: str
    schema_version: Literal["frozen_ashare_pit_snapshot.v2"] = (
        "frozen_ashare_pit_snapshot.v2"
    )

    def __post_init__(self) -> None:
        if self.schema_version != "frozen_ashare_pit_snapshot.v2":
            raise ValueError("unsupported frozen A-share PIT snapshot")
        for name in (
            "registry_hash",
            "evaluation_policy_event_hash",
            "calendar_hash",
            "evaluation_time_policy_hash",
            "split_plan_hash",
            "snapshot_hash",
        ):
            _require_hash(str(getattr(self, name)), name)
        registration = RegisteredAsharePITAdapterV1(
            descriptor=_descriptor_from_dict(self.registration["descriptor"]),
            implementation_hash=str(self.registration["implementation_hash"]),
            authority_class=str(self.registration["authority_class"]),  # type: ignore[arg-type]
            registration_hash=str(self.registration["registration_hash"]),
        )
        if registration.to_dict() != _plain(self.registration):
            raise ValueError("PIT snapshot registration differs")
        request = _request_from_dict(self.request)
        source = _source_manifest_from_dict(self.source_manifest)
        if (
            source.adapter_id != request.adapter_id
            or source.request_hash != request.request_hash
            or registration.descriptor.adapter_id != request.adapter_id
        ):
            raise ValueError("PIT snapshot adapter/request/source identity differs")
        refs = tuple(AsharePITTableReferenceV1.from_dict(item) for item in self.table_refs)
        if not refs or tuple(item.table_name for item in refs) != tuple(
            sorted(set(item.table_name for item in refs))
        ):
            raise ValueError("PIT snapshot table references must be sorted and unique")
        if not isinstance(self.derived_evidence, Mapping):
            raise ValueError("PIT snapshot derived evidence must be an object")
        object.__setattr__(self, "registration", _freeze(registration.to_dict()))
        object.__setattr__(self, "request", _freeze(request.to_dict()))
        object.__setattr__(self, "source_manifest", _freeze(source.to_dict()))
        object.__setattr__(
            self,
            "table_refs",
            tuple(_freeze(item.to_dict()) for item in refs),
        )
        object.__setattr__(self, "derived_evidence", _freeze(self.derived_evidence))
        if self.snapshot_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("PIT snapshot hash differs from content")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "registration": _plain(self.registration),
            "registry_hash": self.registry_hash,
            "request": _plain(self.request),
            "source_manifest": _plain(self.source_manifest),
            "evaluation_policy_event_hash": self.evaluation_policy_event_hash,
            "calendar_hash": self.calendar_hash,
            "evaluation_time_policy_hash": self.evaluation_time_policy_hash,
            "split_plan_hash": self.split_plan_hash,
            "table_refs": [_plain(item) for item in self.table_refs],
            "derived_evidence": _plain(self.derived_evidence),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "snapshot_hash": self.snapshot_hash}

    @classmethod
    def build(
        cls,
        *,
        registration: RegisteredAsharePITAdapterV1,
        registry_hash: str,
        request: AsharePITSnapshotRequestV1,
        source_manifest: AsharePITSourceManifestV1,
        evaluation_policy_event_hash: str,
        calendar_hash: str,
        evaluation_time_policy_hash: str,
        split_plan_hash: str,
        table_refs: tuple[AsharePITTableReferenceV1, ...],
        derived_evidence: Mapping[str, Any],
    ) -> "FrozenAsharePITSnapshotV2":
        content = {
            "schema_version": "frozen_ashare_pit_snapshot.v2",
            "registration": registration.to_dict(),
            "registry_hash": registry_hash,
            "request": request.to_dict(),
            "source_manifest": source_manifest.to_dict(),
            "evaluation_policy_event_hash": evaluation_policy_event_hash,
            "calendar_hash": calendar_hash,
            "evaluation_time_policy_hash": evaluation_time_policy_hash,
            "split_plan_hash": split_plan_hash,
            "table_refs": [item.to_dict() for item in table_refs],
            "derived_evidence": _plain(derived_evidence),
        }
        return cls(
            registration=registration.to_dict(),
            registry_hash=registry_hash,
            request=request.to_dict(),
            source_manifest=source_manifest.to_dict(),
            evaluation_policy_event_hash=evaluation_policy_event_hash,
            calendar_hash=calendar_hash,
            evaluation_time_policy_hash=evaluation_time_policy_hash,
            split_plan_hash=split_plan_hash,
            table_refs=tuple(item.to_dict() for item in table_refs),
            derived_evidence=dict(derived_evidence),
            snapshot_hash=canonical_json_hash(content),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "FrozenAsharePITSnapshotV2":
        if set(raw) != _MANIFEST_KEYS:
            raise ValueError("PIT snapshot manifest is not closed")
        for name in (
            "registration",
            "request",
            "source_manifest",
            "derived_evidence",
        ):
            if not isinstance(raw[name], Mapping):
                raise ValueError(f"PIT snapshot {name} must be an object")
        if not isinstance(raw["table_refs"], (list, tuple)):
            raise ValueError("PIT snapshot table_refs must be a list")
        return cls(
            registration=dict(raw["registration"]),
            registry_hash=str(raw["registry_hash"]),
            request=dict(raw["request"]),
            source_manifest=dict(raw["source_manifest"]),
            evaluation_policy_event_hash=str(raw["evaluation_policy_event_hash"]),
            calendar_hash=str(raw["calendar_hash"]),
            evaluation_time_policy_hash=str(raw["evaluation_time_policy_hash"]),
            split_plan_hash=str(raw["split_plan_hash"]),
            table_refs=tuple(dict(item) for item in raw["table_refs"]),
            derived_evidence=dict(raw["derived_evidence"]),
            snapshot_hash=str(raw["snapshot_hash"]),
        )


def _descriptor_from_dict(raw: Mapping[str, Any]):
    from src.alpha_quality.pit_adapter_v1 import AsharePITAdapterDescriptorV1

    return AsharePITAdapterDescriptorV1(
        adapter_id=str(raw["adapter_id"]),
        provider=str(raw["provider"]),
        adapter_version=str(raw["adapter_version"]),
        market=str(raw["market"]),  # type: ignore[arg-type]
        calendar_id=str(raw["calendar_id"]),
        timezone=str(raw["timezone"]),  # type: ignore[arg-type]
        membership_dataset=str(raw["membership_dataset"]),
        security_master_dataset=str(raw["security_master_dataset"]),
        corporate_action_dataset=str(raw["corporate_action_dataset"]),
        trade_state_dataset=str(raw["trade_state_dataset"]),
        price_dataset=str(raw["price_dataset"]),
        availability_semantics=str(raw["availability_semantics"]),  # type: ignore[arg-type]
        adjustment_semantics=str(raw["adjustment_semantics"]),  # type: ignore[arg-type]
        schema_version=str(raw["schema_version"]),  # type: ignore[arg-type]
    )


def _request_from_dict(raw: Mapping[str, Any]) -> AsharePITSnapshotRequestV1:
    return AsharePITSnapshotRequestV1(
        adapter_id=str(raw["adapter_id"]),
        calendar_dates=tuple(str(item) for item in raw["calendar_dates"]),
        required_fields=tuple(str(item) for item in raw["required_fields"]),
        valid_cutoff=str(raw["valid_cutoff"]),
        evaluation_policy_event_hash=str(raw["evaluation_policy_event_hash"]),
        schema_version=str(raw["schema_version"]),  # type: ignore[arg-type]
    )


def _source_manifest_from_dict(raw: Mapping[str, Any]) -> AsharePITSourceManifestV1:
    return AsharePITSourceManifestV1(
        adapter_id=str(raw["adapter_id"]),
        request_hash=str(raw["request_hash"]),
        dataset_vintage=str(raw["dataset_vintage"]),
        source_as_of=str(raw["source_as_of"]),
        query_receipt_hashes=tuple(str(item) for item in raw["query_receipt_hashes"]),
        source_partition_hashes=tuple(
            str(item) for item in raw["source_partition_hashes"]
        ),
        schema_version=str(raw["schema_version"]),  # type: ignore[arg-type]
    )


class FrozenAsharePITSnapshotArtifactStoreV2:
    namespace = "ashare-pit-snapshot-v2"

    def __init__(self, root: str | Path) -> None:
        self.writer = AtomicContentAddressedArtifactWriter(
            root,
            max_bytes=32 * 1024 * 1024,
        )
        self.tables = AsharePITParquetStoreV1(root)

    def write_manifest(
        self,
        snapshot: FrozenAsharePITSnapshotV2,
    ) -> ContentAddressedArtifact:
        return self.writer.write_json(
            namespace=self.namespace,
            payload=snapshot.to_dict(),
            schema_version="frozen_ashare_pit_snapshot.v2",
            semantic_hash_field="snapshot_hash",
            closed_keys=_MANIFEST_KEYS,
            media_type=PIT_MANIFEST_MEDIA_TYPE,
        )

    def read_manifest(
        self,
        relative_path: str,
        *,
        expected_snapshot_hash: str,
        expected_blob_hash: str,
    ) -> FrozenAsharePITSnapshotV2:
        normalized = validate_artifact_references(
            self.writer.root,
            [
                {
                    "relative_path": relative_path,
                    "artifact_hash": expected_blob_hash,
                    "media_type": PIT_MANIFEST_MEDIA_TYPE,
                }
            ],
        )[0]
        digest = expected_snapshot_hash.removeprefix("sha256:")
        expected_relative = f"{self.namespace}/{digest[:2]}/{digest}.json"
        if normalized["relative_path"] != expected_relative:
            raise ValueError("PIT snapshot manifest path is not content addressed")
        target = self.writer.root.joinpath(*expected_relative.split("/"))
        import json

        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError("PIT snapshot manifest must be an object")
        snapshot = FrozenAsharePITSnapshotV2.from_dict(raw)
        if snapshot.snapshot_hash != expected_snapshot_hash:
            raise ValueError("PIT snapshot manifest identity differs")
        for raw_ref in snapshot.table_refs:
            self.tables.read(AsharePITTableReferenceV1.from_dict(raw_ref))
        return snapshot

    def write_bundle_tables(
        self,
        bundle: AsharePITSourceBundleV1,
        *,
        derived_masks: Mapping[str, pd.DataFrame] | None = None,
    ) -> tuple[AsharePITTableReferenceV1, ...]:
        entries: list[AsharePITTableReferenceV1] = []
        for field, frame in bundle.market_fields.items():
            entries.append(
                self.tables.write(
                    f"market:{field}",
                    "numeric_frame",
                    frame,
                    index_name="date",
                )
            )
        for field, frame in bundle.field_available_at.items():
            entries.append(
                self.tables.write(
                    f"availability:{field}",
                    "availability_frame",
                    frame,
                    index_name="date",
                )
            )
        for name, frame in bundle.trade_state_fields.items():
            role = "bool_frame" if name != "listing_age_days" else "numeric_frame"
            entries.append(
                self.tables.write(
                    f"trade:{name}",
                    role,
                    frame,
                    index_name="date",
                )
            )
        for name in ("daily_membership",):
            entries.append(
                self.tables.write(
                    f"mask:{name}",
                    "bool_frame",
                    getattr(bundle, name),
                    index_name="date",
                )
            )
        for name, frame in sorted((derived_masks or {}).items()):
            entries.append(
                self.tables.write(
                    f"derived_mask:{name}",
                    "bool_frame",
                    frame,
                    index_name="date",
                )
            )
        entries.append(
            self.tables.write(
                "security_master",
                "security_master",
                bundle.security_master,
                index_name="symbol",
            )
        )
        entries.append(
            self.tables.write(
                "corporate_actions",
                "corporate_actions",
                bundle.corporate_actions,
                index_name="action_id",
            )
        )
        return tuple(sorted(entries, key=lambda item: item.table_name))

    def read_bundle(
        self,
        snapshot: FrozenAsharePITSnapshotV2,
    ) -> AsharePITSourceBundleV1:
        tables = {
            reference.table_name: self.tables.read(reference)
            for reference in (
                AsharePITTableReferenceV1.from_dict(raw)
                for raw in snapshot.table_refs
            )
        }
        market = {
            name.removeprefix("market:"): table
            for name, table in tables.items()
            if name.startswith("market:")
        }
        availability = {
            name.removeprefix("availability:"): table
            for name, table in tables.items()
            if name.startswith("availability:")
        }
        trade_states = {
            name.removeprefix("trade:"): table
            for name, table in tables.items()
            if name.startswith("trade:")
        }
        return AsharePITSourceBundleV1(
            market_fields=dict(sorted(market.items())),
            field_available_at=dict(sorted(availability.items())),
            trade_state_fields=dict(sorted(trade_states.items())),
            daily_membership=tables["mask:daily_membership"],
            security_master=tables["security_master"],
            corporate_actions=tables["corporate_actions"],
            calendar_dates=tuple(str(item) for item in snapshot.request["calendar_dates"]),
            source_manifest=_source_manifest_from_dict(snapshot.source_manifest),
        )

    def read_derived_masks(
        self,
        snapshot: FrozenAsharePITSnapshotV2,
    ) -> Mapping[str, pd.DataFrame]:
        result: dict[str, pd.DataFrame] = {}
        for raw in snapshot.table_refs:
            reference = AsharePITTableReferenceV1.from_dict(raw)
            if reference.table_name.startswith("derived_mask:"):
                result[reference.table_name.removeprefix("derived_mask:")] = (
                    self.tables.read(reference)
                )
        return MappingProxyType(dict(sorted(result.items())))


__all__ = [
    "AsharePITParquetStoreV1",
    "AsharePITTableReferenceV1",
    "FrozenAsharePITSnapshotArtifactStoreV2",
    "FrozenAsharePITSnapshotV2",
    "PIT_MANIFEST_MEDIA_TYPE",
    "PIT_TABLE_MEDIA_TYPE",
]
