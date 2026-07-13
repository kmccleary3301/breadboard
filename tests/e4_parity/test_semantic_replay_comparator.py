from __future__ import annotations

import json
from pathlib import Path

import pytest

from conformance.comparators.semantic_replay import LANE_ID, compare

ROOT = Path(__file__).resolve().parents[2]
LANE_DIR = ROOT / "docs" / "conformance" / "e4_target_support" / LANE_ID


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _semantic_input(*, replay_ref: Path | None = None, comparator_ref: Path | None = None) -> dict:
    artifacts = {
        "capture_ref": LANE_DIR / "raw_capture_manifest.json",
        "replay_ref": replay_ref or LANE_DIR / "bb_replay_result.json",
    }
    if comparator_ref is not None:
        artifacts["comparator_ref"] = comparator_ref
    return {
        "capture": {},
        "replay": {},
        "scope": {"lane_id": LANE_ID},
        "artifacts": artifacts,
    }


def test_semantic_replay_recomputes_real_pilot_lane_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "semantic_replay"
    report = compare(_semantic_input(), output_dir=output_dir)
    report_path = output_dir / LANE_ID / "report.json"
    assert report["ok"] is True
    assert report["failed"] == 0
    assert len(report["assertions"]) >= 3
    assert all(assertion["status"] == "passed" for assertion in report["assertions"])
    assert report_path.exists()
    assert _load_json(report_path) == report


def test_semantic_replay_tampered_replay_record_fails(tmp_path: Path) -> None:
    replay = _load_json(LANE_DIR / "bb_replay_result.json")
    replay["replay_summary"]["fetch_event_count"] = 999
    replay_path = tmp_path / "bb_replay_result.json"
    _write_json(replay_path, replay)

    output_dir = tmp_path / "semantic_replay"
    report = compare(_semantic_input(replay_ref=replay_path), output_dir=output_dir)
    assert report["ok"] is False
    assert report["failed"] >= 1
    failures = {assertion["assertion_id"] for assertion in report["assertions"] if assertion["status"] == "failed"}
    assert "provider_network_scope_matches_replay_summary" in failures
    assert (output_dir / LANE_ID / "report.json").exists()


def test_semantic_replay_refuses_accepted_comparator_report_path() -> None:
    accepted_report = LANE_DIR / "comparator_report.json"
    with pytest.raises(ValueError, match="refuses accepted comparator_report"):
        compare(_semantic_input(comparator_ref=accepted_report))
