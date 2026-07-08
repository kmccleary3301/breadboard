from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_gap_closure_consistency.py"
CSV_HEADER = "gap_id,current_status,ct_coverage,primary_checker,remaining_closure_delta\n"


def _write_fixture(tmp_path: Path, *, csv_ct: str, rubric_ct: str, scenario_ids: list[str]) -> tuple[Path, Path, Path]:
    csv_path = tmp_path / "gap.csv"
    csv_path.write_text(CSV_HEADER + f"G-001,covered,{csv_ct},checker.py,\n", encoding="utf-8")

    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "schema_version": "fixture",
                "gaps": [
                    {
                        "gap_id": "G-001",
                        "priority_cluster": "P1",
                        "priority_score": 1,
                        "requires_live_evidence": False,
                        "required_ct_ids": rubric_ct,
                        "required_live_ct_ids": "",
                        "closure_rule": "fixture",
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    scenarios_path = tmp_path / "ct_scenarios_v1.json"
    scenarios_path.write_text(
        json.dumps({"scenarios": [{"test_id": test_id} for test_id in scenario_ids]}, indent=2) + "\n",
        encoding="utf-8",
    )
    return csv_path, rubric_path, scenarios_path


def _run_checker(csv_path: Path, rubric_path: Path, scenarios_path: Path, json_out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--csv",
            str(csv_path),
            "--rubric",
            str(rubric_path),
            "--ct-scenarios",
            str(scenarios_path),
            "--json-out",
            str(json_out),
        ],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        capture_output=True,
    )


def test_gap_closure_consistency_repo_is_current(tmp_path: Path) -> None:
    json_out = tmp_path / "gap_check.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json-out", str(json_out)],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload == {"ok": True, "mismatches": [], "unknown_ct_ids": []}


def test_gap_closure_consistency_catches_injected_mismatch(tmp_path: Path) -> None:
    csv_path, rubric_path, scenarios_path = _write_fixture(
        tmp_path,
        csv_ct="CT-EXT-005;CT-EXT-006",
        rubric_ct="CT-EXT-005/007",
        scenario_ids=["CT-EXT-005", "CT-EXT-006", "CT-EXT-007"],
    )
    json_out = tmp_path / "gap_check.json"

    result = _run_checker(csv_path, rubric_path, scenarios_path, json_out)

    assert result.returncode == 1
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload == {
        "ok": False,
        "mismatches": [
            {
                "gap_id": "G-001",
                "csv": "CT-EXT-005;CT-EXT-006",
                "rubric": "CT-EXT-005;CT-EXT-007",
            }
        ],
        "unknown_ct_ids": [],
    }


def test_gap_closure_consistency_catches_unknown_ct_id(tmp_path: Path) -> None:
    csv_path, rubric_path, scenarios_path = _write_fixture(
        tmp_path,
        csv_ct="CT-EXT-005;CT-EXT-006",
        rubric_ct="CT-EXT-005/006",
        scenario_ids=["CT-EXT-005"],
    )
    json_out = tmp_path / "gap_check.json"

    result = _run_checker(csv_path, rubric_path, scenarios_path, json_out)

    assert result.returncode == 1
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload == {"ok": False, "mismatches": [], "unknown_ct_ids": ["CT-EXT-006"]}
