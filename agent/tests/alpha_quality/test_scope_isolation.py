from __future__ import annotations

import inspect
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.alpha_foundry.search import AlphaFoundrySearch
from src.alpha_foundry.seed_bank import SeedBank
from src.alpha_foundry.dsl.identity import FactorIdentityService, FactorSpecSemantics
from src.alpha_quality.final_test.model import (
    FinalTestDataRequest,
    FinalTestDataset,
    FinalTestPolicy,
    FrozenFinalCandidate,
)
from src.alpha_quality.final_test.runner import FinalTestRunner
from src.alpha_quality.flags import ResolvedAGSFlags
from src.alpha_quality.scope import (
    DiscoveryEvidenceProjector,
    FinalScopeViolation,
    TestScopeAuthority,
)
from src.research_ledger.events import EventDraft, ResearchEventStore
from src.research_ledger.hash_utils import canonical_json_hash, utc_now_iso


class _Provider:
    def __init__(self) -> None:
        self.calls = 0

    def load_final(self, request: FinalTestDataRequest) -> FinalTestDataset:
        self.calls += 1
        return FinalTestDataset(
            data_snapshot_hash=request.data_snapshot_hash,
            period_start=request.period_start,
            period_end=request.period_end,
            rank_ic_series=(0.02, 0.03, 0.04, 0.05),
            net_returns=(0.001, 0.002, 0.0015, 0.0025),
            limitations=(),
        )


def _flags() -> ResolvedAGSFlags:
    return ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
            "VIBE_TRADING_ALPHA_FOUNDRY": "1",
            "VIBE_TRADING_DECISION_V2": "1",
            "VIBE_TRADING_FORWARD_TRACKING": "1",
            "VIBE_TRADING_FACTOR_DAG": "1",
            "VIBE_TRADING_PROCESS_MEMORY": "1",
        }
    )


def _store(tmp_path: Path) -> ResearchEventStore:
    store = ResearchEventStore(
        tmp_path / "research.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=_flags(),
        code_version="pr11-test",
    )
    digest = canonical_json_hash({"fixture": "definition-semantics"})
    attempt = FactorIdentityService(store=store, flags=_flags()).record_attempt(
        trial_id="trial-final-definition",
        run_id="run-final",
        candidate_id="candidate-final",
        formula="rank(close)",
        semantics=FactorSpecSemantics(
            transform_pipeline_hash=digest,
            field_semantics={"close": "pit_eod"},
            signal_time="close",
            order_time="next_open",
            entry_price_time="next_open",
            execution_lag=1,
            return_horizon=5,
            universe_mask_hash=digest,
            tradability_mask_hash=digest,
        ),
    )
    assert attempt.factor_spec_id is not None
    return store


def _policy() -> FinalTestPolicy:
    return FinalTestPolicy(
        schema_version="final_test_policy.v1",
        policy_version="final-policy.1",
        minimum_effective_observations=4,
        minimum_rank_ic_mean=0.01,
        minimum_net_return_mean=0.0001,
    )


def _candidate(
    policy: FinalTestPolicy, store: ResearchEventStore | None = None
) -> FrozenFinalCandidate:
    digest = canonical_json_hash({"fixture": "frozen"})
    factor_spec_id = "factor-final" if store is None else str(
        store.query_events(event_type="FactorDefinitionRecorded")[-1].payload[
            "factor_spec_id"
        ]
    )
    definition_hash = digest if store is None else store.query_events(
        event_type="FactorDefinitionRecorded", entity_id=factor_spec_id
    )[-1].event_hash
    return FrozenFinalCandidate.create(
        factor_spec_id=factor_spec_id,
        definition_hash=definition_hash,
        transform_pipeline_hash=digest,
        cost_model_hash=digest,
        regime_config_hash=digest,
        policy_hash=policy.policy_hash,
        data_snapshot_hash=digest,
        frozen_at=utc_now_iso(),
    )


def _request(candidate: FrozenFinalCandidate, *, run_id: str = "run-final") -> FinalTestDataRequest:
    return FinalTestDataRequest(
        run_id=run_id,
        factor_spec_id=candidate.factor_spec_id,
        candidate_hash=candidate.candidate_hash,
        data_snapshot_hash=candidate.data_snapshot_hash,
        period_start="2025-01-01",
        period_end="2025-06-30",
        fields=("net_returns", "rank_ic_series"),
    )


