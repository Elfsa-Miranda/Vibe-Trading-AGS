"""Pair coordination boundary with isolated arm state and no decision authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.alpha_foundry.activation.candidate_factory_v1 import (
    ProductionActivationCandidateFactoryV1,
)
from src.alpha_foundry.activation.formal_protocol_v3 import FormalActivationPairScheduleV3
from src.alpha_foundry.activation.pair_projector_v2 import ActivationArmEventRefsV2
from src.research_ledger.hash_utils import canonical_json_hash


@dataclass(frozen=True)
class ActivationArmWorkingStateV2:
    arm: Literal["flat", "topology"]
    rng_namespace: str
    cache_namespace: str
    working_memory_namespace: str


@dataclass(frozen=True)
class ActivationPairCompletionV2:
    schedule_hash: str
    flat_completed_event_hash: str | None
    topology_completed_event_hash: str | None
    complete: bool
    incomplete_reason_codes: tuple[str, ...]
    completion_hash: str

    def __post_init__(self) -> None:
        if self.complete != (
            self.flat_completed_event_hash is not None
            and self.topology_completed_event_hash is not None
            and not self.incomplete_reason_codes
        ):
            raise ValueError("pair completion must derive from both arms")
        if self.completion_hash != canonical_json_hash(
            {
                "schema_version": "activation_pair_completion.v2",
                "schedule_hash": self.schedule_hash,
                "flat_completed_event_hash": self.flat_completed_event_hash,
                "topology_completed_event_hash": self.topology_completed_event_hash,
                "complete": self.complete,
                "incomplete_reason_codes": list(self.incomplete_reason_codes),
            }
        ):
            raise ValueError("pair completion hash mismatch")


class ActivationPairCoordinatorV2:
    """Coordinates the one shared factory but cannot mint governance truth."""

    def __init__(self, factory: ProductionActivationCandidateFactoryV1) -> None:
        if not isinstance(factory, ProductionActivationCandidateFactoryV1):
            raise TypeError("pair coordinator requires the production candidate factory")
        self.factory = factory
        self._shared_memory_updates: list[str] = []

    @staticmethod
    def working_states(
        schedule: FormalActivationPairScheduleV3,
    ) -> tuple[ActivationArmWorkingStateV2, ActivationArmWorkingStateV2]:
        return (
            ActivationArmWorkingStateV2(
                arm="flat",
                rng_namespace=schedule.flat_rng_namespace,
                cache_namespace=schedule.flat_cache_namespace,
                working_memory_namespace=schedule.flat_cache_namespace + "/working",
            ),
            ActivationArmWorkingStateV2(
                arm="topology",
                rng_namespace=schedule.topology_rng_namespace,
                cache_namespace=schedule.topology_cache_namespace,
                working_memory_namespace=schedule.topology_cache_namespace + "/working",
            ),
        )

    def close_pair(
        self,
        *,
        schedule_hash: str,
        flat_completed_event_hash: str | None,
        topology_completed_event_hash: str | None,
    ) -> ActivationPairCompletionV2:
        reasons = tuple(
            sorted(
                name
                for name, value in (
                    ("FLAT_ARM_INCOMPLETE", flat_completed_event_hash),
                    ("TOPOLOGY_ARM_INCOMPLETE", topology_completed_event_hash),
                )
                if value is None
            )
        )
        content = {
            "schema_version": "activation_pair_completion.v2",
            "schedule_hash": schedule_hash,
            "flat_completed_event_hash": flat_completed_event_hash,
            "topology_completed_event_hash": topology_completed_event_hash,
            "complete": not reasons,
            "incomplete_reason_codes": list(reasons),
        }
        return ActivationPairCompletionV2(
            schedule_hash=schedule_hash,
            flat_completed_event_hash=flat_completed_event_hash,
            topology_completed_event_hash=topology_completed_event_hash,
            complete=not reasons,
            incomplete_reason_codes=reasons,
            completion_hash=canonical_json_hash(content),
        )

    def projector_refs(
        self,
        result: object,
        *,
        resource_event_hash: str,
    ) -> ActivationArmEventRefsV2:
        """Join post-executor resource authority with refs-only factory output."""
        from src.alpha_foundry.activation.candidate_factory_v1 import (
            ProductionActivationFactoryArmResultV1,
        )

        if not isinstance(result, ProductionActivationFactoryArmResultV1):
            raise TypeError("pair coordinator requires a refs-only factory result")
        resource = self.factory._event(
            resource_event_hash, "ActivationResourceMeasuredV2"
        )
        return ActivationArmEventRefsV2(
            retrieval_authority_event_hashes=result.retriever_decision_event_refs,
            terminal_event_hashes=result.trial_terminal_event_refs,
            evaluation_event_hashes=result.evaluation_event_refs,
            quality_decision_event_hashes=result.quality_decision_event_refs,
            terminal_dossier_event_hashes=result.terminal_dossier_event_refs,
            resource_event_hashes=(resource.event_hash,),
        )

    def update_shared_process_memory(
        self, completion: ActivationPairCompletionV2
    ) -> None:
        if not completion.complete:
            raise RuntimeError("pair completion must precede shared memory update")
        self._shared_memory_updates.append(completion.completion_hash)

    @property
    def shared_memory_updates(self) -> tuple[str, ...]:
        return tuple(self._shared_memory_updates)

    def mint_activation_decision(self, *args: object, **kwargs: object) -> None:
        raise PermissionError("pair coordinator has no Activation decision authority")


__all__ = [
    "ActivationArmWorkingStateV2",
    "ActivationPairCompletionV2",
    "ActivationPairCoordinatorV2",
]
