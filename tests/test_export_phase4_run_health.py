from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "export_phase4_run_health.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("export_phase4_run_health", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_run(run_dir: Path, scenario: str):
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "scenario_manifest.json").write_text(
        json.dumps(
            {
                "scenario": scenario,
                "capture_mode": "fullpane",
                "render_profile": "phase4_locked_v1",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    frames = run_dir / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    (frames / "frame_0001.txt").write_text("x\n", encoding="utf-8")
    (run_dir / "index.jsonl").write_text(
        json.dumps({"frame": 1, "text": "frames/frame_0001.txt"}) + "\n",
        encoding="utf-8",
    )


def _write_pack(pack_dir: Path):
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "INDEX.md").write_text("# x\n", encoding="utf-8")
    (pack_dir / "manifest.json").write_text("{}\n", encoding="utf-8")
    (pack_dir / "visual_pack_schema_report.json").write_text(
        json.dumps({"ok": True}) + "\n",
        encoding="utf-8",
    )


def test_build_health_ok(tmp_path: Path):
    module = _load_module()
    stream = tmp_path / "stream"
    todo = tmp_path / "todo"
    sub = tmp_path / "sub"
    every = tmp_path / "every"
    _write_run(stream, "phase4_replay/streaming_v1_fullpane_v8")
    _write_run(todo, "phase4_replay/todo_preview_v1_fullpane_v7")
    _write_run(sub, "phase4_replay/subagents_v1_fullpane_v7")
    _write_run(every, "phase4_replay/everything_showcase_v1_fullpane_v1")
    (every / "showcase_regression_report.json").write_text(json.dumps({"ok": True}) + "\n", encoding="utf-8")
    pack = tmp_path / "pack"
    _write_pack(pack)

    args = SimpleNamespace(
        mode="gate",
        stream_run_dir=str(stream),
        todo_run_dir=str(todo),
        subagents_run_dir=str(sub),
        everything_run_dir=str(every),
        pack_dir=str(pack),
        scenario_rc="0",
        validation_rc="0",
        pack_rc="0",
        pack_schema_rc="0",
    )
    health = module.build_health(args)
    assert health["schema_version"] == module.SCHEMA_VERSION
    assert health["overall_ok"] is True
    assert health["lanes"]["everything"]["showcase_regression_ok"] is True


def test_build_health_detects_missing_lane(tmp_path: Path):
    module = _load_module()
    stream = tmp_path / "stream"
    _write_run(stream, "phase4_replay/streaming_v1_fullpane_v8")

    args = SimpleNamespace(
        mode="nightly",
        stream_run_dir=str(stream),
        todo_run_dir="",
        subagents_run_dir="",
        everything_run_dir="",
        pack_dir="",
        scenario_rc="0",
        validation_rc="0",
        pack_rc="",
        pack_schema_rc="",
    )
    health = module.build_health(args)
    assert health["overall_ok"] is False
    assert health["lanes"]["todo"]["present"] is False
