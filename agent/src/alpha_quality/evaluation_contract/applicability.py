"""Producer-derived applicability assessments for exact contracts and factors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal, Mapping

from src.alpha_quality.evaluation_contract.contract import (
    CONTRACT_MEDIA_TYPE,
    ResolvedEvaluationContractArtifactStoreV1,
)
from src.alpha_quality.evaluation_contract.registry import applicability_rule_hash
from src.alpha_quality.flags import ResolvedAGSFlags
from src.research_ledger.events.model import (
    EventDraft,
    EventTransitionError,
    ResearchEventEnvelope,
)
from src.research_ledger.hash_utils import canonical_json_hash


APPLICABILITY_PRODUCER_SCHEMA = "applicability_assessment_service.v1"
APPLICABILITY_PRODUCER_MANIFEST_HASH = canonical_json_hash(
    {
        "schema_version": "applicability_producer_manifest.v1",
        "rules": ["mechanism_claim_declared.v1", "non_control_factor.v1"],
        "dynamic_code": False,
        "caller_result": "forbidden",
    }
)


@dataclass(frozen=True)
class ApplicabilityAssessmentV1:
    schema_version: Literal["applicability_assessment.v1"]
    claim_type: str
    profile_template_hash: str
    resolved_contract_hash: str
    applicability_rule_id: str
    applicability_rule_hash: str
    factor_spec_id: str
    factor_definition_event_hash: str
    evaluated_input_hashes: tuple[str, ...]
    result: Literal["applicable", "not_applicable"]
    reason_code: str
    producer_schema_version: str
    producer_manifest_hash: str
    source_event_hashes: tuple[str, ...]
    assessment_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "applicability_assessment.v1":
            raise ValueError("unsupported applicability assessment")
        if self.producer_schema_version != APPLICABILITY_PRODUCER_SCHEMA:
            raise ValueError("unknown applicability producer schema")
        if self.producer_manifest_hash != APPLICABILITY_PRODUCER_MANIFEST_HASH:
            raise ValueError("applicability producer manifest differs")
        if self.evaluated_input_hashes != tuple(sorted(set(self.evaluated_input_hashes))):
            raise ValueError("applicability input hashes must be canonical")
        if self.source_event_hashes != tuple(sorted(set(self.source_event_hashes))):
            raise ValueError("applicability source events must be canonical")
        if self.assessment_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("applicability assessment hash differs")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "claim_type": self.claim_type,
            "profile_template_hash": self.profile_template_hash,
            "resolved_contract_hash": self.resolved_contract_hash,
            "applicability_rule_id": self.applicability_rule_id,
            "applicability_rule_hash": self.applicability_rule_hash,
            "factor_spec_id": self.factor_spec_id,
            "factor_definition_event_hash": self.factor_definition_event_hash,
            "evaluated_input_hashes": list(self.evaluated_input_hashes),
            "result": self.result,
            "reason_code": self.reason_code,
            "producer_schema_version": self.producer_schema_version,
            "producer_manifest_hash": self.producer_manifest_hash,
            "source_event_hashes": list(self.source_event_hashes),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "assessment_hash": self.assessment_hash}


def _contract_from_event(store: Any, event: ResearchEventEnvelope):
    refs = [
        ref for ref in event.payload["artifact_refs"]
        if ref["media_type"] == CONTRACT_MEDIA_TYPE
    ]
    if len(refs) != 1:
        raise ValueError("resolved contract event has no unique contract artifact")
    ref = refs[0]
    return ResolvedEvaluationContractArtifactStoreV1(store.artifact_root).read(
        str(ref["relative_path"]),
        expected_contract_hash=str(event.payload["contract_hash"]),
        expected_blob_hash=str(ref["artifact_hash"]),
    )


def _evaluate_rule(
    *, rule_id: str, factor_payload: Mapping[str, Any]
) -> tuple[Literal["applicable", "not_applicable"], str]:
    metadata = factor_payload.get("metadata")
    if not isinstance(metadata, Mapping):
        raise ValueError("applicability factor definition is not authoritative")
    semantics = metadata.get("semantics")
    if not isinstance(semantics, Mapping):
        raise ValueError("applicability factor semantics are unavailable")
    if rule_id == "non_control_factor.v1":
        formula = metadata.get("canonical_formula")
        if not isinstance(formula, str):
            raise ValueError("applicability canonical formula is unavailable")
        if formula.startswith("control("):
            return "not_applicable", "REGISTERED_CONTROL_FACTOR"
        return "applicable", "NON_CONTROL_FACTOR"
    if rule_id == "mechanism_claim_declared.v1":
        fields = semantics.get("field_semantics")
        if not isinstance(fields, Mapping):
            raise ValueError("applicability field semantics are unavailable")
        declared = any(
            isinstance(value, str) and value.startswith("mechanism:")
            for value in fields.values()
        )
        if declared:
            return "applicable", "MECHANISM_DECLARED_IN_FACTOR_SEMANTICS"
        return "not_applicable", "NO_MECHANISM_CLAIM_DECLARED"
    raise ValueError("unknown closed applicability rule")


def build_assessment(
    *,
    contract_event: ResearchEventEnvelope,
    contract: Any,
    factor_event: ResearchEventEnvelope,
    claim_type: str,
) -> ApplicabilityAssessmentV1:
    if claim_type in contract.profile_template.tier_invariants:
        raise ValueError("tier invariants cannot receive applicability assessments")
    rule_hash = contract.profile_template.applicability_rule_hashes.get(claim_type)
    if rule_hash is None:
        raise ValueError("claim has no registered conditional applicability rule")
    rule_ids = ("mechanism_claim_declared.v1", "non_control_factor.v1")
    matching = [rule_id for rule_id in rule_ids if applicability_rule_hash(rule_id) == rule_hash]
    if len(matching) != 1:
        raise ValueError("contract applicability rule is not in the closed registry")
    rule_id = matching[0]
    result, reason = _evaluate_rule(rule_id=rule_id, factor_payload=factor_event.payload)
    inputs = tuple(
        sorted(
            {
                contract.contract_hash,
                contract.profile_template.template_hash,
                factor_event.event_hash,
                str(factor_event.payload["canonical_ast_hash"]),
                rule_hash,
            }
        )
    )
    sources = tuple(sorted({contract_event.event_hash, factor_event.event_hash}))
    content = {
        "schema_version": "applicability_assessment.v1",
        "claim_type": claim_type,
        "profile_template_hash": contract.profile_template.template_hash,
        "resolved_contract_hash": contract.contract_hash,
        "applicability_rule_id": rule_id,
        "applicability_rule_hash": rule_hash,
        "factor_spec_id": str(factor_event.payload["factor_spec_id"]),
        "factor_definition_event_hash": factor_event.event_hash,
        "evaluated_input_hashes": list(inputs),
        "result": result,
        "reason_code": reason,
        "producer_schema_version": APPLICABILITY_PRODUCER_SCHEMA,
        "producer_manifest_hash": APPLICABILITY_PRODUCER_MANIFEST_HASH,
        "source_event_hashes": list(sources),
    }
    return ApplicabilityAssessmentV1(
        schema_version="applicability_assessment.v1",
        claim_type=claim_type,
        profile_template_hash=contract.profile_template.template_hash,
        resolved_contract_hash=contract.contract_hash,
        applicability_rule_id=rule_id,
        applicability_rule_hash=rule_hash,
        factor_spec_id=str(factor_event.payload["factor_spec_id"]),
        factor_definition_event_hash=factor_event.event_hash,
        evaluated_input_hashes=inputs,
        result=result,
        reason_code=reason,
        producer_schema_version=APPLICABILITY_PRODUCER_SCHEMA,
        producer_manifest_hash=APPLICABILITY_PRODUCER_MANIFEST_HASH,
        source_event_hashes=sources,
        assessment_hash=canonical_json_hash(content),
    )


@dataclass(frozen=True)
class RecordedApplicabilityAssessmentV1:
    assessment: ApplicabilityAssessmentV1
    event: ResearchEventEnvelope


class ApplicabilityAssessmentServiceV1:
    def __init__(self, store: Any, *, flags: ResolvedAGSFlags) -> None:
        from src.research_ledger.events.store import ResearchEventStore

        if not isinstance(store, ResearchEventStore):
            raise TypeError("applicability assessment requires ResearchEventStore")
        required = ("VIBE_TRADING_RESEARCH_EVENTS", "VIBE_TRADING_ALPHA_SCORECARD")
        if any(not flags.enabled(name) for name in required):
            raise RuntimeError("applicability assessment capability is disabled")
        if flags.as_dict() != store.flags.as_dict():
            raise ValueError("applicability assessment flag snapshots differ")
        self.store = store

    def assess(
        self,
        *,
        run_id: str,
        contract_event_hash: str,
        factor_definition_event_hash: str,
        claim_type: str,
    ) -> RecordedApplicabilityAssessmentV1:
        events = {event.event_hash: event for event in self.store.query_events()}
        contract_event = events.get(contract_event_hash)
        factor_event = events.get(factor_definition_event_hash)
        shared_activation_contract = bool(
            contract_event is not None
            and contract_event.run_id != run_id
            and self.store._shared_activation_sources_in_events(
                list(events.values()),
                run_id=run_id,
                contract_event_hash=contract_event.event_hash,
                snapshot_event_hash=None,
                before_event_hash=None,
            )
        )
        if (
            contract_event is None
            or contract_event.event_type != "ResolvedEvaluationContractRegistered"
            or (
                contract_event.run_id != run_id
                and not shared_activation_contract
            )
        ):
            raise EventTransitionError("applicability requires exact run contract")
        if (
            factor_event is None
            or factor_event.event_type != "FactorDefinitionRecorded"
            or factor_event.run_id != run_id
        ):
            raise EventTransitionError("applicability requires exact run factor definition")
        contract = _contract_from_event(self.store, contract_event)
        assessment = build_assessment(
            contract_event=contract_event,
            contract=contract,
            factor_event=factor_event,
            claim_type=claim_type,
        )
        identifier = "applicability-" + assessment.assessment_hash.removeprefix("sha256:")[:24]
        event = self.store._append_producer_event(
            EventDraft(
                event_type="ApplicabilityAssessmentRecorded",
                entity_id=identifier,
                run_id=run_id,
                payload_schema_version="applicability_assessment_recorded.v1",
                idempotency_key=(
                    "applicability:" + contract.contract_hash + ":"
                    + assessment.factor_spec_id + ":" + claim_type
                ),
                payload={"assessment_id": identifier, **assessment.to_dict()},
            )
        )
        return RecordedApplicabilityAssessmentV1(assessment, event)


def validate_narrative_claim_guard(
    *,
    narrative_claim_types: Iterable[str],
    assessments: Iterable[ApplicabilityAssessmentV1],
) -> None:
    results = {item.claim_type: item.result for item in assessments}
    for claim in narrative_claim_types:
        if (
            claim in {"mechanism", "ashare_implementability"}
            and results.get(claim) == "not_applicable"
        ):
            raise ValueError("narrative claim cannot coexist with not-applicable evidence")
