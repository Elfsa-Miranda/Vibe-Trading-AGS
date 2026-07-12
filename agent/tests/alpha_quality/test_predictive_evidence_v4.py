from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from src.alpha_foundry.dsl.identity import FactorIdentityService, FactorSpecSemantics
from src.alpha_quality.evaluation_contract import (
    PIT_SCORECARD_POLICY_REFERENCES,
    ResolvedEvaluationContractServiceV1,
)
from src.alpha_quality.evaluation_registry_v1 import EvaluationPolicyRegistryServiceV1
from src.alpha_quality.flags import ResolvedAGSFlags
from src.alpha_quality.pit_adapter_v1 import (
    AsharePITAdapterDescriptorV1,
    AsharePITAdapterRegistryV1,
    AsharePITSnapshotRequestV1,
    AsharePITSourceBundleV1,
    AsharePITSourceManifestV1,
)
from src.alpha_quality.pit_service_v2 import (
    AsharePITAdapterRegistrationServiceV1,
    AsharePITSnapshotServiceV2,
)
from src.alpha_quality.predictive_evidence_v4 import (
    FACTOR_OUTPUT_EVENT_TYPE,
    FactorOutputArtifactV3,
    FactorOutputArtifactStoreV3,
    PITPredictiveEvidenceServiceV4,
    _exact_axes,
)
from src.alpha_quality.scope.views import DiscoveryEvidenceProjector
from src.research_ledger.events import (
    EventDraft,
    EventTransitionError,
    EventValidationError,
)
from src.research_ledger.events.store import ResearchEventStore
from src.research_ledger.hash_utils import canonical_json_hash


def _hash(label: str) -> str:
    return canonical_json_hash({"label": label})


def _flags() -> ResolvedAGSFlags:
    return ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_ALPHA_FOUNDRY": "1",
            "VIBE_TRADING_ALPHA_SCORECARD": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
            "VIBE_TRADING_FACTOR_DAG": "1",
            "VIBE_TRADING_PROCESS_MEMORY": "1",
        }
    )


def _dates() -> tuple[str, ...]:
    return tuple(f"2025-01-{day:02d}" for day in range(1, 25))


class PredictiveFixturePITAdapterV1:
    def descriptor(self) -> AsharePITAdapterDescriptorV1:
        return AsharePITAdapterDescriptorV1(
            adapter_id="predictive-fixture-pit-v1",
            provider="fixture-provider",
            adapter_version="1.0.0",
            market="CN_A_SHARE",
            calendar_id="XSHG_XSHE",
            timezone="Asia/Shanghai",
            membership_dataset="fixture-membership-v1",
            security_master_dataset="fixture-security-master-v1",
            corporate_action_dataset="fixture-actions-v1",
            trade_state_dataset="fixture-trade-state-v1",
            price_dataset="fixture-price-v1",
            availability_semantics="provider_release_timestamp.v1",
            adjustment_semantics="raw_prices_plus_dated_actions.v1",
        )

    def load(self, request: AsharePITSnapshotRequestV1) -> AsharePITSourceBundleV1:
        dates = pd.DatetimeIndex(request.calendar_dates)
        symbols = ["000001.SZ", "000002.SZ", "600000.SH"]
        time = np.arange(len(dates), dtype=float)[:, None]
        slopes = np.array([[0.13, 0.07, 0.03]])
        close = pd.DataFrame(
            10.0 + time * slopes + np.array([[0.0, 1.0, 2.0]]),
            index=dates,
            columns=symbols,
        )
        market = {
            "amount": pd.DataFrame(1_000_000.0, index=dates, columns=symbols),
            "close": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "open": close - 0.1,
            "volume": pd.DataFrame(100_000.0, index=dates, columns=symbols),
        }
        availability = {
            field: pd.DataFrame(
                [
                    [f"{date}T15:00:00+08:00"] * len(symbols)
                    for date in request.calendar_dates
                ],
                index=dates,
                columns=symbols,
            )
            for field in market
        }
        truth = pd.DataFrame(True, index=dates, columns=symbols, dtype=bool)
        falsehood = pd.DataFrame(False, index=dates, columns=symbols, dtype=bool)
        trade_states = {
            "at_limit_down": falsehood.copy(),
            "at_limit_up": falsehood.copy(),
            "is_st": falsehood.copy(),
            "is_suspended": falsehood.copy(),
            "listing_age_days": pd.DataFrame(1000.0, index=dates, columns=symbols),
        }
        security = pd.DataFrame(
            {
                "delisting_date": [None] * len(symbols),
                "listing_date": ["1991-01-01"] * len(symbols),
                "record_available_at": ["2020-01-01T00:00:00+08:00"] * len(symbols),
            },
            index=pd.Index(symbols, name="symbol"),
        )
        actions = pd.DataFrame(
            {
                "announced_at": ["2024-12-01T18:00:00+08:00"],
                "effective_date": [request.calendar_dates[0]],
                "factor": [1.0],
                "symbol": [symbols[0]],
            },
            index=pd.Index(["action-1"], name="action_id"),
        )
        return AsharePITSourceBundleV1(
            market_fields=dict(sorted(market.items())),
            field_available_at=dict(sorted(availability.items())),
            trade_state_fields=dict(sorted(trade_states.items())),
            daily_membership=truth,
            security_master=security,
            corporate_actions=actions,
            calendar_dates=request.calendar_dates,
            source_manifest=AsharePITSourceManifestV1(
                adapter_id=request.adapter_id,
                request_hash=request.request_hash,
                dataset_vintage="fixture-20250125",
                source_as_of="2025-01-25T00:00:00+08:00",
                query_receipt_hashes=(_hash("query-" + request.request_hash),),
                source_partition_hashes=(_hash("partition-" + request.request_hash),),
            ),
        )


