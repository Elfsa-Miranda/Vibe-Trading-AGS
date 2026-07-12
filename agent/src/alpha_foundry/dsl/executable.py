"""Frozen intersection between the safe DSL grammar and an executable backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from src.alpha_foundry.dsl.grammar import DEFAULT_GRAMMAR, GrammarDefinition
from src.alpha_foundry.dsl.model import ASTNode
from src.alpha_foundry.dsl.operators import (
    CORE_V1_BACKEND_VERSION,
    CORE_V1_IMPLEMENTED_OPERATORS,
    evaluate_ast,
)
from src.alpha_foundry.dsl.parser import FormulaParseError, FormulaParser
from src.alpha_foundry.dsl.validator import validate_expression
from src.research_ledger.hash_utils import canonical_json_hash


class ExecutableFormulaError(ValueError):
    def __init__(self, error_codes: tuple[str, ...] | list[str]) -> None:
        self.error_codes = tuple(sorted(set(error_codes)))
        super().__init__(", ".join(self.error_codes))


class UnsupportedBackendOperatorError(ExecutableFormulaError):
    def __init__(self, operators: tuple[str, ...]) -> None:
        self.operators = tuple(sorted(set(operators)))
        super().__init__(["BACKEND_OPERATOR_UNAVAILABLE"])


@dataclass(frozen=True)
class ExecutableGrammarSnapshotV1:
    source_grammar_version: str
    source_grammar_hash: str
    backend_version: str
    backend_registry_hash: str
    executable_grammar: GrammarDefinition
    unsupported_source_operators: tuple[str, ...]
    schema_version: str = "executable_grammar_snapshot.v1"

    def __post_init__(self) -> None:
        if self.schema_version != "executable_grammar_snapshot.v1":
            raise ValueError("unsupported executable grammar snapshot schema")
        expected = self.from_runtime(_validate=False)
        if self.to_dict() != expected.to_dict():
            raise ValueError("executable grammar snapshot is not the runtime registry snapshot")

    @classmethod
    def from_runtime(
        cls,
        *,
        _validate: bool = True,
    ) -> "ExecutableGrammarSnapshotV1":
        grammar: GrammarDefinition = DEFAULT_GRAMMAR
        executable_names = frozenset(grammar.operators) & CORE_V1_IMPLEMENTED_OPERATORS
        backend_content = {
            "backend_version": CORE_V1_BACKEND_VERSION,
            "operators": sorted(CORE_V1_IMPLEMENTED_OPERATORS),
        }
        executable = GrammarDefinition.create(
            semantic_version=f"{grammar.semantic_version}+core-v1-executable",
            operators={name: grammar.operators[name] for name in sorted(executable_names)},
            allowed_fields=grammar.allowed_fields,
            operator_aliases={
                alias: target
                for alias, target in grammar.operator_aliases.items()
                if target in executable_names
            },
            field_aliases=grammar.field_aliases,
            max_formula_chars=grammar.max_formula_chars,
            max_ast_depth=grammar.max_ast_depth,
            max_ast_nodes=grammar.max_ast_nodes,
            max_window=grammar.max_window,
        )
        instance = object.__new__(cls)
        object.__setattr__(instance, "source_grammar_version", grammar.semantic_version)
        object.__setattr__(instance, "source_grammar_hash", grammar.content_hash)
        object.__setattr__(instance, "backend_version", CORE_V1_BACKEND_VERSION)
        object.__setattr__(
            instance,
            "backend_registry_hash",
            canonical_json_hash(backend_content),
        )
        object.__setattr__(instance, "executable_grammar", executable)
        object.__setattr__(
            instance,
            "unsupported_source_operators",
            tuple(sorted(set(grammar.operators) - executable_names)),
        )
        object.__setattr__(instance, "schema_version", "executable_grammar_snapshot.v1")
        if _validate:
            instance.__post_init__()
        return instance

    @property
    def snapshot_hash(self) -> str:
        return canonical_json_hash(self._content_dict())

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_grammar_version": self.source_grammar_version,
            "source_grammar_hash": self.source_grammar_hash,
            "backend_version": self.backend_version,
            "backend_registry_hash": self.backend_registry_hash,
            "executable_grammar": self.executable_grammar.to_dict(),
            "executable_grammar_hash": self.executable_grammar.content_hash,
            "unsupported_source_operators": list(self.unsupported_source_operators),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "snapshot_hash": self.snapshot_hash}

    def validate_formula(self, formula: str) -> ASTNode:
        try:
            source_ast = FormulaParser(DEFAULT_GRAMMAR).parse(formula)
        except FormulaParseError as exc:
            raise ExecutableFormulaError([exc.error_code]) from exc
        except ValueError as exc:
            raise ExecutableFormulaError(["INVALID_SYNTAX"]) from exc
        source_validation = validate_expression(source_ast, grammar=DEFAULT_GRAMMAR)
        if not source_validation.ok:
            raise ExecutableFormulaError(list(source_validation.errors))
        unsupported = tuple(
            sorted(source_ast.operators() - set(self.executable_grammar.operators))
        )
        if unsupported:
            raise UnsupportedBackendOperatorError(unsupported)
        executable_validation = validate_expression(
            source_ast,
            grammar=self.executable_grammar,
        )
        if not executable_validation.ok:
            raise ExecutableFormulaError(list(executable_validation.errors))
        return source_ast

    def evaluate(self, formula: str, panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
        ast = self.validate_formula(formula)
        result = evaluate_ast(ast, panel)
        if not isinstance(result, pd.DataFrame):
            raise TypeError("executable factor formula must produce a DataFrame")
        return result


DEFAULT_EXECUTABLE_GRAMMAR = ExecutableGrammarSnapshotV1.from_runtime(_validate=False)
DEFAULT_EXECUTABLE_GRAMMAR.__post_init__()


__all__ = [
    "DEFAULT_EXECUTABLE_GRAMMAR",
    "ExecutableFormulaError",
    "ExecutableGrammarSnapshotV1",
    "UnsupportedBackendOperatorError",
]
