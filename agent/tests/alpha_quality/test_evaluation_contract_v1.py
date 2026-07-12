from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.alpha_foundry.dsl.identity import FactorIdentityService, FactorSpecSemantics
from src.alpha_quality.evaluation_contract import (
    DEFAULT_PROFILE_REGISTRY,
    DEFAULT_POLICY_REFERENCES,
    TIER_INVARIANTS,
    ApplicabilityAssessmentServiceV1,
    EvaluationPolicyReferencesV1,
    EvaluationProfileTemplateV1,
    ResolvedEvaluationContractServiceV1,
    validate_narrative_claim_guard,
)
from src.alpha_quality.evaluation_registry_v1 import EvaluationPolicyRegistryServiceV1
from src.alpha_quality.flags import ResolvedAGSFlags
from src.research_ledger.events import (
    EventDraft,
    EventTransitionError,
    EventValidationError,
    ResearchEventStore,
)
from src.research_ledger.hash_utils import canonical_json_hash, utc_now_iso


def _hash(label: str) -> str:
    return canonical_json_hash({"label": label})


def _flags(*, scorecard: bool = True) -> ResolvedAGSFlags:
    settings = {
        "VIBE_TRADING_AGS_ENABLED": "1",
        "VIBE_TRADING_RESEARCH_EVENTS": "1",
        "VIBE_TRADING_ALPHA_FOUNDRY": "1",
        "VIBE_TRADING_FACTOR_DAG": "1",
    }
    if scorecard:
        settings["VIBE_TRADING_ALPHA_SCORECARD"] = "1"
    return ResolvedAGSFlags.from_settings(settings)


def _store(tmp_path: Path, *, scorecard: bool = True) -> ResearchEventStore:
    flags = _flags(scorecard=scorecard)
    return ResearchEventStore(
        tmp_path / "events.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=flags,
        code_version="evaluation-contract-test",
    )


def _dates() -> tuple[str, ...]:
    return tuple(f"2025-01-{day:02d}" for day in range(1, 25))


def _policy(store: ResearchEventStore, run_id: str):
    return EvaluationPolicyRegistryServiceV1(store, flags=store.flags).register(
        dates=_dates(),
        return_horizons=(1,),
        execution_horizon=1,
        holding_period=1,
        rebalance_cadence=1,
        train=("2025-01-01", "2025-01-06"),
        valid=("2025-01-09", "2025-01-14"),
        test=("2025-01-17", "2025-01-22"),
        run_id=run_id,
    )


def _refs(suffix: str = "base") -> EvaluationPolicyReferencesV1:
    if suffix != "base":
        raise ValueError("test requested an unregistered policy bundle")
    return DEFAULT_POLICY_REFERENCES


def _contract(store: ResearchEventStore, run_id: str = "contract-run"):
    policy = _policy(store, run_id)
    resolved = ResolvedEvaluationContractServiceV1(
        store, flags=store.flags
    ).register(
        run_id=run_id,
        evaluation_policy_event_hash=policy.event.event_hash,
        profile_id="production_candidate",
        profile_version="1",
        policy_references=_refs(),
    )
    return policy, resolved


def _semantics(*, mechanism: bool = False) -> FactorSpecSemantics:
    return FactorSpecSemantics(
        transform_pipeline_hash=_hash("transform"),
        field_semantics={"close": "mechanism:reversal" if mechanism else "close_t"},
        signal_time="close_t",
        order_time="close_t_plus_1",
        entry_price_time="open_t_plus_1",
        execution_lag=1,
        return_horizon=1,
        universe_mask_hash=_hash("universe-mask"),
        tradability_mask_hash=_hash("tradability-mask"),
    )


def _factor(store: ResearchEventStore, *, run_id: str, mechanism: bool = False):
    result = FactorIdentityService(store=store, flags=store.flags).record_attempt(
        trial_id="trial-" + ("mechanism" if mechanism else "plain"),
        run_id=run_id,
        candidate_id="candidate-" + ("mechanism" if mechanism else "plain"),
        formula="rank(close)",
        semantics=_semantics(mechanism=mechanism),
    )
    assert result.factor_spec_id is not None
    return store.query_events(
        event_type="FactorDefinitionRecorded", entity_id=result.factor_spec_id
    )[0]


