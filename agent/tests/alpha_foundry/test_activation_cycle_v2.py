from __future__ import annotations

from dataclasses import fields
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.alpha_foundry.activation.coordinator_v2 import ActivationPairCoordinatorV2
from src.alpha_foundry.activation.governance_v2 import (
    ActivationGovernanceService,
    ActivationReadinessServiceV4,
)
from src.alpha_foundry.activation.isolated_worker_v3 import IsolatedWorkerTaskV3
from src.alpha_foundry.activation.pair_projector_v2 import (
    ActivationArmEventRefsV2,
    ActivationEvidenceProjector,
    ActivationPairEvidenceV2,
)
from src.alpha_foundry.activation.protocol_v2 import (
    ActivationApplicabilityMatrixV1,
    ActivationProtocolRegistryV2,
    PreregisteredActivationStatisticalProtocolV2,
)
from src.alpha_foundry.activation.run_source_v3 import FormalActivationRunSourceV3
from src.alpha_foundry.activation.statistical_v2 import (
    ActivationStatisticalAnalyzerV2,
    PreregisteredConfirmatoryActivationPlanV2,
)
from src.alpha_quality.flags import ResolvedAGSFlags
from src.research_ledger.events import EventTransitionError, ResearchEventStore
from src.research_ledger.hash_utils import canonical_json_hash


def _hash(name: str) -> str:
    return canonical_json_hash({"activation-cycle-v2-fixture": name})


def _protocol(*, sesoi: float = 0.05, maximum_pairs: int = 128):
    return PreregisteredActivationStatisticalProtocolV2.create(
        research_cycle_id="activation-cycle-v2-test",
        normalization_policy_hash=_hash("normalization"),
        registration_event_hash=_hash("registration"),
        code_manifest_hash=_hash("code"),
        sesoi=sesoi,
        minimum_pairs=2,
        maximum_pairs=maximum_pairs,
    )


def _registered_protocol(
    tmp_path: Path,
    *,
    sesoi: float = 0.05,
    maximum_pairs: int = 128,
):
    store = _store(tmp_path)
    protocol = PreregisteredActivationStatisticalProtocolV2.create(
        research_cycle_id="activation-cycle-v2-test",
        normalization_policy_hash=_hash("normalization"),
        registration_event_hash="sha256:" + "0" * 64,
        code_manifest_hash=_hash("code"),
        sesoi=sesoi,
        minimum_pairs=2,
        maximum_pairs=maximum_pairs,
    )
    recorded = ActivationProtocolRegistryV2(store).register_protocol(
        run_id="activation-cycle-v2-test",
        protocol=protocol,
    )
    return store, recorded


def _pair(
    name: str,
    difference: float,
    *,
    source_complete: bool = True,
    failure_codes: tuple[str, ...] = (),
    safety_pass: bool | None = True,
    resource_pass: bool | None = True,
) -> ActivationPairEvidenceV2:
    budget = 100
    flat_yield = 20
    topology_yield = flat_yield + round(difference * budget)
    codes = tuple(sorted(set(failure_codes)))
    if source_complete != (not codes):
        raise ValueError("fixture source completeness mismatch")
    content = {
        "schema_version": "activation_pair_evidence.v2",
        "plan_hash": _hash("plan"),
        "pair_id": f"pair-{name}",
        "run_group_id": f"group-{name}",
        "candidate_budget": budget,
        "flat_source_audit_hash": _hash(f"flat-{name}"),
        "topology_source_audit_hash": _hash(f"topology-{name}"),
        "flat_qualified_yield": flat_yield,
        "topology_qualified_yield": topology_yield,
        "normalized_yield_difference": difference,
        "flat_terminal_count": budget,
        "topology_terminal_count": budget,
        "flat_failure_count": 0,
        "topology_failure_count": 0,
        "candidate_set_jaccard": 0.5,
        "changed_selection_rate": 0.5,
        "safety_noninferiority_pass": safety_pass,
        "resource_noninferiority_pass": resource_pass,
        "source_failure_codes": list(codes),
        "source_complete": source_complete,
    }
    return ActivationPairEvidenceV2(
        schema_version="activation_pair_evidence.v2",
        plan_hash=content["plan_hash"],
        pair_id=content["pair_id"],
        run_group_id=content["run_group_id"],
        candidate_budget=budget,
        flat_source_audit_hash=content["flat_source_audit_hash"],
        topology_source_audit_hash=content["topology_source_audit_hash"],
        flat_qualified_yield=flat_yield,
        topology_qualified_yield=topology_yield,
        normalized_yield_difference=difference,
        flat_terminal_count=budget,
        topology_terminal_count=budget,
        flat_failure_count=0,
        topology_failure_count=0,
        candidate_set_jaccard=0.5,
        changed_selection_rate=0.5,
        safety_noninferiority_pass=safety_pass,
        resource_noninferiority_pass=resource_pass,
        source_failure_codes=codes,
        source_complete=source_complete,
        evidence_hash=canonical_json_hash(content),
    )


