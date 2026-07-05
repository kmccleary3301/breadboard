from __future__ import annotations

from pathlib import Path

import pytest

from breadboard.rl.env_package.validate import load_env_package
from breadboard.rl.session import SessionStatus, create_local_session


REPO_ROOT = Path(__file__).resolve().parents[3]
PYTHON_TOY = REPO_ROOT / "examples" / "rl_env_packages" / "python_console_toy" / "env_package.yaml"


def test_evaluate_before_reset_is_rejected() -> None:
    session = create_local_session(load_env_package(PYTHON_TOY), "py_toy_001")

    with pytest.raises(ValueError, match="evaluate requires ready/running"):
        session.evaluate()


def test_step_after_terminate_is_rejected() -> None:
    session = create_local_session(load_env_package(PYTHON_TOY), "py_toy_001")
    session.terminate()

    with pytest.raises(ValueError, match="step requires ready/running"):
        session.step({"tool": "submit_answer", "answer": "42"})
    assert session.lifecycle.status == SessionStatus.TERMINATED


def test_invalid_second_reset_after_evaluation_is_rejected() -> None:
    session = create_local_session(load_env_package(PYTHON_TOY), "py_toy_001")
    session.reset()
    session.step({"tool": "submit_answer", "answer": "42"})
    session.evaluate()

    with pytest.raises(ValueError, match="invalid session transition"):
        session.reset()
