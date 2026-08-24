"""Run the standard-library-only governance test slice without pytest."""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import sys
import tempfile
import traceback
from pathlib import Path
from types import ModuleType
from typing import Callable


def _load_module(path: Path, index: int) -> ModuleType:
    name = f"_governance_test_{index}_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"UNLOADABLE_TEST_MODULE:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _invoke(test: Callable[..., object]) -> None:
    if inspect.iscoroutinefunction(test) or inspect.isasyncgenfunction(test):
        raise TypeError(f"UNSUPPORTED_ASYNC_TEST:{test.__name__}")
    if inspect.isgeneratorfunction(test):
        raise TypeError(f"UNSUPPORTED_GENERATOR_TEST:{test.__name__}")
    parameters = tuple(inspect.signature(test).parameters)
    if not parameters:
        result = test()
    elif parameters == ("tmp_path",):
        with tempfile.TemporaryDirectory(prefix="ags-governance-") as directory:
            result = test(Path(directory))
    else:
        raise TypeError(f"UNSUPPORTED_TEST_SIGNATURE:{test.__name__}:{','.join(parameters)}")
    if inspect.isawaitable(result):
        if inspect.iscoroutine(result):
            result.close()
        raise TypeError(f"UNSUPPORTED_AWAITABLE_RESULT:{test.__name__}")
    if inspect.isgenerator(result) or inspect.isasyncgen(result):
        result.close()
        raise TypeError(f"UNSUPPORTED_GENERATOR_RESULT:{test.__name__}")


def run(tests_root: Path) -> tuple[int, int]:
    passed = 0
    failed = 0
    for index, path in enumerate(sorted(tests_root.glob("test_*.py"))):
        try:
            module = _load_module(path, index)
        except (KeyboardInterrupt, GeneratorExit):
            raise
        except BaseException:
            failed += 1
            print(f"FAIL {path.name}::<module import>")
            traceback.print_exc()
            continue
        tests = [
            value
            for name, value in inspect.getmembers(module, inspect.isfunction)
            if name.startswith("test_") and value.__module__ == module.__name__
        ]
        for test in tests:
            label = f"{path.name}::{test.__name__}"
            try:
                _invoke(test)
            except (KeyboardInterrupt, GeneratorExit):
                raise
            except BaseException:  # SystemExit from a test is a test failure, never a runner success
                failed += 1
                print(f"FAIL {label}")
                traceback.print_exc()
            else:
                passed += 1
                print(f"PASS {label}")
    print(f"governance tests: {passed} passed, {failed} failed")
    return passed, failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--tests-root", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    tests_root = (args.tests_root or root / "agent" / "tests" / "governance").resolve()
    sys.path.insert(0, str(root))
    try:
        passed, failed = run(tests_root)
    except (OSError, RuntimeError) as exc:
        print(f"RUNNER_ERROR:{exc}", file=sys.stderr)
        return 2
    return 0 if passed and failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
