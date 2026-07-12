from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.alpha_quality.flags import ResolvedAGSFlags
from src.alpha_foundry.dsl.grammar import DEFAULT_GRAMMAR
from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy
from src.research_ledger.events import (
    ArtifactReferenceError,
    EventDraft,
    EventIdempotencyConflict,
    EventMutationError,
    EventTransitionError,
    EventValidationError,
    ResearchEventAppendError,
    ResearchEventStore,
    PAYLOAD_SPECS,
)
from src.research_ledger.events.payloads import validate_and_redact_payload
from src.research_ledger.hash_utils import canonical_json_hash
from src.research_ledger.trial_ledger import TrialLedger, TrialLedgerEntry


def _flags(*, enabled: bool = True) -> ResolvedAGSFlags:
    settings = {}
    if enabled:
        settings = {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
            "VIBE_TRADING_ALPHA_FOUNDRY": "1",
            "VIBE_TRADING_ADMISSION_GATE": "1",
            "VIBE_TRADING_FORWARD_TRACKING": "1",
            "VIBE_TRADING_FACTOR_DAG": "1",
            "VIBE_TRADING_PROCESS_MEMORY": "1",
            "VIBE_TRADING_TOPOLOGY_RETRIEVER": "1",
            "VIBE_TRADING_FALSIFICATION_CONTRACT": "1",
            "VIBE_TRADING_COMPLEMENT_V2": "1",
            "VIBE_TRADING_DECISION_V2": "1",
        }
    return ResolvedAGSFlags.from_settings(settings)


def _store(
    tmp_path: Path,
    *,
    enabled: bool = True,
    durability_profile: str = "authoritative",
) -> ResearchEventStore:
    return ResearchEventStore(
        tmp_path / "research.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=_flags(enabled=enabled),
        code_version="test-code-v1",
        durability_profile=durability_profile,
    )


def _factor_payload(factor_spec_id: str = "factor-spec-1") -> dict[str, object]:
    return {
        "factor_spec_id": factor_spec_id,
        "expression_id": "expression-1",
        "canonical_ast_hash": "sha256:" + "a" * 64,
        "grammar_version": "1.0.0",
        "grammar_hash": "sha256:" + "b" * 64,
        "metadata": {},
        "artifact_refs": [],
    }


def _draft(
    *,
    event_type: str = "FactorDefinitionRecorded",
    entity_id: str = "factor-spec-1",
    payload: dict[str, object] | None = None,
    payload_schema_version: str | None = None,
    idempotency_key: str | None = None,
) -> EventDraft:
    return EventDraft(
        event_type=event_type,
        entity_id=entity_id,
        run_id="run-1",
        payload_schema_version=(
            payload_schema_version
            or {
                "FactorDefinitionRecorded": "factor_definition_recorded.v1",
                "TrialStarted": "trial_started.v1",
                "TrialTerminated": "trial_terminated.v1",
                "EvaluationRecorded": "evaluation_recorded.v1",
            }.get(event_type, "unknown.v1")
        ),
        payload=payload if payload is not None else _factor_payload(),
        idempotency_key=idempotency_key,
    )


