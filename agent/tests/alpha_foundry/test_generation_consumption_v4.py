from __future__ import annotations

from pathlib import Path

import pytest

from src.alpha_foundry.activation import (
    ActivationArtifactStore,
    ActivationEvidenceService,
    PairedActivationRunner,
)
from src.alpha_foundry.activation.generation_consumption_v4 import (
    ActivationGenerationConsumptionV4,
    ActivationTreatmentGeneratorV4,
)
from src.alpha_foundry.mutators import SeedMutator
from src.alpha_foundry.search import AlphaFoundrySearch
from src.alpha_foundry.search_lifecycle import EventSourcedSearchLifecycle
from src.alpha_foundry.seed_bank import AlphaSeed, SeedBank
from src.research_ledger.events import EventDraft, EventValidationError
from src.research_ledger.hash_utils import canonical_json_hash
from test_generation_consumption_v1 import _SkipEvaluator, _manifest
from test_retriever_decision_v7 import _record as _record_v7
from test_search_lifecycle import _semantics


def _run(tmp_path: Path):
    store, frozen, snapshot, source, schedule, recorded = _record_v7(tmp_path)
    runner = PairedActivationRunner(ActivationArtifactStore(tmp_path / "runner"))
    service = ActivationEvidenceService(store)
    holder = {}

    def execute(request, scope):
        lifecycle = EventSourcedSearchLifecycle(
            store=store, flags=store.flags, semantics=_semantics(),
            evaluator=_SkipEvaluator(),
            data_snapshot_hash=snapshot.snapshot.snapshot_hash,
        )
        if request.arm == "control":
            search = AlphaFoundrySearch(
                seed_bank=SeedBank([AlphaSeed("control", "close", "fixture")]),
                mutator=SeedMutator(max_candidates_per_seed=3),
                max_candidates=request.candidate_budget,
                trial_budget=request.compute_budget,
                lifecycle=lifecycle,
                run_id=request.execution_run_id,
            )
            result = search.generate()
            return _manifest(request, snapshot.snapshot.snapshot_hash, result)
        parent_id = source.source.candidates[0].factor_spec_id
        generated = ActivationTreatmentGeneratorV4().run(
            request=request, scope=scope, recorded_decision=recorded,
            parent_seeds=(AlphaSeed(parent_id, "rank(close)", "fixture"),),
            lifecycle=lifecycle,
        )
        relative = service.record_generation_consumption_v4(generated.evidence)
        holder.update(generated=generated, relative=relative)
        return _manifest(
            request, snapshot.snapshot.snapshot_hash, generated.search_result
        )

    runner.run_pair(
        runner.register(frozen), run_group_id="group-00",
        mechanism_family="momentum", dag_region="leaf", executor=execute,
    )
    return store, source, schedule, recorded, holder


def test_v4_consumes_v7_without_control_outcome_authority(tmp_path: Path) -> None:
    store, source, schedule, recorded, holder = _run(tmp_path)
    evidence = holder["generated"].evidence
    assert evidence.base.retriever_decision_event_hash == recorded.event.event_hash
    assert evidence.schedule_event_hash == schedule.event.event_hash
    assert evidence.feature_source_event_hash == source.event.event_hash
    assert evidence.base.source_failure_codes == ()
    assert evidence.base.source_complete is True
    raw = evidence.to_dict()
    assert "control_evidence_event_hash" not in raw
    assert raw["schedule_event_hash"] == schedule.event.event_hash
    assert len(store.query_events(
        event_type="ActivationGenerationConsumptionV4Recorded"
    )) == 1
    assert store.verify_chain()


def test_v4_event_rejects_rehashed_schedule_substitution(tmp_path: Path) -> None:
    store, _, _, _, holder = _run(tmp_path)
    event = store.query_events(
        event_type="ActivationGenerationConsumptionV4Recorded"
    )[0]
    payload = event.to_dict()["payload"]
    payload["schedule_hash"] = "sha256:" + "f" * 64
    payload["evidence_hash"] = canonical_json_hash(
        payload, exclude_keys=("generation_id", "evidence_hash", "artifact_refs")
    )
    payload["generation_id"] = (
        "activation-generation-v4-"
        + payload["evidence_hash"].removeprefix("sha256:")[:24]
    )
    with pytest.raises(EventValidationError, match="evidence is invalid"):
        store.append_event(
            EventDraft(
                event_type="ActivationGenerationConsumptionV4Recorded",
                entity_id=payload["generation_id"],
                run_id=event.run_id,
                payload_schema_version="activation_generation_consumption_recorded.v4",
                payload=payload,
                idempotency_key="activation-generation-v4:forged-schedule",
            )
        )


def test_v4_service_rejects_v3_or_base_evidence(tmp_path: Path) -> None:
    store, _, _, _, holder = _run(tmp_path)
    with pytest.raises(TypeError, match="schedule-bound"):
        ActivationEvidenceService(store).record_generation_consumption_v4(
            holder["generated"].evidence.base
        )


def test_v4_rejects_rehashed_false_source_completeness(tmp_path: Path) -> None:
    _, _, _, _, holder = _run(tmp_path)
    raw = holder["generated"].evidence.to_dict()
    raw["generated_candidates"] = []
    raw["consumed_action_ids"] = []
    raw["consumed_parent_factor_spec_ids"] = []
    raw["source_failure_codes"] = []
    raw["source_complete"] = True
    raw["evidence_hash"] = canonical_json_hash(
        raw, exclude_keys=("evidence_hash",)
    )
    with pytest.raises(ValueError, match="omits deterministic source failures"):
        ActivationGenerationConsumptionV4.from_dict(raw)


def test_v4_rejects_rehashed_false_consumption_identity(tmp_path: Path) -> None:
    _, _, _, _, holder = _run(tmp_path)
    raw = holder["generated"].evidence.to_dict()
    raw["consumed_action_ids"] = ["caller-invented-action"]
    raw["consumed_parent_factor_spec_ids"] = ["caller-invented-parent"]
    raw["source_failure_codes"] = []
    raw["source_complete"] = True
    raw["evidence_hash"] = canonical_json_hash(
        raw, exclude_keys=("evidence_hash",)
    )
    with pytest.raises(ValueError, match="omits deterministic source failures"):
        ActivationGenerationConsumptionV4.from_dict(raw)
