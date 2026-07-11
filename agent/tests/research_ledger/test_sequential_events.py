from __future__ import annotations

import hashlib
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.alpha_quality.flags import ResolvedAGSFlags
from src.research_ledger.events import (
    EventDraft,
    EventIdempotencyConflict,
    EventTransitionError,
    EventValidationError,
    ResearchEventStore,
)
from src.research_ledger.hash_utils import canonical_json_hash


def _hash(character: str) -> str:
    return "sha256:" + hashlib.sha256(character.encode("utf-8")).hexdigest()


def _store(tmp_path: Path) -> ResearchEventStore:
    flags = ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
            "VIBE_TRADING_FALSIFICATION_CONTRACT": "1",
        }
    )
    return ResearchEventStore(
        tmp_path / "research.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=flags,
        code_version="sequential-event-test-v1",
    )


def _append_contract(
    store: ResearchEventStore,
    *,
    contract_id: str = "contract-1",
    contract_hash: str = _hash("c"),
    factor_spec_id: str = "factor-1",
    policy_hash: str = _hash("p"),
):
    return store.append_event(
        EventDraft(
            event_type="FalsificationContractRegistered",
            entity_id=contract_id,
            run_id="run-1",
            payload_schema_version="falsification_contract_registered.v1",
            idempotency_key=f"contract:{contract_hash}",
            payload={
                "contract_id": contract_id,
                "contract_hash": contract_hash,
                "factor_spec_id": factor_spec_id,
                "registered_at": "2025-01-01T00:00:00Z",
                "data_access_cutoff": "2025-01-01T00:00:00Z",
                "policy_hash": policy_hash,
            },
        )
    )


def _protocol_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "protocol_id": "protocol-1",
        "protocol_hash": _hash("s"),
        "contract_id": "contract-1",
        "contract_hash": _hash("c"),
        "factor_spec_id": "factor-1",
        "method": "bounded_mean_mixture_e.v1",
        "maximum_looks": 2,
        "stopping_rule": "e_process_boundary_or_max_looks.v1",
        "data_scope": "valid",
        "family_alpha": 0.3,
        "support_alpha": math.exp(-2.0),
        "contradiction_alpha": math.exp(-2.0),
        "lambda_grid": [0.1, 0.2],
        "mixture_weights": [0.5, 0.5],
        "support_log_boundary": 2.0,
        "contradiction_log_boundary": 2.0,
        "filtration_hash": _hash("f"),
        "block_schedule_hash": _hash("b"),
        "policy_hash": _hash("p"),
        "registered_at": "2025-01-01T00:00:01Z",
    }
    payload.update(changes)
    return payload


def _append_protocol(
    store: ResearchEventStore,
    *,
    payload: dict[str, object] | None = None,
    idempotency_key: str = "sequential-protocol:protocol-1",
):
    body = payload or _protocol_payload()
    return store.append_event(
        EventDraft(
            event_type="SequentialProtocolRegistered",
            entity_id=str(body["protocol_id"]),
            run_id="run-1",
            payload_schema_version="sequential_protocol_registered.v1",
            idempotency_key=idempotency_key,
            payload=body,
        )
    )


def _look_payload(index: int, **changes: object) -> dict[str, object]:
    unit_base = 2 * index - 1
    payload: dict[str, object] = {
        "look_id": f"look-{index}",
        "protocol_id": "protocol-1",
        "protocol_hash": _hash("s"),
        "factor_spec_id": "factor-1",
        "look_index": index,
        "information_time": 2 * index,
        "block_id": f"block-{index}",
        "block_hash": _hash(str(index)),
        "unit_hashes": sorted(
            [_hash(chr(96 + unit_base)), _hash(chr(97 + unit_base))]
        ),
        "incremental_information": 2,
        "support_component_log_capitals": [0.2 * index, 0.2 * index],
        "contradiction_component_log_capitals": [-0.1 * index, -0.1 * index],
        "cumulative_support_log_e": 0.2 * index,
        "cumulative_contradiction_log_e": -0.1 * index,
        "support_log_boundary": 2.0,
        "contradiction_log_boundary": 2.0,
        "status": "continue" if index == 1 else "max_looks_reached",
        "stop_reason": "NONE" if index == 1 else "MAX_LOOKS",
        "previous_look_event_hash": None,
    }
    payload.update(changes)
    return payload


