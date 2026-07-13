from __future__ import annotations

import inspect
import json
from dataclasses import replace
from datetime import date, timedelta

import pytest

from src.alpha_quality.falsification import (
    DatedObservationV2,
    FalsificationContractAuthorityV2,
    FalsificationContractV2,
    FalsificationExecutorV2,
    FalsificationSourceProducerV2,
    FalsificationTestCatalogV2,
    TestCapabilityV2,
    TestSpecV2,
    legacy_v1_view,
    render_falsification_narrative_v2,
    validate_contract_v2,
)
from src.alpha_quality.falsification.contract import FalsificationContract
from src.alpha_quality.falsification.service import FalsificationService
from src.alpha_quality.flags import ResolvedAGSFlags
from src.research_ledger.events import EventDraft, EventTransitionError, EventValidationError, ResearchEventStore
from src.research_ledger.events.model import ArtifactReferenceError
from src.research_ledger.hash_utils import canonical_json_hash, utc_now_iso


def _flags() -> ResolvedAGSFlags:
    return ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
            "VIBE_TRADING_FALSIFICATION_CONTRACT": "1",
        }
    )


def _catalog() -> FalsificationTestCatalogV2:
    return FalsificationTestCatalogV2(
        "catalog-v2-test",
        (
            TestCapabilityV2(
                "dated_mean",
                "v2",
                ("positive", "negative", "equivalent"),
                ("mean",),
                ("hac", "iid"),
                500,
            ),
        ),
    )


def _contract(catalog: FalsificationTestCatalogV2, **changes: object) -> FalsificationContractV2:
    digest = canonical_json_hash({"fixture": "falsification-v2"})
    values: dict[str, object] = {
        "factor_spec_id": "factor-v2",
        "mechanism_claim": "the signal captures a persistent dated response",
        "null_hypothesis": "the primary response is non-positive",
        "alternative_hypothesis": "the primary response is positive and the control is equivalent to zero",
        "observable_implication": "primary mean exceeds the SESOI while the placebo stays inside its margin",
        "family_id": "mechanism-family-v2",
        "multiplicity_method": "holm",
        "error_target": 0.05,
        "conditioning_hash": digest,
        "regime_hash": digest,
        "data_scope_hash": digest,
        "catalog_hash": catalog.catalog_hash,
        "policy_hash": digest,
        "data_access_cutoff": utc_now_iso(),
        "tests": (
            TestSpecV2(
                "control",
                "dated_mean",
                "placebo_mean",
                "equivalent",
                0.05,
                "return",
                "date",
                20,
                "mean",
                "iid",
                0,
                None,
                True,
            ),
            TestSpecV2(
                "primary",
                "dated_mean",
                "conditional_mean",
                "positive",
                0.05,
                "return",
                "date",
                20,
                "mean",
                "hac",
                1,
                "control",
                True,
            ),
        ),
    }
    values.update(changes)
    return FalsificationContractV2(**values)  # type: ignore[arg-type]


def _observations(*, count: int = 40, primary: float = 0.12, control: float = 0.0):
    start = date(2024, 1, 1)
    return {
        "control": tuple(
            DatedObservationV2((start + timedelta(days=index)).isoformat(), control + (index % 2) * 0.001)
            for index in range(count)
        ),
        "primary": tuple(
            DatedObservationV2((start + timedelta(days=index)).isoformat(), primary + (index % 2) * 0.002)
            for index in range(count)
        ),
    }


def _setup(tmp_path):
    store = ResearchEventStore(
        tmp_path / "research.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=_flags(),
        code_version="phase10b-test",
    )
    catalog = _catalog()
    contract = _contract(catalog)
    authority = FalsificationContractAuthorityV2(store=store, catalog=catalog)
    contract_event = authority.register(contract, run_id="run-v2")
    source_event = FalsificationSourceProducerV2(store=store).produce(
        contract_event.event_hash,
        _observations(),
        run_id="run-v2",
    )
    return store, contract, contract_event, source_event


def test_executor_recomputes_each_test_and_family_and_replays(tmp_path) -> None:
    store, _, contract_event, source_event = _setup(tmp_path)
    executor = FalsificationExecutorV2(store=store)
    result = executor.execute(contract_event.event_hash, source_event.event_hash, run_id="run-v2")

    assert result.payload["outcome"] == "supported"
    assert [item["test_id"] for item in result.payload["test_results"]] == ["control", "primary"]
    assert result.payload["test_results"][0]["status"] == "support"
    assert result.payload["test_results"][1]["adjusted_p_value"] <= 0.05
    replay = executor.replay(result.event_hash)
    assert canonical_json_hash(replay) == result.payload["result_hash"]
    assert store.verify_chain()


