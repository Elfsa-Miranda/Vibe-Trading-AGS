from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from src.alpha_foundry.activation.provider_pit_audit_v1 import (
    PROHIBITED_FINANCIAL_STATEMENT_FIELDS_V1,
    STRICT_ACTIVATION_REQUIRED_FIELDS_V1,
    ProviderFieldPITAuditV1,
    ProviderPITAuditServiceV1,
    tushare_activation_field_audits_v1,
)
from src.alpha_quality.flags import ResolvedAGSFlags
from src.alpha_quality.pit_adapter_v1 import (
    AsharePITAdapterDescriptorV1,
    AsharePITAdapterRegistryV1,
    AsharePITSnapshotRequestV1,
    AsharePITSourceBundleV1,
)
from src.alpha_quality.pit_service_v2 import AsharePITAdapterRegistrationServiceV1
from src.research_ledger.events import EventTransitionError, ResearchEventStore
from src.research_ledger.hash_utils import canonical_json_hash


def _hash(name: str) -> str:
    return canonical_json_hash({"fixture": name})


class _AuditFixtureAdapter:
    def descriptor(self) -> AsharePITAdapterDescriptorV1:
        return AsharePITAdapterDescriptorV1(
            adapter_id="activation-audit-fixture-v1",
            provider="fixture-provider",
            adapter_version="1.0.0",
            market="CN_A_SHARE",
            calendar_id="XSHG_XSHE",
            timezone="Asia/Shanghai",
            membership_dataset="fixture-membership",
            security_master_dataset="fixture-security",
            corporate_action_dataset="fixture-actions",
            trade_state_dataset="fixture-states",
            price_dataset="fixture-prices",
            availability_semantics="provider_release_timestamp.v1",
            adjustment_semantics="raw_prices_plus_dated_actions.v1",
        )

    def load(self, request: AsharePITSnapshotRequestV1) -> AsharePITSourceBundleV1:
        raise AssertionError("provider PIT audit must not load market data")


def _setup(tmp_path: Path):
    flags = ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_ALPHA_FOUNDRY": "1",
            "VIBE_TRADING_ALPHA_SCORECARD": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
        }
    )
    store = ResearchEventStore(
        tmp_path / "events.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=flags,
        code_version="provider-pit-audit-test",
    )
    registry = AsharePITAdapterRegistryV1({"activation-audit-fixture-v1": _AuditFixtureAdapter()})
    registration = AsharePITAdapterRegistrationServiceV1(store, flags=flags, registry=registry).register(
        adapter_id="activation-audit-fixture-v1", run_id="provider-registry"
    )
    return store, registration.event


def _best_effort_field(**overrides: object) -> ProviderFieldPITAuditV1:
    values: dict[str, object] = {
        "provider": "fixture-provider",
        "adapter_id": "activation-audit-fixture-v1",
        "interface": "daily",
        "field_name": "close",
        "event_or_reporting_time": "trade_date",
        "effective_time": "exchange_session_close",
        "provider_availability_time": "provider_row_timestamp_not_exposed",
        "retrieval_vintage": "source_manifest_source_as_of",
        "revision_history": "unknown",
        "cross_interface_consistency": "not_verified",
        "missingness_policy": "fail_closed",
        "rate_limit_retry_behavior": "partial_batch_possible",
        "cache_vintage": "bound_to_manifest",
        "audit_sample_definition": "fixture field audit",
        "compliance_level": "best_effort",
        "claim_scope_ceiling": "best_effort",
        "allowed_claim_scopes": ("engineering_schema_validation",),
        "prohibited_claim_scopes": ("formal_strict_pit_activation",),
        "evidence_kinds": ("local_implementation_review",),
        "audit_evidence_hashes": (_hash("implementation"),),
        "limitations": ("ROW_AVAILABILITY_NOT_EXPOSED",),
    }
    values.update(overrides)
    return ProviderFieldPITAuditV1.create(**values)  # type: ignore[arg-type]


def test_provider_field_requires_strict_pit_audit(tmp_path: Path) -> None:
    store, registration = _setup(tmp_path)
    service = ProviderPITAuditServiceV1(store)

    with pytest.raises(ValueError, match="canonical hashes"):
        service.decide_authority(
            run_id="activation-cycle",
            adapter_registration_event_hash=registration.event_hash,
            interface_audit_event_hashes=(),
            required_interfaces=("daily",),
        )


def test_best_effort_field_cannot_support_strict_claim() -> None:
    audit = _best_effort_field()

    with pytest.raises(ValueError, match="verified_strict"):
        replace(audit, claim_scope_ceiling="verified_strict")


