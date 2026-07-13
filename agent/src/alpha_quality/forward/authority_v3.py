"""Producer-bound forward monitoring authority with vintage-preserving revisions."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence, cast

import numpy as np
from scipy.stats import rankdata

from src.research_ledger.events import EventDraft, ResearchEventStore
from src.research_ledger.events.artifacts import (
    AtomicContentAddressedArtifactWriter,
    validate_artifact_references,
)
from src.research_ledger.events.model import ResearchEventEnvelope
from src.research_ledger.hash_utils import canonical_json_hash, utc_now_iso

PRODUCER_SCHEMA_VERSION = "forward_monitoring_authority.v3"
PRODUCER_POLICY_HASH = canonical_json_hash(
    {
        "schema_version": "forward_monitoring_policy.v3",
        "eligibility": "current_paper_candidate_and_untainted_final_v2",
        "source": "registered_provider_dated_raw_artifact",
        "observation": "producer_computed_no_caller_metrics",
        "period": "strictly_increasing_no_backfill",
        "revision": "append_only_original_and_restated_views",
        "discovery_import": "forbidden",
    }
)
SOURCE_MEDIA_TYPE = "application/vnd.vibe.forward-source-v3+json"
OBSERVATION_MEDIA_TYPE = "application/vnd.vibe.forward-observation-v3+json"


def _require_hash(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        raise ValueError(f"{field} must be a canonical sha256 hash")
    int(value[7:], 16)


def _require_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include timezone")
    return parsed


@dataclass(frozen=True)
class MonitoringProviderDescriptorV3:
    provider_id: str
    provider_version: str
    calendar_id: str
    vintage_policy: Literal["as_observed_append_only"]
    maximum_availability_delay_days: int
    provider_policy_hash: str

    def __post_init__(self) -> None:
        if (
            not self.provider_id
            or not self.provider_version
            or not self.calendar_id
            or self.vintage_policy != "as_observed_append_only"
            or self.maximum_availability_delay_days < 0
        ):
            raise ValueError("invalid monitoring provider descriptor")
        _require_hash(self.provider_policy_hash, "provider_policy_hash")

    @property
    def vintage_policy_hash(self) -> str:
        return str(
            canonical_json_hash(
                {
                    "vintage_policy": self.vintage_policy,
                    "provider_id": self.provider_id,
                    "provider_version": self.provider_version,
                }
            )
        )

    @property
    def availability_contract_hash(self) -> str:
        return str(
            canonical_json_hash(
                {
                    "calendar_id": self.calendar_id,
                    "maximum_availability_delay_days": self.maximum_availability_delay_days,
                    "required_fields": ["observed_on", "symbol", "signal", "gross_return", "cost", "available_at"],
                }
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "calendar_id": self.calendar_id,
            "vintage_policy": self.vintage_policy,
            "vintage_policy_hash": self.vintage_policy_hash,
            "maximum_availability_delay_days": self.maximum_availability_delay_days,
            "availability_contract_hash": self.availability_contract_hash,
            "provider_policy_hash": self.provider_policy_hash,
        }


@dataclass(frozen=True)
class ForwardKillRulesV3:
    minimum_rank_ic: float
    minimum_net_return: float
    maximum_drawdown: float

    def __post_init__(self) -> None:
        if (
            not all(
                math.isfinite(value) for value in (self.minimum_rank_ic, self.minimum_net_return, self.maximum_drawdown)
            )
            or self.maximum_drawdown <= 0
        ):
            raise ValueError("invalid forward kill rules")

    @property
    def rules_hash(self) -> str:
        return str(canonical_json_hash(self.to_dict()))

    def to_dict(self) -> dict[str, float]:
        return {
            "minimum_rank_ic": self.minimum_rank_ic,
            "minimum_net_return": self.minimum_net_return,
            "maximum_drawdown": self.maximum_drawdown,
        }


@dataclass(frozen=True)
class ForwardPlanConfigV3:
    factor_spec_id: str
    forward_start: str
    calendar_id: str
    return_horizon: int
    minimum_look_observations: int
    provider_id: str
    provider_version: str
    vintage_policy_hash: str
    availability_contract_hash: str
    transform_pipeline_hash: str
    cost_model_hash: str
    regime_config_hash: str
    policy_hash: str
    kill_rules: ForwardKillRulesV3

    def __post_init__(self) -> None:
        date.fromisoformat(self.forward_start)
        if (
            not self.factor_spec_id
            or not self.calendar_id
            or not self.provider_id
            or not self.provider_version
            or self.return_horizon < 1
            or self.minimum_look_observations < 1
        ):
            raise ValueError("invalid forward plan configuration")
        for field in (
            "vintage_policy_hash",
            "availability_contract_hash",
            "transform_pipeline_hash",
            "cost_model_hash",
            "regime_config_hash",
            "policy_hash",
        ):
            _require_hash(str(getattr(self, field)), field)

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_spec_id": self.factor_spec_id,
            "forward_start": self.forward_start,
            "calendar_id": self.calendar_id,
            "return_horizon": self.return_horizon,
            "minimum_look_observations": self.minimum_look_observations,
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "vintage_policy_hash": self.vintage_policy_hash,
            "availability_contract_hash": self.availability_contract_hash,
            "transform_pipeline_hash": self.transform_pipeline_hash,
            "cost_model_hash": self.cost_model_hash,
            "regime_config_hash": self.regime_config_hash,
            "policy_hash": self.policy_hash,
            "kill_rules": self.kill_rules.to_dict(),
            "kill_rules_hash": self.kill_rules.rules_hash,
        }


@dataclass(frozen=True)
class ForwardRawRowV3:
    observed_on: str
    symbol: str
    signal: float
    gross_return: float
    cost: float
    available_at: str

    def __post_init__(self) -> None:
        observed = date.fromisoformat(self.observed_on)
        available = _require_timestamp(self.available_at, "available_at")
        if (
            not self.symbol
            or not all(math.isfinite(value) for value in (self.signal, self.gross_return, self.cost))
            or self.cost < 0
            or available.date() < observed
        ):
            raise ValueError("invalid dated forward raw row")

    def to_dict(self) -> dict[str, Any]:
        return {
            "observed_on": self.observed_on,
            "symbol": self.symbol,
            "signal": self.signal,
            "gross_return": self.gross_return,
            "cost": self.cost,
            "available_at": self.available_at,
        }


class ForwardMonitoringProviderRegistryV3:
    def __init__(self, descriptors: Sequence[MonitoringProviderDescriptorV3]) -> None:
        identities = [(item.provider_id, item.provider_version) for item in descriptors]
        if not identities or identities != sorted(identities) or len(identities) != len(set(identities)):
            raise ValueError("monitoring providers must be sorted and unique")
        self._descriptors = {identity: item for identity, item in zip(identities, descriptors, strict=True)}

    def descriptor(self, provider_id: str, provider_version: str) -> MonitoringProviderDescriptorV3:
        try:
            return self._descriptors[(provider_id, provider_version)]
        except KeyError as exc:
            raise ValueError("monitoring provider is not registered") from exc


class ForwardPlanAuthorityV3:
    def __init__(self, *, store: ResearchEventStore, registry: ForwardMonitoringProviderRegistryV3) -> None:
        if not store.flags.enabled("VIBE_TRADING_FORWARD_TRACKING"):
            raise RuntimeError("forward authority v3 is disabled")
        self.store = store
        self.registry = registry

    def register_provider(self, provider_id: str, provider_version: str, *, run_id: str) -> ResearchEventEnvelope:
        descriptor = self.registry.descriptor(provider_id, provider_version)
        content = descriptor.to_dict()
        registration_hash = canonical_json_hash(content)
        existing = [
            event
            for event in self.store.query_events(event_type="ForwardMonitoringProviderV3Registered")
            if event.payload["registration_hash"] == registration_hash
        ]
        if existing:
            return existing[-1]
        registration_id = "forward-provider-v3-" + registration_hash[7:31]
        return self.store._append_producer_event(
            EventDraft(
                event_type="ForwardMonitoringProviderV3Registered",
                entity_id=registration_id,
                run_id=run_id,
                payload_schema_version="forward_monitoring_provider_registered.v3",
                idempotency_key="forward-provider-v3:" + registration_hash,
                payload={
                    "registration_id": registration_id,
                    "registration_hash": registration_hash,
                    "descriptor": content,
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                    "artifact_refs": [],
                },
            )
        )

    def register_plan(
        self,
        config: ForwardPlanConfigV3,
        *,
        final_artifact_event_hash: str,
        decision_event_hash: str,
        provider_registration_event_hash: str,
        run_id: str,
    ) -> ResearchEventEnvelope:
        final_event = self._exact("FinalTestArtifactV2Recorded", final_artifact_event_hash)
        decision = self._exact_current_decision(config.factor_spec_id, decision_event_hash)
        provider = self._exact("ForwardMonitoringProviderV3Registered", provider_registration_event_hash)
        descriptor = self.registry.descriptor(config.provider_id, config.provider_version)
        if (
            decision.payload["decision"] != "paper_candidate"
            or final_event.payload["factor_spec_id"] != config.factor_spec_id
            or final_event.payload["contaminated"]
            or not final_event.payload["quality_passed"]
            or provider.payload["descriptor"] != descriptor.to_dict()
            or config.calendar_id != descriptor.calendar_id
            or config.vintage_policy_hash != descriptor.vintage_policy_hash
            or config.availability_contract_hash != descriptor.availability_contract_hash
        ):
            raise ValueError("forward plan requires current authoritative paper-candidate evidence")
        final_key = str(final_event.payload["final_evaluation_key"])
        if self._taints(final_key):
            raise ValueError("late final taint invalidates forward plan eligibility")
        registered_at = utc_now_iso()
        earliest = max(
            date.fromisoformat(registered_at[:10]),
            date.fromisoformat(final_event.created_at[:10]),
            date.fromisoformat(decision.created_at[:10]),
        )
        if date.fromisoformat(config.forward_start) < earliest:
            raise ValueError("forward_start cannot precede registration or final completion")
        watermark = self.store.query_events()[-1].event_hash
        content = {
            "schema_version": "forward_plan.v3",
            "config": config.to_dict(),
            "final_artifact_event_hash": final_artifact_event_hash,
            "final_artifact_hash": final_event.payload["artifact_hash"],
            "final_evaluation_key": final_key,
            "decision_event_hash": decision_event_hash,
            "decision_hash": decision.payload["decision_hash"],
            "provider_registration_event_hash": provider_registration_event_hash,
            "eligibility_watermark": watermark,
            "registered_at": registered_at,
        }
        plan_hash = canonical_json_hash(content)
        plan_id = "forward-plan-v3-" + plan_hash[7:31]
        return self.store._append_producer_event(
            EventDraft(
                event_type="ForwardPlanV3Recorded",
                entity_id=plan_id,
                run_id=run_id,
                payload_schema_version="forward_plan_recorded.v3",
                idempotency_key="forward-plan-v3:" + plan_hash,
                payload={
                    "plan_id": plan_id,
                    "plan_hash": plan_hash,
                    **content,
                    "source_event_hashes": sorted(
                        [final_artifact_event_hash, decision_event_hash, provider_registration_event_hash]
                    ),
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                    "artifact_refs": [],
                },
            )
        )

    def current_plan_view(self, plan_event_hash: str) -> dict[str, Any]:
        plan = self._exact("ForwardPlanV3Recorded", plan_event_hash)
        current_decisions = self._decisions(str(plan.payload["config"]["factor_spec_id"]))
        current_decision = current_decisions[-1] if current_decisions else None
        tainted = bool(self._taints(str(plan.payload["final_evaluation_key"])))
        eligible = (
            current_decision is not None
            and current_decision.event_hash == plan.payload["decision_event_hash"]
            and current_decision.payload["decision"] == "paper_candidate"
            and not tainted
        )
        content = {
            "schema_version": "forward_plan_current_view.v3",
            "plan_id": plan.payload["plan_id"],
            "plan_hash": plan.payload["plan_hash"],
            "eligible": eligible,
            "late_final_taint": tainted,
            "current_decision_event_hash": None if current_decision is None else current_decision.event_hash,
            "limitations": ([] if eligible else ["FORWARD_PLAN_ELIGIBILITY_INVALIDATED"]),
        }
        return {**content, "view_hash": canonical_json_hash(content)}

    def _exact_current_decision(self, factor_spec_id: str, event_hash: str) -> ResearchEventEnvelope:
        decisions = self._decisions(factor_spec_id)
        if not decisions or decisions[-1].event_hash != event_hash:
            raise ValueError("forward plan requires the current decision at its watermark")
        return decisions[-1]

    def _decisions(self, factor_spec_id: str) -> list[ResearchEventEnvelope]:
        return [
            event
            for event in self.store.query_events()
            if event.event_type in {"QualityDecisionV2Recorded", "QualityDecisionV3Recorded"}
            and event.payload["factor_spec_id"] == factor_spec_id
        ]

    def _taints(self, final_key: str) -> list[ResearchEventEnvelope]:
        return [
            event
            for event in self.store.query_events(event_type="FinalContaminationV2Recorded")
            if event.payload["final_evaluation_key"] == final_key
        ]

    def _exact(self, event_type: str, event_hash: str) -> ResearchEventEnvelope:
        matches = [event for event in self.store.query_events(event_type=event_type) if event.event_hash == event_hash]
        if len(matches) != 1:
            raise ValueError(f"exact {event_type} reference is required")
        return matches[0]


class ForwardMonitoringProducerV3:
    _source_keys = frozenset(
        {
            "schema_version",
            "plan_hash",
            "provider_id",
            "provider_version",
            "period_start",
            "period_end",
            "rows",
            "source_hash",
        }
    )
    _observation_keys = frozenset(
        {
            "schema_version",
            "plan_hash",
            "source_hash",
            "period_start",
            "period_end",
            "metrics",
            "status",
            "kill_reasons",
            "success_claim",
            "observation_hash",
        }
    )

    def __init__(self, *, store: ResearchEventStore, authority: ForwardPlanAuthorityV3) -> None:
        if not store.flags.enabled("VIBE_TRADING_FORWARD_TRACKING"):
            raise RuntimeError("forward monitoring producer v3 is disabled")
        self.store = store
        self.authority = authority
        self.writer = AtomicContentAddressedArtifactWriter(store.artifact_root, max_bytes=16 * 1024**2)

    def produce_source(
        self,
        plan_event_hash: str,
        rows: Sequence[ForwardRawRowV3],
        *,
        run_id: str,
    ) -> ResearchEventEnvelope:
        plan = self.authority._exact("ForwardPlanV3Recorded", plan_event_hash)
        if not self.authority.current_plan_view(plan_event_hash)["eligible"]:
            raise ValueError("current forward plan eligibility is invalid")
        normalized = sorted(rows, key=lambda item: (item.observed_on, item.symbol))
        identities = [(item.observed_on, item.symbol) for item in normalized]
        if not normalized or len(identities) != len(set(identities)):
            raise ValueError("forward source rows must be non-empty and unique")
        config = cast(Mapping[str, Any], plan.payload["config"])
        period_start = normalized[0].observed_on
        period_end = normalized[-1].observed_on
        if period_start < str(config["forward_start"]):
            raise ValueError("forward source period cannot backfill before forward_start")
        prior = [
            event
            for event in self.store.query_events(event_type="ForwardSourceArtifactV3Recorded")
            if event.payload["plan_hash"] == plan.payload["plan_hash"]
        ]
        if prior and period_start <= prior[-1].payload["period_end"]:
            raise ValueError("forward source periods must be strictly increasing")
        descriptor = self.authority.registry.descriptor(str(config["provider_id"]), str(config["provider_version"]))
        produced_at = datetime.now(timezone.utc)
        for row in normalized:
            available = _require_timestamp(row.available_at, "available_at")
            if (
                available > produced_at
                or (available.date() - date.fromisoformat(row.observed_on)).days
                > descriptor.maximum_availability_delay_days
            ):
                raise ValueError("forward source violates its availability contract")
        content = {
            "schema_version": "forward_source.v3",
            "plan_hash": plan.payload["plan_hash"],
            "provider_id": descriptor.provider_id,
            "provider_version": descriptor.provider_version,
            "period_start": period_start,
            "period_end": period_end,
            "rows": [item.to_dict() for item in normalized],
        }
        source_hash = canonical_json_hash(content)
        artifact = self.writer.write_json(
            namespace="forward_source_v3",
            payload={**content, "source_hash": source_hash},
            schema_version="forward_source.v3",
            semantic_hash_field="source_hash",
            closed_keys=self._source_keys,
            media_type=SOURCE_MEDIA_TYPE,
        )
        source_id = "forward-source-v3-" + source_hash[7:31]
        return self.store._append_producer_event(
            EventDraft(
                event_type="ForwardSourceArtifactV3Recorded",
                entity_id=source_id,
                run_id=run_id,
                payload_schema_version="forward_source_artifact_recorded.v3",
                idempotency_key="forward-source-v3:" + source_hash,
                payload={
                    "source_id": source_id,
                    "source_hash": source_hash,
                    "plan_event_hash": plan_event_hash,
                    "plan_id": plan.payload["plan_id"],
                    "plan_hash": plan.payload["plan_hash"],
                    "provider_registration_event_hash": plan.payload["provider_registration_event_hash"],
                    "provider_id": descriptor.provider_id,
                    "provider_version": descriptor.provider_version,
                    "vintage_policy_hash": config["vintage_policy_hash"],
                    "availability_contract_hash": config["availability_contract_hash"],
                    "period_start": period_start,
                    "period_end": period_end,
                    "produced_at": utc_now_iso(),
                    "source_event_hashes": [plan_event_hash],
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                    "artifact_refs": [artifact.reference()],
                },
            )
        )

    def compute_observation(self, source_event_hash: str, *, run_id: str) -> ResearchEventEnvelope:
        source = self.authority._exact("ForwardSourceArtifactV3Recorded", source_event_hash)
        plan = self.authority._exact("ForwardPlanV3Recorded", str(source.payload["plan_event_hash"]))
        if not self.authority.current_plan_view(plan.event_hash)["eligible"]:
            raise ValueError("late final taint invalidates observation eligibility")
        content = self.recompute(plan, source)
        observation_hash = canonical_json_hash(content)
        artifact = self.writer.write_json(
            namespace="forward_observation_v3",
            payload={**content, "observation_hash": observation_hash},
            schema_version="forward_observation.v3",
            semantic_hash_field="observation_hash",
            closed_keys=self._observation_keys,
            media_type=OBSERVATION_MEDIA_TYPE,
        )
        prior = [
            event
            for event in self.store.query_events(event_type="ForwardObservationV3Recorded")
            if event.payload["plan_hash"] == plan.payload["plan_hash"]
        ]
        previous = None if not prior else prior[-1].payload["observation_hash"]
        observation_id = "forward-observation-v3-" + observation_hash[7:31]
        return self.store._append_producer_event(
            EventDraft(
                event_type="ForwardObservationV3Recorded",
                entity_id=observation_id,
                run_id=run_id,
                payload_schema_version="forward_observation_recorded.v3",
                idempotency_key="forward-observation-v3:" + observation_hash,
                payload={
                    "observation_id": observation_id,
                    "observation_hash": observation_hash,
                    "plan_event_hash": plan.event_hash,
                    "plan_id": plan.payload["plan_id"],
                    "plan_hash": plan.payload["plan_hash"],
                    "source_event_hash": source_event_hash,
                    "source_hash": source.payload["source_hash"],
                    "period_start": content["period_start"],
                    "period_end": content["period_end"],
                    "metrics": content["metrics"],
                    "status": content["status"],
                    "kill_reasons": content["kill_reasons"],
                    "success_claim": False,
                    "previous_observation_hash": previous,
                    "observed_at": utc_now_iso(),
                    "source_event_hashes": sorted([plan.event_hash, source_event_hash]),
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                    "artifact_refs": [artifact.reference()],
                },
            )
        )

    def record_revision(
        self,
        original_source_event_hash: str,
        revised_rows: Sequence[ForwardRawRowV3],
        *,
        reason_code: str,
        run_id: str,
    ) -> ResearchEventEnvelope:
        original = self.authority._exact("ForwardSourceArtifactV3Recorded", original_source_event_hash)
        plan = self.authority._exact("ForwardPlanV3Recorded", str(original.payload["plan_event_hash"]))
        normalized = sorted(revised_rows, key=lambda item: (item.observed_on, item.symbol))
        original_payload = self._read_source_reference(original)
        original_identities = [
            (str(row["observed_on"]), str(row["symbol"]))
            for row in cast(Sequence[Mapping[str, Any]], original_payload["rows"])
        ]
        revised_identities = [(row.observed_on, row.symbol) for row in normalized]
        if (
            not normalized
            or revised_identities != original_identities
            or len(revised_identities) != len(set(revised_identities))
            or normalized[0].observed_on != original.payload["period_start"]
            or normalized[-1].observed_on != original.payload["period_end"]
        ):
            raise ValueError("revision must preserve the original dated row scope")
        content = {
            "schema_version": "forward_source.v3",
            "plan_hash": plan.payload["plan_hash"],
            "provider_id": original.payload["provider_id"],
            "provider_version": original.payload["provider_version"],
            "period_start": original.payload["period_start"],
            "period_end": original.payload["period_end"],
            "rows": [item.to_dict() for item in normalized],
        }
        revised_hash = canonical_json_hash(content)
        artifact = self.writer.write_json(
            namespace="forward_revision_v3",
            payload={**content, "source_hash": revised_hash},
            schema_version="forward_source.v3",
            semantic_hash_field="source_hash",
            closed_keys=self._source_keys,
            media_type=SOURCE_MEDIA_TYPE,
        )
        prior = [
            event
            for event in self.store.query_events(event_type="DataRevisionRecorded")
            if event.payload["original_source_event_hash"] == original_source_event_hash
        ]
        revision_number = len(prior) + 1
        revision_content = {
            "original_source_event_hash": original_source_event_hash,
            "original_source_hash": original.payload["source_hash"],
            "revised_source_hash": revised_hash,
            "revision_number": revision_number,
            "reason_code": reason_code,
        }
        revision_hash = canonical_json_hash(revision_content)
        revision_id = "forward-revision-v3-" + revision_hash[7:31]
        return self.store._append_producer_event(
            EventDraft(
                event_type="DataRevisionRecorded",
                entity_id=revision_id,
                run_id=run_id,
                payload_schema_version="data_revision_recorded.v1",
                idempotency_key="forward-revision-v3:" + revision_hash,
                payload={
                    "revision_id": revision_id,
                    "revision_hash": revision_hash,
                    "plan_event_hash": plan.event_hash,
                    "plan_hash": plan.payload["plan_hash"],
                    **revision_content,
                    "revised_at": utc_now_iso(),
                    "source_event_hashes": [original_source_event_hash],
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                    "artifact_refs": [artifact.reference()],
                },
            )
        )

    def monitoring_view(self, plan_event_hash: str, *, restated: bool = False) -> dict[str, Any]:
        plan = self.authority._exact("ForwardPlanV3Recorded", plan_event_hash)
        observations = [
            event
            for event in self.store.query_events(event_type="ForwardObservationV3Recorded")
            if event.payload["plan_hash"] == plan.payload["plan_hash"]
        ]
        items: list[dict[str, Any]] = []
        for observation in observations:
            if not restated:
                items.append(
                    {
                        "period_start": observation.payload["period_start"],
                        "period_end": observation.payload["period_end"],
                        "metrics": dict(observation.payload["metrics"]),
                        "status": observation.payload["status"],
                        "source_hash": observation.payload["source_hash"],
                    }
                )
                continue
            revisions = [
                event
                for event in self.store.query_events(event_type="DataRevisionRecorded")
                if event.payload["original_source_event_hash"] == observation.payload["source_event_hash"]
            ]
            if not revisions:
                items.append(
                    {
                        "period_start": observation.payload["period_start"],
                        "period_end": observation.payload["period_end"],
                        "metrics": dict(observation.payload["metrics"]),
                        "status": observation.payload["status"],
                        "source_hash": observation.payload["source_hash"],
                    }
                )
                continue
            revised_source = self._read_source_reference(revisions[-1])
            restated_content = self._compute_content(plan, revised_source)
            items.append(
                {
                    "period_start": restated_content["period_start"],
                    "period_end": restated_content["period_end"],
                    "metrics": restated_content["metrics"],
                    "status": restated_content["status"],
                    "source_hash": revisions[-1].payload["revised_source_hash"],
                }
            )
        current = self.authority.current_plan_view(plan_event_hash)
        content = {
            "schema_version": "monitoring_evidence_view.v3",
            "scope": "monitoring",
            "plan_id": plan.payload["plan_id"],
            "plan_hash": plan.payload["plan_hash"],
            "view_kind": "restated" if restated else "original_as_observed",
            "plan_currently_eligible": current["eligible"],
            "observations": items,
            "success_claim": False,
            "limitations": ["MONITORING_ONLY_NOT_DISCOVERY_EVIDENCE"],
        }
        return {**content, "view_hash": canonical_json_hash(content)}

    def recompute(self, plan: ResearchEventEnvelope, source: ResearchEventEnvelope) -> dict[str, Any]:
        return self._compute_content(plan, self._read_source_reference(source))

    def _compute_content(self, plan: ResearchEventEnvelope, source_payload: Mapping[str, Any]) -> dict[str, Any]:
        rows = cast(Sequence[Mapping[str, Any]], source_payload["rows"])
        by_date: dict[str, list[Mapping[str, Any]]] = {}
        for row in rows:
            by_date.setdefault(str(row["observed_on"]), []).append(row)
        rank_ics: list[float] = []
        net_returns: list[float] = []
        for observed_on in sorted(by_date):
            dated = by_date[observed_on]
            if len(dated) < 2:
                continue
            signals = np.asarray([float(row["signal"]) for row in dated], dtype=float)
            returns = np.asarray([float(row["gross_return"]) for row in dated], dtype=float)
            costs = np.asarray([float(row["cost"]) for row in dated], dtype=float)
            signal_ranks = rankdata(signals, method="average")
            return_ranks = rankdata(returns, method="average")
            if float(np.std(signal_ranks)) == 0 or float(np.std(return_ranks)) == 0:
                rank_ics.append(0.0)
            else:
                rank_ics.append(float(np.corrcoef(signal_ranks, return_ranks)[0, 1]))
            centered = signal_ranks - float(np.mean(signal_ranks))
            denominator = float(np.sum(np.abs(centered)))
            weights = np.zeros_like(centered) if denominator == 0 else centered / denominator
            net_returns.append(float(np.dot(weights, returns - costs)))
        effective_n = len(rank_ics)
        rank_ic = 0.0 if not rank_ics else float(np.mean(rank_ics))
        net_return = 0.0 if not net_returns else float(np.sum(net_returns))
        cumulative = np.cumsum(np.asarray(net_returns, dtype=float))
        drawdown = 0.0
        if len(cumulative):
            peaks = np.maximum.accumulate(np.concatenate(([0.0], cumulative)))[:-1]
            drawdown = max(0.0, float(np.max(peaks - cumulative)))
        metrics = {
            "effective_observations": effective_n,
            "rank_ic": rank_ic,
            "net_return": net_return,
            "drawdown": drawdown,
        }
        config = cast(Mapping[str, Any], plan.payload["config"])
        rules = cast(Mapping[str, Any], config["kill_rules"])
        reasons: list[str] = []
        if effective_n >= int(config["minimum_look_observations"]):
            if rank_ic < float(rules["minimum_rank_ic"]):
                reasons.append("RANK_IC_KILL_RULE")
            if net_return < float(rules["minimum_net_return"]):
                reasons.append("NET_RETURN_KILL_RULE")
            if drawdown > float(rules["maximum_drawdown"]):
                reasons.append("DRAWDOWN_KILL_RULE")
        status = (
            "insufficient"
            if effective_n < int(config["minimum_look_observations"])
            else ("kill_triggered" if reasons else "monitoring")
        )
        return {
            "schema_version": "forward_observation.v3",
            "plan_hash": plan.payload["plan_hash"],
            "source_hash": source_payload["source_hash"],
            "period_start": source_payload["period_start"],
            "period_end": source_payload["period_end"],
            "metrics": metrics,
            "status": status,
            "kill_reasons": reasons,
            "success_claim": False,
        }

    def _read_source_reference(self, event: ResearchEventEnvelope) -> Mapping[str, Any]:
        refs = validate_artifact_references(self.store.artifact_root, event.payload["artifact_refs"])
        if len(refs) != 1 or refs[0]["media_type"] != SOURCE_MEDIA_TYPE:
            raise ValueError("forward source reference is invalid")
        path = Path(self.store.artifact_root, *refs[0]["relative_path"].split("/"))
        payload = json.loads(path.read_text(encoding="utf-8"))
        content = {key: value for key, value in payload.items() if key != "source_hash"}
        if set(payload) != self._source_keys or canonical_json_hash(content) != payload["source_hash"]:
            raise ValueError("forward source semantic hash differs")
        return cast(Mapping[str, Any], payload)


__all__ = [
    "ForwardKillRulesV3",
    "ForwardMonitoringProducerV3",
    "ForwardMonitoringProviderRegistryV3",
    "ForwardPlanAuthorityV3",
    "ForwardPlanConfigV3",
    "ForwardRawRowV3",
    "MonitoringProviderDescriptorV3",
]
