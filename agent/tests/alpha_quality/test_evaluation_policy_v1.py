from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.alpha_quality.evaluation_policy import (
    EvaluationTimePolicyV1,
    FrozenSplitPlanV1,
    FrozenTradingCalendarV1,
)
from src.research_ledger.events.artifacts import AtomicContentAddressedArtifactWriter
from src.research_ledger.hash_utils import canonical_json_hash


def _dates(count: int = 60) -> tuple[str, ...]:
    start = date(2020, 1, 1)
    return tuple((start + timedelta(days=offset)).isoformat() for offset in range(count))


def _calendar() -> FrozenTradingCalendarV1:
    return FrozenTradingCalendarV1(
        dates=_dates(),
        source_artifact_hash=canonical_json_hash({"calendar": "fixture"}),
    )


def _policy() -> EvaluationTimePolicyV1:
    return EvaluationTimePolicyV1(
        return_horizons=(5, 1, 5),
        execution_horizon=5,
        holding_period=5,
        rebalance_cadence=1,
    )


def _plan() -> tuple[FrozenTradingCalendarV1, EvaluationTimePolicyV1, FrozenSplitPlanV1]:
    calendar = _calendar()
    policy = _policy()
    dates = calendar.dates
    plan = FrozenSplitPlanV1.build(
        calendar=calendar,
        time_policy=policy,
        train=(dates[0], dates[9]),
        valid=(dates[16], dates[25]),
        test=(dates[32], dates[41]),
    )
    return calendar, policy, plan


def test_timing_policy_freezes_explicit_execution_semantics() -> None:
    policy = _policy()

    assert policy.return_horizons == (1, 5)
    assert policy.execution_horizon == 5
    assert policy.holding_period == 5
    assert policy.rebalance_cadence == 1
    assert policy.entry_lag_trading_days == 1
    assert policy.maximum_outcome_offset == 6
    assert policy.execution_return_policy == "signal_date_forward_return.v2"
    assert policy.to_dict()["policy_hash"] == policy.policy_hash


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"return_horizons": (), "execution_horizon": 1}, "return horizons"),
        ({"return_horizons": (1, 5), "execution_horizon": 3}, "execution horizon"),
        (
            {
                "return_horizons": (1, 5),
                "execution_horizon": 5,
                "holding_period": 4,
            },
            "holding period",
        ),
        (
            {
                "return_horizons": (1,),
                "execution_horizon": 1,
                "order_lag_trading_days": 2,
                "entry_lag_trading_days": 1,
            },
            "order lag",
        ),
    ],
)
def test_timing_policy_rejects_implicit_or_inconsistent_configuration(
    kwargs: dict[str, object],
    match: str,
) -> None:
    values: dict[str, object] = {
        "return_horizons": (1,),
        "execution_horizon": 1,
        "holding_period": 1,
        "rebalance_cadence": 1,
    }
    values.update(kwargs)
    with pytest.raises(ValueError, match=match):
        EvaluationTimePolicyV1(**values)  # type: ignore[arg-type]


def test_split_plan_derives_purge_embargo_and_outcome_containment() -> None:
    calendar, policy, plan = _plan()

    assert plan.purge_trading_days == policy.maximum_outcome_offset == 6
    assert plan.embargo_trading_days == policy.maximum_outcome_offset
    assert plan.signal_dates("train", calendar=calendar) == calendar.dates[0:4]
    assert plan.signal_dates("valid", calendar=calendar) == calendar.dates[16:20]
    assert plan.signal_dates("test", calendar=calendar) == calendar.dates[32:36]
    assert plan.outcome_dates(
        "valid",
        calendar.dates[19],
        horizon=5,
        calendar=calendar,
        time_policy=policy,
    ) == (calendar.dates[19], calendar.dates[20], calendar.dates[25])


