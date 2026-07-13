from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.alpha_foundry.dsl.executable import DEFAULT_EXECUTABLE_GRAMMAR
from src.alpha_quality.evaluation_contract import EXECUTION_POLICY_REFERENCES
from src.alpha_quality.final_test.authority_v2 import (
    FinalDependenceConfigV2,
    FinalEvaluationEligibilityServiceV2,
    FinalRawPartitionBundleV2,
    FinalRawPartitionRefV2,
    FinalRawProviderDescriptorV2,
    FinalRawProviderRegistrationServiceV2,
    FinalRawProviderRegistryV2,
    FinalScopeAuthorityV2,
    FinalScopeConfigV2,
    FinalTestRunnerV2,
    _dependent_mean_se,
)
from src.alpha_quality.flags import ResolvedAGSFlags
from src.alpha_quality.production_evaluator_v1 import (
    ProductionCandidateEvaluatorFactoryV1,
    ProductionEvaluationRequestV1,
)
from src.alpha_quality.secondary_evidence_v1 import ComparisonPoolServiceV1
from src.alpha_quality.scope import DiscoveryEvidenceProjector
from src.research_ledger.events import EventDraft, EventTransitionError
from src.research_ledger.events.model import EventValidationError
from src.research_ledger.events.store import ResearchEventStore
from src.research_ledger.hash_utils import canonical_json_hash
from tests.alpha_quality.test_predictive_evidence_v4 import _setup


class _RawProvider:
    def __init__(self, store, dates: tuple[str, ...], *, mismatch: bool = False) -> None:
        self.store = store
        self.dates = dates
        self.mismatch = mismatch
        self.calls = 0
        self.last_bundle = None

    def descriptor(self) -> FinalRawProviderDescriptorV2:
        return FinalRawProviderDescriptorV2(
            provider_id="final-raw-fixture-v2",
            provider_version="2.0.0",
            provider_policy_hash=canonical_json_hash({"provider": "raw-only-fixture", "version": "2.0.0"}),
        )

    def resolve_partitions(self, eligibility):
        self.calls += 1
        symbols = ("000001.SZ", "000002.SZ", "600000.SH", "600036.SH", "600519.SH")
        rows = []
        for date_index, date in enumerate(self.dates):
            for symbol_index, symbol in enumerate(symbols):
                close = (
                    10.0
                    + symbol_index * 1.7
                    + date_index * (0.025 + symbol_index * 0.002)
                    + np.sin(date_index * 0.71 + symbol_index * 0.83) * 0.45
                )
                rows.append(
                    {
                        "date": date,
                        "symbol": symbol,
                        "close": close,
                        "membership": True,
                        "open": close * (1.0 + 0.001 * np.cos(date_index + symbol_index)),
                        "tradable": True,
                    }
                )
        frame = pd.DataFrame(rows)
        relative = Path("final-raw-v2") / "fixture.parquet"
        path = self.store.artifact_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False)
        descriptor = self.descriptor()
        self.last_bundle = FinalRawPartitionBundleV2.build(
            provider_id=descriptor.provider_id,
            provider_version=("wrong-version" if self.mismatch else descriptor.provider_version),
            data_snapshot_hash=eligibility.scope.data_snapshot_hash,
            period_start=self.dates[0],
            period_end=self.dates[-1],
            fields=("close", "membership", "open", "tradable"),
            partitions=(
                FinalRawPartitionRefV2(
                    relative_path=relative.as_posix(),
                    artifact_hash=self.store.hash_artifact(path),
                    media_type="application/vnd.apache.parquet",
                    partition_start=self.dates[0],
                    partition_end=self.dates[-1],
                    row_count=len(frame),
                ),
            ),
        )
        return self.last_bundle


