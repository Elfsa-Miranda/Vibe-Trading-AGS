from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from src.alpha_quality.decision_v2 import (
    DecisionEvidenceRecord,
    DecisionEvidenceRefs,
    DecisionEvidenceRepository,
    DecisionV2Policy,
    QualityDecisionV2Service,
)
from src.alpha_quality.flags import ResolvedAGSFlags
from src.alpha_quality.forward import (
    ForwardKillRulesV3,
    ForwardMonitoringProducerV3,
    ForwardMonitoringProviderRegistryV3,
    ForwardPlanAuthorityV3,
    ForwardPlanConfigV3,
    ForwardRawRowV3,
    MonitoringProviderDescriptorV3,
)
from src.alpha_quality.scope import DiscoveryEvidenceProjector
from src.research_ledger.events import EventDraft, EventValidationError
from src.research_ledger.hash_utils import canonical_json_hash
from tests.alpha_quality.test_final_authority_v2 import _prepared


def _put(
    repository: DecisionEvidenceRepository,
    factor_spec_id: str,
    kind: str,
    payload: dict[str, object],
) -> str:
    return repository.put(
        DecisionEvidenceRecord.create(
            evidence_kind=kind,  # type: ignore[arg-type]
            factor_spec_id=factor_spec_id,
            payload=payload,
        )
    )


def _paper_context(tmp_path: Path):
    (
        flags,
        store,
        definition,
        _,
        eligibility,
        eligibility_event,
        final_authority,
        capability,
        runner,
        _,
    ) = _prepared(tmp_path)
    artifact = runner.run(eligibility_event.event_hash, capability, run_id="predictive-run")
    final_event = store.query_events(event_type="FinalTestArtifactV2Recorded")[0]
    final_view = runner.decision_view(artifact)
    repository = DecisionEvidenceRepository(tmp_path / "decision-evidence")
    factor_id = str(definition.payload["factor_spec_id"])
    scorecard = _put(
        repository,
        factor_id,
        "scorecard",
        {
            "formula_valid": True,
            "formula_ambiguous": False,
            "lookahead_detected": False,
            "train_valid_terminal": True,
            "reproducible": True,
            "bounded": True,
            "validation_rank_ic": 0.03,
            "regime_dependent": False,
            "limitations": ["TRAIN_VALID_ONLY"],
        },
    )
    execution = _put(
        repository,
        factor_id,
        "execution",
        {
            "available": True,
            "execution_alpha": 0.002,
            "total_cost": 0.0005,
            "economically_nonnegative": True,
            "limitations": [],
        },
    )
    snapshot = _put(
        repository,
        factor_id,
        "snapshot",
        {"pit_available": True, "survivorship_bias": False, "limitations": []},
    )
    ledger = _put(
        repository,
        factor_id,
        "ledger",
        {
            "complete": True,
            "terminal_train_valid": True,
            "reduced_durability": False,
            "ledger_schema_version": "decision_ledger_evidence.v2",
            "infrastructure_failure_event_hashes": [],
            "limitations": [],
        },
    )
    mechanism = _put(
        repository,
        factor_id,
        "mechanism",
        {
            "contract_registered": True,
            "decisive_available": True,
            "ordinal_state": "supported",
            "limitations": ["ORDINAL_NOT_PROBABILITY"],
        },
    )
    complement = _put(
        repository,
        factor_id,
        "complement",
        {"status": "complementary", "limitations": ["TRAIN_VALID_ONLY"]},
    )
    final_hash = _put(
        repository,
        factor_id,
        "final_test",
        {
            "frozen": True,
            "one_shot": True,
            "contaminated": final_view["contaminated"],
            "quality_passed": final_view["quality_passed"],
            "final_oos_ic": artifact.metrics.rank_ic_mean,
            "source_artifact_hash": artifact.artifact_hash,
            "view_hash": final_view["view_hash"],
            "limitations": list(final_view["limitations"]),
        },
    )
    recorded = QualityDecisionV2Service(
        store=store,
        flags=flags,
        policy=DecisionV2Policy(
            schema_version="decision_v2_policy.v1",
            policy_version="phase10c-paper-policy.1",
        ),
        repository=repository,
    ).decide_and_record(
        DecisionEvidenceRefs(
            factor_spec_id=factor_id,
            scorecard_hash=scorecard,
            execution_hash=execution,
            snapshot_hash=snapshot,
            ledger_watermark_hash=ledger,
            mechanism_evidence_hash=mechanism,
            complement_evidence_hash=complement,
            final_test_artifact_hash=final_hash,
            forward_plan_hash=None,
        ),
        run_id="predictive-run",
    )
    assert recorded.decision.decision == "paper_candidate"
    forward_settings = flags.as_dict()
    forward_settings["VIBE_TRADING_FORWARD_TRACKING"] = True
    forward_flags = ResolvedAGSFlags.from_settings(forward_settings)
    from src.research_ledger.events import ResearchEventStore

    store = ResearchEventStore(
        store.db_path,
        artifact_root=store.artifact_root,
        flags=forward_flags,
        code_version="phase10c-test",
    )
    descriptor = MonitoringProviderDescriptorV3(
        provider_id="forward-provider-v3-fixture",
        provider_version="3.0.0",
        calendar_id="calendar-daily-v1",
        vintage_policy="as_observed_append_only",
        maximum_availability_delay_days=0,
        provider_policy_hash=canonical_json_hash({"provider": "forward-v3-fixture"}),
    )
    registry = ForwardMonitoringProviderRegistryV3((descriptor,))
    authority = ForwardPlanAuthorityV3(store=store, registry=registry)
    provider_event = authority.register_provider(
        descriptor.provider_id, descriptor.provider_version, run_id="predictive-run"
    )
    return {
        "flags": forward_flags,
        "store": store,
        "factor_id": factor_id,
        "eligibility": eligibility,
        "eligibility_event": eligibility_event,
        "final_authority": final_authority,
        "final_event": final_event,
        "decision_event": recorded.event,
        "descriptor": descriptor,
        "registry": registry,
        "authority": authority,
        "provider_event": provider_event,
    }