def test_split_plan_rejects_reversed_overlap_and_insufficient_embargo() -> None:
    calendar = _calendar()
    policy = _policy()
    dates = calendar.dates

    with pytest.raises(ValueError, match="exactly two bounds"):
        FrozenSplitPlanV1.build(
            calendar=calendar,
            time_policy=policy,
            train=(dates[0],),  # type: ignore[arg-type]
            valid=(dates[16], dates[25]),
            test=(dates[32], dates[41]),
        )
    with pytest.raises(ValueError, match="reversed"):
        FrozenSplitPlanV1.build(
            calendar=calendar,
            time_policy=policy,
            train=(dates[9], dates[0]),
            valid=(dates[16], dates[25]),
            test=(dates[32], dates[41]),
        )
    with pytest.raises(ValueError, match="ordered"):
        FrozenSplitPlanV1.build(
            calendar=calendar,
            time_policy=policy,
            train=(dates[0], dates[12]),
            valid=(dates[10], dates[25]),
            test=(dates[32], dates[41]),
        )
    with pytest.raises(ValueError, match="embargo"):
        FrozenSplitPlanV1.build(
            calendar=calendar,
            time_policy=policy,
            train=(dates[0], dates[9]),
            valid=(dates[15], dates[24]),
            test=(dates[31], dates[40]),
        )


def test_calendar_is_strict_and_source_bound() -> None:
    source = canonical_json_hash({"calendar": "fixture"})
    with pytest.raises(ValueError, match="strictly increasing"):
        FrozenTradingCalendarV1(
            dates=("2020-01-01", "2020-01-01", "2020-01-02"),
            source_artifact_hash=source,
        )
    with pytest.raises(ValueError, match="ISO calendar date"):
        FrozenTradingCalendarV1(
            dates=("2020-1-1", "2020-01-02", "2020-01-03"),
            source_artifact_hash=source,
        )


def test_plan_hash_changes_for_calendar_timing_or_content_changes() -> None:
    calendar, policy, plan = _plan()
    changed_policy = EvaluationTimePolicyV1(
        return_horizons=(1,),
        execution_horizon=1,
        holding_period=1,
        rebalance_cadence=1,
    )
    changed_plan = FrozenSplitPlanV1.build(
        calendar=calendar,
        time_policy=changed_policy,
        train=(calendar.dates[0], calendar.dates[9]),
        valid=(calendar.dates[12], calendar.dates[21]),
        test=(calendar.dates[24], calendar.dates[33]),
    )

    assert changed_plan.plan_hash != plan.plan_hash
    assert replace(plan, purge_trading_days=7).plan_hash != plan.plan_hash
    changed_calendar = replace(
        calendar,
        source_artifact_hash=canonical_json_hash({"calendar": "another source"}),
    )
    assert changed_calendar.calendar_hash != calendar.calendar_hash
    with pytest.raises(ValueError, match="calendar hash mismatch"):
        plan.signal_dates("train", calendar=changed_calendar)


def test_calendar_policy_and_plan_use_atomic_content_addressing(tmp_path: Path) -> None:
    calendar, policy, plan = _plan()
    writer = AtomicContentAddressedArtifactWriter(tmp_path)
    contracts = (
        (
            "evaluation_calendar",
            calendar.to_dict(),
            "frozen_trading_calendar.v1",
            "calendar_hash",
        ),
        (
            "evaluation_policy",
            policy.to_dict(),
            "evaluation_time_policy.v1",
            "policy_hash",
        ),
        ("split_plan", plan.to_dict(), "frozen_split_plan.v1", "plan_hash"),
    )

    for namespace, payload, schema, hash_field in contracts:
        artifact = writer.write_json(
            namespace=namespace,
            payload=payload,
            schema_version=schema,
            semantic_hash_field=hash_field,
            closed_keys=frozenset(payload),
            media_type=f"application/vnd.vibe.{schema}+json",
        )
        assert (tmp_path / artifact.relative_path).is_file()
