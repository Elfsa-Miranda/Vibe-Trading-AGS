from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import src.alpha_quality.decision_v2.evidence_v3 as evidence_v3_module
from src.alpha_foundry.dsl.executable import ExecutableGrammarSnapshotV1
from src.alpha_foundry.dsl.identity import FactorIdentityService, FactorSpecSemantics
from src.alpha_foundry.retrieval.feature_source_v1 import TrainValidSnapshotServiceV1
from src.alpha_quality.decision_v2.scorecard_evidence_v3 import (
    DecisionScorecardEvidenceServiceV3,
    SCORECARD_EVIDENCE_EVENT_TYPE,
)
from src.alpha_quality.decision_v2.evidence_v3 import (
    DecisionEvidenceArtifactStoreV3,
    SCORECARD_IDENTITY_TRANSFORM_PIPELINE_HASH,
    SCORECARD_TRADABILITY_MASK_POLICY_HASH,
    SCORECARD_UNIVERSE_MASK_POLICY_HASH,
)
from src.alpha_quality.evaluation_registry_v1 import EvaluationPolicyRegistryServiceV1
from src.alpha_quality.flags import ResolvedAGSFlags
from src.research_ledger.events import (
    EventDraft,
    EventTransitionError,
    EventValidationError,
    ResearchEventAppendError,
    ResearchEventStore,
)
from src.research_ledger.events.artifacts import hash_artifact
from src.research_ledger.hash_utils import canonical_json, canonical_json_hash, utc_now_iso


def _flags() -> ResolvedAGSFlags:
    return ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_ALPHA_FOUNDRY": "1",
            "VIBE_TRADING_ALPHA_SCORECARD": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
            "VIBE_TRADING_FACTOR_DAG": "1",
            "VIBE_TRADING_PROCESS_MEMORY": "1",
            "VIBE_TRADING_TOPOLOGY_RETRIEVER": "1",
            "VIBE_TRADING_DECISION_V2": "1",
        }
    )


def _dates() -> tuple[str, ...]:
    return tuple(f"2025-01-{day:02d}" for day in range(1, 25))


def _panel(
    *,
    test_shift: float = 0.0,
    valid_shift: float = 0.0,
    include_masks: bool = True,
) -> dict[str, object]:
    dates = pd.DatetimeIndex(_dates())
    symbols = ["000001.SZ", "000002.SZ", "600000.SH"]
    base = np.arange(len(dates), dtype=float)[:, None]
    close = pd.DataFrame(
        10.0 + base + np.array([[0.0, 1.0, 2.0]]),
        index=dates,
        columns=symbols,
    )
    close.loc[pd.Timestamp("2025-01-09") : pd.Timestamp("2025-01-14"), :] += (
        valid_shift
    )
    close.loc[pd.Timestamp("2025-01-17") :, :] += test_shift
    result: dict[str, object] = {
        "close": close,
        "_meta": {
            "calendar": "XSHG_XSHE",
            "timezone": "Asia/Shanghai",
            "pit_contract_present": False,
            "survivorship_bias": False,
        },
    }
    if include_masks:
        ones = pd.DataFrame(1.0, index=dates, columns=symbols)
        result.update(
            {
                "valid_mask": ones.copy(),
                "universe_mask": ones.copy(),
                "tradable_mask": ones.copy(),
            }
        )
    return result


def _semantics() -> FactorSpecSemantics:
    return FactorSpecSemantics(
        transform_pipeline_hash=SCORECARD_IDENTITY_TRANSFORM_PIPELINE_HASH,
        field_semantics={"close": "content_bound_unverified_pit_eod"},
        signal_time="close_t",
        order_time="after_close_t",
        entry_price_time="close_t_plus_1",
        execution_lag=1,
        return_horizon=1,
        universe_mask_hash=SCORECARD_UNIVERSE_MASK_POLICY_HASH,
        tradability_mask_hash=SCORECARD_TRADABILITY_MASK_POLICY_HASH,
    )


