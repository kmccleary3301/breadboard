from __future__ import annotations

from pathlib import Path

from breadboard.rl.env_package.validate import load_env_package
from breadboard.rl.session import SessionStatus, create_local_session


REPO_ROOT = Path(__file__).resolve().parents[3]
PYTHON_TOY = REPO_ROOT / "examples" / "rl_env_packages" / "python_console_toy" / "env_package.yaml"


def test_step_timeout_produces_structured_failure_and_failed_session() -> None:
    session = create_local_session(load_env_package(PYTHON_TOY), "py_toy_001")
    session.reset()

    result = session.step({"tool": "sleep", "seconds": 999})

    assert result.success is False
    assert result.error["kind"] == "timeout"
    assert session.lifecycle.status == SessionStatus.FAILED
    assert session.export_admission()["trainable"] is False
    assert "lifecycle_status=failed" in session.export_admission()["blocked_reasons"]


def test_runtime_crash_produces_structured_failure() -> None:
    session = create_local_session(load_env_package(PYTHON_TOY), "py_toy_001")
    session.reset()

    result = session.step({"tool": "runtime_crash"})

    assert result.success is False
    assert result.error["kind"] == "runtime_crash"
    assert session.events[-1].error["kind"] == "runtime_crash"