def _audit(arm: str, *, candidates: tuple[str, ...], effective: tuple[str, ...]):
    counts = (
        ("success", len(candidates)),
        ("reject", 0),
        ("skip", 0),
        ("invalid", 0),
        ("duplicate", 0),
        ("timeout", 0),
        ("error", 0),
        ("infrastructure_failure", 0),
    )
    content = {
        "schema_version": "formal_activation_run_source.v3",
        "plan_hash": _hash("plan"),
        "pair_id": "pair-source",
        "run_group_id": "group-source",
        "arm": arm,
        "execution_run_id": f"run-{arm}",
        "source_watermark_event_hash": _hash(f"watermark-{arm}"),
        "retrieval_authority_event_hashes": [_hash(f"retrieval-{arm}")],
        "terminal_event_hashes": [_hash(f"terminal-{arm}")],
        "evaluation_event_hashes": [_hash(f"evaluation-{arm}")],
        "quality_decision_event_hashes": [_hash(f"decision-{arm}")],
        "terminal_dossier_event_hashes": [_hash(f"dossier-{arm}")],
        "derived_terminal_status_counts": [list(item) for item in counts],
        "derived_candidate_ids": list(candidates),
        "derived_effective_candidate_ids": list(effective),
        "source_failure_codes": [],
        "source_complete": True,
    }
    return FormalActivationRunSourceV3(
        schema_version="formal_activation_run_source.v3",
        plan_hash=content["plan_hash"],
        pair_id="pair-source",
        run_group_id="group-source",
        arm=arm,  # type: ignore[arg-type]
        execution_run_id=f"run-{arm}",
        source_watermark_event_hash=content["source_watermark_event_hash"],
        retrieval_authority_event_hashes=tuple(content["retrieval_authority_event_hashes"]),
        terminal_event_hashes=tuple(content["terminal_event_hashes"]),
        evaluation_event_hashes=tuple(content["evaluation_event_hashes"]),
        quality_decision_event_hashes=tuple(content["quality_decision_event_hashes"]),
        terminal_dossier_event_hashes=tuple(content["terminal_dossier_event_hashes"]),
        derived_terminal_status_counts=counts,
        derived_candidate_ids=candidates,
        derived_effective_candidate_ids=effective,
        source_failure_codes=(),
        source_complete=True,
        audit_hash=canonical_json_hash(content),
    )


def _store(tmp_path: Path) -> ResearchEventStore:
    flags = ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_ALPHA_FOUNDRY": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
            "VIBE_TRADING_TOPOLOGY_RETRIEVER": "1",
        }
    )
    return ResearchEventStore(
        tmp_path / "events.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=flags,
        code_version="activation-cycle-v2-test",
    )


