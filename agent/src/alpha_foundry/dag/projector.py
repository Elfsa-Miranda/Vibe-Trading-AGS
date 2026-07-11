"""Deterministic, replayable projection of typed research events into a DAG."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Literal, Mapping, cast

from src.alpha_foundry.dag.bootstrap import validate_registry_bootstrap_payload
from src.alpha_foundry.dag.model import (
    DerivationEdge,
    FactorDAGError,
    FactorDAGProjection,
    FactorNode,
    RegistryRootNode,
)
from src.alpha_foundry.dsl.identity import validate_factor_definition_payload
from src.alpha_quality.flags import ResolvedAGSFlags
from src.research_ledger.events import ResearchEventEnvelope
from src.research_ledger.events.model import VerifiedEventSubsequence
from src.research_ledger.hash_utils import canonical_json_hash


class FactorDAGProjector:
    """Construct the read-only audit DAG from ordered immutable event history."""

    def __init__(self, *, flags: ResolvedAGSFlags) -> None:
        required = (
            "VIBE_TRADING_ALPHA_FOUNDRY", "VIBE_TRADING_RESEARCH_EVENTS",
            "VIBE_TRADING_FACTOR_DAG",
        )
        if any(not flags.enabled(name) for name in required):
            raise RuntimeError("factor DAG capability is disabled")
        self.flags = flags

    def project(
        self,
        events: Iterable[ResearchEventEnvelope] | VerifiedEventSubsequence,
    ) -> FactorDAGProjection:
        if isinstance(events, VerifiedEventSubsequence):
            if not events.is_authorized():
                raise FactorDAGError(
                    "factor DAG received an unauthorized event subsequence"
                )
            verified_subsequence = True
        else:
            verified_subsequence = False
        ordered = list(events)
        if not verified_subsequence:
            _validate_envelope_chain(ordered)
        nodes: dict[str, FactorNode] = {}
        roots: dict[str, RegistryRootNode] = {}
        edges: list[DerivationEdge] = []
        terminal_events: dict[str, ResearchEventEnvelope] = {}
        evaluation_events: dict[str, ResearchEventEnvelope] = {}
        for event in ordered:
            if event.event_type == "TrialTerminated":
                terminal_events[event.event_hash] = event
            elif event.event_type == "EvaluationRecorded":
                evaluation_events[event.event_hash] = event
            elif event.event_type == "FactorDefinitionRecorded":
                self._apply_definition(nodes, event)
            elif event.event_type in {"RegistryBootstrapRecorded", "RegistryBootstrapRecordedV2"}:
                self._apply_registry_bootstrap(roots, event)
            elif event.event_type == "DerivationRecorded":
                self._apply_derivation(
                    nodes, edges, terminal_events, evaluation_events, event
                )
        return self._finalize(ordered, nodes, roots, edges)

    def resume(
        self,
        events: Iterable[ResearchEventEnvelope],
        checkpoint: FactorDAGProjection,
    ) -> FactorDAGProjection:
        """Validate a checkpoint then deterministically resume from its watermark."""
        ordered = list(events)
        if checkpoint.schema_version != "factor_dag_projection.v1":
            raise FactorDAGError("unsupported factor DAG checkpoint version")
        if checkpoint.source_event_count > len(ordered):
            raise FactorDAGError("checkpoint exceeds supplied event history")
        if checkpoint.source_event_count:
            actual = ordered[checkpoint.source_event_count - 1].event_hash
            if actual != checkpoint.source_watermark_event_hash:
                raise FactorDAGError("checkpoint watermark does not match event history")
        elif checkpoint.source_watermark_event_hash is not None:
            raise FactorDAGError("empty checkpoint has a watermark")
        # Rebuild the prefix to prove that the supplied checkpoint is a derived
        # view, not caller-authored depth/edge state; then use it as the resume
        # basis for the remaining append-only suffix.
        rebuilt = self.project(ordered[: checkpoint.source_event_count])
        if rebuilt.projection_hash != checkpoint.projection_hash:
            raise FactorDAGError("checkpoint projection hash does not match replay")
        if checkpoint.source_event_count == len(ordered):
            return rebuilt
        return self.project(ordered)

    @staticmethod
    def _apply_definition(nodes: dict[str, FactorNode], event: ResearchEventEnvelope) -> None:
        payload = event.payload
        try:
            validate_factor_definition_payload(payload)
        except (TypeError, ValueError) as exc:
            raise FactorDAGError("factor definition identity is not reproducible") from exc
        factor_spec_id = str(payload["factor_spec_id"])
        node = FactorNode(
            factor_spec_id=factor_spec_id,
            expression_id=str(payload["expression_id"]),
            canonical_ast_hash=str(payload["canonical_ast_hash"]),
            grammar_version=str(payload["grammar_version"]),
            grammar_hash=str(payload["grammar_hash"]),
            originating_trial_id=str(payload["metadata"]["originating_trial_id"]),
            definition_event_hash=event.event_hash,
        )
        prior = nodes.get(factor_spec_id)
        if prior is not None and prior != node:
            raise FactorDAGError("duplicate factor definition is ambiguous")
        if prior is not None:
            raise FactorDAGError("duplicate factor definition event")
        nodes[factor_spec_id] = node

    @staticmethod
    def _apply_registry_bootstrap(
        roots: dict[str, RegistryRootNode], event: ResearchEventEnvelope
    ) -> None:
        payload = event.payload
        if event.event_type == "RegistryBootstrapRecordedV2":
            try:
                validate_registry_bootstrap_payload(payload)
            except (KeyError, TypeError, ValueError) as exc:
                raise FactorDAGError(
                    "registry bootstrap identity is not reproducible"
                ) from exc
        snapshot_id = str(payload["snapshot_id"])
        for raw in payload["roots"]:
            alpha_id = str(raw["alpha_id"])
            root_id = f"registry:{snapshot_id}:{alpha_id}"
            if root_id in roots:
                raise FactorDAGError("duplicate registry root in bootstrap history")
            roots[root_id] = RegistryRootNode(
                root_id=root_id,
                snapshot_id=snapshot_id,
                alpha_id=alpha_id,
                status=cast(
                    Literal["canonical_dsl", "legacy_opaque"], str(raw["status"])
                ),
                expression_id=(
                    None if raw["expression_id"] is None else str(raw["expression_id"])
                ),
                legacy_formula_hash=str(raw["legacy_formula_hash"]),
                canonical_formula=(
                    None
                    if raw.get("canonical_formula") is None
                    else str(raw["canonical_formula"])
                ),
                source_hash=(
                    None if raw.get("source_hash") is None else str(raw["source_hash"])
                ),
                source_status=cast(
                    Literal["available", "unavailable"],
                    str(raw.get("source_status", "unavailable")),
                ),
                source_reason=(
                    None
                    if raw.get("source_reason") is None
                    else str(raw["source_reason"])
                ),
                bootstrap_event_hash=event.event_hash,
            )

    @staticmethod
    def _apply_derivation(
        nodes: Mapping[str, FactorNode],
        edges: list[DerivationEdge],
        terminal_events: Mapping[str, ResearchEventEnvelope],
        evaluation_events: Mapping[str, ResearchEventEnvelope],
        event: ResearchEventEnvelope,
    ) -> None:
        payload = event.payload
        child = str(payload["child_factor_spec_id"])
        parents = tuple(str(value) for value in payload["parent_factor_spec_ids"])
        if child not in nodes:
            raise FactorDAGError("derivation child has no prior factor definition")
        if not parents or any(parent not in nodes for parent in parents):
            raise FactorDAGError("derivation parent has no prior factor definition")
        if child in parents:
            raise FactorDAGError("lineage self-edge is forbidden")
        if len(set(parents)) != len(parents):
            raise FactorDAGError("derivation contains duplicate parent")
        terminal = terminal_events.get(str(payload["trial_terminal_event_hash"]))
        if terminal is None:
            raise FactorDAGError("derivation has no prior terminal trial event")
        originating_trial_id = nodes[child].originating_trial_id
        if str(terminal.payload["trial_id"]) != originating_trial_id:
            raise FactorDAGError("derivation terminal is not bound to the child trial")
        if terminal.payload["status"] not in {"success", "reject"}:
            raise FactorDAGError("derivation terminal is not an evaluated outcome")
        evaluation_hash = terminal.payload["evaluation_event_hash"]
        evaluation = evaluation_events.get(str(evaluation_hash)) if evaluation_hash else None
        if evaluation is None or (
            evaluation.payload["trial_id"] != originating_trial_id
            or evaluation.payload["factor_spec_id"] != child
            or evaluation.payload["data_scope"] not in {"valid", "train_valid"}
        ):
            raise FactorDAGError("derivation lacks matching train/valid evaluation evidence")
        if any(edge.child_factor_spec_id == child for edge in edges):
            raise FactorDAGError("multiple lineage derivations for one child are ambiguous")
        children = _children_by_parent(edges)
        if any(_has_path(children, child, parent) for parent in parents):
            raise FactorDAGError("derivation creates a lineage cycle")
        edges.append(
            DerivationEdge(
                child_factor_spec_id=child,
                parent_factor_spec_ids=parents,
                trial_terminal_event_hash=str(payload["trial_terminal_event_hash"]),
                derivation_kind=cast(
                    Literal["mutation", "crossover", "manual_registered"],
                    str(payload["derivation_kind"]),
                ),
                event_hash=event.event_hash,
            )
        )

    @staticmethod
    def _finalize(
        events: list[ResearchEventEnvelope],
        nodes: Mapping[str, FactorNode],
        roots: Mapping[str, RegistryRootNode],
        edges: list[DerivationEdge],
    ) -> FactorDAGProjection:
        depth = _depths(nodes, edges)
        state = {
            "schema_version": "factor_dag_projection.v1",
            "source_event_count": len(events),
            "source_watermark_event_hash": events[-1].event_hash if events else None,
            "factor_nodes": [
                {
                    "factor_spec_id": node.factor_spec_id,
                    "expression_id": node.expression_id,
                    "canonical_ast_hash": node.canonical_ast_hash,
                    "grammar_version": node.grammar_version,
                    "grammar_hash": node.grammar_hash,
                    "originating_trial_id": node.originating_trial_id,
                    "definition_event_hash": node.definition_event_hash,
                }
                for _, node in sorted(nodes.items())
            ],
            "registry_roots": [
                {
                    "root_id": root.root_id,
                    "snapshot_id": root.snapshot_id,
                    "alpha_id": root.alpha_id,
                    "status": root.status,
                    "expression_id": root.expression_id,
                    "legacy_formula_hash": root.legacy_formula_hash,
                    "canonical_formula": root.canonical_formula,
                    "source_hash": root.source_hash,
                    "source_status": root.source_status,
                    "source_reason": root.source_reason,
                    "bootstrap_event_hash": root.bootstrap_event_hash,
                }
                for _, root in sorted(roots.items())
            ],
            "derivation_edges": [
                {
                    "child_factor_spec_id": edge.child_factor_spec_id,
                    "parent_factor_spec_ids": list(edge.parent_factor_spec_ids),
                    "trial_terminal_event_hash": edge.trial_terminal_event_hash,
                    "derivation_kind": edge.derivation_kind,
                    "event_hash": edge.event_hash,
                }
                for edge in edges
            ],
            "depth_by_factor_spec_id": dict(sorted(depth.items())),
            "source_event_hashes": [event.event_hash for event in events],
        }
        return FactorDAGProjection(
            schema_version="factor_dag_projection.v1",
            source_event_count=len(events),
            source_watermark_event_hash=events[-1].event_hash if events else None,
            factor_nodes=nodes,
            registry_roots=roots,
            derivation_edges=tuple(edges),
            depth_by_factor_spec_id=depth,
            projection_hash=canonical_json_hash(state),
        )


def _children_by_parent(edges: Iterable[DerivationEdge]) -> dict[str, set[str]]:
    children: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        for parent in edge.parent_factor_spec_ids:
            children[parent].add(edge.child_factor_spec_id)
    return children


def _validate_envelope_chain(events: list[ResearchEventEnvelope]) -> None:
    previous: str | None = None
    for event in events:
        if event.previous_event_hash != previous:
            raise FactorDAGError("factor DAG source event chain is out of order")
        event_dict = event.to_dict()
        if canonical_json_hash(event_dict["payload"]) != event.payload_hash:
            raise FactorDAGError("factor DAG source payload hash is invalid")
        envelope = event_dict
        envelope.pop("event_hash")
        if canonical_json_hash(envelope) != event.event_hash:
            raise FactorDAGError("factor DAG source event hash is invalid")
        previous = event.event_hash


def _has_path(children: Mapping[str, set[str]], start: str, target: str) -> bool:
    pending = [start]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in seen:
            continue
        seen.add(current)
        pending.extend(sorted(children.get(current, ()), reverse=True))
    return False


def _depths(nodes: Mapping[str, FactorNode], edges: Iterable[DerivationEdge]) -> dict[str, int]:
    import heapq

    edge_list = list(edges)
    parents_by_child = {
        edge.child_factor_spec_id: edge.parent_factor_spec_ids for edge in edge_list
    }
    children = _children_by_parent(edge_list)
    remaining = {node_id: len(parents_by_child.get(node_id, ())) for node_id in nodes}
    ready = [node_id for node_id, count in remaining.items() if count == 0]
    heapq.heapify(ready)
    depth = {node_id: 0 for node_id in ready}
    processed = 0
    while ready:
        node_id = heapq.heappop(ready)
        processed += 1
        for child in sorted(children.get(node_id, ())):
            remaining[child] -= 1
            depth[child] = max(depth.get(child, 0), depth[node_id] + 1)
            if remaining[child] == 0:
                heapq.heappush(ready, child)
    if processed != len(nodes):
        raise FactorDAGError("factor DAG depth computation found a cycle")
    return {node_id: depth[node_id] for node_id in sorted(nodes)}


__all__ = ["FactorDAGProjector"]
