from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.alpha_quality.evaluation_policy import (
    EvaluationTimePolicyV1,
    FrozenSplitPlanV1,
    FrozenTradingCalendarV1,
)
from src.alpha_quality.factor_output_v2 import FrozenFactorOutputV2
from src.alpha_quality.scorecard_v2 import (
    FixtureTrainValidDataCapabilityV1,
    compute_fixture_scorecard_v2,
)
from src.research_ledger.hash_utils import canonical_json_hash


def _hash(name: str) -> str:
    return canonical_json_hash({"fixture": name})


def _setup() -> tuple[
    FrozenTradingCalendarV1,
    EvaluationTimePolicyV1,
    FrozenSplitPlanV1,
    pd.DataFrame,
    tuple[str, ...],
]:
    start = date(2020, 1, 1)
    dates = tuple((start + timedelta(days=offset)).isoformat() for offset in range(40))
    calendar = FrozenTradingCalendarV1(
        dates=dates,
        source_artifact_hash=_hash("calendar"),
    )
    policy = EvaluationTimePolicyV1(
        return_horizons=(1,),
        execution_horizon=1,
        holding_period=1,
        rebalance_cadence=1,
    )
    plan = FrozenSplitPlanV1.build(
        calendar=calendar,
        time_policy=policy,
        train=(dates[0], dates[9]),
        valid=(dates[12], dates[21]),
        test=(dates[24], dates[33]),
    )
    symbols = ("A", "B", "C", "D", "E", "F")
    values = np.empty((40, 6), dtype=float)
    for row in range(40):
        for column in range(6):
            values[row, column] = 100.0 + row * (column + 1)
    full_close = pd.DataFrame(
        values,
        index=pd.DatetimeIndex(dates),
        columns=symbols,
    )
    return calendar, policy, plan, full_close, symbols


def _research_dates(
    calendar: FrozenTradingCalendarV1,
    plan: FrozenSplitPlanV1,
) -> tuple[str, ...]:
    train = calendar.dates[
        calendar.dates.index(plan.train.start) : calendar.dates.index(plan.train.end) + 1
    ]
    valid = calendar.dates[
        calendar.dates.index(plan.valid.start) : calendar.dates.index(plan.valid.end) + 1
    ]
    return train + valid


def _capability(
    full_close: pd.DataFrame,
) -> tuple[FixtureTrainValidDataCapabilityV1, tuple[str, ...]]:
    calendar, policy, plan, _, symbols = _setup()
    dates = _research_dates(calendar, plan)
    close = full_close.loc[pd.DatetimeIndex(dates), list(symbols)]
    return (
        FixtureTrainValidDataCapabilityV1.from_fixture(
            snapshot_hash=_hash("snapshot"),
            calendar=calendar,
            split_plan=plan,
            time_policy=policy,
            close=close,
            symbols=symbols,
        ),
        dates,
    )


def _output(
    capability: FixtureTrainValidDataCapabilityV1,
    *,
    universe: pd.DataFrame | None = None,
) -> FrozenFactorOutputV2:
    index = pd.DatetimeIndex(capability.dates)
    factor = pd.DataFrame(
        np.tile(np.arange(len(capability.symbols), dtype=float), (len(index), 1)),
        index=index,
        columns=capability.symbols,
    )
    valid = pd.DataFrame(True, index=index, columns=capability.symbols, dtype=bool)
    tradable = valid.copy()
    universe_mask = valid.copy() if universe is None else universe
    return FrozenFactorOutputV2.build_fixture_content(
        factor_spec_id=_hash("factor"),
        canonical_formula="rank(close)",
        snapshot_hash=capability.snapshot_hash,
        split_plan_hash=capability.split_plan_hash,
        evaluation_time_policy_hash=capability.time_policy.policy_hash,
        executable_grammar_snapshot_hash=_hash("executable grammar"),
        expected_dates=capability.dates,
        expected_symbols=capability.symbols,
        factor=factor,
        valid_mask=valid,
        tradable_mask=tradable,
        universe_mask=universe_mask,
        metadata={
            "backend_version": "core_dataframe_backend.v1",
            "field_semantics_hash": _hash("fields"),
            "transform_pipeline_hash": _hash("transform"),
        },
    )


