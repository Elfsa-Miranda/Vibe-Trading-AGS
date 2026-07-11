from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from src.alpha_foundry.candidate_pool import make_candidate
from src.alpha_foundry.dsl.identity import FactorSpecSemantics
from src.alpha_foundry.search import AlphaFoundrySearch
from src.alpha_foundry.search_lifecycle import (
    CandidateEvaluationInfrastructureError,
    CandidateEvaluationTimeout,
    EventSourcedSearchLifecycle,
    SearchEvaluationOutcome,
)
from src.alpha_foundry.seed_bank import AlphaSeed, SeedBank
from src.alpha_quality.flags import ResolvedAGSFlags
from src.research_ledger.events import ResearchEventStore
from src.research_ledger.hash_utils import canonical_json_hash


def _flags() -> ResolvedAGSFlags:
    return ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_ALPHA_FOUNDRY": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
            "VIBE_TRADING_FACTOR_DAG": "1",
        }
    )


def _semantics() -> FactorSpecSemantics:
    digest = canonical_json_hash({"fixture": "search-lifecycle"})
    return FactorSpecSemantics(
        transform_pipeline_hash=digest,
        field_semantics={
            field: "pit_adjusted_eod"
            for field in ("close", "high", "low", "open", "volume")
        },
        signal_time="close_t",
        order_time="after_close_t",
        entry_price_time="open_t_plus_1",
        execution_lag=1,
        return_horizon=1,
        universe_mask_hash=digest,
        tradability_mask_hash=digest,
    )


class _OnePerSeedMutator:
    def mutate(self, seed: AlphaSeed):
        return [make_candidate(seed.seed_id, seed.formula, mutation="fixture")]


class _Evaluator:
    def __init__(self, store: ResearchEventStore) -> None:
        self.store = store
        self.calls: list[str] = []

    def evaluate(self, *, candidate, factor_spec_id, trial_id, run_id):
        self.calls.append(candidate.formula)
        assert self.store.query_events(event_type="TrialStarted", entity_id=trial_id)
        assert not self.store.query_events(event_type="TrialTerminated", entity_id=trial_id)
        if candidate.formula == "rank(volume)":
            raise CandidateEvaluationTimeout("bounded evaluation timeout")
        if candidate.formula == "rank(high)":
            raise RuntimeError("deterministic evaluator failure")
        if candidate.formula == "rank(low)":
            raise CandidateEvaluationInfrastructureError("fixture worker unavailable")
        status = {
            "rank(close)": "success",
            "zscore(close)": "reject",
            "rank(open)": "skip",
        }[candidate.formula]
        return SearchEvaluationOutcome(
            status=status,
            decision=(
                "candidate_zoo" if status == "success"
                else "reject" if status == "reject" else "none"
            ),
            scorecard_hash=(
                canonical_json_hash({"trial_id": trial_id})
                if status in {"success", "reject"} else None
            ),
            reason_codes=("FROZEN_FIXTURE_RESULT",),
            metadata={"source": "deterministic_train_valid_fixture"},
        )


def _store(tmp_path: Path) -> ResearchEventStore:
    return ResearchEventStore(
        tmp_path / "events.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=_flags(),
        code_version="search-lifecycle-test",
    )


def _search(tmp_path: Path):
    store = _store(tmp_path)
    evaluator = _Evaluator(store)
    lifecycle = EventSourcedSearchLifecycle(
        store=store,
        flags=_flags(),
        semantics=_semantics(),
        evaluator=evaluator,
        data_snapshot_hash=canonical_json_hash({"snapshot": "train-valid"}),
    )
    formulas = (
        "rank(close)",
        "zscore(close)",
        "rank(open)",
        "future(close, 1)",
        "rank(close)",
        "rank(volume)",
        "rank(high)",
        "rank(low)",
    )
    search = AlphaFoundrySearch(
        seed_bank=SeedBank(
            [
                AlphaSeed(seed_id=f"seed-{index}", formula=formula, source="fixture")
                for index, formula in enumerate(formulas)
            ]
        ),
        mutator=_OnePerSeedMutator(),
        max_candidates=len(formulas),
        trial_budget=len(formulas),
        lifecycle=lifecycle,
        run_id="typed-search-run",
    )
    return store, evaluator, search