def _append_look(
    store: ResearchEventStore,
    payload: dict[str, object],
    *,
    idempotency_key: str,
):
    return store.append_event(
        EventDraft(
            event_type="SequentialLookRecorded",
            entity_id=str(payload["look_id"]),
            run_id="run-1",
            payload_schema_version="sequential_look_recorded.v1",
            idempotency_key=idempotency_key,
            payload=payload,
        )
    )


def _append_result_with_artifact(
    store: ResearchEventStore,
    *,
    result_id: str = "result-1",
    contract_id: str = "contract-1",
    contract_hash: str = _hash("c"),
):
    artifact = store.artifact_root / "falsification" / f"{result_id}.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text('{"schema_version":"fixture.v1"}', encoding="utf-8")
    artifact_hash = store.hash_artifact(artifact)
    event = store.append_event(
        EventDraft(
            event_type="FalsificationResultRecorded",
            entity_id=result_id,
            run_id="run-1",
            payload_schema_version="falsification_result_recorded.v1",
            idempotency_key=f"result:{result_id}",
            payload={
                "result_id": result_id,
                "contract_id": contract_id,
                "contract_hash": contract_hash,
                "outcome": "supported",
                "artifact_refs": [
                    {
                        "relative_path": f"falsification/{result_id}.json",
                        "artifact_hash": artifact_hash,
                        "media_type": "application/json",
                    }
                ],
            },
        )
    )
    return event, artifact_hash


