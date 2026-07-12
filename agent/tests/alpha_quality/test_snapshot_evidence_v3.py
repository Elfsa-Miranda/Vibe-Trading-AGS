from __future__ import annotations

import inspect
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import src.alpha_quality.decision_v2.evidence_v3 as evidence_v3_module
from src.alpha_foundry.dsl.identity import FactorIdentityService, FactorSpecSemantics
from src.alpha_foundry.retrieval.feature_source_v1 import TrainValidSnapshotServiceV1
from src.alpha_quality.decision_v2.evidence_v3 import (
    DecisionEvidenceArtifactStoreV3,
    SCORECARD_IDENTITY_TRANSFORM_PIPELINE_HASH,
    SCORECARD_TRADABILITY_MASK_POLICY_HASH,
    SCORECARD_UNIVERSE_MASK_POLICY_HASH,
)
from src.alpha_quality.decision_v2.snapshot_evidence_v3 import (
    DecisionSnapshotEvidenceServiceV3,
    SNAPSHOT_EVIDENCE_EVENT_TYPE,
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
from src.research_ledger.hash_utils import canonical_json, canonical_json_hash


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
    through_valid_only: bool = False,
    include_masks: bool = True,
    incomplete_membership: bool = False,
    pit_claim: bool = False,
    survivorship_claim: bool = False,
):
    flags = _flags()
    store = ResearchEventStore(
        tmp_path / "events.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=flags,
        code_version="snapshot-evidence-v3-test",
    )
    dates = _dates()[:14] if through_valid_only else _dates()
    index = pd.DatetimeIndex(dates)
    symbols = ["000001.SZ", "000002.SZ", "600000.SH"]
    base = np.arange(len(index), dtype=float)[:, None]
    panel: dict[str, object] = {
        "close": pd.DataFrame(
            10.0 + base + np.array([[0.0, 1.0, 2.0]]),
            index=index,
            columns=symbols,
        ),
        "_meta": {
            "pit_contract_present": pit_claim,
            "survivorship_bias": survivorship_claim,
        },
    }
    if include_masks:
        ones = pd.DataFrame(1.0, index=index, columns=symbols)
        panel["universe_mask"] = (
            ones.iloc[2:-2, :-1].copy()
            if incomplete_membership
            else ones.copy()
        )
        panel["tradable_mask"] = ones.copy()
    snapshot = TrainValidSnapshotServiceV1(store, flags=flags).freeze(
        panel,
        universe="fixture-three-symbols",
        period=f"{dates[0]}/{dates[-1]}",
        source_config={"provider": "caller-fixture"},
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
        run_id="snapshot-evaluation-run",
    )
    identity = FactorIdentityService(store=store, flags=flags).record_attempt(
        trial_id="snapshot-evidence-trial",
        run_id="snapshot-evaluation-run",
        candidate_id="snapshot-evidence-candidate",
        formula="rank(close)",
        semantics=_semantics(),
    )
    assert identity.factor_spec_id is not None
    return store, snapshot, policy, identity.factor_spec_id


def _record(tmp_path: Path, **kwargs):
    store, snapshot, policy, factor_spec_id = _sources(tmp_path, **kwargs)
    recorded = DecisionSnapshotEvidenceServiceV3(store).record(
        factor_spec_id=factor_spec_id,
        evaluation_policy_event_hash=policy.event.event_hash,
        snapshot_event_hash=snapshot.event.event_hash,
        run_id="snapshot-evaluation-run",
    )
    return store, snapshot, policy, recorded