def _setup(tmp_path: Path, policy_references=PIT_SCORECARD_POLICY_REFERENCES):
    flags = _flags()
    store = ResearchEventStore(
        tmp_path / "events.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=flags,
        code_version="predictive-v4-test",
    )
    registry = AsharePITAdapterRegistryV1(
        {"predictive-fixture-pit-v1": PredictiveFixturePITAdapterV1()}
    )
    registration = AsharePITAdapterRegistrationServiceV1(
        store, flags=flags, registry=registry
    ).register(adapter_id="predictive-fixture-pit-v1", run_id="adapter-run")
    policy = EvaluationPolicyRegistryServiceV1(store, flags=flags).register(
        dates=_dates(),
        return_horizons=(1,),
        execution_horizon=1,
        holding_period=1,
        rebalance_cadence=1,
        train=("2025-01-01", "2025-01-06"),
        valid=("2025-01-09", "2025-01-14"),
        test=("2025-01-17", "2025-01-22"),
        run_id="predictive-run",
    )
    contract = ResolvedEvaluationContractServiceV1(store, flags=flags).register(
        run_id="predictive-run",
        evaluation_policy_event_hash=policy.event.event_hash,
        profile_id="production_candidate",
        profile_version="1",
        policy_references=policy_references,
    )
    snapshot = AsharePITSnapshotServiceV2(store, flags=flags, registry=registry).record(
        adapter_registration_event_hash=registration.event.event_hash,
        evaluation_policy_event_hash=policy.event.event_hash,
        run_id="predictive-run",
    )
    semantics = FactorSpecSemantics(
        transform_pipeline_hash=_hash("identity-transform"),
        field_semantics={"close": "close_t"},
        signal_time="close_t",
        order_time="close_t_plus_1",
        entry_price_time="open_t_plus_1",
        execution_lag=1,
        return_horizon=1,
        universe_mask_hash=_hash("pit-universe-mask"),
        tradability_mask_hash=_hash("pit-tradability-mask"),
    )
    identity = FactorIdentityService(store=store, flags=flags).record_attempt(
        trial_id="predictive-trial",
        run_id="predictive-run",
        candidate_id="predictive-candidate",
        formula="rank(close)",
        semantics=semantics,
    )
    definition = store.query_events(
        event_type="FactorDefinitionRecorded", entity_id=str(identity.factor_spec_id)
    )[0]
    return flags, store, contract, snapshot, definition


def _record(tmp_path: Path):
    flags, store, contract, snapshot, definition = _setup(tmp_path)
    recorded = PITPredictiveEvidenceServiceV4(store).record(
        run_id="predictive-run",
        contract_event_hash=contract.event.event_hash,
        factor_definition_event_hash=definition.event_hash,
        pit_snapshot_event_hash=snapshot.event.event_hash,
    )
    return flags, store, contract, snapshot, definition, recorded


def test_missing_pit_still_emits_observed_panel_predictive_evidence(
    tmp_path: Path,
) -> None:
    _, store, _, snapshot, _, recorded = _record(tmp_path)
    assert snapshot.snapshot.derived_evidence["decision_grade"] is False
    assert recorded.observed.availability == "available"
    assert recorded.observed.split_metrics["valid"]["effective_dates"] > 0
    assert recorded.observed.promotion_effect == "none"
    assert recorded.pit.availability == "unavailable"
    assert "PIT_PREDICTIVE_AUTHORITY_UNAVAILABLE" in recorded.pit.caps
    assert recorded.scorecard.candidate_promotion_effect == "blocked_pending_execution"
    assert store.verify_chain()


