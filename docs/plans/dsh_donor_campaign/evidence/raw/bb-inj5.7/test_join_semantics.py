"""Focused executable proof for DAG terminal-predecessor joins."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXPECTED = {
    "rq_ablation_zero": False,
    "rq_ablation_pass": True,
    "rq_ablation_reconstructed": True,
    "strict_two_zero": False,
    "strict_two_one": False,
    "strict_two_other": False,
    "strict_two_both": True,
    "rt_replay_one": False,
    "rt_replay_two": False,
    "rt_replay_all": True,
}


def test_join_semantics() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "validate_dag_prototype.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["join_semantics"] == EXPECTED


if __name__ == "__main__":
    test_join_semantics()
    print("PASS: DAG join semantics")
