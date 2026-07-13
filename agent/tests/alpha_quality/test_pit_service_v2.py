from __future__ import annotations

import inspect
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

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
    AsharePITAdapterRegistrationArtifactV1,
    AsharePITAdapterRegistrationServiceV1,
    AsharePITSnapshotServiceV2,
    PIT_ADAPTER_REGISTRATION_EVENT_TYPE,
    PIT_SNAPSHOT_EVENT_TYPE,
)
from src.research_ledger.events import (
    EventDraft,
    EventValidationError,
    ResearchEventAppendError,
    ResearchEventStore,
)
from src.research_ledger.hash_utils import canonical_json_hash


def _flags() -> ResolvedAGSFlags:
    return ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_ALPHA_FOUNDRY": "1",
            "VIBE_TRADING_ALPHA_SCORECARD": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
        }
    )


def _dates() -> tuple[str, ...]:
    return tuple(f"2025-01-{day:02d}" for day in range(1, 25))


class ServiceFixturePITAdapterV1:
    def descriptor(self) -> AsharePITAdapterDescriptorV1:
        return AsharePITAdapterDescriptorV1(
            adapter_id="service-fixture-pit-v1",
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
        symbols = ["000001.SZ", "600000.SH"]
        base = np.arange(len(dates), dtype=float)[:, None]
        close = pd.DataFrame(
            10.0 + base + np.array([[0.0, 1.0]]),
            index=dates,
            columns=symbols,
        )
        market = {
            "amount": pd.DataFrame(1_000_000.0, index=dates, columns=symbols),
            "close": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "open": close - 0.5,
            "volume": pd.DataFrame(100_000.0, index=dates, columns=symbols),
        }
        availability = {
            field: pd.DataFrame(
                [
                    [f"{day}T15:00:00+08:00"] * len(symbols)
                    for day in request.calendar_dates
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
            "listing_age_days": pd.DataFrame(
                1000.0,
                index=dates,
                columns=symbols,
            ),
        }
        security = pd.DataFrame(
            {
                "delisting_date": [None, None],
                "listing_date": ["1991-04-03", "1999-11-10"],
                "record_available_at": [
                    "2020-01-01T00:00:00+08:00",
                    "2020-01-01T00:00:00+08:00",
                ],
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
        request_digest = canonical_json_hash(
            {"adapter": request.adapter_id, "request": request.request_hash}
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
                query_receipt_hashes=(request_digest,),
                source_partition_hashes=(
                    canonical_json_hash({"partition": request.request_hash}),
                ),
            ),
        )


class ChangedServiceFixturePITAdapterV1(ServiceFixturePITAdapterV1):
    """Same descriptor with a deliberately different implementation identity."""

    def load(self, request: AsharePITSnapshotRequestV1) -> AsharePITSourceBundleV1:
        return super().load(request).sealed_copy()


def _setup(tmp_path: Path):
    flags = _flags()
    store = ResearchEventStore(
        tmp_path / "events.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=flags,
        code_version="pit-service-v2-test",
    )
    registry = AsharePITAdapterRegistryV1(
        {"service-fixture-pit-v1": ServiceFixturePITAdapterV1()}
    )
    registration = AsharePITAdapterRegistrationServiceV1(
        store,
        flags=flags,
        registry=registry,
    ).register(adapter_id="service-fixture-pit-v1", run_id="adapter-registry-run")
    policy = EvaluationPolicyRegistryServiceV1(store, flags=flags).register(
        dates=_dates(),
        return_horizons=(1,),
        execution_horizon=1,
        holding_period=1,
        rebalance_cadence=1,
        train=("2025-01-01", "2025-01-06"),
        valid=("2025-01-09", "2025-01-14"),
        test=("2025-01-17", "2025-01-22"),
        run_id="pit-evaluation-run",
    )
    return flags, store, registry, registration, policy


def test_service_records_replayable_fail_closed_external_adapter_snapshot(
    tmp_path: Path,
) -> None:
    flags, store, registry, registration, policy = _setup(tmp_path)
    service = AsharePITSnapshotServiceV2(store, flags=flags, registry=registry)
    recorded = service.record(
        adapter_registration_event_hash=registration.event.event_hash,
        evaluation_policy_event_hash=policy.event.event_hash,
        run_id="pit-evaluation-run",
    )

    assert recorded.event.event_type == PIT_SNAPSHOT_EVENT_TYPE
    assert recorded.snapshot.request["calendar_dates"][-1] == "2025-01-14"
    assert recorded.snapshot.derived_evidence["pit_contract_status"] == "unavailable"
    assert recorded.snapshot.derived_evidence["decision_grade"] is False
    assert recorded.snapshot.derived_evidence["caps"] == (
        "ADAPTER_AUTHORITY_UNVERIFIED",
    )
    assert recorded.event.payload["survivorship_status"] == (
        "controlled_by_daily_membership"
    )
    assert store.verify_chain()
    assert store.replay().event_count == len(store.query_events())

    retry = service.record(
        adapter_registration_event_hash=registration.event.event_hash,
        evaluation_policy_event_hash=policy.event.event_hash,
        run_id="pit-evaluation-run",
    )
    assert retry.event.event_hash == recorded.event.event_hash


def test_public_snapshot_entry_accepts_no_panel_or_truth_labels() -> None:
    parameters = set(inspect.signature(AsharePITSnapshotServiceV2.record).parameters)
    assert parameters.isdisjoint(
        {
            "panel",
            "membership",
            "pit_contract_present",
            "survivorship_bias",
            "can_buy",
            "can_sell",
            "decision_grade",
            "caps",
        }
    )


def test_generic_append_cannot_forge_pit_registration_or_snapshot(
    tmp_path: Path,
) -> None:
    flags, store, registry, registration, policy = _setup(tmp_path)
    recorded = AsharePITSnapshotServiceV2(
        store,
        flags=flags,
        registry=registry,
    ).record(
        adapter_registration_event_hash=registration.event.event_hash,
        evaluation_policy_event_hash=policy.event.event_hash,
        run_id="pit-evaluation-run",
    )
    for event in (registration.event, recorded.event):
        with pytest.raises(EventValidationError, match="deterministic producer"):
            store.append_event(
                EventDraft(
                    event_type=event.event_type,
                    entity_id=event.entity_id,
                    run_id=event.run_id,
                    payload_schema_version=event.payload_schema_version,
                    payload=event.payload,
                )
            )


def test_registration_artifact_rejects_unknown_nested_fields(tmp_path: Path) -> None:
    _, _, _, registration, _ = _setup(tmp_path)
    raw = registration.artifact_record.to_dict()
    raw["registration"] = {
        **raw["registration"],
        "absolute_source_path": "C:/private/provider.json",
    }

    with pytest.raises(ValueError, match="registration is not closed"):
        AsharePITAdapterRegistrationArtifactV1.from_dict(raw)


def test_partition_tampering_breaks_chain_replay(tmp_path: Path) -> None:
    flags, store, registry, registration, policy = _setup(tmp_path)
    recorded = AsharePITSnapshotServiceV2(
        store,
        flags=flags,
        registry=registry,
    ).record(
        adapter_registration_event_hash=registration.event.event_hash,
        evaluation_policy_event_hash=policy.event.event_hash,
        run_id="pit-evaluation-run",
    )
    table_reference = recorded.snapshot.table_refs[0]["artifact_ref"]
    target = store.artifact_root.joinpath(
        *str(table_reference["relative_path"]).split("/")
    )
    target.write_bytes(b"forged parquet")

    assert store.verify_chain() is False
    with pytest.raises(ResearchEventAppendError):
        store.replay()
    with pytest.raises((EventValidationError, ValueError)):
        AsharePITSnapshotServiceV2(
            store,
            flags=flags,
            registry=registry,
        ).record(
            adapter_registration_event_hash=registration.event.event_hash,
            evaluation_policy_event_hash=policy.event.event_hash,
            run_id="pit-evaluation-run",
        )


def test_runtime_adapter_implementation_must_match_registered_source(
    tmp_path: Path,
) -> None:
    flags, store, _, registration, policy = _setup(tmp_path)
    changed_registry = AsharePITAdapterRegistryV1(
        {"service-fixture-pit-v1": ChangedServiceFixturePITAdapterV1()}
    )

    with pytest.raises(EventValidationError, match="runtime PIT registry differs"):
        AsharePITSnapshotServiceV2(
            store,
            flags=flags,
            registry=changed_registry,
        ).record(
            adapter_registration_event_hash=registration.event.event_hash,
            evaluation_policy_event_hash=policy.event.event_hash,
            run_id="pit-evaluation-run",
        )


def test_event_append_failure_leaves_no_orphan_snapshot_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flags, store, registry, registration, policy = _setup(tmp_path)
    service = AsharePITSnapshotServiceV2(store, flags=flags, registry=registry)
    original_append = store._append_producer_event

    def fail_append(draft):  # noqa: ANN001
        raise RuntimeError("simulated event append failure")

    monkeypatch.setattr(store, "_append_producer_event", fail_append)
    with pytest.raises(RuntimeError, match="simulated event append failure"):
        service.record(
            adapter_registration_event_hash=registration.event.event_hash,
            evaluation_policy_event_hash=policy.event.event_hash,
            run_id="pit-evaluation-run",
        )
    assert store.query_events(event_type=PIT_SNAPSHOT_EVENT_TYPE) == []
    assert any((tmp_path / "artifacts").rglob("*.parquet"))

    monkeypatch.setattr(store, "_append_producer_event", original_append)
    recorded = service.record(
        adapter_registration_event_hash=registration.event.event_hash,
        evaluation_policy_event_hash=policy.event.event_hash,
        run_id="pit-evaluation-run",
    )
    assert recorded.event.event_type == PIT_SNAPSHOT_EVENT_TYPE
    assert len(store.query_events(event_type=PIT_SNAPSHOT_EVENT_TYPE)) == 1
    assert store.verify_chain()


def test_concurrent_snapshot_attempts_mint_at_most_one_authority_event(
    tmp_path: Path,
) -> None:
    flags, store, registry, registration, policy = _setup(tmp_path)
    service = AsharePITSnapshotServiceV2(store, flags=flags, registry=registry)

    def attempt() -> str:
        try:
            return service.record(
                adapter_registration_event_hash=registration.event.event_hash,
                evaluation_policy_event_hash=policy.event.event_hash,
                run_id="pit-evaluation-run",
            ).event.event_hash
        except Exception as exc:  # concurrency losers must fail closed
            return type(exc).__name__

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = tuple(executor.map(lambda _: attempt(), range(4)))

    events = store.query_events(event_type=PIT_SNAPSHOT_EVENT_TYPE)
    assert len(events) == 1
    assert events[0].event_hash in results
    assert store.verify_chain()


def test_feature_off_refuses_before_writes(tmp_path: Path) -> None:
    flags = ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
        }
    )
    store = ResearchEventStore(
        tmp_path / "events.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=flags,
        code_version="pit-service-disabled-test",
    )
    registry = AsharePITAdapterRegistryV1(
        {"service-fixture-pit-v1": ServiceFixturePITAdapterV1()}
    )
    before = {path for path in tmp_path.rglob("*") if path.is_file()}
    with pytest.raises(RuntimeError, match="capability is disabled"):
        AsharePITSnapshotServiceV2(store, flags=flags, registry=registry)
    assert {path for path in tmp_path.rglob("*") if path.is_file()} == before
    assert store.query_events() == []