def test_contract_closes_direction_sesoi_family_and_multiplicity() -> None:
    catalog = _catalog()
    base = _contract(catalog)
    changed_test = replace(base.tests[1], prediction="negative")
    hashes = {
        base.contract_hash,
        _contract(catalog, tests=(base.tests[0], changed_test)).contract_hash,
        _contract(catalog, tests=(base.tests[0], replace(base.tests[1], sesoi=0.08))).contract_hash,
        _contract(catalog, family_id="other-family").contract_hash,
        _contract(catalog, multiplicity_method="by").contract_hash,
    }
    assert len(hashes) == 5


def test_contract_requires_sorted_unique_tests_and_catalog_capability() -> None:
    catalog = _catalog()
    base = _contract(catalog)
    with pytest.raises(ValueError, match="sorted"):
        _contract(catalog, tests=tuple(reversed(base.tests)))
    unsupported = replace(base.tests[1], dependence_method="iid", capability_id="unknown")
    with pytest.raises(ValueError, match="capability"):
        validate_contract_v2(_contract(catalog, tests=(base.tests[0], unsupported)), catalog)


def test_fixed_and_sequential_contracts_cannot_be_mixed() -> None:
    catalog = _catalog()
    base = _contract(catalog)
    with pytest.raises(ValueError, match="fixed-horizon"):
        replace(base.tests[1], stopping_rule="e_process", maximum_looks=3)  # type: ignore[arg-type]


def test_execute_has_no_caller_statistics_or_multiplicity_parameters() -> None:
    parameters = set(inspect.signature(FalsificationExecutorV2.execute).parameters)
    assert not parameters.intersection({"p_value", "effect", "outcome", "multiplicity_method"})
    assert parameters == {"self", "contract_event_hash", "source_event_hash", "run_id"}


def test_non_significance_is_not_equivalence(tmp_path) -> None:
    store = ResearchEventStore(
        tmp_path / "research.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=_flags(),
        code_version="phase10b-test",
    )
    catalog = _catalog()
    contract = _contract(catalog)
    contract_event = FalsificationContractAuthorityV2(store=store, catalog=catalog).register(contract, run_id="run-v2")
    noisy = _observations(control=0.0)
    noisy["control"] = tuple(
        DatedObservationV2(item.observed_on, (-1.0 if index % 2 else 1.0))
        for index, item in enumerate(noisy["control"])
    )
    source = FalsificationSourceProducerV2(store=store).produce(contract_event.event_hash, noisy, run_id="run-v2")
    result = FalsificationExecutorV2(store=store).execute(contract_event.event_hash, source.event_hash, run_id="run-v2")
    control = result.payload["test_results"][0]
    assert control["estimate"] == pytest.approx(0.0)
    assert control["status"] == "inconclusive"
    assert result.payload["outcome"] == "inconclusive"


def test_underpowered_decisive_test_is_inconclusive(tmp_path) -> None:
    store = ResearchEventStore(
        tmp_path / "research.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=_flags(),
        code_version="phase10b-test",
    )
    catalog = _catalog()
    contract_event = FalsificationContractAuthorityV2(store=store, catalog=catalog).register(
        _contract(catalog), run_id="run-v2"
    )
    source = FalsificationSourceProducerV2(store=store).produce(
        contract_event.event_hash, _observations(count=5), run_id="run-v2"
    )
    result = FalsificationExecutorV2(store=store).execute(contract_event.event_hash, source.event_hash, run_id="run-v2")
    assert result.payload["outcome"] == "inconclusive"
    assert result.payload["family_result"]["cap"] == "research_only"
    assert all("LOW_POWER" in item["reason_codes"] for item in result.payload["test_results"])
    narrative = render_falsification_narrative_v2(result)
    assert "inconclusive" in narrative
    assert "no support claim" in narrative


def test_contract_binding_prevents_post_registration_rule_swap(tmp_path) -> None:
    store, contract, contract_event, source_event = _setup(tmp_path)
    changed = _contract(_catalog(), tests=(contract.tests[0], replace(contract.tests[1], prediction="negative")))
    assert changed.contract_hash != contract_event.payload["contract_hash"]
    result = FalsificationExecutorV2(store=store).execute(
        contract_event.event_hash, source_event.event_hash, run_id="run-v2"
    )
    assert result.payload["test_results"][1]["prediction"] == "positive"


def test_source_retry_is_idempotent_and_variant_reopen_is_rejected(tmp_path) -> None:
    store, _, contract_event, source_event = _setup(tmp_path)
    producer = FalsificationSourceProducerV2(store=store)
    retried = producer.produce(contract_event.event_hash, _observations(), run_id="run-v2")
    assert retried.event_hash == source_event.event_hash
    with pytest.raises(EventTransitionError, match="source authority"):
        producer.produce(
            contract_event.event_hash,
            _observations(primary=0.2),
            run_id="run-v2",
        )