def test_run_binds_exact_profile_template_and_contract_hash(tmp_path: Path) -> None:
    store = _store(tmp_path)
    policy, resolved = _contract(store)
    retry = ResolvedEvaluationContractServiceV1(store, flags=store.flags).register(
        run_id="contract-run",
        evaluation_policy_event_hash=policy.event.event_hash,
        profile_id="production_candidate",
        profile_version="1",
        policy_references=_refs(),
    )
    assert retry.event.event_hash == resolved.event.event_hash
    assert retry.contract.contract_hash == resolved.contract.contract_hash
    assert [event.event_type for event in store.query_events()] == [
        "EvaluationPolicyRegistered", "ResolvedEvaluationContractRegistered"
    ]
    assert store.verify_chain()


def test_profile_cannot_change_after_run_events_begin(tmp_path: Path) -> None:
    store = _store(tmp_path)
    policy = _policy(store, "late-contract")
    store.append_event(
        EventDraft(
            event_type="TrialStarted",
            entity_id="late-trial",
            run_id="late-contract",
            payload_schema_version="trial_started.v1",
            payload={
                "trial_id": "late-trial", "candidate_id": "late-candidate",
                "data_scope": "train_valid", "objective": "late",
                "started_at": utc_now_iso(),
            },
        )
    )
    with pytest.raises(EventTransitionError, match="second run event"):
        ResolvedEvaluationContractServiceV1(store, flags=store.flags).register(
            run_id="late-contract",
            evaluation_policy_event_hash=policy.event.event_hash,
            profile_id="production_candidate",
            profile_version="1",
            policy_references=_refs(),
        )


def test_custom_profile_is_capped_at_research_only() -> None:
    production = DEFAULT_PROFILE_REGISTRY.resolve_exact("production_candidate", "1")
    custom = DEFAULT_PROFILE_REGISTRY.build_custom(
        profile_id="custom-demo",
        profile_version="1",
        claim_requirements=production.claim_requirements,
        applicability_rule_hashes=production.applicability_rule_hashes,
        requested_maximum_promotion="forward_track",
    )
    assert custom.maximum_promotion == "research_only"
    assert custom.authority_class == "custom_research_only"


def test_custom_profile_contract_persists_research_only_ceiling(tmp_path: Path) -> None:
    store = _store(tmp_path)
    policy = _policy(store, "custom-profile-run")
    production = DEFAULT_PROFILE_REGISTRY.resolve_exact("production_candidate", "1")
    custom = DEFAULT_PROFILE_REGISTRY.build_custom(
        profile_id="custom-demo",
        profile_version="1",
        claim_requirements=production.claim_requirements,
        applicability_rule_hashes=production.applicability_rule_hashes,
        requested_maximum_promotion="forward_track",
    )
    resolved = ResolvedEvaluationContractServiceV1(
        store, flags=store.flags
    ).register(
        run_id="custom-profile-run",
        evaluation_policy_event_hash=policy.event.event_hash,
        profile_id="custom-demo",
        profile_version="1",
        policy_references=_refs(),
        custom_profile=custom,
    )
    assert resolved.event.payload["maximum_promotion"] == "research_only"
    assert resolved.event.payload["profile_authority_class"] == "custom_research_only"
    assert store.verify_chain()


