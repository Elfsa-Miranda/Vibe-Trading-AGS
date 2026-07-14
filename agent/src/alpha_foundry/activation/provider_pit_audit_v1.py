"""Field-level provider PIT audit for formal Activation eligibility.

The audit is intentionally separate from the existing snapshot producer.  It
classifies what a provider interface can prove; it never manufactures market
data or upgrades an adapter registration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from src.alpha_foundry.activation.protocol_v2 import (
    CanonicalHashSpecV1,
    DEFAULT_ACTIVATION_HASH_SPEC,
)
from src.research_ledger.events import (
    EventDraft,
    EventTransitionError,
    ResearchEventEnvelope,
    ResearchEventStore,
)


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
AuditStatus = Literal["verified_strict", "best_effort", "unavailable"]
ComplianceLevel = Literal[
    "verified_strict",
    "verified_with_limitations",
    "best_effort",
    "non_compliant",
    "unknown",
]

STRICT_ACTIVATION_REQUIRED_FIELDS_V1 = (
    "adjustment",
    "amount",
    "close",
    "corporate_action",
    "daily_csi300_membership",
    "delisting_state",
    "high",
    "limit_down",
    "limit_up",
    "listing_date",
    "low",
    "open",
    "reliable_price_availability",
    "st_status",
    "suspension",
    "trading_calendar",
    "volume",
)
PROHIBITED_FINANCIAL_STATEMENT_FIELDS_V1 = (
    "end_date",
    "f_ann_date",
    "update_flag",
)


def _hashes(values: tuple[str, ...], name: str, *, allow_empty: bool = False) -> None:
    if (
        (not values and not allow_empty)
        or values != tuple(sorted(set(values)))
        or any(_HASH_RE.fullmatch(value) is None for value in values)
    ):
        raise ValueError(f"{name} must be canonical hashes")


@dataclass(frozen=True)
class ProviderFieldPITAuditV1:
    schema_version: Literal["provider_field_pit_audit.v1"]
    provider: str
    adapter_id: str
    interface: str
    field_name: str
    event_or_reporting_time: str
    effective_time: str
    provider_availability_time: str
    retrieval_vintage: str
    revision_history: Literal["complete", "partial", "unknown", "not_applicable"]
    cross_interface_consistency: Literal["verified", "not_verified", "not_applicable"]
    missingness_policy: Literal["fail_closed", "typed_unavailable", "unknown"]
    rate_limit_retry_behavior: Literal["complete_manifest_required", "partial_batch_possible", "unknown"]
    cache_vintage: Literal["bound_to_manifest", "unbound", "unknown"]
    audit_sample_definition: str
    compliance_level: ComplianceLevel
    claim_scope_ceiling: AuditStatus
    allowed_claim_scopes: tuple[str, ...]
    prohibited_claim_scopes: tuple[str, ...]
    evidence_kinds: tuple[str, ...]
    audit_evidence_hashes: tuple[str, ...]
    limitations: tuple[str, ...]
    canonical_hash_spec: CanonicalHashSpecV1
    field_audit_hash: str

    def __post_init__(self) -> None:
        if not all((self.provider, self.adapter_id, self.interface, self.field_name)):
            raise ValueError("provider field audit identity is required")
        temporal = (
            self.event_or_reporting_time,
            self.effective_time,
            self.provider_availability_time,
            self.retrieval_vintage,
        )
        if any(not value for value in temporal) or len(set(temporal)) != 4:
            raise ValueError("reporting, effective, availability, and vintage semantics must be distinct")
        if self.evidence_kinds != tuple(sorted(set(self.evidence_kinds))):
            raise ValueError("provider evidence kinds must be sorted and unique")
        _hashes(self.audit_evidence_hashes, "provider audit evidence", allow_empty=True)
        if self.limitations != tuple(sorted(set(self.limitations))):
            raise ValueError("provider field limitations must be sorted and unique")
        for values, name in (
            (self.allowed_claim_scopes, "allowed claim scopes"),
            (self.prohibited_claim_scopes, "prohibited claim scopes"),
        ):
            if not values or values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be non-empty, sorted, and unique")
        if not self.audit_sample_definition:
            raise ValueError("provider audit sample definition is required")
        strict_evidence = {"independent_crosscheck", "source_partition_replay"}
        strict_ready = (
            bool(strict_evidence.intersection(self.evidence_kinds))
            and bool(self.audit_evidence_hashes)
            and self.revision_history in {"complete", "not_applicable"}
            and self.cross_interface_consistency in {"verified", "not_applicable"}
            and self.missingness_policy == "fail_closed"
            and self.rate_limit_retry_behavior == "complete_manifest_required"
            and self.cache_vintage == "bound_to_manifest"
            and "unknown" not in " ".join(temporal).lower()
            and "not_exposed" not in " ".join(temporal).lower()
        )
        if self.compliance_level == "verified_strict" and not strict_ready:
            raise ValueError("verified_strict requires independent field-level audit evidence")
        if self.claim_scope_ceiling == "verified_strict" and (
            self.compliance_level != "verified_strict" or not strict_ready
        ):
            raise ValueError("strict claim scope requires verified_strict compliance")
        if self.compliance_level in {"non_compliant", "unknown"} and not self.limitations:
            raise ValueError("non-strict provider compliance requires a typed limitation")
        if self.claim_scope_ceiling == "unavailable" and not self.limitations:
            raise ValueError("unavailable provider field requires a typed limitation")
        expected = self.canonical_hash_spec.hash_payload("provider-field-pit-audit.v1", self._content_dict())
        if self.field_audit_hash != expected:
            raise ValueError("provider field PIT audit hash mismatch")

    @classmethod
    def create(
        cls,
        *,
        provider: str,
        adapter_id: str,
        interface: str,
        field_name: str,
        event_or_reporting_time: str,
        effective_time: str,
        provider_availability_time: str,
        retrieval_vintage: str,
        revision_history: Literal["complete", "partial", "unknown", "not_applicable"],
        cross_interface_consistency: Literal["verified", "not_verified", "not_applicable"],
        missingness_policy: Literal["fail_closed", "typed_unavailable", "unknown"],
        rate_limit_retry_behavior: Literal["complete_manifest_required", "partial_batch_possible", "unknown"],
        cache_vintage: Literal["bound_to_manifest", "unbound", "unknown"],
        audit_sample_definition: str,
        compliance_level: ComplianceLevel,
        claim_scope_ceiling: AuditStatus,
        allowed_claim_scopes: tuple[str, ...],
        prohibited_claim_scopes: tuple[str, ...],
        evidence_kinds: tuple[str, ...],
        audit_evidence_hashes: tuple[str, ...],
        limitations: tuple[str, ...],
        hash_spec: CanonicalHashSpecV1 = DEFAULT_ACTIVATION_HASH_SPEC,
    ) -> "ProviderFieldPITAuditV1":
        content = {
            "schema_version": "provider_field_pit_audit.v1",
            "provider": provider,
            "adapter_id": adapter_id,
            "interface": interface,
            "field_name": field_name,
            "event_or_reporting_time": event_or_reporting_time,
            "effective_time": effective_time,
            "provider_availability_time": provider_availability_time,
            "retrieval_vintage": retrieval_vintage,
            "revision_history": revision_history,
            "cross_interface_consistency": cross_interface_consistency,
            "missingness_policy": missingness_policy,
            "rate_limit_retry_behavior": rate_limit_retry_behavior,
            "cache_vintage": cache_vintage,
            "audit_sample_definition": audit_sample_definition,
            "compliance_level": compliance_level,
            "claim_scope_ceiling": claim_scope_ceiling,
            "allowed_claim_scopes": list(sorted(set(allowed_claim_scopes))),
            "prohibited_claim_scopes": list(sorted(set(prohibited_claim_scopes))),
            "evidence_kinds": list(sorted(set(evidence_kinds))),
            "audit_evidence_hashes": list(sorted(set(audit_evidence_hashes))),
            "limitations": list(sorted(set(limitations))),
            "canonical_hash_spec": hash_spec.to_dict(),
        }
        return cls(
            schema_version="provider_field_pit_audit.v1",
            provider=provider,
            adapter_id=adapter_id,
            interface=interface,
            field_name=field_name,
            event_or_reporting_time=event_or_reporting_time,
            effective_time=effective_time,
            provider_availability_time=provider_availability_time,
            retrieval_vintage=retrieval_vintage,
            revision_history=revision_history,
            cross_interface_consistency=cross_interface_consistency,
            missingness_policy=missingness_policy,
            rate_limit_retry_behavior=rate_limit_retry_behavior,
            cache_vintage=cache_vintage,
            audit_sample_definition=audit_sample_definition,
            compliance_level=compliance_level,
            claim_scope_ceiling=claim_scope_ceiling,
            allowed_claim_scopes=tuple(content["allowed_claim_scopes"]),
            prohibited_claim_scopes=tuple(content["prohibited_claim_scopes"]),
            evidence_kinds=tuple(content["evidence_kinds"]),
            audit_evidence_hashes=tuple(content["audit_evidence_hashes"]),
            limitations=tuple(content["limitations"]),
            canonical_hash_spec=hash_spec,
            field_audit_hash=hash_spec.hash_payload("provider-field-pit-audit.v1", content),
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "provider": self.provider,
            "adapter_id": self.adapter_id,
            "interface": self.interface,
            "field_name": self.field_name,
            "event_or_reporting_time": self.event_or_reporting_time,
            "effective_time": self.effective_time,
            "provider_availability_time": self.provider_availability_time,
            "retrieval_vintage": self.retrieval_vintage,
            "revision_history": self.revision_history,
            "cross_interface_consistency": self.cross_interface_consistency,
            "missingness_policy": self.missingness_policy,
            "rate_limit_retry_behavior": self.rate_limit_retry_behavior,
            "cache_vintage": self.cache_vintage,
            "audit_sample_definition": self.audit_sample_definition,
            "compliance_level": self.compliance_level,
            "claim_scope_ceiling": self.claim_scope_ceiling,
            "allowed_claim_scopes": list(self.allowed_claim_scopes),
            "prohibited_claim_scopes": list(self.prohibited_claim_scopes),
            "evidence_kinds": list(self.evidence_kinds),
            "audit_evidence_hashes": list(self.audit_evidence_hashes),
            "limitations": list(self.limitations),
            "canonical_hash_spec": self.canonical_hash_spec.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "field_audit_hash": self.field_audit_hash}


@dataclass(frozen=True)
class ProviderInterfacePITAuditV1:
    schema_version: Literal["provider_interface_pit_audit.v1"]
    provider: str
    adapter_id: str
    interface: str
    field_audit_event_hashes: tuple[str, ...]
    field_audit_hashes: tuple[str, ...]
    required_fields: tuple[str, ...]
    completeness: Literal["complete", "partial", "unavailable"]
    complete_manifest_eligible: bool
    claim_scope_ceiling: AuditStatus
    limitations: tuple[str, ...]
    canonical_hash_spec: CanonicalHashSpecV1
    interface_audit_hash: str

    def __post_init__(self) -> None:
        _hashes(self.field_audit_event_hashes, "field audit event")
        _hashes(self.field_audit_hashes, "field audit")
        if self.required_fields != tuple(sorted(set(self.required_fields))):
            raise ValueError("provider interface required fields must be sorted and unique")
        if len(self.field_audit_hashes) != len(self.required_fields):
            raise ValueError("provider interface audit field coverage differs")
        if self.complete_manifest_eligible and self.completeness != "complete":
            raise ValueError("partial provider batch cannot create a complete manifest")
        if self.claim_scope_ceiling == "verified_strict" and (
            not self.complete_manifest_eligible or self.completeness != "complete"
        ):
            raise ValueError("strict interface authority requires a complete manifest")
        expected = self.canonical_hash_spec.hash_payload("provider-interface-pit-audit.v1", self._content_dict())
        if self.interface_audit_hash != expected:
            raise ValueError("provider interface PIT audit hash mismatch")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "provider": self.provider,
            "adapter_id": self.adapter_id,
            "interface": self.interface,
            "field_audit_event_hashes": list(self.field_audit_event_hashes),
            "field_audit_hashes": list(self.field_audit_hashes),
            "required_fields": list(self.required_fields),
            "completeness": self.completeness,
            "complete_manifest_eligible": self.complete_manifest_eligible,
            "claim_scope_ceiling": self.claim_scope_ceiling,
            "limitations": list(self.limitations),
            "canonical_hash_spec": self.canonical_hash_spec.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "interface_audit_hash": self.interface_audit_hash}


@dataclass(frozen=True)
class ProviderAuthorityDecisionV1:
    schema_version: Literal["provider_authority_decision.v1"]
    provider: str
    adapter_id: str
    adapter_registration_event_hash: str
    interface_audit_event_hashes: tuple[str, ...]
    interface_audit_hashes: tuple[str, ...]
    authority_status: Literal["verified_strict", "best_effort", "blocked"]
    claim_scope_ceiling: AuditStatus
    activation_eligible: bool
    blocker_codes: tuple[str, ...]
    canonical_hash_spec: CanonicalHashSpecV1
    decision_hash: str

    def __post_init__(self) -> None:
        _hashes((self.adapter_registration_event_hash,), "adapter registration")
        _hashes(self.interface_audit_event_hashes, "interface audit event")
        _hashes(self.interface_audit_hashes, "interface audit")
        if self.blocker_codes != tuple(sorted(set(self.blocker_codes))):
            raise ValueError("provider authority blockers must be sorted and unique")
        if self.activation_eligible != (
            self.authority_status == "verified_strict"
            and self.claim_scope_ceiling == "verified_strict"
            and not self.blocker_codes
        ):
            raise ValueError("provider activation eligibility must derive from strict authority")
        expected = self.canonical_hash_spec.hash_payload("provider-authority-decision.v1", self._content_dict())
        if self.decision_hash != expected:
            raise ValueError("provider authority decision hash mismatch")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "provider": self.provider,
            "adapter_id": self.adapter_id,
            "adapter_registration_event_hash": self.adapter_registration_event_hash,
            "interface_audit_event_hashes": list(self.interface_audit_event_hashes),
            "interface_audit_hashes": list(self.interface_audit_hashes),
            "authority_status": self.authority_status,
            "claim_scope_ceiling": self.claim_scope_ceiling,
            "activation_eligible": self.activation_eligible,
            "blocker_codes": list(self.blocker_codes),
            "canonical_hash_spec": self.canonical_hash_spec.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "decision_hash": self.decision_hash}


def tushare_activation_field_audits_v1(
    *,
    adapter_registration_event_hash: str,
    hash_spec: CanonicalHashSpecV1 = DEFAULT_ACTIVATION_HASH_SPEC,
) -> tuple[ProviderFieldPITAuditV1, ...]:
    """Audit the locally implemented Tushare interfaces without claiming more.

    Local code and its registration hash are documentation/implementation
    evidence, not an independent historical-as-of verification.  Therefore the
    catalog cannot mint ``verified_strict``.
    """

    official_documentation_hash = hash_spec.hash_payload(
        "tushare-activation-interface-docs.v1",
        {
            "adapter_interfaces": [
                "adj_factor",
                "daily",
                "dividend",
                "index_weight",
                "namechange",
                "stk_limit",
                "stock_basic",
                "suspend_d",
                "trade_cal",
            ],
            "official_docs": [
                "https://tushare.pro/document/1?doc_id=27",
                "https://tushare.pro/document/1?doc_id=108",
                "https://tushare.pro/document/2?doc_id=26",
                "https://tushare.pro/document/2?doc_id=130",
            ],
        },
    )
    interface_by_field = {
        "adjustment": "adj_factor",
        "amount": "daily",
        "close": "daily",
        "corporate_action": "dividend",
        "daily_csi300_membership": "index_weight",
        "delisting_state": "stock_basic",
        "high": "daily",
        "limit_down": "stk_limit",
        "limit_up": "stk_limit",
        "listing_date": "stock_basic",
        "low": "daily",
        "open": "daily",
        "reliable_price_availability": "daily",
        "st_status": "namechange",
        "suspension": "suspend_d",
        "trading_calendar": "trade_cal",
        "volume": "daily",
    }
    rows: list[dict[str, Any]] = []
    for field_name in STRICT_ACTIVATION_REQUIRED_FIELDS_V1:
        availability_is_missing = field_name == "reliable_price_availability"
        rows.append(
            {
                "provider": "tushare-pro",
                "adapter_id": "tushare-csi300-pit-v1",
                "interface": interface_by_field[field_name],
                "field_name": field_name,
                "event_or_reporting_time": "provider_trade_or_announcement_date",
                "effective_time": "market_effective_date",
                "provider_availability_time": (
                    "provider_row_timestamp_not_exposed"
                    if availability_is_missing
                    else "documented_interface_update_window"
                ),
                "retrieval_vintage": "source_manifest_source_as_of",
                "revision_history": "unknown",
                "cross_interface_consistency": "not_verified",
                "missingness_policy": ("typed_unavailable" if availability_is_missing else "fail_closed"),
                "rate_limit_retry_behavior": "partial_batch_possible",
                "cache_vintage": "bound_to_manifest",
                "audit_sample_definition": "not_executed_provider_credential_unavailable",
                "compliance_level": ("non_compliant" if availability_is_missing else "unknown"),
                "claim_scope_ceiling": ("unavailable" if availability_is_missing else "best_effort"),
                "allowed_claim_scopes": ("engineering_schema_validation",),
                "prohibited_claim_scopes": (
                    "formal_strict_pit_activation",
                    "production_snapshot_authority",
                ),
                "evidence_kinds": (
                    "local_implementation_review",
                    "official_provider_documentation",
                ),
                "audit_evidence_hashes": (
                    adapter_registration_event_hash,
                    official_documentation_hash,
                ),
                "limitations": tuple(
                    sorted(
                        {
                            "PROVIDER_CREDENTIAL_REQUIRED_FOR_EMPIRICAL_AUDIT",
                            "PROVIDER_REVISION_HISTORY_UNVERIFIED",
                            "RATE_LIMIT_COMPLETENESS_NOT_INDEPENDENTLY_AUDITED",
                        }
                        | ({"DAILY_PROVIDER_AVAILABILITY_TIMESTAMP_NOT_EXPOSED"} if availability_is_missing else set())
                    )
                ),
                "hash_spec": hash_spec,
            }
        )
    return tuple(
        sorted(
            (
                ProviderFieldPITAuditV1.create(**row)  # type: ignore[arg-type]
                for row in rows
            ),
            key=lambda item: (item.interface, item.field_name),
        )
    )


def baostock_golden_cohort_field_audits_v1(
    *,
    adapter_registration_event_hash: str,
    typed_receipt_hashes: tuple[str, ...],
    hash_spec: CanonicalHashSpecV1 = DEFAULT_ACTIVATION_HASH_SPEC,
) -> tuple[ProviderFieldPITAuditV1, ...]:
    """Build the free-source audit from typed raw-call receipts, never frames.

    BaoStock has no credential boundary, but its retrieval timestamp is not a
    provider publication timestamp.  Receipt-backed success therefore supports
    schema/replay evidence only; it cannot mint strict PIT authority.
    """
    _hashes((adapter_registration_event_hash,), "adapter registration")
    _hashes(typed_receipt_hashes, "BaoStock typed receipt")
    interface_by_field = {
        "amount": "query_history_k_data_plus",
        "close": "query_history_k_data_plus",
        "delisting_state": "query_stock_basic",
        "high": "query_history_k_data_plus",
        "listing_date": "query_stock_basic",
        "low": "query_history_k_data_plus",
        "open": "query_history_k_data_plus",
        "reliable_price_availability": "query_history_k_data_plus",
        "st_status": "query_history_k_data_plus",
        "suspension": "query_history_k_data_plus",
        "trading_calendar": "query_trade_dates",
        "trading_status": "query_history_k_data_plus",
        "volume": "query_history_k_data_plus",
    }
    unavailable_fields = {"reliable_price_availability"}
    return tuple(
        ProviderFieldPITAuditV1.create(
            provider="baostock",
            adapter_id="baostock-ashare-eligible-golden-cohort-v1",
            interface=interface_by_field[field_name],
            field_name=field_name,
            event_or_reporting_time="provider_market_date",
            effective_time="market_session_date",
            provider_availability_time="retrieval_receipt_not_provider_publication_time",
            retrieval_vintage="source_manifest_source_as_of",
            revision_history="unknown",
            cross_interface_consistency="not_verified",
            missingness_policy="fail_closed",
            rate_limit_retry_behavior="partial_batch_possible",
            cache_vintage="bound_to_manifest",
            audit_sample_definition="typed_baostock_raw_receipt_replay",
            compliance_level=("non_compliant" if field_name in unavailable_fields else "verified_with_limitations"),
            claim_scope_ceiling=("unavailable" if field_name in unavailable_fields else "best_effort"),
            allowed_claim_scopes=("eligible_universe_schema_replay",),
            prohibited_claim_scopes=("csi300_membership_claim", "formal_strict_pit_activation"),
            evidence_kinds=("typed_provider_receipt", "source_partition_replay"),
            audit_evidence_hashes=tuple(sorted((adapter_registration_event_hash, *typed_receipt_hashes))),
            limitations=tuple(sorted({"PROVIDER_PUBLICATION_TIMESTAMP_UNAVAILABLE", "REVISION_HISTORY_UNVERIFIED", "STRICT_PIT_NOT_ESTABLISHED"})),
            hash_spec=hash_spec,
        )
        for field_name in sorted(interface_by_field)
    )


@dataclass(frozen=True)
class RecordedProviderFieldPITAuditV1:
    audit: ProviderFieldPITAuditV1
    event: ResearchEventEnvelope


@dataclass(frozen=True)
class RecordedProviderInterfacePITAuditV1:
    audit: ProviderInterfacePITAuditV1
    event: ResearchEventEnvelope


@dataclass(frozen=True)
class RecordedProviderAuthorityDecisionV1:
    decision: ProviderAuthorityDecisionV1
    event: ResearchEventEnvelope


class ProviderPITAuditServiceV1:
    def __init__(self, store: ResearchEventStore) -> None:
        if not isinstance(store, ResearchEventStore):
            raise TypeError("provider PIT audit requires ResearchEventStore")
        required = (
            "VIBE_TRADING_ALPHA_FOUNDRY",
            "VIBE_TRADING_ALPHA_SCORECARD",
            "VIBE_TRADING_RESEARCH_EVENTS",
        )
        if any(not store.flags.enabled(name) for name in required):
            raise RuntimeError("provider PIT audit capability is disabled")
        self.store = store

    def record_field(
        self,
        *,
        run_id: str,
        adapter_registration_event_hash: str,
        audit: ProviderFieldPITAuditV1,
    ) -> RecordedProviderFieldPITAuditV1:
        registration = self._registration(adapter_registration_event_hash)
        self._match_registration(registration, audit.provider, audit.adapter_id)
        self._require_before_snapshot(adapter_registration_event_hash)
        audit_id = "provider-field-pit-" + audit.field_audit_hash[-24:]
        event = self.store._append_producer_event(
            EventDraft(
                event_type="ProviderFieldPITAuditV1Recorded",
                entity_id=audit_id,
                run_id=run_id,
                payload_schema_version="provider_field_pit_audit_recorded.v1",
                idempotency_key="provider-field-pit-v1:" + audit.field_audit_hash,
                payload={
                    "audit_id": audit_id,
                    "adapter_registration_event_hash": adapter_registration_event_hash,
                    "provider": audit.provider,
                    "adapter_id": audit.adapter_id,
                    "interface": audit.interface,
                    "field_name": audit.field_name,
                    "claim_scope_ceiling": audit.claim_scope_ceiling,
                    "field_audit_hash": audit.field_audit_hash,
                    "canonical_hash_spec": audit.canonical_hash_spec.to_dict(),
                    "audit": audit.to_dict(),
                },
            )
        )
        return RecordedProviderFieldPITAuditV1(audit, event)

    def record_interface(
        self,
        *,
        run_id: str,
        adapter_registration_event_hash: str,
        field_audit_event_hashes: tuple[str, ...],
        required_fields: tuple[str, ...],
    ) -> RecordedProviderInterfacePITAuditV1:
        registration = self._registration(adapter_registration_event_hash)
        events = self._events(field_audit_event_hashes, "ProviderFieldPITAuditV1Recorded")
        if any(event.payload["adapter_registration_event_hash"] != adapter_registration_event_hash for event in events):
            raise EventTransitionError("provider interface mixes adapter registrations")
        interfaces = {str(event.payload["interface"]) for event in events}
        if len(interfaces) != 1:
            raise EventTransitionError("provider interface audit must cover one interface")
        fields = tuple(sorted(str(event.payload["field_name"]) for event in events))
        required = tuple(sorted(set(required_fields)))
        completeness: Literal["complete", "partial", "unavailable"] = "complete" if fields == required else "partial"
        audits = [event.payload["audit"] for event in events]
        manifest_eligible = completeness == "complete" and all(
            audit["rate_limit_retry_behavior"] == "complete_manifest_required"
            and audit["cache_vintage"] == "bound_to_manifest"
            for audit in audits
        )
        ceilings = {str(event.payload["claim_scope_ceiling"]) for event in events}
        ceiling: AuditStatus
        if ceilings == {"verified_strict"} and manifest_eligible:
            ceiling = "verified_strict"
        elif "unavailable" in ceilings:
            ceiling = "unavailable"
        else:
            ceiling = "best_effort"
        limitations = tuple(
            sorted(
                {str(code) for audit in audits for code in audit["limitations"]}
                | ({"PARTIAL_OR_RATE_LIMITED_INTERFACE_MANIFEST"} if not manifest_eligible else set())
            )
        )
        normalized_field_event_hashes = tuple(sorted(field_audit_event_hashes))
        normalized_field_audit_hashes = tuple(sorted(str(event.payload["field_audit_hash"]) for event in events))
        content = {
            "schema_version": "provider_interface_pit_audit.v1",
            "provider": str(registration.payload["provider"]),
            "adapter_id": str(registration.payload["adapter_id"]),
            "interface": next(iter(interfaces)),
            "field_audit_event_hashes": list(normalized_field_event_hashes),
            "field_audit_hashes": list(normalized_field_audit_hashes),
            "required_fields": list(required),
            "completeness": completeness,
            "complete_manifest_eligible": manifest_eligible,
            "claim_scope_ceiling": ceiling,
            "limitations": list(limitations),
            "canonical_hash_spec": DEFAULT_ACTIVATION_HASH_SPEC.to_dict(),
        }
        audit = ProviderInterfacePITAuditV1(
            schema_version="provider_interface_pit_audit.v1",
            provider=str(registration.payload["provider"]),
            adapter_id=str(registration.payload["adapter_id"]),
            interface=next(iter(interfaces)),
            field_audit_event_hashes=normalized_field_event_hashes,
            field_audit_hashes=normalized_field_audit_hashes,
            required_fields=required,
            completeness=completeness,
            complete_manifest_eligible=manifest_eligible,
            claim_scope_ceiling=ceiling,
            limitations=limitations,
            canonical_hash_spec=DEFAULT_ACTIVATION_HASH_SPEC,
            interface_audit_hash=DEFAULT_ACTIVATION_HASH_SPEC.hash_payload("provider-interface-pit-audit.v1", content),
        )
        audit_id = "provider-interface-pit-" + audit.interface_audit_hash[-24:]
        event = self.store._append_producer_event(
            EventDraft(
                event_type="ProviderInterfacePITAuditV1Recorded",
                entity_id=audit_id,
                run_id=run_id,
                payload_schema_version="provider_interface_pit_audit_recorded.v1",
                idempotency_key="provider-interface-pit-v1:" + audit.interface_audit_hash,
                payload={
                    "audit_id": audit_id,
                    "adapter_registration_event_hash": adapter_registration_event_hash,
                    "provider": audit.provider,
                    "adapter_id": audit.adapter_id,
                    "interface": audit.interface,
                    "claim_scope_ceiling": audit.claim_scope_ceiling,
                    "interface_audit_hash": audit.interface_audit_hash,
                    "field_audit_event_hashes": list(audit.field_audit_event_hashes),
                    "canonical_hash_spec": audit.canonical_hash_spec.to_dict(),
                    "audit": audit.to_dict(),
                },
            )
        )
        return RecordedProviderInterfacePITAuditV1(audit, event)

    def decide_authority(
        self,
        *,
        run_id: str,
        adapter_registration_event_hash: str,
        interface_audit_event_hashes: tuple[str, ...],
        required_interfaces: tuple[str, ...],
    ) -> RecordedProviderAuthorityDecisionV1:
        registration = self._registration(adapter_registration_event_hash)
        events = self._events(interface_audit_event_hashes, "ProviderInterfacePITAuditV1Recorded")
        interfaces = {str(event.payload["interface"]) for event in events}
        blockers: set[str] = set()
        if interfaces != set(required_interfaces):
            blockers.add("PROVIDER_INTERFACE_AUDIT_INCOMPLETE")
        if str(registration.payload["authority_class"]) != "built_in_production":
            blockers.add("ADAPTER_AUTHORITY_NOT_PRODUCTION")
        ceilings = {str(event.payload["claim_scope_ceiling"]) for event in events}
        if ceilings != {"verified_strict"}:
            blockers.add("PROVIDER_FIELD_PIT_NOT_VERIFIED_STRICT")
        if any(not bool(event.payload["audit"]["complete_manifest_eligible"]) for event in events):
            blockers.add("PROVIDER_COMPLETE_MANIFEST_UNPROVEN")
        ceiling: AuditStatus = (
            "verified_strict"
            if ceilings == {"verified_strict"}
            else "best_effort"
            if "best_effort" in ceilings
            else "unavailable"
        )
        structural_blockers = {
            "PROVIDER_INTERFACE_AUDIT_INCOMPLETE",
            "ADAPTER_AUTHORITY_NOT_PRODUCTION",
        }
        status: Literal["verified_strict", "best_effort", "blocked"] = (
            "verified_strict"
            if not blockers and ceiling == "verified_strict"
            else "best_effort"
            if ceiling == "best_effort" and not structural_blockers.intersection(blockers)
            else "blocked"
        )
        normalized_interface_event_hashes = tuple(sorted(interface_audit_event_hashes))
        normalized_interface_audit_hashes = tuple(
            sorted(str(event.payload["interface_audit_hash"]) for event in events)
        )
        normalized_blockers = tuple(sorted(blockers))
        content = {
            "schema_version": "provider_authority_decision.v1",
            "provider": str(registration.payload["provider"]),
            "adapter_id": str(registration.payload["adapter_id"]),
            "adapter_registration_event_hash": adapter_registration_event_hash,
            "interface_audit_event_hashes": list(normalized_interface_event_hashes),
            "interface_audit_hashes": list(normalized_interface_audit_hashes),
            "authority_status": status,
            "claim_scope_ceiling": ceiling,
            "activation_eligible": not blockers and status == "verified_strict",
            "blocker_codes": list(normalized_blockers),
            "canonical_hash_spec": DEFAULT_ACTIVATION_HASH_SPEC.to_dict(),
        }
        decision = ProviderAuthorityDecisionV1(
            schema_version="provider_authority_decision.v1",
            provider=str(registration.payload["provider"]),
            adapter_id=str(registration.payload["adapter_id"]),
            adapter_registration_event_hash=adapter_registration_event_hash,
            interface_audit_event_hashes=normalized_interface_event_hashes,
            interface_audit_hashes=normalized_interface_audit_hashes,
            authority_status=status,
            claim_scope_ceiling=ceiling,
            activation_eligible=bool(content["activation_eligible"]),
            blocker_codes=normalized_blockers,
            canonical_hash_spec=DEFAULT_ACTIVATION_HASH_SPEC,
            decision_hash=DEFAULT_ACTIVATION_HASH_SPEC.hash_payload("provider-authority-decision.v1", content),
        )
        decision_id = "provider-authority-" + decision.decision_hash[-24:]
        event = self.store._append_producer_event(
            EventDraft(
                event_type="ProviderAuthorityDecisionV1Recorded",
                entity_id=decision_id,
                run_id=run_id,
                payload_schema_version="provider_authority_decision_recorded.v1",
                idempotency_key="provider-authority-v1:" + decision.decision_hash,
                payload={
                    "decision_id": decision_id,
                    "provider": decision.provider,
                    "adapter_id": decision.adapter_id,
                    "adapter_registration_event_hash": adapter_registration_event_hash,
                    "authority_status": decision.authority_status,
                    "claim_scope_ceiling": decision.claim_scope_ceiling,
                    "activation_eligible": decision.activation_eligible,
                    "decision_hash": decision.decision_hash,
                    "interface_audit_event_hashes": list(decision.interface_audit_event_hashes),
                    "canonical_hash_spec": decision.canonical_hash_spec.to_dict(),
                    "decision": decision.to_dict(),
                },
            )
        )
        return RecordedProviderAuthorityDecisionV1(decision, event)

    def _registration(self, event_hash: str) -> ResearchEventEnvelope:
        matches = [
            event
            for event in self.store.query_events(event_type="AsharePITAdapterRegistered")
            if event.event_hash == event_hash
        ]
        if len(matches) != 1:
            raise EventTransitionError("provider PIT audit requires exact adapter registration")
        return matches[0]

    @staticmethod
    def _match_registration(registration: ResearchEventEnvelope, provider: str, adapter_id: str) -> None:
        if registration.payload["provider"] != provider or registration.payload["adapter_id"] != adapter_id:
            raise EventTransitionError("provider PIT audit differs from adapter registration")

    def _require_before_snapshot(self, registration_hash: str) -> None:
        if any(
            event.payload["adapter_registration_event_hash"] == registration_hash
            for event in self.store.query_events(event_type="AsharePITSnapshotRecorded")
        ):
            raise EventTransitionError("provider field audit must precede snapshot production")

    def _events(self, hashes: tuple[str, ...], event_type: str) -> list[ResearchEventEnvelope]:
        _hashes(hashes, event_type)
        by_hash = {event.event_hash: event for event in self.store.query_events()}
        result = [by_hash.get(value) for value in hashes]
        if any(event is None or event.event_type != event_type for event in result):
            raise EventTransitionError(f"{event_type} exact sources are incomplete")
        return [event for event in result if event is not None]


__all__ = [
    "PROHIBITED_FINANCIAL_STATEMENT_FIELDS_V1",
    "ProviderAuthorityDecisionV1",
    "ProviderFieldPITAuditV1",
    "ProviderInterfacePITAuditV1",
    "ProviderPITAuditServiceV1",
    "RecordedProviderAuthorityDecisionV1",
    "RecordedProviderFieldPITAuditV1",
    "RecordedProviderInterfacePITAuditV1",
    "STRICT_ACTIVATION_REQUIRED_FIELDS_V1",
    "baostock_golden_cohort_field_audits_v1",
    "tushare_activation_field_audits_v1",
]
