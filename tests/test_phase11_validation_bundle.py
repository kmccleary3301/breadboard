from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def test_phase11_validation_bundle(tmp_path: Path) -> None:
    tasks = [
        {"id": "t1", "prompt": "Hello", "task_type": "general"},
        {"id": "t2", "prompt": "World", "task_type": "function"},
    ]
    space = {
        "study": {"direction": "maximize"},
        "space": {
            "providers.models[0].params.temperature": {"type": "float", "low": 0.0, "high": 0.2}
        },
    }
    tasks_path = tmp_path / "tasks.json"
    space_path = tmp_path / "space.json"
    out_path = tmp_path / "report.json"
    tasks_path.write_text(json.dumps(tasks), encoding="utf-8")
    space_path.write_text(json.dumps(space), encoding="utf-8")

    env = dict(**os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    result = subprocess.run(
        [
            "python",
            "scripts/phase11_validation_bundle.py",
            "--tasks",
            str(tasks_path),
            "--space",
            str(space_path),
            "--out",
            str(out_path),
            "--strict",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(out_path.read_text())
    assert payload["task_summary"]["ok"] is True
    assert payload["optuna_ok"] is True