def _sources(
    tmp_path: Path,
    *,
    test_shift: float = 0.0,
    valid_shift: float = 0.0,
    include_masks: bool = True,
):
    flags = _flags()
    store = ResearchEventStore(
        tmp_path / "events.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=flags,
        code_version="scorecard-evidence-v3-test",
    )
    snapshot = TrainValidSnapshotServiceV1(store, flags=flags).freeze(
        _panel(
            test_shift=test_shift,
            valid_shift=valid_shift,
            include_masks=include_masks,
        ),
        universe="fixture-three-symbols",
        period="2025-01-01/2025-01-24",
        source_config={"provider": "deterministic-fixture"},
        run_id="snapshot-source-run",
    )
    policy = EvaluationPolicyRegistryServiceV1(store, flags=flags).register(
        dates=_dates(),
        return_horizons=(1,),
        execution_horizon=1,
        holding_period=1,
        rebalance_cadence=1,
        train=("2025-01-01", "2025-01-06"),
        valid=("2025-01-09", "2025-01-14"),
        test=("2025-01-17", "2025-01-22"),
        run_id="scorecard-evaluation-run",
    )
    identity = FactorIdentityService(store=store, flags=flags).record_attempt(
        trial_id="scorecard-evidence-trial",
        run_id="scorecard-evaluation-run",
        candidate_id="scorecard-evidence-candidate",
        formula="rank(close)",
        semantics=_semantics(),
    )
    assert identity.factor_spec_id is not None
    return store, snapshot, policy, identity.factor_spec_id


def _record(
    tmp_path: Path,
    *,
    test_shift: float = 0.0,
    valid_shift: float = 0.0,
    include_masks: bool = True,
):
    store, snapshot, policy, factor_spec_id = _sources(
        tmp_path,
        test_shift=test_shift,
        valid_shift=valid_shift,
        include_masks=include_masks,
    )
    recorded = DecisionScorecardEvidenceServiceV3(store).record(
        factor_spec_id=factor_spec_id,
        evaluation_policy_event_hash=policy.event.event_hash,
        snapshot_event_hash=snapshot.event.event_hash,
        trial_id="scorecard-evidence-trial",
        run_id="scorecard-evaluation-run",
    )
    return store, snapshot, policy, recorded


def test_scorecard_evidence_is_recomputed_from_registered_sources(
    tmp_path: Path,
) -> None:
    store, snapshot, policy, recorded = _record(tmp_path)
    payload = recorded.record.evidence_payload

    assert recorded.event.event_type == SCORECARD_EVIDENCE_EVENT_TYPE
    assert recorded.record.evidence_kind == "scorecard"
    assert payload["snapshot_event_hash"] == snapshot.event.event_hash
    assert payload["evaluation_policy_event_hash"] == policy.event.event_hash
    assert payload["data_scope"] == "train_valid"
    assert payload["decision_grade"] is False
    assert "PIT_SNAPSHOT_PROVENANCE_UNVERIFIED" in payload["caps"]
    assert set(payload["computed_scorecard"]["predictive"][0]) == {
        "split",
        "horizon",
        "signal_dates",
        "summary",
    }
    predictive = payload["computed_scorecard"]["predictive"]
    assert next(item for item in predictive if item["split"] == "train")[
        "signal_dates"
    ][-1] == "2025-01-04"
    assert next(item for item in predictive if item["split"] == "valid")[
        "signal_dates"
    ][-1] == "2025-01-12"
    assert set(payload["factor_output_manifest"]["dates"]).isdisjoint(
        {"2025-01-17", "2025-01-18", "2025-01-19", "2025-01-20"}
    )
    assert store.verify_chain()
    assert store.replay().event_count == len(store.query_events())

    retry = DecisionScorecardEvidenceServiceV3(store).record(
        factor_spec_id=recorded.record.factor_spec_id,
        evaluation_policy_event_hash=policy.event.event_hash,
        snapshot_event_hash=snapshot.event.event_hash,
        trial_id="scorecard-evidence-trial",
        run_id="scorecard-evaluation-run",
    )
    assert retry.event.event_hash == recorded.event.event_hash


