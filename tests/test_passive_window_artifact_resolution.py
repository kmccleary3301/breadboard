from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_module(module_name: str):
    module_path = _repo_root() / "scripts" / "atp_evolake_passive_window.py"
    scripts_dir = str((_repo_root() / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _paths(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    artifact_dir = tmp_path / "artifacts"
    archive_root = artifact_dir / "nightly_archive"
    return (
        artifact_dir,
        archive_root,
        archive_root / "passive_4day_tracker.json",
        archive_root / "passive_4day_tracker.md",
    )


def test_passive_archive_preserves_retired_report_without_current_green(tmp_path: Path):
    module = _load_module("atp_evolake_passive_window_retired")
    artifact_dir, archive_root, tracker_json, tracker_md = _paths(tmp_path)
    _write_json(artifact_dir / "atp_ops_digest.local.json", {"overall_ok": True, "decision_state": "green"})
    retired_bytes = (
        b'{"producer_mode":"bootstrap_structural","ok":true,'
        b'"runs_requested":3,"runs_passed":3,"runs_failed":0}\n'
    )
    retired_report = artifact_dir / "evolake_toy_campaign_nightly.local.json"
    retired_report.parent.mkdir(parents=True, exist_ok=True)
    retired_report.write_bytes(retired_bytes)

    rc, summary = module.run_passive_archive(
        artifact_dir=artifact_dir,
        archive_root=archive_root,
        tracker_json=tracker_json,
        tracker_md=tracker_md,
        day="2026-02-18",
        require_green=True,
        allow_day_fail=False,
    )

    assert rc == 2
    assert summary["atp_ok"] is True
    assert summary["evolake_ok"] is False
    assert summary["evolake_status"] == "retired"
    assert summary["evolake_execution_observed"] is False
    assert summary["ok"] is False
    copied = Path(summary["copied_artifacts"]["evolake"])
    assert copied.read_bytes() == retired_bytes
    tracker = json.loads(tracker_json.read_text(encoding="utf-8"))
    assert tracker["days"][-1]["evolake_status"] == "retired"
    assert tracker["days"][-1]["ok"] is False


def test_passive_archive_reads_existing_archive_bytes_but_blocks_retired_lane(tmp_path: Path):
    module = _load_module("atp_evolake_passive_window_archive")
    artifact_dir, archive_root, tracker_json, tracker_md = _paths(tmp_path)
    day = "2026-02-18"
    day_dir = archive_root / day
    _write_json(day_dir / "atp_ops_digest.ops_nightly.local.json", {"overall_ok": True})
    _write_json(day_dir / "evolake_toy_campaign_nightly.json", {"ok": True})

    rc, summary = module.run_passive_archive(
        artifact_dir=artifact_dir,
        archive_root=archive_root,
        tracker_json=tracker_json,
        tracker_md=tracker_md,
        day=day,
        require_green=True,
        allow_day_fail=False,
    )

    assert rc == 2
    assert summary["atp_ok"] is True
    assert summary["evolake_status"] == "retired"
    assert summary["evolake_ok"] is False
    assert summary["copied_artifacts"]["evolake"].endswith("evolake_toy_campaign_nightly.json")
    assert summary["ok"] is False


def test_passive_archive_materializes_retired_local_reports_without_counting_them(tmp_path: Path):
    module = _load_module("atp_evolake_passive_window_local")
    artifact_dir, archive_root, tracker_json, tracker_md = _paths(tmp_path)
    external_root = tmp_path / "external_artifacts"
    _write_json(external_root / "atp_ops_digest.latest.json", {"overall_ok": True, "decision_state": "green"})
    _write_json(
        external_root / "evolake_toy_campaign_local" / "run_0001.json",
        {"runs_requested": 3, "runs_passed": 3, "runs_failed": 0},
    )

    rc, summary = module.run_passive_archive(
        artifact_dir=artifact_dir,
        archive_root=archive_root,
        tracker_json=tracker_json,
        tracker_md=tracker_md,
        day="2026-02-18",
        require_green=True,
        allow_day_fail=False,
        source_dirs_override=[external_root],
    )

    assert rc == 2
    assert summary["atp_ok"] is True
    assert summary["evolake_status"] == "retired"
    assert summary["evolake_execution_observed"] is False
    assert summary["ok"] is False
    assert "evolake_local_dir" in summary["copied_artifacts"]