def test_observed_panel_evidence_cannot_promote(tmp_path: Path) -> None:
    _, _, _, _, _, recorded = _record(tmp_path)
    bias = recorded.observed.universe_bias_assessment
    assert recorded.observed.evidence_grade == "exploratory"
    assert recorded.observed.claim_scope == "frozen_observed_panel"
    assert bias["status"] == "unknown"
    assert bias["universe_construction"] == "frozen_unverified_observed_panel"
    assert bias["direction_identifiability"] == "not_identified"
    assert bias["likely_effect_on_performance_claims"] == "optimistic_risk"


def test_static_backfill_is_descriptive_only(tmp_path: Path) -> None:
    _, _, _, snapshot, _, recorded = _record(tmp_path)
    assert snapshot.snapshot.derived_evidence["adapter_authority_class"] == (
        "external_unverified"
    )
    assert recorded.observed.evidence_grade == "exploratory"
    assert recorded.observed.promotion_effect == "none"
    assert recorded.pit.availability == "unavailable"


def test_bias_direction_is_unknown_without_identification_evidence(
    tmp_path: Path,
) -> None:
    _, _, _, _, _, recorded = _record(tmp_path)
    assert (
        recorded.observed.universe_bias_assessment["direction_identifiability"]
        == "not_identified"
    )


def test_pit_predictive_requires_registered_pit_snapshot(tmp_path: Path) -> None:
    _, store, contract, _, definition = _setup(tmp_path)
    with pytest.raises(EventValidationError, match="AsharePITSnapshotRecorded"):
        PITPredictiveEvidenceServiceV4.rebuild(
            store,
            run_id="predictive-run",
            contract_event_hash=contract.event.event_hash,
            factor_definition_event_hash=definition.event_hash,
            pit_snapshot_event_hash=definition.event_hash,
            persist_factor=True,
        )


def test_factor_output_partition_replay_matches_content_hash(tmp_path: Path) -> None:
    _, store, _, _, _, recorded = _record(tmp_path)
    assert recorded.factor_output.schema_version == "factor_output_artifact.v3"
    assert recorded.factor_output.factor_table_ref["artifact_ref"]["media_type"] == (
        "application/vnd.apache.parquet"
    )
    frame = FactorOutputArtifactStoreV3(store.artifact_root).read_factor_frame(
        recorded.factor_output
    )
    assert frame.shape == recorded.factor_output.shape
    retry = PITPredictiveEvidenceServiceV4(store).record(
        run_id="predictive-run",
        contract_event_hash=recorded.factor_output.contract_event_hash,
        factor_definition_event_hash=next(
            item
            for item in recorded.factor_output.source_event_hashes
            if any(
                event.event_hash == item
                and event.event_type == "FactorDefinitionRecorded"
                for event in store.query_events()
            )
        ),
        pit_snapshot_event_hash=recorded.factor_output.pit_snapshot_event_hash,
    )
    assert retry.scorecard_event.event_hash == recorded.scorecard_event.event_hash


def test_test_price_changes_do_not_change_any_discovery_evidence(
    tmp_path: Path,
) -> None:
    _, _, _, snapshot, _, recorded = _record(tmp_path)
    parameters = set(
        inspect.signature(PITPredictiveEvidenceServiceV4.record).parameters
    )
    assert parameters.isdisjoint(
        {"test_panel", "forward_panel", "raw_panel", "metrics"}
    )
    assert max(recorded.factor_output.dates) == "2025-01-14"
    assert max(snapshot.snapshot.request["calendar_dates"]) == "2025-01-14"


def test_all_axes_are_exact_and_no_intersection_repair_occurs(tmp_path: Path) -> None:
    _, store, _, _, _, recorded = _record(tmp_path)
    frame = FactorOutputArtifactStoreV3(store.artifact_root).read_factor_frame(
        recorded.factor_output
    )
    with pytest.raises(EventValidationError, match="intersection repair"):
        _exact_axes(
            frame.iloc[:, ::-1],
            recorded.factor_output.dates,
            recorded.factor_output.symbols,
            "permuted factor",
        )