def _prepared(tmp_path: Path, *, provider_mismatch: bool = False):
    flags, store, contract, snapshot, definition = _setup(
        tmp_path,
        policy_references=EXECUTION_POLICY_REFERENCES,
        enable_decision=True,
    )
    pool, _ = ComparisonPoolServiceV1(store).freeze(
        run_id="predictive-run",
        source_watermark_event_hash=definition.event_hash,
        members=(),
    )
    ProductionCandidateEvaluatorFactoryV1.create(store).evaluate(
        ProductionEvaluationRequestV1(
            run_id="predictive-run",
            trial_id="predictive-trial",
            factor_definition_event_hash=definition.event_hash,
            resolved_contract_hash=contract.contract.contract_hash,
            snapshot_event_hash=snapshot.event.event_hash,
            source_watermark_event_hash=definition.event_hash,
            frozen_comparison_pool_hash=pool.comparison_pool_hash,
        )
    )
    decision = store.query_events(event_type="QualityDecisionV4Recorded")[0]
    assert decision.payload["decision"] == "research_only"
    dates = tuple(str(item.date()) for item in pd.bdate_range("2025-04-01", periods=35))
    provider = _RawProvider(store, dates, mismatch=provider_mismatch)
    registry = FinalRawProviderRegistryV2({provider.descriptor().provider_id: provider})
    registration = FinalRawProviderRegistrationServiceV2(store, registry).register(
        provider.descriptor().provider_id, run_id="predictive-run"
    )
    digest = canonical_json_hash({"final": "v2-fixture"})
    scope = FinalScopeConfigV2(
        provider_id=provider.descriptor().provider_id,
        provider_version=provider.descriptor().provider_version,
        data_snapshot_hash=digest,
        period_start=dates[0],
        period_end=dates[-1],
        raw_fields=("close", "membership", "open", "tradable"),
        transform_pipeline_hash=digest,
        cost_model_hash=digest,
        regime_config_hash=digest,
        backend_hash=DEFAULT_EXECUTABLE_GRAMMAR.snapshot_hash,
        execution_lag=1,
        return_horizon=5,
        rebalance_cadence=5,
        round_trip_cost_bps=8.0,
        selection_fraction=0.4,
    )
    dependence = FinalDependenceConfigV2(
        method="hac",
        confidence_level=0.95,
        hac_lags=1,
        cohort_spacing=2,
        block_length=2,
        bootstrap_replicates=200,
        bootstrap_seed=17,
        minimum_effective_observations=3,
        rank_ic_sesoi=-1.0,
        net_return_sesoi=-1.0,
    )
    eligibility, eligibility_event = FinalEvaluationEligibilityServiceV2(store).register(
        run_id="predictive-run",
        factor_definition_event_hash=definition.event_hash,
        prefinal_decision_event_hash=decision.event_hash,
        contract_event_hash=contract.event.event_hash,
        provider_registration_event_hash=registration.event_hash,
        scope=scope,
        dependence=dependence,
    )
    authority = FinalScopeAuthorityV2(store)
    capability = authority.issue(eligibility_event.event_hash, run_id="predictive-run")
    runner = FinalTestRunnerV2(
        store=store,
        authority=authority,
        provider_registry=registry,
    )
    return (
        flags,
        store,
        definition,
        decision,
        eligibility,
        eligibility_event,
        authority,
        capability,
        runner,
        provider,
    )


def test_final_v2_recomputes_raw_partitions_and_preserves_prefinal_ceiling(
    tmp_path: Path,
) -> None:
    _, store, _, _, eligibility, event, _, capability, runner, provider = _prepared(tmp_path)

    artifact = runner.run(event.event_hash, capability, run_id="predictive-run")
    view = runner.decision_view(artifact)

    assert provider.calls == 1
    assert artifact.metrics.effective_observations >= 3
    assert artifact.metrics.quality_passed
    assert view["promotion_ceiling"] == "research_only"
    assert view["quality_passed"] is True
    assert view["confirmatory_grade"] is True
    assert provider.last_bundle is not None
    assert not hasattr(provider.last_bundle, "rank_ic_series")
    terminals = store.query_events(event_type="FinalTestArtifactV2Recorded")
    assert len(terminals) == 1
    assert not store.query_events(event_type="FinalTestFailedV2Recorded")
    assert store.verify_chain()
    assert store.replay().event_count == len(store.query_events())
    discovery_types = {
        item.event_type for item in DiscoveryEvidenceProjector(flags=store.flags).eligible_events(store.query_events())
    }
    assert not any("Final" in event_type for event_type in discovery_types)
    final_event = terminals[0]
    assert final_event.payload["raw_partition_refs"]
    raw_relative = str(final_event.payload["raw_partition_refs"][0]["relative_path"])
    store.artifact_root.joinpath(*raw_relative.split("/")).write_bytes(b"tampered")
    assert not store.verify_chain()


