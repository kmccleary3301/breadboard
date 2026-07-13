from __future__ import annotations

from pathlib import Path

from breadboard.rl.env_package.validate import load_env_package
from breadboard.rl.runtime.local_process import LocalProcessToyRuntime


REPO_ROOT = Path(__file__).resolve().parents[3]
PYTHON_TOY = REPO_ROOT / "examples" / "rl_env_packages" / "python_console_toy" / "env_package.yaml"


def test_local_process_runtime_exposes_backend_contract_methods() -> None:
    runtime = LocalProcessToyRuntime(load_env_package(PYTHON_TOY), "py_toy_001")

    for method_name in [
        "health",
        "reset",
        "observe",
        "step",
        "evaluate",
        "snapshot",
        "restore",
        "terminate",
    ]:
        assert callable(getattr(runtime, method_name))


def test_evaluate_without_submission_is_structured_error() -> None:
    runtime = LocalProcessToyRuntime(load_env_package(PYTHON_TOY), "py_toy_001")
    runtime.reset()

    result = runtime.evaluate()

    assert result.success is False
    assert result.error["kind"] == "no_submission"
