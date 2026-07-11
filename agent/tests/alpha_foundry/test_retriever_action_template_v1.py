from __future__ import annotations

from pathlib import Path

import pytest

from src.alpha_foundry.dsl.identity import FactorIdentityService
from src.alpha_foundry.mutators import (
    SEED_MUTATION_TEMPLATE_REGISTRY_V1,
    SeedMutationTemplateV1,
    SeedMutator,
)
from src.alpha_foundry.retrieval.action_template_v1 import (
    FrozenRetrieverActionTemplateV1,
    RetrieverActionTemplateServiceV1,
)
from src.alpha_foundry.seed_bank import AlphaSeed
from src.alpha_quality.flags import ResolvedAGSFlags
from src.research_ledger.events import (
    EventDraft,
    EventValidationError,
    ResearchEventStore,
)
from src.research_ledger.hash_utils import canonical_json_hash, utc_now_iso
from test_process_memory import _semantics
from test_retriever_shadow import _flags, _views


def _service(store) -> RetrieverActionTemplateServiceV1:
    return RetrieverActionTemplateServiceV1(store=store, flags=store.flags)


def _freeze(
    tmp_path: Path,
    *,
    template_id: str = "rank_wrap",
    execution_run_id: str = "action-template-run",
):
    store, _, discovery = _views(tmp_path)
    parent_id = discovery.factual.factor_ids()[0]
    recorded = _service(store).freeze(
        execution_run_id=execution_run_id,
        parent_factor_spec_id=parent_id,
        template_id=template_id,
        eligible_event_watermark=discovery.source_watermark,
        data_snapshot_hash=discovery.data_snapshot_hash,
        retrieval_policy_hash=canonical_json_hash({"retrieval": "v5-fixture"}),
    )
    return store, discovery, recorded


def test_seed_mutator_uses_frozen_registry_without_changing_output() -> None:
    seed = AlphaSeed("seed", "close", "fixture")
    candidates = SeedMutator(max_candidates_per_seed=5).mutate(seed)
    assert SEED_MUTATION_TEMPLATE_REGISTRY_V1.template_ids == (
        "identity",
        "rank_wrap",
        "decay_3",
        "delay_1",
        "zscore_wrap",
    )
    assert [item.formula for item in candidates] == [
        "close",
        "rank(close)",
        "decay_linear(close, 3)",
        "delay(close, 1)",
        "zscore(close)",
    ]
    assert [item.metadata["mutation"] for item in candidates] == list(
        SEED_MUTATION_TEMPLATE_REGISTRY_V1.template_ids
    )
    assert SEED_MUTATION_TEMPLATE_REGISTRY_V1.registry_hash == canonical_json_hash(
        {
            "schema_version": "seed_mutation_template_registry.v1",
            "templates": [
                item.to_dict()
                for item in SEED_MUTATION_TEMPLATE_REGISTRY_V1.templates
            ],
        }
    )
    assert SeedMutator(max_candidates_per_seed=5).mutate(
        AlphaSeed("long", "x" * 513, "fixture")
    ) == []
    with pytest.raises(ValueError, match="invalid types"):
        SeedMutationTemplateV1("delay_1", "window_call", "delay", True)


def test_frozen_action_derives_real_expected_diff_and_is_replayable(
    tmp_path: Path,
) -> None:
    store, discovery, recorded = _freeze(tmp_path)
    action = recorded.action
    parent_definition = next(
        event
        for event in store.query_events(event_type="FactorDefinitionRecorded")
        if event.entity_id == action.parent_factor_spec_id
    )
    parent_formula = parent_definition.payload["metadata"]["canonical_formula"]
    assert action.expected_formula == f"rank({parent_formula})"
    assert action.expected_motif_version == "ast-motif.v1"
    assert action.expected_motif == "Wrap:rank"
    assert action.expected_ast_diff_hash is not None
    assert action.identity_action is False
    assert action.eligible_event_watermark == discovery.source_watermark
    assert recorded.event.run_id == action.execution_run_id
    assert FrozenRetrieverActionTemplateV1.from_dict(action.to_dict()) == action
    assert _service(store).freeze(
        execution_run_id=action.execution_run_id,
        parent_factor_spec_id=action.parent_factor_spec_id,
        template_id=action.template_id,
        eligible_event_watermark=action.eligible_event_watermark,
        data_snapshot_hash=action.data_snapshot_hash,
        retrieval_policy_hash=action.retrieval_policy_hash,
    ).event.event_hash == recorded.event.event_hash
    assert store.verify_chain()


def test_identity_action_has_no_placeholder_diff_or_motif(tmp_path: Path) -> None:
    store, _, recorded = _freeze(tmp_path, template_id="identity")
    action = recorded.action
    assert action.identity_action is True
    assert action.expected_ast_diff_hash is None
    assert action.expected_motif_version is None
    assert action.expected_motif is None
    assert store.verify_chain()


