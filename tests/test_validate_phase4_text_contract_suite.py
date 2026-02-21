from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _make_run(tmp_path: Path, *, scenario: str, text: str, run_name: str) -> Path:
    run_dir = tmp_path / run_name
    frames = run_dir / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    (run_dir / "scenario_manifest.json").write_text(
        json.dumps({"scenario": scenario}) + "\n",
        encoding="utf-8",
    )
    (frames / "frame_0001.txt").write_text(text, encoding="utf-8")
    (run_dir / "index.jsonl").write_text(
        json.dumps({"frame": 1, "text": "frames/frame_0001.txt"}) + "\n",
        encoding="utf-8",
    )
    return run_dir


def test_text_contract_suite_passes_for_supported_alias(tmp_path: Path):
    text = "\n".join(
        [
            "BreadBoard v0.2.0",
            "✔ completed · Index workspace files · task-1",
            "☐ running · Compute TODO preview metrics · task-2",
            "[done]",
            "  ? for shortcuts  ✻ Cooked for 2s",
            "╭────────────╮",
            "│ Background tasks │",
            "│ › [primary] completed · Compute TODO preview metrics │",
            "│   [primary] completed · Index workspace files │",
            "╰────────────╯",
        ]
    )
    run_dir = _make_run(
        tmp_path,
        scenario="phase4_replay/subagents_smoke_v1_fullpane_v7",
        text=text,
        run_name="run_supported",
    )
    out_json = tmp_path / "suite_report.json"
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "scripts" / "validate_phase4_text_contract_suite.py"),
        "--run-dir",
        str(run_dir),
        "--strict",
        "--fail-on-unmapped",
        "--output-json",
        str(out_json),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    report = json.loads(out_json.read_text(encoding="utf-8"))
    assert report["overall_ok"] is True
    assert report["unmapped_count"] == 0
    assert report["total_runs"] == 1


def test_text_contract_suite_fails_on_unmapped_when_requested(tmp_path: Path):
    text = "\n".join(
        [
            "BreadBoard v0.2.0",
            "[done]",
            "  ? for shortcuts  ✻ Cooked for 0s",
        ]
    )
    run_dir = _make_run(
        tmp_path,
        scenario="phase4_replay/not_mapped_v1",
        text=text,
        run_name="run_unmapped",
    )
    out_json = tmp_path / "suite_report_unmapped.json"
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "scripts" / "validate_phase4_text_contract_suite.py"),
        "--run-dir",
        str(run_dir),
        "--strict",
        "--fail-on-unmapped",
        "--output-json",
        str(out_json),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 2
    report = json.loads(out_json.read_text(encoding="utf-8"))
    assert report["overall_ok"] is False
    assert report["unmapped_count"] == 1


def test_text_contract_suite_scenario_set_reports_missing_members(tmp_path: Path):
    text = "\n".join(
        [
            "BreadBoard v0.2.0",
            "• # Streaming smoke",
            "  - line 1",
            "  - line 2",
            "────────────────",
            '❯ Try "fix typecheck errors"',
            "────────────────",
            "  ? for shortcuts  ✻ Cooked for 2s",
        ]
    )
    run_dir = _make_run(
        tmp_path,
        scenario="phase4_replay/streaming_v1_fullpane_v8",
        text=text,
        run_name="run_stream_only",
    )
    out_json = tmp_path / "suite_report_set.json"
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "scripts" / "validate_phase4_text_contract_suite.py"),
        "--run-dir",
        str(run_dir),
        "--scenario-set",
        "hard_gate",
        "--strict",
        "--fail-on-unmapped",
        "--output-json",
        str(out_json),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == 2
    report = json.loads(out_json.read_text(encoding="utf-8"))
    assert report["overall_ok"] is False
    assert "phase4_replay/todo_preview_v1_fullpane_v7" in report["missing_selected_scenarios"]
