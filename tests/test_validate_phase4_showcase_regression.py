from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "validate_phase4_showcase_regression.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("validate_phase4_showcase_regression", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_run_dir(tmp_path: Path, *, frame_count: int, final_text: str, scenario: str) -> Path:
    run_dir = tmp_path / "run"
    frames = run_dir / "frames"
    frames.mkdir(parents=True, exist_ok=True)

    manifest = {
        "scenario": scenario,
        "capture_mode": "fullpane",
        "render_profile": "phase4_locked_v1",
    }
    (run_dir / "scenario_manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")

    rows: list[str] = []
    for i in range(1, frame_count + 1):
        txt = frames / f"frame_{i:04d}.txt"
        txt.write_text("placeholder\n", encoding="utf-8")
        rows.append(json.dumps({"frame": i, "text": f"frames/frame_{i:04d}.txt"}))
    (frames / f"frame_{frame_count:04d}.txt").write_text(final_text, encoding="utf-8")
    (run_dir / "index.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return run_dir


def test_validate_showcase_run_passes_with_expected_anchors(tmp_path: Path):
    module = _load_module()
    final_text = (
        "BreadBoard v0.2.0\n"
        "Write(hello.c)\n"
        "Patch(hello.c)\n"
        "Markdown Showcase\n"
        "Inline link BreadBoard\n"
        "ls -la\n"
        "Cooked for 3s\n"
    )
    run_dir = _make_run_dir(
        tmp_path,
        frame_count=12,
        final_text=final_text,
        scenario=module.DEFAULT_SCENARIO_ID,
    )
    result = module.validate_showcase_run(run_dir)
    assert result.ok is True
    assert result.errors == []
    assert result.frame_count == 12
    assert result.missing_anchors == []


def test_validate_showcase_run_fails_on_frame_floor_and_missing_anchor(tmp_path: Path):
    module = _load_module()
    run_dir = _make_run_dir(
        tmp_path,
        frame_count=3,
        final_text="BreadBoard v0.2.0\nWrite(hello.c)\n",
        scenario=module.DEFAULT_SCENARIO_ID,
    )
    result = module.validate_showcase_run(run_dir, min_frames=10)
    assert result.ok is False
    assert any("frame_count below minimum" in err for err in result.errors)
    assert any("missing required anchors" in err for err in result.errors)