def test_production_search_counts_every_typed_terminal_outcome(tmp_path: Path) -> None:
    store, evaluator, search = _search(tmp_path)

    result = search.generate()

    assert result.n_candidates_seen == 8
    assert dict(result.terminal_status_counts) == {
        "duplicate": 1,
        "error": 1,
        "infrastructure_failure": 1,
        "invalid": 1,
        "reject": 1,
        "skip": 1,
        "success": 1,
        "timeout": 1,
    }
    assert Counter(attempt.status for attempt in result.attempts) == Counter(
        dict(result.terminal_status_counts)
    )
    assert store.lifecycle_summary().started_count == 8
    assert store.lifecycle_summary().terminal_count == 8
    assert store.lifecycle_summary().open_trial_ids == ()
    assert len(store.query_events(event_type="EvaluationRecorded")) == 2
    assert len(store.query_events(event_type="GenerationFailureRecorded")) == 4
    assert evaluator.calls == [
        "rank(close)", "zscore(close)", "rank(open)",
        "rank(volume)", "rank(high)", "rank(low)",
    ]
    assert store.verify_chain()
    assert store.replay().open_trial_ids == ()


def test_exact_search_retry_reuses_terminals_without_evaluation(tmp_path: Path) -> None:
    store, evaluator, search = _search(tmp_path)
    first = search.generate()
    calls = tuple(evaluator.calls)

    second = search.generate()

    assert second.attempts == first.attempts
    assert tuple(evaluator.calls) == calls
    assert store.lifecycle_summary().started_count == 8
    assert store.lifecycle_summary().terminal_count == 8


def test_invalid_artifact_reference_terminates_as_infrastructure_failure(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    class _BadArtifactEvaluator:
        def evaluate(self, **kwargs):
            return SearchEvaluationOutcome(
                status="success",
                decision="candidate_zoo",
                scorecard_hash=canonical_json_hash({"scorecard": "bad-ref"}),
                reason_codes=("FIXTURE",),
                artifact_refs=(
                    {
                        "relative_path": "missing.json",
                        "artifact_hash": canonical_json_hash({"missing": True}),
                        "media_type": "application/json",
                    },
                ),
            )

    lifecycle = EventSourcedSearchLifecycle(
        store=store,
        flags=_flags(),
        semantics=_semantics(),
        evaluator=_BadArtifactEvaluator(),
        data_snapshot_hash=canonical_json_hash({"snapshot": "bad-ref"}),
    )
    search = AlphaFoundrySearch(
        seed_bank=SeedBank([AlphaSeed("seed", "rank(close)", "fixture")]),
        mutator=_OnePerSeedMutator(),
        lifecycle=lifecycle,
        run_id="bad-artifact-run",
    )

    result = search.generate()

    assert dict(result.terminal_status_counts) == {"infrastructure_failure": 1}
    assert not store.query_events(event_type="EvaluationRecorded")
    terminal = store.query_events(event_type="TrialTerminated")[0]
    assert terminal.payload["decision"] == "research_only"
    assert terminal.payload["evaluation_event_hash"] is None


def test_lifecycle_requires_parent_and_child_capabilities(tmp_path: Path) -> None:
    flags = ResolvedAGSFlags.from_settings(
        {"VIBE_TRADING_AGS_ENABLED": "1", "VIBE_TRADING_RESEARCH_EVENTS": "1"}
    )
    store = ResearchEventStore(
        tmp_path / "disabled.sqlite",
        artifact_root=tmp_path / "disabled-artifacts",
        flags=flags,
        code_version="disabled-lifecycle",
    )
    with pytest.raises(RuntimeError, match="disabled"):
        EventSourcedSearchLifecycle(
            store=store,
            flags=flags,
            semantics=_semantics(),
            evaluator=object(),
            data_snapshot_hash=canonical_json_hash({"snapshot": "disabled"}),
        )


def test_outcome_rejects_caller_promotion_and_nonfinite_metadata() -> None:
    with pytest.raises(ValueError):
        SearchEvaluationOutcome(status="skip", decision="candidate_zoo")
    with pytest.raises(ValueError):
        SearchEvaluationOutcome(
            status="success",
            decision="candidate_zoo",
            scorecard_hash=canonical_json_hash({"scorecard": "finite"}),
            metadata={"bad": float("nan")},
        )