def test_condition_uses_closed_declarative_predicate(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _, resolved = _contract(store, "closed-rule")
    factor = _factor(store, run_id="closed-rule")
    assessment = ApplicabilityAssessmentServiceV1(store, flags=store.flags).assess(
        run_id="closed-rule",
        contract_event_hash=resolved.event.event_hash,
        factor_definition_event_hash=factor.event_hash,
        claim_type="mechanism",
    )
    assert assessment.assessment.result == "not_applicable"
    assert assessment.assessment.applicability_rule_id == "mechanism_claim_declared.v1"
    assert store.verify_chain()


def test_profile_cannot_disable_tier_invariants() -> None:
    production = DEFAULT_PROFILE_REGISTRY.resolve_exact("production_candidate", "1")
    requirements = dict(production.claim_requirements)
    claim = sorted(TIER_INVARIANTS)[0]
    requirements[claim] = "advisory"
    with pytest.raises(ValueError, match="tier invariant"):
        EvaluationProfileTemplateV1.build(
            profile_id="unsafe", profile_version="1",
            claim_requirements=requirements,
            applicability_rule_hashes=production.applicability_rule_hashes,
            maximum_promotion="candidate_zoo",
            registrar_manifest_hash=production.registrar_manifest_hash,
            producer_registry_hash=production.producer_registry_hash,
            authority_class="build_time_allowlisted",
        )


def test_not_applicable_requires_producer_applicability_assessment(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _, resolved = _contract(store, "na-run")
    factor = _factor(store, run_id="na-run")
    result = ApplicabilityAssessmentServiceV1(store, flags=store.flags).assess(
        run_id="na-run",
        contract_event_hash=resolved.event.event_hash,
        factor_definition_event_hash=factor.event_hash,
        claim_type="mechanism",
    )
    assert result.assessment.result == "not_applicable"
    with pytest.raises(EventValidationError, match="deterministic producer"):
        store.append_event(
            EventDraft(
                event_type="ApplicabilityAssessmentRecorded",
                entity_id=result.event.entity_id,
                run_id="na-run",
                payload_schema_version="applicability_assessment_recorded.v1",
                payload=result.event.to_dict()["payload"],
            )
        )


def test_narrative_claim_cannot_silently_coexist_with_not_applicable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _, resolved = _contract(store, "narrative-run")
    factor = _factor(store, run_id="narrative-run")
    result = ApplicabilityAssessmentServiceV1(store, flags=store.flags).assess(
        run_id="narrative-run",
        contract_event_hash=resolved.event.event_hash,
        factor_definition_event_hash=factor.event_hash,
        claim_type="mechanism",
    )
    with pytest.raises(ValueError, match="narrative claim"):
        validate_narrative_claim_guard(
            narrative_claim_types=("mechanism",), assessments=(result.assessment,)
        )


def test_contract_registration_requires_first_policy_event_and_precedes_trial(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    policy, resolved = _contract(store, "ordered-run")
    events = store.query_events()
    assert events[0].event_hash == policy.event.event_hash
    assert events[1].event_hash == resolved.event.event_hash
    _factor(store, run_id="ordered-run")
    assert store.verify_chain()


def test_latest_profile_lookup_is_not_used_for_replay(tmp_path: Path) -> None:
    with pytest.raises(KeyError, match="exact version"):
        DEFAULT_PROFILE_REGISTRY.resolve_exact("production_candidate", "999")
    store = _store(tmp_path)
    _, resolved = _contract(store)
    assert resolved.contract.profile_template.profile_version == "1"
    assert store.verify_chain()


def test_generic_append_cross_run_and_artifact_tamper_fail_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _, resolved = _contract(store, "protected-run")
    with pytest.raises(EventValidationError, match="deterministic producer"):
        store.append_event(
            EventDraft(
                event_type="ResolvedEvaluationContractRegistered",
                entity_id=resolved.event.entity_id,
                run_id="protected-run",
                payload_schema_version="resolved_evaluation_contract_registered.v1",
                payload=resolved.event.to_dict()["payload"],
            )
        )
    with pytest.raises(EventTransitionError, match="exact run contract"):
        ApplicabilityAssessmentServiceV1(store, flags=store.flags).assess(
            run_id="other-run",
            contract_event_hash=resolved.event.event_hash,
            factor_definition_event_hash=_hash("unknown-event"),
            claim_type="mechanism",
        )
    artifact = store.artifact_root / str(
        resolved.event.payload["artifact_refs"][0]["relative_path"]
    )
    artifact.write_text("{}", encoding="utf-8")
    assert store.verify_chain() is False


def test_research_family_excludes_run_and_event_timestamp(tmp_path: Path) -> None:
    first_store = _store(tmp_path / "first")
    second_store = _store(tmp_path / "second")
    _, first = _contract(first_store, "family-run-a")
    _, second = _contract(second_store, "family-run-b")
    assert first.event.event_hash != second.event.event_hash
    assert first.contract.research_family_id == second.contract.research_family_id


def test_feature_off_refuses_before_contract_artifact_creation(tmp_path: Path) -> None:
    flags = _flags(scorecard=False)
    store = ResearchEventStore(
        tmp_path / "events.sqlite", artifact_root=tmp_path / "artifacts",
        flags=flags, code_version="feature-off-contract-test",
    )
    with pytest.raises(RuntimeError, match="disabled"):
        ResolvedEvaluationContractServiceV1(store, flags=flags)
    assert not (store.artifact_root / "resolved-evaluation-contract-v1").exists()


def test_unregistered_policy_hash_and_injected_profile_registry_are_rejected(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    policy = _policy(store, "unregistered-policy-run")
    unsafe = EvaluationPolicyReferencesV1(
        **{
            **DEFAULT_POLICY_REFERENCES.to_dict(),
            "execution_policy_hash": _hash("caller-selected-free-execution"),
        }
    )
    with pytest.raises(ValueError, match="not registered"):
        ResolvedEvaluationContractServiceV1(store, flags=store.flags).register(
            run_id="unregistered-policy-run",
            evaluation_policy_event_hash=policy.event.event_hash,
            profile_id="production_candidate",
            profile_version="1",
            policy_references=unsafe,
        )
    injected = type(DEFAULT_PROFILE_REGISTRY).build_default()
    with pytest.raises(ValueError, match="build-time profile registry"):
        ResolvedEvaluationContractServiceV1(
            store, flags=store.flags, profile_registry=injected
        )


def test_concurrent_exact_registration_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    policy = _policy(store, "concurrent-contract")

    def register() -> str:
        return ResolvedEvaluationContractServiceV1(
            store, flags=store.flags
        ).register(
            run_id="concurrent-contract",
            evaluation_policy_event_hash=policy.event.event_hash,
            profile_id="production_candidate",
            profile_version="1",
            policy_references=_refs(),
        ).event.event_hash

    with ThreadPoolExecutor(max_workers=2) as pool:
        hashes = tuple(pool.map(lambda _: register(), range(2)))
    assert len(set(hashes)) == 1
    assert len(store.query_events(event_type="ResolvedEvaluationContractRegistered")) == 1
    assert store.verify_chain()


def test_event_append_failure_leaves_orphan_artifact_non_authoritative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    policy = _policy(store, "contract-crash")
    original = store._append_producer_event

    def fail_append(draft: EventDraft):
        raise RuntimeError("simulated append failure")

    monkeypatch.setattr(store, "_append_producer_event", fail_append)
    with pytest.raises(RuntimeError, match="simulated append failure"):
        ResolvedEvaluationContractServiceV1(store, flags=store.flags).register(
            run_id="contract-crash",
            evaluation_policy_event_hash=policy.event.event_hash,
            profile_id="production_candidate",
            profile_version="1",
            policy_references=_refs(),
        )
    assert store.query_events(event_type="ResolvedEvaluationContractRegistered") == []
    assert (store.artifact_root / "resolved-evaluation-contract-v1").exists()

    monkeypatch.setattr(store, "_append_producer_event", original)
    recovered = ResolvedEvaluationContractServiceV1(
        store, flags=store.flags
    ).register(
        run_id="contract-crash",
        evaluation_policy_event_hash=policy.event.event_hash,
        profile_id="production_candidate",
        profile_version="1",
        policy_references=_refs(),
    )
    assert recovered.event.event_type == "ResolvedEvaluationContractRegistered"
    assert store.verify_chain()