def test_snapshot_evidence_rebuilds_sources_and_defaults_to_insufficient(
    tmp_path: Path,
) -> None:
    store, snapshot, policy, recorded = _record(tmp_path)
    payload = recorded.record.evidence_payload

    assert recorded.event.event_type == SNAPSHOT_EVIDENCE_EVENT_TYPE
    assert recorded.record.evidence_kind == "snapshot"
    assert payload["snapshot_event_hash"] == snapshot.event.event_hash
    assert payload["evaluation_policy_event_hash"] == policy.event.event_hash
    assert payload["pit_authority_status"] == "unverified_legacy_caller_snapshot"
    assert payload["survivorship_status"] == "unknown"
    assert payload["availability_time_status"] == "unavailable"
    assert payload["corporate_action_status"] == "unavailable"
    assert payload["decision_grade"] is False
    assert payload["cutoff_status"] == "contains_dates_after_registered_valid_end"
    assert "SNAPSHOT_SCOPE_CUTOFF_VIOLATION" in payload["caps"]
    assert set(payload["frame_inventory"]) == {
        "close",
        "tradable_mask",
        "universe_mask",
    }
    assert store.verify_chain()
    assert store.replay().event_count == len(store.query_events())

    retry = DecisionSnapshotEvidenceServiceV3(store).record(
        factor_spec_id=recorded.record.factor_spec_id,
        evaluation_policy_event_hash=policy.event.event_hash,
        snapshot_event_hash=snapshot.event.event_hash,
        run_id="snapshot-evaluation-run",
    )
    assert retry.event.event_hash == recorded.event.event_hash


def test_snapshot_entry_accepts_no_panel_or_caller_truth_channel() -> None:
    parameters = set(
        inspect.signature(DecisionSnapshotEvidenceServiceV3.record).parameters
    )
    assert parameters.isdisjoint(
        {
            "panel",
            "period",
            "source_config",
            "pit_contract_present",
            "survivorship_bias",
            "cutoff_status",
            "decision_grade",
            "caps",
            "metadata",
        }
    )
    assert "_mint_snapshot_record" not in evidence_v3_module.__all__


def test_legacy_caller_pit_and_survivorship_boole_never_become_truth(
    tmp_path: Path,
) -> None:
    _, _, _, negative_claims = _record(
        tmp_path / "negative",
        pit_claim=False,
        survivorship_claim=False,
    )
    _, _, _, positive_claims = _record(
        tmp_path / "positive",
        pit_claim=True,
        survivorship_claim=True,
    )
    for recorded in (negative_claims, positive_claims):
        payload = recorded.record.evidence_payload
        assert payload["pit_authority_status"] == "unverified_legacy_caller_snapshot"
        assert payload["survivorship_status"] == "unknown"
        assert "PIT_SNAPSHOT_PROVENANCE_UNVERIFIED" in payload["caps"]
        assert "SURVIVORSHIP_STATUS_UNKNOWN" in payload["caps"]


def test_snapshot_cutoff_is_computed_from_registered_valid_end(
    tmp_path: Path,
) -> None:
    _, _, _, recorded = _record(tmp_path, through_valid_only=True)
    payload = recorded.record.evidence_payload

    assert payload["observed_date_end"] == "2025-01-14"
    assert payload["registered_valid_end"] == "2025-01-14"
    assert payload["cutoff_status"] == "within_registered_valid_end"
    assert "SNAPSHOT_SCOPE_CUTOFF_VIOLATION" not in payload["caps"]
    assert payload["decision_grade"] is False


def test_missing_membership_and_tradability_are_explicitly_unavailable(
    tmp_path: Path,
) -> None:
    _, _, _, recorded = _record(tmp_path, include_masks=False)
    payload = recorded.record.evidence_payload

    assert payload["daily_membership_status"] == "unavailable"
    assert payload["tradability_status"] == "unavailable"
    assert "PIT_UNIVERSE_MASK_UNAVAILABLE" in payload["caps"]
    assert "TRADABILITY_MASK_UNAVAILABLE" in payload["caps"]


def test_present_but_incomplete_membership_axes_are_not_silently_accepted(
    tmp_path: Path,
) -> None:
    _, _, _, recorded = _record(tmp_path, incomplete_membership=True)
    payload = recorded.record.evidence_payload

    assert (
        payload["daily_membership_status"]
        == "present_but_unverified_caller_frame"
    )
    assert "SNAPSHOT_FRAME_COVERAGE_INCOMPLETE" in payload["caps"]
    assert "SNAPSHOT_FRAME_SYMBOL_AXES_INCONSISTENT" in payload["caps"]
    assert payload["decision_grade"] is False