def test_train_valid_capability_rejects_test_or_gap_dates() -> None:
    calendar, policy, plan, full_close, symbols = _setup()

    with pytest.raises(ValueError, match="rejects test"):
        FixtureTrainValidDataCapabilityV1.from_fixture(
            snapshot_hash=_hash("snapshot"),
            calendar=calendar,
            split_plan=plan,
            time_policy=policy,
            close=full_close,
            symbols=symbols,
        )

    capability, _ = _capability(full_close)
    assert not hasattr(capability, "test_window")
    assert plan.test.start not in repr(vars(capability))
    assert plan.test.end not in repr(vars(capability))
    assert all(value < plan.test.start for value in capability.dates)


def test_discovery_execution_is_invariant_to_any_test_price_change() -> None:
    _, _, plan, full_close, _ = _setup()
    first_capability, _ = _capability(full_close)
    first = compute_fixture_scorecard_v2(
        factor_output=_output(first_capability),
        capability=first_capability,
    )

    changed = full_close.copy()
    test_rows = changed.index >= pd.Timestamp(plan.test.start)
    changed.loc[test_rows] = changed.loc[test_rows] * 10_000.0
    second_capability, _ = _capability(changed)
    second = compute_fixture_scorecard_v2(
        factor_output=_output(second_capability),
        capability=second_capability,
    )

    assert first.to_dict() == second.to_dict()
    assert first.scorecard_hash == second.scorecard_hash
    assert first.execution_evidence_status == "separate_producer_required"
    assert "execution" not in first.to_dict()


def test_train_metric_is_invariant_to_valid_price_changes() -> None:
    _, _, plan, full_close, _ = _setup()
    first_capability, _ = _capability(full_close)
    first = compute_fixture_scorecard_v2(
        factor_output=_output(first_capability),
        capability=first_capability,
    )
    changed = full_close.copy()
    valid_rows = (changed.index >= pd.Timestamp(plan.valid.start)) & (
        changed.index <= pd.Timestamp(plan.valid.end)
    )
    changed.loc[valid_rows, "F"] *= 100.0
    second_capability, _ = _capability(changed)
    second = compute_fixture_scorecard_v2(
        factor_output=_output(second_capability),
        capability=second_capability,
    )

    first_train = [item.to_dict() for item in first.predictive if item.split == "train"]
    second_train = [item.to_dict() for item in second.predictive if item.split == "train"]
    assert first_train == second_train


def test_horizon_boundary_rows_are_purged_and_never_cross_split() -> None:
    _, _, _, full_close, _ = _setup()
    capability, _ = _capability(full_close)
    scorecard = compute_fixture_scorecard_v2(
        factor_output=_output(capability),
        capability=capability,
    )

    train = next(item for item in scorecard.predictive if item.split == "train")
    valid = next(item for item in scorecard.predictive if item.split == "valid")
    assert len(train.signal_dates) == capability.train_window.eligible_signal_count == 8
    assert len(valid.signal_dates) == capability.valid_window.eligible_signal_count == 8
    assert train.signal_dates[-1] == capability.train_window.eligible_signal_end
    assert valid.signal_dates[-1] == capability.valid_window.eligible_signal_end


def test_discovery_coverage_contains_no_test_dates() -> None:
    _, _, plan, full_close, _ = _setup()
    capability, _ = _capability(full_close)
    universe = pd.DataFrame(
        True,
        index=pd.DatetimeIndex(capability.dates),
        columns=capability.symbols,
        dtype=bool,
    )
    universe.loc[pd.Timestamp(capability.train_window.start)] = False
    output = _output(capability, universe=universe)
    scorecard = compute_fixture_scorecard_v2(
        factor_output=output,
        capability=capability,
    )

    all_dates = {
        value
        for item in scorecard.coverage
        for value, _ in item.by_date
    }
    train_coverage = next(item for item in scorecard.coverage if item.split == "train")
    assert dict(train_coverage.by_date)[capability.train_window.start] is None
    assert all(value < plan.test.start for value in all_dates)