def test_missing_valid_mask_is_typed_unavailable_not_fail_open(
    tmp_path: Path,
) -> None:
    _, store, _, _, _, recorded = _record(tmp_path)
    events = {event.event_hash: event for event in store.query_events()}
    frame = FactorOutputArtifactStoreV3(store.artifact_root).read_factor_frame(
        recorded.factor_output
    )
    fake_complete_snapshot = SimpleNamespace(
        derived_evidence={
            "decision_grade": True,
            "warnings": [],
            "pit_contract_status": "complete",
            "survivorship_status": "controlled_by_daily_membership",
            "adapter_authority_class": "built_in_production",
        }
    )
    result = PITPredictiveEvidenceServiceV4._predictive(
        factor_artifact=recorded.factor_output,
        factor_frame=frame,
        close=frame + 10.0,
        masks=None,
        snapshot=fake_complete_snapshot,
        contract_event=events[recorded.factor_output.contract_event_hash],
        snapshot_event=events[recorded.factor_output.pit_snapshot_event_hash],
        time_policy=SimpleNamespace(entry_lag_trading_days=1, execution_horizon=1),
        split_plan=SimpleNamespace(),
        channel="pit_scoped",
    )
    assert result.availability == "unavailable"
    assert not result.split_metrics
    assert "PIT_PREDICTIVE_AUTHORITY_UNAVAILABLE" in result.caps


def test_factor_partition_dtype_nan_and_infinity_rules(tmp_path: Path) -> None:
    _, store, _, _, _, recorded = _record(tmp_path)
    artifact_store = FactorOutputArtifactStoreV3(store.artifact_root)
    frame = artifact_store.read_factor_frame(recorded.factor_output)
    with_nan = frame.copy()
    with_nan.iloc[0, 0] = np.nan
    reference = artifact_store.write_factor_table("nan-control", with_nan)
    assert np.isnan(artifact_store.tables.read(reference).iloc[0, 0])
    with_infinity = frame.copy()
    with_infinity.iloc[0, 0] = np.inf
    with pytest.raises(ValueError, match="Infinity"):
        artifact_store.write_factor_table("infinity-control", with_infinity)
    with pytest.raises(ValueError, match="non-numeric"):
        artifact_store.write_factor_table("dtype-control", frame.astype(str))


def test_generic_append_and_cross_contract_binding_fail_closed(tmp_path: Path) -> None:
    _, store, _, _, _, recorded = _record(tmp_path)
    with pytest.raises(EventValidationError, match="deterministic producer"):
        store.append_event(
            EventDraft(
                event_type=FACTOR_OUTPUT_EVENT_TYPE,
                entity_id=recorded.factor_event.entity_id,
                run_id="predictive-run",
                payload_schema_version="factor_output_recorded.v3",
                payload=recorded.factor_event.to_dict()["payload"],
            )
        )
    with pytest.raises(EventTransitionError, match="cannot mix runs"):
        PITPredictiveEvidenceServiceV4.rebuild(
            store,
            run_id="other-run",
            contract_event_hash=recorded.factor_output.contract_event_hash,
            factor_definition_event_hash=next(
                event.event_hash
                for event in store.query_events()
                if event.event_type == "FactorDefinitionRecorded"
            ),
            pit_snapshot_event_hash=recorded.factor_output.pit_snapshot_event_hash,
            persist_factor=True,
        )
    with pytest.raises(EventTransitionError, match="retry sources differ"):
        PITPredictiveEvidenceServiceV4(store).record(
            run_id="predictive-run",
            contract_event_hash=recorded.factor_output.pit_snapshot_event_hash,
            factor_definition_event_hash=next(
                event.event_hash
                for event in store.query_events()
                if event.event_type == "FactorDefinitionRecorded"
            ),
            pit_snapshot_event_hash=recorded.factor_output.pit_snapshot_event_hash,
        )


def test_factor_manifest_cannot_relabel_parquet_axes(tmp_path: Path) -> None:
    _, _, _, _, _, recorded = _record(tmp_path)
    raw = recorded.factor_output.to_dict()
    raw["symbols"] = list(reversed(raw["symbols"]))
    content = {key: value for key, value in raw.items() if key != "factor_output_hash"}
    raw["factor_output_hash"] = canonical_json_hash(content)
    with pytest.raises(ValueError, match="columns|canonical"):
        FactorOutputArtifactV3.from_dict(raw)


def test_factor_parquet_tamper_breaks_chain_replay(tmp_path: Path) -> None:
    _, store, _, _, _, recorded = _record(tmp_path)
    relative = recorded.factor_output.factor_table_ref["artifact_ref"]["relative_path"]
    target = store.artifact_root.joinpath(*str(relative).split("/"))
    target.write_bytes(b"tampered parquet")
    assert store.verify_chain() is False


def test_observed_panel_result_does_not_enter_authoritative_positive_memory(
    tmp_path: Path,
) -> None:
    flags, store, _, _, _, recorded = _record(tmp_path)
    eligible = DiscoveryEvidenceProjector(flags=flags).eligible_events(
        store.query_events()
    )
    eligible_hashes = {event.event_hash for event in eligible}
    assert recorded.observed_event.event_hash not in eligible_hashes
    assert recorded.pit_event.event_hash not in eligible_hashes
    assert recorded.scorecard_event.event_hash not in eligible_hashes