def test_generic_append_cannot_forge_snapshot_evidence(tmp_path: Path) -> None:
    store, _, _, recorded = _record(tmp_path)
    payload = recorded.event.to_dict()["payload"]

    with pytest.raises(EventValidationError, match="deterministic producer"):
        store.append_event(
            EventDraft(
                event_type=SNAPSHOT_EVIDENCE_EVENT_TYPE,
                entity_id=str(payload["evidence_id"]),
                run_id="snapshot-evaluation-run",
                payload_schema_version="snapshot_decision_evidence_recorded.v3",
                payload=payload,
            )
        )


def test_snapshot_source_tampering_breaks_read_and_replay(tmp_path: Path) -> None:
    store, snapshot, policy, recorded = _record(tmp_path)
    reference = snapshot.event.payload["artifact_refs"][0]
    target = store.artifact_root.joinpath(*str(reference["relative_path"]).split("/"))
    target.write_text("{}\n", encoding="utf-8")

    with pytest.raises(EventValidationError):
        DecisionSnapshotEvidenceServiceV3(store).record(
            factor_spec_id=recorded.record.factor_spec_id,
            evaluation_policy_event_hash=policy.event.event_hash,
            snapshot_event_hash=snapshot.event.event_hash,
            run_id="snapshot-evaluation-run",
        )
    assert store.verify_chain() is False
    with pytest.raises(ResearchEventAppendError):
        store.replay()


def test_snapshot_record_is_deeply_immutable(tmp_path: Path) -> None:
    _, _, _, recorded = _record(tmp_path)

    with pytest.raises(TypeError):
        recorded.record.evidence_payload["frame_inventory"]["close"][
            "date_count"
        ] = 1
    with pytest.raises(AttributeError):
        recorded.record.evidence_payload["caps"].append("CALLER_CAP")


def test_rehashed_snapshot_artifact_cannot_forge_inner_inventory_status(
    tmp_path: Path,
) -> None:
    store, _, _, recorded = _record(tmp_path)
    forged = recorded.record.to_dict()
    forged["evidence_payload"]["observed_date_end"] = "2025-01-14"
    content = dict(forged)
    content.pop("evidence_hash")
    forged_hash = canonical_json_hash(content)
    forged["evidence_hash"] = forged_hash
    digest = forged_hash.removeprefix("sha256:")
    relative_path = f"decision-evidence-v3/{digest[:2]}/{digest}.json"
    target = store.artifact_root.joinpath(*relative_path.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(canonical_json(forged) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="observed range differs"):
        DecisionEvidenceArtifactStoreV3(store.artifact_root).read(
            relative_path,
            expected_evidence_hash=forged_hash,
            expected_blob_hash=hash_artifact(target),
        )


def test_disabled_snapshot_producer_creates_no_artifact_or_event(
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
        code_version="snapshot-evidence-v3-disabled-test",
    )
    before = {path for path in tmp_path.rglob("*") if path.is_file()}
    with pytest.raises(RuntimeError, match="capability is disabled"):
        DecisionSnapshotEvidenceServiceV3(store)
    assert {path for path in tmp_path.rglob("*") if path.is_file()} == before
    assert store.query_events() == []


def test_intervening_event_invalidates_snapshot_source_watermark(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, snapshot, policy, factor_spec_id = _sources(tmp_path)
    service = DecisionSnapshotEvidenceServiceV3(store)
    original = store._append_producer_event

    def append_after_intervening_event(draft: EventDraft):
        store.append_event(
            EventDraft(
                event_type="TrialStarted",
                entity_id="snapshot-intervening-trial",
                run_id="snapshot-intervening-run",
                payload_schema_version="trial_started.v1",
                payload={
                    "trial_id": "snapshot-intervening-trial",
                    "candidate_id": "snapshot-intervening-candidate",
                    "data_scope": "train_valid",
                    "objective": "snapshot-watermark-race",
                    "started_at": "2025-01-01T00:00:00Z",
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
            run_id="snapshot-evaluation-run",
        )
    assert store.query_events(event_type=SNAPSHOT_EVIDENCE_EVENT_TYPE) == []
    assert store.verify_chain()
