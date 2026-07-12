"""First non-empty replayable AGS v3.2 baseline using a bundled fixture.

The fixture is intentionally external-unverified research evidence.  It proves
that the production evaluator path executes end-to-end; it does not claim
production data authority or profitability.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from src.alpha_foundry.dsl.identity import FactorIdentityService, FactorSpecSemantics
from src.alpha_quality.evaluation_contract import (
    EXECUTION_POLICY_REFERENCES,
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
from src.alpha_quality.production_evaluator_v1 import (
    ProductionCandidateEvaluatorFactoryV1,
    ProductionEvaluationRequestV1,
)
from src.alpha_quality.secondary_evidence_v1 import ComparisonPoolServiceV1
from src.research_ledger.events.store import ResearchEventStore
from src.research_ledger.hash_utils import canonical_json, canonical_json_hash


BASELINE_RUN_ID = "ags-v32-baseline-reversal-5d-v1"
BASELINE_TRIAL_ID = "ags-v32-baseline-trial-1"
BASELINE_ADAPTER_ID = "bundled-ashare-history-fixture-v1"
BASELINE_FORMULA = "neg(delta(close,5))"


def _dates() -> tuple[str, ...]:
    return tuple(
        str(value.date())
        for value in pd.bdate_range("2024-09-02", periods=72)
    )


class BundledHistoricalFixturePITAdapterV1:
    """Deterministic endpoint fixture, never production data authority."""

    def descriptor(self) -> AsharePITAdapterDescriptorV1:
        return AsharePITAdapterDescriptorV1(
            adapter_id=BASELINE_ADAPTER_ID,
            provider="bundled-historical-fixture",
            adapter_version="1.0.0",
            market="CN_A_SHARE",
            calendar_id="XSHG_XSHE_FIXTURE",
            timezone="Asia/Shanghai",
            membership_dataset="bundled-csi300-like-membership-v1",
            security_master_dataset="bundled-security-master-v1",
            corporate_action_dataset="bundled-corporate-actions-v1",
            trade_state_dataset="bundled-trade-states-v1",
            price_dataset="bundled-adjusted-eod-fixture-v1",
            availability_semantics="provider_release_timestamp.v1",
            adjustment_semantics="raw_prices_plus_dated_actions.v1",
        )

    def load(self, request: AsharePITSnapshotRequestV1) -> AsharePITSourceBundleV1:
        dates = pd.DatetimeIndex(request.calendar_dates)
        symbols = (
            "000001.SZ", "000002.SZ", "000333.SZ", "000651.SZ",
            "600000.SH", "600036.SH", "600519.SH", "601318.SH",
        )
        t: np.ndarray = np.arange(len(dates), dtype=float)[:, None]
        offsets: np.ndarray = np.arange(len(symbols), dtype=float)[None, :]
        phase = offsets * 0.71
        close_values = (
            18.0
            + offsets * 2.5
            + t * (0.018 + offsets * 0.001)
            + np.sin(t * 0.62 + phase) * (0.75 + offsets * 0.03)
            + np.cos(t * 0.17 + phase) * 0.25
        )
        close = pd.DataFrame(close_values, index=dates, columns=symbols)
        open_ = close * (1.0 + 0.0015 * np.sin(t * 0.43 + phase))
        high = pd.DataFrame(
            np.maximum(open_.to_numpy(), close.to_numpy()) * 1.012,
            index=dates,
            columns=symbols,
        )
        low = pd.DataFrame(
            np.minimum(open_.to_numpy(), close.to_numpy()) * 0.988,
            index=dates,
            columns=symbols,
        )
        volume = pd.DataFrame(
            1_200_000.0 + 250_000.0 * (1.0 + np.sin(t * 0.21 + phase)),
            index=dates,
            columns=symbols,
        )
        amount = volume * close
        market = {
            "amount": amount,
            "close": close,
            "high": high,
            "low": low,
            "open": open_,
            "volume": volume,
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
        # Membership is a dated panel, not a latest/static constituent list.
        truth.loc[dates[:8], symbols[-1]] = False
        truth.loc[dates[-8:], symbols[1]] = False
        falsehood = pd.DataFrame(False, index=dates, columns=symbols, dtype=bool)
        trade_states = {
            "at_limit_down": falsehood.copy(),
            "at_limit_up": falsehood.copy(),
            "is_st": falsehood.copy(),
            "is_suspended": falsehood.copy(),
            "listing_age_days": pd.DataFrame(
                np.broadcast_to(2_000.0 + t, (len(dates), len(symbols))),
                index=dates,
                columns=symbols,
            ),
        }
        security = pd.DataFrame(
            {
                "delisting_date": [None] * len(symbols),
                "listing_date": ["2000-01-01"] * len(symbols),
                "record_available_at": ["2020-01-01T00:00:00+08:00"] * len(symbols),
            },
            index=pd.Index(symbols, name="symbol"),
        )
        actions = pd.DataFrame(
            {
                "announced_at": ["2024-08-01T18:00:00+08:00"],
                "effective_date": [request.calendar_dates[0]],
                "factor": [1.0],
                "symbol": [symbols[0]],
            },
            index=pd.Index(["fixture-action-1"], name="action_id"),
        )
        source = {
            "adapter_id": request.adapter_id,
            "request_hash": request.request_hash,
            "dataset_vintage": "bundled-fixture-20241210",
            "source_as_of": "2024-12-11T00:00:00+08:00",
            "query_receipt_hashes": [
                canonical_json_hash(
                    {"fixture": "query", "request_hash": request.request_hash}
                )
            ],
            "source_partition_hashes": [
                canonical_json_hash(
                    {
                        "fixture": "partition",
                        "request_hash": request.request_hash,
                        "rows": len(dates),
                        "symbols": list(symbols),
                    }
                )
            ],
        }
        return AsharePITSourceBundleV1(
            market_fields=dict(sorted(market.items())),
            field_available_at=dict(sorted(availability.items())),
            trade_state_fields=dict(sorted(trade_states.items())),
            daily_membership=truth,
            security_master=security,
            corporate_actions=actions,
            calendar_dates=request.calendar_dates,
            source_manifest=AsharePITSourceManifestV1(
                adapter_id=str(source["adapter_id"]),
                request_hash=str(source["request_hash"]),
                dataset_vintage=str(source["dataset_vintage"]),
                source_as_of=str(source["source_as_of"]),
                query_receipt_hashes=tuple(source["query_receipt_hashes"]),
                source_partition_hashes=tuple(source["source_partition_hashes"]),
            ),
        )


def _flags() -> ResolvedAGSFlags:
    return ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_ALPHA_FOUNDRY": "1",
            "VIBE_TRADING_ALPHA_SCORECARD": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
            "VIBE_TRADING_FACTOR_DAG": "1",
            "VIBE_TRADING_PROCESS_MEMORY": "1",
            "VIBE_TRADING_DECISION_V2": "1",
            "VIBE_TRADING_ALPHA_REPORT_API": "1",
        }
    )


def _read_json_artifact(root: Path, event: Any) -> Mapping[str, Any]:
    if len(event.payload["artifact_refs"]) != 1:
        raise ValueError("baseline predictive artifact is ambiguous")
    reference = event.payload["artifact_refs"][0]
    path = root.joinpath(*str(reference["relative_path"]).split("/"))
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("baseline predictive artifact is malformed")
    return raw


def _effective_sample(predictive: Mapping[str, Any]) -> int:
    split_metrics = predictive.get("split_metrics")
    if not isinstance(split_metrics, Mapping):
        return 0
    total = 0
    for split in ("train", "valid"):
        metrics = split_metrics.get(split)
        if not isinstance(metrics, Mapping):
            continue
        for key in ("n_obs", "effective_dates", "sample_size"):
            value = metrics.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                total += value
                break
    return total


@dataclass(frozen=True)
class FirstRealBaselineResultV1:
    manifest: Mapping[str, Any]
    manifest_path: Path


class FirstRealBaselineRunnerV1:
    def run(self, output_root: str | Path) -> FirstRealBaselineResultV1:
        root = Path(output_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        flags = _flags()
        store = ResearchEventStore(
            root / "research.sqlite",
            artifact_root=root / "artifacts",
            flags=flags,
            code_version="ags-v32-phase8-baseline-v1",
        )
        adapter = BundledHistoricalFixturePITAdapterV1()
        registry = AsharePITAdapterRegistryV1({BASELINE_ADAPTER_ID: adapter})
        registration = AsharePITAdapterRegistrationServiceV1(
            store, flags=flags, registry=registry
        ).register(adapter_id=BASELINE_ADAPTER_ID, run_id="baseline-adapter-registration")
        dates = _dates()
        policy = EvaluationPolicyRegistryServiceV1(store, flags=flags).register(
            dates=dates,
            return_horizons=(5,),
            execution_horizon=5,
            holding_period=5,
            rebalance_cadence=5,
            train=(dates[0], dates[18]),
            valid=(dates[25], dates[43]),
            test=(dates[50], dates[71]),
            run_id=BASELINE_RUN_ID,
        )
        contract = ResolvedEvaluationContractServiceV1(store, flags=flags).register(
            run_id=BASELINE_RUN_ID,
            evaluation_policy_event_hash=policy.event.event_hash,
            profile_id="production_candidate",
            profile_version="1",
            policy_references=EXECUTION_POLICY_REFERENCES,
        )
        snapshot = AsharePITSnapshotServiceV2(
            store, flags=flags, registry=registry
        ).record(
            adapter_registration_event_hash=registration.event.event_hash,
            evaluation_policy_event_hash=policy.event.event_hash,
            run_id=BASELINE_RUN_ID,
        )
        semantics = FactorSpecSemantics(
            transform_pipeline_hash=canonical_json_hash(
                {"pipeline": "raw_reversal_signal.v1"}
            ),
            field_semantics={"close": "pit_adjusted_close_t"},
            signal_time="close_t",
            order_time="close_t_plus_1",
            entry_price_time="open_t_plus_1",
            execution_lag=1,
            return_horizon=5,
            universe_mask_hash=canonical_json_hash(
                {"universe": "bundled_daily_membership.v1"}
            ),
            tradability_mask_hash=canonical_json_hash(
                {"tradability": "bundled_ashare_states.v1"}
            ),
        )
        identity = FactorIdentityService(store=store, flags=flags).record_attempt(
            trial_id=BASELINE_TRIAL_ID,
            run_id=BASELINE_RUN_ID,
            candidate_id="reversal-5d-candidate",
            formula=BASELINE_FORMULA,
            semantics=semantics,
        )
        if identity.status != "recorded" or identity.factor_spec_id is None:
            raise RuntimeError("baseline factor identity was not recorded")
        definition = store.query_events(
            event_type="FactorDefinitionRecorded",
            entity_id=identity.factor_spec_id,
        )[0]
        pool, _ = ComparisonPoolServiceV1(store).freeze(
            run_id=BASELINE_RUN_ID,
            source_watermark_event_hash=definition.event_hash,
            members=(),
        )
        request = ProductionEvaluationRequestV1(
            run_id=BASELINE_RUN_ID,
            trial_id=BASELINE_TRIAL_ID,
            factor_definition_event_hash=definition.event_hash,
            resolved_contract_hash=contract.contract.contract_hash,
            snapshot_event_hash=snapshot.event.event_hash,
            source_watermark_event_hash=definition.event_hash,
            frozen_comparison_pool_hash=pool.comparison_pool_hash,
        )
        evaluator = ProductionCandidateEvaluatorFactoryV1.create(store)
        result = evaluator.evaluate(request)
        replay = evaluator.evaluate(request)
        if result != replay:
            raise RuntimeError("baseline serial retry does not replay")
        events = store.query_events()
        by_type: dict[str, list[Any]] = {}
        for event in events:
            by_type.setdefault(event.event_type, []).append(event)
        observed = by_type["ObservedPanelPredictiveEvidenceRecorded"][0]
        pit = by_type["PITPredictiveEvidenceRecorded"][0]
        observed_artifact = _read_json_artifact(store.artifact_root, observed)
        pit_artifact = _read_json_artifact(store.artifact_root, pit)
        effective_sample = max(
            _effective_sample(observed_artifact),
            _effective_sample(pit_artifact),
        )
        decision = by_type["QualityDecisionV4Recorded"][0]
        dossier = by_type["ResearchDossierRecorded"][0]
        run_report = by_type["ExperimentRunReportRecorded"][0]
        release = by_type["ResearchReleaseManifestRecorded"][0]
        scientific = {
            "formula": BASELINE_FORMULA,
            "factor_spec_id": identity.factor_spec_id,
            "evaluation_policy_bundle_hash": policy.bundle.bundle_hash,
            "contract_hash": contract.contract.contract_hash,
            "snapshot_hash": snapshot.snapshot.snapshot_hash,
            "observed_evidence_hash": observed.payload["evidence_hash"],
            "pit_evidence_hash": pit.payload["evidence_hash"],
            "execution_artifact_hash": by_type["ExecutionEvidenceRecorded"][0].payload[
                "execution_artifact_hash"
            ],
        }
        manifest_content = {
            "schema_version": "first_real_baseline_manifest.v1",
            "baseline_status": (
                "COMPLETED_RESEARCH_ONLY"
                if result.completion_status == "completed"
                else "PARTIAL_IMPLEMENTED_ACCEPTANCE_BLOCKED"
            ),
            "authority_grade": "external_unverified_bundled_historical_fixture",
            "provider": "bundled-historical-fixture",
            "dataset_vintage": "bundled-fixture-20241210",
            "source_as_of": "2024-12-11T00:00:00+08:00",
            "run_id": BASELINE_RUN_ID,
            "trial_id": BASELINE_TRIAL_ID,
            "factor_spec_id": identity.factor_spec_id,
            "formula": BASELINE_FORMULA,
            "dates": {"start": dates[0], "end": dates[-1], "count": len(dates)},
            "splits": {
                "train": [dates[0], dates[18]],
                "valid": [dates[25], dates[43]],
                "test": [dates[50], dates[71]],
            },
            "execution": {
                "signal_time": "close_t",
                "order_time": "close_t_plus_1",
                "entry_price_time": "open_t_plus_1",
                "horizon": 5,
                "rebalance_cadence": 5,
            },
            "preregistered_policy": {
                "universe": "daily_pit_csi300_like_fixture_membership",
                "portfolio": "long_only_benchmark_relative",
                "weighting": "frozen_execution_policy_reference",
                "cost": "frozen_execution_policy_reference",
                "missing_outcome": "drop_only_when_policy_declares_unavailable",
                "capacity": "frozen_execution_policy_reference",
                "inference": "resolved_contract_producer_manifest",
                "multiplicity": "resolved_contract_producer_manifest",
                "comparison_pool_hash": pool.comparison_pool_hash,
                "stopping_rule": "one_candidate_one_serial_retry",
                "resource_budget": {
                    "candidate_count": 1,
                    "worker_count": 1,
                    "calendar_dates": len(dates),
                    "symbols": 8,
                },
            },
            "effective_sample": effective_sample,
            "completion_status": result.completion_status,
            "decision": decision.payload["decision"],
            "decision_hash": decision.payload["decision_hash"],
            "candidate_dossier_hash": dossier.payload["dossier_hash"],
            "run_report_hash": run_report.payload["run_report_hash"],
            "release_manifest_hash": release.payload["manifest_hash"],
            "test_access_count": len(by_type.get("FinalTestAccessRecorded", [])),
            "forward_observation_count": len(
                by_type.get("ForwardObservationRecorded", [])
            ) + len(by_type.get("ForwardObservationV2Recorded", [])),
            "event_count": len(events),
            "chain_verified": store.verify_chain(),
            "serial_retry_equal": result == replay,
            "scientific_runtime": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "numpy": np.__version__,
                "pandas": pd.__version__,
            },
            "scientific_evidence": scientific,
            "scientific_replay_hash": canonical_json_hash(scientific),
            "limitations": [
                "BUNDLED_FIXTURE_NOT_PRODUCTION_MARKET_DATA_AUTHORITY",
                "RESEARCH_ONLY_NO_LIVE_TRADING_MEANING",
                "FINAL_TEST_NOT_OPENED",
                "FORWARD_MONITORING_NOT_STARTED",
                "BASELINE_PROVES_PIPELINE_EXECUTION_NOT_PROFITABILITY",
            ],
        }
        if effective_sample < 1:
            raise RuntimeError("baseline effective sample is empty")
        if decision.payload["decision"] != "research_only":
            raise RuntimeError("external-unverified baseline escaped research_only")
        if manifest_content["test_access_count"] != 0:
            raise RuntimeError("baseline discovery path accessed final test")
        manifest = {
            **manifest_content,
            "baseline_manifest_hash": canonical_json_hash(manifest_content),
        }
        manifest_path = root / "baseline_manifest.json"
        manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
        return FirstRealBaselineResultV1(manifest=manifest, manifest_path=manifest_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the AGS v3.2 bundled baseline")
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args(argv)
    result = FirstRealBaselineRunnerV1().run(args.output_root)
    sys.stdout.write(canonical_json(result.manifest) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BASELINE_ADAPTER_ID",
    "BASELINE_FORMULA",
    "BASELINE_RUN_ID",
    "BundledHistoricalFixturePITAdapterV1",
    "FirstRealBaselineResultV1",
    "FirstRealBaselineRunnerV1",
    "main",
]