def test_activation_projector_delegates_to_run_source_v3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projector = ActivationEvidenceProjector(_store(tmp_path))
    audits = {
        "flat": _audit("flat", candidates=("a", "b"), effective=("a",)),
        "topology": _audit(
            "topology", candidates=("b", "c"), effective=("b", "c")
        ),
    }
    calls: list[str] = []

    def audit(**kwargs: object):
        arm = str(kwargs["arm"])
        calls.append(arm)
        return audits[arm]

    monkeypatch.setattr(projector.run_source, "audit", audit)
    refs = ActivationArmEventRefsV2(
        retrieval_authority_event_hashes=(_hash("r"),),
        terminal_event_hashes=(_hash("t"),),
        evaluation_event_hashes=(_hash("e"),),
        quality_decision_event_hashes=(_hash("q"),),
        terminal_dossier_event_hashes=(_hash("d"),),
    )

    projected = projector.project_pair(
        plan_hash=_hash("plan"),
        pair_id="pair-source",
        run_group_id="group-source",
        candidate_budget=2,
        flat_refs=refs,
        topology_refs=refs,
    )

    assert calls == ["flat", "topology"]
    assert projected.flat_qualified_yield == 1
    assert projected.topology_qualified_yield == 2
    assert projected.normalized_yield_difference == 0.5


def test_projector_rebuilds_all_metrics_from_exact_events() -> None:
    signature = inspect.signature(ActivationEvidenceProjector.project_pair)
    forbidden = {"yield", "score", "worker_summary", "report_json", "metrics"}

    assert forbidden.isdisjoint(signature.parameters)
    assert "FormalActivationRunSourceAuditorV3" in inspect.getsource(
        ActivationEvidenceProjector
    )


def test_projector_rebuilds_metrics_from_exact_events() -> None:
    test_projector_rebuilds_all_metrics_from_exact_events()


def test_projector_rejects_empty_or_noncanonical_event_refs() -> None:
    with pytest.raises(ValueError, match="non-zero canonical hashes"):
        ActivationArmEventRefsV2(
            retrieval_authority_event_hashes=(),
            terminal_event_hashes=(_hash("terminal"),),
            evaluation_event_hashes=(_hash("evaluation"),),
            quality_decision_event_hashes=(_hash("decision"),),
            terminal_dossier_event_hashes=(_hash("dossier"),),
        )
    with pytest.raises(ValueError, match="non-zero canonical hashes"):
        ActivationArmEventRefsV2(
            retrieval_authority_event_hashes=("not-a-hash",),
            terminal_event_hashes=(_hash("terminal"),),
            evaluation_event_hashes=(_hash("evaluation"),),
            quality_decision_event_hashes=(_hash("decision"),),
            terminal_dossier_event_hashes=(_hash("dossier"),),
        )


def test_phase8_effective_sample_is_not_activation_pair_count() -> None:
    parameters = inspect.signature(
        ActivationStatisticalAnalyzerV2.mint_confirmatory_plan
    ).parameters

    assert "phase8_effective_sample" not in parameters
    assert "effective_sample" not in parameters


def test_primary_test_does_not_switch_after_normality_test() -> None:
    protocol = _protocol()

    assert protocol.primary_test == "paired_randomization_sign_flip.v1"
    assert "normality" not in inspect.getsource(ActivationStatisticalAnalyzerV2)


def test_pilot_observed_uplift_cannot_change_sesoi(tmp_path: Path) -> None:
    store, registered = _registered_protocol(tmp_path, sesoi=0.07)
    analyzer = ActivationStatisticalAnalyzerV2(store)
    low = analyzer.summarize_pilot(
        registered_protocol=registered,
        pairs=(_pair("low-1", 0.0), _pair("low-2", 0.01)),
        preregistered_variance_floor=0.01,
    )
    high = analyzer.summarize_pilot(
        registered_protocol=registered,
        pairs=(_pair("high-1", 0.5), _pair("high-2", 0.6)),
        preregistered_variance_floor=0.01,
    )

    assert analyzer.mint_confirmatory_plan(
        registered_protocol=registered, pilot=low, strata=("all",)
    ).fixed_sesoi == 0.07
    assert analyzer.mint_confirmatory_plan(
        registered_protocol=registered, pilot=high, strata=("all",)
    ).fixed_sesoi == 0.07
    assert not hasattr(low, "observed_uplift")


