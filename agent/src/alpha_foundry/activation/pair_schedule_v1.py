"""Outcome-free counterbalanced arm order frozen before paired execution."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from threading import Lock
from typing import Any, Literal, Mapping

from src.alpha_foundry.activation.artifacts import ActivationArtifactStore
from src.research_ledger.events import (
    EventDraft,
    ResearchEventEnvelope,
    ResearchEventStore,
)
from src.research_ledger.hash_utils import canonical_json_hash


Arm = Literal["control", "treatment"]
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_AUTHORITY = object()
_ORDER_RULE = "alternating_frozen_run_group_index.v1"


@dataclass(frozen=True)
class ActivationPairExecutionScheduleV1:
    schema_version: str
    plan_hash: str
    pair_id: str
    run_group_id: str
    mechanism_family: str
    dag_region: str
    seed: int
    arm_order: tuple[Arm, Arm]
    order_rule: str
    candidate_budget: int
    compute_budget: int
    worker_limit: int
    timeout_seconds: float
    schedule_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "activation_pair_execution_schedule.v1":
            raise ValueError("unsupported Activation pair execution schedule")
        if _HASH_RE.fullmatch(self.plan_hash) is None or _HASH_RE.fullmatch(
            self.schedule_hash
        ) is None:
            raise ValueError("Activation pair schedule identity is invalid")
        if self.pair_id != (
            f"{self.run_group_id}:{self.mechanism_family}:{self.dag_region}"
        ):
            raise ValueError("Activation pair schedule stratum is invalid")
        if self.arm_order not in {
            ("control", "treatment"),
            ("treatment", "control"),
        } or self.order_rule != _ORDER_RULE:
            raise ValueError("Activation pair schedule order is invalid")
        if (
            isinstance(self.seed, bool)
            or isinstance(self.candidate_budget, bool)
            or isinstance(self.compute_budget, bool)
            or isinstance(self.worker_limit, bool)
            or self.candidate_budget < 1
            or self.compute_budget < self.candidate_budget
            or self.worker_limit < 1
            or isinstance(self.timeout_seconds, bool)
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("Activation pair schedule resources are invalid")
        if self.schedule_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("Activation pair schedule hash mismatch")

    @classmethod
    def from_plan(
        cls,
        *,
        plan_hash: str,
        plan: Mapping[str, Any],
        run_group_id: str,
        mechanism_family: str,
        dag_region: str,
    ) -> "ActivationPairExecutionScheduleV1":
        design = plan.get("design")
        if (
            plan.get("plan_hash") != plan_hash
            or plan.get("phase") != "confirmatory"
            or not isinstance(design, Mapping)
        ):
            raise ValueError("pair schedule requires a registered confirmatory plan")
        groups = design.get("run_group_ids")
        seeds = design.get("seeds")
        excluded = design.get("pilot_excluded_run_group_ids")
        mechanisms = design.get("mechanism_families")
        regions = design.get("dag_regions")
        if not all(isinstance(value, list) for value in (
            groups, seeds, excluded, mechanisms, regions
        )):
            raise ValueError("pair schedule plan design is malformed")
        assert isinstance(groups, list) and isinstance(seeds, list)
        assert isinstance(excluded, list) and isinstance(mechanisms, list)
        assert isinstance(regions, list)
        if (
            len(groups) < 2
            or len(groups) % 2
            or len(groups) != len(seeds)
            or run_group_id not in groups
            or run_group_id in excluded
            or mechanism_family not in mechanisms
            or dag_region not in regions
        ):
            raise ValueError("pair schedule design is not exactly counterbalanceable")
        index = groups.index(run_group_id)
        arm_order: tuple[Arm, Arm] = (
            ("control", "treatment")
            if index % 2 == 0
            else ("treatment", "control")
        )
        content = {
            "schema_version": "activation_pair_execution_schedule.v1",
            "plan_hash": plan_hash,
            "pair_id": f"{run_group_id}:{mechanism_family}:{dag_region}",
            "run_group_id": run_group_id,
            "mechanism_family": mechanism_family,
            "dag_region": dag_region,
            "seed": seeds[index],
            "arm_order": list(arm_order),
            "order_rule": _ORDER_RULE,
            "candidate_budget": design.get("candidate_budget"),
            "compute_budget": design.get("compute_budget"),
            "worker_limit": design.get("worker_limit"),
            "timeout_seconds": design.get("timeout_seconds"),
        }
        return cls(
            schema_version="activation_pair_execution_schedule.v1",
            plan_hash=plan_hash,
            pair_id=str(content["pair_id"]),
            run_group_id=run_group_id,
            mechanism_family=mechanism_family,
            dag_region=dag_region,
            seed=int(content["seed"]),
            arm_order=arm_order,
            order_rule=_ORDER_RULE,
            candidate_budget=int(content["candidate_budget"]),
            compute_budget=int(content["compute_budget"]),
            worker_limit=int(content["worker_limit"]),
            timeout_seconds=float(content["timeout_seconds"]),
            schedule_hash=canonical_json_hash(content),
        )

    @classmethod
    def from_dict(
        cls, raw: Mapping[str, Any]
    ) -> "ActivationPairExecutionScheduleV1":
        expected = {
            "schema_version", "plan_hash", "pair_id", "run_group_id",
            "mechanism_family", "dag_region", "seed", "arm_order",
            "order_rule", "candidate_budget", "compute_budget", "worker_limit",
            "timeout_seconds", "schedule_hash",
        }
        if set(raw) != expected or not isinstance(raw.get("arm_order"), list):
            raise ValueError("Activation pair schedule has an invalid schema")
        order = raw["arm_order"]
        if order not in (["control", "treatment"], ["treatment", "control"]):
            raise ValueError("Activation pair schedule arm order is invalid")
        return cls(
            schema_version=str(raw["schema_version"]),
            plan_hash=str(raw["plan_hash"]),
            pair_id=str(raw["pair_id"]),
            run_group_id=str(raw["run_group_id"]),
            mechanism_family=str(raw["mechanism_family"]),
            dag_region=str(raw["dag_region"]),
            seed=int(raw["seed"]),
            arm_order=(str(order[0]), str(order[1])),  # type: ignore[arg-type]
            order_rule=str(raw["order_rule"]),
            candidate_budget=int(raw["candidate_budget"]),
            compute_budget=int(raw["compute_budget"]),
            worker_limit=int(raw["worker_limit"]),
            timeout_seconds=float(raw["timeout_seconds"]),
            schedule_hash=str(raw["schedule_hash"]),
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_hash": self.plan_hash,
            "pair_id": self.pair_id,
            "run_group_id": self.run_group_id,
            "mechanism_family": self.mechanism_family,
            "dag_region": self.dag_region,
            "seed": self.seed,
            "arm_order": list(self.arm_order),
            "order_rule": self.order_rule,
            "candidate_budget": self.candidate_budget,
            "compute_budget": self.compute_budget,
            "worker_limit": self.worker_limit,
            "timeout_seconds": self.timeout_seconds,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "schedule_hash": self.schedule_hash}


@dataclass(frozen=True, init=False)
class RecordedActivationPairExecutionScheduleV1:
    schedule: ActivationPairExecutionScheduleV1
    event: ResearchEventEnvelope
    _authority: object

    def __init__(
        self,
        schedule: ActivationPairExecutionScheduleV1,
        event: ResearchEventEnvelope,
        *,
        _authority: object,
    ) -> None:
        if _authority is not _AUTHORITY:
            raise TypeError("pair schedules must be recorded by the schedule service")
        object.__setattr__(self, "schedule", schedule)
        object.__setattr__(self, "event", event)
        object.__setattr__(self, "_authority", _authority)


class _ExecutionUseGuard:
    def __init__(self) -> None:
        self._lock = Lock()
        self._used = False

    def consume(self) -> None:
        with self._lock:
            if self._used:
                raise RuntimeError("claimed pair execution is one-shot")
            self._used = True


@dataclass(frozen=True, init=False)
class ClaimedActivationPairExecutionV1:
    recorded_schedule: RecordedActivationPairExecutionScheduleV1
    claim_event: ResearchEventEnvelope
    _guard: _ExecutionUseGuard
    _authority: object

    def __init__(
        self,
        recorded_schedule: RecordedActivationPairExecutionScheduleV1,
        claim_event: ResearchEventEnvelope,
        *,
        _authority: object,
    ) -> None:
        if _authority is not _AUTHORITY:
            raise TypeError("pair execution claims must be issued by the schedule service")
        object.__setattr__(self, "recorded_schedule", recorded_schedule)
        object.__setattr__(self, "claim_event", claim_event)
        object.__setattr__(self, "_guard", _ExecutionUseGuard())
        object.__setattr__(self, "_authority", _authority)


class ActivationPairExecutionScheduleServiceV1:
    media_type = "application/vnd.vibe.activation-pair-execution-schedule-v1+json"

    def __init__(self, store: ResearchEventStore) -> None:
        self.store = store
        self.artifacts = ActivationArtifactStore(store.artifact_root)

    def freeze(
        self,
        *,
        plan_hash: str,
        run_group_id: str,
        mechanism_family: str,
        dag_region: str,
    ) -> RecordedActivationPairExecutionScheduleV1:
        plan = self.artifacts.get("plan", plan_hash)
        schedule = ActivationPairExecutionScheduleV1.from_plan(
            plan_hash=plan_hash,
            plan=plan,
            run_group_id=run_group_id,
            mechanism_family=mechanism_family,
            dag_region=dag_region,
        )
        relative = self.artifacts.put("pair_schedule", schedule.to_dict())
        identifier = (
            "activation-pair-schedule-v1-"
            + schedule.schedule_hash.removeprefix("sha256:")[:24]
        )
        event = self.store.append_event(
            EventDraft(
                event_type="ActivationPairExecutionScheduled",
                entity_id=identifier,
                run_id=schedule.run_group_id,
                payload_schema_version="activation_pair_execution_scheduled.v1",
                idempotency_key="activation-pair-schedule-v1:" + schedule.schedule_hash,
                payload={
                    "schedule_id": identifier,
                    **{
                        key: value for key, value in schedule.to_dict().items()
                        if key != "schema_version"
                    },
                    "artifact_refs": [{
                        "relative_path": relative,
                        "artifact_hash": self._artifact_hash(relative),
                        "media_type": self.media_type,
                    }],
                },
            )
        )
        return RecordedActivationPairExecutionScheduleV1(
            schedule, event, _authority=_AUTHORITY
        )

    def claim(
        self, recorded: RecordedActivationPairExecutionScheduleV1
    ) -> ClaimedActivationPairExecutionV1:
        if not is_authoritative_pair_schedule(recorded):
            raise TypeError("pair execution claim requires recorded schedule authority")
        schedule = recorded.schedule
        identifier = (
            "activation-pair-claim-v1-"
            + schedule.schedule_hash.removeprefix("sha256:")[:24]
        )
        event = self.store.append_event(
            EventDraft(
                event_type="ActivationPairExecutionClaimed",
                entity_id=identifier,
                run_id=schedule.run_group_id,
                payload_schema_version="activation_pair_execution_claimed.v1",
                idempotency_key=None,
                payload={
                    "claim_id": identifier,
                    "schedule_event_hash": recorded.event.event_hash,
                    "schedule_hash": schedule.schedule_hash,
                    "plan_hash": schedule.plan_hash,
                    "pair_id": schedule.pair_id,
                    "run_group_id": schedule.run_group_id,
                },
            )
        )
        return ClaimedActivationPairExecutionV1(
            recorded, event, _authority=_AUTHORITY
        )

    def _artifact_hash(self, relative: str) -> str:
        from src.research_ledger.events.artifacts import hash_artifact

        return hash_artifact(self.store.artifact_root.joinpath(*relative.split("/")))


def is_authoritative_pair_schedule(
    value: object,
) -> bool:
    return (
        isinstance(value, RecordedActivationPairExecutionScheduleV1)
        and value._authority is _AUTHORITY
        and value.event.event_type == "ActivationPairExecutionScheduled"
        and value.event.payload["schedule_hash"] == value.schedule.schedule_hash
    )


def consume_claimed_pair_execution(
    value: object,
) -> RecordedActivationPairExecutionScheduleV1:
    if (
        not isinstance(value, ClaimedActivationPairExecutionV1)
        or value._authority is not _AUTHORITY
        or not is_authoritative_pair_schedule(value.recorded_schedule)
        or value.claim_event.event_type != "ActivationPairExecutionClaimed"
        or value.claim_event.payload["schedule_event_hash"]
        != value.recorded_schedule.event.event_hash
        or value.claim_event.payload["schedule_hash"]
        != value.recorded_schedule.schedule.schedule_hash
    ):
        raise TypeError("counterbalanced execution requires a claimed schedule")
    value._guard.consume()
    return value.recorded_schedule


__all__ = [
    "ActivationPairExecutionScheduleServiceV1",
    "ActivationPairExecutionScheduleV1",
    "ClaimedActivationPairExecutionV1",
    "RecordedActivationPairExecutionScheduleV1",
    "consume_claimed_pair_execution",
    "is_authoritative_pair_schedule",
]
