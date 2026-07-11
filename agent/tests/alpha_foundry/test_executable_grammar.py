from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

import src.alpha_foundry.dsl.executable as executable_module
from src.alpha_foundry.dsl.executable import (
    DEFAULT_EXECUTABLE_GRAMMAR,
    ExecutableGrammarSnapshotV1,
    UnsupportedBackendOperatorError,
)


def _panel() -> dict[str, pd.DataFrame]:
    index = pd.date_range("2020-01-01", periods=8, freq="D")
    columns = ["A", "B", "C"]
    close = pd.DataFrame(
        np.arange(24, dtype=float).reshape(8, 3) + 1.0,
        index=index,
        columns=columns,
    )
    return {"close": close, "volume": close * 100.0}


def test_runtime_snapshot_is_the_exact_grammar_backend_intersection() -> None:
    snapshot = DEFAULT_EXECUTABLE_GRAMMAR

    assert snapshot.backend_version == "core_dataframe_backend.v1"
    assert snapshot.unsupported_source_operators == (
        "group_neutralize",
        "signed_power",
        "ts_corr",
        "ts_cov",
        "ts_rank",
        "ts_std",
        "vwap_deviation",
    )
    assert not (
        set(snapshot.unsupported_source_operators)
        & set(snapshot.executable_grammar.operators)
    )
    assert snapshot.to_dict()["snapshot_hash"] == snapshot.snapshot_hash
    assert ExecutableGrammarSnapshotV1.from_runtime().to_dict() == snapshot.to_dict()


def test_public_replacement_cannot_forge_runtime_executable_snapshot() -> None:
    with pytest.raises(ValueError, match="runtime registry snapshot"):
        replace(DEFAULT_EXECUTABLE_GRAMMAR, backend_version="caller-backend.v9")


@pytest.mark.parametrize(
    "formula",
    [
        "rank(close)",
        "zscore(close)",
        "winsorize(close)",
        "clip(close,-1,1)",
        "ts_mean(close,2)",
        "delay(close,1)",
        "delta(close,1)",
        "decay_linear(close,2)",
        "add(close,1)",
        "sub(close,1)",
        "mul(close,2)",
        "div_safe(close,2)",
        "neg(close)",
        "log1p_abs(close)",
        "volume_shock(volume,2)",
        "illiquidity_proxy(close)",
        "ts_avg(close,2)",
    ],
)
def test_every_advertised_backend_operation_executes(formula: str) -> None:
    result = DEFAULT_EXECUTABLE_GRAMMAR.evaluate(formula, _panel())
    assert isinstance(result, pd.DataFrame)
    assert result.shape == (8, 3)


def test_unsupported_operator_is_typed_skip_before_backend_compute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_compute(*args: object, **kwargs: object) -> object:
        raise AssertionError("backend compute must not run")

    monkeypatch.setattr(executable_module, "evaluate_ast", forbidden_compute)

    with pytest.raises(UnsupportedBackendOperatorError) as captured:
        DEFAULT_EXECUTABLE_GRAMMAR.evaluate("ts_std(close,5)", _panel())

    assert captured.value.error_codes == ("BACKEND_OPERATOR_UNAVAILABLE",)
    assert captured.value.operators == ("ts_std",)


def test_unknown_operator_remains_formula_invalid_not_backend_unavailable() -> None:
    with pytest.raises(ValueError) as captured:
        DEFAULT_EXECUTABLE_GRAMMAR.validate_formula("caller_operator(close)")
    assert not isinstance(captured.value, UnsupportedBackendOperatorError)