def test_family_key_cannot_reopen_by_changed_scope_or_variant_hash(
    tmp_path: Path,
) -> None:
    _, store, _, _, eligibility, event, authority, _, _, _ = _prepared(tmp_path)
    service = FinalEvaluationEligibilityServiceV2(store)
    with pytest.raises(EventTransitionError, match="already frozen"):
        service.register(
            run_id="predictive-run",
            factor_definition_event_hash=eligibility.factor_definition_event_hash,
            prefinal_decision_event_hash=eligibility.prefinal_decision_event_hash,
            contract_event_hash=eligibility.contract_event_hash,
            provider_registration_event_hash=eligibility.provider_registration_event_hash,
            scope=replace(eligibility.scope, round_trip_cost_bps=9.0),
            dependence=eligibility.dependence,
        )
    with pytest.raises(EventTransitionError, match="already issued"):
        authority.issue(event.event_hash, run_id="predictive-run")
    assert authority.is_tainted(eligibility.final_evaluation_key)


def test_definition_without_prefinal_decision_cannot_mint_eligibility(
    tmp_path: Path,
) -> None:
    _, store, contract, _, definition = _setup(
        tmp_path,
        policy_references=EXECUTION_POLICY_REFERENCES,
        enable_decision=True,
    )
    dates = tuple(str(item.date()) for item in pd.bdate_range("2025-04-01", periods=10))
    provider = _RawProvider(store, dates)
    registry = FinalRawProviderRegistryV2({provider.descriptor().provider_id: provider})
    registration = FinalRawProviderRegistrationServiceV2(store, registry).register(
        provider.descriptor().provider_id, run_id="predictive-run"
    )
    digest = canonical_json_hash({"missing": "decision"})
    scope = FinalScopeConfigV2(
        provider_id=provider.descriptor().provider_id,
        provider_version=provider.descriptor().provider_version,
        data_snapshot_hash=digest,
        period_start=dates[0],
        period_end=dates[-1],
        raw_fields=("close", "membership", "open", "tradable"),
        transform_pipeline_hash=digest,
        cost_model_hash=digest,
        regime_config_hash=digest,
        backend_hash=DEFAULT_EXECUTABLE_GRAMMAR.snapshot_hash,
        execution_lag=1,
        return_horizon=5,
        rebalance_cadence=5,
        round_trip_cost_bps=8.0,
        selection_fraction=0.4,
    )
    dependence = FinalDependenceConfigV2("hac", 0.95, 1, 2, 2, 200, 17, 2, -1.0, -1.0)
    with pytest.raises(EventTransitionError, match="sources are incomplete"):
        FinalEvaluationEligibilityServiceV2(store).register(
            run_id="predictive-run",
            factor_definition_event_hash=definition.event_hash,
            prefinal_decision_event_hash=canonical_json_hash({"missing": "decision"}),
            contract_event_hash=contract.event.event_hash,
            provider_registration_event_hash=registration.event_hash,
            scope=scope,
            dependence=dependence,
        )


def test_provider_scope_failure_has_exactly_one_typed_terminal(
    tmp_path: Path,
) -> None:
    _, store, _, _, _, event, _, capability, runner, _ = _prepared(tmp_path, provider_mismatch=True)
    with pytest.raises(ValueError, match="provider scope"):
        runner.run(event.event_hash, capability, run_id="predictive-run")

    assert len(store.query_events(event_type="FinalTestFailedV2Recorded")) == 1
    assert not store.query_events(event_type="FinalTestArtifactV2Recorded")
    selection = store.query_events(event_type="FinalSelectionAssessmentV2Recorded")
    assert len(selection) == 1
    assert selection[0].payload["confirmatory_grade_eligible"] is False


def test_open_access_recovery_records_interrupted_failure(tmp_path: Path) -> None:
    _, store, _, _, eligibility, event, authority, capability, runner, _ = _prepared(tmp_path)
    authority.authorize(capability, eligibility, run_id="predictive-run")

    recovered = runner.reconcile_open_accesses()

    assert len(recovered) == 1
    assert recovered[0].payload["failure_code"] == "FINAL_RUN_INTERRUPTED"
    assert len(store.query_events(event_type="FinalTestFailedV2Recorded")) == 1
    assert not store.query_events(event_type="FinalTestArtifactV2Recorded")
    assert event.payload["final_evaluation_key"] == eligibility.final_evaluation_key
    selection = store.query_events(event_type="FinalSelectionAssessmentV2Recorded")[-1]
    assert selection.payload["confirmatory_grade_eligible"] is False


