from __future__ import annotations

import inspect
import sqlite3
from pathlib import Path

import pytest

from src.alpha_quality.evaluation_registry_v1 import (
    EVALUATION_POLICY_MEDIA_TYPE,
    EvaluationPolicyArtifactStoreV1,
    EvaluationPolicyRegistryServiceV1,
)
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


def _flags(*, enabled: bool = True) -> ResolvedAGSFlags:
    settings = {
        "VIBE_TRADING_AGS_ENABLED": "1",
        "VIBE_TRADING_RESEARCH_EVENTS": "1",
    }
    if enabled:
        settings["VIBE_TRADING_ALPHA_SCORECARD"] = "1"
    return ResolvedAGSFlags.from_settings(settings)


def _store(tmp_path: Path, *, enabled: bool = True) -> ResearchEventStore:
    flags = _flags(enabled=enabled)
    return ResearchEventStore(
        tmp_path / "events.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=flags,
        code_version="evaluation-policy-registry-test",
    )


def _dates() -> tuple[str, ...]:
    return tuple(f"2025-01-{day:02d}" for day in range(1, 25))


def _request(run_id: str = "evaluation-policy-run") -> dict[str, object]:
    return {
        "dates": _dates(),
        "return_horizons": (1,),
        "execution_horizon": 1,
        "holding_period": 1,
        "rebalance_cadence": 1,
        "train": ("2025-01-01", "2025-01-06"),
        "valid": ("2025-01-09", "2025-01-14"),
        "test": ("2025-01-17", "2025-01-22"),
        "run_id": run_id,
    }


def _register(store: ResearchEventStore, *, run_id: str = "evaluation-policy-run"):
    return EvaluationPolicyRegistryServiceV1(store, flags=store.flags).register(
        **_request(run_id)  # type: ignore[arg-type]
    )