def test_power_uses_fixed_sesoi_and_conservative_variance_rule(
    tmp_path: Path,
) -> None:
    store, registered = _registered_protocol(tmp_path, sesoi=0.05)
    analyzer = ActivationStatisticalAnalyzerV2(store)
    protocol = registered.protocol
    pilot = analyzer.summarize_pilot(
        registered_protocol=registered,
        pairs=(_pair("v1", 0.01), _pair("v2", 0.02)),
        preregistered_variance_floor=0.04,
    )
    plan = analyzer.mint_confirmatory_plan(
        registered_protocol=registered, pilot=pilot, strata=("all",)
    )

    assert pilot.conservative_variance_upper_bound >= 0.04
    assert plan.fixed_sesoi == protocol.sesoi
    assert plan.required_pairs >= protocol.minimum_pairs
    assert plan.conservative_variance_upper_bound == (
        pilot.conservative_variance_upper_bound
    )
    assert plan.power_simulation_seed == protocol.power_simulation_seed
    assert plan.power_simulation_count == protocol.power_simulation_count
    assert "simulation" in protocol.power_method


def test_confirmatory_plan_cannot_change_after_mint() -> None:
    assert PreregisteredConfirmatoryActivationPlanV2.__dataclass_params__.frozen


def test_confirmatory_not_feasible_does_not_lower_thresholds(
    tmp_path: Path,
) -> None:
    store, registered = _registered_protocol(
        tmp_path, sesoi=0.01, maximum_pairs=2
    )
    analyzer = ActivationStatisticalAnalyzerV2(store)
    protocol = registered.protocol
    pilot = analyzer.summarize_pilot(
        registered_protocol=registered,
        pairs=(_pair("wide-1", -0.4), _pair("wide-2", 0.4)),
        preregistered_variance_floor=1.0,
    )
    plan = analyzer.mint_confirmatory_plan(
        registered_protocol=registered, pilot=pilot, strata=("all",)
    )

    assert plan.feasible is False
    assert plan.required_pairs > plan.maximum_pairs
    assert plan.seeds == ()
    assert plan.fixed_sesoi == protocol.sesoi


def _analysis(
    tmp_path: Path,
    values: tuple[float, ...],
    *,
    required_pairs: int,
    source_failure: str | None = None,
    safety_pass: bool | None = True,
    resource_pass: bool | None = True,
):
    store, registered = _registered_protocol(tmp_path)
    protocol = registered.protocol
    pairs = tuple(
        _pair(
            str(index),
            value,
            source_complete=source_failure is None,
            failure_codes=(() if source_failure is None else (source_failure,)),
            safety_pass=safety_pass,
            resource_pass=resource_pass,
        )
        for index, value in enumerate(values)
    )
    analysis = ActivationStatisticalAnalyzerV2(store).analyze(
        registered_protocol=registered,
        pairs=pairs,
        required_pairs=required_pairs,
    )
    return protocol, analysis


def test_analyzer_requires_registered_protocol(tmp_path: Path) -> None:
    protocol = _protocol()
    store = _store(tmp_path / "unregistered")
    with pytest.raises(TypeError, match="registry-minted frozen protocol"):
        ActivationStatisticalAnalyzerV2(store).analyze(
            registered_protocol=protocol,  # type: ignore[arg-type]
            pairs=(_pair("one", 0.1), _pair("two", 0.1)),
            required_pairs=2,
        )