def _append_discovery_evaluation(store: ResearchEventStore) -> str:
    digest = canonical_json_hash({"fixture": "discovery-scorecard"})
    definition = store.query_events(event_type="FactorDefinitionRecorded")[-1]
    factor_spec_id = str(definition.payload["factor_spec_id"])
    trial_id = str(definition.payload["metadata"]["originating_trial_id"])
    evaluation = store.append_event(
        EventDraft(
            event_type="EvaluationRecorded",
            entity_id="evaluation-discovery",
            run_id="run-discovery",
            payload_schema_version="evaluation_recorded.v1",
            payload={
                "evaluation_id": "evaluation-discovery",
                "trial_id": trial_id,
                "factor_spec_id": factor_spec_id,
                "data_scope": "valid",
                "scorecard_hash": digest,
                "artifact_refs": [],
                "metadata": {},
            },
        )
    )
    terminal = store.append_event(
        EventDraft(
            event_type="TrialTerminated",
            entity_id=trial_id,
            run_id="run-discovery",
            payload_schema_version="trial_terminated.v1",
            payload={
                "trial_id": trial_id,
                "status": "success",
                "reason_codes": [],
                "decision": "candidate_zoo",
                "evaluation_event_hash": evaluation.event_hash,
                "terminated_at": utc_now_iso(),
            },
        )
    )
    return terminal.event_hash


def test_foundry_cannot_be_constructed_with_final_test_provider() -> None:
    assert "final_test_provider" not in inspect.signature(AlphaFoundrySearch).parameters
    with pytest.raises(TypeError, match="final_test_provider"):
        AlphaFoundrySearch(seed_bank=SeedBank([]), final_test_provider=object())  # type: ignore[call-arg]


def test_final_scope_import_graph_is_one_way() -> None:
    source_root = Path(__file__).parents[2] / "src" / "alpha_foundry"
    forbidden = (
        "src.alpha_quality.final_test",
        "src.alpha_quality.scope.capabilities",
        "FinalTestDataProvider",
        "TestScopeCapability",
    )
    violations = {
        path.relative_to(source_root).as_posix(): token
        for path in source_root.rglob("*.py")
        for token in forbidden
        if token in path.read_text(encoding="utf-8")
    }
    assert violations == {}


def test_final_access_requires_frozen_definition_and_one_shot_capability(
    tmp_path: Path,
) -> None:
    empty_store = ResearchEventStore(
        tmp_path / "empty.sqlite",
        artifact_root=tmp_path / "empty-artifacts",
        flags=_flags(),
        code_version="pr11-test",
    )
    policy = _policy()
    candidate = _candidate(policy)
    empty_authority = TestScopeAuthority(store=empty_store, flags=_flags())
    with pytest.raises(FinalScopeViolation, match="prior frozen factor"):
        empty_authority.issue(
            candidate,
            run_id="run-final",
            period_start="2025-01-01",
            period_end="2025-06-30",
        )

    store = _store(tmp_path)
    candidate = _candidate(policy, store)
    authority = TestScopeAuthority(store=store, flags=_flags())
    capability = authority.issue(
        candidate,
        run_id="run-final",
        period_start="2025-01-01",
        period_end="2025-06-30",
    )
    provider = _Provider()
    runner = FinalTestRunner(
        flags=_flags(), authority=authority, provider=provider, policy=policy, store=store
    )
    runner.run(candidate, capability, _request(candidate), run_id="run-final")
    with pytest.raises(FinalScopeViolation, match="REPEATED_FINAL_ACCESS"):
        runner.run(candidate, capability, _request(candidate), run_id="run-final")
    with pytest.raises(FinalScopeViolation, match="one final capability"):
        authority.issue(
            candidate,
            run_id="run-final",
            period_start="2025-01-01",
            period_end="2025-06-30",
        )
    assert provider.calls == 1
    assert authority.audit_trail(candidate.candidate_hash)[-1].reason_code == (
        "REPEATED_FINAL_CAPABILITY_ISSUANCE"
    )


