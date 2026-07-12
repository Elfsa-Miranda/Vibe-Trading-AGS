"""Strict immutable factor output content for the future partitioned artifact v2."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from src.research_ledger.hash_utils import canonical_json_hash


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _hash(value: str, name: str) -> None:
    if _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical sha256 hash")


def _axis_dates(index: pd.Index) -> tuple[str, ...]:
    if not isinstance(index, pd.DatetimeIndex) or index.tz is not None:
        raise ValueError("factor output index must be a timezone-naive DatetimeIndex")
    dates = tuple(value.date().isoformat() for value in index)
    if not index.equals(pd.DatetimeIndex(dates)):
        raise ValueError("factor output dates must be normalized midnight dates")
    if dates != tuple(sorted(set(dates))):
        raise ValueError("factor output dates must be strictly increasing and unique")
    return dates


def _axis_symbols(columns: pd.Index) -> tuple[str, ...]:
    if any(not isinstance(value, str) or not value for value in columns):
        raise ValueError("factor output symbols must be non-empty strings")
    symbols = tuple(columns)
    if symbols != tuple(sorted(set(symbols))):
        raise ValueError("factor output symbols must be sorted and unique")
    return symbols


def _require_exact_axes(
    frame: pd.DataFrame,
    *,
    dates: tuple[str, ...],
    symbols: tuple[str, ...],
    name: str,
) -> None:
    if _axis_dates(frame.index) != dates or _axis_symbols(frame.columns) != symbols:
        raise ValueError(f"{name} axes do not exactly match the frozen snapshot axes")


def _float_bytes(frame: pd.DataFrame) -> tuple[bytes, np.ndarray]:
    if any(not pd.api.types.is_numeric_dtype(dtype) for dtype in frame.dtypes):
        raise ValueError("factor output values must be numeric")
    values = frame.to_numpy(dtype=np.dtype("<f8"), copy=True)
    if np.isinf(values).any():
        raise ValueError("factor output contains Infinity")
    values[np.isnan(values)] = np.float64(np.nan)
    return values.tobytes(order="C"), values


def _mask_bytes(frame: pd.DataFrame, name: str) -> tuple[bytes, np.ndarray]:
    if any(dtype != np.dtype(bool) for dtype in frame.dtypes):
        raise ValueError(f"{name} must use strict bool dtype with no NA")
    values = frame.to_numpy(dtype=np.dtype(bool), copy=True)
    return values.astype(np.uint8, copy=False).tobytes(order="C"), values


def _bytes_hash(
    *,
    kind: str,
    dtype: str,
    shape: tuple[int, int],
    dates: tuple[str, ...],
    symbols: tuple[str, ...],
    payload: bytes,
) -> str:
    return canonical_json_hash(
        {
            "kind": kind,
            "dtype": dtype,
            "shape": list(shape),
            "dates": list(dates),
            "symbols": list(symbols),
            "payload_sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
        }
    )


@dataclass(frozen=True, init=False)
class FrozenFactorOutputV2:
    factor_spec_id: str
    canonical_formula: str
    snapshot_hash: str
    split_plan_hash: str
    evaluation_time_policy_hash: str
    executable_grammar_snapshot_hash: str
    dates: tuple[str, ...]
    symbols: tuple[str, ...]
    shape: tuple[int, int]
    factor_content_hash: str
    valid_mask_content_hash: str
    tradable_mask_content_hash: str
    universe_mask_content_hash: str
    metadata_hash: str
    storage_status: str
    schema_version: str
    _factor_bytes: bytes
    _valid_mask_bytes: bytes
    _tradable_mask_bytes: bytes
    _universe_mask_bytes: bytes

    @classmethod
    def build_fixture_content(
        cls,
        *,
        factor_spec_id: str,
        canonical_formula: str,
        snapshot_hash: str,
        split_plan_hash: str,
        evaluation_time_policy_hash: str,
        executable_grammar_snapshot_hash: str,
        expected_dates: tuple[str, ...],
        expected_symbols: tuple[str, ...],
        factor: pd.DataFrame,
        valid_mask: pd.DataFrame,
        tradable_mask: pd.DataFrame,
        universe_mask: pd.DataFrame,
        metadata: Mapping[str, str],
    ) -> "FrozenFactorOutputV2":
        for name, value in (
            ("factor_spec_id", factor_spec_id),
            ("snapshot_hash", snapshot_hash),
            ("split_plan_hash", split_plan_hash),
            ("evaluation_time_policy_hash", evaluation_time_policy_hash),
            ("executable_grammar_snapshot_hash", executable_grammar_snapshot_hash),
        ):
            _hash(value, name)
        if not isinstance(canonical_formula, str) or not canonical_formula:
            raise ValueError("canonical formula is required")
        if expected_dates != tuple(sorted(set(expected_dates))) or not expected_dates:
            raise ValueError("expected dates must be sorted, unique, and non-empty")
        if expected_symbols != tuple(sorted(set(expected_symbols))) or not expected_symbols:
            raise ValueError("expected symbols must be sorted, unique, and non-empty")
        if any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or not value
            for key, value in metadata.items()
        ):
            raise ValueError("factor output metadata must be a closed string mapping")
        metadata_plain = dict(sorted(metadata.items()))
        if set(metadata_plain) != {
            "backend_version",
            "field_semantics_hash",
            "transform_pipeline_hash",
        }:
            raise ValueError("factor output metadata fields are not closed")
        for key in ("field_semantics_hash", "transform_pipeline_hash"):
            _hash(metadata_plain[key], key)

        for name, frame in (
            ("factor", factor),
            ("valid_mask", valid_mask),
            ("tradable_mask", tradable_mask),
            ("universe_mask", universe_mask),
        ):
            if not isinstance(frame, pd.DataFrame) or frame.empty:
                raise ValueError(f"{name} must be a non-empty DataFrame")
            _require_exact_axes(
                frame,
                dates=expected_dates,
                symbols=expected_symbols,
                name=name,
            )

        factor_bytes, factor_values = _float_bytes(factor)
        valid_bytes, valid_values = _mask_bytes(valid_mask, "valid_mask")
        tradable_bytes, _ = _mask_bytes(tradable_mask, "tradable_mask")
        universe_bytes, _ = _mask_bytes(universe_mask, "universe_mask")
        if np.isnan(factor_values)[valid_values].any():
            raise ValueError("valid factor observations must be finite")
        shape = factor_values.shape
        if len(shape) != 2:
            raise ValueError("factor output must be two-dimensional")

        instance = object.__new__(cls)
        values: dict[str, Any] = {
            "factor_spec_id": factor_spec_id,
            "canonical_formula": canonical_formula,
            "snapshot_hash": snapshot_hash,
            "split_plan_hash": split_plan_hash,
            "evaluation_time_policy_hash": evaluation_time_policy_hash,
            "executable_grammar_snapshot_hash": executable_grammar_snapshot_hash,
            "dates": tuple(expected_dates),
            "symbols": tuple(expected_symbols),
            "shape": (int(shape[0]), int(shape[1])),
            "factor_content_hash": _bytes_hash(
                kind="factor",
                dtype="float64-le",
                shape=shape,
                dates=expected_dates,
                symbols=expected_symbols,
                payload=factor_bytes,
            ),
            "valid_mask_content_hash": _bytes_hash(
                kind="valid_mask",
                dtype="bool-u8",
                shape=shape,
                dates=expected_dates,
                symbols=expected_symbols,
                payload=valid_bytes,
            ),
            "tradable_mask_content_hash": _bytes_hash(
                kind="tradable_mask",
                dtype="bool-u8",
                shape=shape,
                dates=expected_dates,
                symbols=expected_symbols,
                payload=tradable_bytes,
            ),
            "universe_mask_content_hash": _bytes_hash(
                kind="universe_mask",
                dtype="bool-u8",
                shape=shape,
                dates=expected_dates,
                symbols=expected_symbols,
                payload=universe_bytes,
            ),
            "metadata_hash": canonical_json_hash(metadata_plain),
            "storage_status": "fixture_only_partition_artifact_unavailable",
            "schema_version": "frozen_factor_output_content.v2",
            "_factor_bytes": factor_bytes,
            "_valid_mask_bytes": valid_bytes,
            "_tradable_mask_bytes": tradable_bytes,
            "_universe_mask_bytes": universe_bytes,
        }
        for name, value in values.items():
            object.__setattr__(instance, name, value)
        return instance

    @property
    def decision_grade(self) -> bool:
        return False

    @property
    def content_hash(self) -> str:
        return canonical_json_hash(self.to_manifest_dict())

    def to_manifest_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "factor_spec_id": self.factor_spec_id,
            "canonical_formula": self.canonical_formula,
            "snapshot_hash": self.snapshot_hash,
            "split_plan_hash": self.split_plan_hash,
            "evaluation_time_policy_hash": self.evaluation_time_policy_hash,
            "executable_grammar_snapshot_hash": self.executable_grammar_snapshot_hash,
            "dates": list(self.dates),
            "symbols": list(self.symbols),
            "shape": list(self.shape),
            "factor_content_hash": self.factor_content_hash,
            "valid_mask_content_hash": self.valid_mask_content_hash,
            "tradable_mask_content_hash": self.tradable_mask_content_hash,
            "universe_mask_content_hash": self.universe_mask_content_hash,
            "metadata_hash": self.metadata_hash,
            "storage_status": self.storage_status,
            "decision_grade": False,
        }

    def factor_frame(self) -> pd.DataFrame:
        values = np.frombuffer(self._factor_bytes, dtype=np.dtype("<f8")).copy()
        return pd.DataFrame(
            values.reshape(self.shape),
            index=pd.DatetimeIndex(self.dates),
            columns=self.symbols,
        )

    def valid_mask_frame(self) -> pd.DataFrame:
        return self._mask_frame(self._valid_mask_bytes)

    def tradable_mask_frame(self) -> pd.DataFrame:
        return self._mask_frame(self._tradable_mask_bytes)

    def universe_mask_frame(self) -> pd.DataFrame:
        return self._mask_frame(self._universe_mask_bytes)

    def _mask_frame(self, payload: bytes) -> pd.DataFrame:
        values = np.frombuffer(payload, dtype=np.uint8).astype(bool, copy=True)
        return pd.DataFrame(
            values.reshape(self.shape),
            index=pd.DatetimeIndex(self.dates),
            columns=self.symbols,
        )

    def require_exact_frame_axes(self, frame: pd.DataFrame, *, name: str) -> None:
        _require_exact_axes(frame, dates=self.dates, symbols=self.symbols, name=name)

    def verify_content(self) -> None:
        expected_size = self.shape[0] * self.shape[1]
        if len(self._factor_bytes) != expected_size * 8:
            raise ValueError("frozen factor byte length is invalid")
        for name, payload in (
            ("valid_mask", self._valid_mask_bytes),
            ("tradable_mask", self._tradable_mask_bytes),
            ("universe_mask", self._universe_mask_bytes),
        ):
            if len(payload) != expected_size:
                raise ValueError(f"frozen {name} byte length is invalid")
        expected = {
            "factor_content_hash": _bytes_hash(
                kind="factor",
                dtype="float64-le",
                shape=self.shape,
                dates=self.dates,
                symbols=self.symbols,
                payload=self._factor_bytes,
            ),
            "valid_mask_content_hash": _bytes_hash(
                kind="valid_mask",
                dtype="bool-u8",
                shape=self.shape,
                dates=self.dates,
                symbols=self.symbols,
                payload=self._valid_mask_bytes,
            ),
            "tradable_mask_content_hash": _bytes_hash(
                kind="tradable_mask",
                dtype="bool-u8",
                shape=self.shape,
                dates=self.dates,
                symbols=self.symbols,
                payload=self._tradable_mask_bytes,
            ),
            "universe_mask_content_hash": _bytes_hash(
                kind="universe_mask",
                dtype="bool-u8",
                shape=self.shape,
                dates=self.dates,
                symbols=self.symbols,
                payload=self._universe_mask_bytes,
            ),
        }
        for name, value in expected.items():
            if getattr(self, name) != value:
                raise ValueError(f"frozen factor output {name} does not match bytes")


__all__ = ["FrozenFactorOutputV2"]
