from __future__ import annotations

from pathlib import Path

from breadboard.rl.env_package.validate import load_env_package
from breadboard.rl.runtime.local_process import LocalProcessToyRuntime


REPO_ROOT = Path(__file__).resolve().parents[3]
PYTHON_TOY = REPO_ROOT / "examples" / "rl_env_packages" / "python_console_toy" / "env_package.yaml"
SWE_TOY = REPO_ROOT / "examples" / "rl_env_packages" / "swe_toy_patch" / "env_package.yaml"


def test_local_process_runtime_health_ready_for_python_toy() -> None:
    runtime = LocalProcessToyRuntime(load_env_package(PYTHON_TOY), "py_toy_001")

    assert runtime.health().ready is True


def test_local_process_runtime_health_rejects_docker_swe_package() -> None:
    runtime = LocalProcessToyRuntime(load_env_package(SWE_TOY), "swe_toy_001")

    health = runtime.health()
    assert health.ready is False
    assert "runtime.backend must be local_process" in health.reasons
    assert "local toy runtime requires verifier.kind=exact_match" in health.reasons