def test_repeated_or_mismatched_final_access_marks_contamination(tmp_path: Path) -> None:
    store = _store(tmp_path)
    policy = _policy()
    candidate = _candidate(policy, store)
    authority = TestScopeAuthority(store=store, flags=_flags())
    capability = authority.issue(
        candidate,
        run_id="run-final",
        period_start="2025-01-01",
        period_end="2025-06-30",
    )
    provider = _Provider()
    runner = FinalTestRunner(
        flags=_flags(), authority=authority, provider=provider, policy=policy, store=store
    )
    with pytest.raises(FinalScopeViolation, match="MISMATCHED_FINAL_RUN"):
        runner.run(
            candidate,
            capability,
            _request(candidate, run_id="another-run"),
            run_id="another-run",
        )
    assert provider.calls == 0
    assert authority.is_tainted(candidate.candidate_hash)
    assert authority.audit_trail(candidate.candidate_hash)[-1].outcome == "denied"


def test_final_scope_taint_propagates_to_all_derived_artifacts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    policy = _policy()
    candidate = _candidate(policy, store)
    authority = TestScopeAuthority(store=store, flags=_flags())
    capability = authority.issue(
        candidate,
        run_id="run-final",
        period_start="2025-01-01",
        period_end="2025-06-30",
    )
    provider = _Provider()
    runner = FinalTestRunner(
        flags=_flags(), authority=authority, provider=provider, policy=policy, store=store
    )
    artifact = runner.run(candidate, capability, _request(candidate), run_id="run-final")
    assert artifact.quality_passed

    with pytest.raises(FinalScopeViolation):
        authority.authorize(capability, _request(candidate))

    first_view = runner.final_decision_view(artifact)
    second_view = runner.final_decision_view(artifact)
    assert first_view.contaminated and second_view.contaminated
    assert not first_view.quality_passed and not second_view.quality_passed
    assert first_view.to_decision_record().payload["contaminated"] is True


def test_final_metrics_never_enter_discovery_projection(tmp_path: Path) -> None:
    store = _store(tmp_path)
    discovery_terminal_hash = _append_discovery_evaluation(store)
    projector = DiscoveryEvidenceProjector(flags=_flags())
    before = projector.factual_view(store)
    factor_spec_id = before.factor_ids()[0]
    assert before.evidence_by_factor_spec_id[factor_spec_id].terminal_event_hash == discovery_terminal_hash
    policy = _policy()
    candidate = _candidate(policy, store)
    authority = TestScopeAuthority(store=store, flags=_flags())
    capability = authority.issue(
        candidate,
        run_id="run-final",
        period_start="2025-01-01",
        period_end="2025-06-30",
    )
    runner = FinalTestRunner(
        flags=_flags(), authority=authority, provider=_Provider(), policy=policy, store=store
    )
    runner.run(candidate, capability, _request(candidate), run_id="run-final")
    after = projector.factual_view(store)

    assert after.evidence_by_factor_spec_id == before.evidence_by_factor_spec_id
    assert after.factor_ids() == before.factor_ids()


def test_concurrent_capability_issuance_commits_only_one_and_audits_taint(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    candidate = _candidate(_policy(), store)
    authorities = (
        TestScopeAuthority(store=store, flags=_flags()),
        TestScopeAuthority(store=store, flags=_flags()),
    )

    def issue(authority: TestScopeAuthority) -> str:
        try:
            authority.issue(
                candidate,
                run_id="run-final",
                period_start="2025-01-01",
                period_end="2025-06-30",
            )
        except FinalScopeViolation:
            return "denied"
        return "issued"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(issue, authorities))

    assert outcomes == ["denied", "issued"]
    assert len(store.query_events(event_type="FinalTestCapabilityIssued")) == 1
    assert any(
        event.payload["reason_code"]
        in {
            "CONCURRENT_FINAL_CAPABILITY_ISSUANCE",
            "REPEATED_FINAL_CAPABILITY_ISSUANCE",
        }
        for event in store.query_events(event_type="FinalTestAccessRecorded")
    )
    assert store.verify_chain()
