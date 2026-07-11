"""Runner-owned resource observations for Activation arms."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, Mapping, TYPE_CHECKING

from src.alpha_foundry.activation.model import ActivationRunManifest
from src.research_ledger.hash_utils import canonical_json_hash

if TYPE_CHECKING:
    from src.alpha_foundry.activation.runner import ActivationArmRequest


_RESOURCE_AUTHORITY = object()


@dataclass(frozen=True, init=False)
class ActivationResourceEvidenceV1:
    schema_version: str
    plan_hash: str
    pair_id: str
    run_group_id: str
    arm: Literal["control", "treatment"]
    manifest_hash: str
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
        request: ActivationArmRequest,
        manifest: ActivationRunManifest,
        wall_seconds: float,
        cpu_seconds: float,
        _authority: object,
    ) -> None:
        if _authority is not _RESOURCE_AUTHORITY:
            raise TypeError("Activation resource evidence is minted by the paired runner")
        if any(
            isinstance(value, bool) or not math.isfinite(value) or value < 0.0
            for value in (wall_seconds, cpu_seconds)
        ):
            raise ValueError("Activation resource timers must be finite and non-negative")
        measurement_policy = {
            "schema_version": "activation_resource_measurement_policy.v1",
            "wall_clock": "time.perf_counter.v1",
            "cpu_clock": "time.process_time.v1",
            "measurement_boundary": "immediately_around_arm_executor.v1",
            "arm_execution_order": "control_then_treatment_not_counterbalanced.v1",
            "timeout_enforcement": "observed_not_enforced.v1",
            "peak_rss_method": "unavailable_without_isolated_worker.v1",
        }
        policy_hash = canonical_json_hash(measurement_policy)
        failures = (
            "ARM_ORDER_NOT_COUNTERBALANCED",
            "EXECUTOR_TIMEOUT_NOT_ENFORCED",
            "PEAK_RSS_ISOLATED_MEASUREMENT_UNAVAILABLE",
        )
        content = {
            "schema_version": "activation_resource_evidence.v1",
            "plan_hash": request.plan_hash,
            "pair_id": request.pair_id,
            "run_group_id": request.run_group_id,
            "arm": request.arm,
            "manifest_hash": manifest.manifest_hash,
            "measurement_policy_hash": policy_hash,
            "wall_seconds": wall_seconds,
            "cpu_seconds": cpu_seconds,
            "peak_rss_mb": None,
            "peak_rss_method": "unavailable_without_isolated_worker.v1",
            "source_failure_codes": list(failures),
            "source_complete": False,
        }
        for name, value in content.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "source_failure_codes", failures)
        object.__setattr__(self, "evidence_hash", canonical_json_hash(content))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_hash": self.plan_hash,
            "pair_id": self.pair_id,
            "run_group_id": self.run_group_id,
            "arm": self.arm,
            "manifest_hash": self.manifest_hash,
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
class MeasuredActivationPairV1:
    control: ActivationRunManifest
    treatment: ActivationRunManifest
    control_resource: ActivationResourceEvidenceV1
    treatment_resource: ActivationResourceEvidenceV1


def _mint_resource_evidence(
    *,
    request: ActivationArmRequest,
    manifest: ActivationRunManifest,
    wall_seconds: float,
    cpu_seconds: float,
) -> ActivationResourceEvidenceV1:
    return ActivationResourceEvidenceV1(
        request=request,
        manifest=manifest,
        wall_seconds=wall_seconds,
        cpu_seconds=cpu_seconds,
        _authority=_RESOURCE_AUTHORITY,
    )


def validate_resource_evidence_mapping(raw: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "schema_version", "plan_hash", "pair_id", "run_group_id", "arm",
        "manifest_hash", "measurement_policy_hash", "wall_seconds", "cpu_seconds",
        "peak_rss_mb", "peak_rss_method", "source_failure_codes",
        "source_complete", "evidence_hash",
    }
    if set(raw) != expected:
        raise ValueError("Activation resource evidence has an invalid closed schema")
    if (
        raw["schema_version"] != "activation_resource_evidence.v1"
        or raw["arm"] not in {"control", "treatment"}
        or raw["peak_rss_mb"] is not None
        or raw["peak_rss_method"] != "unavailable_without_isolated_worker.v1"
        or raw["source_complete"] is not False
        or raw["source_failure_codes"]
        != [
            "ARM_ORDER_NOT_COUNTERBALANCED",
            "EXECUTOR_TIMEOUT_NOT_ENFORCED",
            "PEAK_RSS_ISOLATED_MEASUREMENT_UNAVAILABLE",
        ]
    ):
        raise ValueError("Activation resource evidence cannot claim unavailable RSS")
    for name in ("wall_seconds", "cpu_seconds"):
        value = raw[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) < 0.0
        ):
            raise ValueError("Activation resource timer is invalid")
    content = {key: value for key, value in raw.items() if key != "evidence_hash"}
    if canonical_json_hash(content) != raw["evidence_hash"]:
        raise ValueError("Activation resource evidence hash mismatch")
    return dict(raw)


__all__ = [
    "ActivationResourceEvidenceV1", "MeasuredActivationPairV1",
    "validate_resource_evidence_mapping",
]
