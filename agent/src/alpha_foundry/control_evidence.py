"""Replayable evidence for the official deterministic flat search control."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, TYPE_CHECKING

from src.alpha_foundry.artifacts import safe_artifact_path, safe_artifact_write_json
from src.alpha_foundry.mutators import SeedMutator
from src.alpha_foundry.seed_bank import AlphaSeed, SeedBank
from src.research_ledger.events.artifacts import hash_artifact
from src.research_ledger.events import (
    EventDraft,
    ResearchEventEnvelope,
    ResearchEventStore,
)
from src.research_ledger.hash_utils import canonical_json, canonical_json_hash, redact_secrets

if TYPE_CHECKING:
    from src.alpha_foundry.search import AlphaFoundrySearch, AlphaFoundrySearchResult


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_ARTIFACT_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class FlatControlPolicyV1:
    schema_version: str
    generator_version: str
    seed_ordering: str
    mutator_version: str
    mutation_templates: tuple[str, ...]
    max_candidates_per_seed: int
    max_candidates: int
    trial_budget: int
    policy_hash: str

    @classmethod
    def create(
        cls,
        *,
        max_candidates_per_seed: int,
        max_candidates: int,
        trial_budget: int,
    ) -> "FlatControlPolicyV1":
        content = {
            "schema_version": "flat_control_policy.v1",
            "generator_version": "alpha_foundry_search.v1",
            "seed_ordering": "seed_bank_insertion_order.v1",
            "mutator_version": "seed_mutator_templates.v1",
            "mutation_templates": [
                "identity", "rank_wrap", "decay_3", "delay_1", "zscore_wrap",
            ],
            "max_candidates_per_seed": max_candidates_per_seed,
            "max_candidates": max_candidates,
            "trial_budget": trial_budget,
        }
        return cls(
            schema_version="flat_control_policy.v1",
            generator_version="alpha_foundry_search.v1",
            seed_ordering="seed_bank_insertion_order.v1",
            mutator_version="seed_mutator_templates.v1",
            mutation_templates=(
                "identity", "rank_wrap", "decay_3", "delay_1", "zscore_wrap",
            ),
            max_candidates_per_seed=max_candidates_per_seed,
            max_candidates=max_candidates,
            trial_budget=trial_budget,
            policy_hash=canonical_json_hash(content),
        )

    def __post_init__(self) -> None:
        if (
            self.schema_version != "flat_control_policy.v1"
            or self.generator_version != "alpha_foundry_search.v1"
            or self.seed_ordering != "seed_bank_insertion_order.v1"
            or self.mutator_version != "seed_mutator_templates.v1"
            or self.mutation_templates
            != ("identity", "rank_wrap", "decay_3", "delay_1", "zscore_wrap")
        ):
            raise ValueError("unsupported flat control policy")
        for value in (
            self.max_candidates_per_seed, self.max_candidates, self.trial_budget
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("flat control budgets must be non-negative integers")
        if self.max_candidates_per_seed < 1:
            raise ValueError("flat control per-seed budget must be positive")
        if self.policy_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("flat control policy hash mismatch")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generator_version": self.generator_version,
            "seed_ordering": self.seed_ordering,
            "mutator_version": self.mutator_version,
            "mutation_templates": list(self.mutation_templates),
            "max_candidates_per_seed": self.max_candidates_per_seed,
            "max_candidates": self.max_candidates,
            "trial_budget": self.trial_budget,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "policy_hash": self.policy_hash}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "FlatControlPolicyV1":
        expected = {
            "schema_version", "generator_version", "seed_ordering",
            "mutator_version", "mutation_templates", "max_candidates_per_seed",
            "max_candidates", "trial_budget", "policy_hash",
        }
        if set(raw) != expected or not isinstance(raw["mutation_templates"], list):
            raise ValueError("flat control policy has an invalid closed schema")
        if any(
            isinstance(raw[name], bool) or not isinstance(raw[name], int)
            for name in (
                "max_candidates_per_seed", "max_candidates", "trial_budget"
            )
        ):
            raise ValueError("flat control policy budgets must be integers")
        return cls(
            schema_version=str(raw["schema_version"]),
            generator_version=str(raw["generator_version"]),
            seed_ordering=str(raw["seed_ordering"]),
            mutator_version=str(raw["mutator_version"]),
            mutation_templates=tuple(str(item) for item in raw["mutation_templates"]),
            max_candidates_per_seed=int(raw["max_candidates_per_seed"]),
            max_candidates=int(raw["max_candidates"]),
            trial_budget=int(raw["trial_budget"]),
            policy_hash=str(raw["policy_hash"]),
        )


@dataclass(frozen=True)
class OfficialSearchControlEvidenceV1:
    schema_version: str
    run_id: str
    data_snapshot_hash: str
    policy: FlatControlPolicyV1
    seeds: tuple[Mapping[str, str | None], ...]
    candidates: tuple[Mapping[str, str], ...]
    terminal_event_hashes: tuple[str, ...]
    output_hash: str
    evidence_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "official_search_control_evidence.v1":
            raise ValueError("unsupported official control evidence")
        if not self.run_id or _HASH_RE.fullmatch(self.data_snapshot_hash) is None:
            raise ValueError("official control run or snapshot is invalid")
        if len(self.terminal_event_hashes) != len(self.candidates):
            raise ValueError("official control terminals must cover every candidate")
        if self.terminal_event_hashes != tuple(sorted(set(self.terminal_event_hashes))):
            raise ValueError("official control terminal hashes must be sorted and unique")
        if any(_HASH_RE.fullmatch(item) is None for item in self.terminal_event_hashes):
            raise ValueError("official control terminal hash is invalid")
        candidate_ids = [str(item.get("candidate_id", "")) for item in self.candidates]
        if not candidate_ids or len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("official control candidates must be non-empty and unique")
        if self.output_hash != canonical_json_hash(
            {"official_candidate_ids": candidate_ids}
        ):
            raise ValueError("official control output hash mismatch")
        if self.evidence_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("official control evidence hash mismatch")
        normalized_seeds: list[Mapping[str, str | None]] = []
        for seed in self.seeds:
            if set(seed) != {"seed_id", "formula", "source_hash", "parent_seed_id"}:
                raise ValueError("official control seed has invalid fields")
            if _HASH_RE.fullmatch(str(seed["source_hash"])) is None:
                raise ValueError("official control seed source hash is invalid")
            normalized_seeds.append(MappingProxyType(dict(seed)))
        normalized_candidates: list[Mapping[str, str]] = []
        for candidate in self.candidates:
            if set(candidate) != {
                "candidate_id", "parent_seed_id", "formula_hash", "mutation"
            } or _HASH_RE.fullmatch(str(candidate["formula_hash"])) is None:
                raise ValueError("official control candidate has invalid fields")
            normalized_candidates.append(MappingProxyType(dict(candidate)))
        object.__setattr__(self, "seeds", tuple(normalized_seeds))
        object.__setattr__(self, "candidates", tuple(normalized_candidates))

    @classmethod
    def from_search(
        cls,
        search: "AlphaFoundrySearch",
        result: "AlphaFoundrySearchResult",
    ) -> "OfficialSearchControlEvidenceV1":
        if search.lifecycle is None or search.run_id is None:
            raise ValueError("official control evidence requires event-sourced search")
        if type(search.mutator) is not SeedMutator:
            raise ValueError("official control evidence requires the frozen mutator")
        if len(result.attempts) != len(result.candidates):
            raise ValueError("official control evidence requires terminal coverage")
        seeds = tuple(
            {
                "seed_id": seed.seed_id,
                "formula": seed.formula,
                "source_hash": canonical_json_hash({"source": seed.source}),
                "parent_seed_id": seed.parent_seed_id,
            }
            for seed in search.seed_bank.list()
        )
        candidates = tuple(
            {
                "candidate_id": candidate.candidate_id,
                "parent_seed_id": candidate.parent_seed_id,
                "formula_hash": candidate.formula_hash,
                "mutation": str(candidate.metadata["mutation"]),
            }
            for candidate in result.candidates
        )
        terminals = tuple(sorted(attempt.terminal_event_hash for attempt in result.attempts))
        if any(
            attempt.candidate_id != candidate.candidate_id
            or attempt.data_snapshot_hash != search.lifecycle.data_snapshot_hash
            for attempt, candidate in zip(result.attempts, result.candidates, strict=True)
        ):
            raise ValueError("official control attempts differ from generated candidates")
        policy = FlatControlPolicyV1.create(
            max_candidates_per_seed=search.mutator.max_candidates_per_seed,
            max_candidates=search.max_candidates,
            trial_budget=search.trial_budget,
        )
        output_hash = canonical_json_hash(
            {"official_candidate_ids": [item["candidate_id"] for item in candidates]}
        )
        content = {
            "schema_version": "official_search_control_evidence.v1",
            "run_id": search.run_id,
            "data_snapshot_hash": search.lifecycle.data_snapshot_hash,
            "policy": policy.to_dict(),
            "seeds": [dict(item) for item in seeds],
            "candidates": [dict(item) for item in candidates],
            "terminal_event_hashes": list(terminals),
            "output_hash": output_hash,
        }
        return cls(
            schema_version="official_search_control_evidence.v1",
            run_id=search.run_id,
            data_snapshot_hash=search.lifecycle.data_snapshot_hash,
            policy=policy,
            seeds=seeds,
            candidates=candidates,
            terminal_event_hashes=terminals,
            output_hash=output_hash,
            evidence_hash=canonical_json_hash(content),
        )

    def replay(self) -> tuple[str, ...]:
        from src.alpha_foundry.search import AlphaFoundrySearch

        seeds = [
            AlphaSeed(
                seed_id=str(item["seed_id"]),
                formula=str(item["formula"]),
                source="content-addressed:" + str(item["source_hash"]),
                parent_seed_id=(
                    None if item["parent_seed_id"] is None
                    else str(item["parent_seed_id"])
                ),
            )
            for item in self.seeds
        ]
        replay = AlphaFoundrySearch(
            seed_bank=SeedBank(seeds),
            mutator=SeedMutator(
                max_candidates_per_seed=self.policy.max_candidates_per_seed
            ),
            max_candidates=self.policy.max_candidates,
            trial_budget=self.policy.trial_budget,
        ).generate()
        records = tuple(
            {
                "candidate_id": candidate.candidate_id,
                "parent_seed_id": candidate.parent_seed_id,
                "formula_hash": candidate.formula_hash,
                "mutation": str(candidate.metadata["mutation"]),
            }
            for candidate in replay.candidates
        )
        if records != self.candidates:
            raise ValueError("official flat control does not replay from frozen inputs")
        return tuple(str(item["candidate_id"]) for item in records)

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "data_snapshot_hash": self.data_snapshot_hash,
            "policy": self.policy.to_dict(),
            "seeds": [dict(item) for item in self.seeds],
            "candidates": [dict(item) for item in self.candidates],
            "terminal_event_hashes": list(self.terminal_event_hashes),
            "output_hash": self.output_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "evidence_hash": self.evidence_hash}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "OfficialSearchControlEvidenceV1":
        expected = {
            "schema_version", "run_id", "data_snapshot_hash", "policy", "seeds",
            "candidates", "terminal_event_hashes", "output_hash", "evidence_hash",
        }
        if (
            set(raw) != expected
            or not isinstance(raw["policy"], Mapping)
            or not isinstance(raw["seeds"], list)
            or not isinstance(raw["candidates"], list)
            or not isinstance(raw["terminal_event_hashes"], list)
        ):
            raise ValueError("official control evidence has an invalid closed schema")
        seeds: list[Mapping[str, str | None]] = []
        for item in raw["seeds"]:
            if not isinstance(item, Mapping) or set(item) != {
                "seed_id", "formula", "source_hash", "parent_seed_id"
            }:
                raise ValueError("official control seed evidence is invalid")
            seeds.append({
                "seed_id": str(item["seed_id"]),
                "formula": str(item["formula"]),
                "source_hash": str(item["source_hash"]),
                "parent_seed_id": (
                    None if item["parent_seed_id"] is None
                    else str(item["parent_seed_id"])
                ),
            })
        candidates: list[Mapping[str, str]] = []
        for item in raw["candidates"]:
            if not isinstance(item, Mapping) or set(item) != {
                "candidate_id", "parent_seed_id", "formula_hash", "mutation"
            }:
                raise ValueError("official control candidate evidence is invalid")
            candidates.append({str(key): str(value) for key, value in item.items()})
        return cls(
            schema_version=str(raw["schema_version"]),
            run_id=str(raw["run_id"]),
            data_snapshot_hash=str(raw["data_snapshot_hash"]),
            policy=FlatControlPolicyV1.from_dict(raw["policy"]),
            seeds=tuple(seeds),
            candidates=tuple(candidates),
            terminal_event_hashes=tuple(
                str(item) for item in raw["terminal_event_hashes"]
            ),
            output_hash=str(raw["output_hash"]),
            evidence_hash=str(raw["evidence_hash"]),
        )


class OfficialSearchControlArtifactStoreV1:
    media_type = "application/vnd.vibe.official-search-control-v1+json"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def write(self, evidence: OfficialSearchControlEvidenceV1) -> dict[str, str]:
        payload = evidence.to_dict()
        if canonical_json(payload) != canonical_json(redact_secrets(payload)):
            raise ValueError("official control evidence contains unsafe material")
        if len(canonical_json(payload).encode("utf-8")) > _MAX_ARTIFACT_BYTES:
            raise ValueError("official control evidence exceeds byte budget")
        relative = self.relative_path(evidence.evidence_hash)
        target = safe_artifact_path(self.root, relative)
        if target.exists():
            if self.read(relative, evidence.evidence_hash) != evidence:
                raise ValueError("official control evidence collision")
        else:
            safe_artifact_write_json(self.root, relative, payload)
        return {
            "relative_path": relative,
            "artifact_hash": hash_artifact(target),
            "media_type": self.media_type,
        }

    def read(
        self, relative_path: str, expected_evidence_hash: str
    ) -> OfficialSearchControlEvidenceV1:
        target = safe_artifact_path(self.root, relative_path)
        raw = target.read_bytes()
        if len(raw) > _MAX_ARTIFACT_BYTES:
            raise ValueError("official control evidence exceeds byte budget")

        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite official control value: {value}")

        def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate official control key")
                result[key] = value
            return result

        payload = json.loads(
            raw.decode("utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
        if not isinstance(payload, Mapping):
            raise ValueError("official control evidence must be an object")
        if canonical_json(payload) != canonical_json(redact_secrets(payload)):
            raise ValueError("official control evidence contains unsafe material")
        evidence = OfficialSearchControlEvidenceV1.from_dict(payload)
        if evidence.evidence_hash != expected_evidence_hash:
            raise ValueError("official control evidence identity mismatch")
        if self.relative_path(evidence.evidence_hash) != relative_path.replace("\\", "/"):
            raise ValueError("official control evidence path is not content addressed")
        evidence.replay()
        return evidence

    @staticmethod
    def relative_path(evidence_hash: str) -> str:
        if _HASH_RE.fullmatch(evidence_hash) is None:
            raise ValueError("official control evidence hash is invalid")
        digest = evidence_hash.removeprefix("sha256:")
        return f"official-control-v1/{digest[:2]}/{digest}.json"


@dataclass(frozen=True)
class RecordedOfficialSearchControlV1:
    evidence: OfficialSearchControlEvidenceV1
    event: ResearchEventEnvelope


class OfficialSearchControlServiceV1:
    def __init__(self, store: ResearchEventStore) -> None:
        required = ("VIBE_TRADING_ALPHA_FOUNDRY", "VIBE_TRADING_RESEARCH_EVENTS")
        if any(not store.flags.enabled(name) for name in required):
            raise RuntimeError("official control evidence capability is disabled")
        self.store = store
        self.artifacts = OfficialSearchControlArtifactStoreV1(store.artifact_root)

    def record(
        self,
        search: "AlphaFoundrySearch",
        result: "AlphaFoundrySearchResult",
    ) -> RecordedOfficialSearchControlV1:
        evidence = OfficialSearchControlEvidenceV1.from_search(search, result)
        reference = self.artifacts.write(evidence)
        identifier = (
            "official-control-v1-"
            + evidence.evidence_hash.removeprefix("sha256:")[:24]
        )
        event = self.store.append_event(
            EventDraft(
                event_type="OfficialSearchControlRecorded",
                entity_id=identifier,
                run_id=evidence.run_id,
                payload_schema_version="official_search_control_recorded.v1",
                idempotency_key="official-search-control-v1:" + evidence.evidence_hash,
                payload={
                    "control_id": identifier,
                    "evidence_hash": evidence.evidence_hash,
                    "policy_hash": evidence.policy.policy_hash,
                    "output_hash": evidence.output_hash,
                    "search_run_id": evidence.run_id,
                    "data_snapshot_hash": evidence.data_snapshot_hash,
                    "candidate_count": len(evidence.candidates),
                    "terminal_event_hashes": list(evidence.terminal_event_hashes),
                    "artifact_refs": [reference],
                },
            )
        )
        return RecordedOfficialSearchControlV1(evidence=evidence, event=event)


__all__ = [
    "FlatControlPolicyV1", "OfficialSearchControlArtifactStoreV1",
    "OfficialSearchControlEvidenceV1", "OfficialSearchControlServiceV1",
    "RecordedOfficialSearchControlV1",
]