def test_analyzer_rejects_protocol_registered_in_another_store(
    tmp_path: Path,
) -> None:
    _, registered = _registered_protocol(tmp_path / "source")
    other_store = _store(tmp_path / "other")

    with pytest.raises(
        EventTransitionError, match="not in the analyzer event store"
    ):
        ActivationStatisticalAnalyzerV2(other_store).analyze(
            registered_protocol=registered,
            pairs=(_pair("one", 0.1), _pair("two", 0.1)),
            required_pairs=2,
        )


def test_approval_requires_lcb_above_sesoi(tmp_path: Path) -> None:
    protocol, analysis = _analysis(
        tmp_path, (0.2, 0.2, 0.2, 0.2), required_pairs=4
    )
    decision = ActivationGovernanceService().decide(
        protocol=protocol, analysis=analysis
    )

    assert analysis.confidence_lower > protocol.sesoi
    assert decision.verdict == "approved"


def test_p_value_alone_cannot_approve(tmp_path: Path) -> None:
    protocol, analysis = _analysis(
        tmp_path, (0.2, 0.2, 0.2, 0.2, 0.2, 0.2), required_pairs=8
    )
    decision = ActivationGovernanceService().decide(
        protocol=protocol, analysis=analysis
    )

    assert analysis.one_sided_randomization_p_value is not None
    assert analysis.one_sided_randomization_p_value < protocol.alpha_level
    assert decision.verdict == "inconclusive"


def test_safety_failure_blocks_approval(tmp_path: Path) -> None:
    protocol, analysis = _analysis(
        tmp_path,
        (0.2, 0.2, 0.2, 0.2),
        required_pairs=4,
        safety_pass=False,
    )

    assert ActivationGovernanceService().decide(
        protocol=protocol, analysis=analysis
    ).verdict == "rejected"


def test_underpowered_valid_experiment_is_inconclusive(tmp_path: Path) -> None:
    protocol, analysis = _analysis(tmp_path, (0.2, 0.2), required_pairs=4)

    assert ActivationGovernanceService().decide(
        protocol=protocol, analysis=analysis
    ).verdict == "inconclusive"


def test_protocol_violation_is_invalidated_not_inconclusive(
    tmp_path: Path,
) -> None:
    protocol, analysis = _analysis(
        tmp_path,
        (0.2, 0.2),
        required_pairs=2,
        source_failure="CONTAMINATION",
    )

    assert ActivationGovernanceService().decide(
        protocol=protocol, analysis=analysis
    ).verdict == "invalidated"


def test_adequately_powered_no_effect_is_rejected(tmp_path: Path) -> None:
    protocol, analysis = _analysis(
        tmp_path, (0.0, 0.0, 0.0, 0.0), required_pairs=4
    )

    assert ActivationGovernanceService().decide(
        protocol=protocol, analysis=analysis
    ).verdict == "rejected"


def test_governance_rejects_caller_authored_statistics() -> None:
    with pytest.raises(TypeError, match="caller statistics"):
        ActivationGovernanceService().decide_from_mapping(
            {"statistics": {"p_value": 0.001}}
        )


def test_report_cannot_be_governance_input() -> None:
    with pytest.raises(TypeError, match="report"):
        ActivationGovernanceService().decide_from_mapping(
            {"report_json": {"verdict": "approved"}}
        )


def test_governance_cannot_accept_report_json() -> None:
    test_report_cannot_be_governance_input()


def test_governance_cannot_accept_worker_summary() -> None:
    with pytest.raises(TypeError, match="summary"):
        ActivationGovernanceService().decide_from_mapping(
            {"worker_summary": {"success_count": 12}}
        )


def test_worker_has_no_ledger_write_handle() -> None:
    worker_fields = {field.name for field in fields(IsolatedWorkerTaskV3)}

    assert worker_fields.isdisjoint({"store", "ledger", "db_path", "artifact_root"})