def _config(context, *, minimum_look: int = 1, minimum_rank_ic: float = -1.0):
    descriptor = context["descriptor"]
    digest = canonical_json_hash({"forward": "v3-fixture"})
    return ForwardPlanConfigV3(
        factor_spec_id=context["factor_id"],
        forward_start=date.today().isoformat(),
        calendar_id=descriptor.calendar_id,
        return_horizon=5,
        minimum_look_observations=minimum_look,
        provider_id=descriptor.provider_id,
        provider_version=descriptor.provider_version,
        vintage_policy_hash=descriptor.vintage_policy_hash,
        availability_contract_hash=descriptor.availability_contract_hash,
        transform_pipeline_hash=digest,
        cost_model_hash=digest,
        regime_config_hash=digest,
        policy_hash=digest,
        kill_rules=ForwardKillRulesV3(
            minimum_rank_ic=minimum_rank_ic,
            minimum_net_return=-1.0,
            maximum_drawdown=1.0,
        ),
    )


def _register_plan(context, config=None):
    config = _config(context) if config is None else config
    return context["authority"].register_plan(
        config,
        final_artifact_event_hash=context["final_event"].event_hash,
        decision_event_hash=context["decision_event"].event_hash,
        provider_registration_event_hash=context["provider_event"].event_hash,
        run_id="predictive-run",
    )


def _rows(*, reversed_signal: bool = False):
    observed = date.today().isoformat()
    available = observed + "T00:00:00Z"
    rows = []
    for index, symbol in enumerate(("A", "B", "C", "D")):
        signal = float(index + 1)
        if reversed_signal:
            signal = -signal
        rows.append(
            ForwardRawRowV3(
                observed_on=observed,
                symbol=symbol,
                signal=signal,
                gross_return=0.01 * (index + 1),
                cost=0.0001,
                available_at=available,
            )
        )
    return tuple(rows)


def test_plan_and_observation_are_current_producer_bound_and_discovery_isolated(tmp_path: Path) -> None:
    context = _paper_context(tmp_path)
    before = DiscoveryEvidenceProjector(flags=context["flags"]).project(
        context["store"], data_snapshot_hash=canonical_json_hash({"snapshot": "forward"})
    )
    plan = _register_plan(context)
    producer = ForwardMonitoringProducerV3(store=context["store"], authority=context["authority"])
    source = producer.produce_source(plan.event_hash, _rows(), run_id="predictive-run")
    observation = producer.compute_observation(source.event_hash, run_id="predictive-run")
    after = DiscoveryEvidenceProjector(flags=context["flags"]).project(
        context["store"], data_snapshot_hash=canonical_json_hash({"snapshot": "forward"})
    )

    assert observation.payload["metrics"]["effective_observations"] == 1
    assert observation.payload["metrics"]["rank_ic"] == pytest.approx(1.0)
    assert observation.payload["success_claim"] is False
    assert observation.payload["status"] == "monitoring"
    assert before == after
    assert not set(inspect.signature(producer.compute_observation).parameters).intersection(
        {"rank_ic", "net_return", "drawdown", "effective_observations"}
    )
    assert context["store"].verify_chain()


def test_plan_requires_quality_decision_and_rejects_early_forward_start(tmp_path: Path) -> None:
    context = _paper_context(tmp_path)
    with pytest.raises(ValueError, match="forward_start"):
        _register_plan(context, replace(_config(context), forward_start="2020-01-01"))
    with pytest.raises(ValueError, match="current decision"):
        context["authority"].register_plan(
            _config(context),
            final_artifact_event_hash=context["final_event"].event_hash,
            decision_event_hash=context["final_event"].event_hash,
            provider_registration_event_hash=context["provider_event"].event_hash,
            run_id="predictive-run",
        )