def test_all_scorecard_components_share_the_same_authorized_scope() -> None:
    _, _, _, full_close, _ = _setup()
    capability, _ = _capability(full_close)
    scorecard = compute_fixture_scorecard_v2(
        factor_output=_output(capability),
        capability=capability,
    )

    assert {item.split for item in scorecard.predictive} == {"train", "valid"}
    assert {item.split for item in scorecard.coverage} == {"train", "valid"}
    assert scorecard.execution_evidence_status == "separate_producer_required"
    assert scorecard.decision_grade is False
    assert scorecard.authority_status == "fixture_only_not_decision_evidence"
    parameters = inspect.signature(compute_fixture_scorecard_v2).parameters
    assert "panel" not in parameters
    assert "test" not in parameters
    assert "score" not in parameters
    assert "decision" not in parameters


def test_scorecard_rejects_output_from_another_snapshot() -> None:
    _, _, _, full_close, _ = _setup()
    capability, _ = _capability(full_close)
    output = _output(capability)
    object.__setattr__(output, "snapshot_hash", _hash("other snapshot"))

    with pytest.raises(ValueError, match="snapshot differ"):
        compute_fixture_scorecard_v2(
            factor_output=output,
            capability=capability,
        )


def test_scorecard_recomputes_capability_bytes_before_metrics() -> None:
    _, _, _, full_close, _ = _setup()
    capability, _ = _capability(full_close)
    output = _output(capability)
    object.__setattr__(
        capability,
        "_close_bytes",
        b"\x00" * len(capability._close_bytes),
    )

    with pytest.raises(ValueError, match="close must be positive|does not match bytes"):
        compute_fixture_scorecard_v2(
            factor_output=output,
            capability=capability,
        )


def test_train_valid_capability_rejects_nonpositive_available_prices() -> None:
    calendar, policy, plan, full_close, symbols = _setup()
    dates = _research_dates(calendar, plan)
    close = full_close.loc[pd.DatetimeIndex(dates), list(symbols)].copy()
    close.iloc[0, 0] = 0.0

    with pytest.raises(ValueError, match="must be positive"):
        FixtureTrainValidDataCapabilityV1.from_fixture(
            snapshot_hash=_hash("snapshot"),
            calendar=calendar,
            split_plan=plan,
            time_policy=policy,
            close=close,
            symbols=symbols,
        )


def test_train_valid_capability_rejects_intraday_axes() -> None:
    calendar, policy, plan, full_close, symbols = _setup()
    dates = _research_dates(calendar, plan)
    close = full_close.loc[pd.DatetimeIndex(dates), list(symbols)].copy()
    close.index = close.index + pd.Timedelta(hours=12)

    with pytest.raises(ValueError, match="rejects test, gap, missing, or reordered"):
        FixtureTrainValidDataCapabilityV1.from_fixture(
            snapshot_hash=_hash("snapshot"),
            calendar=calendar,
            split_plan=plan,
            time_policy=policy,
            close=close,
            symbols=symbols,
        )


def test_train_valid_capability_rejects_unknown_runtime_split() -> None:
    _, _, _, full_close, _ = _setup()
    capability, _ = _capability(full_close)

    with pytest.raises(ValueError, match="must be train or valid"):
        capability.signal_dates("test")  # type: ignore[arg-type]


def test_train_valid_capability_rebuilds_and_rejects_forged_split_fields() -> None:
    calendar, policy, plan, full_close, symbols = _setup()
    dates = _research_dates(calendar, plan)
    close = full_close.loc[pd.DatetimeIndex(dates), list(symbols)]
    forged_train = replace(
        plan.train,
        eligible_signal_end=plan.train.end,
        eligible_signal_count=plan.train.calendar_day_count,
    )
    forged_plan = replace(plan, train=forged_train)

    with pytest.raises(ValueError, match="calendar-derived policy"):
        FixtureTrainValidDataCapabilityV1.from_fixture(
            snapshot_hash=_hash("snapshot"),
            calendar=calendar,
            split_plan=forged_plan,
            time_policy=policy,
            close=close,
            symbols=symbols,
        )
