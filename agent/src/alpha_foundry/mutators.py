from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from src.alpha_foundry.candidate_pool import CandidateExpression, make_candidate
from src.alpha_foundry.seed_bank import AlphaSeed
from src.research_ledger.hash_utils import canonical_json_hash


TemplateKind = Literal["identity", "wrap", "window_call"]


@dataclass(frozen=True)
class SeedMutationTemplateV1:
    template_id: str
    kind: TemplateKind
    operator: str | None
    window: int | None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.template_id, str)
            or not isinstance(self.kind, str)
            or (self.operator is not None and not isinstance(self.operator, str))
            or isinstance(self.window, bool)
            or (self.window is not None and not isinstance(self.window, int))
        ):
            raise ValueError("seed mutation template fields have invalid types")
        allowed = {
            ("identity", "identity", None, None),
            ("rank_wrap", "wrap", "rank", None),
            ("decay_3", "window_call", "decay_linear", 3),
            ("delay_1", "window_call", "delay", 1),
            ("zscore_wrap", "wrap", "zscore", None),
        }
        if (self.template_id, self.kind, self.operator, self.window) not in allowed:
            raise ValueError("unsupported seed mutation template")

    def render(self, parent_formula: str) -> str:
        if self.kind == "identity":
            return parent_formula
        if self.kind == "wrap":
            assert self.operator is not None
            return f"{self.operator}({parent_formula})"
        assert self.operator is not None and self.window is not None
        return f"{self.operator}({parent_formula}, {self.window})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "kind": self.kind,
            "operator": self.operator,
            "window": self.window,
        }


@dataclass(frozen=True)
class SeedMutationTemplateRegistryV1:
    schema_version: str
    templates: tuple[SeedMutationTemplateV1, ...]
    registry_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "seed_mutation_template_registry.v1":
            raise ValueError("unsupported seed mutation template registry")
        expected = _seed_mutation_templates_v1()
        if self.templates != expected:
            raise ValueError("seed mutation registry differs from the frozen v1 catalog")
        if self.registry_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("seed mutation template registry hash mismatch")

    @classmethod
    def create(cls) -> "SeedMutationTemplateRegistryV1":
        templates = _seed_mutation_templates_v1()
        content = {
            "schema_version": "seed_mutation_template_registry.v1",
            "templates": [template.to_dict() for template in templates],
        }
        return cls(
            schema_version="seed_mutation_template_registry.v1",
            templates=templates,
            registry_hash=canonical_json_hash(content),
        )

    def get(self, template_id: str) -> SeedMutationTemplateV1:
        matches = [item for item in self.templates if item.template_id == template_id]
        if len(matches) != 1:
            raise ValueError("unknown seed mutation template")
        return matches[0]

    @property
    def template_ids(self) -> tuple[str, ...]:
        return tuple(item.template_id for item in self.templates)

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "templates": [template.to_dict() for template in self.templates],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "registry_hash": self.registry_hash}


def _seed_mutation_templates_v1() -> tuple[SeedMutationTemplateV1, ...]:
    return (
        SeedMutationTemplateV1("identity", "identity", None, None),
        SeedMutationTemplateV1("rank_wrap", "wrap", "rank", None),
        SeedMutationTemplateV1("decay_3", "window_call", "decay_linear", 3),
        SeedMutationTemplateV1("delay_1", "window_call", "delay", 1),
        SeedMutationTemplateV1("zscore_wrap", "wrap", "zscore", None),
    )


SEED_MUTATION_TEMPLATE_REGISTRY_V1 = SeedMutationTemplateRegistryV1.create()


class SeedMutator:
    def __init__(self, *, max_candidates_per_seed: int = 8) -> None:
        self.max_candidates_per_seed = max(1, max_candidates_per_seed)

    def mutate(self, seed: AlphaSeed) -> list[CandidateExpression]:
        candidates: list[CandidateExpression] = []
        seen: set[str] = set()
        for template in SEED_MUTATION_TEMPLATE_REGISTRY_V1.templates:
            formula = template.render(seed.formula)
            if len(formula) > 512:
                continue
            candidate = make_candidate(
                seed.seed_id,
                formula,
                mutation=template.template_id,
            )
            if candidate.formula_hash in seen:
                continue
            seen.add(candidate.formula_hash)
            candidates.append(candidate)
            if len(candidates) >= self.max_candidates_per_seed:
                break
        return candidates


__all__ = [
    "SEED_MUTATION_TEMPLATE_REGISTRY_V1",
    "SeedMutationTemplateRegistryV1",
    "SeedMutationTemplateV1",
    "SeedMutator",
]