def test_verified_strict_requires_actual_audit_evidence_hash() -> None:
    with pytest.raises(ValueError, match="verified_strict"):
        ProviderFieldPITAuditV1.create(
            provider="fixture-provider",
            adapter_id="activation-audit-fixture-v1",
            interface="daily",
            field_name="close",
            event_or_reporting_time="trade_date",
            effective_time="exchange_session_close",
            provider_availability_time="receipt_ingested_at",
            retrieval_vintage="partition_vintage_id",
            revision_history="complete",
            cross_interface_consistency="verified",
            missingness_policy="fail_closed",
            rate_limit_retry_behavior="complete_manifest_required",
            cache_vintage="bound_to_manifest",
            audit_sample_definition="fixture strict audit",
            compliance_level="verified_strict",
            claim_scope_ceiling="verified_strict",
            allowed_claim_scopes=("formal_strict_pit_activation",),
            prohibited_claim_scopes=("test_forward",),
            evidence_kinds=("independent_crosscheck",),
            audit_evidence_hashes=(),
            limitations=(),
        )


def test_event_effective_available_and_vintage_times_are_distinct() -> None:
    audits = tushare_activation_field_audits_v1(adapter_registration_event_hash=_hash("tushare-registration"))
    ann = next(audit for audit in audits if audit.field_name == "corporate_action")

    assert (
        len(
            {
                ann.event_or_reporting_time,
                ann.effective_time,
                ann.provider_availability_time,
                ann.retrieval_vintage,
            }
        )
        == 4
    )
    assert ann.event_or_reporting_time == "provider_trade_or_announcement_date"
    assert ann.effective_time == "market_effective_date"


def test_revision_history_gap_caps_claim_scope() -> None:
    audits = tushare_activation_field_audits_v1(adapter_registration_event_hash=_hash("tushare-registration"))

    assert all(
        audit.claim_scope_ceiling != "verified_strict"
        for audit in audits
        if audit.revision_history in {"partial", "unknown"}
    )
    assert set(a.field_name for a in audits) == set(STRICT_ACTIVATION_REQUIRED_FIELDS_V1)
    assert not set(PROHIBITED_FINANCIAL_STATEMENT_FIELDS_V1).intersection(audit.field_name for audit in audits)


def test_narrow_activation_catalog_matches_existing_adapter_interfaces() -> None:
    audits = tushare_activation_field_audits_v1(adapter_registration_event_hash=_hash("tushare-registration"))

    assert {audit.interface for audit in audits} == {
        "adj_factor",
        "daily",
        "dividend",
        "index_weight",
        "namechange",
        "stk_limit",
        "stock_basic",
        "suspend_d",
        "trade_cal",
    }
    assert {audit.compliance_level for audit in audits} == {
        "non_compliant",
        "unknown",
    }
    assert all(audit.claim_scope_ceiling != "verified_strict" for audit in audits)


def test_no_secret_in_provider_artifacts(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = "provider-secret-sentinel-never-persist"
    monkeypatch.setenv("TUSHARE_TOKEN", sentinel)
    audits = tushare_activation_field_audits_v1(adapter_registration_event_hash=_hash("tushare-registration"))

    serialized = json.dumps([audit.to_dict() for audit in audits], sort_keys=True)
    assert sentinel not in serialized
    assert "TUSHARE_TOKEN" not in serialized


def test_partial_batch_cannot_create_complete_manifest(
    tmp_path: Path,
) -> None:
    store, registration = _setup(tmp_path)
    service = ProviderPITAuditServiceV1(store)
    field = service.record_field(
        run_id="activation-cycle",
        adapter_registration_event_hash=registration.event_hash,
        audit=_best_effort_field(
            audit_evidence_hashes=(registration.event_hash,),
        ),
    )
    interface = service.record_interface(
        run_id="activation-cycle",
        adapter_registration_event_hash=registration.event_hash,
        field_audit_event_hashes=(field.event.event_hash,),
        required_fields=("close",),
    )
    decision = service.decide_authority(
        run_id="activation-cycle",
        adapter_registration_event_hash=registration.event_hash,
        interface_audit_event_hashes=(interface.event.event_hash,),
        required_interfaces=("daily",),
    )

    assert interface.audit.completeness == "complete"
    assert interface.audit.complete_manifest_eligible is False
    assert decision.decision.authority_status == "blocked"
    assert decision.decision.activation_eligible is False
    assert "PROVIDER_COMPLETE_MANIFEST_UNPROVEN" in decision.decision.blocker_codes
    assert "ADAPTER_AUTHORITY_NOT_PRODUCTION" in decision.decision.blocker_codes
    assert store.verify_chain()


def test_field_audit_must_match_registered_provider(tmp_path: Path) -> None:
    store, registration = _setup(tmp_path)

    with pytest.raises(EventTransitionError, match="differs from adapter"):
        ProviderPITAuditServiceV1(store).record_field(
            run_id="activation-cycle",
            adapter_registration_event_hash=registration.event_hash,
            audit=_best_effort_field(provider="different-provider"),
        )
