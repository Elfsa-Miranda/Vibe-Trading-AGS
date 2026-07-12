from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from src.alpha_quality.factor_output_v2 import FrozenFactorOutputV2
from src.research_ledger.hash_utils import canonical_json_hash


DATES = ("2020-01-01", "2020-01-02", "2020-01-03")
SYMBOLS = ("A", "B")


def _hash(name: str) -> str:
    return canonical_json_hash({"fixture": name})


def _frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    index = pd.DatetimeIndex(DATES)
    factor = pd.DataFrame(
        [[1.0, 2.0], [3.0, np.nan], [5.0, 6.0]],
        index=index,
        columns=SYMBOLS,
    )
    valid = pd.DataFrame(
        [[True, True], [True, False], [True, True]],
        index=index,
        columns=SYMBOLS,
        dtype=bool,
    )
    tradable = pd.DataFrame(True, index=index, columns=SYMBOLS, dtype=bool)
    universe = pd.DataFrame(True, index=index, columns=SYMBOLS, dtype=bool)
    return factor, valid, tradable, universe


def _build(
    *,
    factor: pd.DataFrame | None = None,
    valid: pd.DataFrame | None = None,
    tradable: pd.DataFrame | None = None,
    universe: pd.DataFrame | None = None,
) -> FrozenFactorOutputV2:
    defaults = _frames()
    return FrozenFactorOutputV2.build_fixture_content(
        factor_spec_id=_hash("factor"),
        canonical_formula="rank(close)",
        snapshot_hash=_hash("snapshot"),
        split_plan_hash=_hash("split"),
        evaluation_time_policy_hash=_hash("timing"),
        executable_grammar_snapshot_hash=_hash("grammar"),
        expected_dates=DATES,
        expected_symbols=SYMBOLS,
        factor=defaults[0] if factor is None else factor,
        valid_mask=defaults[1] if valid is None else valid,
        tradable_mask=defaults[2] if tradable is None else tradable,
        universe_mask=defaults[3] if universe is None else universe,
        metadata={
            "backend_version": "core_dataframe_backend.v1",
            "field_semantics_hash": _hash("fields"),
            "transform_pipeline_hash": _hash("transforms"),
        },
    )


def test_frozen_output_is_deeply_detached_from_input_and_returned_frames() -> None:
    factor, valid, tradable, universe = _frames()
    frozen = _build(
        factor=factor,
        valid=valid,
        tradable=tradable,
        universe=universe,
    )
    expected_hash = frozen.content_hash

    factor.iloc[0, 0] = 999.0
    valid.iloc[0, 0] = False
    returned = frozen.factor_frame()
    returned.iloc[0, 0] = -999.0

    assert frozen.factor_frame().iloc[0, 0] == 1.0
    assert bool(frozen.valid_mask_frame().iloc[0, 0]) is True
    assert frozen.content_hash == expected_hash
    assert frozen.decision_grade is False
    assert frozen.storage_status == "fixture_only_partition_artifact_unavailable"


def test_factor_output_hash_changes_for_one_value() -> None:
    first = _build()
    changed, valid, tradable, universe = _frames()
    changed.iloc[0, 0] = 1.5
    second = _build(
        factor=changed,
        valid=valid,
        tradable=tradable,
        universe=universe,
    )

    assert first.factor_content_hash != second.factor_content_hash
    assert first.content_hash != second.content_hash


def test_output_rejects_axis_mismatch_instead_of_intersection() -> None:
    factor, valid, tradable, universe = _frames()
    valid = valid.drop(index=valid.index[-1])
    with pytest.raises(ValueError, match="exactly match"):
        _build(factor=factor, valid=valid, tradable=tradable, universe=universe)

    frozen = _build()
    with pytest.raises(ValueError, match="exactly match"):
        frozen.require_exact_frame_axes(
            factor.drop(columns=["B"]),
            name="returns",
        )


@pytest.mark.parametrize("axis", ["duplicate_date", "unsorted_symbol"])
def test_output_rejects_noncanonical_axes(axis: str) -> None:
    factor, valid, tradable, universe = _frames()
    frames = [factor, valid, tradable, universe]
    if axis == "duplicate_date":
        for frame in frames:
            frame.index = pd.DatetimeIndex([DATES[0], DATES[0], DATES[2]])
    else:
        for frame in frames:
            frame.columns = ["B", "A"]

    with pytest.raises(ValueError, match="strictly increasing|sorted and unique"):
        _build(
            factor=factor,
            valid=valid,
            tradable=tradable,
            universe=universe,
        )


def test_output_rejects_na_or_nonstrict_boolean_masks() -> None:
    factor, valid, tradable, universe = _frames()
    valid = valid.astype("boolean")
    valid.iloc[0, 0] = pd.NA
    with pytest.raises(ValueError, match="strict bool"):
        _build(factor=factor, valid=valid, tradable=tradable, universe=universe)

    numeric = tradable.astype(float)
    with pytest.raises(ValueError, match="strict bool"):
        _build(
            factor=factor,
            valid=_frames()[1],
            tradable=numeric,
            universe=universe,
        )


def test_output_rejects_infinity_and_nan_marked_valid() -> None:
    factor, valid, tradable, universe = _frames()
    factor.iloc[0, 0] = np.inf
    with pytest.raises(ValueError, match="Infinity"):
        _build(factor=factor, valid=valid, tradable=tradable, universe=universe)

    factor, valid, tradable, universe = _frames()
    valid.iloc[1, 1] = True
    with pytest.raises(ValueError, match="must be finite"):
        _build(factor=factor, valid=valid, tradable=tradable, universe=universe)


def test_public_dataclass_replace_cannot_remint_frozen_content() -> None:
    frozen = _build()
    with pytest.raises(TypeError):
        replace(frozen, snapshot_hash=_hash("caller snapshot"))


def test_frozen_output_recomputes_byte_hash_before_consumption() -> None:
    frozen = _build()
    object.__setattr__(frozen, "_factor_bytes", b"\x00" * len(frozen._factor_bytes))
    with pytest.raises(ValueError, match="does not match bytes"):
        frozen.verify_content()


def test_frozen_output_rejects_intraday_axes_instead_of_truncating_them() -> None:
    index = pd.date_range("2020-01-01 12:00:00", periods=3, freq="D")
    columns = ["A", "B"]
    factor = pd.DataFrame(1.0, index=index, columns=columns)
    mask = pd.DataFrame(True, index=index, columns=columns, dtype=bool)

    with pytest.raises(ValueError, match="normalized midnight dates"):
        FrozenFactorOutputV2.build_fixture_content(
            factor_spec_id=_hash("factor"),
            canonical_formula="rank(close)",
            snapshot_hash=_hash("snapshot"),
            split_plan_hash=_hash("split"),
            evaluation_time_policy_hash=_hash("time"),
            executable_grammar_snapshot_hash=_hash("grammar"),
            expected_dates=tuple(value.date().isoformat() for value in index),
            expected_symbols=tuple(columns),
            factor=factor,
            valid_mask=mask,
            tradable_mask=mask,
            universe_mask=mask,
            metadata={
                "backend_version": "core_dataframe_backend.v1",
                "field_semantics_hash": _hash("fields"),
                "transform_pipeline_hash": _hash("transform"),
            },
        )
