from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_module(module_name: str, rel_path: str):
    module_path = _repo_root() / rel_path
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


def test_passive_archive_accepts_local_variants_from_artifact_root(tmp_path: Path):
    module = _load_module("atp_evolake_passive_window", "scripts/atp_evolake_passive_window.py")
    artifact_dir = tmp_path / "artifacts"
    archive_root = artifact_dir / "nightly_archive"
    tracker_json = archive_root / "passive_4day_tracker.json"
    tracker_md = archive_root / "passive_4day_tracker.md"

    _write_json(artifact_dir / "atp_ops_digest.local.json", {"overall_ok": True, "decision_state": "green"})
    _write_json(
        artifact_dir / "evolake_toy_campaign_nightly.local.json",
        {"runs_requested": 3, "runs_passed": 3, "runs_failed": 0, "classification": "stable_pass"},
    )

    rc, summary = module.run_passive_archive(
        artifact_dir=artifact_dir,
        archive_root=archive_root,
        tracker_json=tracker_json,
        tracker_md=tracker_md,
        day="2026-02-18",
        require_green=True,
        allow_day_fail=False,
    )

    assert rc == 0
    assert summary["atp_ok"] is True
    assert summary["evolake_ok"] is True
    assert summary["ok"] is True
    assert "ops_digest" in summary["copied_artifacts"]
    assert "evolake" in summary["copied_artifacts"]
    tracker = json.loads(tracker_json.read_text(encoding="utf-8"))
    assert tracker["days"][-1]["ok"] is True


def test_passive_archive_accepts_archive_day_fallback_variants(tmp_path: Path):
    module = _load_module("atp_evolake_passive_window", "scripts/atp_evolake_passive_window.py")
    artifact_dir = tmp_path / "artifacts"
    archive_root = artifact_dir / "nightly_archive"
    tracker_json = archive_root / "passive_4day_tracker.json"
    tracker_md = archive_root / "passive_4day_tracker.md"
    day = "2026-02-18"
    day_dir = archive_root / day
    day_dir.mkdir(parents=True, exist_ok=True)

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

    assert rc == 0
    assert summary["atp_ok"] is True
    assert summary["evolake_ok"] is True
    assert summary["copied_artifacts"]["ops_digest"].endswith("atp_ops_digest.ops_nightly.local.json")
    assert summary["copied_artifacts"]["evolake"].endswith("evolake_toy_campaign_nightly.json")


def test_passive_archive_accepts_evolake_local_dir_fallback(tmp_path: Path):
    module = _load_module("atp_evolake_passive_window", "scripts/atp_evolake_passive_window.py")
    artifact_dir = tmp_path / "artifacts"
    archive_root = artifact_dir / "nightly_archive"
    tracker_json = archive_root / "passive_4day_tracker.json"
    tracker_md = archive_root / "passive_4day_tracker.md"
    day = "2026-02-18"

    _write_json(artifact_dir / "atp_ops_digest.latest.json", {"overall_ok": True})
    _write_json(
        artifact_dir / "evolake_toy_campaign_local" / "run_0001.json",
        {"runs_requested": 3, "runs_passed": 3, "runs_failed": 0},
    )

    rc, summary = module.run_passive_archive(
        artifact_dir=artifact_dir,
        archive_root=archive_root,
        tracker_json=tracker_json,
        tracker_md=tracker_md,
        day=day,
        require_green=True,
        allow_day_fail=False,
    )

    assert rc == 0
    assert summary["atp_ok"] is True
    assert summary["evolake_ok"] is True
    assert "evolake_local_dir" in summary["copied_artifacts"]


def test_passive_archive_accepts_external_source_dirs_override(tmp_path: Path):
    module = _load_module("atp_evolake_passive_window", "scripts/atp_evolake_passive_window.py")
    artifact_dir = tmp_path / "artifacts"
    external_root = tmp_path / "external_artifacts"
    archive_root = artifact_dir / "nightly_archive"
    tracker_json = archive_root / "passive_4day_tracker.json"
    tracker_md = archive_root / "passive_4day_tracker.md"
    day = "2026-02-18"

    _write_json(external_root / "atp_ops_digest.latest.json", {"overall_ok": True, "decision_state": "green"})
    _write_json(external_root / "evolake_toy_campaign_nightly.local.json", {"ok": True})

    rc, summary = module.run_passive_archive(
        artifact_dir=artifact_dir,
        archive_root=archive_root,
        tracker_json=tracker_json,
        tracker_md=tracker_md,
        day=day,
        require_green=True,
        allow_day_fail=False,
        source_dirs_override=[external_root],
    )

    assert rc == 0
    assert summary["atp_ok"] is True
    assert summary["evolake_ok"] is True
    assert summary["ok"] is True
