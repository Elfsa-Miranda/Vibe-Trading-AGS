"""Schedule-bound resource observations with unresolved isolation failures."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Literal, Mapping, TYPE_CHECKING

from src.alpha_foundry.activation.model import ActivationRunManifest
from src.research_ledger.hash_utils import canonical_json_hash

if TYPE_CHECKING:
    from src.alpha_foundry.activation.pair_schedule_v1 import (
        ClaimedActivationPairExecutionV1,
    )
    from src.alpha_foundry.activation.runner import ActivationArmRequest


_RESOURCE_V2_AUTHORITY = object()


@dataclass(frozen=True, init=False)
class ActivationResourceEvidenceV2:
    schema_version: str
    plan_hash: str
    pair_id: str
    run_group_id: str
    arm: Literal["control", "treatment"]
    manifest_hash: str
    pair_schedule_event_hash: str
    pair_schedule_hash: str
    execution_claim_event_hash: str
    arm_order_position: int
    timeout_limit_seconds: float
    measurement_policy_hash: str
    wall_seconds: float
    cpu_seconds: float
    peak_rss_mb: None
    peak_rss_method: str
    source_failure_codes: tuple[str, ...]
    source_complete: bool
    evidence_hash: str

    def __init__(
        self,
        *,
        claim: ClaimedActivationPairExecutionV1,
        request: ActivationArmRequest,
        manifest: ActivationRunManifest,
        wall_seconds: float,
        cpu_seconds: float,
        _authority: object,
    ) -> None:
        if _authority is not _RESOURCE_V2_AUTHORITY:
            raise TypeError("Activation resource v2 is minted by the scheduled runner")
        if any(
            isinstance(value, bool) or not math.isfinite(value) or value < 0.0
            for value in (wall_seconds, cpu_seconds)
        ):
            raise ValueError("Activation resource v2 timers are invalid")
        schedule = claim.recorded_schedule.schedule
        if (
            request.plan_hash != schedule.plan_hash
            or request.pair_id != schedule.pair_id
            or request.run_group_id != schedule.run_group_id
            or request.arm not in schedule.arm_order
            or manifest.manifest_hash == ""
        ):
            raise ValueError("Activation resource v2 schedule binding differs")
        position = schedule.arm_order.index(request.arm)
        policy = {
            "schema_version": "activation_resource_measurement_policy.v2",
            "wall_clock": "time.perf_counter.v1",
            "cpu_clock": "time.process_time.v1",
            "measurement_boundary": "immediately_around_scheduled_arm_executor.v1",
            "arm_order_rule": schedule.order_rule,
            "arm_order": list(schedule.arm_order),
            "pair_schedule_event_hash": claim.recorded_schedule.event.event_hash,
            "pair_schedule_hash": schedule.schedule_hash,
            "execution_claim_event_hash": claim.claim_event.event_hash,
            "timeout_limit_seconds": schedule.timeout_seconds,
            "timeout_enforcement": "observed_not_enforced.v1",
            "peak_rss_method": "unavailable_without_isolated_worker.v1",
        }
        failures = {
            "EXECUTOR_TIMEOUT_NOT_ENFORCED",
            "PEAK_RSS_ISOLATED_MEASUREMENT_UNAVAILABLE",
        }
        if wall_seconds > schedule.timeout_seconds:
            failures.add("EXECUTOR_TIMEOUT_EXCEEDED")
        codes = tuple(sorted(failures))
        content = {
            "schema_version": "activation_resource_evidence.v2",
            "plan_hash": request.plan_hash,
            "pair_id": request.pair_id,
            "run_group_id": request.run_group_id,
            "arm": request.arm,
            "manifest_hash": manifest.manifest_hash,
            "pair_schedule_event_hash": claim.recorded_schedule.event.event_hash,
            "pair_schedule_hash": schedule.schedule_hash,
            "execution_claim_event_hash": claim.claim_event.event_hash,
            "arm_order_position": position,
            "timeout_limit_seconds": schedule.timeout_seconds,
            "measurement_policy_hash": canonical_json_hash(policy),
            "wall_seconds": wall_seconds,
            "cpu_seconds": cpu_seconds,
            "peak_rss_mb": None,
            "peak_rss_method": "unavailable_without_isolated_worker.v1",
            "source_failure_codes": list(codes),
            "source_complete": False,
        }
        for name, value in content.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "source_failure_codes", codes)
        object.__setattr__(self, "evidence_hash", canonical_json_hash(content))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_hash": self.plan_hash,
            "pair_id": self.pair_id,
            "run_group_id": self.run_group_id,
            "arm": self.arm,
            "manifest_hash": self.manifest_hash,
            "pair_schedule_event_hash": self.pair_schedule_event_hash,
            "pair_schedule_hash": self.pair_schedule_hash,
            "execution_claim_event_hash": self.execution_claim_event_hash,
            "arm_order_position": self.arm_order_position,
            "timeout_limit_seconds": self.timeout_limit_seconds,
            "measurement_policy_hash": self.measurement_policy_hash,
            "wall_seconds": self.wall_seconds,
            "cpu_seconds": self.cpu_seconds,
            "peak_rss_mb": self.peak_rss_mb,
            "peak_rss_method": self.peak_rss_method,
            "source_failure_codes": list(self.source_failure_codes),
            "source_complete": self.source_complete,
            "evidence_hash": self.evidence_hash,
        }


@dataclass(frozen=True)
class MeasuredScheduledActivationPairV2:
    control: ActivationRunManifest
    treatment: ActivationRunManifest
    control_resource: ActivationResourceEvidenceV2
    treatment_resource: ActivationResourceEvidenceV2


def _mint_resource_evidence_v2(
    *,
    claim: ClaimedActivationPairExecutionV1,
    request: ActivationArmRequest,
    manifest: ActivationRunManifest,
    wall_seconds: float,
    cpu_seconds: float,
) -> ActivationResourceEvidenceV2:
    return ActivationResourceEvidenceV2(
        claim=claim,
        request=request,
        manifest=manifest,
        wall_seconds=wall_seconds,
        cpu_seconds=cpu_seconds,
        _authority=_RESOURCE_V2_AUTHORITY,
    )


def validate_resource_evidence_v2_mapping(raw: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "schema_version", "plan_hash", "pair_id", "run_group_id", "arm",
        "manifest_hash", "pair_schedule_event_hash", "pair_schedule_hash",
        "execution_claim_event_hash", "arm_order_position", "timeout_limit_seconds",
        "measurement_policy_hash", "wall_seconds", "cpu_seconds", "peak_rss_mb",
        "peak_rss_method", "source_failure_codes", "source_complete", "evidence_hash",
    }
    if set(raw) != expected:
        raise ValueError("Activation resource v2 has an invalid closed schema")
    failures = raw["source_failure_codes"]
    allowed = {
        "EXECUTOR_TIMEOUT_EXCEEDED",
        "EXECUTOR_TIMEOUT_NOT_ENFORCED",
        "PEAK_RSS_ISOLATED_MEASUREMENT_UNAVAILABLE",
    }
    if (
        raw["schema_version"] != "activation_resource_evidence.v2"
        or raw["arm"] not in {"control", "treatment"}
        or raw["arm_order_position"] not in {0, 1}
        or raw["peak_rss_mb"] is not None
        or raw["peak_rss_method"] != "unavailable_without_isolated_worker.v1"
        or raw["source_complete"] is not False
        or not isinstance(failures, list)
        or failures != sorted(set(failures))
        or not {"EXECUTOR_TIMEOUT_NOT_ENFORCED", "PEAK_RSS_ISOLATED_MEASUREMENT_UNAVAILABLE"}.issubset(failures)
        or not set(failures).issubset(allowed)
    ):
        raise ValueError("Activation resource v2 omits unresolved limitations")
    for name in ("timeout_limit_seconds", "wall_seconds", "cpu_seconds"):
        value = raw[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) < 0.0
            or (name == "timeout_limit_seconds" and float(value) <= 0.0)
        ):
            raise ValueError("Activation resource v2 timer is invalid")
    exceeded = float(raw["wall_seconds"]) > float(raw["timeout_limit_seconds"])
    if ("EXECUTOR_TIMEOUT_EXCEEDED" in failures) != exceeded:
        raise ValueError("Activation resource v2 timeout classification differs")
    content = {key: value for key, value in raw.items() if key != "evidence_hash"}
    if canonical_json_hash(content) != raw["evidence_hash"]:
        raise ValueError("Activation resource v2 hash mismatch")
    return dict(raw)


__all__ = [
    "ActivationResourceEvidenceV2", "MeasuredScheduledActivationPairV2",
    "validate_resource_evidence_v2_mapping",
]
