from __future__ import annotations

from pathlib import Path

from breadboard.rl.env_package.validate import load_env_package
from breadboard.rl.session import SessionStatus, create_local_session


REPO_ROOT = Path(__file__).resolve().parents[3]
PYTHON_TOY = REPO_ROOT / "examples" / "rl_env_packages" / "python_console_toy" / "env_package.yaml"


def test_termination_cleanup_runs_after_error() -> None:
    session = create_local_session(load_env_package(PYTHON_TOY), "py_toy_001")
    session.reset()
    session.step({"tool": "runtime_crash"})

    result = session.terminate()

    assert result.success is True
    assert session.lifecycle.status == SessionStatus.TERMINATED
    assert session.events[-1].event_kind == "terminate"
    assert session.events[-1].status_before == SessionStatus.FAILED


def test_terminate_is_idempotent() -> None:
    session = create_local_session(load_env_package(PYTHON_TOY), "py_toy_001")
    first = session.terminate()
    second = session.terminate()

    assert first.success is True
    assert second.success is True
    assert second.evidence["already_terminated"] is True
