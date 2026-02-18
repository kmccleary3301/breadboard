from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "validate_phase4_visual_pack.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("validate_phase4_visual_pack", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _touch(p: Path, text: str = "x\n"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _make_lane(pack_dir: Path, key: str, scenario: str):
    lane = pack_dir / key
    _touch(lane / "01_landing.png")
    _touch(lane / "01_landing.txt")
    _touch(lane / "01_landing.ansi")
    _touch(lane / "02_active.png")
    _touch(lane / "02_active.txt")
    _touch(lane / "02_active.ansi")
    _touch(lane / "03_final.png")
    _touch(lane / "03_final.txt")
    _touch(lane / "03_final.ansi")
    _touch(lane / "04_prev_final.png")
    _touch(lane / "03_final_fit_to_prev.png")
    _touch(lane / "compare_prev_vs_new_side_by_side.png")
    _touch(lane / "compare_prev_vs_new_diff_heat_x3.png")

    metrics_path = lane / "compare_prev_vs_new_metrics.json"
    metrics_path.write_text(
        json.dumps(
            {
                "mean_abs_diff_rgb": [0.0, 0.0, 0.0],
                "rms_diff_rgb": [0.0, 0.0, 0.0],
                "prev_size": [1600, 900],
                "new_size": [1600, 900],
                "new_fit_size": [1600, 900],
                "scenario": scenario,
                "selected_frame_indices": {"landing": 1, "active": 2, "final": 3, "prev_final": 3},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    return {
        "scenario": scenario,
        "new_run": f"docs_tmp/tmux_captures/scenarios/phase4_replay/{key}/run",
        "prev_source": "docs_tmp/tmux_captures/scenarios/phase4_replay/baseline/run",
        "new_frame_count": 3,
        "selected": {
            "landing": {"index": 1, "png": f"{key}/01_landing.png", "txt": f"{key}/01_landing.txt", "ansi": f"{key}/01_landing.ansi"},
            "active": {"index": 2, "png": f"{key}/02_active.png", "txt": f"{key}/02_active.txt", "ansi": f"{key}/02_active.ansi"},
            "final": {"index": 3, "png": f"{key}/03_final.png", "txt": f"{key}/03_final.txt", "ansi": f"{key}/03_final.ansi"},
            "prev_final": {"index": 3, "png": f"{key}/04_prev_final.png", "txt": None, "ansi": None},
            "final_fit_to_prev_png": f"{key}/03_final_fit_to_prev.png",
            "compare_side_by_side_png": f"{key}/compare_prev_vs_new_side_by_side.png",
            "compare_heat_x3_png": f"{key}/compare_prev_vs_new_diff_heat_x3.png",
            "compare_metrics_json": f"{key}/compare_prev_vs_new_metrics.json",
        },
    }


def _make_pack(tmp_path: Path):
    module = _load_module()
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir(parents=True, exist_ok=True)
    _touch(pack_dir / "everything_showcase_ref_v2.png", "png\n")

    manifest = {}
    for key, scenario in module.DEFAULT_LANE_SCENARIOS.items():
        manifest[key] = _make_lane(pack_dir, key, scenario)

    (pack_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    (pack_dir / "INDEX.md").write_text(
        "\n".join(module.DEFAULT_INDEX_ANCHORS) + "\n",
        encoding="utf-8",
    )
    return module, pack_dir


def test_validate_visual_pack_passes_for_valid_pack(tmp_path: Path):
    module, pack_dir = _make_pack(tmp_path)
    result = module.validate_pack(pack_dir)
    assert result.ok is True
    assert result.errors == []
    assert result.lanes_seen == sorted(module.DEFAULT_LANE_SCENARIOS.keys())


def test_validate_visual_pack_fails_on_missing_anchor_and_missing_file(tmp_path: Path):
    module, pack_dir = _make_pack(tmp_path)
    (pack_dir / "INDEX.md").write_text("# Visual Review Pack (Phase4 Fullpane Lock V1)\n", encoding="utf-8")
    (pack_dir / "todo" / "02_active.png").unlink()
    result = module.validate_pack(pack_dir)
    assert result.ok is False
    assert any("INDEX.md missing required anchor" in err for err in result.errors)
    assert any("todo.active.png" in err for err in result.errors)
