from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_surface_coverage_audit_script_ok(tmp_path: Path) -> None:
    run_summary = {
        "surface_manifest": {
            "surfaces": [
                {"name": "tool_schema_latest"},
                {"name": "tool_allowlist_latest"},
                {"name": "prompt_hashes"},
                {"name": "tool_prompt_mode"},
                {"name": "system_roles"},
            ]
        }
    }
    summary_path = tmp_path / "run_summary.json"
    summary_path.write_text(json.dumps(run_summary), encoding="utf-8")
    result = subprocess.run(
        ["python", "scripts/phase11_surface_coverage_audit.py", "--summary", str(summary_path)],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