def test_source_artifact_tamper_breaks_replay_and_chain_verification(tmp_path) -> None:
    store, _, contract_event, source_event = _setup(tmp_path)
    reference = source_event.payload["artifact_refs"][0]
    artifact_path = store.artifact_root.joinpath(*reference["relative_path"].split("/"))
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    payload["tests"]["primary"][0]["value"] = 999.0
    artifact_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ArtifactReferenceError, match="hash mismatch"):
        FalsificationExecutorV2(store=store).execute(
            contract_event.event_hash, source_event.event_hash, run_id="run-v2"
        )
    assert not store.verify_chain()


def test_protected_events_cannot_be_minted_through_generic_append(tmp_path) -> None:
    store, _, contract_event, _ = _setup(tmp_path)
    with pytest.raises(EventValidationError, match="deterministic producer"):
        store.append_event(
            EventDraft(
                event_type="FalsificationResultV2Recorded",
                entity_id="forged",
                run_id="run-v2",
                payload_schema_version="falsification_result_recorded.v2",
                payload={},
            )
        )
    with pytest.raises(EventValidationError, match="deterministic producer"):
        store.append_event(
            EventDraft(
                event_type="FalsificationSourceArtifactV2Recorded",
                entity_id="fixture-forgery",
                run_id=contract_event.run_id,
                payload_schema_version="falsification_source_artifact_recorded.v2",
                payload={},
            )
        )


def test_result_requires_exact_outcome_access_and_source_transition(tmp_path) -> None:
    store = ResearchEventStore(
        tmp_path / "research.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=_flags(),
        code_version="phase10b-test",
    )
    digest = canonical_json_hash({"missing": "access"})
    with pytest.raises(EventValidationError, match="result authority"):
        store._append_producer_event(
            EventDraft(
                event_type="FalsificationResultV2Recorded",
                entity_id="result-missing-access",
                run_id="run-v2",
                payload_schema_version="falsification_result_recorded.v2",
                payload={
                    "result_id": "result-missing-access",
                    "result_hash": digest,
                    "contract_event_hash": digest,
                    "contract_hash": digest,
                    "source_event_hash": digest,
                    "source_hash": digest,
                    "outcome_access_event_hash": digest,
                    "factor_spec_id": "factor-v2",
                    "family_id": "family",
                    "outcome": "supported",
                    "test_results": [{"test_id": "test"}],
                    "family_result": {"family_id": "family", "outcome": "supported"},
                    "legacy_promotion_cap": None,
                    "source_event_hashes": [digest],
                    "producer_schema_version": "falsification_authority.v2",
                    "producer_policy_hash": digest,
                    "artifact_refs": [],
                },
            )
        )


def test_legacy_result_is_read_only_inconclusive_and_research_only(tmp_path) -> None:
    store = ResearchEventStore(
        tmp_path / "research.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=_flags(),
        code_version="phase10b-test",
    )
    digest = canonical_json_hash({"legacy": True})
    legacy_contract = FalsificationContract(
        "legacy-factor",
        digest,
        "legacy-estimand",
        "positive",
        0.01,
        "return",
        "date",
        digest,
        digest,
        "legacy-family",
        0.05,
        "hac",
        1,
        "fixed",
        True,
        digest,
    )
    FalsificationService(store=store, flags=_flags()).register(legacy_contract, run_id="legacy-run")
    event = store.append_event(
        EventDraft(
            event_type="FalsificationResultRecorded",
            entity_id="legacy-result",
            run_id="legacy-run",
            payload_schema_version="falsification_result_recorded.v1",
            payload={
                "result_id": "legacy-result",
                "contract_id": legacy_contract.contract_id,
                "contract_hash": legacy_contract.contract_hash,
                "outcome": "supported",
                "artifact_refs": [],
            },
        )
    )
    view = legacy_v1_view(event)
    assert view["outcome"] == "inconclusive"
    assert view["promotion_cap"] == "research_only"


def test_feature_off_constructs_no_authority_or_files(tmp_path) -> None:
    flags = ResolvedAGSFlags.from_settings({"VIBE_TRADING_AGS_ENABLED": "1", "VIBE_TRADING_RESEARCH_EVENTS": "1"})
    store = ResearchEventStore(
        tmp_path / "research.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=flags,
        code_version="phase10b-test",
    )
    with pytest.raises(RuntimeError, match="disabled"):
        FalsificationContractAuthorityV2(store=store, catalog=_catalog())
    with pytest.raises(RuntimeError, match="disabled"):
        FalsificationSourceProducerV2(store=store)
    with pytest.raises(RuntimeError, match="disabled"):
        FalsificationExecutorV2(store=store)
    assert store.query_events() == []


def test_mechanism_result_does_not_rewrite_predictive_claim(tmp_path) -> None:
    store, _, contract_event, source_event = _setup(tmp_path)
    result = FalsificationExecutorV2(store=store).execute(
        contract_event.event_hash, source_event.event_hash, run_id="run-v2"
    )
    assert "predictive" not in result.payload
    assert store.query_events(event_type="ClaimMatrixRecorded") == []
