"""Frozen train/valid panel source for independently replayable Retriever features."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import pandas as pd  # type: ignore[import-untyped]

from src.alpha_foundry.artifacts import safe_artifact_path, safe_artifact_write_json
from src.alpha_quality.flags import ResolvedAGSFlags
from src.research_ledger.data_snapshot import build_data_snapshot_v2
from src.research_ledger.events import EventDraft, ResearchEventEnvelope, ResearchEventStore
from src.research_ledger.events.artifacts import hash_artifact
from src.research_ledger.hash_utils import canonical_json, canonical_json_hash, redact_secrets


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_FIELDS = 128
_MAX_DATES = 100_000
_MAX_SYMBOLS = 20_000
_MAX_VALUES = 2_000_000
_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024


def _manifest_contract(manifest: Any) -> dict[str, Any]:
    base = manifest.base_manifest.to_dict()
    base.pop("generated_at", None)
    base.pop("snapshot_hash", None)
    return {
        "schema_version": "data_snapshot.v2",
        "base_manifest_content": base,
        "frame_content_hashes": dict(manifest.frame_content_hashes),
        "panel_content_hash": manifest.panel_content_hash,
        "snapshot_hash": manifest.snapshot_hash,
    }


def _normalize_frames(panel: Mapping[str, Any]) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    raw_frames = {
        str(name): frame
        for name, frame in panel.items()
        if isinstance(frame, pd.DataFrame)
    }
    if not raw_frames or len(raw_frames) > _MAX_FIELDS:
        raise ValueError("train/valid panel field count is out of bounds")
    normalized: dict[str, pd.DataFrame] = {}
    total_values = 0
    for name, frame in sorted(raw_frames.items()):
        if not name or frame.empty:
            raise ValueError("train/valid panel fields must be named and non-empty")
        dates = pd.DatetimeIndex(pd.to_datetime(frame.index))
        symbols = [str(value) for value in frame.columns]
        if (
            dates.has_duplicates
            or len(set(symbols)) != len(symbols)
            or len(dates) > _MAX_DATES
            or len(symbols) > _MAX_SYMBOLS
        ):
            raise ValueError("train/valid panel axes are invalid or exceed limits")
        numeric = frame.copy()
        numeric.index = dates
        numeric.columns = symbols
        try:
            numeric = numeric.astype(float)
        except (TypeError, ValueError) as exc:
            raise ValueError("train/valid panel accepts numeric fields only") from exc
        finite_or_missing = numeric.map(
            lambda value: pd.isna(value) or math.isfinite(float(value))
        )
        if not bool(finite_or_missing.to_numpy().all()):
            raise ValueError("train/valid panel rejects infinite values")
        numeric = numeric.sort_index().sort_index(axis=1)
        total_values += int(numeric.shape[0] * numeric.shape[1])
        normalized[name] = numeric
    if total_values > _MAX_VALUES:
        raise ValueError("train/valid panel exceeds its value budget")
    meta_raw = panel.get("_meta")
    meta = dict(meta_raw) if isinstance(meta_raw, Mapping) else {}
    return normalized, meta


def _frame_payload(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "dates": [pd.Timestamp(value).isoformat() for value in frame.index],
        "symbols": [str(value) for value in frame.columns],
        "values": [
            [None if pd.isna(value) else float(value) for value in row]
            for row in frame.to_numpy().tolist()
        ],
    }


def _frame_from_payload(raw: Mapping[str, Any]) -> pd.DataFrame:
    if set(raw) != {"dates", "symbols", "values"} or any(
        not isinstance(raw[name], list) for name in ("dates", "symbols", "values")
    ):
        raise ValueError("frozen train/valid frame has an invalid schema")
    dates = [str(value) for value in raw["dates"]]
    symbols = [str(value) for value in raw["symbols"]]
    values = raw["values"]
    if len(values) != len(dates) or any(
        not isinstance(row, list) or len(row) != len(symbols) for row in values
    ):
        raise ValueError("frozen train/valid frame shape is invalid")
    for row in values:
        for value in row:
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ValueError("frozen train/valid frame value is invalid")
    frame = pd.DataFrame(
        values,
        index=pd.DatetimeIndex(pd.to_datetime(dates)),
        columns=symbols,
        dtype=float,
    )
    normalized, _ = _normalize_frames({"field": frame})
    return normalized["field"]


@dataclass(frozen=True)
class FrozenTrainValidSnapshotV1:
    schema_version: str
    data_scope: str
    snapshot_contract: Mapping[str, Any]
    frames: Mapping[str, Mapping[str, Any]]
    metadata: Mapping[str, Any]
    snapshot_hash: str

    def __post_init__(self) -> None:
        if (
            self.schema_version != "frozen_train_valid_snapshot.v1"
            or self.data_scope != "train_valid"
            or _HASH_RE.fullmatch(self.snapshot_hash) is None
        ):
            raise ValueError("unsupported frozen train/valid snapshot")
        if not self.frames or len(self.frames) > _MAX_FIELDS:
            raise ValueError("frozen train/valid snapshot fields are invalid")
        if list(self.frames) != sorted(self.frames):
            raise ValueError("frozen train/valid snapshot fields must be sorted")
        frames = {
            name: _frame_from_payload(raw)
            for name, raw in self.frames.items()
        }
        panel: dict[str, Any] = {**frames, "_meta": dict(self.metadata)}
        base = self.snapshot_contract.get("base_manifest_content")
        if not isinstance(base, Mapping):
            raise ValueError("frozen train/valid snapshot contract is invalid")
        manifest = build_data_snapshot_v2(
            panel,
            universe=str(base.get("universe", "")),
            period=str(base.get("period", "")),
            source_config=dict(base.get("redacted_source_config", {})),
        )
        rebuilt = _manifest_contract(manifest)
        if (
            rebuilt != dict(self.snapshot_contract)
            or manifest.snapshot_hash != self.snapshot_hash
        ):
            raise ValueError("frozen train/valid snapshot cannot be rebuilt")
        object.__setattr__(self, "snapshot_contract", MappingProxyType(rebuilt))
        object.__setattr__(
            self,
            "frames",
            MappingProxyType(
                {
                    name: MappingProxyType(dict(raw))
                    for name, raw in self.frames.items()
                }
            ),
        )
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @classmethod
    def build(
        cls,
        panel: Mapping[str, Any],
        *,
        universe: str,
        period: str,
        source_config: Mapping[str, Any],
    ) -> "FrozenTrainValidSnapshotV1":
        if not universe or not period:
            raise ValueError("train/valid snapshot scope must be named")
        frames, metadata = _normalize_frames(panel)
        normalized_panel: dict[str, Any] = {**frames, "_meta": metadata}
        manifest = build_data_snapshot_v2(
            normalized_panel,
            universe,
            period,
            dict(source_config),
        )
        return cls(
            schema_version="frozen_train_valid_snapshot.v1",
            data_scope="train_valid",
            snapshot_contract=_manifest_contract(manifest),
            frames=MappingProxyType(
                {name: _frame_payload(frame) for name, frame in frames.items()}
            ),
            metadata=MappingProxyType(metadata),
            snapshot_hash=manifest.snapshot_hash,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "FrozenTrainValidSnapshotV1":
        expected = {
            "schema_version", "data_scope", "snapshot_contract", "frames",
            "metadata", "snapshot_hash",
        }
        if (
            set(raw) != expected
            or not isinstance(raw["snapshot_contract"], Mapping)
            or not isinstance(raw["frames"], Mapping)
            or not isinstance(raw["metadata"], Mapping)
            or any(not isinstance(value, Mapping) for value in raw["frames"].values())
        ):
            raise ValueError("frozen train/valid snapshot has an invalid schema")
        return cls(
            schema_version=str(raw["schema_version"]),
            data_scope=str(raw["data_scope"]),
            snapshot_contract=dict(raw["snapshot_contract"]),
            frames={str(name): dict(value) for name, value in raw["frames"].items()},
            metadata=dict(raw["metadata"]),
            snapshot_hash=str(raw["snapshot_hash"]),
        )

    def to_panel(self) -> dict[str, Any]:
        return {
            **{
                name: _frame_from_payload(raw)
                for name, raw in self.frames.items()
            },
            "_meta": dict(self.metadata),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "data_scope": self.data_scope,
            "snapshot_contract": dict(self.snapshot_contract),
            "frames": {
                name: dict(raw) for name, raw in self.frames.items()
            },
            "metadata": dict(self.metadata),
            "snapshot_hash": self.snapshot_hash,
        }


class FrozenTrainValidSnapshotArtifactStoreV1:
    media_type = "application/vnd.vibe.frozen-train-valid-snapshot-v1+json"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def write(self, snapshot: FrozenTrainValidSnapshotV1) -> dict[str, str]:
        payload = snapshot.to_dict()
        if canonical_json(payload) != canonical_json(redact_secrets(payload)):
            raise ValueError("frozen train/valid snapshot contains unsafe material")
        encoded = canonical_json(payload).encode("utf-8")
        if len(encoded) > _MAX_ARTIFACT_BYTES:
            raise ValueError("frozen train/valid snapshot exceeds byte budget")
        relative = self.relative_path(snapshot.snapshot_hash)
        target = safe_artifact_path(self.root, relative)
        if target.exists():
            if self.read(relative, snapshot.snapshot_hash) != snapshot:
                raise ValueError("frozen train/valid snapshot collision")
        else:
            safe_artifact_write_json(self.root, relative, payload)
        return {
            "relative_path": relative,
            "artifact_hash": hash_artifact(target),
            "media_type": self.media_type,
        }

    def read(
        self,
        relative_path: str,
        expected_snapshot_hash: str,
    ) -> FrozenTrainValidSnapshotV1:
        target = safe_artifact_path(self.root, relative_path)
        raw = target.read_bytes()
        if len(raw) > _MAX_ARTIFACT_BYTES:
            raise ValueError("frozen train/valid snapshot exceeds byte budget")

        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite snapshot value: {value}")

        def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate frozen snapshot key")
                result[key] = value
            return result

        payload = json.loads(
            raw.decode("utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
        if not isinstance(payload, Mapping):
            raise ValueError("frozen train/valid snapshot must be an object")
        if canonical_json(payload) != canonical_json(redact_secrets(payload)):
            raise ValueError("frozen train/valid snapshot contains unsafe material")
        snapshot = FrozenTrainValidSnapshotV1.from_dict(payload)
        if snapshot.snapshot_hash != expected_snapshot_hash:
            raise ValueError("frozen train/valid snapshot identity differs")
        if self.relative_path(snapshot.snapshot_hash) != relative_path.replace("\\", "/"):
            raise ValueError("frozen train/valid snapshot path is not content addressed")
        return snapshot

    @staticmethod
    def relative_path(snapshot_hash: str) -> str:
        if _HASH_RE.fullmatch(snapshot_hash) is None:
            raise ValueError("frozen train/valid snapshot hash is invalid")
        digest = snapshot_hash.removeprefix("sha256:")
        return f"train-valid-snapshots-v1/{digest[:2]}/{digest}.json"


@dataclass(frozen=True)
class RecordedTrainValidSnapshotV1:
    snapshot: FrozenTrainValidSnapshotV1
    event: ResearchEventEnvelope


class TrainValidSnapshotServiceV1:
    def __init__(self, store: ResearchEventStore, *, flags: ResolvedAGSFlags) -> None:
        required = (
            "VIBE_TRADING_ALPHA_FOUNDRY",
            "VIBE_TRADING_RESEARCH_EVENTS",
            "VIBE_TRADING_FACTOR_DAG",
            "VIBE_TRADING_PROCESS_MEMORY",
            "VIBE_TRADING_TOPOLOGY_RETRIEVER",
        )
        if any(not flags.enabled(name) for name in required):
            raise RuntimeError("train/valid snapshot capability is disabled")
        self.store = store
        self.artifacts = FrozenTrainValidSnapshotArtifactStoreV1(store.artifact_root)

    def freeze(
        self,
        panel: Mapping[str, Any],
        *,
        universe: str,
        period: str,
        source_config: Mapping[str, Any],
        run_id: str,
    ) -> RecordedTrainValidSnapshotV1:
        snapshot = FrozenTrainValidSnapshotV1.build(
            panel,
            universe=universe,
            period=period,
            source_config=source_config,
        )
        reference = self.artifacts.write(snapshot)
        contract = snapshot.snapshot_contract
        base = contract["base_manifest_content"]
        assert isinstance(base, Mapping)
        identifier = (
            "train-valid-snapshot-v1-"
            + snapshot.snapshot_hash.removeprefix("sha256:")[:24]
        )
        event = self.store.append_event(
            EventDraft(
                event_type="TrainValidDataSnapshotFrozen",
                entity_id=identifier,
                run_id=run_id,
                payload_schema_version="train_valid_data_snapshot_frozen.v1",
                idempotency_key="train-valid-snapshot-v1:" + snapshot.snapshot_hash,
                payload={
                    "snapshot_id": identifier,
                    "snapshot_hash": snapshot.snapshot_hash,
                    "data_scope": "train_valid",
                    "panel_content_hash": contract["panel_content_hash"],
                    "frame_content_hashes": contract["frame_content_hashes"],
                    "frame_names": list(snapshot.frames),
                    "source_config_hash": base["source_config_hash"],
                    "pit_contract_present": base["pit_contract_present"],
                    "survivorship_bias": base["survivorship_bias"],
                    "artifact_refs": [reference],
                },
            )
        )
        return RecordedTrainValidSnapshotV1(snapshot, event)


__all__ = [
    "FrozenTrainValidSnapshotArtifactStoreV1",
    "FrozenTrainValidSnapshotV1",
    "RecordedTrainValidSnapshotV1",
    "TrainValidSnapshotServiceV1",
]
