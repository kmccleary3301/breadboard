from __future__ import annotations

from pathlib import Path

from breadboard.rl.env_package.validate import load_env_package
from breadboard.rl.runtime import run_local_ray_toy_probe


REPO_ROOT = Path(__file__).resolve().parents[3]
PYTHON_TOY = REPO_ROOT / "examples" / "rl_env_packages" / "python_console_toy" / "env_package.yaml"


def test_local_ray_worker_executes_toy_sessions() -> None:
    package = load_env_package(PYTHON_TOY)
    report = run_local_ray_toy_probe(
        package=package,
        task_ids=["py_toy_001", "py_toy_002", "py_toy_003", "py_toy_004"],
        num_workers=2,
    )

    assert report["row_count"] == 4
    assert report["worker_count"] == 2
    assert report["ray_local_mode"] is True
    assert all(row["reward"] == 1.0 for row in report["rows"])
    assert all(row["event_count"] == 3 for row in report["rows"])