def test_scorecard_entry_accepts_no_panel_metric_failure_or_decision_channel() -> None:
    parameters = set(
        inspect.signature(DecisionScorecardEvidenceServiceV3.record).parameters
    )
    forbidden = {
        "panel",
        "factor",
        "returns",
        "ic",
        "score",
        "scorecard",
        "hard_failures",
        "caps",
        "decision",
        "test_data",
    }
    assert parameters.isdisjoint(forbidden)
    assert "_mint_scorecard_record" not in evidence_v3_module.__all__


def test_disabled_scorecard_producer_creates_no_artifact_or_event(
    tmp_path: Path,
) -> None:
    flags = ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
        }
    )
    store = ResearchEventStore(
        tmp_path / "disabled.sqlite",
        artifact_root=tmp_path / "disabled-artifacts",
        flags=flags,
        code_version="scorecard-evidence-v3-disabled-test",
    )
    before = {
        path.relative_to(tmp_path).as_posix()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    with pytest.raises(RuntimeError, match="capability is disabled"):
        DecisionScorecardEvidenceServiceV3(store)

    after = {
        path.relative_to(tmp_path).as_posix()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert store.query_events() == []


def test_valid_metrics_are_invariant_to_test_price_changes(tmp_path: Path) -> None:
    _, _, _, baseline = _record(tmp_path / "baseline", test_shift=0.0)
    _, _, _, changed = _record(tmp_path / "changed", test_shift=10000.0)

    assert (
        baseline.record.evidence_payload["computed_scorecard"]["predictive"]
        == changed.record.evidence_payload["computed_scorecard"]["predictive"]
    )
    assert (
        baseline.record.evidence_payload["computed_scorecard"]["coverage"]
        == changed.record.evidence_payload["computed_scorecard"]["coverage"]
    )


def test_train_metrics_are_invariant_to_valid_and_test_price_changes(
    tmp_path: Path,
) -> None:
    _, _, _, baseline = _record(tmp_path / "baseline")
    _, _, _, changed = _record(
        tmp_path / "changed",
        valid_shift=5000.0,
        test_shift=10000.0,
    )
    baseline_train = [
        item
        for item in baseline.record.evidence_payload["computed_scorecard"][
            "predictive"
        ]
        if item["split"] == "train"
    ]
    changed_train = [
        item
        for item in changed.record.evidence_payload["computed_scorecard"][
            "predictive"
        ]
        if item["split"] == "train"
    ]
    assert baseline_train == changed_train


def test_formula_backend_never_receives_test_dates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, snapshot, policy, factor_spec_id = _sources(tmp_path)
    original = ExecutableGrammarSnapshotV1.evaluate
    observed_max_dates: list[pd.Timestamp] = []

    def guarded_evaluate(self, formula, panel):
        observed_max_dates.extend(
            pd.Timestamp(frame.index.max()) for frame in panel.values()
        )
        assert all(value <= pd.Timestamp("2025-01-14") for value in observed_max_dates)
        return original(self, formula, panel)

    monkeypatch.setattr(ExecutableGrammarSnapshotV1, "evaluate", guarded_evaluate)
    DecisionScorecardEvidenceServiceV3(store).record(
        factor_spec_id=factor_spec_id,
        evaluation_policy_event_hash=policy.event.event_hash,
        snapshot_event_hash=snapshot.event.event_hash,
        trial_id="scorecard-evidence-trial",
        run_id="scorecard-evaluation-run",
    )
    assert observed_max_dates


def test_missing_masks_produce_explicit_caps_and_no_decision_grade(
    tmp_path: Path,
) -> None:
    _, _, _, recorded = _record(tmp_path, include_masks=False)
    payload = recorded.record.evidence_payload

    assert payload["decision_grade"] is False
    assert "PIT_UNIVERSE_MASK_UNAVAILABLE" in payload["caps"]
    assert "TRADABILITY_MASK_UNAVAILABLE" in payload["caps"]
    assert all(
        item["summary"]["n_obs"] == 0
        for item in payload["computed_scorecard"]["predictive"]
    )


def test_generic_append_cannot_forge_scorecard_evidence(tmp_path: Path) -> None:
    store, _, _, recorded = _record(tmp_path)
    payload = recorded.event.to_dict()["payload"]

    with pytest.raises(EventValidationError, match="deterministic producer"):
        store.append_event(
            EventDraft(
                event_type=SCORECARD_EVIDENCE_EVENT_TYPE,
                entity_id=str(payload["evidence_id"]),
                run_id="scorecard-evaluation-run",
                payload_schema_version="scorecard_decision_evidence_recorded.v3",
                payload=payload,
            )
        )


def test_source_snapshot_tampering_breaks_scorecard_read_and_chain(
    tmp_path: Path,
) -> None:
    store, snapshot, policy, recorded = _record(tmp_path)
    reference = snapshot.event.payload["artifact_refs"][0]
    target = store.artifact_root.joinpath(*str(reference["relative_path"]).split("/"))
    target.write_text("{}\n", encoding="utf-8")

    with pytest.raises(EventValidationError):
        DecisionScorecardEvidenceServiceV3(store).record(
            factor_spec_id=recorded.record.factor_spec_id,
            evaluation_policy_event_hash=policy.event.event_hash,
            snapshot_event_hash=snapshot.event.event_hash,
            trial_id="scorecard-evidence-trial",
            run_id="scorecard-evaluation-run",
        )
    assert store.verify_chain() is False
    with pytest.raises(ResearchEventAppendError):
        store.replay()


def test_existing_scorecard_read_rebuilds_event_payload(tmp_path: Path) -> None:
    store, snapshot, policy, recorded = _record(tmp_path)
    forged = recorded.event.to_dict()["payload"]
    forged["scorecard_hash"] = canonical_json_hash({"forged": "scorecard"})
    with sqlite3.connect(tmp_path / "events.sqlite") as connection:
        connection.execute("DROP TRIGGER research_events_no_update")
        connection.execute(
            "UPDATE research_events SET payload = ? WHERE event_hash = ?",
            (canonical_json(forged), recorded.event.event_hash),
        )
        connection.commit()

    with pytest.raises(EventValidationError, match="event differs from replay"):
        DecisionScorecardEvidenceServiceV3(store).record(
            factor_spec_id=recorded.record.factor_spec_id,
            evaluation_policy_event_hash=policy.event.event_hash,
            snapshot_event_hash=snapshot.event.event_hash,
            trial_id="scorecard-evidence-trial",
            run_id="scorecard-evaluation-run",
        )


def test_scorecard_evidence_must_precede_trial_terminal(tmp_path: Path) -> None:
    store, snapshot, policy, factor_spec_id = _sources(tmp_path)
    store.append_event(
        EventDraft(
            event_type="GenerationFailureRecorded",
            entity_id="scorecard-evidence-trial",
            run_id="scorecard-evaluation-run",
            payload_schema_version="generation_failure_recorded.v1",
            payload={
                "trial_id": "scorecard-evidence-trial",
                "failure_code": "PREMATURE_TERMINAL_FIXTURE",
                "failure_kind": "error",
                "message": "negative fixture terminal",
                "occurred_at": utc_now_iso(),
            },
        )
    )
    store.append_event(
        EventDraft(
            event_type="TrialTerminated",
            entity_id="scorecard-evidence-trial",
            run_id="scorecard-evaluation-run",
            payload_schema_version="trial_terminated.v1",
            payload={
                "trial_id": "scorecard-evidence-trial",
                "status": "error",
                "reason_codes": ["PREMATURE_TERMINAL_FIXTURE"],
                "decision": "research_only",
                "evaluation_event_hash": None,
                "terminated_at": utc_now_iso(),
            },
        )
    )

    with pytest.raises(EventValidationError, match="precede trial terminal"):
        DecisionScorecardEvidenceServiceV3(store).record(
            factor_spec_id=factor_spec_id,
            evaluation_policy_event_hash=policy.event.event_hash,
            snapshot_event_hash=snapshot.event.event_hash,
            trial_id="scorecard-evidence-trial",
            run_id="scorecard-evaluation-run",
        )


def test_unsupported_transform_or_mask_semantics_are_not_silently_scored(
    tmp_path: Path,
) -> None:
    store, snapshot, policy, _ = _sources(tmp_path)
    supported = _semantics()
    unsupported = FactorSpecSemantics(
        transform_pipeline_hash=(
            "sha256:" + "a" * 64
        ),
        field_semantics=supported.field_semantics,
        signal_time=supported.signal_time,
        order_time=supported.order_time,
        entry_price_time=supported.entry_price_time,
        execution_lag=supported.execution_lag,
        return_horizon=supported.return_horizon,
        universe_mask_hash=supported.universe_mask_hash,
        tradability_mask_hash=supported.tradability_mask_hash,
    )
    identity = FactorIdentityService(store=store, flags=store.flags).record_attempt(
        trial_id="unsupported-semantics-trial",
        run_id="scorecard-evaluation-run",
        candidate_id="unsupported-semantics-candidate",
        formula="rank(close)",
        semantics=unsupported,
    )
    assert identity.factor_spec_id is not None

    with pytest.raises(EventValidationError, match="semantics are unsupported"):
        DecisionScorecardEvidenceServiceV3(store).record(
            factor_spec_id=identity.factor_spec_id,
            evaluation_policy_event_hash=policy.event.event_hash,
            snapshot_event_hash=snapshot.event.event_hash,
            trial_id="unsupported-semantics-trial",
            run_id="scorecard-evaluation-run",
        )


def test_scorecard_evidence_artifact_tampering_breaks_replay(tmp_path: Path) -> None:
    store, _, _, recorded = _record(tmp_path)
    target = store.artifact_root.joinpath(*recorded.artifact.relative_path.split("/"))
    target.write_text("{}\n", encoding="utf-8")

    assert store.verify_chain() is False
    with pytest.raises(ResearchEventAppendError):
        store.replay()


def test_rehashed_scorecard_artifact_cannot_break_inner_outer_binding(
    tmp_path: Path,
) -> None:
    store, _, _, recorded = _record(tmp_path)
    forged = recorded.record.to_dict()
    forged["evidence_payload"]["computed_scorecard"]["factor_spec_id"] = (
        "forged-factor"
    )
    content = dict(forged)
    content.pop("evidence_hash")
    forged_hash = canonical_json_hash(content)
    forged["evidence_hash"] = forged_hash
    digest = forged_hash.removeprefix("sha256:")
    relative_path = f"decision-evidence-v3/{digest[:2]}/{digest}.json"
    target = store.artifact_root.joinpath(*relative_path.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(canonical_json(forged) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="scorecard hash differs"):
        DecisionEvidenceArtifactStoreV3(store.artifact_root).read(
            relative_path,
            expected_evidence_hash=forged_hash,
            expected_blob_hash=hash_artifact(target),
        )


def test_scorecard_record_is_deeply_immutable(tmp_path: Path) -> None:
    _, _, _, recorded = _record(tmp_path)

    with pytest.raises(TypeError):
        recorded.record.evidence_payload["computed_scorecard"]["decision_grade"] = True
    with pytest.raises(AttributeError):
        recorded.record.evidence_payload["caps"].append("CALLER_CAP")


def test_intervening_event_invalidates_scorecard_source_watermark(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, snapshot, policy, factor_spec_id = _sources(tmp_path)
    service = DecisionScorecardEvidenceServiceV3(store)
    original = store._append_producer_event

    def append_after_intervening_event(draft: EventDraft):
        store.append_event(
            EventDraft(
                event_type="TrialStarted",
                entity_id="scorecard-intervening-trial",
                run_id="scorecard-intervening-run",
                payload_schema_version="trial_started.v1",
                payload={
                    "trial_id": "scorecard-intervening-trial",
                    "candidate_id": "scorecard-intervening-candidate",
                    "data_scope": "train_valid",
                    "objective": "scorecard-watermark-race",
                    "started_at": utc_now_iso(),
                },
            )
        )
        return original(draft)

    monkeypatch.setattr(store, "_append_producer_event", append_after_intervening_event)
    with pytest.raises(EventTransitionError, match="watermark is stale"):
        service.record(
            factor_spec_id=factor_spec_id,
            evaluation_policy_event_hash=policy.event.event_hash,
            snapshot_event_hash=snapshot.event.event_hash,
            trial_id="scorecard-evidence-trial",
            run_id="scorecard-evaluation-run",
        )

    assert store.query_events(event_type=SCORECARD_EVIDENCE_EVENT_TYPE) == []
    assert store.verify_chain()
