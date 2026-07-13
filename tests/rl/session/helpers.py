from __future__ import annotations

from pathlib import Path

from breadboard.rl.env_package.validate import load_env_package
from breadboard.rl.session.controller import LocalSession, create_local_session


REPO_ROOT = Path(__file__).resolve().parents[3]
PYTHON_TOY = REPO_ROOT / "examples" / "rl_env_packages" / "python_console_toy" / "env_package.yaml"


def build_successful_toy_session() -> LocalSession:
    session = create_local_session(load_env_package(PYTHON_TOY), "py_toy_001")
    session.reset()
    session.observe()
    session.step({"tool": "submit_answer", "answer": "42"})
    session.evaluate()
    return session