def test_late_final_taint_invalidates_current_plan_and_blocks_observation(tmp_path: Path) -> None:
    context = _paper_context(tmp_path)
    plan = _register_plan(context)
    producer = ForwardMonitoringProducerV3(store=context["store"], authority=context["authority"])
    source = producer.produce_source(plan.event_hash, _rows(), run_id="predictive-run")
    context["final_authority"].record_taint(
        context["eligibility_event"].event_hash,
        run_id="predictive-run",
        taint_class="selection",
        reason_code="LATE_SELECTION_TAINT",
    )
    view = context["authority"].current_plan_view(plan.event_hash)
    assert view["eligible"] is False
    assert view["late_final_taint"] is True
    with pytest.raises(ValueError, match="late final taint"):
        producer.compute_observation(source.event_hash, run_id="predictive-run")


def test_no_backfill_minimum_look_and_kill_rules_are_executed(tmp_path: Path) -> None:
    context = _paper_context(tmp_path)
    plan = _register_plan(context, _config(context, minimum_look=2, minimum_rank_ic=0.5))
    producer = ForwardMonitoringProducerV3(store=context["store"], authority=context["authority"])
    source = producer.produce_source(plan.event_hash, _rows(reversed_signal=True), run_id="predictive-run")
    observation = producer.compute_observation(source.event_hash, run_id="predictive-run")
    assert observation.payload["status"] == "insufficient"
    assert observation.payload["success_claim"] is False
    with pytest.raises(ValueError, match="strictly increasing"):
        producer.produce_source(plan.event_hash, _rows(), run_id="predictive-run")

    context2 = _paper_context(tmp_path / "kill")
    kill_plan = _register_plan(context2, _config(context2, minimum_look=1, minimum_rank_ic=0.5))
    kill_producer = ForwardMonitoringProducerV3(store=context2["store"], authority=context2["authority"])
    kill_source = kill_producer.produce_source(
        kill_plan.event_hash, _rows(reversed_signal=True), run_id="predictive-run"
    )
    killed = kill_producer.compute_observation(kill_source.event_hash, run_id="predictive-run")
    assert killed.payload["status"] == "kill_triggered"
    assert "RANK_IC_KILL_RULE" in killed.payload["kill_reasons"]


def test_revision_preserves_original_and_builds_separate_restated_view(tmp_path: Path) -> None:
    context = _paper_context(tmp_path)
    plan = _register_plan(context)
    producer = ForwardMonitoringProducerV3(store=context["store"], authority=context["authority"])
    source = producer.produce_source(plan.event_hash, _rows(), run_id="predictive-run")
    producer.compute_observation(source.event_hash, run_id="predictive-run")
    original = producer.monitoring_view(plan.event_hash)
    snapshot_hash = canonical_json_hash({"snapshot": "forward-revision"})
    discovery_before = DiscoveryEvidenceProjector(flags=context["flags"]).project(
        context["store"], data_snapshot_hash=snapshot_hash
    )
    revision = producer.record_revision(
        source.event_hash,
        _rows(reversed_signal=True),
        reason_code="PROVIDER_CORRECTION",
        run_id="predictive-run",
    )
    restated = producer.monitoring_view(plan.event_hash, restated=True)
    discovery_after = DiscoveryEvidenceProjector(flags=context["flags"]).project(
        context["store"], data_snapshot_hash=snapshot_hash
    )

    assert original["view_kind"] == "original_as_observed"
    assert restated["view_kind"] == "restated"
    assert original["observations"][0]["metrics"]["rank_ic"] == pytest.approx(1.0)
    assert restated["observations"][0]["metrics"]["rank_ic"] == pytest.approx(-1.0)
    assert source.payload["source_hash"] == original["observations"][0]["source_hash"]
    assert revision.payload["original_source_hash"] == source.payload["source_hash"]
    assert discovery_before == discovery_after
    assert context["store"].verify_chain()


def test_protected_events_reject_generic_metric_or_revision_forgery(tmp_path: Path) -> None:
    context = _paper_context(tmp_path)
    for event_type, version in (
        ("ForwardPlanV3Recorded", "forward_plan_recorded.v3"),
        ("ForwardObservationV3Recorded", "forward_observation_recorded.v3"),
        ("DataRevisionRecorded", "data_revision_recorded.v1"),
    ):
        with pytest.raises(EventValidationError, match="deterministic producer"):
            context["store"].append_event(
                EventDraft(
                    event_type=event_type,
                    entity_id="forged",
                    run_id="predictive-run",
                    payload_schema_version=version,
                    payload={},
                )
            )


def test_feature_off_constructs_no_v3_authority(tmp_path: Path) -> None:
    flags = ResolvedAGSFlags.from_settings({"VIBE_TRADING_AGS_ENABLED": "1", "VIBE_TRADING_RESEARCH_EVENTS": "1"})
    from src.research_ledger.events import ResearchEventStore

    store = ResearchEventStore(
        tmp_path / "research.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=flags,
        code_version="phase10c-test",
    )
    descriptor = MonitoringProviderDescriptorV3(
        "provider",
        "3",
        "calendar",
        "as_observed_append_only",
        0,
        canonical_json_hash({"provider": 3}),
    )
    with pytest.raises(RuntimeError, match="disabled"):
        ForwardPlanAuthorityV3(
            store=store,
            registry=ForwardMonitoringProviderRegistryV3((descriptor,)),
        )
    assert store.query_events() == []
