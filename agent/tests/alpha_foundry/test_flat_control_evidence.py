from __future__ import annotations

from pathlib import Path

import pytest

from src.alpha_foundry.control_evidence import (
    OfficialSearchControlArtifactStoreV1,
    OfficialSearchControlEvidenceV1,
    OfficialSearchControlServiceV1,
)
from src.alpha_foundry.mutators import SeedMutator
from src.alpha_foundry.search import AlphaFoundrySearch
from src.alpha_foundry.search_lifecycle import (
    EventSourcedSearchLifecycle,
    SearchEvaluationOutcome,
)
from src.alpha_foundry.seed_bank import AlphaSeed, SeedBank
from src.research_ledger.hash_utils import canonical_json_hash
from src.research_ledger.events import EventDraft, EventValidationError
from test_search_lifecycle import _flags, _semantics, _store


class _SkipEvaluator:
    def evaluate(self, **kwargs):
        return SearchEvaluationOutcome(
            status="skip",
            decision="none",
            reason_codes=("CONTROL_EVIDENCE_FIXTURE",),
        )


def _evidence(tmp_path: Path):
    store = _store(tmp_path)
    snapshot = canonical_json_hash({"snapshot": "flat-control"})
    lifecycle = EventSourcedSearchLifecycle(
        store=store,
        flags=_flags(),
        semantics=_semantics(),
        evaluator=_SkipEvaluator(),
        data_snapshot_hash=snapshot,
    )
    search = AlphaFoundrySearch(
        seed_bank=SeedBank(
            [
                AlphaSeed("seed-a", "close", "registry-a"),
                AlphaSeed("seed-b", "volume", "registry-b"),
            ]
        ),
        mutator=SeedMutator(max_candidates_per_seed=3),
        max_candidates=6,
        trial_budget=6,
        lifecycle=lifecycle,
        run_id="flat-control-run",
    )
    result = search.generate()
    return store, search, result, OfficialSearchControlEvidenceV1.from_search(
        search, result
    )


def test_official_control_replays_exact_candidate_order_and_policy(tmp_path: Path) -> None:
    store, _, result, evidence = _evidence(tmp_path)
    assert evidence.replay() == tuple(
        candidate.candidate_id for candidate in result.candidates
    )
    assert evidence.output_hash == canonical_json_hash(
        {"official_candidate_ids": list(evidence.replay())}
    )
    assert evidence.policy.max_candidates_per_seed == 3
    assert evidence.policy.max_candidates == 6
    assert evidence.policy.trial_budget == 6
    assert len(evidence.terminal_event_hashes) == 6
    with pytest.raises(TypeError):
        evidence.candidates[0]["candidate_id"] = "mutable"  # type: ignore[index]
    assert store.verify_chain()


def test_rehashed_caller_candidate_fabrication_fails_replay(tmp_path: Path) -> None:
    _, _, _, evidence = _evidence(tmp_path)
    raw = evidence.to_dict()
    raw["candidates"][0]["candidate_id"] = "caller-forged"
    raw["output_hash"] = canonical_json_hash(
        {"official_candidate_ids": [item["candidate_id"] for item in raw["candidates"]]}
    )
    raw["evidence_hash"] = canonical_json_hash(
        raw, exclude_keys=("evidence_hash",)
    )
    forged = OfficialSearchControlEvidenceV1.from_dict(raw)
    with pytest.raises(ValueError, match="does not replay"):
        forged.replay()


def test_control_artifact_is_strict_content_addressed_and_bounded(tmp_path: Path) -> None:
    _, _, _, evidence = _evidence(tmp_path)
    artifacts = OfficialSearchControlArtifactStoreV1(tmp_path / "artifacts")
    reference = artifacts.write(evidence)
    assert artifacts.read(
        reference["relative_path"], evidence.evidence_hash
    ) == evidence
    target = artifacts.root.joinpath(*reference["relative_path"].split("/"))
    original = target.read_text(encoding="utf-8")
    duplicate = original.replace(
        '{"candidates"', '{"candidates":[],"candidates"', 1
    )
    target.write_text(duplicate, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        artifacts.read(reference["relative_path"], evidence.evidence_hash)
    raw = evidence.to_dict()
    raw["policy"]["trial_budget"] = True
    raw["policy"]["policy_hash"] = canonical_json_hash(
        raw["policy"], exclude_keys=("policy_hash",)
    )
    raw["evidence_hash"] = canonical_json_hash(
        raw, exclude_keys=("evidence_hash",)
    )
    with pytest.raises(ValueError, match="budgets must be integers"):
        OfficialSearchControlEvidenceV1.from_dict(raw)


def test_control_evidence_requires_production_mutator_and_terminal_lifecycle(
    tmp_path: Path,
) -> None:
    store, search, result, _ = _evidence(tmp_path)
    no_lifecycle = AlphaFoundrySearch(
        seed_bank=search.seed_bank,
        mutator=SeedMutator(),
    )
    with pytest.raises(ValueError, match="event-sourced"):
        OfficialSearchControlEvidenceV1.from_search(
            no_lifecycle, no_lifecycle.generate()
        )

    class _CallerMutator(SeedMutator):
        pass

    search.mutator = _CallerMutator()  # type: ignore[assignment]
    with pytest.raises(ValueError, match="frozen mutator"):
        OfficialSearchControlEvidenceV1.from_search(search, result)
    assert store.verify_chain()


def test_control_service_appends_replayable_source_event(tmp_path: Path) -> None:
    store, search, result, evidence = _evidence(tmp_path)
    recorded = OfficialSearchControlServiceV1(store).record(search, result)
    assert recorded.evidence == evidence
    assert recorded.event.event_type == "OfficialSearchControlRecorded"
    assert recorded.event.payload["policy_hash"] == evidence.policy.policy_hash
    assert recorded.event.payload["output_hash"] == evidence.output_hash
    assert store.verify_chain()

    payload = recorded.event.to_dict()["payload"]
    payload["control_id"] = "official-control-v1-forged"
    payload["output_hash"] = canonical_json_hash({"caller": "output"})
    with pytest.raises(EventValidationError, match="differs from source artifact"):
        store.append_event(
            EventDraft(
                event_type="OfficialSearchControlRecorded",
                entity_id="official-control-v1-forged",
                run_id=evidence.run_id,
                payload_schema_version="official_search_control_recorded.v1",
                payload=payload,
                idempotency_key="official-search-control-v1:forged",
            )
        )

    reference = recorded.event.payload["artifact_refs"][0]
    target = store.artifact_root.joinpath(*str(reference["relative_path"]).split("/"))
    target.write_text("{}\n", encoding="utf-8")
    assert store.verify_chain() is False