def test_late_taint_replays_into_decision_view(tmp_path: Path) -> None:
    _, store, _, _, _, event, authority, capability, runner, _ = _prepared(tmp_path)
    artifact = runner.run(event.event_hash, capability, run_id="predictive-run")
    before = runner.decision_view(artifact)
    authority.record_taint(
        event.event_hash,
        run_id="predictive-run",
        taint_class="variant",
        reason_code="FINAL_POST_ACCESS_VARIANT_DISCOVERED",
    )
    after = runner.decision_view(artifact)

    assert before["contaminated"] is False
    assert after["contaminated"] is True
    assert after["quality_passed"] is False
    assert after["confirmatory_grade"] is False
    assert after == runner.decision_view(artifact)
    assert store.verify_chain()


def test_same_capability_physical_reopen_is_denied_and_taints_family(
    tmp_path: Path,
) -> None:
    _, store, _, _, eligibility, event, authority, capability, runner, _ = _prepared(tmp_path)
    artifact = runner.run(event.event_hash, capability, run_id="predictive-run")

    with pytest.raises(EventTransitionError, match="FINAL_PHYSICAL_REOPEN"):
        authority.authorize(capability, eligibility, run_id="predictive-run")

    denied = store.query_events(event_type="FinalOutcomeAccessV2Recorded")[-1]
    assert denied.payload["outcome"] == "denied"
    assert denied.payload["taint_class"] == "physical"
    assert authority.is_tainted(eligibility.final_evaluation_key)
    assert len(store.query_events(event_type="FinalTestArtifactV2Recorded")) == 1
    assert runner.decision_view(artifact)["confirmatory_grade"] is False
    selection = store.query_events(event_type="FinalSelectionAssessmentV2Recorded")[-1]
    assert selection.payload["confirmatory_grade_eligible"] is False


def test_independent_recomputation_preserves_metrics_and_decision_scope(
    tmp_path: Path,
) -> None:
    first = _prepared(tmp_path / "first")
    second = _prepared(tmp_path / "second")
    first_artifact = first[8].run(first[5].event_hash, first[7], run_id="predictive-run")
    second_artifact = second[8].run(second[5].event_hash, second[7], run_id="predictive-run")
    first_view = first[8].decision_view(first_artifact)
    second_view = second[8].decision_view(second_artifact)

    assert first_artifact.metrics == second_artifact.metrics
    for key in (
        "promotion_ceiling",
        "one_shot",
        "confirmatory_grade",
        "contaminated",
        "quality_passed",
        "metrics",
        "limitations",
    ):
        assert first_view[key] == second_view[key]


def test_generic_append_cannot_forge_final_v2_authority(tmp_path: Path) -> None:
    _, store, _, _, _, _, _, _, _, _ = _prepared(tmp_path)
    with pytest.raises(EventValidationError, match="deterministic producer"):
        store.append_event(
            EventDraft(
                event_type="FinalTestArtifactV2Recorded",
                entity_id="forged-final",
                run_id="predictive-run",
                payload_schema_version="final_test_artifact_recorded.v2",
                payload={"quality_passed": True},
            )
        )


def test_confidence_lower_bound_not_mean_controls_quality() -> None:
    config = FinalDependenceConfigV2("hac", 0.95, 1, 2, 2, 200, 17, 3, 0.03, 0.0)
    mean, standard_error = _dependent_mean_se((0.01, 0.03, 0.05, 0.07), config)
    lower = mean - 1.959963984540054 * standard_error

    assert mean > config.rank_ic_sesoi
    assert lower < config.rank_ic_sesoi


def test_final_v2_feature_off_is_no_write(tmp_path: Path) -> None:
    flags = ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
            "VIBE_TRADING_DECISION_V2": "0",
        }
    )
    store = ResearchEventStore(
        tmp_path / "disabled.sqlite",
        artifact_root=tmp_path / "disabled-artifacts",
        flags=flags,
        code_version="final-v2-feature-off",
    )
    with pytest.raises(RuntimeError, match="disabled"):
        FinalEvaluationEligibilityServiceV2(store)
    with pytest.raises(RuntimeError, match="disabled"):
        FinalScopeAuthorityV2(store)
    assert store.query_events() == []


@pytest.mark.parametrize("method", ["hac", "nonoverlapping_cohort", "block_bootstrap"])
def test_dependence_methods_are_frozen_and_deterministic(method: str) -> None:
    config = FinalDependenceConfigV2(
        method,
        0.95,
        1,
        2,
        2,
        200,
        17,
        3,
        -1.0,
        -1.0,  # type: ignore[arg-type]
    )
    first = _dependent_mean_se((0.1, -0.1, 0.2, 0.0, 0.3), config)
    second = _dependent_mean_se((0.1, -0.1, 0.2, 0.0, 0.3), config)
    assert first == second