def test_registry_mints_closed_content_addressed_policy_before_run(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    recorded = _register(store)
    calendar, time_policy, split_plan = recorded.bundle.resolved_components()

    assert recorded.event.event_type == "EvaluationPolicyRegistered"
    assert recorded.event.previous_event_hash is None
    assert recorded.event.payload["preregistration_watermark"] is None
    assert recorded.event.payload["calendar_hash"] == calendar.calendar_hash
    assert (
        recorded.event.payload["evaluation_time_policy_hash"]
        == time_policy.policy_hash
    )
    assert recorded.event.payload["split_plan_hash"] == split_plan.plan_hash
    assert recorded.artifact.media_type == EVALUATION_POLICY_MEDIA_TYPE
    assert split_plan.purge_trading_days == time_policy.maximum_outcome_offset
    assert split_plan.embargo_trading_days == time_policy.maximum_outcome_offset
    assert store.verify_chain()
    assert store.replay().event_count == 1

    retry = _register(store)
    assert retry.event.event_hash == recorded.event.event_hash
    assert retry.bundle == recorded.bundle


def test_registry_rejects_overlap_and_insufficient_derived_embargo(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    service = EvaluationPolicyRegistryServiceV1(store, flags=store.flags)
    overlap = _request("overlap-run")
    overlap["valid"] = ("2025-01-06", "2025-01-11")
    with pytest.raises(ValueError, match="strictly ordered"):
        service.register(**overlap)  # type: ignore[arg-type]

    insufficient = _request("insufficient-gap-run")
    insufficient["valid"] = ("2025-01-08", "2025-01-13")
    with pytest.raises(ValueError, match="derived trading-day embargo"):
        service.register(**insufficient)  # type: ignore[arg-type]

    assert store.query_events() == []
    assert not (store.artifact_root / "registered-evaluation-policy-v1").exists()


def test_generic_append_cannot_mint_evaluation_policy(tmp_path: Path) -> None:
    store = _store(tmp_path)
    recorded = _register(store)
    payload = recorded.event.to_dict()["payload"]

    with pytest.raises(EventValidationError, match="deterministic producer"):
        store.append_event(
            EventDraft(
                event_type="EvaluationPolicyRegistered",
                entity_id=str(payload["registration_id"]),
                run_id="forged-policy-run",
                payload_schema_version="evaluation_policy_registered.v1",
                payload=payload,
            )
        )


def test_registration_entry_has_no_caller_hash_or_policy_object_channel() -> None:
    parameters = set(
        inspect.signature(EvaluationPolicyRegistryServiceV1.register).parameters
    )
    assert {
        "calendar_hash",
        "evaluation_time_policy_hash",
        "split_plan_hash",
        "calendar",
        "time_policy",
        "split_plan",
        "purge_trading_days",
        "embargo_trading_days",
    }.isdisjoint(parameters)


def test_policy_registration_after_same_run_event_is_rejected_without_artifact(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.append_event(
        EventDraft(
            event_type="TrialStarted",
            entity_id="late-policy-trial",
            run_id="late-policy-run",
            payload_schema_version="trial_started.v1",
            payload={
                "trial_id": "late-policy-trial",
                "candidate_id": "late-policy-candidate",
                "data_scope": "train_valid",
                "objective": "policy-must-precede-run",
                "started_at": utc_now_iso(),
            },
        )
    )

    with pytest.raises(EventTransitionError, match="first event"):
        _register(store, run_id="late-policy-run")

    assert store.query_events(event_type="EvaluationPolicyRegistered") == []
    assert not (store.artifact_root / "registered-evaluation-policy-v1").exists()


def test_policy_artifact_tampering_breaks_chain_and_replay(tmp_path: Path) -> None:
    store = _store(tmp_path)
    recorded = _register(store)
    target = store.artifact_root.joinpath(*recorded.artifact.relative_path.split("/"))
    target.write_text("{}\n", encoding="utf-8")

    assert store.verify_chain() is False
    with pytest.raises(ResearchEventAppendError):
        store.replay()


def test_policy_artifact_reader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    store = _store(tmp_path)
    recorded = _register(store)
    target = store.artifact_root.joinpath(*recorded.artifact.relative_path.split("/"))
    raw = target.read_text(encoding="utf-8")
    target.write_text(
        '{"schema_version":"registered_evaluation_policy.v1",' + raw[1:],
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate evaluation policy artifact key"):
        EvaluationPolicyArtifactStoreV1(store.artifact_root).read(
            recorded.artifact.relative_path,
            expected_bundle_hash=recorded.bundle.bundle_hash,
            expected_blob_hash=hash_artifact(target),
        )


def test_existing_registration_read_rebuilds_event_payload(tmp_path: Path) -> None:
    store = _store(tmp_path)
    recorded = _register(store)
    forged = recorded.event.to_dict()["payload"]
    forged["calendar_hash"] = canonical_json_hash({"forged": "calendar"})
    with sqlite3.connect(tmp_path / "events.sqlite") as connection:
        connection.execute("DROP TRIGGER research_events_no_update")
        connection.execute(
            "UPDATE research_events SET payload = ? WHERE event_hash = ?",
            (canonical_json(forged), recorded.event.event_hash),
        )
        connection.commit()

    with pytest.raises(EventValidationError, match="differs from source replay"):
        _register(store)


def test_disabled_registry_constructor_creates_no_artifact_or_event(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path, enabled=False)
    before = {
        path.relative_to(tmp_path).as_posix()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    with pytest.raises(RuntimeError, match="capability is disabled"):
        EvaluationPolicyRegistryServiceV1(store, flags=store.flags)

    after = {
        path.relative_to(tmp_path).as_posix()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert store.query_events() == []


def test_same_content_registers_separately_for_distinct_runs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _register(store, run_id="policy-run-a")
    second = _register(store, run_id="policy-run-b")

    assert first.bundle.bundle_hash == second.bundle.bundle_hash
    assert first.artifact.relative_path == second.artifact.relative_path
    assert first.event.event_hash != second.event.event_hash
    assert second.event.previous_event_hash == first.event.event_hash
    assert store.verify_chain()


def test_same_run_cannot_replace_registered_policy(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _register(store)
    changed = _request()
    changed["dates"] = (*_dates(), "2025-01-25")

    with pytest.raises(EventTransitionError, match="already frozen"):
        EvaluationPolicyRegistryServiceV1(
            store,
            flags=store.flags,
        ).register(**changed)  # type: ignore[arg-type]


def test_intervening_chain_event_invalidates_preregistration_watermark(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    service = EvaluationPolicyRegistryServiceV1(store, flags=store.flags)
    original = store._append_producer_event

    def append_after_intervening_event(draft: EventDraft):
        store.append_event(
            EventDraft(
                event_type="TrialStarted",
                entity_id="intervening-policy-trial",
                run_id="unrelated-intervening-run",
                payload_schema_version="trial_started.v1",
                payload={
                    "trial_id": "intervening-policy-trial",
                    "candidate_id": "intervening-policy-candidate",
                    "data_scope": "train_valid",
                    "objective": "watermark-race",
                    "started_at": utc_now_iso(),
                },
            )
        )
        return original(draft)

    monkeypatch.setattr(store, "_append_producer_event", append_after_intervening_event)
    with pytest.raises(EventTransitionError, match="watermark is stale"):
        service.register(**_request("watermark-policy-run"))  # type: ignore[arg-type]

    assert store.query_events(event_type="EvaluationPolicyRegistered") == []
    assert store.verify_chain()


def test_calendar_source_hash_is_derived_from_registered_dates(tmp_path: Path) -> None:
    store = _store(tmp_path)
    recorded = _register(store)
    calendar, _, _ = recorded.bundle.resolved_components()
    expected = canonical_json_hash(
        {
            "schema_version": "registered_calendar_date_content.v1",
            "dates": list(_dates()),
        }
    )
    assert calendar.source_artifact_hash == expected


def test_registered_policy_bundle_is_deeply_immutable(tmp_path: Path) -> None:
    recorded = _register(_store(tmp_path))

    with pytest.raises(AttributeError):
        recorded.bundle.calendar["dates"].append("2099-01-01")
    with pytest.raises(AttributeError):
        recorded.bundle.time_policy["return_horizons"].append(99)
    with pytest.raises(TypeError):
        recorded.bundle.split_plan["train"]["end"] = "2099-01-01"
