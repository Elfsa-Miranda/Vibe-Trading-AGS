"""Registered fixed-budget paired execution boundary for activation experiments."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Literal, cast, TYPE_CHECKING

from src.alpha_foundry.activation.artifacts import ActivationArtifactStore
from src.alpha_foundry.activation.model import (
    ActivationExperimentPlan,
    ActivationRunManifest,
)
from src.research_ledger.hash_utils import canonical_json_hash

if TYPE_CHECKING:
    from src.alpha_foundry.activation.resource_v1 import (
        ActivationResourceEvidenceV1,
        MeasuredActivationPairV1,
    )


_REGISTRATION_AUTHORITY = object()
_SCOPE_AUTHORITY = object()


@dataclass(frozen=True, init=False)
class RegisteredActivationPlan:
    plan: ActivationExperimentPlan
    relative_artifact: str
    _authority: object

    def __init__(
        self,
        plan: ActivationExperimentPlan,
        relative_artifact: str,
        *,
        _authority: object,
    ) -> None:
        if _authority is not _REGISTRATION_AUTHORITY:
            raise TypeError("activation plans must be registered by the paired runner")
        object.__setattr__(self, "plan", plan)
        object.__setattr__(self, "relative_artifact", relative_artifact)
        object.__setattr__(self, "_authority", _authority)


@dataclass(frozen=True, init=False)
class TrainValidActivationScope:
    train_snapshot_hash: str
    valid_snapshot_hash: str
    discovery_chain_head: str
    _authority: object

    def __init__(
        self,
        *,
        train_snapshot_hash: str,
        valid_snapshot_hash: str,
        discovery_chain_head: str,
        _authority: object,
    ) -> None:
        if _authority is not _SCOPE_AUTHORITY:
            raise TypeError("activation scope is issued only from a registered plan")
        object.__setattr__(self, "train_snapshot_hash", train_snapshot_hash)
        object.__setattr__(self, "valid_snapshot_hash", valid_snapshot_hash)
        object.__setattr__(self, "discovery_chain_head", discovery_chain_head)
        object.__setattr__(self, "_authority", _authority)


@dataclass(frozen=True)
class ActivationArmRequest:
    plan_hash: str
    pair_id: str
    run_group_id: str
    execution_run_id: str
    arm: Literal["control", "treatment"]
    seed: int
    mechanism_family: str
    dag_region: str
    policy_hash: str
    rng_namespace: str
    cache_namespace: str
    candidate_budget: int
    compute_budget: int

    def __post_init__(self) -> None:
        expected_execution = activation_arm_execution_run_id(
            plan_hash=self.plan_hash,
            run_group_id=self.run_group_id,
            arm=self.arm,
        )
        if self.execution_run_id != expected_execution:
            raise ValueError("Activation arm execution namespace is not runner-derived")
        if self.rng_namespace != (
            f"{self.plan_hash}:{self.run_group_id}:{self.arm}:rng"
        ):
            raise ValueError("Activation arm RNG namespace is not frozen")
        if self.cache_namespace != (
            f"{self.plan_hash}:{self.run_group_id}:{self.arm}:cache"
        ):
            raise ValueError("Activation arm cache namespace is not frozen")
        if (
            isinstance(self.candidate_budget, bool)
            or isinstance(self.compute_budget, bool)
            or self.candidate_budget < 1
            or self.compute_budget < self.candidate_budget
        ):
            raise ValueError("Activation arm budgets are invalid")


ArmExecutor = Callable[[ActivationArmRequest, TrainValidActivationScope], ActivationRunManifest]


def activation_arm_execution_run_id(
    *,
    plan_hash: str,
    run_group_id: str,
    arm: Literal["control", "treatment"],
) -> str:
    digest = canonical_json_hash(
        {
            "schema_version": "activation_arm_execution_run.v1",
            "plan_hash": plan_hash,
            "run_group_id": run_group_id,
            "arm": arm,
        }
    )
    return "activation-arm-" + digest.removeprefix("sha256:")[:24]


class PairedActivationRunner:
    def __init__(self, artifact_store: ActivationArtifactStore) -> None:
        self.artifact_store = artifact_store

    def register(self, plan: ActivationExperimentPlan) -> RegisteredActivationPlan:
        relative = self.artifact_store.put("plan", plan.to_dict())
        return RegisteredActivationPlan(plan, relative, _authority=_REGISTRATION_AUTHORITY)

    def run_pair(
        self,
        registered: RegisteredActivationPlan,
        *,
        run_group_id: str,
        mechanism_family: str,
        dag_region: str,
        executor: ArmExecutor,
    ) -> tuple[ActivationRunManifest, ActivationRunManifest]:
        if not isinstance(registered, RegisteredActivationPlan) or registered._authority is not _REGISTRATION_AUTHORITY:
            raise TypeError("formal outcomes require a registered immutable plan")
        plan = registered.plan
        if plan.phase != "confirmatory":
            raise ValueError("pilot plans cannot produce a confirmatory pair")
        if run_group_id not in plan.design.run_group_ids:
            raise ValueError("run group is outside the fixed stopping set")
        if run_group_id in plan.design.pilot_excluded_run_group_ids:
            raise ValueError("pilot run group is permanently excluded")
        if mechanism_family not in plan.design.mechanism_families or dag_region not in plan.design.dag_regions:
            raise ValueError("pair stratum is not preregistered")
        index = plan.design.run_group_ids.index(run_group_id)
        seed = plan.design.seeds[index]
        pair_id = f"{run_group_id}:{mechanism_family}:{dag_region}"
        scope = TrainValidActivationScope(
            train_snapshot_hash=plan.provenance.train_snapshot_hash,
            valid_snapshot_hash=plan.provenance.valid_snapshot_hash,
            discovery_chain_head=plan.provenance.eligible_event_chain_head,
            _authority=_SCOPE_AUTHORITY,
        )
        manifests: list[ActivationRunManifest] = []
        for arm, policy_hash in (
            ("control", plan.provenance.control_policy_hash),
            ("treatment", plan.provenance.treatment_policy_hash),
        ):
            request = ActivationArmRequest(
                plan_hash=plan.plan_hash,
                pair_id=pair_id,
                run_group_id=run_group_id,
                execution_run_id=activation_arm_execution_run_id(
                    plan_hash=plan.plan_hash,
                    run_group_id=run_group_id,
                    arm=cast(Literal["control", "treatment"], arm),
                ),
                arm=cast(Literal["control", "treatment"], arm),
                seed=seed,
                mechanism_family=mechanism_family,
                dag_region=dag_region,
                policy_hash=policy_hash,
                rng_namespace=f"{plan.plan_hash}:{run_group_id}:{arm}:rng",
                cache_namespace=f"{plan.plan_hash}:{run_group_id}:{arm}:cache",
                candidate_budget=plan.design.candidate_budget,
                compute_budget=plan.design.compute_budget,
            )
            manifest = executor(request, scope)
            self._validate_response(request, manifest)
            self.artifact_store.put("run", manifest.to_dict())
            manifests.append(manifest)
        return manifests[0], manifests[1]

    def run_pair_measured(
        self,
        registered: RegisteredActivationPlan,
        *,
        run_group_id: str,
        mechanism_family: str,
        dag_region: str,
        executor: ArmExecutor,
    ) -> "MeasuredActivationPairV1":
        """Execute both arms while runner-owned clocks measure the executor boundary."""
        from src.alpha_foundry.activation.resource_v1 import (
            MeasuredActivationPairV1,
            _mint_resource_evidence,
        )

        if (
            not isinstance(registered, RegisteredActivationPlan)
            or registered._authority is not _REGISTRATION_AUTHORITY
        ):
            raise TypeError("formal outcomes require a registered immutable plan")
        plan = registered.plan
        if plan.phase != "confirmatory":
            raise ValueError("pilot plans cannot produce a confirmatory pair")
        if run_group_id not in plan.design.run_group_ids:
            raise ValueError("run group is outside the fixed stopping set")
        if run_group_id in plan.design.pilot_excluded_run_group_ids:
            raise ValueError("pilot run group is permanently excluded")
        if (
            mechanism_family not in plan.design.mechanism_families
            or dag_region not in plan.design.dag_regions
        ):
            raise ValueError("pair stratum is not preregistered")
        index = plan.design.run_group_ids.index(run_group_id)
        scope = TrainValidActivationScope(
            train_snapshot_hash=plan.provenance.train_snapshot_hash,
            valid_snapshot_hash=plan.provenance.valid_snapshot_hash,
            discovery_chain_head=plan.provenance.eligible_event_chain_head,
            _authority=_SCOPE_AUTHORITY,
        )
        manifests: list[ActivationRunManifest] = []
        resources: list[ActivationResourceEvidenceV1] = []
        for arm, policy_hash in (
            ("control", plan.provenance.control_policy_hash),
            ("treatment", plan.provenance.treatment_policy_hash),
        ):
            request = ActivationArmRequest(
                plan_hash=plan.plan_hash,
                pair_id=f"{run_group_id}:{mechanism_family}:{dag_region}",
                run_group_id=run_group_id,
                execution_run_id=activation_arm_execution_run_id(
                    plan_hash=plan.plan_hash,
                    run_group_id=run_group_id,
                    arm=cast(Literal["control", "treatment"], arm),
                ),
                arm=cast(Literal["control", "treatment"], arm),
                seed=plan.design.seeds[index],
                mechanism_family=mechanism_family,
                dag_region=dag_region,
                policy_hash=policy_hash,
                rng_namespace=f"{plan.plan_hash}:{run_group_id}:{arm}:rng",
                cache_namespace=f"{plan.plan_hash}:{run_group_id}:{arm}:cache",
                candidate_budget=plan.design.candidate_budget,
                compute_budget=plan.design.compute_budget,
            )
            wall_start = time.perf_counter()
            cpu_start = time.process_time()
            manifest = executor(request, scope)
            cpu_seconds = time.process_time() - cpu_start
            wall_seconds = time.perf_counter() - wall_start
            self._validate_response(request, manifest)
            resource = _mint_resource_evidence(
                request=request,
                manifest=manifest,
                wall_seconds=wall_seconds,
                cpu_seconds=cpu_seconds,
            )
            self.artifact_store.put("run", manifest.to_dict())
            self.artifact_store.put("resource", resource.to_dict())
            manifests.append(manifest)
            resources.append(resource)
        return MeasuredActivationPairV1(
            control=manifests[0],
            treatment=manifests[1],
            control_resource=resources[0],
            treatment_resource=resources[1],
        )

    @staticmethod
    def _validate_response(
        request: ActivationArmRequest,
        manifest: ActivationRunManifest,
    ) -> None:
        for name in (
            "plan_hash", "pair_id", "run_group_id", "arm", "seed", "mechanism_family",
            "dag_region", "policy_hash", "rng_namespace", "cache_namespace",
            "candidate_budget", "compute_budget",
        ):
            if getattr(manifest, name) != getattr(request, name):
                raise ValueError(f"executor changed frozen activation field: {name}")


__all__ = [
    "ActivationArmRequest", "ArmExecutor", "PairedActivationRunner",
    "RegisteredActivationPlan", "TrainValidActivationScope",
    "activation_arm_execution_run_id",
]
