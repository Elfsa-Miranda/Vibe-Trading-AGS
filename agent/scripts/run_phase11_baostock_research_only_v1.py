"""Freeze the real BaoStock train/valid inputs for Phase 11 research-only runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.alpha_foundry.activation.provider_pit_audit_v1 import (
    ProviderPITAuditServiceV1,
    baostock_golden_cohort_field_audits_v1,
)
from src.alpha_foundry.activation.run_input_v1 import (
    ResearchOnlyActivationRunInputBundleV1,
    ResearchOnlyActivationRunInputServiceV1,
)
from src.alpha_foundry.control_evidence import FlatControlPolicyV1
from src.alpha_foundry.retrieval.feature_source_v1 import TrainValidSnapshotServiceV1
from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy
from src.alpha_quality.adapters.baostock_eligible_universe_v1 import BaoStockAshareEligibleUniverseAdapterV1
from src.alpha_quality.evaluation_contract import PIT_SCORECARD_POLICY_REFERENCES, ResolvedEvaluationContractServiceV1
from src.alpha_quality.evaluation_registry_v1 import EvaluationPolicyRegistryServiceV1
from src.alpha_quality.flags import ResolvedAGSFlags
from src.alpha_quality.pit_adapter_v1 import AsharePITAdapterRegistryV1
from src.alpha_quality.pit_artifact_v2 import FrozenAsharePITSnapshotArtifactStoreV2
from src.alpha_quality.pit_service_v2 import AsharePITAdapterRegistrationServiceV1, AsharePITSnapshotServiceV2
from src.research_ledger.events import ResearchEventStore
from src.research_ledger.events.artifacts import AtomicContentAddressedArtifactWriter
from src.research_ledger.hash_utils import canonical_json_hash


CYCLE = "ags-v32-phase11-flat-topology-baostock-research-only-v1"


def _flags() -> ResolvedAGSFlags:
    return ResolvedAGSFlags.from_settings({
        "VIBE_TRADING_AGS_ENABLED": "1", "VIBE_TRADING_ALPHA_FOUNDRY": "1",
        "VIBE_TRADING_ALPHA_SCORECARD": "1", "VIBE_TRADING_RESEARCH_EVENTS": "1",
        "VIBE_TRADING_FACTOR_DAG": "1", "VIBE_TRADING_PROCESS_MEMORY": "1",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER": "1", "VIBE_TRADING_DECISION_V2": "1",
    })


def _trading_dates(adapter: BaoStockAshareEligibleUniverseAdapterV1, start: str, end: str) -> tuple[str, ...]:
    calendar, _ = adapter._fetch("query_trade_dates", {"start_date": start, "end_date": end})
    return tuple(calendar.loc[calendar["is_trading_day"].astype(str) == "1", "calendar_date"].astype(str))


def _panel(bundle: object, *, dates: tuple[str, ...]) -> dict[str, object]:
    from src.alpha_quality.pit_adapter_v1 import AsharePITSourceBundleV1
    if not isinstance(bundle, AsharePITSourceBundleV1):
        raise TypeError("PIT artifact replay did not produce the registered bundle")
    frames = {name: frame.loc[list(dates)].copy() for name, frame in bundle.market_fields.items()}
    return {**frames, "_meta": {
        "pit_contract_present": True, "survivorship_bias": False,
        "calendar": "SSE_SZSE_BAOSTOCK", "timezone": "Asia/Shanghai",
    }}


def freeze(*, root: Path, start: str, train_end: str, valid_end: str, test_end: str) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=True)
    store = ResearchEventStore(root / "events.sqlite", artifact_root=root / "artifacts", flags=_flags(), code_version="phase11-baostock-research-only-v1")
    raw_writer = AtomicContentAddressedArtifactWriter(store.artifact_root)
    adapter = BaoStockAshareEligibleUniverseAdapterV1.from_environment(artifact_writer=raw_writer)
    dates = _trading_dates(adapter, start, test_end)
    train_dates = tuple(day for day in dates if day <= train_end)
    valid_dates = tuple(day for day in dates if train_end < day <= valid_end)
    test_dates = tuple(day for day in dates if valid_end < day <= test_end)
    if len(train_dates) < 40 or len(valid_dates) < 20 or not test_dates:
        raise ValueError("frozen Phase 11 window does not contain sufficient train/valid dates")
    # Existing evaluation policy derives a one-day purge/embargo from the
    # horizon.  Reserve three sessions at each formal split boundary; they
    # remain inside the train/valid raw snapshot but are not scored.
    train_window = train_dates[:-3]
    valid_window = valid_dates[3:-3]
    test_window = test_dates[3:]
    policy = EvaluationPolicyRegistryServiceV1(store, flags=store.flags).register(
        dates=dates, return_horizons=(1,), execution_horizon=1, holding_period=1,
        rebalance_cadence=1, train=(train_window[0], train_window[-1]),
        valid=(valid_window[0], valid_window[-1]), test=(test_window[0], test_window[-1]), run_id=CYCLE,
    )
    registry = AsharePITAdapterRegistryV1({adapter.descriptor().adapter_id: adapter})
    registration = AsharePITAdapterRegistrationServiceV1(store, flags=store.flags, registry=registry).register(adapter_id=adapter.descriptor().adapter_id, run_id=CYCLE + ":provider")
    contract = ResolvedEvaluationContractServiceV1(store, flags=store.flags).register(
        run_id=CYCLE, evaluation_policy_event_hash=policy.event.event_hash,
        profile_id="production_candidate", profile_version="1", policy_references=PIT_SCORECARD_POLICY_REFERENCES,
    )
    # Field authority is registered before snapshot production.  The calendar
    # receipt is already a typed, content-addressed provider partition; the
    # subsequent snapshot adds the complete raw partition manifest.
    _, audit_receipt = adapter._fetch("query_trade_dates", {"start_date": start, "end_date": valid_end})
    audit_service = ProviderPITAuditServiceV1(store)
    field_events = tuple(audit_service.record_field(
        run_id=CYCLE, adapter_registration_event_hash=registration.event.event_hash, audit=audit
    ).event.event_hash for audit in baostock_golden_cohort_field_audits_v1(
        adapter_registration_event_hash=registration.event.event_hash, typed_receipt_hashes=(audit_receipt,),
    ))
    interfaces = tuple(sorted({event.payload["interface"] for event in store.query_events(event_type="ProviderFieldPITAuditV1Recorded")}))
    interface_events = tuple(audit_service.record_interface(
        run_id=CYCLE, adapter_registration_event_hash=registration.event.event_hash,
        field_audit_event_hashes=tuple(sorted(event.event_hash for event in store.query_events(event_type="ProviderFieldPITAuditV1Recorded") if event.payload["interface"] == interface)),
        required_fields=tuple(sorted({event.payload["field_name"] for event in store.query_events(event_type="ProviderFieldPITAuditV1Recorded") if event.payload["interface"] == interface})),
    ).event.event_hash for interface in interfaces)
    authority = audit_service.decide_authority(
        run_id=CYCLE, adapter_registration_event_hash=registration.event.event_hash,
        interface_audit_event_hashes=tuple(sorted(interface_events)), required_interfaces=interfaces,
    )
    snapshot = AsharePITSnapshotServiceV2(store, flags=store.flags, registry=registry).record(
        adapter_registration_event_hash=registration.event.event_hash,
        evaluation_policy_event_hash=policy.event.event_hash, run_id=CYCLE,
    )
    pit_store = FrozenAsharePITSnapshotArtifactStoreV2(store.artifact_root)
    source_bundle = pit_store.read_bundle(snapshot.snapshot)
    train = TrainValidSnapshotServiceV1(store, flags=store.flags).freeze(
        _panel(source_bundle, dates=train_window), universe="A_SHARE_ELIGIBLE_GOLDEN_COHORT_V1",
        period=f"{train_window[0]}/{train_window[-1]}", source_config={"pit_snapshot_hash": snapshot.snapshot.snapshot_hash, "scope": "train"}, run_id=CYCLE + ":train",
    )
    valid = TrainValidSnapshotServiceV1(store, flags=store.flags).freeze(
        _panel(source_bundle, dates=valid_window), universe="A_SHARE_ELIGIBLE_GOLDEN_COHORT_V1",
        period=f"{valid_window[0]}/{valid_window[-1]}", source_config={"pit_snapshot_hash": snapshot.snapshot.snapshot_hash, "scope": "valid"}, run_id=CYCLE + ":valid",
    )
    combined = TrainValidSnapshotServiceV1(store, flags=store.flags).freeze(
        _panel(source_bundle, dates=train_window + valid_window), universe="A_SHARE_ELIGIBLE_GOLDEN_COHORT_V1",
        period=f"{train_window[0]}/{valid_window[-1]}", source_config={"pit_snapshot_hash": snapshot.snapshot.snapshot_hash, "scope": "train_valid"}, run_id=CYCLE + ":train-valid",
    )
    tail = store.query_events()[-1].event_hash
    bundle = ResearchOnlyActivationRunInputBundleV1.create(
        research_cycle_id=CYCLE, resolved_contract_event_hash=contract.event.event_hash,
        provider_authority_decision_event_hash=authority.event.event_hash,
        pit_snapshot_event_hash=snapshot.event.event_hash, train_valid_snapshot_event_hash=combined.event.event_hash,
        train_snapshot_hash=train.snapshot.snapshot_hash, valid_snapshot_hash=valid.snapshot.snapshot_hash,
        train_valid_split_plan_hash=policy.event.payload["split_plan_hash"],
        flat_policy_hash=FlatControlPolicyV1.create(max_candidates_per_seed=5, max_candidates=32, trial_budget=64).policy_hash,
        topology_policy_hash=ActivationRetrieverPolicy().policy_hash, candidate_budget=32, compute_budget=64,
        source_watermark=tail, limitation_codes=("BAOSTOCK_BEST_EFFORT_AUTHORITY", "RESEARCH_ONLY_EMPIRICAL_ACTIVATION"),
    )
    input_event = ResearchOnlyActivationRunInputServiceV1(store).register(run_id=CYCLE, bundle=bundle)
    result = {"research_cycle_id": CYCLE, "bundle_hash": bundle.bundle_hash, "input_event_hash": input_event.event_hash,
              "provider_authority": authority.decision.to_dict(), "symbols": list(source_bundle.daily_membership.columns),
              "train_snapshot_hash": train.snapshot.snapshot_hash, "valid_snapshot_hash": valid.snapshot.snapshot_hash,
              "train_valid_snapshot_event_hash": combined.event.event_hash, "pit_snapshot_event_hash": snapshot.event.event_hash,
              "contract_event_hash": contract.event.event_hash, "replay_chain_verified": store.verify_chain(),
              "manifest_hash": canonical_json_hash({"cycle": CYCLE, "bundle": bundle.bundle_hash, "input": input_event.event_hash})}
    (root / "replay_manifest.json").write_text(json.dumps(result, sort_keys=True, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--start", default="2025-01-02")
    parser.add_argument("--train-end", default="2025-04-30")
    parser.add_argument("--valid-end", default="2025-06-30")
    parser.add_argument("--test-end", default="2025-07-31")
    args = parser.parse_args()
    print(json.dumps(freeze(root=args.root, start=args.start, train_end=args.train_end, valid_end=args.valid_end, test_end=args.test_end), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