def test_payload_hash_is_deterministic_but_event_hash_represents_each_append(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    first = store.append_event(_draft())
    second = store.append_event(_draft())

    assert first.payload_hash == second.payload_hash
    assert first.event_id != second.event_id
    assert first.event_hash != second.event_hash
    assert second.previous_event_hash == first.event_hash
    assert store.verify_chain()
    with pytest.raises(TypeError):
        first.payload["factor_spec_id"] = "mutated"  # type: ignore[index]
    with pytest.raises(TypeError):
        first.feature_flags["VIBE_TRADING_RESEARCH_EVENTS"] = False  # type: ignore[index]


def test_idempotent_retry_returns_existing_event_and_conflict_is_rejected(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    draft = _draft(idempotency_key="factor-definition:1")

    first = store.append_event(draft)
    retried = store.append_event(draft)

    assert retried == first
    assert len(store.query_events()) == 1
    with pytest.raises(EventIdempotencyConflict):
        store.append_event(
            _draft(
                idempotency_key="factor-definition:1",
                entity_id="factor-spec-conflict",
                payload=_factor_payload("factor-spec-conflict"),
            )
        )
    balanced = ResearchEventStore(
        tmp_path / "research.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=_flags(),
        code_version="test-code-v1",
        durability_profile="balanced",
    )
    with pytest.raises(EventIdempotencyConflict):
        balanced.append_event(draft)


def test_unknown_event_payload_version_and_unknown_fields_are_rejected(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    with pytest.raises(EventValidationError, match="unknown event type"):
        store.append_event(_draft(event_type="CallerInventedEvent"))
    with pytest.raises(EventValidationError, match="payload schema version"):
        store.append_event(_draft(payload_schema_version="factor_definition_recorded.v999"))
    bad = _factor_payload()
    bad["is_terminal"] = True
    with pytest.raises(EventValidationError, match="unknown payload fields"):
        store.append_event(_draft(payload=bad))


def test_closed_payload_registry_covers_every_required_event_type() -> None:
    assert set(PAYLOAD_SPECS) == {
        "TrialStarted",
        "FactorDefinitionRecorded",
        "RegistryBootstrapRecorded",
        "RegistryBootstrapRecordedV2",
        "DerivationRecorded",
        "ProcessActionFrozen",
        "ProcessOutcomeRecorded",
        "ProcessActionFrozenV2",
        "ProcessOutcomeRecordedV2",
        "GenerationFailureRecorded",
        "EvaluationRecorded",
        "TrialTerminated",
        "RetrieverDecisionRecorded",
        "RetrieverActionTemplateFrozen",
        "TrainValidDataSnapshotFrozen",
        "EvaluationPolicyRegistered",
        "AsharePITAdapterRegistered",
        "AsharePITSnapshotRecorded",
        "RetrieverFeatureSourceRecorded",
        "RetrieverDecisionV2Recorded",
        "RetrieverDecisionV3Recorded",
        "RetrieverDecisionV4Recorded",
        "RetrieverDecisionV5Recorded",
        "RetrieverDecisionV6Recorded",
        "RetrieverDecisionV7Recorded",
        "OfficialSearchControlRecorded",
        "PreArmFlatScheduleFrozen",
        "ActivationPairExecutionScheduled",
        "ActivationPairExecutionClaimed",
        "ActivationPlanRegistered",
        "ActivationRunRecorded",
        "ActivationRunSourceAudited",
        "ActivationResourceMeasured",
        "ActivationResourceMeasuredV2",
        "ActivationGenerationConsumptionRecorded",
        "ActivationGenerationConsumptionV2Recorded",
        "ActivationGenerationConsumptionV3Recorded",
        "ActivationGenerationConsumptionV4Recorded",
        "ActivationResultRecorded",
        "RetrieverActivationDecisionRecorded",
        "FalsificationContractRegistered",
        "SequentialProtocolRegistered",
        "SequentialLookRecorded",
        "OutcomeDataAccessed",
        "FalsificationResultRecorded",
        "MechanismEvidenceIndexRecorded",
        "ComplementEvidenceRecorded",
        "QualityDecisionRecorded",
        "DecisionEvidenceV3Recorded",
        "ScorecardDecisionEvidenceV3Recorded",
        "SnapshotDecisionEvidenceV3Recorded",
        "QualityDecisionV2Recorded",
        "QualityDecisionV3Recorded",
        "FinalCandidateFrozen",
        "FinalTestCapabilityIssued",
        "FinalTestAccessRecorded",
        "FinalTestArtifactRecorded",
        "ForwardPlanV2Recorded",
        "ForwardObservationV2Recorded",
        "ForwardPlanRecorded",
        "ForwardObservationRecorded",
    }
    assert len({spec.version for spec in PAYLOAD_SPECS.values()}) == len(PAYLOAD_SPECS)
    with pytest.raises(TypeError):
        PAYLOAD_SPECS["CallerInventedEvent"] = PAYLOAD_SPECS["TrialStarted"]  # type: ignore[index]
    with pytest.raises(TypeError):
        PAYLOAD_SPECS["TrialStarted"].fields["is_terminal"] = lambda value, path: None  # type: ignore[index]


def test_every_registered_payload_schema_validates_a_complete_production_shape() -> None:
    digest = "sha256:" + "d" * 64
    timestamp = "2025-01-01T00:00:00Z"
    samples: dict[str, dict[str, object]] = {
        "TrialStarted": {
            "trial_id": "trial-1",
            "candidate_id": "candidate-1",
            "data_scope": "train_valid",
            "objective": "rank_ic",
            "started_at": timestamp,
        },
            "FactorDefinitionRecorded": _factor_payload(),
            "RegistryBootstrapRecorded": {
                "snapshot_id": "registry-1",
                "registry_snapshot_hash": digest,
                "registry_code_hash": digest,
                "grammar_hash": digest,
                "roots": [
                    {
                        "alpha_id": "fixture_alpha",
                        "status": "legacy_opaque",
                        "expression_id": None,
                        "legacy_formula_hash": digest,
                    }
                ],
            },
            "RegistryBootstrapRecordedV2": {
                "snapshot_id": "registry-v2-1",
                "registry_snapshot_hash": digest,
                "registry_code_hash": digest,
                "grammar_version": DEFAULT_GRAMMAR.semantic_version,
                "grammar_hash": DEFAULT_GRAMMAR.content_hash,
                "grammar_definition": DEFAULT_GRAMMAR.to_dict(),
                "roots": [
                    {
                        "alpha_id": "fixture_alpha", "status": "legacy_opaque",
                        "expression_id": None, "canonical_formula": None,
                        "legacy_formula_hash": digest, "source_hash": None,
                        "source_status": "unavailable",
                        "source_reason": "REGISTRY_SOURCE_API_UNAVAILABLE",
                    }
                ],
            },
            "DerivationRecorded": {
            "child_factor_spec_id": "child-1",
            "parent_factor_spec_ids": ["parent-1", "parent-2"],
            "trial_terminal_event_hash": digest,
                "derivation_kind": "crossover",
            },
            "ProcessActionFrozen": {
                "action_id": "action-1",
                "trial_id": "trial-1",
                "parent_factor_spec_id": "factor-1",
                "candidate_id": "candidate-1",
                "base_expected_utility": 0.1,
                "eligible_event_watermark": digest,
                "policy_hash": digest,
                "seed": 7,
                "candidate_budget": 10,
                "frozen_at": timestamp,
            },
            "ProcessOutcomeRecorded": {
                "outcome_id": "outcome-1",
                "action_id": "action-1",
                "trial_id": "trial-1",
                "terminal_event_hash": digest,
                "data_scope": "train_valid",
                "child_factor_spec_id": "factor-2",
                "observed_validation_utility": 0.2,
                "ast_diff": {"schema_version": "ast_diff.v1"},
                "available_at": timestamp,
                "policy_hash": digest,
            },
            "ProcessActionFrozenV2": {
                "action_id": "action-v2-1",
                "trial_id": "trial-1",
                "parent_factor_spec_id": "factor-1",
                "candidate_id": "candidate-1",
                "base_expected_utility": 0.1,
                "eligible_event_watermark": digest,
                "policy_hash": digest,
                "utility_policy_hash": digest,
                "data_snapshot_hash": digest,
                "regime_config_hash": None,
                "run_group_id": "run-group-1",
                "seed": 7,
                "candidate_budget": 10,
                "frozen_at": timestamp,
            },
            "ProcessOutcomeRecordedV2": {
                "outcome_id": "outcome-v2-1",
                "action_id": "action-v2-1",
                "trial_id": "trial-1",
                "terminal_event_hash": digest,
                "evaluation_event_hash": digest,
                "derivation_event_hash": digest,
                "data_scope": "valid",
                "child_factor_spec_id": "factor-2",
                "observed_validation_utility": 0.2,
                "scorecard_hash": digest,
                "utility_policy_hash": digest,
                "ast_diff": {"schema_version": "ast_diff.v1"},
                "ast_diff_hash": digest,
                "available_at": timestamp,
                "policy_hash": digest,
                "data_snapshot_hash": digest,
                "regime_config_hash": None,
                "run_group_id": "run-group-1",
            },
        "GenerationFailureRecorded": {
            "trial_id": "trial-1",
            "failure_code": "INVALID_FORMULA",
            "failure_kind": "invalid",
            "message": "formula did not parse",
            "occurred_at": timestamp,
        },
        "EvaluationRecorded": {
            "evaluation_id": "evaluation-1",
            "trial_id": "trial-1",
            "factor_spec_id": "factor-1",
            "data_scope": "train_valid",
            "scorecard_hash": digest,
            "artifact_refs": [],
            "metadata": {},
        },
        "TrialTerminated": {
            "trial_id": "trial-1",
            "status": "reject",
            "reason_codes": ["QUALITY_REJECTED"],
            "decision": "reject",
            "evaluation_event_hash": None,
            "terminated_at": timestamp,
        },
                "RetrieverDecisionRecorded": {
            "decision_id": "retriever-1",
            "selected_factor_spec_ids": ["factor-1"],
            "selection_propensity": 0.5,
            "seed": 7,
            "policy_hash": digest,
            "eligible_event_watermark": digest,
                    "veto_reason": None,
                },
        "RetrieverActionTemplateFrozen": {
                    "action_id": "retriever-action-v1-fixture",
                    "action_hash": digest,
                    "schema_version": "frozen_retriever_action_template.v1",
                    "execution_run_id": "retriever-action-run",
                    "parent_factor_spec_id": "factor-1",
                    "parent_definition_event_hash": digest,
                    "eligible_event_watermark": digest,
                    "data_snapshot_hash": digest,
                    "retrieval_policy_hash": digest,
                    "template_registry_hash": digest,
                    "template_id": "rank_wrap",
                    "expected_formula": "rank(close)",
                    "expected_formula_hash": digest,
                    "expected_candidate_id": "d" * 16,
                    "expected_expression_id": digest,
                    "expected_canonical_ast_hash": digest,
                    "expected_ast_diff_hash": digest,
                    "expected_motif_version": "ast-motif.v1",
                    "expected_motif": "Wrap:rank",
                "identity_action": False,
            },
            "TrainValidDataSnapshotFrozen": {
                "snapshot_id": "train-valid-snapshot-v1-" + "d" * 24,
                "snapshot_hash": digest,
                "data_scope": "train_valid",
                "panel_content_hash": digest,
                "frame_content_hashes": {"close": digest},
                "frame_names": ["close"],
                "source_config_hash": digest,
                "pit_contract_present": True,
                "survivorship_bias": False,
                "artifact_refs": [],
            },
        "RetrieverFeatureSourceRecorded": {
                "feature_source_id": "retriever-feature-source-v1-" + "d" * 24,
                "source_hash": digest,
                "execution_run_id": "feature-run",
                "snapshot_event_hash": digest,
                "snapshot_hash": digest,
                "eligible_event_watermark": digest,
                "retrieval_policy_hash": digest,
                "feature_policy_hash": digest,
                "action_event_hashes": [digest],
                "scorecard_event_hashes": [digest],
                "candidate_count": 1,
                "candidate_hashes": [digest],
                "semantic_state": "unavailable",
                "artifact_refs": [],
            },
                    "RetrieverDecisionV2Recorded": {
                "decision_id": "retriever-v2-1",
                "decision_hash": digest,
                "selected_factor_spec_ids": ["factor-1"],
                "seed": 7,
                "policy_version": "topology_shadow_policy.v2",
                "policy_hash": digest,
                "policy_config": {
                    "policy_version": "topology_shadow_policy.v2",
                    "epsilon": 1e-9,
                    "memory_weight": 0.5,
                    "residual_clip": 0.25,
                    "veto_exploration_probability": 0.05,
                    "softmax_temperature": 1.0,
                    "maximum_candidate_budget": 10000,
                },
                "eligible_event_watermark": digest,
                "data_snapshot_hash": digest,
                "candidate_budget": 1,
                "official_output_hash": digest,
                "propensity_semantics": "sequential_softmax_draw_probability.v1",
                "components": [
                    {
                        "factor_spec_id": "factor-1",
                        "action_id": "action-1",
                        "motif": "Wrap:rank",
                        "node_kind": "leaf",
                        "output_panel_hash": digest,
                        "semantic_model_id": "embedding-model",
                        "semantic_model_version": "1",
                        "semantic_embedding_hash": digest,
                        "cost_evidence_hash": digest,
                        "valdiv": 0.5,
                        "semdiv": 0.5,
                        "syndiv": 0.5,
                        "topology_score": 0.125,
                        "base_score": 0.1,
                        "memory_adjustment": 0.0,
                        "action_score": -2.3,
                        "confidence": 0.0,
                        "selected": True,
                        "selection_propensity": 1.0,
                        "warnings": [],
                        "veto_reason": None,
                    }
                ],
                    "shadow_only": True,
                },
        "RetrieverDecisionV3Recorded": {
                    "decision_id": "retriever-v3-1",
                    "decision_hash": digest,
                    "shadow_decision_hash": digest,
                    "input_bundle_hash": digest,
                    "selected_factor_spec_ids": ["factor-1"],
                    "seed": 7,
                    "policy_version": "topology_activation_policy.v3",
                    "policy_hash": digest,
                    "policy_config": ActivationRetrieverPolicy().to_dict(),
                    "eligible_event_watermark": digest,
                    "data_snapshot_hash": digest,
                    "candidate_budget": 1,
                    "official_output_hash": digest,
                    "propensity_semantics": "sequential_softmax_draw_probability.v1",
                    "components": [
                        {
                            "factor_spec_id": "factor-1",
                            "action_id": "action-1",
                            "motif": "Wrap:rank",
                            "node_kind": "leaf",
                            "output_panel_hash": digest,
                            "semantic_model_id": "embedding-model",
                            "semantic_model_version": "1",
                            "semantic_embedding_hash": digest,
                            "cost_evidence_hash": digest,
                            "valdiv": 0.5,
                            "semdiv": 0.5,
                            "syndiv": 0.5,
                            "topology_score": 0.125,
                            "base_score": 0.1,
                            "memory_adjustment": 0.0,
                            "action_score": -2.3,
                            "confidence": 0.0,
                            "selected": True,
                            "selection_propensity": 1.0,
                            "warnings": [],
                            "veto_reason": None,
                        }
                    ],
                    "shadow_only": True,
                    "artifact_refs": [],
                },
                "EvaluationPolicyRegistered": {
                    "registration_id": "evaluation-policy-registration-v1-fixture",
                    "bundle_hash": digest,
                    "producer_schema_version": "evaluation_policy_registry_service.v1",
                    "producer_policy_hash": digest,
                    "calendar_hash": digest,
                    "evaluation_time_policy_hash": digest,
                    "split_plan_hash": digest,
                    "preregistration_watermark": None,
                    "artifact_refs": [
                        {
                            "relative_path": "registered-evaluation-policy-v1/fixture.json",
                            "artifact_hash": digest,
                            "media_type": "application/vnd.vibe.registered-evaluation-policy-v1+json",
                        }
                    ],
                    },
                "AsharePITAdapterRegistered": {
                    "registration_id": "ashare-pit-adapter-v1-" + "d" * 24,
                    "adapter_id": "fixture-pit-v1",
                    "provider": "fixture-provider",
                    "adapter_version": "1.0.0",
                    "authority_class": "external_unverified",
                    "registry_hash": digest,
                    "registration_hash": digest,
                    "implementation_hash": digest,
                    "factory_origin": "unverified_or_injected",
                    "factory_hash": digest,
                    "provider_version": "unverified",
                    "descriptor_hash": digest,
                    "producer_schema_version": "ashare_pit_adapter_registration_service.v1",
                    "producer_policy_hash": digest,
                    "registration_artifact_hash": digest,
                    "source_watermark_event_hash": digest,
                    "artifact_refs": [
                        {
                            "relative_path": "registered-ashare-pit-adapter-v1/fixture.json",
                            "artifact_hash": digest,
                            "media_type": "application/vnd.vibe.registered-ashare-pit-adapter-v1+json",
                        }
                    ],
                },
                "AsharePITSnapshotRecorded": {
                    "snapshot_id": "ashare-pit-snapshot-v2-" + "d" * 24,
                    "snapshot_hash": digest,
                    "adapter_id": "fixture-pit-v1",
                    "adapter_registration_event_hash": digest,
                    "evaluation_policy_event_hash": digest,
                    "source_watermark_event_hash": digest,
                    "producer_schema_version": "ashare_pit_snapshot_service.v2",
                    "producer_policy_hash": digest,
                    "validation_policy_hash": digest,
                    "request_hash": digest,
                    "source_manifest_hash": digest,
                    "registry_hash": digest,
                    "registration_hash": digest,
                    "calendar_hash": digest,
                    "evaluation_time_policy_hash": digest,
                    "split_plan_hash": digest,
                    "pit_contract_status": "unavailable",
                    "survivorship_status": "controlled_by_daily_membership",
                    "cutoff_status": "within_registered_valid_end",
                    "decision_grade": False,
                    "hard_failures": [],
                    "caps": ["ADAPTER_AUTHORITY_UNVERIFIED"],
                    "warnings": [],
                    "artifact_refs": [
                        {
                            "relative_path": "ashare-pit-snapshot-v2/fixture.json",
                            "artifact_hash": digest,
                            "media_type": "application/vnd.vibe.ashare-pit-snapshot-v2+json",
                        }
                    ],
                },
            "RetrieverDecisionV4Recorded": {
                    "decision_id": "retriever-v4-1",
                    "decision_hash": digest,
                    "shadow_decision_hash": digest,
                    "input_bundle_hash": digest,
                    "control_evidence_event_hash": digest,
                    "control_evidence_hash": digest,
                    "control_policy_hash": digest,
                    "selected_factor_spec_ids": ["factor-1"],
                    "seed": 7,
                    "policy_version": "topology_activation_policy.v3",
                    "policy_hash": digest,
                    "policy_config": ActivationRetrieverPolicy().to_dict(),
                    "eligible_event_watermark": digest,
                    "data_snapshot_hash": digest,
                    "candidate_budget": 1,
                    "official_output_hash": digest,
                    "propensity_semantics": "sequential_softmax_draw_probability.v1",
                    "components": [
                        {
                            "factor_spec_id": "factor-1", "action_id": "action-1",
                            "motif": "Wrap:rank", "node_kind": "leaf",
                            "output_panel_hash": digest,
                            "semantic_model_id": "embedding-model",
                            "semantic_model_version": "1",
                            "semantic_embedding_hash": digest,
                            "cost_evidence_hash": digest, "valdiv": 0.5,
                            "semdiv": 0.5, "syndiv": 0.5,
                            "topology_score": 0.125, "base_score": 0.1,
                            "memory_adjustment": 0.0, "action_score": -2.3,
                            "confidence": 0.0, "selected": True,
                            "selection_propensity": 1.0, "warnings": [],
                            "veto_reason": None,
                        }
                    ],
                    "shadow_only": True,
                    "artifact_refs": [],
                },
            "RetrieverDecisionV5Recorded": {
                    "decision_id": "retriever-v5-1",
                    "decision_hash": digest,
                    "shadow_decision_hash": digest,
                    "input_bundle_hash": digest,
                    "control_evidence_event_hash": digest,
                    "control_evidence_hash": digest,
                    "control_policy_hash": digest,
                    "selected_action_ids": ["action-1"],
                    "selected_parent_factor_spec_ids": ["factor-1"],
                    "action_template_event_hashes": [digest],
                    "seed": 7,
                    "policy_version": "topology_activation_policy.v3",
                    "policy_hash": digest,
                    "policy_config": ActivationRetrieverPolicy().to_dict(),
                    "eligible_event_watermark": digest,
                    "data_snapshot_hash": digest,
                    "candidate_budget": 1,
                    "official_output_hash": digest,
                    "propensity_semantics": (
                        "sequential_action_softmax_draw_probability.v1"
                    ),
                    "components": [
                        {
                            "factor_spec_id": "factor-1", "action_id": "action-1",
                            "motif": "Wrap:rank", "node_kind": "leaf",
                            "output_panel_hash": digest,
                            "semantic_model_id": "embedding-model",
                            "semantic_model_version": "1",
                            "semantic_embedding_hash": digest,
                            "cost_evidence_hash": digest, "valdiv": 0.5,
                            "semdiv": 0.5, "syndiv": 0.5,
                            "topology_score": 0.125, "base_score": 0.1,
                            "memory_adjustment": 0.0, "action_score": -2.3,
                            "confidence": 0.0, "selected": True,
                            "selection_propensity": 1.0, "warnings": [],
                            "veto_reason": None,
                        }
                    ],
                    "shadow_only": True,
                        "artifact_refs": [],
                    },
            "RetrieverDecisionV6Recorded": {
                        "decision_id": "retriever-v6-1",
                        "decision_hash": digest,
                        "shadow_decision_hash": digest,
                        "input_bundle_hash": digest,
                        "control_evidence_event_hash": digest,
                        "control_evidence_hash": digest,
                        "control_policy_hash": digest,
                        "feature_source_event_hash": digest,
                        "feature_source_hash": digest,
                        "selected_action_ids": ["action-1"],
                        "selected_parent_factor_spec_ids": ["factor-1"],
                        "action_template_event_hashes": [digest],
                        "seed": 7,
                        "policy_version": "topology_activation_policy.v3",
                        "policy_hash": digest,
                        "policy_config": ActivationRetrieverPolicy().to_dict(),
                        "eligible_event_watermark": digest,
                        "data_snapshot_hash": digest,
                        "candidate_budget": 1,
                        "official_output_hash": digest,
                        "propensity_semantics": (
                            "sequential_action_softmax_draw_probability.v1"
                        ),
                        "components": [
                            {
                                "factor_spec_id": "factor-1", "action_id": "action-1",
                                "motif": "Wrap:rank", "node_kind": "leaf",
                                "output_panel_hash": digest,
                                "semantic_model_id": "embedding-model",
                                "semantic_model_version": "1",
                                "semantic_embedding_hash": digest,
                                "cost_evidence_hash": digest, "valdiv": 0.5,
                                "semdiv": 0.5, "syndiv": 0.5,
                                "topology_score": 0.125, "base_score": 0.1,
                                "memory_adjustment": 0.0, "action_score": -2.3,
                                "confidence": 0.0, "selected": True,
                                "selection_propensity": 1.0, "warnings": [],
                                "veto_reason": None,
                            }
                        ],
                        "shadow_only": True,
                        "artifact_refs": [],
                    },
            "RetrieverDecisionV7Recorded": {
                        "decision_id": "retriever-v7-1",
                        "decision_hash": digest,
                        "shadow_decision_hash": digest,
                        "input_bundle_hash": digest,
                        "plan_hash": digest,
                        "pair_id": "group-1:momentum:leaf",
                        "schedule_event_hash": digest,
                        "schedule_hash": digest,
                        "feature_source_event_hash": digest,
                        "feature_source_hash": digest,
                        "selected_action_ids": ["action-1"],
                        "selected_parent_factor_spec_ids": ["factor-1"],
                        "action_template_event_hashes": [digest],
                        "seed": 7,
                        "policy_version": "topology_activation_policy.v3",
                        "policy_hash": digest,
                        "policy_config": ActivationRetrieverPolicy().to_dict(),
                        "eligible_event_watermark": digest,
                        "data_snapshot_hash": digest,
                        "candidate_budget": 1,
                        "official_output_hash": digest,
                        "propensity_semantics": (
                            "sequential_action_softmax_draw_probability.v1"
                        ),
                        "components": [
                            {
                                "factor_spec_id": "factor-1", "action_id": "action-1",
                                "motif": "Wrap:rank", "node_kind": "leaf",
                                "output_panel_hash": digest,
                                "semantic_model_id": "embedding-model",
                                "semantic_model_version": "1",
                                "semantic_embedding_hash": digest,
                                "cost_evidence_hash": digest, "valdiv": 0.5,
                                "semdiv": 0.5, "syndiv": 0.5,
                                "topology_score": 0.125, "base_score": 0.1,
                                "memory_adjustment": 0.0, "action_score": -2.3,
                                "confidence": 0.0, "selected": True,
                                "selection_propensity": 1.0, "warnings": [],
                                "veto_reason": None,
                            }
                        ],
                        "shadow_only": True,
                        "artifact_refs": [],
                    },
            "ActivationPlanRegistered": {
                "experiment_id": "activation-1",
                "plan_hash": digest,
                "phase": "confirmatory",
                "registered_at": timestamp,
                "artifact_refs": [],
            },
        "ActivationRunRecorded": {
                "manifest_id": "activation-run-1",
                "plan_hash": digest,
                "manifest_hash": digest,
                "pair_id": "pair-1",
                "run_group_id": "group-1",
                "arm": "control",
                "terminal_status_counts": {
                    "success": 1,
                    "reject": 0,
                    "skip": 0,
                    "invalid": 0,
                    "duplicate": 0,
                    "timeout": 0,
                    "error": 0,
                    "infrastructure_failure": 0,
                },
                "complete": True,
                "contaminated": False,
                "artifact_refs": [],
            },
            "ActivationResultRecorded": {
                "result_id": "activation-result-1",
                "plan_hash": digest,
                "result_hash": digest,
                "complete_pairs": 0,
                "invalidation_reasons": ["PREFLIGHT_FAILED"],
                "replayable": False,
                "artifact_refs": [],
            },
            "RetrieverActivationDecisionRecorded": {
                "activation_decision_id": "activation-decision-1",
                "plan_hash": digest,
                "result_hash": digest,
                "decision_hash": digest,
                "policy_hash": digest,
                "verdict": "invalidated",
                "reasons": ["PREFLIGHT_FAILED"],
                "active_research_only": False,
                "artifact_refs": [],
            },
        "FalsificationContractRegistered": {
            "contract_id": "contract-1",
            "contract_hash": digest,
            "factor_spec_id": "factor-1",
            "registered_at": timestamp,
            "data_access_cutoff": timestamp,
            "policy_hash": digest,
        },
        "SequentialProtocolRegistered": {
            "protocol_id": "protocol-1",
            "protocol_hash": digest,
            "contract_id": "contract-1",
            "contract_hash": digest,
            "factor_spec_id": "factor-1",
            "method": "bounded_mean_mixture_e.v1",
            "maximum_looks": 2,
            "stopping_rule": "e_process_boundary_or_max_looks.v1",
            "data_scope": "valid",
            "family_alpha": 0.05,
            "support_alpha": 0.025,
            "contradiction_alpha": 0.025,
            "lambda_grid": [0.1, 0.2],
            "mixture_weights": [0.5, 0.5],
            "support_log_boundary": 3.6888794541139363,
            "contradiction_log_boundary": 3.6888794541139363,
            "filtration_hash": digest,
            "block_schedule_hash": digest,
            "policy_hash": digest,
            "registered_at": timestamp,
        },
        "SequentialLookRecorded": {
            "look_id": "look-1",
            "protocol_id": "protocol-1",
            "protocol_hash": digest,
            "factor_spec_id": "factor-1",
            "look_index": 1,
            "information_time": 2,
            "block_id": "block-1",
            "block_hash": digest,
            "unit_hashes": ["sha256:" + "a" * 64, "sha256:" + "b" * 64],
            "incremental_information": 2,
            "support_component_log_capitals": [0.1, 0.2],
            "contradiction_component_log_capitals": [-0.1, -0.2],
            "cumulative_support_log_e": 0.2,
            "cumulative_contradiction_log_e": -0.1,
            "support_log_boundary": 2.0,
            "contradiction_log_boundary": 2.0,
            "status": "continue",
            "stop_reason": "NONE",
            "previous_look_event_hash": None,
        },
        "OutcomeDataAccessed": {
            "access_id": "access-1",
            "factor_spec_id": "factor-1",
            "data_scope": "valid",
            "accessed_at": timestamp,
        },
        "FalsificationResultRecorded": {
            "result_id": "result-1",
            "contract_id": "contract-1",
            "contract_hash": digest,
            "outcome": "inconclusive",
            "artifact_refs": [],
        },
            "OfficialSearchControlRecorded": {
            "control_id": "official-control-1",
            "evidence_hash": digest,
            "policy_hash": digest,
            "output_hash": digest,
            "search_run_id": "control-run-1",
            "data_snapshot_hash": digest,
            "candidate_count": 1,
            "terminal_event_hashes": [digest],
            "artifact_refs": [],
        },
            "PreArmFlatScheduleFrozen": {
            "schedule_id": "prearm-flat-v1-" + "d" * 24,
            "schedule_hash": digest,
            "plan_hash": digest,
            "pair_id": "group-1:momentum:leaf",
            "run_group_id": "group-1",
            "data_snapshot_hash": digest,
            "policy_hash": digest,
            "candidate_count": 1,
            "output_hash": digest,
            "artifact_refs": [],
        },
        "ActivationRunSourceAudited": {
            "audit_id": "activation-source-v2-1",
            "plan_hash": digest,
            "summary_manifest_hash": digest,
            "source_watermark_event_hash": digest,
            "audit_hash": digest,
            "retriever_decision_event_hashes": [],
            "terminal_event_hashes": [],
            "evaluation_event_hashes": [],
            "quality_decision_event_hashes": [],
            "source_failure_codes": [
                "QUALITY_DECISION_UPSTREAM_AUTHORITY_UNPROVEN",
                "RESOURCE_METRICS_SOURCE_UNBOUND",
            ],
            "source_complete": False,
            "artifact_refs": [],
        },
            "ActivationResourceMeasured": {
                "resource_id": "activation-resource-v1-" + "d" * 24,
            "plan_hash": digest,
            "pair_id": "pair-1",
            "run_group_id": "group-1",
            "arm": "control",
            "manifest_hash": digest,
            "measurement_policy_hash": digest,
            "wall_seconds": 1.0,
            "cpu_seconds": 0.5,
            "peak_rss_mb": None,
                "peak_rss_method": "unavailable_without_isolated_worker.v1",
                "source_failure_codes": [
                    "ARM_ORDER_NOT_COUNTERBALANCED",
                    "EXECUTOR_TIMEOUT_NOT_ENFORCED",
                    "PEAK_RSS_ISOLATED_MEASUREMENT_UNAVAILABLE"
                ],
            "source_complete": False,
            "evidence_hash": digest,
                "artifact_refs": [],
            },
            "ActivationResourceMeasuredV2": {
                "resource_id": "activation-resource-v2-" + "d" * 24,
                "plan_hash": digest, "pair_id": "group-1:momentum:leaf",
                "run_group_id": "group-1", "arm": "control",
                "manifest_hash": digest, "pair_schedule_event_hash": digest,
                "pair_schedule_hash": digest, "execution_claim_event_hash": digest,
                "arm_order_position": 0, "timeout_limit_seconds": 1.0,
                "measurement_policy_hash": digest, "wall_seconds": 0.0,
                "cpu_seconds": 0.0, "peak_rss_mb": None,
                "peak_rss_method": "unavailable_without_isolated_worker.v1",
                "source_failure_codes": [
                    "EXECUTOR_TIMEOUT_NOT_ENFORCED",
                    "PEAK_RSS_ISOLATED_MEASUREMENT_UNAVAILABLE",
                ],
                "source_complete": False, "evidence_hash": digest,
                "artifact_refs": [],
            },
            "ActivationPairExecutionScheduled": {
                "schedule_id": "activation-pair-schedule-v1-" + "d" * 24,
                "schedule_hash": digest, "plan_hash": digest,
                "pair_id": "group-1:momentum:leaf", "run_group_id": "group-1",
                "mechanism_family": "momentum", "dag_region": "leaf",
                "seed": 1, "arm_order": ["control", "treatment"],
                "order_rule": "alternating_frozen_run_group_index.v1",
                "candidate_budget": 1, "compute_budget": 1,
                "worker_limit": 1, "timeout_seconds": 1.0,
                "artifact_refs": [],
            },
            "ActivationPairExecutionClaimed": {
                "claim_id": "activation-pair-claim-v1-" + "d" * 24,
                "schedule_event_hash": digest, "schedule_hash": digest,
                "plan_hash": digest, "pair_id": "group-1:momentum:leaf",
                "run_group_id": "group-1",
            },
            "ActivationGenerationConsumptionRecorded": {
                "generation_id": "activation-generation-v1-" + "d" * 24,
                "plan_hash": digest,
                "pair_id": "pair-1",
                "run_group_id": "group-1",
                "mechanism_family": "momentum",
                "dag_region": "leaf",
                "execution_run_id": "activation-arm-fixture",
                "retriever_decision_event_hash": digest,
                "retriever_decision_hash": digest,
                "control_evidence_event_hash": digest,
                "generator_policy_hash": digest,
                "selected_parent_factor_spec_ids": ["factor-1"],
                "consumed_parent_factor_spec_ids": ["factor-1"],
                "generated_candidate_count": 1,
                "candidate_budget": 2,
                "compute_budget": 2,
                "source_failure_codes": ["FIXED_CANDIDATE_BUDGET_INCOMPLETE"],
                "source_complete": False,
                "evidence_hash": digest,
                "artifact_refs": [],
            },
                "ActivationGenerationConsumptionV2Recorded": {
                "generation_id": "activation-generation-v2-" + "d" * 24,
                "plan_hash": digest,
                "pair_id": "pair-1",
                "run_group_id": "group-1",
                "mechanism_family": "momentum",
                "dag_region": "leaf",
                "execution_run_id": "activation-arm-fixture",
                "retriever_decision_event_hash": digest,
                "retriever_decision_hash": digest,
                "control_evidence_event_hash": digest,
                "generator_policy_hash": digest,
                "selected_action_ids": ["action-1"],
                "selected_action_event_hashes": [digest],
                "selected_parent_factor_spec_ids": ["factor-1"],
                "consumed_action_ids": ["action-1"],
                "consumed_parent_factor_spec_ids": ["factor-1"],
                "generated_candidate_count": 1,
                "candidate_budget": 2,
                "compute_budget": 2,
                "source_failure_codes": ["RETRIEVER_FEATURE_SOURCE_UNVERIFIED"],
                "source_complete": False,
                "evidence_hash": digest,
                "artifact_refs": [],
            },
            "ActivationGenerationConsumptionV3Recorded": {
                "generation_id": "activation-generation-v3-" + "d" * 24,
                "plan_hash": digest,
                "pair_id": "pair-1",
                "run_group_id": "group-1",
                "mechanism_family": "momentum",
                "dag_region": "leaf",
                "execution_run_id": "activation-arm-fixture",
                "retriever_decision_event_hash": digest,
                "retriever_decision_hash": digest,
                "control_evidence_event_hash": digest,
                "retriever_input_bundle_hash": digest,
                "feature_source_event_hash": digest,
                "feature_source_hash": digest,
                "feature_snapshot_event_hash": digest,
                "feature_snapshot_hash": digest,
                "feature_policy_hash": digest,
                "feature_scorecard_event_hashes": [digest],
                "generator_policy_hash": digest,
                "selected_action_ids": ["action-1"],
                "selected_action_event_hashes": [digest],
                "selected_parent_factor_spec_ids": ["factor-1"],
                "consumed_action_ids": ["action-1"],
                "consumed_parent_factor_spec_ids": ["factor-1"],
                "generated_candidate_count": 1,
                "candidate_budget": 1,
                "compute_budget": 1,
                "source_failure_codes": [],
                "source_complete": True,
                "evidence_hash": digest,
                "artifact_refs": [],
            },
            "ActivationGenerationConsumptionV4Recorded": {
                "generation_id": "activation-generation-v4-" + "d" * 24,
                "plan_hash": digest, "pair_id": "pair-1",
                "run_group_id": "group-1", "mechanism_family": "momentum",
                "dag_region": "leaf", "execution_run_id": "activation-arm-fixture",
                "retriever_decision_event_hash": digest,
                "retriever_decision_hash": digest,
                "retriever_input_bundle_hash": digest,
                "schedule_event_hash": digest, "schedule_hash": digest,
                "feature_source_event_hash": digest, "feature_source_hash": digest,
                "feature_snapshot_event_hash": digest,
                "feature_snapshot_hash": digest, "feature_policy_hash": digest,
                "feature_scorecard_event_hashes": [digest],
                "generator_policy_hash": digest,
                "selected_action_ids": ["action-1"],
                "selected_action_event_hashes": [digest],
                "selected_parent_factor_spec_ids": ["factor-1"],
                "consumed_action_ids": ["action-1"],
                "consumed_parent_factor_spec_ids": ["factor-1"],
                "generated_candidate_count": 1,
                "candidate_budget": 1, "compute_budget": 1,
                "source_failure_codes": [], "source_complete": True,
                "evidence_hash": digest, "artifact_refs": [],
            },
        "MechanismEvidenceIndexRecorded": {
            "mei_id": "mei-1",
            "factor_spec_id": "factor-1",
            "mei_hash": digest,
            "mei_schema_version": "mechanism_evidence_index.v1",
            "truth_table_version": "mechanism_evidence_truth_table.v1",
            "policy_version": "mei-policy.v1",
            "policy_hash": digest,
            "source_result_hashes": [digest],
            "source_event_hashes": [digest],
            "decisive_event_hashes": [digest],
            "advisory_event_hashes": [],
            "ordinal_state": "inconclusive",
            "reason_codes": ["DECISIVE_TEST_UNAVAILABLE"],
            "warning_codes": ["LOW_POWER"],
            "limitation_codes": ["VALIDATION_ONLY"],
        },
        "ComplementEvidenceRecorded": {
            "complement_id": "complement-1",
            "factor_spec_id": "factor-1",
            "complement_hash": digest,
            "policy_version": "complement-policy.v2",
            "policy_hash": digest,
            "data_scope": "valid",
            "snapshot_hash": digest,
            "source_evaluation_event_hash": digest,
            "source_terminal_event_hash": digest,
            "pool_factor_spec_ids": ["pool-factor-1"],
            "identity_hash": digest,
            "residual_hash": digest,
            "portfolio_hash": digest,
            "portfolio_construction_hash": digest,
            "cost_model_hash": digest,
            "capacity_model_hash": digest,
            "exposure_model_hash": digest,
            "status": "complementary",
            "cap": None,
            "artifact_refs": [],
        },
        "QualityDecisionRecorded": {
            "decision_id": "quality-1",
            "factor_spec_id": "factor-1",
            "decision": "research_only",
            "policy_hash": digest,
            "evidence_hashes": [digest],
            "reasons": ["EVIDENCE_BOUNDED"],
            "warnings": ["LIMITED_SAMPLE"],
            "caps": ["MISSING_EXECUTION"],
            "limitations": ["research evidence only"],
        },
            "DecisionEvidenceV3Recorded": {
            "evidence_id": "decision-evidence-v3-fixture",
            "evidence_hash": digest,
            "evidence_kind": "ledger",
            "factor_spec_id": "factor-1",
            "evidence_run_id": "run-1",
            "producer_schema_version": "decision_ledger_evidence_service.v3",
            "producer_policy_hash": digest,
            "source_event_hashes": [digest],
            "source_artifact_hashes": [digest],
            "evidence_payload_hash": digest,
            "factor_definition_event_hash": digest,
            "evaluation_event_hash": digest,
            "terminal_event_hash": digest,
            "ledger_watermark_event_hash": digest,
            "artifact_refs": [
                {
                    "relative_path": "decision-evidence-v3/fixture.json",
                    "artifact_hash": digest,
                    "media_type": "application/vnd.vibe.decision-evidence-v3+json",
                }
                ],
            },
                "ScorecardDecisionEvidenceV3Recorded": {
                "evidence_id": "scorecard-decision-evidence-v3-fixture",
                "evidence_hash": digest,
                "evidence_kind": "scorecard",
                "factor_spec_id": "factor-1",
                "evidence_run_id": "run-1",
                "trial_id": "trial-1",
                "producer_schema_version": "decision_scorecard_evidence_service.v3",
                "producer_policy_hash": digest,
                "source_event_hashes": [digest],
                "source_artifact_hashes": [digest],
                "evidence_payload_hash": digest,
                "factor_definition_event_hash": digest,
                "evaluation_policy_event_hash": digest,
                "snapshot_event_hash": digest,
                "source_watermark_event_hash": digest,
                "scorecard_hash": digest,
                "factor_output_content_hash": digest,
                "decision_grade": False,
                "caps": ["PIT_SNAPSHOT_PROVENANCE_UNVERIFIED"],
                "artifact_refs": [
                    {
                        "relative_path": "decision-evidence-v3/scorecard.json",
                        "artifact_hash": digest,
                        "media_type": "application/vnd.vibe.decision-evidence-v3+json",
                    }
                    ],
                },
                "SnapshotDecisionEvidenceV3Recorded": {
                    "evidence_id": "snapshot-decision-evidence-v3-fixture",
                    "evidence_hash": digest,
                    "evidence_kind": "snapshot",
                    "factor_spec_id": "factor-1",
                    "evidence_run_id": "run-1",
                    "producer_schema_version": "decision_snapshot_evidence_service.v3",
                    "producer_policy_hash": digest,
                    "source_event_hashes": [digest],
                    "source_artifact_hashes": [digest],
                    "evidence_payload_hash": digest,
                    "factor_definition_event_hash": digest,
                    "evaluation_policy_event_hash": digest,
                    "snapshot_event_hash": digest,
                    "source_watermark_event_hash": digest,
                    "snapshot_hash": digest,
                    "panel_content_hash": digest,
                    "cutoff_status": "contains_dates_after_registered_valid_end",
                    "pit_authority_status": "unverified_legacy_caller_snapshot",
                    "survivorship_status": "unknown",
                    "decision_grade": False,
                    "caps": ["PIT_SNAPSHOT_PROVENANCE_UNVERIFIED"],
                    "artifact_refs": [
                        {
                            "relative_path": "decision-evidence-v3/snapshot.json",
                            "artifact_hash": digest,
                            "media_type": "application/vnd.vibe.decision-evidence-v3+json",
                        }
                    ],
                },
            "QualityDecisionV2Recorded": {
            "decision_id": "quality-v2-1",
            "factor_spec_id": "factor-1",
            "decision_hash": digest,
            "decision": "candidate_zoo",
            "tier": 2,
            "policy_version": "decision-v2-policy.1",
            "policy_hash": digest,
            "scorecard_hash": digest,
            "execution_hash": digest,
            "snapshot_hash": digest,
            "ledger_watermark_hash": digest,
            "mechanism_evidence_hash": digest,
            "complement_evidence_hash": digest,
            "final_test_artifact_hash": None,
            "forward_plan_hash": None,
            "evidence_hashes": [digest],
            "reasons": ["TERMINAL_TRAIN_VALID_EVIDENCE_QUALIFIED"],
            "warnings": [],
            "caps": [],
            "limitations": ["TRAIN_VALID_ONLY"],
            "within_tier_score": 0.5,
            "forward_success_claim": False,
            "artifact_refs": [],
        },
        "QualityDecisionV3Recorded": {
            "decision_id": "quality-v3-1",
            "decision_hash": digest,
            "quality_decision_hash": digest,
            "input_bundle_hash": digest,
            "factor_spec_id": "factor-1",
            "decision": "candidate_zoo",
            "tier": 2,
            "policy_version": "decision-v2-policy.1",
            "policy_hash": digest,
            "evidence_hashes": [digest],
            "reasons": ["TERMINAL_TRAIN_VALID_EVIDENCE_QUALIFIED"],
            "warnings": [],
            "caps": [],
            "limitations": ["TRAIN_VALID_ONLY"],
            "within_tier_score": 0.5,
            "forward_success_claim": False,
            "artifact_refs": [],
        },
        "FinalCandidateFrozen": {
            "freeze_id": "final-freeze-1",
            "candidate_schema_version": "frozen_final_candidate.v1",
            "factor_spec_id": "factor-1",
            "definition_hash": digest,
            "transform_pipeline_hash": digest,
            "cost_model_hash": digest,
            "regime_config_hash": digest,
            "policy_hash": digest,
            "data_snapshot_hash": digest,
            "frozen_at": timestamp,
            "candidate_hash": digest,
        },
        "FinalTestCapabilityIssued": {
            "capability_id": "final-capability-1",
            "capability_fingerprint": digest,
            "candidate_hash": digest,
            "factor_spec_id": "factor-1",
            "declared_run_id": "run-final",
            "data_snapshot_hash": digest,
            "period_start": "2025-01-01",
            "period_end": "2025-06-30",
            "allowed_fields": ["net_returns", "rank_ic_series"],
            "issued_at": timestamp,
        },
        "FinalTestAccessRecorded": {
            "access_id": "final-access-1",
            "capability_fingerprint": digest,
            "candidate_hash": digest,
            "factor_spec_id": "factor-1",
            "declared_run_id": "run-final",
            "request_hash": digest,
            "outcome": "allowed",
            "reason_code": "FINAL_ACCESS_ALLOWED_ONCE",
            "contaminated": False,
            "accessed_at": timestamp,
        },
        "FinalTestArtifactRecorded": {
            "artifact_id": "final-artifact-1",
            "artifact_hash": digest,
            "factor_spec_id": "factor-1",
            "candidate_hash": digest,
            "definition_hash": digest,
            "transform_pipeline_hash": digest,
            "cost_model_hash": digest,
            "regime_config_hash": digest,
            "access_event_hash": digest,
            "policy_hash": digest,
            "data_snapshot_hash": digest,
            "effective_observations": 20,
            "quality_passed": True,
            "contaminated": False,
            "artifact_refs": [],
        },
        "ForwardPlanV2Recorded": {
            "plan_schema_version": "frozen_forward_plan.v2",
            "plan_id": "forward-plan-placeholder",
            "factor_spec_id": "factor-1",
            "final_test_artifact_hash": digest,
            "definition_hash": digest,
            "transform_pipeline_hash": digest,
            "cost_model_hash": digest,
            "regime_config_hash": digest,
            "policy_hash": digest,
            "expected_horizon": 5,
            "minimum_effective_observations": 20,
            "minimum_rank_ic": -0.01,
            "maximum_drawdown": 0.20,
            "kill_rules_hash": digest,
            "created_at": timestamp,
            "plan_hash": digest,
            "artifact_refs": [],
        },
        "ForwardObservationV2Recorded": {
            "observation_schema_version": "forward_observation.v2",
            "observation_id": "forward-observation-1",
            "plan_id": "forward-plan-placeholder",
            "plan_hash": digest,
            "period_start": "2025-07-01",
            "period_end": "2025-07-31",
            "effective_observations": 5,
            "rank_ic": 0.02,
            "net_return": 0.01,
            "drawdown": 0.03,
            "previous_observation_hash": None,
            "observed_at": timestamp,
            "observation_hash": digest,
            "artifact_refs": [],
        },
        "ForwardPlanRecorded": {
            "plan_id": "plan-1",
            "factor_spec_id": "factor-1",
            "plan_hash": digest,
            "minimum_observations": 12,
            "policy_hash": digest,
        },
        "ForwardObservationRecorded": {
            "observation_id": "observation-1",
            "plan_id": "plan-1",
            "period_start": timestamp,
            "period_end": timestamp,
            "observation_hash": digest,
            "previous_observation_hash": None,
            "artifact_refs": [],
        },
    }

    frozen = samples["FinalCandidateFrozen"]
    frozen["candidate_hash"] = canonical_json_hash(
        {
            "schema_version": frozen["candidate_schema_version"],
            "factor_spec_id": frozen["factor_spec_id"],
            "definition_hash": frozen["definition_hash"],
            "transform_pipeline_hash": frozen["transform_pipeline_hash"],
            "cost_model_hash": frozen["cost_model_hash"],
            "regime_config_hash": frozen["regime_config_hash"],
            "policy_hash": frozen["policy_hash"],
            "data_snapshot_hash": frozen["data_snapshot_hash"],
            "frozen_at": frozen["frozen_at"],
        }
    )
    samples["FinalTestCapabilityIssued"]["candidate_hash"] = frozen["candidate_hash"]
    samples["FinalTestAccessRecorded"]["candidate_hash"] = frozen["candidate_hash"]
    samples["FinalTestArtifactRecorded"]["candidate_hash"] = frozen["candidate_hash"]
    forward_plan = samples["ForwardPlanV2Recorded"]
    forward_plan["plan_hash"] = canonical_json_hash(
        {
            "schema_version": forward_plan["plan_schema_version"],
            "factor_spec_id": forward_plan["factor_spec_id"],
            "final_test_artifact_hash": forward_plan["final_test_artifact_hash"],
            "definition_hash": forward_plan["definition_hash"],
            "transform_pipeline_hash": forward_plan["transform_pipeline_hash"],
            "cost_model_hash": forward_plan["cost_model_hash"],
            "regime_config_hash": forward_plan["regime_config_hash"],
            "policy_hash": forward_plan["policy_hash"],
            "expected_horizon": forward_plan["expected_horizon"],
            "minimum_effective_observations": forward_plan["minimum_effective_observations"],
            "minimum_rank_ic": forward_plan["minimum_rank_ic"],
            "maximum_drawdown": forward_plan["maximum_drawdown"],
            "kill_rules_hash": forward_plan["kill_rules_hash"],
            "created_at": forward_plan["created_at"],
        }
    )
    forward_plan["plan_id"] = "forward-plan-" + str(forward_plan["plan_hash"]).removeprefix(
        "sha256:"
    )[:24]
    forward_observation = samples["ForwardObservationV2Recorded"]
    forward_observation["plan_id"] = forward_plan["plan_id"]
    forward_observation["plan_hash"] = forward_plan["plan_hash"]
    forward_observation["observation_hash"] = canonical_json_hash(
        {
            "schema_version": forward_observation["observation_schema_version"],
            "observation_id": forward_observation["observation_id"],
            "plan_id": forward_observation["plan_id"],
            "plan_hash": forward_observation["plan_hash"],
            "period_start": forward_observation["period_start"],
            "period_end": forward_observation["period_end"],
            "effective_observations": forward_observation["effective_observations"],
            "rank_ic": forward_observation["rank_ic"],
            "net_return": forward_observation["net_return"],
            "drawdown": forward_observation["drawdown"],
            "previous_observation_hash": forward_observation["previous_observation_hash"],
            "observed_at": forward_observation["observed_at"],
        }
    )

    decision_v2 = samples["QualityDecisionV2Recorded"]
    decision_v2["decision_hash"] = canonical_json_hash(
        {
            "schema_version": "alpha_quality_decision.v2",
            "factor_spec_id": decision_v2["factor_spec_id"],
            "decision": decision_v2["decision"],
            "tier": decision_v2["tier"],
            "policy_version": decision_v2["policy_version"],
            "policy_hash": decision_v2["policy_hash"],
            "evidence_hashes": decision_v2["evidence_hashes"],
            "reasons": decision_v2["reasons"],
            "warnings": decision_v2["warnings"],
            "caps": decision_v2["caps"],
            "limitations": decision_v2["limitations"],
            "within_tier_score": decision_v2["within_tier_score"],
            "forward_success_claim": decision_v2["forward_success_claim"],
        }
    )
    decision_v3 = samples["QualityDecisionV3Recorded"]
    decision_v3["decision_hash"] = canonical_json_hash(
        {
            "schema_version": "quality_decision_source_bound.v3",
            "quality_decision_hash": decision_v3["quality_decision_hash"],
            "input_bundle_hash": decision_v3["input_bundle_hash"],
            "factor_spec_id": decision_v3["factor_spec_id"],
            "decision": decision_v3["decision"],
            "tier": decision_v3["tier"],
            "policy_version": decision_v3["policy_version"],
            "policy_hash": decision_v3["policy_hash"],
            "evidence_hashes": decision_v3["evidence_hashes"],
            "reasons": decision_v3["reasons"],
            "warnings": decision_v3["warnings"],
            "caps": decision_v3["caps"],
            "limitations": decision_v3["limitations"],
            "within_tier_score": decision_v3["within_tier_score"],
            "forward_success_claim": decision_v3["forward_success_claim"],
        }
    )

    retriever_v2 = samples["RetrieverDecisionV2Recorded"]
    retriever_v2["policy_hash"] = canonical_json_hash(retriever_v2["policy_config"])
    retriever_v2["decision_hash"] = canonical_json_hash(
        {
            "schema_version": "retriever_shadow_decision.v2",
            "selected_factor_spec_ids": retriever_v2["selected_factor_spec_ids"],
            "seed": retriever_v2["seed"],
            "policy_version": retriever_v2["policy_version"],
            "policy_hash": retriever_v2["policy_hash"],
            "policy_config": retriever_v2["policy_config"],
            "eligible_event_watermark": retriever_v2["eligible_event_watermark"],
            "data_snapshot_hash": retriever_v2["data_snapshot_hash"],
            "candidate_budget": retriever_v2["candidate_budget"],
            "official_output_hash": retriever_v2["official_output_hash"],
            "propensity_semantics": retriever_v2["propensity_semantics"],
            "components": retriever_v2["components"],
            "shadow_only": retriever_v2["shadow_only"],
        }
    )
    retriever_v3 = samples["RetrieverDecisionV3Recorded"]
    retriever_v3["policy_hash"] = canonical_json_hash(retriever_v3["policy_config"])
    retriever_v3["decision_hash"] = canonical_json_hash(
        {
            "schema_version": "retriever_source_bound_decision.v3",
            **{
                key: value
                for key, value in retriever_v3.items()
                if key not in {"decision_id", "decision_hash", "artifact_refs"}
            },
        }
    )
    retriever_v4 = samples["RetrieverDecisionV4Recorded"]
    retriever_v4["policy_hash"] = canonical_json_hash(retriever_v4["policy_config"])
    retriever_v4["decision_hash"] = canonical_json_hash(
        {
            "schema_version": "retriever_source_bound_decision.v4",
            **{
                key: value
                for key, value in retriever_v4.items()
                if key not in {"decision_id", "decision_hash", "artifact_refs"}
            },
        }
    )
    retriever_v4["decision_id"] = (
        "retriever-v4-"
        + retriever_v4["decision_hash"].removeprefix("sha256:")[:20]
    )
    retriever_v5 = samples["RetrieverDecisionV5Recorded"]
    retriever_v5["policy_hash"] = canonical_json_hash(retriever_v5["policy_config"])
    retriever_v5["decision_hash"] = canonical_json_hash(
        {
            "schema_version": "retriever_action_source_bound_decision.v5",
            **{
                key: value
                for key, value in retriever_v5.items()
                if key not in {"decision_id", "decision_hash", "artifact_refs"}
            },
        }
    )
    retriever_v5["decision_id"] = (
        "retriever-v5-"
        + retriever_v5["decision_hash"].removeprefix("sha256:")[:20]
    )
    retriever_v6 = samples["RetrieverDecisionV6Recorded"]
    retriever_v6["policy_hash"] = canonical_json_hash(retriever_v6["policy_config"])
    retriever_v6["decision_hash"] = canonical_json_hash(
        {
            "schema_version": "retriever_action_source_bound_decision.v6",
            **{
                key: value
                for key, value in retriever_v6.items()
                if key not in {"decision_id", "decision_hash", "artifact_refs"}
            },
        }
    )
    retriever_v6["decision_id"] = (
        "retriever-v6-"
        + retriever_v6["decision_hash"].removeprefix("sha256:")[:20]
    )
    retriever_v7 = samples["RetrieverDecisionV7Recorded"]
    retriever_v7["policy_hash"] = canonical_json_hash(retriever_v7["policy_config"])
    retriever_v7["decision_hash"] = canonical_json_hash(
        {
            "schema_version": "retriever_action_schedule_bound_decision.v7",
            **{
                key: value for key, value in retriever_v7.items()
                if key not in {"decision_id", "decision_hash", "artifact_refs"}
            },
        }
    )
    retriever_v7["decision_id"] = (
        "retriever-v7-"
        + retriever_v7["decision_hash"].removeprefix("sha256:")[:20]
    )

    for event_type, spec in PAYLOAD_SPECS.items():
        validated = validate_and_redact_payload(event_type, spec.version, samples[event_type])
        assert set(validated) == set(spec.fields)


def test_non_finite_values_rejected_before_redaction_and_secrets_paths_redacted(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    bad = _factor_payload()
    bad["metadata"] = {"metric": math.nan}

    with pytest.raises(EventValidationError, match="non-finite"):
        store.append_event(_draft(payload=bad))

    payload = _factor_payload()
    payload["metadata"] = {
        "api_key": "sk-super-secret",
        "nested": {
            "cache_path": r"C:\private\factor.parquet",
            "account_id": "private-account-123",
            "environment": {"HOME": "/home/private-user"},
            "message": "/home/private-user/factor.parquet",
            "safe": "kept",
        },
    }
    event = store.append_event(_draft(payload=payload))
    encoded = json.dumps(event.to_dict(), sort_keys=True)

    assert "sk-super-secret" not in encoded
    assert r"C:\private" not in encoded
    assert "private-account-123" not in encoded
    assert "/home/private-user" not in encoded
    assert event.payload["metadata"]["api_key"] == "[redacted]"
    assert event.payload["metadata"]["nested"]["safe"] == "kept"


def test_secret_or_absolute_path_in_envelope_identity_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(EventValidationError, match="entity_id contains secret or local path"):
        store.append_event(_draft(entity_id="sk-super-secret-value"))
    with pytest.raises(EventValidationError, match="idempotency_key contains secret or local path"):
        store.append_event(_draft(idempotency_key=r"C:\private\request.key"))


def test_artifact_reference_requires_containment_existence_and_content_hash(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    artifact = tmp_path / "artifacts" / "panels" / "factor.bin"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"factor-panel")
    payload = _factor_payload()
    payload["artifact_refs"] = [
        {
            "relative_path": "panels/factor.bin",
            "artifact_hash": ResearchEventStore.hash_artifact(artifact),
            "media_type": "application/octet-stream",
        }
    ]

    event = store.append_event(_draft(payload=payload))

    assert event.payload["artifact_refs"][0]["relative_path"] == "panels/factor.bin"
    wrong_hash = _factor_payload()
    wrong_hash["artifact_refs"] = [
        {
            "relative_path": "panels/factor.bin",
            "artifact_hash": "sha256:" + "0" * 64,
            "media_type": "application/octet-stream",
        }
    ]
    with pytest.raises(ArtifactReferenceError, match="hash mismatch"):
        store.append_event(_draft(payload=wrong_hash))
    traversal = _factor_payload()
    traversal["artifact_refs"] = [
        {
            "relative_path": "../outside.bin",
            "artifact_hash": "sha256:" + "0" * 64,
            "media_type": "application/octet-stream",
        }
    ]
    with pytest.raises(ArtifactReferenceError, match="relative artifact path"):
        store.append_event(_draft(payload=traversal))
    encoded = _factor_payload()
    encoded["artifact_refs"] = [
        {
            "relative_path": "%2e%2e/outside.bin",
            "artifact_hash": "sha256:" + "0" * 64,
            "media_type": "application/octet-stream",
        }
    ]
    with pytest.raises(ArtifactReferenceError, match="encoded relative artifact path"):
        store.append_event(_draft(payload=encoded))


@pytest.mark.parametrize(
    "terminal_status",
    [
        "success",
        "reject",
        "skip",
        "invalid",
        "duplicate",
        "timeout",
        "error",
        "infrastructure_failure",
    ],
)
def test_every_started_trial_has_exactly_one_typed_terminal_outcome(
    tmp_path: Path,
    terminal_status: str,
) -> None:
    store = _store(tmp_path)
    trial_id = f"trial-{terminal_status}"
    store.append_event(
        _draft(
            event_type="TrialStarted",
            entity_id=trial_id,
            payload={
                "trial_id": trial_id,
                "candidate_id": f"candidate-{terminal_status}",
                "data_scope": "train_valid",
                "objective": "rank_ic",
                "started_at": "2025-01-01T00:00:00Z",
            },
        )
    )
    decision = "candidate_zoo" if terminal_status == "success" else "none"
    evaluation_event_hash = None
    if terminal_status == "success":
        evaluation = store.append_event(
            _draft(
                event_type="EvaluationRecorded",
                entity_id=f"evaluation-{trial_id}",
                payload={
                    "evaluation_id": f"evaluation-{trial_id}",
                    "trial_id": trial_id,
                    "factor_spec_id": f"factor-{trial_id}",
                    "data_scope": "train_valid",
                    "scorecard_hash": "sha256:" + "c" * 64,
                    "artifact_refs": [],
                    "metadata": {},
                },
            )
        )
        evaluation_event_hash = evaluation.event_hash
    terminal = store.append_event(
        _draft(
            event_type="TrialTerminated",
            entity_id=trial_id,
            payload={
                "trial_id": trial_id,
                "status": terminal_status,
                "reason_codes": [] if terminal_status == "success" else [terminal_status.upper()],
                "decision": decision,
                "evaluation_event_hash": evaluation_event_hash,
                "terminated_at": "2025-01-01T00:01:00Z",
            },
        )
    )

    assert terminal.payload["status"] == terminal_status
    assert store.lifecycle_summary().open_trial_ids == ()
    with pytest.raises(EventTransitionError, match="already terminated"):
        store.append_event(
            _draft(
                event_type="TrialTerminated",
                entity_id=trial_id,
                payload=dict(terminal.payload),
            )
        )


def test_terminal_requires_start_and_infrastructure_cannot_promote(tmp_path: Path) -> None:
    store = _store(tmp_path)
    payload = {
        "trial_id": "trial-orphan",
        "status": "infrastructure_failure",
        "reason_codes": ["WORKER_CRASH"],
        "decision": "paper_candidate",
        "evaluation_event_hash": None,
        "terminated_at": "2025-01-01T00:01:00Z",
    }

    with pytest.raises(EventValidationError, match="cannot promote"):
        store.append_event(
            _draft(event_type="TrialTerminated", entity_id="trial-orphan", payload=payload)
        )
    payload["decision"] = "none"
    with pytest.raises(EventTransitionError, match="was not started"):
        store.append_event(
            _draft(event_type="TrialTerminated", entity_id="trial-orphan", payload=payload)
        )


def test_trial_lifecycle_cannot_cross_run_boundaries(tmp_path: Path) -> None:
    store = _store(tmp_path)
    trial_id = "trial-run-boundary"
    started = _draft(
        event_type="TrialStarted",
        entity_id=trial_id,
        payload={
            "trial_id": trial_id,
            "candidate_id": "candidate-run-boundary",
            "data_scope": "train_valid",
            "objective": "rank_ic",
            "started_at": "2025-01-01T00:00:00Z",
        },
    )
    store.append_event(started)

    with pytest.raises(EventTransitionError, match="same run"):
        store.append_event(
            EventDraft(
                event_type="GenerationFailureRecorded",
                entity_id=trial_id,
                run_id="run-2",
                payload_schema_version="generation_failure_recorded.v1",
                payload={
                    "trial_id": trial_id,
                    "failure_code": "WORKER_TIMEOUT",
                    "failure_kind": "timeout",
                    "message": "bounded fixture timeout",
                    "occurred_at": "2025-01-01T00:00:30Z",
                },
            )
        )
    with pytest.raises(EventTransitionError, match="share one run"):
        store.append_event(
            EventDraft(
                event_type="EvaluationRecorded",
                entity_id="evaluation-run-boundary",
                run_id="run-2",
                payload_schema_version="evaluation_recorded.v1",
                payload={
                    "evaluation_id": "evaluation-run-boundary",
                    "trial_id": trial_id,
                    "factor_spec_id": "factor-run-boundary",
                    "data_scope": "train_valid",
                    "scorecard_hash": "sha256:" + "c" * 64,
                    "artifact_refs": [],
                    "metadata": {},
                },
            )
        )
    with pytest.raises(EventTransitionError, match="share one run"):
        store.append_event(
            EventDraft(
                event_type="TrialTerminated",
                entity_id=trial_id,
                run_id="run-2",
                payload_schema_version="trial_terminated.v1",
                payload={
                    "trial_id": trial_id,
                    "status": "skip",
                    "reason_codes": ["NO_EVALUATOR"],
                    "decision": "none",
                    "evaluation_event_hash": None,
                    "terminated_at": "2025-01-01T00:01:00Z",
                },
            )
        )
    terminal = store.append_event(
        _draft(
            event_type="TrialTerminated",
            entity_id=trial_id,
            payload={
                "trial_id": trial_id,
                "status": "skip",
                "reason_codes": ["NO_EVALUATOR"],
                "decision": "none",
                "evaluation_event_hash": None,
                "terminated_at": "2025-01-01T00:01:00Z",
            },
        )
    )
    assert terminal.run_id == started.run_id
    assert store.verify_chain()


def test_out_of_order_cross_event_references_are_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    digest = "sha256:" + "d" * 64

    with pytest.raises(EventTransitionError, match="prior terminal trial"):
        store.append_event(
            EventDraft(
                event_type="DerivationRecorded",
                entity_id="child-1",
                run_id="run-1",
                payload_schema_version="derivation_recorded.v1",
                payload={
                    "child_factor_spec_id": "child-1",
                    "parent_factor_spec_ids": ["parent-1"],
                    "trial_terminal_event_hash": digest,
                    "derivation_kind": "mutation",
                },
            )
        )
    with pytest.raises(EventTransitionError, match="prior contract"):
        store.append_event(
            EventDraft(
                event_type="FalsificationResultRecorded",
                entity_id="result-1",
                run_id="run-1",
                payload_schema_version="falsification_result_recorded.v1",
                payload={
                    "result_id": "result-1",
                    "contract_id": "contract-1",
                    "contract_hash": digest,
                    "outcome": "inconclusive",
                    "artifact_refs": [],
                },
            )
        )
    with pytest.raises(EventTransitionError, match="prior plan"):
        store.append_event(
            EventDraft(
                event_type="ForwardObservationRecorded",
                entity_id="observation-1",
                run_id="run-1",
                payload_schema_version="forward_observation_recorded.v1",
                payload={
                    "observation_id": "observation-1",
                    "plan_id": "plan-1",
                    "period_start": "2025-01-01T00:00:00Z",
                    "period_end": "2025-01-02T00:00:00Z",
                    "observation_hash": digest,
                    "previous_observation_hash": None,
                    "artifact_refs": [],
                },
            )
        )

    assert store.query_events() == []


def test_sql_update_delete_and_public_mutation_methods_are_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    event = store.append_event(_draft())

    with pytest.raises(EventMutationError):
        store.update(event.event_id, payload={})
    with pytest.raises(EventMutationError):
        store.delete(event.event_id)
    with sqlite3.connect(tmp_path / "research.sqlite") as conn:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("UPDATE research_events SET entity_id = 'x'")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute("DELETE FROM research_events")


def test_replay_rebuilds_identical_projection_state_hash_and_detects_tampering(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.append_event(_draft())
    store.append_event(
        _draft(
            event_type="TrialStarted",
            entity_id="trial-replay",
            payload={
                "trial_id": "trial-replay",
                "candidate_id": "candidate-replay",
                "data_scope": "train_valid",
                "objective": "rank_ic",
                "started_at": "2025-01-01T00:00:00Z",
            },
        )
    )
    store.append_event(
        _draft(
            event_type="EvaluationRecorded",
            entity_id="evaluation-1",
            payload={
                "evaluation_id": "evaluation-1",
                "trial_id": "trial-replay",
                "factor_spec_id": "factor-spec-1",
                "data_scope": "train_valid",
                "scorecard_hash": "sha256:" + "c" * 64,
                "artifact_refs": [],
                "metadata": {},
            },
        )
    )

    full = store.replay()
    repeated = store.replay()

    assert full == repeated
    assert full.event_count == 3
    assert full.watermark_event_hash == store.query_events()[-1].event_hash
    with sqlite3.connect(tmp_path / "research.sqlite") as conn:
        conn.execute("DROP TRIGGER research_events_no_update")
        conn.execute("UPDATE research_events SET payload = '{}' WHERE seq = 1")
    assert not store.verify_chain()
    with pytest.raises(ResearchEventAppendError, match="replay verification failed"):
        store.replay()


def test_writer_crash_rolls_back_partial_event_and_recovers(tmp_path: Path) -> None:
    class CrashingStore(ResearchEventStore):
        def _insert_event(self, conn, event, payload_json):  # noqa: ANN001
            super()._insert_event(conn, event, payload_json)
            raise RuntimeError("simulated writer crash")

    crashing = CrashingStore(
        tmp_path / "research.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=_flags(),
        code_version="test-code-v1",
    )

    with pytest.raises(ResearchEventAppendError, match="simulated writer crash"):
        crashing.append_event(_draft())

    recovered = _store(tmp_path)
    assert recovered.query_events() == []
    recovered.append_event(_draft())
    assert recovered.verify_chain()


def test_connection_lock_during_open_uses_bounded_retry_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    real_connect = store._connect
    attempts = 0

    def flaky_connect():  # noqa: ANN202
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise sqlite3.OperationalError("database is locked")
        return real_connect()

    monkeypatch.setattr(store, "_connect", flaky_connect)

    event = store.append_event(_draft())

    assert attempts == 3
    assert event.event_type == "FactorDefinitionRecorded"


def test_connection_retry_stops_at_configured_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    store.max_retries = 3
    attempts = 0

    def always_locked():  # noqa: ANN202
        nonlocal attempts
        attempts += 1
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "_connect", always_locked)

    with pytest.raises(ResearchEventAppendError, match="database is locked"):
        store.append_event(_draft())
    assert attempts == 3


def test_feature_off_store_refuses_before_creating_database_or_artifact_root(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="research events capability is disabled"):
        _store(tmp_path, enabled=False)

    assert list(tmp_path.iterdir()) == []


def test_durability_profiles_are_explicit_and_balanced_caps_research_only(
    tmp_path: Path,
) -> None:
    full = _store(tmp_path / "full")
    balanced = _store(tmp_path / "balanced", durability_profile="balanced")

    full_event = full.append_event(_draft())
    balanced_event = balanced.append_event(_draft())

    assert full.synchronous_mode == "FULL"
    assert full.decision_cap is None
    assert "REDUCED_DURABILITY" not in full_event.warnings
    assert balanced.synchronous_mode == "NORMAL"
    assert balanced.decision_cap == "research_only"
    assert "REDUCED_DURABILITY" in balanced_event.warnings
    assert full.durability_diagnostics() == {
        "journal_mode": "WAL",
        "synchronous": "FULL",
        "busy_timeout_ms": 30_000,
    }
    assert balanced.durability_diagnostics()["synchronous"] == "NORMAL"


def test_trial_ledger_v1_hash_contract_is_unchanged() -> None:
    entry = TrialLedgerEntry(
        trial_id="trial-golden",
        trial_group_id="group",
        parent_trial_id=None,
        candidate_id="candidate",
        parent_seed_id=None,
        formula="rank(close)",
        formula_hash="sha256:formula",
        data_snapshot_hash="sha256:snapshot",
        universe_hash="sha256:universe",
        split_id="train_valid",
        data_scope="train_valid",
        search_space_hash="sha256:space",
        objective="objective",
        random_seed=1,
        n_candidates_seen_so_far=1,
        status="success",
        decision="research_only",
        reason_codes=[],
        metrics_summary={"rank_ic": 0.01},
        previous_entry_hash=None,
        entry_hash="",
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat(),
    )

    assert entry.with_hashes(None).entry_hash == (
        "sha256:0bd93ae1941ff5d8bcad66fdacca04ef5b04be0515d1d03362a50c0305bb8afd"
    )


def test_event_table_coexists_in_same_database_without_changing_v1_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "shared-research.sqlite"
    v1 = TrialLedger(db_path)
    entry = TrialLedgerEntry(
        trial_id="trial-v1-shared",
        trial_group_id="group",
        parent_trial_id=None,
        candidate_id="candidate",
        parent_seed_id=None,
        formula="rank(close)",
        formula_hash="sha256:formula",
        data_snapshot_hash="sha256:snapshot",
        universe_hash="sha256:universe",
        split_id="train_valid",
        data_scope="train_valid",
        search_space_hash="sha256:space",
        objective="objective",
        random_seed=1,
        n_candidates_seen_so_far=1,
        status="success",
        decision="research_only",
        reason_codes=[],
        metrics_summary={"rank_ic": 0.01},
        previous_entry_hash=None,
        entry_hash="",
        created_at="2025-01-01T00:00:00Z",
    )
    stored_v1 = v1.append(entry)
    events = ResearchEventStore(
        db_path,
        artifact_root=tmp_path / "artifacts",
        flags=_flags(),
        code_version="test-code-v1",
    )
    events.append_event(_draft())

    reread = TrialLedger(db_path).query()
    assert len(reread) == 1
    assert reread[0].to_dict() == stored_v1.to_dict()
    assert TrialLedger(db_path).verify_hash_chain()
    assert events.verify_chain()
