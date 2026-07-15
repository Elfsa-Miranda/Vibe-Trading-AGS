"""Parent-enforced process isolation for formal Activation arm execution.

The worker surface is deliberately closed.  It contains infrastructure probes
only; production research execution must be added as a separately reviewed
operation rather than accepting a caller-provided callable or import path.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import multiprocessing
from multiprocessing.connection import Connection
import time
from typing import Any, Literal, Mapping

import psutil

from src.research_ledger.hash_utils import canonical_json_hash


WorkerOperationV3 = Literal["resource_probe", "timeout_probe"]
WorkerStatusV3 = Literal["completed", "timeout", "worker_error"]


@dataclass(frozen=True)
class IsolatedWorkerTaskV3:
    schema_version: Literal["activation_isolated_worker_task.v3"]
    operation: WorkerOperationV3
    duration_seconds: float
    allocation_mb: int
    nonce: str
    task_hash: str

    @classmethod
    def create(
        cls,
        *,
        operation: WorkerOperationV3,
        duration_seconds: float,
        allocation_mb: int,
        nonce: str,
    ) -> "IsolatedWorkerTaskV3":
        content = {
            "schema_version": "activation_isolated_worker_task.v3",
            "operation": operation,
            "duration_seconds": duration_seconds,
            "allocation_mb": allocation_mb,
            "nonce": nonce,
        }
        return cls(
            schema_version="activation_isolated_worker_task.v3",
            operation=operation,
            duration_seconds=duration_seconds,
            allocation_mb=allocation_mb,
            nonce=nonce,
            task_hash=canonical_json_hash(content),
        )

    def __post_init__(self) -> None:
        if self.schema_version != "activation_isolated_worker_task.v3":
            raise ValueError("unsupported isolated worker task schema")
        if (
            isinstance(self.duration_seconds, bool)
            or not math.isfinite(self.duration_seconds)
            or self.duration_seconds < 0.0
            or isinstance(self.allocation_mb, bool)
            or not 0 <= self.allocation_mb <= 256
            or not self.nonce
        ):
            raise ValueError("isolated worker task is outside its resource bounds")
        if self.operation == "timeout_probe" and self.duration_seconds <= 0.0:
            raise ValueError("timeout probe must remain alive long enough to terminate")
        if self.task_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("isolated worker task hash mismatch")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operation": self.operation,
            "duration_seconds": self.duration_seconds,
            "allocation_mb": self.allocation_mb,
            "nonce": self.nonce,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "task_hash": self.task_hash}


@dataclass(frozen=True)
class IsolatedWorkerResourceV3:
    schema_version: Literal["activation_isolated_worker_resource.v3"]
    task_hash: str
    status: WorkerStatusV3
    timeout_limit_seconds: float
    timeout_enforced: bool
    worker_start_method: Literal["spawn"]
    worker_exit_code: int | None
    wall_seconds: float
    cpu_seconds: float
    peak_rss_mb: float
    peak_rss_method: Literal["parent_psutil_isolated_process_tree.v1"]
    response_hash: str | None
    failure_codes: tuple[str, ...]
    evidence_hash: str

    @classmethod
    def create(
        cls,
        *,
        task_hash: str,
        status: WorkerStatusV3,
        timeout_limit_seconds: float,
        worker_exit_code: int | None,
        wall_seconds: float,
        cpu_seconds: float,
        peak_rss_mb: float,
        response_hash: str | None,
        failure_codes: tuple[str, ...] = (),
    ) -> "IsolatedWorkerResourceV3":
        normalized = tuple(sorted(set(failure_codes)))
        content = {
            "schema_version": "activation_isolated_worker_resource.v3",
            "task_hash": task_hash,
            "status": status,
            "timeout_limit_seconds": timeout_limit_seconds,
            "timeout_enforced": True,
            "worker_start_method": "spawn",
            "worker_exit_code": worker_exit_code,
            "wall_seconds": wall_seconds,
            "cpu_seconds": cpu_seconds,
            "peak_rss_mb": peak_rss_mb,
            "peak_rss_method": "parent_psutil_isolated_process_tree.v1",
            "response_hash": response_hash,
            "failure_codes": list(normalized),
        }
        return cls(
            schema_version="activation_isolated_worker_resource.v3",
            task_hash=task_hash,
            status=status,
            timeout_limit_seconds=timeout_limit_seconds,
            timeout_enforced=True,
            worker_start_method="spawn",
            worker_exit_code=worker_exit_code,
            wall_seconds=wall_seconds,
            cpu_seconds=cpu_seconds,
            peak_rss_mb=peak_rss_mb,
            peak_rss_method="parent_psutil_isolated_process_tree.v1",
            response_hash=response_hash,
            failure_codes=normalized,
            evidence_hash=canonical_json_hash(content),
        )

    def __post_init__(self) -> None:
        if not self.timeout_enforced or self.worker_start_method != "spawn":
            raise ValueError("formal Activation requires parent-enforced spawn workers")
        if self.peak_rss_method != "parent_psutil_isolated_process_tree.v1":
            raise ValueError("formal Activation requires isolated process-tree RSS")
        for value in (self.timeout_limit_seconds, self.wall_seconds, self.cpu_seconds, self.peak_rss_mb):
            if isinstance(value, bool) or not math.isfinite(value) or value < 0.0:
                raise ValueError("isolated worker resource measurement is invalid")
        if self.timeout_limit_seconds <= 0.0:
            raise ValueError("worker timeout must be positive")
        if self.status == "timeout":
            if "WORKER_TIMEOUT_ENFORCED" not in self.failure_codes or self.response_hash is not None:
                raise ValueError("timeout evidence must prove enforcement and have no response")
        elif self.status == "completed":
            if self.failure_codes or self.response_hash is None or self.worker_exit_code != 0:
                raise ValueError("completed worker evidence is internally inconsistent")
        elif not self.failure_codes:
            raise ValueError("worker error requires an exact failure code")
        if self.evidence_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("isolated worker resource hash mismatch")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_hash": self.task_hash,
            "status": self.status,
            "timeout_limit_seconds": self.timeout_limit_seconds,
            "timeout_enforced": self.timeout_enforced,
            "worker_start_method": self.worker_start_method,
            "worker_exit_code": self.worker_exit_code,
            "wall_seconds": self.wall_seconds,
            "cpu_seconds": self.cpu_seconds,
            "peak_rss_mb": self.peak_rss_mb,
            "peak_rss_method": self.peak_rss_method,
            "response_hash": self.response_hash,
            "failure_codes": list(self.failure_codes),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "evidence_hash": self.evidence_hash}


def _child_main(connection: Connection, task: IsolatedWorkerTaskV3) -> None:
    """Execute only closed probe operations; never open a ledger or artifact DB."""
    try:
        allocation = bytearray(task.allocation_mb * 1024 * 1024)
        for index in range(0, len(allocation), 4096):
            allocation[index] = (index // 4096) % 251
        deadline = time.perf_counter() + task.duration_seconds
        accumulator = 0
        while time.perf_counter() < deadline:
            accumulator = (accumulator * 33 + 17) % 1_000_003
            if task.operation == "timeout_probe":
                time.sleep(0.005)
        response = {
            "schema_version": "activation_isolated_worker_response.v3",
            "task_hash": task.task_hash,
            "allocation_bytes": len(allocation),
            "accumulator": accumulator,
        }
        connection.send(response)
    except BaseException as exc:  # pragma: no cover - defensive child boundary
        connection.send({
            "schema_version": "activation_isolated_worker_error.v3",
            "task_hash": task.task_hash,
            "error_type": type(exc).__name__,
        })
    finally:
        connection.close()


def _process_tree_usage(process: psutil.Process) -> tuple[float, float]:
    rss = 0
    cpu = 0.0
    try:
        current_processes = (process, *process.children(recursive=True))
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        current_processes = (process,)
    for current in current_processes:
        try:
            rss += int(current.memory_info().rss)
            times = current.cpu_times()
            cpu += float(times.user + times.system)
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    return rss / (1024.0 * 1024.0), cpu


class ParentEnforcedWorkerV3:
    """Run one arm in its own spawn process and enforce its wall-clock limit."""

    def run(
        self,
        task: IsolatedWorkerTaskV3,
        *,
        timeout_seconds: float,
        sample_interval_seconds: float = 0.01,
    ) -> IsolatedWorkerResourceV3:
        if (
            isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0.0
            or not 0.001 <= sample_interval_seconds <= 0.1
        ):
            raise ValueError("worker timeout or sampling interval is invalid")
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=False)
        worker = context.Process(target=_child_main, args=(child, task), daemon=False)
        started = time.perf_counter()
        worker.start()
        child.close()
        tracked = psutil.Process(worker.pid)
        peak_rss = 0.0
        cpu_seconds = 0.0
        timed_out = False
        while worker.is_alive():
            elapsed = time.perf_counter() - started
            rss, cpu = _process_tree_usage(tracked)
            peak_rss = max(peak_rss, rss)
            cpu_seconds = max(cpu_seconds, cpu)
            if elapsed >= timeout_seconds:
                timed_out = True
                worker.terminate()
                worker.join(timeout=1.0)
                if worker.is_alive():
                    worker.kill()
                    worker.join(timeout=1.0)
                break
            time.sleep(sample_interval_seconds)
        worker.join(timeout=0.2)
        wall_seconds = time.perf_counter() - started
        rss, cpu = _process_tree_usage(tracked)
        peak_rss = max(peak_rss, rss)
        cpu_seconds = max(cpu_seconds, cpu)
        response: Mapping[str, Any] | None = None
        if not timed_out and parent.poll(0.1):
            received = parent.recv()
            if isinstance(received, Mapping):
                response = received
        parent.close()
        if timed_out:
            return IsolatedWorkerResourceV3.create(
                task_hash=task.task_hash,
                status="timeout",
                timeout_limit_seconds=timeout_seconds,
                worker_exit_code=worker.exitcode,
                wall_seconds=wall_seconds,
                cpu_seconds=cpu_seconds,
                peak_rss_mb=peak_rss,
                response_hash=None,
                failure_codes=("WORKER_TIMEOUT_ENFORCED",),
            )
        if worker.exitcode != 0 or response is None or response.get("schema_version") != "activation_isolated_worker_response.v3":
            return IsolatedWorkerResourceV3.create(
                task_hash=task.task_hash,
                status="worker_error",
                timeout_limit_seconds=timeout_seconds,
                worker_exit_code=worker.exitcode,
                wall_seconds=wall_seconds,
                cpu_seconds=cpu_seconds,
                peak_rss_mb=peak_rss,
                response_hash=None,
                failure_codes=("WORKER_RESPONSE_INVALID",),
            )
        return IsolatedWorkerResourceV3.create(
            task_hash=task.task_hash,
            status="completed",
            timeout_limit_seconds=timeout_seconds,
            worker_exit_code=worker.exitcode,
            wall_seconds=wall_seconds,
            cpu_seconds=cpu_seconds,
            peak_rss_mb=peak_rss,
            response_hash=canonical_json_hash(dict(response)),
        )


__all__ = [
    "IsolatedWorkerResourceV3",
    "IsolatedWorkerTaskV3",
    "ParentEnforcedWorkerV3",
]
