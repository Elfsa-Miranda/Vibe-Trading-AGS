"""Closed versioned payload registry and deterministic validation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Any, Callable, Mapping

from src.research_ledger.events.model import EventValidationError
from src.research_ledger.hash_utils import canonical_json, canonical_json_hash, redact_secrets


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_MAX_PAYLOAD_BYTES = 262_144
_MAX_DEPTH = 24
_MAX_NODES = 10_000
_EVENT_PRIVATE_PATH_RE = re.compile(
    r"^(?:/(?:home|mnt|opt|private|root|tmp|Users|var|workspace)(?:/|$)|[A-Za-z]:[\\/]|\\\\)"
)
_EVENT_SENSITIVE_KEYS = frozenset(
    {"account_id", "account_ids", "env", "environ", "environment", "environment_variables", "oauth_cache"}
)

Validator = Callable[[Any, str], None]


def _string(value: Any, path: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise EventValidationError(f"{path} must be a non-empty bounded string")
    if any(ord(char) < 32 for char in value):
        raise EventValidationError(f"{path} contains control characters")


def _nullable_string(value: Any, path: str) -> None:
    if value is not None:
        _string(value, path)


def _hash(value: Any, path: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise EventValidationError(f"{path} must be a sha256 content hash")


def _nullable_hash(value: Any, path: str) -> None:
    if value is not None:
        _hash(value, path)


def _timestamp(value: Any, path: str) -> None:
    _string(value, path)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise EventValidationError(f"{path} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise EventValidationError(f"{path} must include a timezone")


def _date(value: Any, path: str) -> None:
    _string(value, path)
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise EventValidationError(f"{path} must be an ISO date") from exc


def _integer(value: Any, path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EventValidationError(f"{path} must be an integer")


def _nonnegative_integer(value: Any, path: str) -> None:
    _integer(value, path)
    if value < 0:
        raise EventValidationError(f"{path} must be non-negative")


def _positive_integer(value: Any, path: str) -> None:
    _integer(value, path)
    if value <= 0:
        raise EventValidationError(f"{path} must be positive")


def _candidate_budget(value: Any, path: str) -> None:
    _positive_integer(value, path)
    if value > 100_000:
        raise EventValidationError(f"{path} exceeds the process-memory resource limit")


def _probability(value: Any, path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EventValidationError(f"{path} must be numeric")
    if not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
        raise EventValidationError(f"{path} must be finite and between zero and one")


def _finite_number(value: Any, path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EventValidationError(f"{path} must be numeric")
    if not math.isfinite(float(value)):
        raise EventValidationError(f"{path} must be finite")


def _positive_finite_number(value: Any, path: str) -> None:
    _finite_number(value, path)
    if float(value) <= 0.0:
        raise EventValidationError(f"{path} must be positive")


def _nonnegative_finite_number(value: Any, path: str) -> None:
    _finite_number(value, path)
    if float(value) < 0.0:
        raise EventValidationError(f"{path} must be non-negative")


def _boolean(value: Any, path: str) -> None:
    if not isinstance(value, bool):
        raise EventValidationError(f"{path} must be boolean")


def _mapping(value: Any, path: str) -> None:
    if not isinstance(value, Mapping):
        raise EventValidationError(f"{path} must be an object")


def _sorted_hash_mapping(value: Any, path: str) -> None:
    if not isinstance(value, Mapping) or not value:
        raise EventValidationError(f"{path} must be a non-empty object")
    keys = [str(key) for key in value]
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise EventValidationError(f"{path} keys must be sorted and unique")
    for key, item in value.items():
        _string(str(key), f"{path}.key")
        _hash(item, f"{path}.{key}")


def _nullable_mapping(value: Any, path: str) -> None:
    if value is not None:
        _mapping(value, path)


def _nullable_finite_number(value: Any, path: str) -> None:
    if value is not None:
        _finite_number(value, path)


def _string_list(value: Any, path: str) -> None:
    if not isinstance(value, (list, tuple)):
        raise EventValidationError(f"{path} must be a list")
    for index, item in enumerate(value):
        _string(item, f"{path}[{index}]")


def _nonempty_string_list(value: Any, path: str) -> None:
    _string_list(value, path)
    if not value:
        raise EventValidationError(f"{path} must not be empty")


def _finite_number_list(value: Any, path: str) -> None:
    if not isinstance(value, (list, tuple)) or not value:
        raise EventValidationError(f"{path} must be a non-empty list")
    for index, item in enumerate(value):
        _finite_number(item, f"{path}[{index}]")


def _hash_list(value: Any, path: str, *, allow_empty: bool) -> None:
    if not isinstance(value, (list, tuple)):
        raise EventValidationError(f"{path} must be a list")
    if not allow_empty and not value:
        raise EventValidationError(f"{path} must not be empty")
    normalized: list[str] = []
    for index, item in enumerate(value):
        _hash(item, f"{path}[{index}]")
        normalized.append(str(item))
    if len(normalized) != len(set(normalized)):
        raise EventValidationError(f"{path} must contain unique hashes")
    if normalized != sorted(normalized):
        raise EventValidationError(f"{path} must be sorted")


def _nonempty_hash_list(value: Any, path: str) -> None:
    _hash_list(value, path, allow_empty=False)


def _unique_hash_list(value: Any, path: str) -> None:
    _hash_list(value, path, allow_empty=True)


def _ordered_unique_hash_list(value: Any, path: str) -> None:
    if not isinstance(value, (list, tuple)) or not value:
        raise EventValidationError(f"{path} must be a non-empty list")
    normalized: list[str] = []
    for index, item in enumerate(value):
        _hash(item, f"{path}[{index}]")
        normalized.append(str(item))
    if len(normalized) != len(set(normalized)):
        raise EventValidationError(f"{path} must contain unique hashes")


def _artifact_list(value: Any, path: str) -> None:
    if not isinstance(value, (list, tuple)):
        raise EventValidationError(f"{path} must be a list")
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise EventValidationError(f"{path}[{index}] must be an object")
        expected = {"relative_path", "artifact_hash", "media_type"}
        if set(item) != expected:
            raise EventValidationError(f"{path}[{index}] has unknown artifact fields")
        _string(item["relative_path"], f"{path}[{index}].relative_path")
        _hash(item["artifact_hash"], f"{path}[{index}].artifact_hash")
        _string(item["media_type"], f"{path}[{index}].media_type")


def _activation_terminal_counts(value: Any, path: str) -> None:
    _mapping(value, path)
    expected = {
        "success", "reject", "skip", "invalid", "duplicate", "timeout", "error",
        "infrastructure_failure",
    }
    if set(value) != expected:
        raise EventValidationError(f"{path} must contain the closed terminal-status set")
    for key in sorted(expected):
        _nonnegative_integer(value[key], f"{path}.{key}")


def _registry_root_list(value: Any, path: str) -> None:
    if not isinstance(value, (list, tuple)):
        raise EventValidationError(f"{path} must be a list")
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise EventValidationError(f"{path}[{index}] must be an object")
        expected = {
            "alpha_id",
            "status",
            "expression_id",
            "legacy_formula_hash",
        }
        if set(item) != expected:
            raise EventValidationError(f"{path}[{index}] has unknown root fields")
        alpha_id = item["alpha_id"]
        _string(alpha_id, f"{path}[{index}].alpha_id")
        if alpha_id in seen:
            raise EventValidationError(f"{path} contains duplicate alpha_id")
        seen.add(alpha_id)
        _enum("canonical_dsl", "legacy_opaque")(item["status"], f"{path}[{index}].status")
        _nullable_hash(item["expression_id"], f"{path}[{index}].expression_id")
        _hash(item["legacy_formula_hash"], f"{path}[{index}].legacy_formula_hash")
        if item["status"] == "canonical_dsl" and item["expression_id"] is None:
            raise EventValidationError(f"{path}[{index}] canonical root requires expression_id")
        if item["status"] == "legacy_opaque" and item["expression_id"] is not None:
            raise EventValidationError(f"{path}[{index}] opaque root cannot claim expression_id")


def _registry_root_v2_list(value: Any, path: str) -> None:
    if not isinstance(value, (list, tuple)) or not value:
        raise EventValidationError(f"{path} must be a non-empty list")
    seen: set[str] = set()
    expected = {
        "alpha_id", "status", "expression_id", "canonical_formula",
        "legacy_formula_hash", "source_hash", "source_status", "source_reason",
    }
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, Mapping) or set(item) != expected:
            raise EventValidationError(f"{item_path} has unknown or missing root fields")
        alpha_id = item["alpha_id"]
        _string(alpha_id, f"{item_path}.alpha_id")
        if alpha_id in seen:
            raise EventValidationError(f"{path} contains duplicate alpha_id")
        seen.add(alpha_id)
        _enum("canonical_dsl", "legacy_opaque")(item["status"], f"{item_path}.status")
        _nullable_hash(item["expression_id"], f"{item_path}.expression_id")
        _nullable_string(item["canonical_formula"], f"{item_path}.canonical_formula")
        _hash(item["legacy_formula_hash"], f"{item_path}.legacy_formula_hash")
        _nullable_hash(item["source_hash"], f"{item_path}.source_hash")
        _enum("available", "unavailable")(item["source_status"], f"{item_path}.source_status")
        _nullable_string(item["source_reason"], f"{item_path}.source_reason")
        if item["status"] == "canonical_dsl" and (
            item["expression_id"] is None or item["canonical_formula"] is None
        ):
            raise EventValidationError(f"{item_path} canonical root lacks identity evidence")
        if item["status"] == "legacy_opaque" and (
            item["expression_id"] is not None or item["canonical_formula"] is not None
        ):
            raise EventValidationError(f"{item_path} opaque root claims canonical identity")
        if item["source_status"] == "available" and (
            item["source_hash"] is None or item["source_reason"] is not None
        ):
            raise EventValidationError(f"{item_path} available source evidence is malformed")
        if item["source_status"] == "unavailable" and (
            item["source_hash"] is not None or item["source_reason"] is None
        ):
            raise EventValidationError(f"{item_path} unavailable source evidence is malformed")


def _enum(*values: str) -> Validator:
    allowed = frozenset(values)

    def validate(value: Any, path: str) -> None:
        if value not in allowed:
            raise EventValidationError(f"{path} must be one of {sorted(allowed)}")

    return validate


def _reason_codes(value: Any, path: str) -> None:
    _string_list(value, path)
    for item in value:
        if not _SAFE_CODE_RE.fullmatch(item):
            raise EventValidationError(f"{path} contains an invalid reason code")


def _validate_retriever_component_list(
    value: Any,
    path: str,
    *,
    require_unique_factors: bool,
) -> None:
    if not isinstance(value, (list, tuple)):
        raise EventValidationError(f"{path} must be a list")
    seen_factors: set[str] = set()
    seen_actions: set[str] = set()
    expected = {
        "factor_spec_id", "action_id", "motif", "node_kind",
        "output_panel_hash", "semantic_model_id", "semantic_model_version",
        "semantic_embedding_hash", "cost_evidence_hash", "valdiv",
        "semdiv", "syndiv", "topology_score", "base_score",
        "memory_adjustment", "action_score", "confidence", "selected",
        "selection_propensity", "warnings", "veto_reason",
    }
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not isinstance(item, Mapping) or set(item) != expected:
            raise EventValidationError(f"{item_path} has unknown or missing component fields")
        for name in ("factor_spec_id", "action_id", "motif"):
            _string(item[name], f"{item_path}.{name}")
        for name in ("semantic_model_id", "semantic_model_version"):
            _string(item[name], f"{item_path}.{name}")
        for name in ("output_panel_hash", "semantic_embedding_hash", "cost_evidence_hash"):
            _hash(item[name], f"{item_path}.{name}")
        if (
            (require_unique_factors and item["factor_spec_id"] in seen_factors)
            or item["action_id"] in seen_actions
        ):
            raise EventValidationError(f"{path} contains duplicate factor or action identity")
        seen_factors.add(item["factor_spec_id"])
        seen_actions.add(item["action_id"])
        _enum("leaf", "nonleaf")(item["node_kind"], f"{item_path}.node_kind")
        for name in (
            "valdiv", "semdiv", "syndiv", "topology_score", "confidence",
            "selection_propensity",
        ):
            _probability(item[name], f"{item_path}.{name}")
        for name in ("base_score", "memory_adjustment", "action_score"):
            _finite_number(item[name], f"{item_path}.{name}")
        if float(item["base_score"]) <= 0.0:
            raise EventValidationError(f"{item_path}.base_score must be positive")
        _boolean(item["selected"], f"{item_path}.selected")
        _string_list(item["warnings"], f"{item_path}.warnings")
        _nullable_string(item["veto_reason"], f"{item_path}.veto_reason")
        if item["selected"] and float(item["selection_propensity"]) <= 0.0:
            raise EventValidationError(f"{item_path} selected component needs a propensity")
        if item["veto_reason"] is not None and item["selected"]:
            raise EventValidationError(f"{item_path} vetoed component cannot be selected")


def _retriever_component_list(value: Any, path: str) -> None:
    _validate_retriever_component_list(
        value,
        path,
        require_unique_factors=True,
    )


def _retriever_action_component_list(value: Any, path: str) -> None:
    _validate_retriever_component_list(
        value,
        path,
        require_unique_factors=False,
    )


def _retriever_policy_config(value: Any, path: str) -> None:
    if not isinstance(value, Mapping):
        raise EventValidationError(f"{path} must be an object")
    expected = {
        "policy_version", "epsilon", "memory_weight", "residual_clip",
        "veto_exploration_probability", "softmax_temperature",
        "maximum_candidate_budget",
    }
    if set(value) != expected:
        raise EventValidationError(f"{path} has unknown or missing policy fields")
    _enum("topology_shadow_policy.v2")(value["policy_version"], f"{path}.policy_version")
    for name in (
        "epsilon", "memory_weight", "residual_clip",
        "veto_exploration_probability", "softmax_temperature",
    ):
        _finite_number(value[name], f"{path}.{name}")
    _positive_integer(value["maximum_candidate_budget"], f"{path}.maximum_candidate_budget")
    if not 0.0 < float(value["epsilon"]) <= 1e-3:
        raise EventValidationError(f"{path}.epsilon is out of bounds")
    if not 0.0 <= float(value["memory_weight"]) <= 1.0:
        raise EventValidationError(f"{path}.memory_weight is out of bounds")
    if not 0.0 < float(value["residual_clip"]) <= 1.0:
        raise EventValidationError(f"{path}.residual_clip is out of bounds")
    if not 0.0 <= float(value["veto_exploration_probability"]) <= 0.25:
        raise EventValidationError(f"{path}.veto exploration is out of bounds")
    if not 0.0 < float(value["softmax_temperature"]) <= 10.0:
        raise EventValidationError(f"{path}.softmax temperature is out of bounds")
    if int(value["maximum_candidate_budget"]) > 100_000:
        raise EventValidationError(f"{path}.maximum candidate budget is out of bounds")


@dataclass(frozen=True)
class PayloadSpec:
    version: str
    fields: Mapping[str, Validator]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))


_DATA_SCOPE = _enum("train", "valid", "train_valid", "test", "final_test", "forward", "demo_fixture")
_DISCOVERY_EVALUATION_SCOPE = _enum("valid", "train_valid")
_DECISION = _enum(
    "reject",
    "research_only",
    "candidate_zoo",
    "paper_candidate",
    "forward_track",
    "none",
)

_PAYLOAD_SPECS: dict[str, PayloadSpec] = {
    "TrialStarted": PayloadSpec(
        "trial_started.v1",
        {
            "trial_id": _string,
            "candidate_id": _string,
            "data_scope": _DATA_SCOPE,
            "objective": _string,
            "started_at": _timestamp,
        },
    ),
    "FactorDefinitionRecorded": PayloadSpec(
        "factor_definition_recorded.v1",
        {
            "factor_spec_id": _string,
            "expression_id": _string,
            "canonical_ast_hash": _hash,
            "grammar_version": _string,
            "grammar_hash": _hash,
            "metadata": _mapping,
            "artifact_refs": _artifact_list,
        },
    ),
    "RegistryBootstrapRecorded": PayloadSpec(
        "registry_bootstrap_recorded.v1",
        {
            "snapshot_id": _string,
            "registry_snapshot_hash": _hash,
            "registry_code_hash": _hash,
            "grammar_hash": _hash,
            "roots": _registry_root_list,
        },
    ),
    "RegistryBootstrapRecordedV2": PayloadSpec(
        "registry_bootstrap_recorded.v2",
        {
            "snapshot_id": _string,
            "registry_snapshot_hash": _hash,
            "registry_code_hash": _hash,
            "grammar_version": _string,
            "grammar_hash": _hash,
            "grammar_definition": _mapping,
            "roots": _registry_root_v2_list,
        },
    ),
    "DerivationRecorded": PayloadSpec(
        "derivation_recorded.v1",
        {
            "child_factor_spec_id": _string,
            "parent_factor_spec_ids": _nonempty_string_list,
            "trial_terminal_event_hash": _hash,
            "derivation_kind": _enum("mutation", "crossover", "manual_registered"),
        },
    ),
    "ProcessActionFrozen": PayloadSpec(
        "process_action_frozen.v1",
        {
            "action_id": _string,
            "trial_id": _string,
            "parent_factor_spec_id": _string,
            "candidate_id": _string,
            "base_expected_utility": _finite_number,
            "eligible_event_watermark": _nullable_hash,
            "policy_hash": _hash,
            "seed": _integer,
            "candidate_budget": _nonnegative_integer,
            "frozen_at": _timestamp,
        },
    ),
    "ProcessOutcomeRecorded": PayloadSpec(
        "process_outcome_recorded.v1",
        {
            "outcome_id": _string,
            "action_id": _string,
            "trial_id": _string,
            "terminal_event_hash": _hash,
            "data_scope": _DATA_SCOPE,
            "child_factor_spec_id": _nullable_string,
            "observed_validation_utility": _nullable_finite_number,
            "ast_diff": _nullable_mapping,
            "available_at": _timestamp,
            "policy_hash": _hash,
        },
    ),
    "ProcessActionFrozenV2": PayloadSpec(
        "process_action_frozen.v2",
        {
            "action_id": _string,
            "trial_id": _string,
            "parent_factor_spec_id": _string,
            "candidate_id": _string,
            "base_expected_utility": _finite_number,
            "eligible_event_watermark": _hash,
            "policy_hash": _hash,
            "utility_policy_hash": _hash,
            "data_snapshot_hash": _hash,
            "regime_config_hash": _nullable_hash,
            "run_group_id": _string,
            "seed": _integer,
            "candidate_budget": _candidate_budget,
            "frozen_at": _timestamp,
        },
    ),
    "ProcessOutcomeRecordedV2": PayloadSpec(
        "process_outcome_recorded.v2",
        {
            "outcome_id": _string,
            "action_id": _string,
            "trial_id": _string,
            "terminal_event_hash": _hash,
            "evaluation_event_hash": _hash,
            "derivation_event_hash": _hash,
            "data_scope": _DISCOVERY_EVALUATION_SCOPE,
            "child_factor_spec_id": _string,
            "observed_validation_utility": _finite_number,
            "scorecard_hash": _hash,
            "utility_policy_hash": _hash,
            "ast_diff": _mapping,
            "ast_diff_hash": _hash,
            "available_at": _timestamp,
            "policy_hash": _hash,
            "data_snapshot_hash": _hash,
            "regime_config_hash": _nullable_hash,
            "run_group_id": _string,
        },
    ),
    "GenerationFailureRecorded": PayloadSpec(
        "generation_failure_recorded.v1",
        {
            "trial_id": _string,
            "failure_code": _string,
            "failure_kind": _enum("invalid", "timeout", "error", "infrastructure_failure"),
            "message": _string,
            "occurred_at": _timestamp,
        },
    ),
    "EvaluationRecorded": PayloadSpec(
        "evaluation_recorded.v1",
        {
            "evaluation_id": _string,
            "trial_id": _string,
            "factor_spec_id": _string,
            "data_scope": _DATA_SCOPE,
            "scorecard_hash": _hash,
            "artifact_refs": _artifact_list,
            "metadata": _mapping,
        },
    ),
    "TrialTerminated": PayloadSpec(
        "trial_terminated.v1",
        {
            "trial_id": _string,
            "status": _enum(
                "success",
                "reject",
                "skip",
                "invalid",
                "duplicate",
                "timeout",
                "error",
                "infrastructure_failure",
            ),
            "reason_codes": _reason_codes,
            "decision": _DECISION,
            "evaluation_event_hash": _nullable_hash,
            "terminated_at": _timestamp,
        },
    ),
    "RetrieverDecisionRecorded": PayloadSpec(
        "retriever_decision_recorded.v1",
        {
            "decision_id": _string,
            "selected_factor_spec_ids": _string_list,
            "selection_propensity": _probability,
            "seed": _integer,
            "policy_hash": _hash,
            "eligible_event_watermark": _nullable_hash,
            "veto_reason": _nullable_string,
        },
    ),
    "RetrieverActionTemplateFrozen": PayloadSpec(
        "retriever_action_template_frozen.v1",
        {
            "action_id": _string,
            "action_hash": _hash,
            "schema_version": _enum("frozen_retriever_action_template.v1"),
            "execution_run_id": _string,
            "parent_factor_spec_id": _string,
            "parent_definition_event_hash": _hash,
            "eligible_event_watermark": _hash,
            "data_snapshot_hash": _hash,
            "retrieval_policy_hash": _hash,
            "template_registry_hash": _hash,
            "template_id": _enum(
                "identity", "rank_wrap", "decay_3", "delay_1", "zscore_wrap"
            ),
            "expected_formula": _string,
            "expected_formula_hash": _hash,
            "expected_candidate_id": _string,
            "expected_expression_id": _hash,
            "expected_canonical_ast_hash": _hash,
            "expected_ast_diff_hash": _nullable_hash,
            "expected_motif_version": _nullable_string,
            "expected_motif": _nullable_string,
            "identity_action": _boolean,
        },
    ),
    "TrainValidDataSnapshotFrozen": PayloadSpec(
        "train_valid_data_snapshot_frozen.v1",
        {
            "snapshot_id": _string,
            "snapshot_hash": _hash,
            "data_scope": _enum("train_valid"),
            "panel_content_hash": _hash,
            "frame_content_hashes": _sorted_hash_mapping,
            "frame_names": _nonempty_string_list,
            "source_config_hash": _hash,
            "pit_contract_present": _boolean,
            "survivorship_bias": _boolean,
            "artifact_refs": _artifact_list,
        },
    ),
    "RetrieverFeatureSourceRecorded": PayloadSpec(
        "retriever_feature_source_recorded.v1",
        {
            "feature_source_id": _string,
            "source_hash": _hash,
            "execution_run_id": _string,
            "snapshot_event_hash": _hash,
            "snapshot_hash": _hash,
            "eligible_event_watermark": _hash,
            "retrieval_policy_hash": _hash,
            "feature_policy_hash": _hash,
            "action_event_hashes": _ordered_unique_hash_list,
            "scorecard_event_hashes": _unique_hash_list,
            "candidate_count": _positive_integer,
            "candidate_hashes": _ordered_unique_hash_list,
            "semantic_state": _enum("unavailable"),
            "artifact_refs": _artifact_list,
        },
    ),
    "RetrieverDecisionV2Recorded": PayloadSpec(
        "retriever_decision_recorded.v2",
        {
            "decision_id": _string,
            "decision_hash": _hash,
            "selected_factor_spec_ids": _string_list,
            "seed": _integer,
            "policy_version": _string,
            "policy_hash": _hash,
            "policy_config": _retriever_policy_config,
            "eligible_event_watermark": _hash,
            "data_snapshot_hash": _hash,
            "candidate_budget": _candidate_budget,
            "official_output_hash": _hash,
            "propensity_semantics": _enum("sequential_softmax_draw_probability.v1"),
            "components": _retriever_component_list,
            "shadow_only": _boolean,
        },
    ),
    "RetrieverDecisionV3Recorded": PayloadSpec(
        "retriever_decision_recorded.v3",
        {
            "decision_id": _string,
            "decision_hash": _hash,
            "shadow_decision_hash": _hash,
            "input_bundle_hash": _hash,
            "selected_factor_spec_ids": _string_list,
            "seed": _integer,
            "policy_version": _enum("topology_activation_policy.v3"),
            "policy_hash": _hash,
            "policy_config": _mapping,
            "eligible_event_watermark": _hash,
            "data_snapshot_hash": _hash,
            "candidate_budget": _candidate_budget,
            "official_output_hash": _hash,
            "propensity_semantics": _enum("sequential_softmax_draw_probability.v1"),
            "components": _retriever_component_list,
            "shadow_only": _boolean,
            "artifact_refs": _artifact_list,
        },
    ),
    "RetrieverDecisionV4Recorded": PayloadSpec(
        "retriever_decision_recorded.v4",
        {
            "decision_id": _string,
            "decision_hash": _hash,
            "shadow_decision_hash": _hash,
            "input_bundle_hash": _hash,
            "control_evidence_event_hash": _hash,
            "control_evidence_hash": _hash,
            "control_policy_hash": _hash,
            "selected_factor_spec_ids": _string_list,
            "seed": _integer,
            "policy_version": _enum("topology_activation_policy.v3"),
            "policy_hash": _hash,
            "policy_config": _mapping,
            "eligible_event_watermark": _hash,
            "data_snapshot_hash": _hash,
            "candidate_budget": _candidate_budget,
            "official_output_hash": _hash,
            "propensity_semantics": _enum("sequential_softmax_draw_probability.v1"),
            "components": _retriever_component_list,
            "shadow_only": _boolean,
            "artifact_refs": _artifact_list,
        },
    ),
    "RetrieverDecisionV5Recorded": PayloadSpec(
        "retriever_decision_recorded.v5",
        {
            "decision_id": _string,
            "decision_hash": _hash,
            "shadow_decision_hash": _hash,
            "input_bundle_hash": _hash,
            "control_evidence_event_hash": _hash,
            "control_evidence_hash": _hash,
            "control_policy_hash": _hash,
            "selected_action_ids": _string_list,
            "selected_parent_factor_spec_ids": _string_list,
            "action_template_event_hashes": _ordered_unique_hash_list,
            "seed": _integer,
            "policy_version": _enum("topology_activation_policy.v3"),
            "policy_hash": _hash,
            "policy_config": _mapping,
            "eligible_event_watermark": _hash,
            "data_snapshot_hash": _hash,
            "candidate_budget": _candidate_budget,
            "official_output_hash": _hash,
            "propensity_semantics": _enum(
                "sequential_action_softmax_draw_probability.v1"
            ),
            "components": _retriever_action_component_list,
            "shadow_only": _boolean,
            "artifact_refs": _artifact_list,
        },
    ),
    "OfficialSearchControlRecorded": PayloadSpec(
        "official_search_control_recorded.v1",
        {
            "control_id": _string,
            "evidence_hash": _hash,
            "policy_hash": _hash,
            "output_hash": _hash,
            "search_run_id": _string,
            "data_snapshot_hash": _hash,
            "candidate_count": _positive_integer,
            "terminal_event_hashes": _nonempty_hash_list,
            "artifact_refs": _artifact_list,
        },
    ),
    "ActivationPlanRegistered": PayloadSpec(
        "activation_plan_registered.v1",
        {
            "experiment_id": _string,
            "plan_hash": _hash,
            "phase": _enum("pilot", "confirmatory"),
            "registered_at": _timestamp,
            "artifact_refs": _artifact_list,
        },
    ),
    "ActivationRunRecorded": PayloadSpec(
        "activation_run_recorded.v1",
        {
            "manifest_id": _string,
            "plan_hash": _hash,
            "manifest_hash": _hash,
            "pair_id": _string,
            "run_group_id": _string,
            "arm": _enum("control", "treatment"),
            "terminal_status_counts": _activation_terminal_counts,
            "complete": _boolean,
            "contaminated": _boolean,
            "artifact_refs": _artifact_list,
        },
    ),
    "ActivationRunSourceAudited": PayloadSpec(
        "activation_run_source_audited.v2",
        {
            "audit_id": _string,
            "plan_hash": _hash,
            "summary_manifest_hash": _hash,
            "source_watermark_event_hash": _hash,
            "audit_hash": _hash,
            "retriever_decision_event_hashes": _unique_hash_list,
            "terminal_event_hashes": _unique_hash_list,
            "evaluation_event_hashes": _unique_hash_list,
            "quality_decision_event_hashes": _unique_hash_list,
            "source_failure_codes": _reason_codes,
            "source_complete": _boolean,
            "artifact_refs": _artifact_list,
        },
    ),
    "ActivationResourceMeasured": PayloadSpec(
        "activation_resource_measured.v1",
        {
            "resource_id": _string,
            "plan_hash": _hash,
            "pair_id": _string,
            "run_group_id": _string,
            "arm": _enum("control", "treatment"),
            "manifest_hash": _hash,
            "measurement_policy_hash": _hash,
            "wall_seconds": _nonnegative_finite_number,
            "cpu_seconds": _nonnegative_finite_number,
            "peak_rss_mb": _nullable_finite_number,
            "peak_rss_method": _enum("unavailable_without_isolated_worker.v1"),
            "source_failure_codes": _reason_codes,
            "source_complete": _boolean,
            "evidence_hash": _hash,
            "artifact_refs": _artifact_list,
        },
    ),
    "ActivationGenerationConsumptionRecorded": PayloadSpec(
        "activation_generation_consumption_recorded.v1",
        {
            "generation_id": _string,
            "plan_hash": _hash,
            "pair_id": _string,
            "run_group_id": _string,
            "mechanism_family": _string,
            "dag_region": _string,
            "execution_run_id": _string,
            "retriever_decision_event_hash": _hash,
            "retriever_decision_hash": _hash,
            "control_evidence_event_hash": _hash,
            "generator_policy_hash": _hash,
            "selected_parent_factor_spec_ids": _nonempty_string_list,
            "consumed_parent_factor_spec_ids": _nonempty_string_list,
            "generated_candidate_count": _positive_integer,
            "candidate_budget": _candidate_budget,
            "compute_budget": _positive_integer,
            "source_failure_codes": _reason_codes,
            "source_complete": _boolean,
            "evidence_hash": _hash,
            "artifact_refs": _artifact_list,
        },
    ),
    "ActivationGenerationConsumptionV2Recorded": PayloadSpec(
        "activation_generation_consumption_recorded.v2",
        {
            "generation_id": _string,
            "plan_hash": _hash,
            "pair_id": _string,
            "run_group_id": _string,
            "mechanism_family": _string,
            "dag_region": _string,
            "execution_run_id": _string,
            "retriever_decision_event_hash": _hash,
            "retriever_decision_hash": _hash,
            "control_evidence_event_hash": _hash,
            "generator_policy_hash": _hash,
            "selected_action_ids": _nonempty_string_list,
            "selected_action_event_hashes": _ordered_unique_hash_list,
            "selected_parent_factor_spec_ids": _nonempty_string_list,
            "consumed_action_ids": _nonempty_string_list,
            "consumed_parent_factor_spec_ids": _nonempty_string_list,
            "generated_candidate_count": _positive_integer,
            "candidate_budget": _candidate_budget,
            "compute_budget": _positive_integer,
            "source_failure_codes": _reason_codes,
            "source_complete": _boolean,
            "evidence_hash": _hash,
            "artifact_refs": _artifact_list,
        },
    ),
    "ActivationResultRecorded": PayloadSpec(
        "activation_result_recorded.v1",
        {
            "result_id": _string,
            "plan_hash": _hash,
            "result_hash": _hash,
            "complete_pairs": _nonnegative_integer,
            "invalidation_reasons": _reason_codes,
            "replayable": _boolean,
            "artifact_refs": _artifact_list,
        },
    ),
    "RetrieverActivationDecisionRecorded": PayloadSpec(
        "retriever_activation_decision_recorded.v1",
        {
            "activation_decision_id": _string,
            "plan_hash": _hash,
            "result_hash": _hash,
            "decision_hash": _hash,
            "policy_hash": _hash,
            "verdict": _enum("approved", "rejected", "inconclusive", "invalidated"),
            "reasons": _reason_codes,
            "active_research_only": _boolean,
            "artifact_refs": _artifact_list,
        },
    ),
    "FalsificationContractRegistered": PayloadSpec(
        "falsification_contract_registered.v1",
        {
            "contract_id": _string,
            "contract_hash": _hash,
            "factor_spec_id": _string,
            "registered_at": _timestamp,
            "data_access_cutoff": _timestamp,
            "policy_hash": _hash,
        },
    ),
    "SequentialProtocolRegistered": PayloadSpec(
        "sequential_protocol_registered.v1",
        {
            "protocol_id": _string,
            "protocol_hash": _hash,
            "contract_id": _string,
            "contract_hash": _hash,
            "factor_spec_id": _string,
            "method": _enum("bounded_mean_mixture_e.v1"),
            "maximum_looks": _positive_integer,
            "stopping_rule": _enum("e_process_boundary_or_max_looks.v1"),
            "data_scope": _enum("valid"),
            "family_alpha": _probability,
            "support_alpha": _probability,
            "contradiction_alpha": _probability,
            "lambda_grid": _finite_number_list,
            "mixture_weights": _finite_number_list,
            "support_log_boundary": _positive_finite_number,
            "contradiction_log_boundary": _positive_finite_number,
            "filtration_hash": _hash,
            "block_schedule_hash": _hash,
            "policy_hash": _hash,
            "registered_at": _timestamp,
        },
    ),
    "SequentialLookRecorded": PayloadSpec(
        "sequential_look_recorded.v1",
        {
            "look_id": _string,
            "protocol_id": _string,
            "protocol_hash": _hash,
            "factor_spec_id": _string,
            "look_index": _positive_integer,
            "information_time": _positive_integer,
            "block_id": _string,
            "block_hash": _hash,
            "unit_hashes": _nonempty_hash_list,
            "incremental_information": _positive_integer,
            "support_component_log_capitals": _finite_number_list,
            "contradiction_component_log_capitals": _finite_number_list,
            "cumulative_support_log_e": _finite_number,
            "cumulative_contradiction_log_e": _finite_number,
            "support_log_boundary": _positive_finite_number,
            "contradiction_log_boundary": _positive_finite_number,
            "status": _enum(
                "continue",
                "support_boundary_crossed",
                "contradiction_boundary_crossed",
                "max_looks_reached",
            ),
            "stop_reason": _enum(
                "NONE",
                "SUPPORT_BOUNDARY",
                "CONTRADICTION_BOUNDARY",
                "MAX_LOOKS",
            ),
            "previous_look_event_hash": _nullable_hash,
        },
    ),
    "OutcomeDataAccessed": PayloadSpec(
        "outcome_data_accessed.v1",
        {
            "access_id": _string,
            "factor_spec_id": _string,
            "data_scope": _enum("valid", "test", "final_test"),
            "accessed_at": _timestamp,
        },
    ),
    "FalsificationResultRecorded": PayloadSpec(
        "falsification_result_recorded.v1",
        {
            "result_id": _string,
            "contract_id": _string,
            "contract_hash": _hash,
            "outcome": _enum("falsified", "inconclusive", "partial_support", "supported"),
            "artifact_refs": _artifact_list,
        },
    ),
    "MechanismEvidenceIndexRecorded": PayloadSpec(
        "mechanism_evidence_index_recorded.v1",
        {
            "mei_id": _string,
            "factor_spec_id": _string,
            "mei_hash": _hash,
            "mei_schema_version": _enum("mechanism_evidence_index.v1"),
            "truth_table_version": _enum("mechanism_evidence_truth_table.v1"),
            "policy_version": _string,
            "policy_hash": _hash,
            "source_result_hashes": _nonempty_hash_list,
            "source_event_hashes": _nonempty_hash_list,
            "decisive_event_hashes": _unique_hash_list,
            "advisory_event_hashes": _unique_hash_list,
            "ordinal_state": _enum(
                "falsified", "inconclusive", "partial_support", "supported"
            ),
            "reason_codes": _reason_codes,
            "warning_codes": _reason_codes,
            "limitation_codes": _reason_codes,
        },
    ),
    "ComplementEvidenceRecorded": PayloadSpec(
        "complement_evidence_recorded.v2",
        {
            "complement_id": _string,
            "factor_spec_id": _string,
            "complement_hash": _hash,
            "policy_version": _string,
            "policy_hash": _hash,
            "data_scope": _enum("train_valid", "valid"),
            "snapshot_hash": _hash,
            "source_evaluation_event_hash": _hash,
            "source_terminal_event_hash": _hash,
            "pool_factor_spec_ids": _string_list,
            "identity_hash": _hash,
            "residual_hash": _hash,
            "portfolio_hash": _hash,
            "portfolio_construction_hash": _hash,
            "cost_model_hash": _hash,
            "capacity_model_hash": _hash,
            "exposure_model_hash": _hash,
            "status": _enum(
                "duplicate",
                "unavailable",
                "insufficient",
                "nonpositive_marginal_value",
                "complementary",
            ),
            "cap": _nullable_string,
            "artifact_refs": _artifact_list,
        },
    ),
    "QualityDecisionRecorded": PayloadSpec(
        "quality_decision_recorded.v1",
        {
            "decision_id": _string,
            "factor_spec_id": _string,
            "decision": _DECISION,
            "policy_hash": _hash,
            "evidence_hashes": _string_list,
            "reasons": _reason_codes,
            "warnings": _reason_codes,
            "caps": _reason_codes,
            "limitations": _string_list,
        },
    ),
    "QualityDecisionV2Recorded": PayloadSpec(
        "quality_decision_recorded.v2",
        {
            "decision_id": _string,
            "factor_spec_id": _string,
            "decision_hash": _hash,
            "decision": _enum(
                "reject",
                "research_only",
                "candidate_zoo",
                "paper_candidate",
                "forward_track",
            ),
            "tier": _nonnegative_integer,
            "policy_version": _string,
            "policy_hash": _hash,
            "scorecard_hash": _hash,
            "execution_hash": _nullable_hash,
            "snapshot_hash": _nullable_hash,
            "ledger_watermark_hash": _hash,
            "mechanism_evidence_hash": _nullable_hash,
            "complement_evidence_hash": _nullable_hash,
            "final_test_artifact_hash": _nullable_hash,
            "forward_plan_hash": _nullable_hash,
            "evidence_hashes": _nonempty_hash_list,
            "reasons": _reason_codes,
            "warnings": _reason_codes,
            "caps": _reason_codes,
            "limitations": _string_list,
            "within_tier_score": _finite_number,
            "forward_success_claim": _boolean,
            "artifact_refs": _artifact_list,
        },
    ),
    "QualityDecisionV3Recorded": PayloadSpec(
        "quality_decision_recorded.v3",
        {
            "decision_id": _string,
            "decision_hash": _hash,
            "quality_decision_hash": _hash,
            "input_bundle_hash": _hash,
            "factor_spec_id": _string,
            "decision": _enum(
                "reject",
                "research_only",
                "candidate_zoo",
                "paper_candidate",
                "forward_track",
            ),
            "tier": _nonnegative_integer,
            "policy_version": _string,
            "policy_hash": _hash,
            "evidence_hashes": _nonempty_hash_list,
            "reasons": _reason_codes,
            "warnings": _reason_codes,
            "caps": _reason_codes,
            "limitations": _string_list,
            "within_tier_score": _finite_number,
            "forward_success_claim": _boolean,
            "artifact_refs": _artifact_list,
        },
    ),
    "FinalCandidateFrozen": PayloadSpec(
        "final_candidate_frozen.v1",
        {
            "freeze_id": _string,
            "candidate_schema_version": _enum("frozen_final_candidate.v1"),
            "factor_spec_id": _string,
            "definition_hash": _hash,
            "transform_pipeline_hash": _hash,
            "cost_model_hash": _hash,
            "regime_config_hash": _hash,
            "policy_hash": _hash,
            "data_snapshot_hash": _hash,
            "frozen_at": _timestamp,
            "candidate_hash": _hash,
        },
    ),
    "FinalTestCapabilityIssued": PayloadSpec(
        "final_test_capability_issued.v1",
        {
            "capability_id": _string,
            "capability_fingerprint": _hash,
            "candidate_hash": _hash,
            "factor_spec_id": _string,
            "declared_run_id": _string,
            "data_snapshot_hash": _hash,
            "period_start": _date,
            "period_end": _date,
            "allowed_fields": _nonempty_string_list,
            "issued_at": _timestamp,
        },
    ),
    "FinalTestAccessRecorded": PayloadSpec(
        "final_test_access_recorded.v1",
        {
            "access_id": _string,
            "capability_fingerprint": _hash,
            "candidate_hash": _hash,
            "factor_spec_id": _string,
            "declared_run_id": _string,
            "request_hash": _hash,
            "outcome": _enum("allowed", "denied"),
            "reason_code": _string,
            "contaminated": _boolean,
            "accessed_at": _timestamp,
        },
    ),
    "FinalTestArtifactRecorded": PayloadSpec(
        "final_test_artifact_recorded.v1",
        {
            "artifact_id": _string,
            "artifact_hash": _hash,
            "factor_spec_id": _string,
            "candidate_hash": _hash,
            "definition_hash": _hash,
            "transform_pipeline_hash": _hash,
            "cost_model_hash": _hash,
            "regime_config_hash": _hash,
            "access_event_hash": _hash,
            "policy_hash": _hash,
            "data_snapshot_hash": _hash,
            "effective_observations": _positive_integer,
            "quality_passed": _boolean,
            "contaminated": _boolean,
            "artifact_refs": _artifact_list,
        },
    ),
    "ForwardPlanV2Recorded": PayloadSpec(
        "forward_plan_recorded.v2",
        {
            "plan_schema_version": _enum("frozen_forward_plan.v2"),
            "plan_id": _string,
            "factor_spec_id": _string,
            "final_test_artifact_hash": _hash,
            "definition_hash": _hash,
            "transform_pipeline_hash": _hash,
            "cost_model_hash": _hash,
            "regime_config_hash": _hash,
            "policy_hash": _hash,
            "expected_horizon": _positive_integer,
            "minimum_effective_observations": _positive_integer,
            "minimum_rank_ic": _finite_number,
            "maximum_drawdown": _positive_finite_number,
            "kill_rules_hash": _hash,
            "created_at": _timestamp,
            "plan_hash": _hash,
            "artifact_refs": _artifact_list,
        },
    ),
    "ForwardObservationV2Recorded": PayloadSpec(
        "forward_observation_recorded.v2",
        {
            "observation_schema_version": _enum("forward_observation.v2"),
            "observation_id": _string,
            "plan_id": _string,
            "plan_hash": _hash,
            "period_start": _date,
            "period_end": _date,
            "effective_observations": _positive_integer,
            "rank_ic": _finite_number,
            "net_return": _finite_number,
            "drawdown": _finite_number,
            "previous_observation_hash": _nullable_hash,
            "observed_at": _timestamp,
            "observation_hash": _hash,
            "artifact_refs": _artifact_list,
        },
    ),
    "ForwardPlanRecorded": PayloadSpec(
        "forward_plan_recorded.v1",
        {
            "plan_id": _string,
            "factor_spec_id": _string,
            "plan_hash": _hash,
            "minimum_observations": _nonnegative_integer,
            "policy_hash": _hash,
        },
    ),
    "ForwardObservationRecorded": PayloadSpec(
        "forward_observation_recorded.v1",
        {
            "observation_id": _string,
            "plan_id": _string,
            "period_start": _timestamp,
            "period_end": _timestamp,
            "observation_hash": _hash,
            "previous_observation_hash": _nullable_hash,
            "artifact_refs": _artifact_list,
        },
    ),
}
PAYLOAD_SPECS: Mapping[str, PayloadSpec] = MappingProxyType(_PAYLOAD_SPECS)


def _check_finite_and_bounds(value: Any, *, path: str = "payload", depth: int = 0) -> int:
    if depth > _MAX_DEPTH:
        raise EventValidationError("payload exceeds maximum depth")
    nodes = 1
    if isinstance(value, float) and not math.isfinite(value):
        raise EventValidationError(f"{path} contains a non-finite value")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise EventValidationError(f"{path} keys must be strings")
            nodes += _check_finite_and_bounds(item, path=f"{path}.{key}", depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            nodes += _check_finite_and_bounds(item, path=f"{path}[{index}]", depth=depth + 1)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise EventValidationError(f"{path} contains unsupported JSON type {type(value).__name__}")
    if nodes > _MAX_NODES:
        raise EventValidationError("payload exceeds maximum node count")
    return nodes


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


def _event_redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in _EVENT_SENSITIVE_KEYS:
                redacted[str(key)] = "[redacted]"
            else:
                redacted[str(key)] = _event_redact(item)
        return redacted
    if isinstance(value, list):
        return [_event_redact(item) for item in value]
    if isinstance(value, str) and _EVENT_PRIVATE_PATH_RE.match(value):
        return "[redacted]"
    return value


def validate_and_redact_payload(
    event_type: str,
    payload_schema_version: str,
    raw_payload: Mapping[str, Any],
) -> dict[str, Any]:
    spec = PAYLOAD_SPECS.get(event_type)
    if spec is None:
        raise EventValidationError(f"unknown event type: {event_type}")
    if payload_schema_version != spec.version:
        raise EventValidationError(
            f"invalid payload schema version for {event_type}: {payload_schema_version}"
        )
    if not isinstance(raw_payload, Mapping):
        raise EventValidationError("payload must be an object")
    _check_finite_and_bounds(raw_payload)
    payload = _event_redact(redact_secrets(_plain_json(raw_payload)))
    expected = set(spec.fields)
    supplied = set(payload)
    unknown = sorted(supplied - expected)
    missing = sorted(expected - supplied)
    if unknown:
        raise EventValidationError(f"unknown payload fields: {unknown}")
    if missing:
        raise EventValidationError(f"missing payload fields: {missing}")
    for name, validator in spec.fields.items():
        validator(payload[name], f"payload.{name}")
    encoded = canonical_json(payload).encode("utf-8")
    if len(encoded) > _MAX_PAYLOAD_BYTES:
        raise EventValidationError("payload exceeds maximum encoded size")
    _validate_cross_field_rules(event_type, payload)
    return payload


def _validate_cross_field_rules(event_type: str, payload: Mapping[str, Any]) -> None:
    if event_type == "TrialTerminated":
        status = payload["status"]
        decision = payload["decision"]
        if status in {"skip", "invalid", "duplicate", "timeout", "error", "infrastructure_failure"}:
            if decision not in {"none", "reject", "research_only"}:
                raise EventValidationError(f"{status} terminal outcome cannot promote a candidate")
        if status == "reject" and decision not in {"none", "reject"}:
            raise EventValidationError("reject terminal outcome cannot promote a candidate")
        if status == "success" and decision == "none":
            raise EventValidationError("success terminal outcome requires a research decision")
    if event_type == "ForwardPlanRecorded" and payload["minimum_observations"] <= 0:
        raise EventValidationError("minimum_observations must be positive")
    if event_type == "RetrieverDecisionV2Recorded":
        if not payload["shadow_only"]:
            raise EventValidationError("retriever v2 is shadow-only before activation")
        selected = [
            component["factor_spec_id"]
            for component in payload["components"] if component["selected"]
        ]
        if set(selected) != set(payload["selected_factor_spec_ids"]):
            raise EventValidationError("retriever selected IDs do not match its components")
        if len(payload["selected_factor_spec_ids"]) != len(set(payload["selected_factor_spec_ids"])):
            raise EventValidationError("retriever selected IDs must be unique")
        if len(selected) > payload["candidate_budget"]:
            raise EventValidationError("retriever selection exceeds its frozen budget")
        if payload["candidate_budget"] > payload["policy_config"]["maximum_candidate_budget"]:
            raise EventValidationError("retriever budget exceeds its frozen policy")
        if payload["policy_version"] != payload["policy_config"]["policy_version"]:
            raise EventValidationError("retriever policy version is inconsistent")
        if canonical_json_hash(payload["policy_config"]) != payload["policy_hash"]:
            raise EventValidationError("retriever policy hash is inconsistent")
        decision_content = {
            "schema_version": "retriever_shadow_decision.v2",
            "selected_factor_spec_ids": payload["selected_factor_spec_ids"],
            "seed": payload["seed"],
            "policy_version": payload["policy_version"],
            "policy_hash": payload["policy_hash"],
            "policy_config": payload["policy_config"],
            "eligible_event_watermark": payload["eligible_event_watermark"],
            "data_snapshot_hash": payload["data_snapshot_hash"],
            "candidate_budget": payload["candidate_budget"],
            "official_output_hash": payload["official_output_hash"],
            "propensity_semantics": payload["propensity_semantics"],
            "components": payload["components"],
            "shadow_only": payload["shadow_only"],
        }
        if canonical_json_hash(decision_content) != payload["decision_hash"]:
            raise EventValidationError("retriever v2 decision hash is invalid")
    if event_type == "RetrieverDecisionV3Recorded":
        try:
            from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy

            policy = ActivationRetrieverPolicy(**dict(payload["policy_config"]))
        except (TypeError, ValueError) as exc:
            raise EventValidationError("retriever v3 policy config is invalid") from exc
        if policy.policy_hash != payload["policy_hash"]:
            raise EventValidationError("retriever v3 policy hash is inconsistent")
        if policy.policy_version != payload["policy_version"]:
            raise EventValidationError("retriever v3 policy version is inconsistent")
        if not payload["shadow_only"]:
            raise EventValidationError("retriever v3 remains shadow-only before approval")
        selected = [
            component["factor_spec_id"]
            for component in payload["components"] if component["selected"]
        ]
        if set(selected) != set(payload["selected_factor_spec_ids"]):
            raise EventValidationError("retriever v3 selected IDs differ from components")
        if len(selected) != len(set(selected)) or len(selected) > payload["candidate_budget"]:
            raise EventValidationError("retriever v3 selection is duplicated or over budget")
        decision_content = {
            "schema_version": "retriever_source_bound_decision.v3",
            "shadow_decision_hash": payload["shadow_decision_hash"],
            "input_bundle_hash": payload["input_bundle_hash"],
            "selected_factor_spec_ids": payload["selected_factor_spec_ids"],
            "seed": payload["seed"],
            "policy_version": payload["policy_version"],
            "policy_hash": payload["policy_hash"],
            "policy_config": payload["policy_config"],
            "eligible_event_watermark": payload["eligible_event_watermark"],
            "data_snapshot_hash": payload["data_snapshot_hash"],
            "candidate_budget": payload["candidate_budget"],
            "official_output_hash": payload["official_output_hash"],
            "propensity_semantics": payload["propensity_semantics"],
            "components": payload["components"],
            "shadow_only": payload["shadow_only"],
        }
        if canonical_json_hash(decision_content) != payload["decision_hash"]:
            raise EventValidationError("retriever v3 source-bound decision hash is invalid")
    if event_type == "RetrieverDecisionV4Recorded":
        expected_identifier = (
            "retriever-v4-"
            + str(payload["decision_hash"]).removeprefix("sha256:")[:20]
        )
        if payload["decision_id"] != expected_identifier:
            raise EventValidationError(
                "retriever v4 identity must derive from its decision hash"
            )
        try:
            from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy

            policy = ActivationRetrieverPolicy(**dict(payload["policy_config"]))
        except (TypeError, ValueError) as exc:
            raise EventValidationError("retriever v4 policy config is invalid") from exc
        if (
            policy.policy_hash != payload["policy_hash"]
            or policy.policy_version != payload["policy_version"]
            or not payload["shadow_only"]
        ):
            raise EventValidationError("retriever v4 policy or shadow state is invalid")
        selected = [
            component["factor_spec_id"]
            for component in payload["components"] if component["selected"]
        ]
        if (
            set(selected) != set(payload["selected_factor_spec_ids"])
            or len(selected) != len(set(selected))
            or len(selected) > payload["candidate_budget"]
        ):
            raise EventValidationError("retriever v4 selection is inconsistent")
        decision_content = {
            "schema_version": "retriever_source_bound_decision.v4",
            "shadow_decision_hash": payload["shadow_decision_hash"],
            "input_bundle_hash": payload["input_bundle_hash"],
            "control_evidence_event_hash": payload["control_evidence_event_hash"],
            "control_evidence_hash": payload["control_evidence_hash"],
            "control_policy_hash": payload["control_policy_hash"],
            "selected_factor_spec_ids": payload["selected_factor_spec_ids"],
            "seed": payload["seed"],
            "policy_version": payload["policy_version"],
            "policy_hash": payload["policy_hash"],
            "policy_config": payload["policy_config"],
            "eligible_event_watermark": payload["eligible_event_watermark"],
            "data_snapshot_hash": payload["data_snapshot_hash"],
            "candidate_budget": payload["candidate_budget"],
            "official_output_hash": payload["official_output_hash"],
            "propensity_semantics": payload["propensity_semantics"],
            "components": payload["components"],
            "shadow_only": payload["shadow_only"],
        }
        if canonical_json_hash(decision_content) != payload["decision_hash"]:
            raise EventValidationError("retriever v4 source-bound decision hash is invalid")
    if event_type == "RetrieverDecisionV5Recorded":
        expected_identifier = (
            "retriever-v5-"
            + str(payload["decision_hash"]).removeprefix("sha256:")[:20]
        )
        if payload["decision_id"] != expected_identifier:
            raise EventValidationError(
                "retriever v5 identity must derive from its decision hash"
            )
        try:
            from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy

            policy = ActivationRetrieverPolicy(**dict(payload["policy_config"]))
        except (TypeError, ValueError) as exc:
            raise EventValidationError("retriever v5 policy config is invalid") from exc
        if (
            policy.policy_hash != payload["policy_hash"]
            or policy.policy_version != payload["policy_version"]
            or not payload["shadow_only"]
        ):
            raise EventValidationError("retriever v5 policy or shadow state is invalid")
        selected_components = {
            component["action_id"]: component["factor_spec_id"]
            for component in payload["components"]
            if component["selected"]
        }
        selected_actions = list(payload["selected_action_ids"])
        selected_parents = list(payload["selected_parent_factor_spec_ids"])
        if (
            len(selected_actions) != len(set(selected_actions))
            or len(selected_actions) != len(selected_parents)
            or len(selected_actions) > payload["candidate_budget"]
            or set(selected_actions) != set(selected_components)
            or any(
                selected_components.get(action_id) != parent_id
                for action_id, parent_id in zip(
                    selected_actions, selected_parents, strict=True
                )
            )
            or len(payload["action_template_event_hashes"])
            != len(payload["components"])
        ):
            raise EventValidationError("retriever v5 action selection is inconsistent")
        decision_content = {
            "schema_version": "retriever_action_source_bound_decision.v5",
            "shadow_decision_hash": payload["shadow_decision_hash"],
            "input_bundle_hash": payload["input_bundle_hash"],
            "control_evidence_event_hash": payload["control_evidence_event_hash"],
            "control_evidence_hash": payload["control_evidence_hash"],
            "control_policy_hash": payload["control_policy_hash"],
            "selected_action_ids": payload["selected_action_ids"],
            "selected_parent_factor_spec_ids": payload[
                "selected_parent_factor_spec_ids"
            ],
            "action_template_event_hashes": payload[
                "action_template_event_hashes"
            ],
            "seed": payload["seed"],
            "policy_version": payload["policy_version"],
            "policy_hash": payload["policy_hash"],
            "policy_config": payload["policy_config"],
            "eligible_event_watermark": payload["eligible_event_watermark"],
            "data_snapshot_hash": payload["data_snapshot_hash"],
            "candidate_budget": payload["candidate_budget"],
            "official_output_hash": payload["official_output_hash"],
            "propensity_semantics": payload["propensity_semantics"],
            "components": payload["components"],
            "shadow_only": payload["shadow_only"],
        }
        if canonical_json_hash(decision_content) != payload["decision_hash"]:
            raise EventValidationError("retriever v5 source-bound decision hash is invalid")
    if event_type == "TrainValidDataSnapshotFrozen":
        expected_identifier = (
            "train-valid-snapshot-v1-"
            + str(payload["snapshot_hash"]).removeprefix("sha256:")[:24]
        )
        if payload["snapshot_id"] != expected_identifier:
            raise EventValidationError(
                "train/valid snapshot identity must derive from its hash"
            )
        frame_names = list(payload["frame_names"])
        if (
            frame_names != sorted(set(frame_names))
            or frame_names != list(payload["frame_content_hashes"])
        ):
            raise EventValidationError(
                "train/valid snapshot frame inventory is inconsistent"
            )
    if event_type == "RetrieverFeatureSourceRecorded":
        expected_identifier = (
            "retriever-feature-source-v1-"
            + str(payload["source_hash"]).removeprefix("sha256:")[:24]
        )
        if payload["feature_source_id"] != expected_identifier:
            raise EventValidationError(
                "Retriever feature source identity must derive from its hash"
            )
        if (
            payload["candidate_count"] != len(payload["action_event_hashes"])
            or payload["candidate_count"] != len(payload["candidate_hashes"])
        ):
            raise EventValidationError(
                "Retriever feature source candidate inventory is inconsistent"
            )
    if event_type == "ActivationRunRecorded":
        attempted = sum(int(value) for value in payload["terminal_status_counts"].values())
        if payload["complete"] and attempted == 0:
            raise EventValidationError("complete activation run must contain terminal attempts")
    if event_type == "ActivationResultRecorded":
        if payload["invalidation_reasons"] and payload["replayable"]:
            raise EventValidationError("invalidated activation result cannot be replayable")
    if event_type == "RetrieverActivationDecisionRecorded":
        if payload["active_research_only"] != (payload["verdict"] == "approved"):
            raise EventValidationError("only approved activation may enable research influence")
    if event_type == "SequentialProtocolRegistered":
        if payload["maximum_looks"] < 2:
            raise EventValidationError("sequential protocol requires at least two looks")
        alphas = (
            float(payload["family_alpha"]),
            float(payload["support_alpha"]),
            float(payload["contradiction_alpha"]),
        )
        if any(not 0.0 < value < 0.5 for value in alphas):
            raise EventValidationError("sequential alpha allocations must be in (0, 0.5)")
        if alphas[1] + alphas[2] > alphas[0] + 1e-15:
            raise EventValidationError("sequential channel alpha exceeds family alpha")
        if not math.isclose(
            float(payload["support_log_boundary"]),
            -math.log(alphas[1]),
            rel_tol=0.0,
            abs_tol=1e-12,
        ) or not math.isclose(
            float(payload["contradiction_log_boundary"]),
            -math.log(alphas[2]),
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise EventValidationError("sequential log boundaries must match frozen alpha")
        lambdas = tuple(float(value) for value in payload["lambda_grid"])
        weights = tuple(float(value) for value in payload["mixture_weights"])
        if len(lambdas) != len(weights):
            raise EventValidationError("sequential lambda and mixture lists must align")
        if any(not 0.0 < value < 1.0 for value in lambdas) or len(set(lambdas)) != len(lambdas):
            raise EventValidationError("sequential lambda grid must be unique and in (0, 1)")
        if any(value <= 0.0 for value in weights) or not math.isclose(
            math.fsum(weights), 1.0, rel_tol=0.0, abs_tol=1e-12
        ):
            raise EventValidationError("sequential mixture weights must be positive and sum to one")
    if event_type == "SequentialLookRecorded":
        support_components = payload["support_component_log_capitals"]
        contradiction_components = payload["contradiction_component_log_capitals"]
        if len(support_components) != len(contradiction_components):
            raise EventValidationError("sequential component capital lists must have equal length")
        if len(payload["unit_hashes"]) != payload["incremental_information"]:
            raise EventValidationError(
                "incremental_information must equal the number of unique unit hashes"
            )
        status = payload["status"]
        reason = payload["stop_reason"]
        expected_reason = {
            "continue": "NONE",
            "support_boundary_crossed": "SUPPORT_BOUNDARY",
            "contradiction_boundary_crossed": "CONTRADICTION_BOUNDARY",
            "max_looks_reached": "MAX_LOOKS",
        }[status]
        if reason != expected_reason:
            raise EventValidationError("sequential look status and stop reason do not match")
        support_crossed = (
            payload["cumulative_support_log_e"] >= payload["support_log_boundary"]
        )
        contradiction_crossed = (
            payload["cumulative_contradiction_log_e"]
            >= payload["contradiction_log_boundary"]
        )
        if support_crossed and contradiction_crossed:
            raise EventValidationError("sequential look cannot cross both opposing boundaries")
        if status == "support_boundary_crossed" and not support_crossed:
            raise EventValidationError("support boundary status requires a crossed boundary")
        if status == "contradiction_boundary_crossed" and not contradiction_crossed:
            raise EventValidationError("contradiction boundary status requires a crossed boundary")
        if status in {"continue", "max_looks_reached"} and (
            support_crossed or contradiction_crossed
        ):
            raise EventValidationError("uncrossed sequential status cannot carry crossed evidence")
    if event_type == "MechanismEvidenceIndexRecorded":
        result_hashes = payload["source_result_hashes"]
        sources = set(payload["source_event_hashes"])
        decisive = set(payload["decisive_event_hashes"])
        advisory = set(payload["advisory_event_hashes"])
        if len(result_hashes) != len(sources):
            raise EventValidationError("MEI result and event source counts must match")
        if decisive & advisory:
            raise EventValidationError("MEI decisive and advisory result references must be disjoint")
        if decisive | advisory != sources:
            raise EventValidationError("MEI roles must partition every source result reference")
        required_reason_by_state = {
            "falsified": {"DECISIVE_MECHANISM_CONTRADICTION"},
            "supported": {"ALL_INCLUDED_EVIDENCE_SUPPORTED"},
            "partial_support": {"DECISIVE_SUPPORT_WITH_ADVISORY_GAPS"},
            "inconclusive": {
                "NO_DECISIVE_EVIDENCE",
                "DECISIVE_EVIDENCE_MISSING",
                "DECISIVE_TEST_UNAVAILABLE",
                "DECISIVE_TEST_LOW_POWER",
                "DECISIVE_TEST_INCONCLUSIVE",
            },
        }
        if not set(payload["reason_codes"]).intersection(
            required_reason_by_state[payload["ordinal_state"]]
        ):
            raise EventValidationError("MEI state requires a versioned truth-table reason")
    if event_type == "ComplementEvidenceRecorded":
        missing_status = payload["status"] in {"unavailable", "insufficient"}
        if missing_status and payload["cap"] != "RESEARCH_ONLY":
            raise EventValidationError("missing complement evidence must cap research_only")
        if not missing_status and payload["cap"] is not None:
            raise EventValidationError("complete complement evidence cannot carry a cap")
    if event_type == "QualityDecisionV2Recorded":
        decision = payload["decision"]
        expected_tier = {
            "reject": 0,
            "research_only": 1,
            "candidate_zoo": 2,
            "paper_candidate": 3,
            "forward_track": 4,
        }[decision]
        if payload["tier"] != expected_tier:
            raise EventValidationError("Decision v2 tier does not match decision")
        if decision == "reject" and not payload["reasons"]:
            raise EventValidationError("Decision v2 reject requires exact reasons")
        if decision == "research_only" and not payload["caps"]:
            raise EventValidationError("Decision v2 research_only requires exact caps")
        if decision in {"paper_candidate", "forward_track"} and (
            payload["final_test_artifact_hash"] is None
        ):
            raise EventValidationError("paper tiers require a frozen final-test artifact")
        if decision == "forward_track" and payload["forward_plan_hash"] is None:
            raise EventValidationError("forward_track requires a frozen plan")
        if payload["forward_success_claim"]:
            raise EventValidationError("Decision v2 cannot claim forward success")
        reference_hashes = {
            payload[name]
            for name in (
                "scorecard_hash",
                "execution_hash",
                "snapshot_hash",
                "ledger_watermark_hash",
                "mechanism_evidence_hash",
                "complement_evidence_hash",
                "final_test_artifact_hash",
                "forward_plan_hash",
            )
            if payload[name] is not None
        }
        if payload["evidence_hashes"] != sorted(reference_hashes):
            raise EventValidationError("Decision v2 evidence hashes must match its closed refs")
        decision_content = {
            "schema_version": "alpha_quality_decision.v2",
            "factor_spec_id": payload["factor_spec_id"],
            "decision": decision,
            "tier": payload["tier"],
            "policy_version": payload["policy_version"],
            "policy_hash": payload["policy_hash"],
            "evidence_hashes": payload["evidence_hashes"],
            "reasons": payload["reasons"],
            "warnings": payload["warnings"],
            "caps": payload["caps"],
            "limitations": payload["limitations"],
            "within_tier_score": payload["within_tier_score"],
            "forward_success_claim": payload["forward_success_claim"],
        }
        if canonical_json_hash(decision_content) != payload["decision_hash"]:
            raise EventValidationError("Decision v2 hash does not match deterministic content")
    if event_type == "QualityDecisionV3Recorded":
        decision = payload["decision"]
        expected_tier = {
            "reject": 0,
            "research_only": 1,
            "candidate_zoo": 2,
            "paper_candidate": 3,
            "forward_track": 4,
        }[decision]
        if payload["tier"] != expected_tier:
            raise EventValidationError("Decision v3 tier does not match decision")
        if decision == "reject" and not payload["reasons"]:
            raise EventValidationError("Decision v3 reject requires exact reasons")
        if decision == "research_only" and not payload["caps"]:
            raise EventValidationError("Decision v3 research_only requires exact caps")
        if payload["forward_success_claim"]:
            raise EventValidationError("Decision v3 cannot claim forward success")
        decision_content = {
            "schema_version": "quality_decision_source_bound.v3",
            "quality_decision_hash": payload["quality_decision_hash"],
            "input_bundle_hash": payload["input_bundle_hash"],
            "factor_spec_id": payload["factor_spec_id"],
            "decision": decision,
            "tier": payload["tier"],
            "policy_version": payload["policy_version"],
            "policy_hash": payload["policy_hash"],
            "evidence_hashes": payload["evidence_hashes"],
            "reasons": payload["reasons"],
            "warnings": payload["warnings"],
            "caps": payload["caps"],
            "limitations": payload["limitations"],
            "within_tier_score": payload["within_tier_score"],
            "forward_success_claim": payload["forward_success_claim"],
        }
        if canonical_json_hash(decision_content) != payload["decision_hash"]:
            raise EventValidationError("Decision v3 hash does not match source-bound content")
    if event_type == "ActivationRunSourceAudited":
        required = {
            "RESOURCE_METRICS_SOURCE_UNBOUND",
            "QUALITY_DECISION_UPSTREAM_AUTHORITY_UNPROVEN",
        }
        if payload["source_complete"] or not required.issubset(
            payload["source_failure_codes"]
        ):
            raise EventValidationError(
                "Activation source v2 must retain its unavailable production sources"
            )
    if event_type == "OfficialSearchControlRecorded" and (
        payload["candidate_count"] != len(payload["terminal_event_hashes"])
    ):
        raise EventValidationError(
            "official control terminals must cover every generated candidate"
        )
    if event_type == "ActivationResourceMeasured" and (
        payload["peak_rss_mb"] is not None
        or payload["source_complete"]
        or payload["source_failure_codes"]
        != [
            "ARM_ORDER_NOT_COUNTERBALANCED",
            "EXECUTOR_TIMEOUT_NOT_ENFORCED",
            "PEAK_RSS_ISOLATED_MEASUREMENT_UNAVAILABLE",
        ]
    ):
        raise EventValidationError(
            "Activation resource v1 must retain all runner limitations"
        )
    if event_type == "ActivationResourceMeasured":
        expected_resource_id = (
            "activation-resource-v1-"
            + str(payload["evidence_hash"]).removeprefix("sha256:")[:24]
        )
        if payload["resource_id"] != expected_resource_id:
            raise EventValidationError(
                "Activation resource identity must derive from its evidence hash"
            )
    if event_type == "ActivationGenerationConsumptionRecorded":
        expected_identifier = (
            "activation-generation-v1-"
            + str(payload["evidence_hash"]).removeprefix("sha256:")[:24]
        )
        if payload["generation_id"] != expected_identifier:
            raise EventValidationError(
                "Activation generation identity must derive from evidence"
            )
        if payload["source_complete"] != (not payload["source_failure_codes"]):
            raise EventValidationError(
                "Activation generation completeness must derive from failures"
            )
        if payload["generated_candidate_count"] > payload["candidate_budget"]:
            raise EventValidationError(
                "Activation generation exceeds its frozen candidate budget"
            )
    if event_type == "ActivationGenerationConsumptionV2Recorded":
        expected_identifier = (
            "activation-generation-v2-"
            + str(payload["evidence_hash"]).removeprefix("sha256:")[:24]
        )
        if payload["generation_id"] != expected_identifier:
            raise EventValidationError(
                "Activation exact generation identity must derive from evidence"
            )
        if payload["source_complete"] != (not payload["source_failure_codes"]):
            raise EventValidationError(
                "Activation exact generation completeness must derive from failures"
            )
        selected_actions = list(payload["selected_action_ids"])
        selected_events = list(payload["selected_action_event_hashes"])
        selected_parents = list(payload["selected_parent_factor_spec_ids"])
        consumed_actions = list(payload["consumed_action_ids"])
        consumed_parents = list(payload["consumed_parent_factor_spec_ids"])
        if (
            len(selected_actions) != len(set(selected_actions))
            or len(selected_actions) != len(selected_events)
            or len(selected_actions) != len(selected_parents)
            or len(consumed_actions) != len(set(consumed_actions))
            or len(consumed_actions) != len(consumed_parents)
            or payload["generated_candidate_count"] != len(consumed_actions)
            or payload["generated_candidate_count"] > payload["candidate_budget"]
        ):
            raise EventValidationError(
                "Activation exact generation action binding is inconsistent"
            )
    if event_type == "FinalCandidateFrozen":
        candidate_content = {
            "schema_version": payload["candidate_schema_version"],
            "factor_spec_id": payload["factor_spec_id"],
            "definition_hash": payload["definition_hash"],
            "transform_pipeline_hash": payload["transform_pipeline_hash"],
            "cost_model_hash": payload["cost_model_hash"],
            "regime_config_hash": payload["regime_config_hash"],
            "policy_hash": payload["policy_hash"],
            "data_snapshot_hash": payload["data_snapshot_hash"],
            "frozen_at": payload["frozen_at"],
        }
        if canonical_json_hash(candidate_content) != payload["candidate_hash"]:
            raise EventValidationError("frozen final candidate hash mismatch")
    if event_type == "FinalTestCapabilityIssued":
        if payload["period_end"] < payload["period_start"]:
            raise EventValidationError("final capability period is reversed")
        if payload["allowed_fields"] != sorted(set(payload["allowed_fields"])):
            raise EventValidationError("final allowed fields must be sorted and unique")
    if event_type == "FinalTestAccessRecorded":
        if payload["outcome"] == "allowed" and payload["contaminated"]:
            raise EventValidationError("allowed final access cannot already be contaminated")
        if payload["outcome"] == "denied" and not payload["contaminated"]:
            raise EventValidationError("denied final access must taint the candidate")
    if event_type == "FinalTestArtifactRecorded":
        if payload["contaminated"] and payload["quality_passed"]:
            raise EventValidationError("contaminated final artifact cannot pass quality")
    if event_type == "ForwardPlanV2Recorded":
        plan_content = {
            "schema_version": payload["plan_schema_version"],
            "factor_spec_id": payload["factor_spec_id"],
            "final_test_artifact_hash": payload["final_test_artifact_hash"],
            "definition_hash": payload["definition_hash"],
            "transform_pipeline_hash": payload["transform_pipeline_hash"],
            "cost_model_hash": payload["cost_model_hash"],
            "regime_config_hash": payload["regime_config_hash"],
            "policy_hash": payload["policy_hash"],
            "expected_horizon": payload["expected_horizon"],
            "minimum_effective_observations": payload["minimum_effective_observations"],
            "minimum_rank_ic": payload["minimum_rank_ic"],
            "maximum_drawdown": payload["maximum_drawdown"],
            "kill_rules_hash": payload["kill_rules_hash"],
            "created_at": payload["created_at"],
        }
        if canonical_json_hash(plan_content) != payload["plan_hash"]:
            raise EventValidationError("frozen forward plan hash mismatch")
        expected_id = "forward-plan-" + payload["plan_hash"].removeprefix("sha256:")[:24]
        if payload["plan_id"] != expected_id:
            raise EventValidationError("forward plan ID does not derive from plan hash")
    if event_type == "ForwardObservationV2Recorded":
        if payload["period_end"] < payload["period_start"] or payload["drawdown"] < 0.0:
            raise EventValidationError("invalid forward observation period or drawdown")
        observation_content = {
            "schema_version": payload["observation_schema_version"],
            "observation_id": payload["observation_id"],
            "plan_id": payload["plan_id"],
            "plan_hash": payload["plan_hash"],
            "period_start": payload["period_start"],
            "period_end": payload["period_end"],
            "effective_observations": payload["effective_observations"],
            "rank_ic": payload["rank_ic"],
            "net_return": payload["net_return"],
            "drawdown": payload["drawdown"],
            "previous_observation_hash": payload["previous_observation_hash"],
            "observed_at": payload["observed_at"],
        }
        if canonical_json_hash(observation_content) != payload["observation_hash"]:
            raise EventValidationError("forward observation hash mismatch")


def envelope_diagnostics(
    event_type: str,
    payload: Mapping[str, Any],
    *,
    reduced_durability: bool,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    warnings: set[str] = set()
    hard_failures: set[str] = set()
    if reduced_durability:
        warnings.add("REDUCED_DURABILITY")
    if event_type == "GenerationFailureRecorded":
        hard_failures.add(str(payload["failure_code"]))
    if event_type in {
        "RetrieverDecisionV2Recorded", "RetrieverDecisionV3Recorded",
        "RetrieverDecisionV4Recorded", "RetrieverDecisionV5Recorded",
    }:
        warnings.add("TOPOLOGY_RETRIEVER_SHADOW_ONLY")
    if event_type == "ActivationResultRecorded" and payload["invalidation_reasons"]:
        hard_failures |= {str(code) for code in payload["invalidation_reasons"]}
    if event_type == "RetrieverActivationDecisionRecorded":
        if payload["verdict"] != "approved":
            warnings.add("TOPOLOGY_RETRIEVER_NOT_ACTIVATED")
        if payload["verdict"] in {"rejected", "invalidated"}:
            hard_failures |= {str(code) for code in payload["reasons"]}
    if event_type == "TrialTerminated":
        codes = {str(code) for code in payload["reason_codes"]}
        if payload["status"] == "skip":
            warnings |= codes
        elif payload["status"] != "success":
            hard_failures |= codes
    if event_type == "FalsificationResultRecorded" and payload["outcome"] == "inconclusive":
        warnings.add("FALSIFICATION_INCONCLUSIVE")
    if event_type == "MechanismEvidenceIndexRecorded":
        if payload["ordinal_state"] == "inconclusive":
            warnings.add("MECHANISM_EVIDENCE_INCONCLUSIVE")
        elif payload["ordinal_state"] == "falsified":
            hard_failures.add("MECHANISM_FALSIFIED")
    if event_type == "ComplementEvidenceRecorded":
        if payload["status"] == "duplicate":
            hard_failures.add("COMPLEMENT_DUPLICATE_IDENTITY")
        elif payload["status"] in {"unavailable", "insufficient"}:
            warnings.add("COMPLEMENT_EVIDENCE_INCONCLUSIVE")
        elif payload["status"] == "nonpositive_marginal_value":
            warnings.add("COMPLEMENT_NET_VALUE_NONPOSITIVE")
    if event_type == "QualityDecisionRecorded":
        warnings |= {str(code) for code in payload["warnings"]}
        warnings |= {str(code) for code in payload["caps"]}
        if payload["decision"] == "reject":
            hard_failures |= {str(code) for code in payload["reasons"]}
    if event_type == "FinalTestAccessRecorded" and payload["outcome"] == "denied":
        hard_failures.add("FINAL_TEST_CONTAMINATED")
    if event_type == "FinalTestArtifactRecorded" and payload["contaminated"]:
        hard_failures.add("FINAL_TEST_CONTAMINATED")
    if event_type in {"ForwardPlanV2Recorded", "ForwardObservationV2Recorded"}:
        warnings.add("FORWARD_MONITORING_ONLY")
    if event_type in {"QualityDecisionV2Recorded", "QualityDecisionV3Recorded"}:
        warnings |= {str(code) for code in payload["warnings"]}
        warnings |= {str(code) for code in payload["caps"]}
        if payload["decision"] == "reject":
            hard_failures |= {str(code) for code in payload["reasons"]}
    return tuple(sorted(warnings)), tuple(sorted(hard_failures))


__all__ = ["PAYLOAD_SPECS", "envelope_diagnostics", "validate_and_redact_payload"]
