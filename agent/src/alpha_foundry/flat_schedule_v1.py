"""Outcome-free official flat candidate schedule frozen before paired arms."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from src.alpha_foundry.artifacts import safe_artifact_path, safe_artifact_write_json
from src.alpha_foundry.control_evidence import FlatControlPolicyV1
from src.alpha_foundry.mutators import SeedMutator
from src.alpha_foundry.search import AlphaFoundrySearch
from src.alpha_foundry.seed_bank import AlphaSeed, SeedBank
from src.research_ledger.events import EventDraft, ResearchEventEnvelope, ResearchEventStore
from src.research_ledger.events.artifacts import hash_artifact
from src.research_ledger.hash_utils import canonical_json, canonical_json_hash, redact_secrets


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class PreArmFlatScheduleV1:
    schema_version: str
    plan_hash: str
    pair_id: str
    run_group_id: str
    data_snapshot_hash: str
    policy: FlatControlPolicyV1
    seeds: tuple[Mapping[str, str | None], ...]
    candidates: tuple[Mapping[str, str], ...]
    output_hash: str
    schedule_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "prearm_flat_schedule.v1":
            raise ValueError("unsupported pre-arm flat schedule")
        if any(
            _HASH_RE.fullmatch(value) is None
            for value in (self.plan_hash, self.data_snapshot_hash, self.output_hash,
                          self.schedule_hash)
        ):
            raise ValueError("pre-arm flat schedule identity is invalid")
        if not self.run_group_id or not self.pair_id.startswith(self.run_group_id + ":"):
            raise ValueError("pre-arm flat schedule pair identity is invalid")
        normalized_seeds: list[Mapping[str, str | None]] = []
        for seed in self.seeds:
            if set(seed) != {"seed_id", "formula", "source_hash", "parent_seed_id"}:
                raise ValueError("pre-arm flat schedule seed is invalid")
            if _HASH_RE.fullmatch(str(seed["source_hash"])) is None:
                raise ValueError("pre-arm flat schedule seed source is invalid")
            normalized_seeds.append(MappingProxyType(dict(seed)))
        normalized_candidates: list[Mapping[str, str]] = []
        for candidate in self.candidates:
            if set(candidate) != {
                "candidate_id", "parent_seed_id", "formula_hash", "mutation"
            } or _HASH_RE.fullmatch(str(candidate["formula_hash"])) is None:
                raise ValueError("pre-arm flat schedule candidate is invalid")
            normalized_candidates.append(MappingProxyType(dict(candidate)))
        candidate_ids = [str(item["candidate_id"]) for item in normalized_candidates]
        if (
            not candidate_ids
            or len(candidate_ids) != len(set(candidate_ids))
            or len(candidate_ids) != self.policy.max_candidates
            or self.output_hash != canonical_json_hash(
                {"official_candidate_ids": candidate_ids}
            )
        ):
            raise ValueError("pre-arm flat schedule output is incomplete")
        object.__setattr__(self, "seeds", tuple(normalized_seeds))
        object.__setattr__(self, "candidates", tuple(normalized_candidates))
        if self.schedule_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("pre-arm flat schedule hash mismatch")

    @classmethod
    def create(
        cls,
        *,
        plan_hash: str,
        pair_id: str,
        run_group_id: str,
        data_snapshot_hash: str,
        seed_bank: SeedBank,
        mutator: SeedMutator,
        max_candidates: int,
        trial_budget: int,
    ) -> "PreArmFlatScheduleV1":
        if type(mutator) is not SeedMutator:
            raise ValueError("pre-arm flat schedule requires the frozen flat mutator")
        policy = FlatControlPolicyV1.create(
            max_candidates_per_seed=mutator.max_candidates_per_seed,
            max_candidates=max_candidates,
            trial_budget=trial_budget,
        )
        seeds = tuple(
            {
                "seed_id": seed.seed_id,
                "formula": seed.formula,
                "source_hash": canonical_json_hash({"source": seed.source}),
                "parent_seed_id": seed.parent_seed_id,
            }
            for seed in seed_bank.list()
        )
        replay = AlphaFoundrySearch(
            seed_bank=SeedBank(seed_bank.list()),
            mutator=SeedMutator(
                max_candidates_per_seed=mutator.max_candidates_per_seed
            ),
            max_candidates=max_candidates,
            trial_budget=trial_budget,
        ).generate()
        candidates = tuple(
            {
                "candidate_id": candidate.candidate_id,
                "parent_seed_id": candidate.parent_seed_id,
                "formula_hash": candidate.formula_hash,
                "mutation": str(candidate.metadata["mutation"]),
            }
            for candidate in replay.candidates
        )
        output_hash = canonical_json_hash(
            {"official_candidate_ids": [item["candidate_id"] for item in candidates]}
        )
        content = {
            "schema_version": "prearm_flat_schedule.v1",
            "plan_hash": plan_hash,
            "pair_id": pair_id,
            "run_group_id": run_group_id,
            "data_snapshot_hash": data_snapshot_hash,
            "policy": policy.to_dict(),
            "seeds": [dict(item) for item in seeds],
            "candidates": [dict(item) for item in candidates],
            "output_hash": output_hash,
        }
        return cls(
            schema_version="prearm_flat_schedule.v1",
            plan_hash=plan_hash,
            pair_id=pair_id,
            run_group_id=run_group_id,
            data_snapshot_hash=data_snapshot_hash,
            policy=policy,
            seeds=seeds,
            candidates=candidates,
            output_hash=output_hash,
            schedule_hash=canonical_json_hash(content),
        )

    def replay(self) -> tuple[str, ...]:
        seed_bank = SeedBank(
            [
                AlphaSeed(
                    seed_id=str(seed["seed_id"]),
                    formula=str(seed["formula"]),
                    source="content-addressed:" + str(seed["source_hash"]),
                    parent_seed_id=(
                        None if seed["parent_seed_id"] is None
                        else str(seed["parent_seed_id"])
                    ),
                )
                for seed in self.seeds
            ]
        )
        rebuilt = PreArmFlatScheduleV1.create(
            plan_hash=self.plan_hash,
            pair_id=self.pair_id,
            run_group_id=self.run_group_id,
            data_snapshot_hash=self.data_snapshot_hash,
            seed_bank=seed_bank,
            mutator=SeedMutator(
                max_candidates_per_seed=self.policy.max_candidates_per_seed
            ),
            max_candidates=self.policy.max_candidates,
            trial_budget=self.policy.trial_budget,
        )
        if rebuilt.candidates != self.candidates or rebuilt.output_hash != self.output_hash:
            raise ValueError("pre-arm flat schedule does not replay")
        return tuple(str(item["candidate_id"]) for item in self.candidates)

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_hash": self.plan_hash,
            "pair_id": self.pair_id,
            "run_group_id": self.run_group_id,
            "data_snapshot_hash": self.data_snapshot_hash,
            "policy": self.policy.to_dict(),
            "seeds": [dict(item) for item in self.seeds],
            "candidates": [dict(item) for item in self.candidates],
            "output_hash": self.output_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "schedule_hash": self.schedule_hash}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PreArmFlatScheduleV1":
        expected = {
            "schema_version", "plan_hash", "pair_id", "run_group_id",
            "data_snapshot_hash", "policy", "seeds", "candidates",
            "output_hash", "schedule_hash",
        }
        if (
            set(raw) != expected
            or not isinstance(raw["policy"], Mapping)
            or not isinstance(raw["seeds"], list)
            or not isinstance(raw["candidates"], list)
            or any(not isinstance(item, Mapping) for item in raw["seeds"])
            or any(not isinstance(item, Mapping) for item in raw["candidates"])
        ):
            raise ValueError("pre-arm flat schedule has an invalid schema")
        return cls(
            schema_version=str(raw["schema_version"]),
            plan_hash=str(raw["plan_hash"]),
            pair_id=str(raw["pair_id"]),
            run_group_id=str(raw["run_group_id"]),
            data_snapshot_hash=str(raw["data_snapshot_hash"]),
            policy=FlatControlPolicyV1.from_dict(raw["policy"]),
            seeds=tuple(dict(item) for item in raw["seeds"] if isinstance(item, Mapping)),
            candidates=tuple(
                {str(key): str(value) for key, value in item.items()}
                for item in raw["candidates"] if isinstance(item, Mapping)
            ),
            output_hash=str(raw["output_hash"]),
            schedule_hash=str(raw["schedule_hash"]),
        )


class PreArmFlatScheduleArtifactStoreV1:
    media_type = "application/vnd.vibe.prearm-flat-schedule-v1+json"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def write(self, schedule: PreArmFlatScheduleV1) -> dict[str, str]:
        payload = schedule.to_dict()
        if canonical_json(payload) != canonical_json(redact_secrets(payload)):
            raise ValueError("pre-arm flat schedule contains unsafe material")
        if len(canonical_json(payload).encode("utf-8")) > _MAX_BYTES:
            raise ValueError("pre-arm flat schedule exceeds byte budget")
        relative = self.relative_path(schedule.schedule_hash)
        target = safe_artifact_path(self.root, relative)
        if target.exists():
            if self.read(relative, schedule.schedule_hash) != schedule:
                raise ValueError("pre-arm flat schedule collision")
        else:
            safe_artifact_write_json(self.root, relative, payload)
        return {
            "relative_path": relative,
            "artifact_hash": hash_artifact(target),
            "media_type": self.media_type,
        }

    def read(self, relative_path: str, expected_hash: str) -> PreArmFlatScheduleV1:
        target = safe_artifact_path(self.root, relative_path)
        raw = target.read_bytes()
        if len(raw) > _MAX_BYTES:
            raise ValueError("pre-arm flat schedule exceeds byte budget")
        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite pre-arm schedule value: {value}")
        def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate pre-arm schedule key")
                result[key] = value
            return result
        payload = json.loads(
            raw.decode("utf-8"), parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
        if not isinstance(payload, Mapping):
            raise ValueError("pre-arm flat schedule must be an object")
        if canonical_json(payload) != canonical_json(redact_secrets(payload)):
            raise ValueError("pre-arm flat schedule contains unsafe material")
        schedule = PreArmFlatScheduleV1.from_dict(payload)
        if schedule.schedule_hash != expected_hash:
            raise ValueError("pre-arm flat schedule identity differs")
        if self.relative_path(expected_hash) != relative_path.replace("\\", "/"):
            raise ValueError("pre-arm flat schedule path is not content addressed")
        schedule.replay()
        return schedule

    @staticmethod
    def relative_path(schedule_hash: str) -> str:
        if _HASH_RE.fullmatch(schedule_hash) is None:
            raise ValueError("pre-arm flat schedule hash is invalid")
        digest = schedule_hash.removeprefix("sha256:")
        return f"prearm-flat-schedule-v1/{digest[:2]}/{digest}.json"


@dataclass(frozen=True)
class RecordedPreArmFlatScheduleV1:
    schedule: PreArmFlatScheduleV1
    event: ResearchEventEnvelope


class PreArmFlatScheduleServiceV1:
    def __init__(self, store: ResearchEventStore) -> None:
        self.store = store
        self.artifacts = PreArmFlatScheduleArtifactStoreV1(store.artifact_root)

    def freeze(self, **kwargs: Any) -> RecordedPreArmFlatScheduleV1:
        schedule = PreArmFlatScheduleV1.create(**kwargs)
        reference = self.artifacts.write(schedule)
        identifier = "prearm-flat-v1-" + schedule.schedule_hash.removeprefix("sha256:")[:24]
        event = self.store.append_event(
            EventDraft(
                event_type="PreArmFlatScheduleFrozen",
                entity_id=identifier,
                run_id=schedule.run_group_id,
                payload_schema_version="prearm_flat_schedule_frozen.v1",
                idempotency_key="prearm-flat-v1:" + schedule.schedule_hash,
                payload={
                    "schedule_id": identifier,
                    "schedule_hash": schedule.schedule_hash,
                    "plan_hash": schedule.plan_hash,
                    "pair_id": schedule.pair_id,
                    "run_group_id": schedule.run_group_id,
                    "data_snapshot_hash": schedule.data_snapshot_hash,
                    "policy_hash": schedule.policy.policy_hash,
                    "candidate_count": len(schedule.candidates),
                    "output_hash": schedule.output_hash,
                    "artifact_refs": [reference],
                },
            )
        )
        return RecordedPreArmFlatScheduleV1(schedule, event)


__all__ = [
    "PreArmFlatScheduleArtifactStoreV1", "PreArmFlatScheduleServiceV1",
    "PreArmFlatScheduleV1", "RecordedPreArmFlatScheduleV1",
]
