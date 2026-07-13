"""Producer-bound final-test authority with raw-partition recomputation."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath
from typing import Any, Literal, Mapping, Protocol, cast
from uuid import uuid4

import numpy as np
import pandas as pd

from src.alpha_foundry.dsl.executable import DEFAULT_EXECUTABLE_GRAMMAR
from src.research_ledger.events import EventDraft, EventTransitionError, ResearchEventStore
from src.research_ledger.events.artifacts import (
    AtomicContentAddressedArtifactWriter,
    validate_artifact_references,
)
from src.research_ledger.events.model import ResearchEventEnvelope
from src.research_ledger.hash_utils import canonical_json_hash, utc_now_iso


PRODUCER_SCHEMA_VERSION = "final_test_authority.v2"
PRODUCER_POLICY_HASH = canonical_json_hash(
    {
        "schema_version": "final_test_authority_policy.v2",
        "eligibility": "authoritative_candidate_zoo_decision",
        "one_shot_key": "research_family_exact_scope_without_timestamp",
        "provider_output": "raw_bounded_parquet_partition_refs",
        "statistics": "runner_recomputed_dependence_aware_lower_bounds",
        "failure_terminal": "exactly_one_artifact_or_failed_after_access",
        "legacy_v1_authority": "read_only_research_only_cap",
    }
)
FINAL_ARTIFACT_MEDIA_TYPE = "application/vnd.vibe.final-test-artifact-v2+json"
RAW_PARTITION_MEDIA_TYPE = "application/vnd.apache.parquet"


def _hash(value: str, name: str) -> None:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:"):
        raise ValueError(f"{name} must be a canonical sha256 hash")
    try:
        int(value[7:], 16)
    except ValueError as exc:
        raise ValueError(f"{name} must be a canonical sha256 hash") from exc


@dataclass(frozen=True)
class FinalDependenceConfigV2:
    method: Literal["hac", "nonoverlapping_cohort", "block_bootstrap"]
    confidence_level: float
    hac_lags: int
    cohort_spacing: int
    block_length: int
    bootstrap_replicates: int
    bootstrap_seed: int
    minimum_effective_observations: int
    rank_ic_sesoi: float
    net_return_sesoi: float
    rank_ic_bounds: tuple[float, float] = (-1.0, 1.0)
    net_return_bounds: tuple[float, float] = (-1.0, 10.0)
    confidence_rule: Literal["two_sided_lower_bound_gte_sesoi"] = "two_sided_lower_bound_gte_sesoi"
    schema_version: Literal["final_dependence_config.v2"] = "final_dependence_config.v2"

    def __post_init__(self) -> None:
        if self.confidence_level != 0.95 or self.minimum_effective_observations < 2:
            raise ValueError("final dependence confidence/sample policy is invalid")
        if (
            min(
                self.hac_lags,
                self.cohort_spacing,
                self.block_length,
                self.bootstrap_replicates,
            )
            < 1
        ):
            raise ValueError("final dependence parameters must be positive")
        if self.bootstrap_replicates < 100:
            raise ValueError("final bootstrap requires at least 100 replicates")
        for bounds, sesoi in (
            (self.rank_ic_bounds, self.rank_ic_sesoi),
            (self.net_return_bounds, self.net_return_sesoi),
        ):
            if (
                len(bounds) != 2
                or not all(math.isfinite(item) for item in (*bounds, sesoi))
                or bounds[0] >= bounds[1]
                or not bounds[0] <= sesoi <= bounds[1]
            ):
                raise ValueError("final SESOI/range policy is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "method": self.method,
            "confidence_level": self.confidence_level,
            "hac_lags": self.hac_lags,
            "cohort_spacing": self.cohort_spacing,
            "block_length": self.block_length,
            "bootstrap_replicates": self.bootstrap_replicates,
            "bootstrap_seed": self.bootstrap_seed,
            "minimum_effective_observations": self.minimum_effective_observations,
            "rank_ic_sesoi": self.rank_ic_sesoi,
            "net_return_sesoi": self.net_return_sesoi,
            "rank_ic_bounds": list(self.rank_ic_bounds),
            "net_return_bounds": list(self.net_return_bounds),
            "confidence_rule": self.confidence_rule,
        }

    @property
    def config_hash(self) -> str:
        return cast(str, canonical_json_hash(self.to_dict()))


@dataclass(frozen=True)
class FinalScopeConfigV2:
    provider_id: str
    provider_version: str
    data_snapshot_hash: str
    period_start: str
    period_end: str
    raw_fields: tuple[str, ...]
    transform_pipeline_hash: str
    cost_model_hash: str
    regime_config_hash: str
    backend_hash: str
    execution_lag: int
    return_horizon: int
    rebalance_cadence: int
    round_trip_cost_bps: float
    selection_fraction: float
    schema_version: Literal["final_scope_config.v2"] = "final_scope_config.v2"

    def __post_init__(self) -> None:
        for name in (
            "data_snapshot_hash",
            "transform_pipeline_hash",
            "cost_model_hash",
            "regime_config_hash",
            "backend_hash",
        ):
            _hash(str(getattr(self, name)), name)
        try:
            start = date.fromisoformat(self.period_start)
            end = date.fromisoformat(self.period_end)
        except ValueError as exc:
            raise ValueError("final scope dates must be ISO dates") from exc
        required = {"close", "membership", "open", "tradable"}
        if (
            not self.provider_id
            or not self.provider_version
            or end < start
            or self.raw_fields != tuple(sorted(set(self.raw_fields)))
            or not required.issubset(self.raw_fields)
            or min(self.execution_lag, self.return_horizon, self.rebalance_cadence) < 1
            or not math.isfinite(self.round_trip_cost_bps)
            or self.round_trip_cost_bps < 0.0
            or not 0.0 < self.selection_fraction <= 1.0
        ):
            raise ValueError("final scope configuration is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "data_snapshot_hash": self.data_snapshot_hash,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "raw_fields": list(self.raw_fields),
            "transform_pipeline_hash": self.transform_pipeline_hash,
            "cost_model_hash": self.cost_model_hash,
            "regime_config_hash": self.regime_config_hash,
            "backend_hash": self.backend_hash,
            "execution_lag": self.execution_lag,
            "return_horizon": self.return_horizon,
            "rebalance_cadence": self.rebalance_cadence,
            "round_trip_cost_bps": self.round_trip_cost_bps,
            "selection_fraction": self.selection_fraction,
        }

    @property
    def scope_hash(self) -> str:
        return cast(str, canonical_json_hash(self.to_dict()))


@dataclass(frozen=True)
class FinalRawProviderDescriptorV2:
    provider_id: str
    provider_version: str
    provider_policy_hash: str
    output_capability: Literal["raw_bounded_parquet_partition_refs"] = "raw_bounded_parquet_partition_refs"

    def __post_init__(self) -> None:
        if not self.provider_id or not self.provider_version:
            raise ValueError("final raw provider identity is required")
        _hash(self.provider_policy_hash, "provider_policy_hash")

    def to_dict(self) -> dict[str, str]:
        return dict(self.__dict__)


class FinalRawPartitionProviderV2(Protocol):
    def descriptor(self) -> FinalRawProviderDescriptorV2: ...

    def resolve_partitions(self, eligibility: "FinalEvaluationEligibilityV2") -> "FinalRawPartitionBundleV2": ...


class FinalRawProviderRegistryV2:
    def __init__(self, providers: Mapping[str, FinalRawPartitionProviderV2]) -> None:
        self._providers = dict(providers)
        if not self._providers or any(
            key != provider.descriptor().provider_id for key, provider in self._providers.items()
        ):
            raise ValueError("final raw provider registry identity differs")

    def provider(self, provider_id: str) -> FinalRawPartitionProviderV2:
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise KeyError("final raw provider is not registered") from exc


class FinalRawProviderRegistrationServiceV2:
    def __init__(self, store: ResearchEventStore, registry: FinalRawProviderRegistryV2) -> None:
        self.store = store
        self.registry = registry

    def register(self, provider_id: str, *, run_id: str) -> ResearchEventEnvelope:
        descriptor = self.registry.provider(provider_id).descriptor()
        existing = [
            event
            for event in self.store.query_events(event_type="FinalRawProviderV2Registered")
            if event.payload["descriptor"]["provider_id"] == provider_id
            and event.payload["descriptor"]["provider_version"] == descriptor.provider_version
        ]
        if existing:
            if existing[0].run_id != run_id:
                raise EventTransitionError("final raw provider registration run differs")
            return existing[0]
        content = {
            "schema_version": "final_raw_provider_registration.v2",
            "descriptor": descriptor.to_dict(),
            "producer_schema_version": PRODUCER_SCHEMA_VERSION,
            "producer_policy_hash": PRODUCER_POLICY_HASH,
        }
        registration_hash = canonical_json_hash(content)
        registration_id = "final-provider-" + registration_hash[7:31]
        return self.store._append_producer_event(
            EventDraft(
                event_type="FinalRawProviderV2Registered",
                entity_id=registration_id,
                run_id=run_id,
                payload_schema_version="final_raw_provider_registered.v2",
                idempotency_key="final-provider:" + provider_id + ":" + descriptor.provider_version,
                payload={
                    "registration_id": registration_id,
                    "registration_hash": registration_hash,
                    "descriptor": descriptor.to_dict(),
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                    "artifact_refs": [],
                },
            )
        )


@dataclass(frozen=True)
class FinalEvaluationEligibilityV2:
    research_family_id: str
    factor_spec_id: str
    factor_definition_event_hash: str
    prefinal_decision_event_hash: str
    prefinal_decision_hash: str
    prefinal_decision: Literal["research_only", "candidate_zoo"]
    contract_event_hash: str
    contract_hash: str
    provider_registration_event_hash: str
    scope: FinalScopeConfigV2
    dependence: FinalDependenceConfigV2
    final_evaluation_key: str
    eligibility_hash: str


class FinalEvaluationEligibilityServiceV2:
    def __init__(self, store: ResearchEventStore) -> None:
        if not store.flags.enabled("VIBE_TRADING_DECISION_V2"):
            raise RuntimeError("final eligibility capability is disabled")
        self.store = store

    def register(
        self,
        *,
        run_id: str,
        factor_definition_event_hash: str,
        prefinal_decision_event_hash: str,
        contract_event_hash: str,
        provider_registration_event_hash: str,
        scope: FinalScopeConfigV2,
        dependence: FinalDependenceConfigV2,
    ) -> tuple[FinalEvaluationEligibilityV2, ResearchEventEnvelope]:
        by_hash = {event.event_hash: event for event in self.store.query_events()}
        definition = by_hash.get(factor_definition_event_hash)
        decision = by_hash.get(prefinal_decision_event_hash)
        contract = by_hash.get(contract_event_hash)
        provider_registration = by_hash.get(provider_registration_event_hash)
        if (
            definition is None
            or definition.event_type != "FactorDefinitionRecorded"
            or decision is None
            or decision.event_type != "QualityDecisionV4Recorded"
            or contract is None
            or contract.event_type != "ResolvedEvaluationContractRegistered"
            or provider_registration is None
            or provider_registration.event_type != "FinalRawProviderV2Registered"
        ):
            raise EventTransitionError("final eligibility sources are incomplete")
        if any(event.run_id != run_id for event in (definition, decision, contract, provider_registration)):
            raise EventTransitionError("final eligibility cannot mix runs")
        descriptor = provider_registration.payload["descriptor"]
        if descriptor["provider_id"] != scope.provider_id or descriptor["provider_version"] != scope.provider_version:
            raise EventTransitionError("final provider registration differs from scope")
        if decision.payload["factor_spec_id"] != definition.entity_id or decision.payload["decision"] not in {
            "research_only",
            "candidate_zoo",
        }:
            raise EventTransitionError("final eligibility requires authoritative prefinal decision")
        family_id = str(contract.payload["research_family_id"])
        existing = [
            event
            for event in self.store.query_events(event_type="FinalEvaluationEligibilityRecorded")
            if event.payload["research_family_id"] == family_id
        ]
        if existing:
            frozen = self._from_event(existing[0])
            if (
                frozen.factor_spec_id != definition.entity_id
                or frozen.prefinal_decision_hash != decision.payload["decision_hash"]
                or frozen.contract_hash != contract.payload["contract_hash"]
                or frozen.provider_registration_event_hash != provider_registration.event_hash
                or frozen.scope.to_dict() != scope.to_dict()
                or frozen.dependence.to_dict() != dependence.to_dict()
            ):
                raise EventTransitionError("research family final eligibility is already frozen")
            return frozen, existing[0]
        key_content = {
            "schema_version": "final_evaluation_key.v2",
            "research_family_id": family_id,
            "scope_hash": scope.scope_hash,
            "dependence_config_hash": dependence.config_hash,
            "producer_policy_hash": PRODUCER_POLICY_HASH,
        }
        final_key = canonical_json_hash(key_content)
        content = {
            "schema_version": "final_evaluation_eligibility.v2",
            "research_family_id": family_id,
            "factor_spec_id": definition.entity_id,
            "factor_definition_event_hash": definition.event_hash,
            "prefinal_decision_hash": decision.payload["decision_hash"],
            "prefinal_decision": decision.payload["decision"],
            "contract_hash": contract.payload["contract_hash"],
            "provider_registration_event_hash": provider_registration.event_hash,
            "scope_config": scope.to_dict(),
            "dependence_config": dependence.to_dict(),
            "final_evaluation_key": final_key,
            "producer_schema_version": PRODUCER_SCHEMA_VERSION,
            "producer_policy_hash": PRODUCER_POLICY_HASH,
        }
        eligibility_hash = canonical_json_hash(content)
        eligibility_id = "final-eligibility-" + eligibility_hash[7:31]
        event = self.store._append_producer_event(
            EventDraft(
                event_type="FinalEvaluationEligibilityRecorded",
                entity_id=eligibility_id,
                run_id=run_id,
                payload_schema_version="final_evaluation_eligibility_recorded.v2",
                idempotency_key="final-eligibility-family:" + family_id,
                payload={
                    "eligibility_id": eligibility_id,
                    "eligibility_hash": eligibility_hash,
                    "final_evaluation_key": final_key,
                    "research_family_id": family_id,
                    "factor_spec_id": definition.entity_id,
                    "factor_definition_event_hash": definition.event_hash,
                    "prefinal_decision_event_hash": decision.event_hash,
                    "prefinal_decision_hash": decision.payload["decision_hash"],
                    "prefinal_decision": decision.payload["decision"],
                    "contract_event_hash": contract.event_hash,
                    "contract_hash": contract.payload["contract_hash"],
                    "provider_registration_event_hash": provider_registration.event_hash,
                    "scope_config": scope.to_dict(),
                    "dependence_config": dependence.to_dict(),
                    "source_event_hashes": sorted(
                        [
                            definition.event_hash,
                            decision.event_hash,
                            contract.event_hash,
                            provider_registration.event_hash,
                        ]
                    ),
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                    "artifact_refs": [],
                },
            )
        )
        return self._from_event(event), event

    @staticmethod
    def _from_event(event: ResearchEventEnvelope) -> FinalEvaluationEligibilityV2:
        return FinalEvaluationEligibilityV2(
            research_family_id=str(event.payload["research_family_id"]),
            factor_spec_id=str(event.payload["factor_spec_id"]),
            factor_definition_event_hash=str(event.payload["factor_definition_event_hash"]),
            prefinal_decision_event_hash=str(event.payload["prefinal_decision_event_hash"]),
            prefinal_decision_hash=str(event.payload["prefinal_decision_hash"]),
            prefinal_decision=str(event.payload["prefinal_decision"]),  # type: ignore[arg-type]
            contract_event_hash=str(event.payload["contract_event_hash"]),
            contract_hash=str(event.payload["contract_hash"]),
            provider_registration_event_hash=str(event.payload["provider_registration_event_hash"]),
            scope=FinalScopeConfigV2(**dict(event.payload["scope_config"])),
            dependence=FinalDependenceConfigV2(
                **{
                    **dict(event.payload["dependence_config"]),
                    "rank_ic_bounds": tuple(event.payload["dependence_config"]["rank_ic_bounds"]),
                    "net_return_bounds": tuple(event.payload["dependence_config"]["net_return_bounds"]),
                }
            ),
            final_evaluation_key=str(event.payload["final_evaluation_key"]),
            eligibility_hash=str(event.payload["eligibility_hash"]),
        )


@dataclass(frozen=True)
class FinalScopeCapabilityV2:
    token_id: str
    token_hash: str
    eligibility_event_hash: str
    eligibility_hash: str
    final_evaluation_key: str
    exact_scope_hash: str
    issued_at: str


@dataclass
class _CapabilityStateV2:
    capability: FinalScopeCapabilityV2
    consumed: bool = False


class FinalScopeAuthorityV2:
    def __init__(self, store: ResearchEventStore) -> None:
        if not store.flags.enabled("VIBE_TRADING_DECISION_V2"):
            raise RuntimeError("final scope authority v2 is disabled")
        self.store = store
        self._states: dict[str, _CapabilityStateV2] = {}

    def issue(self, eligibility_event_hash: str, *, run_id: str) -> FinalScopeCapabilityV2:
        events = [
            event
            for event in self.store.query_events(event_type="FinalEvaluationEligibilityRecorded")
            if event.event_hash == eligibility_event_hash
        ]
        if len(events) != 1 or events[0].run_id != run_id:
            raise EventTransitionError("capability requires exact final eligibility")
        eligibility = events[0]
        prior = [
            event
            for event in self.store.query_events(event_type="FinalTestCapabilityV2Issued")
            if event.payload["final_evaluation_key"] == eligibility.payload["final_evaluation_key"]
        ]
        if prior:
            self.record_taint(
                eligibility_event_hash,
                run_id=run_id,
                taint_class="variant",
                reason_code="FINAL_VARIANT_REISSUE_ATTEMPT",
            )
            raise EventTransitionError("research family final capability was already issued")
        frozen_eligibility = FinalEvaluationEligibilityServiceV2._from_event(eligibility)
        token_id = "final-v2-token-" + str(uuid4())
        issued_at = utc_now_iso()
        token_content = {
            "token_id": token_id,
            "eligibility_event_hash": eligibility.event_hash,
            "eligibility_hash": eligibility.payload["eligibility_hash"],
            "final_evaluation_key": eligibility.payload["final_evaluation_key"],
            "exact_scope_hash": canonical_json_hash(
                {
                    "scope_config": frozen_eligibility.scope.to_dict(),
                    "dependence_config": frozen_eligibility.dependence.to_dict(),
                }
            ),
            "issued_at": issued_at,
        }
        token_hash = canonical_json_hash(token_content)
        capability = FinalScopeCapabilityV2(
            token_id=token_id,
            token_hash=token_hash,
            eligibility_event_hash=eligibility.event_hash,
            eligibility_hash=str(eligibility.payload["eligibility_hash"]),
            final_evaluation_key=str(eligibility.payload["final_evaluation_key"]),
            exact_scope_hash=str(token_content["exact_scope_hash"]),
            issued_at=issued_at,
        )
        self.store._append_producer_event(
            EventDraft(
                event_type="FinalTestCapabilityV2Issued",
                entity_id=token_id,
                run_id=run_id,
                payload_schema_version="final_test_capability_issued.v2",
                idempotency_key="final-v2-capability:" + capability.final_evaluation_key,
                payload={
                    "capability_id": token_id,
                    "capability_hash": token_hash,
                    "eligibility_event_hash": eligibility.event_hash,
                    "eligibility_hash": eligibility.payload["eligibility_hash"],
                    "final_evaluation_key": eligibility.payload["final_evaluation_key"],
                    "research_family_id": eligibility.payload["research_family_id"],
                    "exact_scope_hash": capability.exact_scope_hash,
                    "issued_at": issued_at,
                    "source_event_hashes": [eligibility.event_hash],
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                    "artifact_refs": [],
                },
            )
        )
        self._states[token_hash] = _CapabilityStateV2(capability)
        return capability

    def authorize(
        self,
        capability: FinalScopeCapabilityV2,
        eligibility: FinalEvaluationEligibilityV2,
        *,
        run_id: str,
    ) -> ResearchEventEnvelope:
        state = self._states.get(capability.token_hash)
        if state is None or state.capability != capability:
            raise EventTransitionError("unknown final v2 capability")
        request_scope_hash = canonical_json_hash(
            {
                "scope_config": eligibility.scope.to_dict(),
                "dependence_config": eligibility.dependence.to_dict(),
            }
        )
        outcome: Literal["allowed", "denied"] = "allowed"
        taint_class: Literal["none", "physical", "selection", "variant"] = "none"
        reason = "FINAL_V2_ACCESS_ALLOWED"
        if state.consumed:
            outcome, taint_class, reason = "denied", "physical", "FINAL_PHYSICAL_REOPEN"
        elif (
            capability.final_evaluation_key != eligibility.final_evaluation_key
            or capability.eligibility_hash != eligibility.eligibility_hash
        ):
            outcome, taint_class, reason = "denied", "variant", "FINAL_VARIANT_SCOPE_MISMATCH"
        elif capability.exact_scope_hash != request_scope_hash:
            outcome, taint_class, reason = "denied", "selection", "FINAL_SELECTION_SCOPE_MISMATCH"
        state.consumed = True
        accessed_at = utc_now_iso()
        content = {
            "final_evaluation_key": capability.final_evaluation_key,
            "capability_hash": capability.token_hash,
            "request_scope_hash": request_scope_hash,
            "outcome": outcome,
            "taint_class": taint_class,
            "reason_code": reason,
            "accessed_at": accessed_at,
        }
        access_hash = canonical_json_hash(content)
        access_id = "final-v2-access-" + access_hash[7:31]
        event = self.store._append_producer_event(
            EventDraft(
                event_type="FinalOutcomeAccessV2Recorded",
                entity_id=access_id,
                run_id=run_id,
                payload_schema_version="final_outcome_access_recorded.v2",
                payload={
                    "access_id": access_id,
                    "access_hash": access_hash,
                    "final_evaluation_key": capability.final_evaluation_key,
                    "eligibility_event_hash": capability.eligibility_event_hash,
                    "eligibility_hash": capability.eligibility_hash,
                    "capability_hash": capability.token_hash,
                    "request_scope_hash": request_scope_hash,
                    "outcome": outcome,
                    "taint_class": taint_class,
                    "reason_code": reason,
                    "accessed_at": accessed_at,
                    "source_event_hashes": [capability.eligibility_event_hash],
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                    "artifact_refs": [],
                },
            )
        )
        if outcome == "denied":
            self.record_taint(
                capability.eligibility_event_hash,
                run_id=run_id,
                taint_class=cast(Literal["physical", "selection", "variant"], taint_class),
                reason_code=reason,
                source_event_hash=event.event_hash,
            )
            raise EventTransitionError(reason)
        return event

    def record_taint(
        self,
        eligibility_event_hash: str,
        *,
        run_id: str,
        taint_class: Literal["physical", "selection", "variant"],
        reason_code: str,
        source_event_hash: str | None = None,
    ) -> ResearchEventEnvelope:
        eligibility = next(
            event
            for event in self.store.query_events(event_type="FinalEvaluationEligibilityRecorded")
            if event.event_hash == eligibility_event_hash
        )
        content = {
            "final_evaluation_key": eligibility.payload["final_evaluation_key"],
            "research_family_id": eligibility.payload["research_family_id"],
            "taint_class": taint_class,
            "reason_code": reason_code,
            "source_event_hash": source_event_hash,
        }
        taint_hash = canonical_json_hash(content)
        taint_id = "final-v2-taint-" + taint_hash[7:31]
        sources = sorted({eligibility.event_hash, *(() if source_event_hash is None else (source_event_hash,))})
        return self.store._append_producer_event(
            EventDraft(
                event_type="FinalContaminationV2Recorded",
                entity_id=taint_id,
                run_id=run_id,
                payload_schema_version="final_contamination_recorded.v2",
                idempotency_key="final-v2-taint:" + taint_hash,
                payload={
                    "taint_id": taint_id,
                    "taint_hash": taint_hash,
                    "final_evaluation_key": eligibility.payload["final_evaluation_key"],
                    "research_family_id": eligibility.payload["research_family_id"],
                    "eligibility_event_hash": eligibility.event_hash,
                    "taint_class": taint_class,
                    "reason_code": reason_code,
                    "recorded_at": utc_now_iso(),
                    "source_event_hashes": sources,
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                    "artifact_refs": [],
                },
            )
        )

    def is_tainted(self, final_evaluation_key: str) -> bool:
        return any(
            event.payload["final_evaluation_key"] == final_evaluation_key
            for event in self.store.query_events(event_type="FinalContaminationV2Recorded")
        )


@dataclass(frozen=True)
class FinalRawPartitionRefV2:
    relative_path: str
    artifact_hash: str
    media_type: Literal["application/vnd.apache.parquet"]
    partition_start: str
    partition_end: str
    row_count: int

    def __post_init__(self) -> None:
        path = PurePosixPath(self.relative_path)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in self.relative_path
            or "\x00" in self.relative_path
            or self.media_type != RAW_PARTITION_MEDIA_TYPE
        ):
            raise ValueError("final raw partition path is unsafe")
        _hash(self.artifact_hash, "raw_partition_hash")
        try:
            start = date.fromisoformat(self.partition_start)
            end = date.fromisoformat(self.partition_end)
        except ValueError as exc:
            raise ValueError("final partition dates must be ISO dates") from exc
        if end < start or self.row_count < 1:
            raise ValueError("final raw partition bounds/count are invalid")


@dataclass(frozen=True)
class FinalRawPartitionBundleV2:
    provider_id: str
    provider_version: str
    data_snapshot_hash: str
    period_start: str
    period_end: str
    fields: tuple[str, ...]
    partitions: tuple[FinalRawPartitionRefV2, ...]
    bundle_hash: str

    def __post_init__(self) -> None:
        _hash(self.data_snapshot_hash, "data_snapshot_hash")
        if (
            not self.provider_id
            or not self.provider_version
            or self.period_end < self.period_start
            or self.fields != tuple(sorted(set(self.fields)))
            or not self.partitions
        ):
            raise ValueError("final raw partition bundle is invalid")
        content = {
            "schema_version": "final_raw_partition_bundle.v2",
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "data_snapshot_hash": self.data_snapshot_hash,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "fields": list(self.fields),
            "partitions": [item.__dict__ for item in self.partitions],
        }
        if canonical_json_hash(content) != self.bundle_hash:
            raise ValueError("final raw partition bundle hash differs")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "final_raw_partition_bundle.v2",
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "data_snapshot_hash": self.data_snapshot_hash,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "fields": list(self.fields),
            "partitions": [item.__dict__ for item in self.partitions],
            "bundle_hash": self.bundle_hash,
        }

    @classmethod
    def build(
        cls,
        *,
        provider_id: str,
        provider_version: str,
        data_snapshot_hash: str,
        period_start: str,
        period_end: str,
        fields: tuple[str, ...],
        partitions: tuple[FinalRawPartitionRefV2, ...],
    ) -> "FinalRawPartitionBundleV2":
        content = {
            "schema_version": "final_raw_partition_bundle.v2",
            "provider_id": provider_id,
            "provider_version": provider_version,
            "data_snapshot_hash": data_snapshot_hash,
            "period_start": period_start,
            "period_end": period_end,
            "fields": list(fields),
            "partitions": [item.__dict__ for item in partitions],
        }
        return cls(
            provider_id,
            provider_version,
            data_snapshot_hash,
            period_start,
            period_end,
            fields,
            partitions,
            canonical_json_hash(content),
        )


@dataclass(frozen=True)
class FinalTestMetricsV2:
    effective_observations: int
    rank_ic_mean: float
    rank_ic_standard_error: float
    rank_ic_lower_bound: float
    net_return_mean: float
    net_return_standard_error: float
    net_return_lower_bound: float
    quality_passed: bool

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class FinalTestArtifactV2:
    final_evaluation_key: str
    research_family_id: str
    factor_spec_id: str
    eligibility_hash: str
    raw_partition_bundle_hash: str
    dependence_config_hash: str
    metrics: FinalTestMetricsV2
    contaminated: bool
    limitations: tuple[str, ...]
    artifact_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "final_test_artifact.v2",
            "final_evaluation_key": self.final_evaluation_key,
            "research_family_id": self.research_family_id,
            "factor_spec_id": self.factor_spec_id,
            "eligibility_hash": self.eligibility_hash,
            "raw_partition_bundle_hash": self.raw_partition_bundle_hash,
            "dependence_config_hash": self.dependence_config_hash,
            "metrics": self.metrics.to_dict(),
            "contaminated": self.contaminated,
            "limitations": list(self.limitations),
            "artifact_hash": self.artifact_hash,
        }


class FinalTestRunnerV2:
    _artifact_keys = frozenset(
        {
            "schema_version",
            "final_evaluation_key",
            "research_family_id",
            "factor_spec_id",
            "eligibility_hash",
            "raw_partition_bundle_hash",
            "dependence_config_hash",
            "metrics",
            "contaminated",
            "limitations",
            "artifact_hash",
        }
    )

    def __init__(
        self,
        *,
        store: ResearchEventStore,
        authority: FinalScopeAuthorityV2,
        provider_registry: FinalRawProviderRegistryV2,
    ) -> None:
        if not store.flags.enabled("VIBE_TRADING_DECISION_V2"):
            raise RuntimeError("final runner v2 is disabled")
        self.store = store
        self.authority = authority
        self.provider_registry = provider_registry
        self.writer = AtomicContentAddressedArtifactWriter(store.artifact_root, max_bytes=4 * 1024**2)

    def run(
        self,
        eligibility_event_hash: str,
        capability: FinalScopeCapabilityV2,
        *,
        run_id: str,
    ) -> FinalTestArtifactV2:
        eligibility_event = next(
            event
            for event in self.store.query_events(event_type="FinalEvaluationEligibilityRecorded")
            if event.event_hash == eligibility_event_hash
        )
        eligibility = FinalEvaluationEligibilityServiceV2._from_event(eligibility_event)
        access = self.authority.authorize(capability, eligibility, run_id=run_id)
        try:
            provider = self.provider_registry.provider(eligibility.scope.provider_id)
            descriptor = provider.descriptor()
            if (
                descriptor.provider_id != eligibility.scope.provider_id
                or descriptor.provider_version != eligibility.scope.provider_version
            ):
                raise ValueError("final runtime provider differs from frozen registration")
            bundle = provider.resolve_partitions(eligibility)
            self._validate_bundle(bundle, eligibility)
            metrics = self._compute(bundle, eligibility)
            contaminated = self.authority.is_tainted(eligibility.final_evaluation_key)
            if contaminated and metrics.quality_passed:
                metrics = FinalTestMetricsV2(**{**metrics.to_dict(), "quality_passed": False})
            limitations = ["FINAL_TEST_DECISION_ONLY", "RAW_PARTITIONS_RECOMPUTED"]
            if contaminated:
                limitations.append("FINAL_SCOPE_CONTAMINATED")
            content = {
                "schema_version": "final_test_artifact.v2",
                "final_evaluation_key": eligibility.final_evaluation_key,
                "research_family_id": eligibility.research_family_id,
                "factor_spec_id": eligibility.factor_spec_id,
                "eligibility_hash": eligibility.eligibility_hash,
                "raw_partition_bundle_hash": bundle.bundle_hash,
                "dependence_config_hash": eligibility.dependence.config_hash,
                "metrics": metrics.to_dict(),
                "contaminated": contaminated,
                "limitations": sorted(limitations),
            }
            artifact = FinalTestArtifactV2(
                final_evaluation_key=eligibility.final_evaluation_key,
                research_family_id=eligibility.research_family_id,
                factor_spec_id=eligibility.factor_spec_id,
                eligibility_hash=eligibility.eligibility_hash,
                raw_partition_bundle_hash=bundle.bundle_hash,
                dependence_config_hash=eligibility.dependence.config_hash,
                metrics=metrics,
                contaminated=contaminated,
                limitations=tuple(sorted(limitations)),
                artifact_hash=canonical_json_hash(content),
            )
            stored = self.writer.write_json(
                namespace="final-test-v2",
                payload=artifact.to_dict(),
                schema_version="final_test_artifact.v2",
                semantic_hash_field="artifact_hash",
                closed_keys=self._artifact_keys,
                media_type=FINAL_ARTIFACT_MEDIA_TYPE,
            )
            artifact_id = "final-v2-artifact-" + artifact.artifact_hash[7:31]
            self.store._append_producer_event(
                EventDraft(
                    event_type="FinalTestArtifactV2Recorded",
                    entity_id=artifact_id,
                    run_id=run_id,
                    payload_schema_version="final_test_artifact_recorded.v2",
                    idempotency_key="final-v2-terminal:" + eligibility.final_evaluation_key,
                    payload={
                        "artifact_id": artifact_id,
                        "artifact_hash": artifact.artifact_hash,
                        "final_evaluation_key": eligibility.final_evaluation_key,
                        "research_family_id": eligibility.research_family_id,
                        "factor_spec_id": eligibility.factor_spec_id,
                        "eligibility_event_hash": eligibility_event.event_hash,
                        "eligibility_hash": eligibility.eligibility_hash,
                        "access_event_hash": access.event_hash,
                        "raw_partition_bundle_hash": bundle.bundle_hash,
                        "raw_partition_bundle": bundle.to_dict(),
                        "raw_partition_refs": [
                            {
                                "relative_path": item.relative_path,
                                "artifact_hash": item.artifact_hash,
                                "media_type": item.media_type,
                            }
                            for item in bundle.partitions
                        ],
                        "dependence_config_hash": eligibility.dependence.config_hash,
                        "metrics": metrics.to_dict(),
                        "quality_passed": metrics.quality_passed,
                        "contaminated": contaminated,
                        "limitations": list(artifact.limitations),
                        "source_event_hashes": sorted([eligibility_event.event_hash, access.event_hash]),
                        "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                        "producer_policy_hash": PRODUCER_POLICY_HASH,
                        "artifact_refs": [stored.reference()],
                    },
                )
            )
            self._record_selection(eligibility, run_id=run_id)
            return artifact
        except Exception as exc:
            self._record_failure(
                eligibility,
                eligibility_event,
                access,
                run_id=run_id,
                failure_code="FINAL_V2_RECOMPUTATION_FAILED",
                failure_class="validation" if isinstance(exc, ValueError) else "infrastructure",
            )
            self._record_selection(eligibility, run_id=run_id)
            raise

    def _validate_bundle(
        self,
        bundle: FinalRawPartitionBundleV2,
        eligibility: FinalEvaluationEligibilityV2,
    ) -> None:
        scope = eligibility.scope
        if (
            bundle.provider_id != scope.provider_id
            or bundle.provider_version != scope.provider_version
            or bundle.data_snapshot_hash != scope.data_snapshot_hash
            or bundle.period_start != scope.period_start
            or bundle.period_end != scope.period_end
            or bundle.fields != scope.raw_fields
            or not bundle.partitions
        ):
            self.authority.record_taint(
                next(
                    event.event_hash
                    for event in self.store.query_events(event_type="FinalEvaluationEligibilityRecorded")
                    if event.payload["eligibility_hash"] == eligibility.eligibility_hash
                ),
                run_id=next(
                    event.run_id
                    for event in self.store.query_events(event_type="FinalEvaluationEligibilityRecorded")
                    if event.payload["eligibility_hash"] == eligibility.eligibility_hash
                ),
                taint_class="selection",
                reason_code="FINAL_PROVIDER_SCOPE_MISMATCH",
            )
            raise ValueError("final raw provider scope differs from eligibility")
        references = [
            {
                "relative_path": item.relative_path,
                "artifact_hash": item.artifact_hash,
                "media_type": item.media_type,
            }
            for item in bundle.partitions
        ]
        validate_artifact_references(self.store.artifact_root, references)

    def _compute(
        self,
        bundle: FinalRawPartitionBundleV2,
        eligibility: FinalEvaluationEligibilityV2,
    ) -> FinalTestMetricsV2:
        frames: list[pd.DataFrame] = []
        for partition in bundle.partitions:
            path = self.store.artifact_root.joinpath(*partition.relative_path.split("/"))
            frame = pd.read_parquet(path)
            if len(frame) != partition.row_count:
                raise ValueError("final raw partition row count differs")
            frames.append(frame)
        raw = pd.concat(frames, ignore_index=True)
        required = {"date", "symbol", *eligibility.scope.raw_fields}
        if set(raw) != required or raw.duplicated(["date", "symbol"]).any():
            raise ValueError("final raw partition schema/keys differ")
        if not np.isfinite(raw[["close", "open"]].to_numpy(dtype=float)).all():
            raise ValueError("final raw partition contains non-finite prices")
        raw["date"] = pd.to_datetime(raw["date"])
        raw = raw.sort_values(["date", "symbol"], kind="mergesort")
        if (
            raw["date"].dt.date.min().isoformat() != eligibility.scope.period_start
            or raw["date"].dt.date.max().isoformat() != eligibility.scope.period_end
        ):
            raise ValueError("final raw partition date bounds differ")
        panel = {
            field: raw.pivot(index="date", columns="symbol", values=field)
            for field in eligibility.scope.raw_fields
            if field not in {"membership", "tradable"}
        }
        dates = panel["close"].index
        symbols = panel["close"].columns
        membership = raw.pivot(index="date", columns="symbol", values="membership").astype(bool)
        tradable = raw.pivot(index="date", columns="symbol", values="tradable").astype(bool)
        formula = self._canonical_formula(eligibility.factor_definition_event_hash)
        signal = DEFAULT_EXECUTABLE_GRAMMAR.evaluate(formula, panel)
        rank_ics: list[float] = []
        net_returns: list[float] = []
        scope = eligibility.scope
        maximum_signal = len(dates) - scope.return_horizon
        for index in range(0, maximum_signal, scope.rebalance_cadence):
            entry_index = index + scope.execution_lag
            exit_index = index + scope.return_horizon
            if entry_index >= len(dates) or exit_index >= len(dates):
                continue
            available = (
                membership.iloc[index]
                & tradable.iloc[entry_index]
                & signal.iloc[index].notna()
                & panel["open"].iloc[entry_index].notna()
                & panel["close"].iloc[exit_index].notna()
            )
            selected_symbols = symbols[available.to_numpy()]
            if len(selected_symbols) < 2:
                continue
            values = signal.loc[dates[index], selected_symbols].astype(float)
            returns = (
                panel["close"].loc[dates[exit_index], selected_symbols]
                / panel["open"].loc[dates[entry_index], selected_symbols]
                - 1.0
            ).astype(float)
            correlation = values.rank().corr(returns.rank())
            if not math.isfinite(float(correlation)):
                continue
            count = max(1, math.ceil(len(values) * scope.selection_fraction))
            top = values.nlargest(count).index
            cost = scope.round_trip_cost_bps / 10_000.0
            rank_ics.append(float(correlation))
            net_returns.append(float(returns.loc[top].mean() - returns.mean() - cost))
        if not rank_ics:
            raise ValueError("final recomputation produced no effective observations")
        dependence = eligibility.dependence
        rank_mean, rank_se = _dependent_mean_se(tuple(rank_ics), dependence)
        net_mean, net_se = _dependent_mean_se(tuple(net_returns), dependence)
        if not dependence.rank_ic_bounds[0] <= rank_mean <= dependence.rank_ic_bounds[1]:
            raise ValueError("final RankIC lies outside frozen bounds")
        if not dependence.net_return_bounds[0] <= net_mean <= dependence.net_return_bounds[1]:
            raise ValueError("final net return lies outside frozen bounds")
        z_value = 1.959963984540054
        rank_lower = rank_mean - z_value * rank_se
        net_lower = net_mean - z_value * net_se
        quality = (
            len(rank_ics) >= dependence.minimum_effective_observations
            and rank_lower >= dependence.rank_ic_sesoi
            and net_lower >= dependence.net_return_sesoi
        )
        return FinalTestMetricsV2(
            effective_observations=len(rank_ics),
            rank_ic_mean=rank_mean,
            rank_ic_standard_error=rank_se,
            rank_ic_lower_bound=rank_lower,
            net_return_mean=net_mean,
            net_return_standard_error=net_se,
            net_return_lower_bound=net_lower,
            quality_passed=quality,
        )

    def _canonical_formula(self, definition_event_hash: str) -> str:
        event = next(
            event
            for event in self.store.query_events(event_type="FactorDefinitionRecorded")
            if event.event_hash == definition_event_hash
        )
        return str(event.payload["metadata"]["canonical_formula"])

    def _record_failure(
        self,
        eligibility: FinalEvaluationEligibilityV2,
        eligibility_event: ResearchEventEnvelope,
        access_event: ResearchEventEnvelope,
        *,
        run_id: str,
        failure_code: str,
        failure_class: Literal["provider", "validation", "infrastructure", "contamination"],
    ) -> ResearchEventEnvelope:
        existing = self._terminal_events(eligibility.final_evaluation_key)
        if existing:
            return existing[0]
        content = {
            "final_evaluation_key": eligibility.final_evaluation_key,
            "eligibility_hash": eligibility.eligibility_hash,
            "access_event_hash": access_event.event_hash,
            "failure_code": failure_code,
            "failure_class": failure_class,
            "contaminated": self.authority.is_tainted(eligibility.final_evaluation_key),
        }
        failure_hash = canonical_json_hash(content)
        failure_id = "final-v2-failure-" + failure_hash[7:31]
        return self.store._append_producer_event(
            EventDraft(
                event_type="FinalTestFailedV2Recorded",
                entity_id=failure_id,
                run_id=run_id,
                payload_schema_version="final_test_failed_recorded.v2",
                idempotency_key="final-v2-terminal:" + eligibility.final_evaluation_key,
                payload={
                    "failure_id": failure_id,
                    "failure_hash": failure_hash,
                    "final_evaluation_key": eligibility.final_evaluation_key,
                    "research_family_id": eligibility.research_family_id,
                    "factor_spec_id": eligibility.factor_spec_id,
                    "eligibility_event_hash": eligibility_event.event_hash,
                    "eligibility_hash": eligibility.eligibility_hash,
                    "access_event_hash": access_event.event_hash,
                    "failure_code": failure_code,
                    "failure_class": failure_class,
                    "contaminated": content["contaminated"],
                    "source_event_hashes": sorted([eligibility_event.event_hash, access_event.event_hash]),
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                    "artifact_refs": [],
                },
            )
        )

    def reconcile_open_accesses(self) -> tuple[ResearchEventEnvelope, ...]:
        recorded: list[ResearchEventEnvelope] = []
        for access in self.store.query_events(event_type="FinalOutcomeAccessV2Recorded"):
            if access.payload["outcome"] != "allowed" or self._terminal_events(
                str(access.payload["final_evaluation_key"])
            ):
                continue
            eligibility_event = next(
                event
                for event in self.store.query_events(event_type="FinalEvaluationEligibilityRecorded")
                if event.event_hash == access.payload["eligibility_event_hash"]
            )
            eligibility = FinalEvaluationEligibilityServiceV2._from_event(eligibility_event)
            recorded.append(
                self._record_failure(
                    eligibility,
                    eligibility_event,
                    access,
                    run_id=access.run_id,
                    failure_code="FINAL_RUN_INTERRUPTED",
                    failure_class="infrastructure",
                )
            )
            self._record_selection(eligibility, run_id=access.run_id)
        return tuple(recorded)

    def decision_view(self, artifact: FinalTestArtifactV2) -> Mapping[str, Any]:
        eligibility_event = next(
            event
            for event in self.store.query_events(event_type="FinalEvaluationEligibilityRecorded")
            if event.payload["eligibility_hash"] == artifact.eligibility_hash
        )
        self._record_selection(
            FinalEvaluationEligibilityServiceV2._from_event(eligibility_event),
            run_id=eligibility_event.run_id,
        )
        contaminated = artifact.contaminated or self.authority.is_tainted(artifact.final_evaluation_key)
        content = {
            "schema_version": "final_decision_evidence_view.v2",
            "final_test_artifact_hash": artifact.artifact_hash,
            "final_evaluation_key": artifact.final_evaluation_key,
            "research_family_id": artifact.research_family_id,
            "factor_spec_id": artifact.factor_spec_id,
            "one_shot": True,
            "confirmatory_grade": not contaminated,
            "contaminated": contaminated,
            "quality_passed": artifact.metrics.quality_passed and not contaminated,
            "promotion_ceiling": next(
                str(event.payload["prefinal_decision"])
                for event in self.store.query_events(event_type="FinalEvaluationEligibilityRecorded")
                if event.payload["eligibility_hash"] == artifact.eligibility_hash
            ),
            "metrics": artifact.metrics.to_dict(),
            "limitations": sorted(
                set(artifact.limitations) | ({"FINAL_SCOPE_CONTAMINATED"} if contaminated else set())
            ),
        }
        return {**content, "view_hash": canonical_json_hash(content)}

    def _terminal_events(self, final_key: str) -> list[ResearchEventEnvelope]:
        return [
            event
            for event in self.store.query_events()
            if event.event_type in {"FinalTestArtifactV2Recorded", "FinalTestFailedV2Recorded"}
            and event.payload["final_evaluation_key"] == final_key
        ]

    def _record_selection(self, eligibility: FinalEvaluationEligibilityV2, *, run_id: str) -> ResearchEventEnvelope:
        accesses = [
            event
            for event in self.store.query_events(event_type="FinalOutcomeAccessV2Recorded")
            if event.payload["final_evaluation_key"] == eligibility.final_evaluation_key
        ]
        terminals = self._terminal_events(eligibility.final_evaluation_key)
        artifact_count = sum(event.event_type == "FinalTestArtifactV2Recorded" for event in terminals)
        failure_count = sum(event.event_type == "FinalTestFailedV2Recorded" for event in terminals)
        taints = [
            event
            for event in self.store.query_events(event_type="FinalContaminationV2Recorded")
            if event.payload["final_evaluation_key"] == eligibility.final_evaluation_key
        ]
        missing = bool(accesses) and not terminals
        content = {
            "research_family_id": eligibility.research_family_id,
            "final_evaluation_key": eligibility.final_evaluation_key,
            "access_count": len(accesses),
            "terminal_count": len(terminals),
            "artifact_count": artifact_count,
            "failure_count": failure_count,
            "taint_count": len(taints),
            "missing_terminal": missing,
            "selective_nondisclosure": missing,
            "confirmatory_grade_eligible": (
                len(accesses) == 1 and artifact_count == 1 and failure_count == 0 and not taints
            ),
        }
        assessment_hash = canonical_json_hash(content)
        assessment_id = "final-selection-" + assessment_hash[7:31]
        existing = [
            event
            for event in self.store.query_events(event_type="FinalSelectionAssessmentV2Recorded")
            if event.payload["final_evaluation_key"] == eligibility.final_evaluation_key
            and event.payload["assessment_hash"] == assessment_hash
        ]
        if existing:
            return existing[-1]
        sources = sorted({event.event_hash for event in (*accesses, *terminals, *taints)})
        return self.store._append_producer_event(
            EventDraft(
                event_type="FinalSelectionAssessmentV2Recorded",
                entity_id=assessment_id,
                run_id=run_id,
                payload_schema_version="final_selection_assessment_recorded.v2",
                idempotency_key="final-selection:" + assessment_hash,
                payload={
                    "assessment_id": assessment_id,
                    "assessment_hash": assessment_hash,
                    **content,
                    "source_event_hashes": sources,
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                    "artifact_refs": [],
                },
            )
        )


def _dependent_mean_se(values: tuple[float, ...], config: FinalDependenceConfigV2) -> tuple[float, float]:
    array = np.asarray(values, dtype=float)
    if config.method == "nonoverlapping_cohort":
        array = array[:: config.cohort_spacing]
        if len(array) < 1:
            raise ValueError("final cohort dependence produced no observations")
        mean = float(array.mean())
        se = 0.0 if len(array) == 1 else float(array.std(ddof=1) / math.sqrt(len(array)))
        return mean, se
    mean = float(array.mean())
    if config.method == "hac":
        centered = array - mean
        n = len(array)
        variance = float(np.dot(centered, centered) / n)
        for lag in range(1, min(config.hac_lags, n - 1) + 1):
            covariance = float(np.dot(centered[lag:], centered[:-lag]) / n)
            variance += 2.0 * (1.0 - lag / (config.hac_lags + 1.0)) * covariance
        return mean, math.sqrt(max(0.0, variance) / n)
    rng = np.random.default_rng(config.bootstrap_seed)
    n = len(array)
    blocks: list[Any] = [np.take(array, np.arange(start, start + config.block_length) % n) for start in range(n)]
    means = []
    blocks_needed = math.ceil(n / config.block_length)
    for _ in range(config.bootstrap_replicates):
        chosen = rng.integers(0, len(blocks), size=blocks_needed)
        sample = np.concatenate([blocks[index] for index in chosen])[:n]
        means.append(float(sample.mean()))
    return mean, float(statistics.stdev(means))


__all__ = [
    "FinalDependenceConfigV2",
    "FinalEvaluationEligibilityServiceV2",
    "FinalEvaluationEligibilityV2",
    "FinalRawPartitionBundleV2",
    "FinalRawPartitionProviderV2",
    "FinalRawPartitionRefV2",
    "FinalRawProviderDescriptorV2",
    "FinalRawProviderRegistrationServiceV2",
    "FinalRawProviderRegistryV2",
    "FinalScopeAuthorityV2",
    "FinalScopeCapabilityV2",
    "FinalScopeConfigV2",
    "FinalTestArtifactV2",
    "FinalTestMetricsV2",
    "FinalTestRunnerV2",
]
