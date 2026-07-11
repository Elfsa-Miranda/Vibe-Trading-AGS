"""Frozen pre-generation action templates derived from authoritative parents."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from src.alpha_foundry.dsl.diff import extract_ast_diff
from src.alpha_foundry.dsl.grammar import GrammarDefinition
from src.alpha_foundry.dsl.identity import (
    build_expression_identity,
    validate_factor_definition_payload,
)
from src.alpha_foundry.memory.motif import derive_motif
from src.alpha_foundry.mutators import SEED_MUTATION_TEMPLATE_REGISTRY_V1
from src.alpha_quality.flags import ResolvedAGSFlags
from src.alpha_quality.scope import DiscoveryEvidenceProjector
from src.research_ledger.events import (
    EventDraft,
    ResearchEventEnvelope,
    ResearchEventStore,
)
from src.research_ledger.hash_utils import canonical_json_hash


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class FrozenRetrieverActionTemplateV1:
    schema_version: str
    action_id: str
    action_hash: str
    execution_run_id: str
    parent_factor_spec_id: str
    parent_definition_event_hash: str
    eligible_event_watermark: str
    data_snapshot_hash: str
    retrieval_policy_hash: str
    template_registry_hash: str
    template_id: str
    expected_formula: str
    expected_formula_hash: str
    expected_candidate_id: str
    expected_expression_id: str
    expected_canonical_ast_hash: str
    expected_ast_diff_hash: str | None
    expected_motif_version: str | None
    expected_motif: str | None
    identity_action: bool

    def __post_init__(self) -> None:
        if self.schema_version != "frozen_retriever_action_template.v1":
            raise ValueError("unsupported frozen Retriever action schema")
        for value in (
            self.action_hash,
            self.parent_definition_event_hash,
            self.eligible_event_watermark,
            self.data_snapshot_hash,
            self.retrieval_policy_hash,
            self.template_registry_hash,
            self.expected_formula_hash,
            self.expected_expression_id,
            self.expected_canonical_ast_hash,
        ):
            if _HASH_RE.fullmatch(value) is None:
                raise ValueError("frozen Retriever action contains an invalid hash")
        if self.expected_ast_diff_hash is not None and _HASH_RE.fullmatch(
            self.expected_ast_diff_hash
        ) is None:
            raise ValueError("frozen Retriever action AST diff hash is invalid")
        if not self.execution_run_id or not self.parent_factor_spec_id:
            raise ValueError("frozen Retriever action identity is incomplete")
        if (
            self.template_registry_hash
            != SEED_MUTATION_TEMPLATE_REGISTRY_V1.registry_hash
        ):
            raise ValueError("frozen Retriever action template registry differs")
        template = SEED_MUTATION_TEMPLATE_REGISTRY_V1.get(self.template_id)
        if self.identity_action != (template.kind == "identity"):
            raise ValueError("frozen Retriever action kind differs from its template")
        if self.expected_formula_hash != canonical_json_hash(
            {"formula": self.expected_formula}
        ):
            raise ValueError("frozen Retriever action formula hash mismatch")
        expected_id = "retriever-action-v1-" + self.action_hash.removeprefix(
            "sha256:"
        )[:24]
        if self.action_id != expected_id:
            raise ValueError("frozen Retriever action ID does not derive from its hash")
        if self.action_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("frozen Retriever action hash mismatch")
        if self.expected_candidate_id != self.expected_formula_hash.removeprefix(
            "sha256:"
        )[:16]:
            raise ValueError("frozen Retriever candidate ID is invalid")
        empty_structural = (
            self.expected_ast_diff_hash is None
            and self.expected_motif_version is None
            and self.expected_motif is None
        )
        if self.identity_action != empty_structural:
            raise ValueError("identity action must not fabricate an AST diff or motif")
        if not self.identity_action and (
            self.expected_motif_version is None or self.expected_motif is None
        ):
            raise ValueError("non-identity action requires a derived motif")

    @classmethod
    def build(
        cls,
        *,
        execution_run_id: str,
        parent_definition: Mapping[str, Any],
        parent_definition_event_hash: str,
        eligible_event_watermark: str,
        data_snapshot_hash: str,
        retrieval_policy_hash: str,
        template_id: str,
    ) -> "FrozenRetrieverActionTemplateV1":
        validate_factor_definition_payload(parent_definition)
        metadata = parent_definition["metadata"]
        assert isinstance(metadata, Mapping)
        grammar_raw = metadata["grammar_definition"]
        assert isinstance(grammar_raw, Mapping)
        grammar = GrammarDefinition.from_dict(grammar_raw)
        parent_formula = str(metadata["canonical_formula"])
        parent_expression = build_expression_identity(parent_formula, grammar=grammar)
        template = SEED_MUTATION_TEMPLATE_REGISTRY_V1.get(template_id)
        expected_formula = template.render(parent_formula)
        expected_expression = build_expression_identity(expected_formula, grammar=grammar)
        formula_hash = canonical_json_hash({"formula": expected_formula})
        identity_action = template.kind == "identity"
        diff_hash: str | None = None
        motif_version: str | None = None
        motif_name: str | None = None
        if not identity_action:
            diff = extract_ast_diff(
                parent_expression.canonical_ast,
                expected_expression.canonical_ast,
                parent_expression_id=parent_expression.expression_id,
                child_expression_id=expected_expression.expression_id,
                grammar_version=expected_expression.grammar_version,
                grammar_hash=expected_expression.grammar_hash,
            )
            motif = derive_motif(diff)
            diff_hash = motif.ast_diff_hash
            motif_version = motif.motif_version
            motif_name = motif.motif
        content = {
            "schema_version": "frozen_retriever_action_template.v1",
            "execution_run_id": execution_run_id,
            "parent_factor_spec_id": str(parent_definition["factor_spec_id"]),
            "parent_definition_event_hash": parent_definition_event_hash,
            "eligible_event_watermark": eligible_event_watermark,
            "data_snapshot_hash": data_snapshot_hash,
            "retrieval_policy_hash": retrieval_policy_hash,
            "template_registry_hash": (
                SEED_MUTATION_TEMPLATE_REGISTRY_V1.registry_hash
            ),
            "template_id": template.template_id,
            "expected_formula": expected_formula,
            "expected_formula_hash": formula_hash,
            "expected_candidate_id": formula_hash.removeprefix("sha256:")[:16],
            "expected_expression_id": expected_expression.expression_id,
            "expected_canonical_ast_hash": expected_expression.canonical_ast_hash,
            "expected_ast_diff_hash": diff_hash,
            "expected_motif_version": motif_version,
            "expected_motif": motif_name,
            "identity_action": identity_action,
        }
        action_hash = canonical_json_hash(content)
        return cls(
            schema_version="frozen_retriever_action_template.v1",
            action_id="retriever-action-v1-"
            + action_hash.removeprefix("sha256:")[:24],
            action_hash=action_hash,
            execution_run_id=execution_run_id,
            parent_factor_spec_id=str(parent_definition["factor_spec_id"]),
            parent_definition_event_hash=parent_definition_event_hash,
            eligible_event_watermark=eligible_event_watermark,
            data_snapshot_hash=data_snapshot_hash,
            retrieval_policy_hash=retrieval_policy_hash,
            template_registry_hash=(
                SEED_MUTATION_TEMPLATE_REGISTRY_V1.registry_hash
            ),
            template_id=template.template_id,
            expected_formula=expected_formula,
            expected_formula_hash=formula_hash,
            expected_candidate_id=formula_hash.removeprefix("sha256:")[:16],
            expected_expression_id=expected_expression.expression_id,
            expected_canonical_ast_hash=expected_expression.canonical_ast_hash,
            expected_ast_diff_hash=diff_hash,
            expected_motif_version=motif_version,
            expected_motif=motif_name,
            identity_action=identity_action,
        )

    @classmethod
    def from_dict(
        cls, raw: Mapping[str, Any]
    ) -> "FrozenRetrieverActionTemplateV1":
        expected = {
            "schema_version",
            "action_id",
            "action_hash",
            "execution_run_id",
            "parent_factor_spec_id",
            "parent_definition_event_hash",
            "eligible_event_watermark",
            "data_snapshot_hash",
            "retrieval_policy_hash",
            "template_registry_hash",
            "template_id",
            "expected_formula",
            "expected_formula_hash",
            "expected_candidate_id",
            "expected_expression_id",
            "expected_canonical_ast_hash",
            "expected_ast_diff_hash",
            "expected_motif_version",
            "expected_motif",
            "identity_action",
        }
        if set(raw) != expected or not isinstance(raw["identity_action"], bool):
            raise ValueError("frozen Retriever action has an invalid closed schema")
        nullable = {
            name: None if raw[name] is None else str(raw[name])
            for name in (
                "expected_ast_diff_hash",
                "expected_motif_version",
                "expected_motif",
            )
        }
        return cls(
            schema_version=str(raw["schema_version"]),
            action_id=str(raw["action_id"]),
            action_hash=str(raw["action_hash"]),
            execution_run_id=str(raw["execution_run_id"]),
            parent_factor_spec_id=str(raw["parent_factor_spec_id"]),
            parent_definition_event_hash=str(raw["parent_definition_event_hash"]),
            eligible_event_watermark=str(raw["eligible_event_watermark"]),
            data_snapshot_hash=str(raw["data_snapshot_hash"]),
            retrieval_policy_hash=str(raw["retrieval_policy_hash"]),
            template_registry_hash=str(raw["template_registry_hash"]),
            template_id=str(raw["template_id"]),
            expected_formula=str(raw["expected_formula"]),
            expected_formula_hash=str(raw["expected_formula_hash"]),
            expected_candidate_id=str(raw["expected_candidate_id"]),
            expected_expression_id=str(raw["expected_expression_id"]),
            expected_canonical_ast_hash=str(raw["expected_canonical_ast_hash"]),
            expected_ast_diff_hash=nullable["expected_ast_diff_hash"],
            expected_motif_version=nullable["expected_motif_version"],
            expected_motif=nullable["expected_motif"],
            identity_action=raw["identity_action"],
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "execution_run_id": self.execution_run_id,
            "parent_factor_spec_id": self.parent_factor_spec_id,
            "parent_definition_event_hash": self.parent_definition_event_hash,
            "eligible_event_watermark": self.eligible_event_watermark,
            "data_snapshot_hash": self.data_snapshot_hash,
            "retrieval_policy_hash": self.retrieval_policy_hash,
            "template_registry_hash": self.template_registry_hash,
            "template_id": self.template_id,
            "expected_formula": self.expected_formula,
            "expected_formula_hash": self.expected_formula_hash,
            "expected_candidate_id": self.expected_candidate_id,
            "expected_expression_id": self.expected_expression_id,
            "expected_canonical_ast_hash": self.expected_canonical_ast_hash,
            "expected_ast_diff_hash": self.expected_ast_diff_hash,
            "expected_motif_version": self.expected_motif_version,
            "expected_motif": self.expected_motif,
            "identity_action": self.identity_action,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_hash": self.action_hash,
            **self._content_dict(),
        }


@dataclass(frozen=True)
class RecordedRetrieverActionTemplateV1:
    action: FrozenRetrieverActionTemplateV1
    event: ResearchEventEnvelope


class RetrieverActionTemplateServiceV1:
    def __init__(
        self,
        *,
        store: ResearchEventStore,
        flags: ResolvedAGSFlags,
    ) -> None:
        required = (
            "VIBE_TRADING_ALPHA_FOUNDRY",
            "VIBE_TRADING_RESEARCH_EVENTS",
            "VIBE_TRADING_FACTOR_DAG",
            "VIBE_TRADING_PROCESS_MEMORY",
            "VIBE_TRADING_TOPOLOGY_RETRIEVER",
        )
        if any(not flags.enabled(name) for name in required):
            raise RuntimeError("Retriever action-template capability is disabled")
        self.store = store
        self.projector = DiscoveryEvidenceProjector(flags=flags)

    def freeze(
        self,
        *,
        execution_run_id: str,
        parent_factor_spec_id: str,
        template_id: str,
        eligible_event_watermark: str,
        data_snapshot_hash: str,
        retrieval_policy_hash: str,
    ) -> RecordedRetrieverActionTemplateV1:
        discovery = self.projector.project_at_watermark(
            self.store,
            data_snapshot_hash=data_snapshot_hash,
            watermark_event_hash=eligible_event_watermark,
        )
        if discovery.source_watermark != eligible_event_watermark:
            raise ValueError("Retriever action watermark is not discovery-eligible")
        if parent_factor_spec_id not in discovery.factual.factor_ids():
            raise ValueError("Retriever action parent lacks terminal train/valid evidence")
        definition_hash = discovery.factual.definition_event_hash(
            parent_factor_spec_id
        )
        definitions = [
            event
            for event in self.store.query_events(
                event_type="FactorDefinitionRecorded",
                entity_id=parent_factor_spec_id,
            )
            if event.event_hash == definition_hash
        ]
        if len(definitions) != 1:
            raise ValueError("Retriever action parent definition is ambiguous")
        action = FrozenRetrieverActionTemplateV1.build(
            execution_run_id=execution_run_id,
            parent_definition=definitions[0].payload,
            parent_definition_event_hash=definition_hash,
            eligible_event_watermark=eligible_event_watermark,
            data_snapshot_hash=data_snapshot_hash,
            retrieval_policy_hash=retrieval_policy_hash,
            template_id=template_id,
        )
        event = self.store.append_event(
            EventDraft(
                event_type="RetrieverActionTemplateFrozen",
                entity_id=action.action_id,
                run_id=execution_run_id,
                payload_schema_version="retriever_action_template_frozen.v1",
                payload=action.to_dict(),
                idempotency_key="retriever-action-v1:" + action.action_hash,
            )
        )
        return RecordedRetrieverActionTemplateV1(action, event)


__all__ = [
    "FrozenRetrieverActionTemplateV1",
    "RecordedRetrieverActionTemplateV1",
    "RetrieverActionTemplateServiceV1",
]
