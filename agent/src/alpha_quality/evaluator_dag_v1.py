"""Deterministic spawn-based scheduler in front of the serial evaluator.

Workers execute only closed, pure content-hash operations.  The parent process
owns timeouts, RSS limits, cancellation and every ledger write.  The existing
serial evaluator remains the sole authority for evidence, claims and decisions.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import random
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence, cast

import psutil

from src.alpha_quality.production_evaluator_v1 import (
    ProductionCandidateEvaluatorFactoryV1,
    ProductionCandidateEvaluatorV1,
    ProductionEvaluationRequestV1,
    ProductionEvaluationResultV1,
)
from src.research_ledger.hash_utils import canonical_json_hash


NodeOperation = Literal["content_hash", "sleep", "crash", "allocate"]
NodeStatus = Literal["completed", "blocked", "cancelled", "timeout", "crashed", "rss_exceeded"]


@dataclass(frozen=True)
class EvaluatorDAGNodeSpecV1:
    name: str
    dependencies: tuple[str, ...]
    operation: NodeOperation = "content_hash"

    def __post_init__(self) -> None:
        if not self.name or self.dependencies != tuple(sorted(set(self.dependencies))):
            raise ValueError("DAG node identity/dependencies are not canonical")
        if self.name in self.dependencies:
            raise ValueError("DAG node cannot depend on itself")


class _RegistryAuthority:
    pass


_REGISTRY_AUTHORITY = _RegistryAuthority()


@dataclass(frozen=True)
class EvaluatorDAGDependencyRegistryV1:
    registry_id: str
    nodes: Mapping[str, EvaluatorDAGNodeSpecV1]
    registry_hash: str
    _authority: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._authority is not _REGISTRY_AUTHORITY:
            raise TypeError("DAG dependency registries are factory-minted")
        canonical = dict(sorted(self.nodes.items()))
        if set(canonical) != {spec.name for spec in canonical.values()}:
            raise ValueError("DAG registry node keys differ from node identities")
        unknown = sorted(
            dependency for spec in canonical.values() for dependency in spec.dependencies if dependency not in canonical
        )
        if unknown:
            raise ValueError(f"DAG registry has unknown dependencies: {unknown}")
        _topological_names(canonical)
        content = {
            "schema_version": "evaluator_dag_dependency_registry.v1",
            "registry_id": self.registry_id,
            "nodes": [
                {
                    "name": spec.name,
                    "dependencies": list(spec.dependencies),
                    "operation": spec.operation,
                }
                for spec in canonical.values()
            ],
        }
        if canonical_json_hash(content) != self.registry_hash:
            raise ValueError("DAG registry hash differs")
        object.__setattr__(self, "nodes", MappingProxyType(canonical))

    @classmethod
    def production(cls) -> "EvaluatorDAGDependencyRegistryV1":
        return _build_registry(
            "production-candidate-evaluator-dag-v1",
            (
                EvaluatorDAGNodeSpecV1("source_resolution", ()),
                EvaluatorDAGNodeSpecV1("backend_capability", ("source_resolution",)),
                EvaluatorDAGNodeSpecV1("snapshot_authority", ("source_resolution",)),
                EvaluatorDAGNodeSpecV1("factor_output", ("backend_capability", "snapshot_authority")),
                EvaluatorDAGNodeSpecV1("observed_predictive", ("factor_output",)),
                EvaluatorDAGNodeSpecV1("pit_predictive", ("factor_output",)),
                EvaluatorDAGNodeSpecV1("duplicate_identity", ("factor_output",)),
                EvaluatorDAGNodeSpecV1("execution", ("factor_output",)),
                EvaluatorDAGNodeSpecV1("complement_mechanism", ("duplicate_identity", "execution")),
                EvaluatorDAGNodeSpecV1(
                    "claim_assessments",
                    ("complement_mechanism", "observed_predictive", "pit_predictive"),
                ),
                EvaluatorDAGNodeSpecV1("narrow_decision", ("claim_assessments",)),
            ),
        )


def _build_registry(
    registry_id: str,
    specs: Sequence[EvaluatorDAGNodeSpecV1],
) -> EvaluatorDAGDependencyRegistryV1:
    if not registry_id or len(specs) != len({spec.name for spec in specs}):
        raise ValueError("DAG registry identity is empty or duplicated")
    nodes = dict(sorted((spec.name, spec) for spec in specs))
    content = {
        "schema_version": "evaluator_dag_dependency_registry.v1",
        "registry_id": registry_id,
        "nodes": [
            {
                "name": spec.name,
                "dependencies": list(spec.dependencies),
                "operation": spec.operation,
            }
            for spec in nodes.values()
        ],
    }
    return EvaluatorDAGDependencyRegistryV1(
        registry_id=registry_id,
        nodes=nodes,
        registry_hash=canonical_json_hash(content),
        _authority=_REGISTRY_AUTHORITY,
    )


def _topological_names(nodes: Mapping[str, EvaluatorDAGNodeSpecV1]) -> tuple[str, ...]:
    remaining = set(nodes)
    resolved: set[str] = set()
    order: list[str] = []
    while remaining:
        ready = sorted(name for name in remaining if set(nodes[name].dependencies) <= resolved)
        if not ready:
            raise ValueError("DAG registry contains a cycle")
        order.extend(ready)
        resolved.update(ready)
        remaining.difference_update(ready)
    return tuple(order)


@dataclass(frozen=True)
class EvaluatorDAGSchedulerPolicyV1:
    maximum_workers: int = 4
    node_timeout_seconds: float = 30.0
    maximum_worker_rss_bytes: int = 512 * 1024**2
    deterministic_seed: int = 3201
    cache_namespace: str = "ags-v32-evaluator-dag-v1"

    def __post_init__(self) -> None:
        if not 1 <= self.maximum_workers <= 32:
            raise ValueError("DAG worker count is outside the closed resource budget")
        if not 0.01 <= self.node_timeout_seconds <= 300.0:
            raise ValueError("DAG timeout is outside the closed resource budget")
        if not 8 * 1024**2 <= self.maximum_worker_rss_bytes <= 8 * 1024**3:
            raise ValueError("DAG RSS limit is outside the closed resource budget")
        if not self.cache_namespace:
            raise ValueError("DAG cache namespace is required")

    @property
    def policy_hash(self) -> str:
        return cast(
            str,
            canonical_json_hash(
                {
                    "schema_version": "evaluator_dag_scheduler_policy.v1",
                    "maximum_workers": self.maximum_workers,
                    "node_timeout_seconds": self.node_timeout_seconds,
                    "maximum_worker_rss_bytes": self.maximum_worker_rss_bytes,
                    "deterministic_seed": self.deterministic_seed,
                    "cache_namespace": self.cache_namespace,
                },
            ),
        )


@dataclass(frozen=True)
class EvaluatorDAGNodeResultV1:
    node_name: str
    status: NodeStatus
    input_hash: str
    output_hash: str | None
    reason_codes: tuple[str, ...]
    peak_rss_bytes: int


@dataclass(frozen=True)
class EvaluatorDAGSchedulerResultV1:
    registry_hash: str
    scheduler_policy_hash: str
    node_results: tuple[EvaluatorDAGNodeResultV1, ...]
    semantic_graph_hash: str

    @property
    def completed(self) -> bool:
        return all(item.status == "completed" for item in self.node_results)


def _worker_entry(
    connection: Any,
    operation: NodeOperation,
    input_hash: str,
    external_input: Mapping[str, Any],
) -> None:
    try:
        if operation == "sleep":
            time.sleep(float(external_input.get("sleep_seconds", 0.0)))
        elif operation == "crash":
            os._exit(23)
        elif operation == "allocate":
            size = int(external_input.get("allocate_bytes", 0))
            allocation = bytearray(size)
            if allocation:
                allocation[0] = 1
                allocation[-1] = 1
            time.sleep(float(external_input.get("hold_seconds", 0.1)))
        output_hash = canonical_json_hash(
            {
                "schema_version": "evaluator_dag_node_output.v1",
                "operation": operation,
                "input_hash": input_hash,
            }
        )
        rss = psutil.Process(os.getpid()).memory_info().rss
        connection.send((output_hash, rss))
    finally:
        connection.close()


class DeterministicEvaluatorDAGSchedulerV1:
    def __init__(
        self,
        registry: EvaluatorDAGDependencyRegistryV1,
        *,
        policy: EvaluatorDAGSchedulerPolicyV1 | None = None,
    ) -> None:
        if not isinstance(registry, EvaluatorDAGDependencyRegistryV1):
            raise TypeError("scheduler requires a registered dependency DAG")
        self.registry = registry
        self.policy = policy or EvaluatorDAGSchedulerPolicyV1()

    def run(
        self,
        inputs_by_node: Mapping[str, Mapping[str, Any]],
        *,
        schedule_seed: int = 0,
        cancelled_nodes: frozenset[str] = frozenset(),
    ) -> EvaluatorDAGSchedulerResultV1:
        unknown = (set(inputs_by_node) | set(cancelled_nodes)) - set(self.registry.nodes)
        if unknown:
            raise ValueError(f"DAG request contains unknown nodes: {sorted(unknown)}")
        results: dict[str, EvaluatorDAGNodeResultV1] = {}
        remaining = set(self.registry.nodes)
        rng = random.Random(schedule_seed)
        while remaining:
            ready = [name for name in remaining if set(self.registry.nodes[name].dependencies) <= set(results)]
            if not ready:
                raise RuntimeError("validated DAG made no scheduling progress")
            ready.sort()
            rng.shuffle(ready)
            for offset in range(0, len(ready), self.policy.maximum_workers):
                batch = ready[offset : offset + self.policy.maximum_workers]
                runnable: list[str] = []
                for name in batch:
                    spec = self.registry.nodes[name]
                    external = dict(inputs_by_node.get(name, {}))
                    dependency_results = [results[item] for item in spec.dependencies]
                    input_hash = self._input_hash(name, external, dependency_results)
                    blockers = tuple(
                        sorted(
                            f"BLOCKED_BY_{item.node_name}_{item.status}".upper()
                            for item in dependency_results
                            if item.status != "completed"
                        )
                    )
                    if blockers:
                        results[name] = EvaluatorDAGNodeResultV1(name, "blocked", input_hash, None, blockers, 0)
                    elif name in cancelled_nodes:
                        results[name] = EvaluatorDAGNodeResultV1(
                            name,
                            "cancelled",
                            input_hash,
                            None,
                            ("DAG_NODE_CANCELLED",),
                            0,
                        )
                    else:
                        runnable.append(name)
                self._execute_batch(runnable, inputs_by_node, results)
                remaining.difference_update(batch)
        ordered = tuple(results[name] for name in sorted(results))
        graph_content = {
            "schema_version": "evaluator_dag_semantic_graph.v1",
            "registry_hash": self.registry.registry_hash,
            "scheduler_policy_hash": self.policy.policy_hash,
            "nodes": [
                {
                    "node_name": item.node_name,
                    "status": item.status,
                    "input_hash": item.input_hash,
                    "output_hash": item.output_hash,
                    "reason_codes": list(item.reason_codes),
                }
                for item in ordered
            ],
        }
        return EvaluatorDAGSchedulerResultV1(
            registry_hash=self.registry.registry_hash,
            scheduler_policy_hash=self.policy.policy_hash,
            node_results=ordered,
            semantic_graph_hash=canonical_json_hash(graph_content),
        )

    def _input_hash(
        self,
        name: str,
        external_input: Mapping[str, Any],
        dependency_results: Sequence[EvaluatorDAGNodeResultV1],
    ) -> str:
        return cast(
            str,
            canonical_json_hash(
                {
                    "schema_version": "evaluator_dag_node_input.v1",
                    "registry_hash": self.registry.registry_hash,
                    "node_name": name,
                    "operation": self.registry.nodes[name].operation,
                    "dependency_output_hashes": {item.node_name: item.output_hash for item in dependency_results},
                    "external_input": dict(external_input),
                    "deterministic_seed": self.policy.deterministic_seed,
                    "cache_namespace": self.policy.cache_namespace,
                },
            ),
        )

    def _execute_batch(
        self,
        names: Sequence[str],
        inputs_by_node: Mapping[str, Mapping[str, Any]],
        results: dict[str, EvaluatorDAGNodeResultV1],
    ) -> None:
        if not names:
            return
        context = mp.get_context("spawn")
        active: dict[str, tuple[Any, Any, float, str, int]] = {}
        for name in names:
            spec = self.registry.nodes[name]
            dependencies = [results[item] for item in spec.dependencies]
            input_hash = self._input_hash(name, dict(inputs_by_node.get(name, {})), dependencies)
            parent, child = context.Pipe(duplex=False)
            process = context.Process(
                target=_worker_entry,
                args=(child, spec.operation, input_hash, dict(inputs_by_node.get(name, {}))),
                daemon=False,
            )
            try:
                process.start()
            except (OSError, RuntimeError):
                child.close()
                parent.close()
                results[name] = EvaluatorDAGNodeResultV1(
                    node_name=name,
                    status="crashed",
                    input_hash=input_hash,
                    output_hash=None,
                    reason_codes=("DAG_WORKER_START_FAILED",),
                    peak_rss_bytes=0,
                )
                continue
            child.close()
            active[name] = (process, parent, time.monotonic(), input_hash, 0)
        while active:
            for name in tuple(active):
                process, connection, started, input_hash, peak_rss = active[name]
                try:
                    rss = psutil.Process(process.pid).memory_info().rss
                except (psutil.Error, TypeError):
                    rss = 0
                peak_rss = max(peak_rss, rss)
                status: NodeStatus | None = None
                reason: tuple[str, ...] = ()
                output_hash: str | None = None
                if peak_rss > self.policy.maximum_worker_rss_bytes:
                    status, reason = "rss_exceeded", ("DAG_WORKER_RSS_EXCEEDED",)
                    process.terminate()
                elif time.monotonic() - started > self.policy.node_timeout_seconds:
                    status, reason = "timeout", ("DAG_WORKER_TIMEOUT",)
                    process.terminate()
                else:
                    try:
                        has_message = connection.poll()
                    except (BrokenPipeError, EOFError, OSError):
                        has_message = False
                        if not process.is_alive():
                            status, reason = "crashed", ("DAG_WORKER_CRASHED",)
                    if has_message:
                        try:
                            output_hash, child_rss = connection.recv()
                        except (BrokenPipeError, EOFError, OSError):
                            status, reason = "crashed", ("DAG_WORKER_CRASHED",)
                        else:
                            peak_rss = max(peak_rss, int(child_rss))
                            status = "completed"
                if status is None and not process.is_alive():
                    status, reason = "crashed", ("DAG_WORKER_CRASHED",)
                if status is None:
                    active[name] = (
                        process,
                        connection,
                        started,
                        input_hash,
                        peak_rss,
                    )
                    continue
                process.join(timeout=1.0)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=1.0)
                connection.close()
                results[name] = EvaluatorDAGNodeResultV1(
                    node_name=name,
                    status=status,
                    input_hash=input_hash,
                    output_hash=output_hash,
                    reason_codes=reason,
                    peak_rss_bytes=peak_rss,
                )
                del active[name]
            if active:
                time.sleep(0.002)


@dataclass(frozen=True)
class ProductionDAGEvaluationResultV1:
    authoritative_result: ProductionEvaluationResultV1
    scheduler_result: EvaluatorDAGSchedulerResultV1
    authority_mode: Literal["serial_reference"] = "serial_reference"


class _DAGFactoryToken:
    pass


_DAG_FACTORY_TOKEN = _DAGFactoryToken()


class ProductionCandidateDAGEvaluatorV1:
    def __init__(self, store: Any, *, _token: _DAGFactoryToken) -> None:
        if _token is not _DAG_FACTORY_TOKEN:
            raise TypeError("production DAG evaluator must be factory-minted")
        self.serial: ProductionCandidateEvaluatorV1 = ProductionCandidateEvaluatorFactoryV1.create(store)
        self.scheduler = DeterministicEvaluatorDAGSchedulerV1(EvaluatorDAGDependencyRegistryV1.production())

    def evaluate(self, request: ProductionEvaluationRequestV1) -> ProductionDAGEvaluationResultV1:
        sources = self.serial._resolve_sources(request)
        source_hashes = {name: event.payload_hash for name, event in sorted(sources.items())}
        inputs = {
            "source_resolution": {"source_payload_hashes": source_hashes},
            "backend_capability": {"contract_hash": request.resolved_contract_hash},
            "snapshot_authority": {"snapshot_payload_hash": sources["snapshot"].payload_hash},
        }
        scheduled = self.scheduler.run(inputs)
        if scheduled.completed:
            authoritative = self.serial.evaluate(request)
        else:
            failure = next(
                (
                    item
                    for item in scheduled.node_results
                    if item.status in {"timeout", "crashed", "rss_exceeded", "cancelled"}
                ),
                next(item for item in scheduled.node_results if item.status != "completed"),
            )
            node = self.serial._node(
                request,
                sources["factor"].entity_id,
                "backend_capability",
                "invalid",
                failure.reason_codes or ("DAG_SCHEDULER_BLOCKED",),
                (sources["factor"].event_hash,),
            )
            authoritative = self.serial._terminate_failure(
                request,
                sources["factor"],
                completion_status=("timeout" if failure.status == "timeout" else "infrastructure_failure"),
                reason_codes=failure.reason_codes or ("DAG_SCHEDULER_BLOCKED",),
                evidence=[],
                nodes=[node],
            )
        return ProductionDAGEvaluationResultV1(authoritative, scheduled)


class ProductionCandidateDAGEvaluatorFactoryV1:
    @staticmethod
    def create(store: Any) -> ProductionCandidateDAGEvaluatorV1:
        return ProductionCandidateDAGEvaluatorV1(store, _token=_DAG_FACTORY_TOKEN)


__all__ = [
    "DeterministicEvaluatorDAGSchedulerV1",
    "EvaluatorDAGDependencyRegistryV1",
    "EvaluatorDAGNodeResultV1",
    "EvaluatorDAGNodeSpecV1",
    "EvaluatorDAGSchedulerPolicyV1",
    "EvaluatorDAGSchedulerResultV1",
    "ProductionCandidateDAGEvaluatorFactoryV1",
    "ProductionCandidateDAGEvaluatorV1",
    "ProductionDAGEvaluationResultV1",
]
