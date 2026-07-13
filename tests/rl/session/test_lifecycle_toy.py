from __future__ import annotations

from pathlib import Path

from breadboard.rl.env_package.validate import load_env_package
from breadboard.rl.session import SessionStatus, create_local_session


REPO_ROOT = Path(__file__).resolve().parents[3]
PYTHON_TOY = REPO_ROOT / "examples" / "rl_env_packages" / "python_console_toy" / "env_package.yaml"


def test_full_toy_lifecycle_success_path() -> None:
    package = load_env_package(PYTHON_TOY)
    session = create_local_session(package, "py_toy_001")

    assert session.health().ready is True
    reset = session.reset()
    assert reset.success is True
    assert session.lifecycle.status == SessionStatus.READY

    observed = session.observe()
    assert observed.success is True
    assert observed.observation["task_id"] == "py_toy_001"

    step = session.step({"tool": "submit_answer", "answer": "42"})
    assert step.success is True
    assert step.done is True
    assert session.lifecycle.status == SessionStatus.RUNNING

    snapshot = session.snapshot()
    assert snapshot.state["submitted_answer"] == "42"

    evaluation = session.evaluate()
    assert evaluation.success is True
    assert evaluation.reward == 1.0
    assert session.lifecycle.status == SessionStatus.EVALUATED
    assert session.export_admission()["exportable_debug"] is True
    assert session.export_admission()["trainable"] is False

    termination = session.terminate()
    assert termination.success is True
    assert session.lifecycle.status == SessionStatus.TERMINATED
    assert [event.event_kind for event in session.events] == [
        "reset",
        "observe",
        "step",
        "snapshot",
        "evaluate",
        "terminate",
    ]


def test_snapshot_restore_round_trip() -> None:
    package = load_env_package(PYTHON_TOY)
    session = create_local_session(package, "py_toy_001")
    session.reset()
    snapshot = session.snapshot()
    session.step({"tool": "submit_answer", "answer": "wrong"})

    restored = session.restore(snapshot)
    assert restored.success is True
    assert restored.observation["submitted"] is False

    session.step({"tool": "submit_answer", "answer": "42"})
    assert session.evaluate().reward == 1.0
