"""Producer-scoped registration for frozen evaluation timing and split policy."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from src.alpha_quality.evaluation_policy import (
    EvaluationTimePolicyV1,
    FrozenSplitPlanV1,
    FrozenTradingCalendarV1,
    SplitWindowV1,
)
from src.alpha_quality.flags import ResolvedAGSFlags
from src.research_ledger.events.artifacts import (
    AtomicContentAddressedArtifactWriter,
    ContentAddressedArtifact,
    validate_artifact_references,
)
from src.research_ledger.events.model import (
    EventDraft,
    EventTransitionError,
    EventValidationError,
    ResearchEventEnvelope,
)
from src.research_ledger.hash_utils import canonical_json, canonical_json_hash


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
EVALUATION_POLICY_MEDIA_TYPE = (
    "application/vnd.vibe.registered-evaluation-policy-v1+json"
)
EVALUATION_POLICY_PRODUCER_SCHEMA = "evaluation_policy_registry_service.v1"
EVALUATION_POLICY_PRODUCER_POLICY_HASH = canonical_json_hash(
    {
        "schema_version": "evaluation_policy_registry_policy.v1",
        "calendar_source": "canonical_registered_date_content.v1",
        "timing_policy": "evaluation_time_policy.v1",
        "split_policy": "frozen_split_plan.v1",
        "registration_boundary": "first_event_in_run.v1",
        "mutation_policy": "append_new_registration_only",
    }
)
_ARTIFACT_KEYS = frozenset(
    {
        "schema_version",
        "producer_schema_version",
        "producer_policy_hash",
        "calendar",
        "time_policy",
        "split_plan",
        "bundle_hash",
    }
)
def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"registered evaluation policy {name} must be an object")
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _calendar_from_dict(raw: Mapping[str, Any]) -> FrozenTradingCalendarV1:
    expected = {
        "schema_version",
        "exchange",
        "timezone",
        "dates",
        "source_artifact_hash",
        "calendar_hash",
    }
    if set(raw) != expected or not isinstance(raw["dates"], (list, tuple)):
        raise ValueError("registered evaluation calendar schema is invalid")
    calendar = FrozenTradingCalendarV1(
        schema_version=str(raw["schema_version"]),  # type: ignore[arg-type]
        exchange=str(raw["exchange"]),  # type: ignore[arg-type]
        timezone=str(raw["timezone"]),  # type: ignore[arg-type]
        dates=tuple(str(item) for item in raw["dates"]),
        source_artifact_hash=str(raw["source_artifact_hash"]),
    )
    if calendar.calendar_hash != raw["calendar_hash"]:
        raise ValueError("registered evaluation calendar hash differs")
    return calendar


def _time_policy_from_dict(raw: Mapping[str, Any]) -> EvaluationTimePolicyV1:
    expected = {
        "schema_version",
        "return_horizons",
        "execution_horizon",
        "holding_period",
        "rebalance_cadence",
        "order_lag_trading_days",
        "entry_lag_trading_days",
        "signal_timestamp",
        "entry_price",
        "exit_price",
        "execution_return_policy",
        "policy_hash",
    }
    if set(raw) != expected or not isinstance(
        raw["return_horizons"],
        (list, tuple),
    ):
        raise ValueError("registered evaluation timing schema is invalid")
    policy = EvaluationTimePolicyV1(
        schema_version=str(raw["schema_version"]),  # type: ignore[arg-type]
        return_horizons=tuple(int(item) for item in raw["return_horizons"]),
        execution_horizon=int(raw["execution_horizon"]),
        holding_period=int(raw["holding_period"]),
        rebalance_cadence=int(raw["rebalance_cadence"]),
        order_lag_trading_days=int(raw["order_lag_trading_days"]),
        entry_lag_trading_days=int(raw["entry_lag_trading_days"]),
        signal_timestamp=str(raw["signal_timestamp"]),  # type: ignore[arg-type]
        entry_price=str(raw["entry_price"]),  # type: ignore[arg-type]
        exit_price=str(raw["exit_price"]),  # type: ignore[arg-type]
        execution_return_policy=str(  # type: ignore[arg-type]
            raw["execution_return_policy"]
        ),
    )
    if policy.policy_hash != raw["policy_hash"]:
        raise ValueError("registered evaluation timing hash differs")
    return policy


def _window_from_dict(raw: Mapping[str, Any]) -> SplitWindowV1:
    expected = {
        "start",
        "end",
        "eligible_signal_start",
        "eligible_signal_end",
        "calendar_day_count",
        "eligible_signal_count",
    }
    if set(raw) != expected:
        raise ValueError("registered evaluation split window schema is invalid")
    return SplitWindowV1(
        start=str(raw["start"]),
        end=str(raw["end"]),
        eligible_signal_start=str(raw["eligible_signal_start"]),
        eligible_signal_end=str(raw["eligible_signal_end"]),
        calendar_day_count=int(raw["calendar_day_count"]),
        eligible_signal_count=int(raw["eligible_signal_count"]),
    )


def _split_plan_from_dict(raw: Mapping[str, Any]) -> FrozenSplitPlanV1:
    expected = {
        "schema_version",
        "calendar_hash",
        "evaluation_time_policy_hash",
        "train",
        "valid",
        "test",
        "purge_trading_days",
        "embargo_trading_days",
        "plan_hash",
    }
    if set(raw) != expected:
        raise ValueError("registered evaluation split-plan schema is invalid")
    plan = FrozenSplitPlanV1(
        schema_version=str(raw["schema_version"]),  # type: ignore[arg-type]
        calendar_hash=str(raw["calendar_hash"]),
        evaluation_time_policy_hash=str(raw["evaluation_time_policy_hash"]),
        train=_window_from_dict(_mapping(raw["train"], "train")),
        valid=_window_from_dict(_mapping(raw["valid"], "valid")),
        test=_window_from_dict(_mapping(raw["test"], "test")),
        purge_trading_days=int(raw["purge_trading_days"]),
        embargo_trading_days=int(raw["embargo_trading_days"]),
    )
    if plan.plan_hash != raw["plan_hash"]:
        raise ValueError("registered evaluation split-plan hash differs")
    return plan


@dataclass(frozen=True)
class RegisteredEvaluationPolicyV1:
    schema_version: str
    producer_schema_version: str
    producer_policy_hash: str
    calendar: Mapping[str, Any]
    time_policy: Mapping[str, Any]
    split_plan: Mapping[str, Any]
    bundle_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "registered_evaluation_policy.v1":
            raise ValueError("unsupported registered evaluation policy schema")
        if self.producer_schema_version != EVALUATION_POLICY_PRODUCER_SCHEMA:
            raise ValueError("unknown evaluation policy producer schema")
        if self.producer_policy_hash != EVALUATION_POLICY_PRODUCER_POLICY_HASH:
            raise ValueError("evaluation policy producer policy differs")
        calendar = _calendar_from_dict(self.calendar)
        time_policy = _time_policy_from_dict(self.time_policy)
        split_plan = _split_plan_from_dict(self.split_plan)
        if (
            split_plan.calendar_hash != calendar.calendar_hash
            or split_plan.evaluation_time_policy_hash != time_policy.policy_hash
        ):
            raise ValueError("registered evaluation policy components are unbound")
        if _HASH_RE.fullmatch(self.bundle_hash) is None:
            raise ValueError("registered evaluation policy bundle hash is invalid")
        object.__setattr__(self, "calendar", _freeze(calendar.to_dict()))
        object.__setattr__(self, "time_policy", _freeze(time_policy.to_dict()))
        object.__setattr__(self, "split_plan", _freeze(split_plan.to_dict()))
        if self.bundle_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("registered evaluation policy bundle hash differs")

    @classmethod
    def build(
        cls,
        *,
        calendar: FrozenTradingCalendarV1,
        time_policy: EvaluationTimePolicyV1,
        split_plan: FrozenSplitPlanV1,
    ) -> "RegisteredEvaluationPolicyV1":
        content = {
            "schema_version": "registered_evaluation_policy.v1",
            "producer_schema_version": EVALUATION_POLICY_PRODUCER_SCHEMA,
            "producer_policy_hash": EVALUATION_POLICY_PRODUCER_POLICY_HASH,
            "calendar": calendar.to_dict(),
            "time_policy": time_policy.to_dict(),
            "split_plan": split_plan.to_dict(),
        }
        return cls(
            schema_version="registered_evaluation_policy.v1",
            producer_schema_version=EVALUATION_POLICY_PRODUCER_SCHEMA,
            producer_policy_hash=EVALUATION_POLICY_PRODUCER_POLICY_HASH,
            calendar=calendar.to_dict(),
            time_policy=time_policy.to_dict(),
            split_plan=split_plan.to_dict(),
            bundle_hash=canonical_json_hash(content),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RegisteredEvaluationPolicyV1":
        if set(raw) != _ARTIFACT_KEYS:
            raise ValueError("registered evaluation policy artifact is not closed")
        return cls(
            schema_version=str(raw["schema_version"]),
            producer_schema_version=str(raw["producer_schema_version"]),
            producer_policy_hash=str(raw["producer_policy_hash"]),
            calendar=dict(_mapping(raw["calendar"], "calendar")),
            time_policy=dict(_mapping(raw["time_policy"], "time_policy")),
            split_plan=dict(_mapping(raw["split_plan"], "split_plan")),
            bundle_hash=str(raw["bundle_hash"]),
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "producer_schema_version": self.producer_schema_version,
            "producer_policy_hash": self.producer_policy_hash,
            "calendar": _plain(self.calendar),
            "time_policy": _plain(self.time_policy),
            "split_plan": _plain(self.split_plan),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "bundle_hash": self.bundle_hash}

    def resolved_components(
        self,
    ) -> tuple[
        FrozenTradingCalendarV1,
        EvaluationTimePolicyV1,
        FrozenSplitPlanV1,
    ]:
        return (
            _calendar_from_dict(self.calendar),
            _time_policy_from_dict(self.time_policy),
            _split_plan_from_dict(self.split_plan),
        )


class EvaluationPolicyArtifactStoreV1:
    namespace = "registered-evaluation-policy-v1"

    def __init__(self, root: str | Path) -> None:
        self.writer = AtomicContentAddressedArtifactWriter(root, max_bytes=2 * 1024 * 1024)

    def write(
        self,
        bundle: RegisteredEvaluationPolicyV1,
    ) -> ContentAddressedArtifact:
        return self.writer.write_json(
            namespace=self.namespace,
            payload=bundle.to_dict(),
            schema_version="registered_evaluation_policy.v1",
            semantic_hash_field="bundle_hash",
            closed_keys=_ARTIFACT_KEYS,
            media_type=EVALUATION_POLICY_MEDIA_TYPE,
        )

    def read(
        self,
        relative_path: str,
        *,
        expected_bundle_hash: str,
        expected_blob_hash: str,
    ) -> RegisteredEvaluationPolicyV1:
        reference = {
            "relative_path": relative_path,
            "artifact_hash": expected_blob_hash,
            "media_type": EVALUATION_POLICY_MEDIA_TYPE,
        }
        normalized = validate_artifact_references(self.writer.root, [reference])[0]
        target = self.writer.root.joinpath(*normalized["relative_path"].split("/"))

        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite evaluation policy JSON: {value}")

        def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, item in items:
                if key in result:
                    raise ValueError("duplicate evaluation policy artifact key")
                result[key] = item
            return result

        raw = json.loads(
            target.read_text(encoding="utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
        if not isinstance(raw, Mapping):
            raise ValueError("registered evaluation policy must be an object")
        bundle = RegisteredEvaluationPolicyV1.from_dict(raw)
        if bundle.bundle_hash != expected_bundle_hash:
            raise ValueError("registered evaluation policy identity differs")
        return bundle


@dataclass(frozen=True)
class RecordedEvaluationPolicyV1:
    bundle: RegisteredEvaluationPolicyV1
    event: ResearchEventEnvelope
    artifact: ContentAddressedArtifact


class EvaluationPolicyRegistryServiceV1:
    def __init__(
        self,
        store: Any,
        *,
        flags: ResolvedAGSFlags,
    ) -> None:
        from src.research_ledger.events.store import ResearchEventStore

        if not isinstance(store, ResearchEventStore):
            raise TypeError("evaluation policy registry requires ResearchEventStore")
        required = (
            "VIBE_TRADING_RESEARCH_EVENTS",
            "VIBE_TRADING_ALPHA_SCORECARD",
        )
        if any(not flags.enabled(name) for name in required):
            raise RuntimeError("evaluation policy registry capability is disabled")
        if store.flags.as_dict() != flags.as_dict():
            raise ValueError("evaluation policy registry flag snapshots differ")
        self.store = store
        self.flags = flags
        self.artifacts = EvaluationPolicyArtifactStoreV1(store.artifact_root)

    def register(
        self,
        *,
        dates: Iterable[str],
        return_horizons: tuple[int, ...],
        execution_horizon: int,
        holding_period: int,
        rebalance_cadence: int,
        train: tuple[str, str],
        valid: tuple[str, str],
        test: tuple[str, str],
        run_id: str,
    ) -> RecordedEvaluationPolicyV1:
        if not run_id:
            raise ValueError("evaluation policy registration requires a run")
        normalized_dates = tuple(dates)
        calendar_source_hash = canonical_json_hash(
            {
                "schema_version": "registered_calendar_date_content.v1",
                "dates": list(normalized_dates),
            }
        )
        calendar = FrozenTradingCalendarV1(
            dates=normalized_dates,
            source_artifact_hash=calendar_source_hash,
        )
        time_policy = EvaluationTimePolicyV1(
            return_horizons=return_horizons,
            execution_horizon=execution_horizon,
            holding_period=holding_period,
            rebalance_cadence=rebalance_cadence,
        )
        split_plan = FrozenSplitPlanV1.build(
            calendar=calendar,
            time_policy=time_policy,
            train=train,
            valid=valid,
            test=test,
        )
        bundle = RegisteredEvaluationPolicyV1.build(
            calendar=calendar,
            time_policy=time_policy,
            split_plan=split_plan,
        )
        existing = [
            event
            for event in self.store.query_events(event_type="EvaluationPolicyRegistered")
            if event.run_id == run_id
        ]
        if len(existing) > 1:
            raise EventTransitionError("multiple evaluation policies exist for run")
        if existing:
            event = existing[0]
            if event.payload["bundle_hash"] != bundle.bundle_hash:
                raise EventTransitionError("evaluation policy run is already frozen")
            reference = event.payload["artifact_refs"][0]
            loaded = self.artifacts.read(
                str(reference["relative_path"]),
                expected_bundle_hash=bundle.bundle_hash,
                expected_blob_hash=str(reference["artifact_hash"]),
            )
            expected_id = self.registration_id(run_id, loaded.bundle_hash)
            expected_payload = self.event_payload(
                expected_id,
                loaded,
                reference,
                (
                    None
                    if event.payload["preregistration_watermark"] is None
                    else str(event.payload["preregistration_watermark"])
                ),
            )
            if (
                event.entity_id != expected_id
                or canonical_json(event.to_dict()["payload"])
                != canonical_json(expected_payload)
            ):
                raise EventValidationError(
                    "existing evaluation policy event differs from source replay"
                )
            return RecordedEvaluationPolicyV1(
                bundle=loaded,
                event=event,
                artifact=ContentAddressedArtifact(
                    semantic_hash=loaded.bundle_hash,
                    relative_path=str(reference["relative_path"]),
                    blob_hash=str(reference["artifact_hash"]),
                    media_type=str(reference["media_type"]),
                ),
            )
        events = self.store.query_events()
        self._require_pre_registration(run_id, events)
        watermark = None if not events else events[-1].event_hash
        artifact = self.artifacts.write(bundle)
        registration_id = self.registration_id(run_id, bundle.bundle_hash)
        event = self.store._append_producer_event(
            EventDraft(
                event_type="EvaluationPolicyRegistered",
                entity_id=registration_id,
                run_id=run_id,
                payload_schema_version="evaluation_policy_registered.v1",
                idempotency_key=(
                    "evaluation-policy-registration:" + run_id + ":" + bundle.bundle_hash
                ),
                payload=self.event_payload(
                    registration_id,
                    bundle,
                    artifact.reference(),
                    watermark,
                ),
            )
        )
        return RecordedEvaluationPolicyV1(bundle=bundle, event=event, artifact=artifact)

    @staticmethod
    def _require_pre_registration(
        run_id: str,
        events: Iterable[ResearchEventEnvelope],
    ) -> None:
        if any(
            event.run_id == run_id
            for event in events
        ):
            raise EventTransitionError(
                "evaluation policy must be the first event in its run"
            )

    @staticmethod
    def registration_id(run_id: str, bundle_hash: str) -> str:
        digest = canonical_json_hash(
            {
                "schema_version": "evaluation_policy_registration_id.v1",
                "run_id": run_id,
                "bundle_hash": bundle_hash,
            }
        ).removeprefix("sha256:")
        return "evaluation-policy-registration-v1-" + digest[:24]

    @staticmethod
    def event_payload(
        registration_id: str,
        bundle: RegisteredEvaluationPolicyV1,
        artifact_reference: Mapping[str, str],
        preregistration_watermark: str | None,
    ) -> dict[str, Any]:
        calendar, time_policy, split_plan = bundle.resolved_components()
        return {
            "registration_id": registration_id,
            "bundle_hash": bundle.bundle_hash,
            "producer_schema_version": bundle.producer_schema_version,
            "producer_policy_hash": bundle.producer_policy_hash,
            "calendar_hash": calendar.calendar_hash,
            "evaluation_time_policy_hash": time_policy.policy_hash,
            "split_plan_hash": split_plan.plan_hash,
            "preregistration_watermark": preregistration_watermark,
            "artifact_refs": [dict(artifact_reference)],
        }


__all__ = [
    "EVALUATION_POLICY_MEDIA_TYPE",
    "EVALUATION_POLICY_PRODUCER_POLICY_HASH",
    "EVALUATION_POLICY_PRODUCER_SCHEMA",
    "EvaluationPolicyArtifactStoreV1",
    "EvaluationPolicyRegistryServiceV1",
    "RecordedEvaluationPolicyV1",
    "RegisteredEvaluationPolicyV1",
]