def test_unknown_template_ineligible_parent_and_nondiscovery_watermark_fail(
    tmp_path: Path,
) -> None:
    store, _, discovery = _views(tmp_path / "eligible")
    parent_id = discovery.factual.factor_ids()[0]
    args = {
        "execution_run_id": "negative-action-run",
        "parent_factor_spec_id": parent_id,
        "eligible_event_watermark": discovery.source_watermark,
        "data_snapshot_hash": discovery.data_snapshot_hash,
        "retrieval_policy_hash": canonical_json_hash({"policy": "negative"}),
    }
    with pytest.raises(ValueError, match="unknown seed mutation template"):
        _service(store).freeze(template_id="caller-template", **args)

    open_store = ResearchEventStore(
        tmp_path / "open-parent" / "research.sqlite",
        artifact_root=tmp_path / "open-parent" / "artifacts",
        flags=_flags(),
        code_version="action-template-open-parent-test",
    )
    identity = FactorIdentityService(store=open_store, flags=open_store.flags)
    open_parent = identity.record_attempt(
        trial_id="open-parent",
        run_id="open-run",
        candidate_id="open-parent",
        formula="rank(close)",
        semantics=_semantics(),
    )
    watermark = open_store.replay().watermark_event_hash
    assert open_parent.factor_spec_id and watermark
    with pytest.raises(ValueError, match="terminal discovery evidence boundary"):
        _service(open_store).freeze(
            execution_run_id="open-run",
            parent_factor_spec_id=open_parent.factor_spec_id,
            template_id="rank_wrap",
            eligible_event_watermark=watermark,
            data_snapshot_hash=canonical_json_hash({"snapshot": "open"}),
            retrieval_policy_hash=canonical_json_hash({"policy": "open"}),
        )

    monitoring = store.append_event(
        EventDraft(
            event_type="TrialStarted",
            entity_id="forward-only-trial",
            run_id="monitoring",
            payload_schema_version="trial_started.v1",
            idempotency_key="forward-only-trial:start",
            payload={
                "trial_id": "forward-only-trial",
                "candidate_id": "forward-only-candidate",
                "data_scope": "forward",
                "objective": "monitoring_only",
                "started_at": utc_now_iso(),
            },
        )
    )
    with pytest.raises(ValueError, match="terminal discovery evidence boundary"):
        _service(store).freeze(
            template_id="rank_wrap",
            **{**args, "eligible_event_watermark": monitoring.event_hash},
        )


def test_rehashed_template_substitution_is_rejected_by_independent_rebuild(
    tmp_path: Path,
) -> None:
    store, _, recorded = _freeze(tmp_path, template_id="rank_wrap")
    original = recorded.action
    definition = next(
        event
        for event in store.query_events(event_type="FactorDefinitionRecorded")
        if event.event_hash == original.parent_definition_event_hash
    )
    substituted = FrozenRetrieverActionTemplateV1.build(
        execution_run_id="forged-template-run",
        parent_definition=definition.payload,
        parent_definition_event_hash=definition.event_hash,
        eligible_event_watermark=original.eligible_event_watermark,
        data_snapshot_hash=original.data_snapshot_hash,
        retrieval_policy_hash=original.retrieval_policy_hash,
        template_id="zscore_wrap",
    ).to_dict()
    substituted["template_id"] = "rank_wrap"
    content = {
        key: value
        for key, value in substituted.items()
        if key not in {"action_id", "action_hash"}
    }
    substituted["action_hash"] = canonical_json_hash(content)
    substituted["action_id"] = (
        "retriever-action-v1-"
        + substituted["action_hash"].removeprefix("sha256:")[:24]
    )
    with pytest.raises(EventValidationError, match="deterministic template rebuild"):
        store.append_event(
            EventDraft(
                event_type="RetrieverActionTemplateFrozen",
                entity_id=substituted["action_id"],
                run_id="forged-template-run",
                payload_schema_version="retriever_action_template_frozen.v1",
                payload=substituted,
                idempotency_key="retriever-action-v1:forged-template",
            )
        )


def test_action_template_capability_is_parent_dominated(tmp_path: Path) -> None:
    store, _, _ = _views(tmp_path)
    disabled = ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_ALPHA_FOUNDRY": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
            "VIBE_TRADING_FACTOR_DAG": "1",
            "VIBE_TRADING_PROCESS_MEMORY": "1",
            "VIBE_TRADING_TOPOLOGY_RETRIEVER": "0",
        }
    )
    with pytest.raises(RuntimeError, match="disabled"):
        RetrieverActionTemplateServiceV1(store=store, flags=disabled)