def test_cross_arm_rng_cache_and_working_state_are_isolated() -> None:
    schedule = SimpleNamespace(
        flat_rng_namespace="pair/flat/rng",
        topology_rng_namespace="pair/topology/rng",
        flat_cache_namespace="pair/flat/cache",
        topology_cache_namespace="pair/topology/cache",
    )
    flat, topology = ActivationPairCoordinatorV2.working_states(schedule)

    assert flat.rng_namespace != topology.rng_namespace
    assert flat.cache_namespace != topology.cache_namespace
    assert flat.working_memory_namespace != topology.working_memory_namespace


def test_pair_completion_precedes_shared_memory_update() -> None:
    coordinator = object.__new__(ActivationPairCoordinatorV2)
    coordinator._shared_memory_updates = []
    incomplete = coordinator.close_pair(
        schedule_hash=_hash("schedule"),
        flat_completed_event_hash=_hash("flat"),
        topology_completed_event_hash=None,
    )

    with pytest.raises(RuntimeError, match="completion"):
        coordinator.update_shared_process_memory(incomplete)


def test_incomplete_pair_is_never_silently_deleted() -> None:
    coordinator = object.__new__(ActivationPairCoordinatorV2)
    coordinator._shared_memory_updates = []
    result = coordinator.close_pair(
        schedule_hash=_hash("schedule"),
        flat_completed_event_hash=None,
        topology_completed_event_hash=_hash("topology"),
    )

    assert result.complete is False
    assert result.incomplete_reason_codes == ("FLAT_ARM_INCOMPLETE",)


def test_orchestrator_cannot_mint_activation_decision() -> None:
    coordinator = object.__new__(ActivationPairCoordinatorV2)
    with pytest.raises(PermissionError, match="no Activation decision authority"):
        coordinator.mint_activation_decision()


def test_readiness_does_not_treat_valid_chain_as_complete_source_replay(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    readiness, event = ActivationReadinessServiceV4(store).assess(
        run_id="readiness-run",
        research_cycle_id="readiness-cycle",
    )

    assert store.verify_chain()
    assert readiness.source_replay_complete is False
    assert readiness.resource_isolation_verified is False
    assert readiness.quality_decision_v3_producer_bound is False
    assert readiness.governance_roles_separated is True
    assert readiness.ready_for_pilot_outcome_access is False
    assert "SOURCE_REPLAY_COMPLETE" in readiness.blocker_codes
    assert "QUALITY_DECISION_V3_PRODUCER_BOUND" in readiness.blocker_codes
    assert event.payload["readiness_hash"] == readiness.readiness_hash


def test_applicability_matrix_is_frozen_before_candidate_execution() -> None:
    matrix = ActivationApplicabilityMatrixV1.create_default(
        research_cycle_id="cycle",
        profile="a-share-production",
        source_hash=_hash("source"),
        frozen_before_event_hash=_hash("watermark"),
        code_manifest_hash=_hash("code"),
    )

    assert matrix.frozen_before_event_hash == _hash("watermark")


def test_not_applicable_requires_producer_assessment() -> None:
    matrix = ActivationApplicabilityMatrixV1.create_default(
        research_cycle_id="cycle",
        profile="a-share-production",
        source_hash=_hash("source"),
        frozen_before_event_hash=_hash("watermark"),
        code_manifest_hash=_hash("code"),
    )
    conditional = [
        rule for rule in matrix.rules if rule.allowed_na_rule == "producer_not_applicable_only"
    ]

    assert conditional
    assert all(rule.required_evidence_producer for rule in conditional)


def test_final_and_forward_are_not_applicable_to_discovery_activation_cycle() -> None:
    matrix = ActivationApplicabilityMatrixV1.create_default(
        research_cycle_id="cycle",
        profile="a-share-production",
        source_hash=_hash("source"),
        frozen_before_event_hash=_hash("watermark"),
        code_manifest_hash=_hash("code"),
    )
    rule = next(rule for rule in matrix.rules if rule.claim_type == "final_forward")

    assert rule.execution_stage == "forbidden"
    assert rule.activation_effect == "not_applicable_no_feedback"
