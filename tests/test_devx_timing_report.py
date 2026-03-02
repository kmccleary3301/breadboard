from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_report_devx_timing_json_contract(tmp_path: Path) -> None:
    payload = {
        "profile": "engine",
        "started_at_utc": "2026-03-02T00:00:00Z",
        "ended_at_utc": "2026-03-02T00:01:00Z",
        "timing": {
            "bootstrap_duration_ms": 1234,
            "devx_smoke_duration_ms": 2345,
            "bootstrap_started_ms": 1,
            "bootstrap_ended_ms": 2,
            "devx_smoke_started_ms": 3,
            "devx_smoke_ended_ms": 4,
        },
    }
    src = tmp_path / "devx_full_pass_latest.json"
    src.write_text(json.dumps(payload), encoding="utf-8")

    proc = _run("python3", "scripts/dev/report_devx_timing.py", "--input", str(src), "--json")
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)

    assert out["profile"] == "engine"
    assert out["bootstrap_duration_ms"] == 1234
    assert out["devx_smoke_duration_ms"] == 2345
    assert out["total_duration_ms"] == 3579
    assert isinstance(out["bootstrap_duration_s"], float)
    assert isinstance(out["devx_smoke_duration_s"], float)
    assert isinstance(out["total_duration_s"], float)
