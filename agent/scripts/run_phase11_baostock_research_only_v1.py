"""Freeze the real BaoStock train/valid inputs for Phase 11 research-only runs."""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

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
from src.alpha_quality.evaluation_contract import EXECUTION_POLICY_REFERENCES, ResolvedEvaluationContractServiceV1
from src.alpha_quality.evaluation_registry_v1 import EvaluationPolicyRegistryServiceV1
from src.alpha_quality.flags import ResolvedAGSFlags
from src.alpha_quality.pit_adapter_v1 import AsharePITAdapterRegistryV1
from src.alpha_quality.pit_artifact_v2 import FrozenAsharePITSnapshotArtifactStoreV2
from src.alpha_quality.pit_service_v2 import AsharePITAdapterRegistrationServiceV1, AsharePITSnapshotServiceV2
from src.research_ledger.events import ResearchEventStore
from src.research_ledger.events.artifacts import AtomicContentAddressedArtifactWriter
from src.research_ledger.hash_utils import canonical_json, canonical_json_hash


CYCLE = "ags-v32-phase11-flat-topology-baostock-research-only-v1"


class _ReplayedBaoStockResultV1:
    error_code = "0"
    error_msg = "success"

    def __init__(self, *, fields: list[str], rows: list[list[object]]) -> None:
        self.fields = fields
        self._rows = rows
        self._position = -1

    def next(self) -> bool:
        self._position += 1
        return self._position < len(self._rows)

    def get_row_data(self) -> list[object]:
        if not 0 <= self._position < len(self._rows):
            raise RuntimeError("BaoStock replay cursor is not positioned on a row")
        return list(self._rows[self._position])


class _ExactBaoStockRawReplayClientV1:
    def __init__(
        self,
        partitions: Mapping[tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]],
        *,
        blob_hashes: tuple[str, ...],
    ) -> None:
        self._partitions = dict(partitions)
        self.replay_verification = {
            "partition_count": len(partitions),
            "blob_hash_manifest": canonical_json_hash(list(blob_hashes)),
            "semantic_hash_manifest": canonical_json_hash(
                sorted(str(payload["partition_hash"]) for payload in partitions.values())
            ),
        }

    def __getattr__(self, interface: str):
        def replay(**parameters: object) -> _ReplayedBaoStockResultV1:
            normalized = tuple(sorted((str(key), str(value)) for key, value in parameters.items()))
            try:
                payload = self._partitions[(interface, normalized)]
            except KeyError as exc:
                raise RuntimeError(
                    f"exact BaoStock raw partition is missing for {interface} {dict(normalized)}"
                ) from exc
            return _ReplayedBaoStockResultV1(
                fields=[str(value) for value in payload["columns"]],
                rows=[list(row) for row in payload["rows"]],
            )

        return replay


def _load_exact_baostock_replay(
    root: Path,
) -> tuple[_ExactBaoStockRawReplayClientV1, str, str]:
    resolved_root = root.resolve(strict=True)
    artifact_root = resolved_root / "artifacts"
    scan_root = artifact_root.resolve(strict=True) if artifact_root.is_dir() else resolved_root
    partitions: dict[tuple[str, tuple[tuple[str, str], ...]], dict[str, Any]] = {}
    blob_hashes: list[str] = []
    provider_versions: set[str] = set()
    retrieved_at_values: set[str] = set()
    for candidate in scan_root.rglob("*.json"):
        if candidate.is_symlink():
            raise ValueError("BaoStock replay source must not contain symlink artifacts")
        resolved = candidate.resolve(strict=True)
        if resolved_root not in resolved.parents:
            raise ValueError("BaoStock replay artifact escapes the frozen source root")
        raw = resolved.read_bytes()
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("schema_version") != "baostock_raw_partition.v1":
            continue
        blob_hashes.append("sha256:" + hashlib.sha256(raw).hexdigest())
        if raw != (canonical_json(payload) + "\n").encode("utf-8"):
            raise ValueError("BaoStock replay blob is not canonical JSON")
        if payload.get("status") != "success" or payload.get("error_code") != "0":
            raise ValueError("BaoStock replay contains a non-success raw partition")
        content = {key: value for key, value in payload.items() if key != "partition_hash"}
        if canonical_json_hash(content) != payload.get("partition_hash"):
            raise ValueError("BaoStock replay semantic partition hash differs")
        if resolved.stem != str(payload["partition_hash"]).removeprefix("sha256:"):
            raise ValueError("BaoStock replay semantic path differs")
        parameters = payload.get("parameters")
        if not isinstance(parameters, dict):
            raise ValueError("BaoStock replay parameters must be a mapping")
        key = (
            str(payload["interface"]),
            tuple(sorted((str(name), str(value)) for name, value in parameters.items())),
        )
        prior = partitions.get(key)
        if prior is not None and prior["partition_hash"] != payload["partition_hash"]:
            raise ValueError("BaoStock replay has conflicting partitions for one exact request")
        partitions[key] = payload
        provider_versions.add(str(payload["provider_version"]))
        retrieved_at_values.add(str(payload["retrieved_at"]))
    if not partitions:
        raise ValueError("BaoStock replay source contains no raw partitions")
    if len(provider_versions) != 1 or len(retrieved_at_values) != 1:
        raise ValueError("BaoStock replay must have one fixed provider vintage")
    return (
        _ExactBaoStockRawReplayClientV1(
            partitions, blob_hashes=tuple(sorted(blob_hashes))
        ),
        next(iter(provider_versions)),
        next(iter(retrieved_at_values)),
    )


def _adapter(
    *,
    raw_writer: AtomicContentAddressedArtifactWriter,
    replay_raw_root: Path | None,
) -> BaoStockAshareEligibleUniverseAdapterV1:
    live = BaoStockAshareEligibleUniverseAdapterV1.from_environment(
        artifact_writer=raw_writer
    )
    if replay_raw_root is None:
        return live
    client, provider_version, source_as_of = _load_exact_baostock_replay(
        replay_raw_root
    )
    return replace(
        live,
        client=client,
        dataset_vintage=f"baostock-{provider_version}-{source_as_of[:10]}",
        source_as_of=source_as_of,
        provider_version=provider_version,
    )


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


def freeze(
    *,
    root: Path,
    start: str,
    train_end: str,
    valid_end: str,
    test_end: str,
    replay_raw_root: Path | None = None,
) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=True)
    store = ResearchEventStore(root / "events.sqlite", artifact_root=root / "artifacts", flags=_flags(), code_version="phase11-baostock-research-only-v1")
    raw_writer = AtomicContentAddressedArtifactWriter(store.artifact_root)
    adapter = _adapter(raw_writer=raw_writer, replay_raw_root=replay_raw_root)
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
        profile_id="production_candidate", profile_version="1", policy_references=EXECUTION_POLICY_REFERENCES,
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
    replay_verification = getattr(adapter.client, "replay_verification", None)
    if replay_verification is not None:
        result["raw_replay_verification"] = dict(replay_verification)
    (root / "replay_manifest.json").write_text(json.dumps(result, sort_keys=True, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--start", default="2025-01-02")
    parser.add_argument("--train-end", default="2025-04-30")
    parser.add_argument("--valid-end", default="2025-06-30")
    parser.add_argument("--test-end", default="2025-07-31")
    parser.add_argument("--replay-raw-root", type=Path)
    args = parser.parse_args()
    print(json.dumps(freeze(root=args.root, start=args.start, train_end=args.train_end, valid_end=args.valid_end, test_end=args.test_end, replay_raw_root=args.replay_raw_root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