def test_sequential_schemas_are_closed_and_reject_repeated_p_values(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _append_contract(store)
    bad_protocol = _protocol_payload(method="ordinary_repeated_p_values.v1")
    with pytest.raises(EventValidationError, match="method"):
        _append_protocol(store, payload=bad_protocol)
    with pytest.raises(EventValidationError, match="stopping_rule"):
        _append_protocol(
            store,
            payload=_protocol_payload(stopping_rule="caller_decides_after_look"),
        )
    with pytest.raises(EventValidationError, match="data_scope"):
        _append_protocol(store, payload=_protocol_payload(data_scope="final_test"))

    _append_protocol(store)
    bad_look = _look_payload(1)
    bad_look["raw_p_value"] = 0.01
    with pytest.raises(EventValidationError, match="unknown payload fields"):
        _append_look(store, bad_look, idempotency_key="look:bad-p")

    mismatched = _look_payload(1, stop_reason="MAX_LOOKS")
    with pytest.raises(EventValidationError, match="status and stop reason"):
        _append_look(store, mismatched, idempotency_key="look:bad-reason")
    forged_cumulative = _look_payload(1, cumulative_support_log_e=0.9)
    with pytest.raises(EventTransitionError, match="component state"):
        _append_look(store, forged_cumulative, idempotency_key="look:forged-e")


def test_protocol_must_freeze_before_access_and_retry_is_exact(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(EventTransitionError, match="prior falsification contract"):
        _append_protocol(store)

    _append_contract(store)
    first = _append_protocol(store)
    assert _append_protocol(store) == first

    conflict = _protocol_payload(protocol_hash=_hash("x"))
    with pytest.raises(EventIdempotencyConflict):
        _append_protocol(store, payload=conflict)

    second_store = _store(tmp_path / "contaminated")
    _append_contract(second_store)
    second_store.append_event(
        EventDraft(
            event_type="OutcomeDataAccessed",
            entity_id="access-1",
            run_id="run-1",
            payload_schema_version="outcome_data_accessed.v1",
            payload={
                "access_id": "access-1",
                "factor_spec_id": "factor-1",
                "data_scope": "valid",
                "accessed_at": "2025-01-01T00:00:02Z",
            },
        )
    )
    with pytest.raises(EventTransitionError, match="precede outcome-data access"):
        _append_protocol(second_store)


def test_sequential_look_state_machine_rejects_reuse_and_replays(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _append_contract(store)
    _append_protocol(store)

    first = _append_look(store, _look_payload(1), idempotency_key="look:protocol-1:1")
    assert _append_look(store, _look_payload(1), idempotency_key="look:protocol-1:1") == first

    conflicting_retry = _look_payload(1, block_id="different", block_hash=_hash("z"))
    with pytest.raises(EventIdempotencyConflict):
        _append_look(store, conflicting_retry, idempotency_key="look:protocol-1:1")

    reused_block = _look_payload(
        2,
        block_id="block-1",
        block_hash=_hash("1"),
        previous_look_event_hash=first.event_hash,
    )
    with pytest.raises(EventTransitionError, match="reuse an observed block"):
        _append_look(store, reused_block, idempotency_key="look:reuse-block")

    reused_unit = _look_payload(
        2,
        unit_hashes=sorted([_hash("a"), _hash("d")]),
        previous_look_event_hash=first.event_hash,
    )
    with pytest.raises(EventTransitionError, match="reuse an observed unit"):
        _append_look(store, reused_unit, idempotency_key="look:reuse-unit")

    wrong_information = _look_payload(
        2,
        information_time=5,
        previous_look_event_hash=first.event_hash,
    )
    with pytest.raises(EventTransitionError, match="advance cumulatively"):
        _append_look(store, wrong_information, idempotency_key="look:wrong-information")

    second_payload = _look_payload(2, previous_look_event_hash=first.event_hash)
    second = _append_look(store, second_payload, idempotency_key="look:protocol-1:2")
    assert second.payload["status"] == "max_looks_reached"

    third = _look_payload(
        3,
        status="support_boundary_crossed",
        stop_reason="SUPPORT_BOUNDARY",
        cumulative_support_log_e=3.0,
        support_component_log_capitals=[3.0, 3.0],
        previous_look_event_hash=second.event_hash,
    )
    with pytest.raises(EventTransitionError, match="maximum_looks"):
        _append_look(store, third, idempotency_key="look:protocol-1:3")

    assert store.verify_chain()
    assert store.replay() == store.replay()

    reordered = store.query_events()
    protocol_index = next(
        index for index, event in enumerate(reordered) if event.event_type == "SequentialProtocolRegistered"
    )
    first_look_index = next(
        index for index, event in enumerate(reordered) if event.event_type == "SequentialLookRecorded"
    )
    reordered[protocol_index], reordered[first_look_index] = (
        reordered[first_look_index],
        reordered[protocol_index],
    )
    assert not ResearchEventStore._verify_references_and_lifecycle(reordered)


def test_concurrent_same_index_looks_commit_exactly_one_valid_transition(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _append_contract(store)
    _append_protocol(store)
    barrier = threading.Barrier(2)
    first = _look_payload(1)
    second = _look_payload(
        1,
        look_id="look-1-conflict",
        block_id="block-conflict",
        block_hash=_hash("conflict-block"),
        unit_hashes=sorted([_hash("conflict-a"), _hash("conflict-b")]),
    )

    def append(payload: dict[str, object]) -> str:
        barrier.wait()
        try:
            _append_look(
                store,
                payload,
                idempotency_key="sequential-look:protocol-1:1",
            )
            return "committed"
        except EventIdempotencyConflict:
            return "idempotency_conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(append, (first, second)))

    assert outcomes == ["committed", "idempotency_conflict"]
    assert len(store.query_events(event_type="SequentialLookRecorded")) == 1
    assert store.verify_chain()


def test_sequential_result_requires_terminal_look(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _append_contract(store)
    _append_protocol(store)
    _append_look(store, _look_payload(1), idempotency_key="look:protocol-1:1")

    with pytest.raises(EventTransitionError, match="terminal sequential look"):
        _append_result_with_artifact(store)


def test_sequential_indices_previous_hash_and_terminal_state_are_closed(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _append_contract(store)
    _append_protocol(store, payload=_protocol_payload(maximum_looks=3))
    first = _append_look(store, _look_payload(1), idempotency_key="look:1")

    skipped = _look_payload(3, previous_look_event_hash=first.event_hash)
    with pytest.raises(EventTransitionError, match="contiguous"):
        _append_look(store, skipped, idempotency_key="look:skipped")

    wrong_previous = _look_payload(2, previous_look_event_hash=_hash("wrong"))
    with pytest.raises(EventTransitionError, match="previous hash"):
        _append_look(store, wrong_previous, idempotency_key="look:wrong-previous")

    premature_max = _look_payload(2, previous_look_event_hash=first.event_hash)
    with pytest.raises(EventTransitionError, match="only at maximum_looks"):
        _append_look(store, premature_max, idempotency_key="look:premature-max")

    support = _look_payload(
        2,
        status="support_boundary_crossed",
        stop_reason="SUPPORT_BOUNDARY",
        cumulative_support_log_e=2.1,
        support_component_log_capitals=[2.1, 2.1],
        previous_look_event_hash=first.event_hash,
    )
    terminal = _append_look(store, support, idempotency_key="look:support")
    after_terminal = _look_payload(
        3,
        status="support_boundary_crossed",
        stop_reason="SUPPORT_BOUNDARY",
        cumulative_support_log_e=2.2,
        support_component_log_capitals=[2.2, 2.2],
        previous_look_event_hash=terminal.event_hash,
    )
    with pytest.raises(EventTransitionError, match="already stopped"):
        _append_look(store, after_terminal, idempotency_key="look:after-terminal")


def test_mei_sources_are_prior_unique_same_factor_and_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _append_contract(store)
    result, result_hash = _append_result_with_artifact(store)
    payload = {
        "mei_id": "mei-1",
        "factor_spec_id": "factor-1",
        "mei_hash": _hash("m"),
        "mei_schema_version": "mechanism_evidence_index.v1",
        "truth_table_version": "mechanism_evidence_truth_table.v1",
        "policy_version": "mei-policy.v1",
        "policy_hash": _hash("p"),
        "source_result_hashes": [result_hash],
        "source_event_hashes": [result.event_hash],
        "decisive_event_hashes": [result.event_hash],
        "advisory_event_hashes": [],
        "ordinal_state": "supported",
        "reason_codes": ["ALL_INCLUDED_EVIDENCE_SUPPORTED"],
        "warning_codes": [],
        "limitation_codes": ["VALIDATION_ONLY"],
    }
    payload["mei_hash"] = canonical_json_hash(
        ResearchEventStore._mechanism_index_content(payload)
    )
    draft = EventDraft(
        event_type="MechanismEvidenceIndexRecorded",
        entity_id="mei-1",
        run_id="run-1",
        payload_schema_version="mechanism_evidence_index_recorded.v1",
        idempotency_key="mei:factor-1:policy-v1",
        payload=payload,
    )
    first = store.append_event(draft)
    assert store.append_event(draft) == first
    assert store.verify_chain()

    forged_state = dict(
        payload,
        mei_id="mei-forged-state",
        ordinal_state="partial_support",
        reason_codes=["DECISIVE_SUPPORT_WITH_ADVISORY_GAPS"],
    )
    forged_state["mei_hash"] = canonical_json_hash(
        ResearchEventStore._mechanism_index_content(forged_state)
    )
    with pytest.raises(EventTransitionError, match="truth table"):
        store.append_event(
            EventDraft(
                event_type="MechanismEvidenceIndexRecorded",
                entity_id="mei-forged-state",
                run_id="run-1",
                payload_schema_version="mechanism_evidence_index_recorded.v1",
                payload=forged_state,
            )
        )

    forged_hash = dict(payload, mei_id="mei-forged-hash", mei_hash=_hash("forged"))
    with pytest.raises(EventTransitionError, match="MEI hash"):
        store.append_event(
            EventDraft(
                event_type="MechanismEvidenceIndexRecorded",
                entity_id="mei-forged-hash",
                run_id="run-1",
                payload_schema_version="mechanism_evidence_index_recorded.v1",
                payload=forged_hash,
            )
        )

    wrong_policy = dict(payload, mei_id="mei-wrong-policy", policy_hash=_hash("other-policy"))
    wrong_policy["mei_hash"] = canonical_json_hash(
        ResearchEventStore._mechanism_index_content(wrong_policy)
    )
    with pytest.raises(EventTransitionError, match="frozen policy"):
        store.append_event(
            EventDraft(
                event_type="MechanismEvidenceIndexRecorded",
                entity_id="mei-wrong-policy",
                run_id="run-1",
                payload_schema_version="mechanism_evidence_index_recorded.v1",
                payload=wrong_policy,
            )
        )

    wrong_factor = dict(payload, mei_id="mei-2", factor_spec_id="factor-2")
    with pytest.raises(EventTransitionError, match="one factor"):
        store.append_event(
            EventDraft(
                event_type="MechanismEvidenceIndexRecorded",
                entity_id="mei-2",
                run_id="run-1",
                payload_schema_version="mechanism_evidence_index_recorded.v1",
                payload=wrong_factor,
            )
        )

    no_evidence = dict(
        payload,
        mei_id="mei-3",
        source_result_hashes=[_hash("x")],
        source_event_hashes=[_hash("y")],
        decisive_event_hashes=[_hash("y")],
    )
    with pytest.raises(EventTransitionError, match="no prior falsification result"):
        store.append_event(
            EventDraft(
                event_type="MechanismEvidenceIndexRecorded",
                entity_id="mei-3",
                run_id="run-1",
                payload_schema_version="mechanism_evidence_index_recorded.v1",
                payload=no_evidence,
            )
        )

    probability = dict(payload, mei_id="mei-4", probability=0.9)
    with pytest.raises(EventValidationError, match="unknown payload fields"):
        store.append_event(
            EventDraft(
                event_type="MechanismEvidenceIndexRecorded",
                entity_id="mei-4",
                run_id="run-1",
                payload_schema_version="mechanism_evidence_index_recorded.v1",
                payload=probability,
            )
        )


def test_mei_role_partition_and_result_artifact_hash_are_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _append_contract(store)
    result, result_hash = _append_result_with_artifact(store)
    base = {
        "mei_id": "mei-bad",
        "factor_spec_id": "factor-1",
        "mei_hash": _hash("m"),
        "mei_schema_version": "mechanism_evidence_index.v1",
        "truth_table_version": "mechanism_evidence_truth_table.v1",
        "policy_version": "mei-policy.v1",
        "policy_hash": _hash("p"),
        "source_result_hashes": [result_hash],
        "source_event_hashes": [result.event_hash],
        "decisive_event_hashes": [result.event_hash],
        "advisory_event_hashes": [result.event_hash],
        "ordinal_state": "partial_support",
        "reason_codes": ["DECISIVE_SUPPORT_WITH_ADVISORY_GAPS"],
        "warning_codes": [],
        "limitation_codes": [],
    }
    with pytest.raises(EventValidationError, match="disjoint"):
        store.append_event(
            EventDraft(
                event_type="MechanismEvidenceIndexRecorded",
                entity_id="mei-bad",
                run_id="run-1",
                payload_schema_version="mechanism_evidence_index_recorded.v1",
                payload=base,
            )
        )

    bad_hash = dict(
        base,
        mei_id="mei-bad-hash",
        source_result_hashes=[_hash("z")],
        advisory_event_hashes=[],
    )
    with pytest.raises(EventTransitionError, match="artifact evidence"):
        store.append_event(
            EventDraft(
                event_type="MechanismEvidenceIndexRecorded",
                entity_id="mei-bad-hash",
                run_id="run-1",
                payload_schema_version="mechanism_evidence_index_recorded.v1",
                payload=bad_hash,
            )
        )
