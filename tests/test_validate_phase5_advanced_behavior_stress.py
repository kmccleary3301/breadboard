from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "validate_phase5_advanced_behavior_stress.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("validate_phase5_advanced_behavior_stress", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_png(path: Path, *, color: tuple[int, int, int], mutate: bool = False) -> None:
    arr = np.zeros((6, 6, 3), dtype=np.uint8)
    arr[:] = color
    if mutate:
        arr[2:4, 2:4] = (250, 250, 250)
    Image.fromarray(arr, mode="RGB").save(path)


def _write_run(
    run_root: Path,
    *,
    scenario: str,
    run_id: str,
    frame_count: int = 6,
    max_gap: float = 1.0,
    unchanged_streak_seconds: float = 1.0,
    with_motion: bool = True,
) -> Path:
    slug = scenario.replace("phase4_replay/", "", 1)
    run_dir = run_root / slug / run_id
    frames = run_dir / "frames"
    frames.mkdir(parents=True, exist_ok=True)

    base_ts = 1000.0
    rows = []
    for idx in range(1, frame_count + 1):
        frame = f"frame_{idx:04d}"
        _write_png(frames / f"{frame}.png", color=(10, 12, 20), mutate=(with_motion and idx == frame_count))
        (frames / f"{frame}.txt").write_text("BreadBoard v0.2.0\nTry \"fix typecheck errors\"\n", encoding="utf-8")
        (frames / f"{frame}.ansi").write_text("ansi\n", encoding="utf-8")
        rows.append(
            {
                "frame": idx,
                "timestamp": base_ts + (idx - 1) * max_gap,
                "cols": 160,
                "rows": 45,
                "text": f"frames/{frame}.txt",
                "ansi": f"frames/{frame}.ansi",
                "png": f"frames/{frame}.png",
            }
        )
    (run_dir / "index.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    manifest = {
        "scenario": scenario,
        "run_id": run_id,
        "scenario_result": "pass",
        "execution_error": None,
        "action_error": None,
        "frame_stall_error": None,
        "frame_validation": {
            "frame_count": frame_count,
            "frames_dir_exists": True,
            "missing_artifacts": [],
            "missing_frame_indices": [],
            "max_unchanged_streak_seconds_estimate": unchanged_streak_seconds,
        },
    }
    (run_dir / "scenario_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return run_dir


class _ParityPass:
    ok = True
    errors: list[str] = []


def test_validate_phase5_advanced_behavior_stress_passes(tmp_path: Path):
    module = _load_module()
    scenario = "phase4_replay/custom_advanced_v1"
    _write_run(
        tmp_path / "runs",
        scenario=scenario,
        run_id="20260224-000001",
        frame_count=6,
        max_gap=1.0,
        unchanged_streak_seconds=1.2,
        with_motion=True,
    )
    module.validate_run = lambda *_args, **_kwargs: _ParityPass()

    result = module.validate_advanced_stress(
        run_root=tmp_path / "runs",
        scenarios=[scenario],
        contract_file=tmp_path / "contract.json",
        max_frame_gap_seconds=3.2,
        max_unchanged_streak_seconds=2.2,
        min_active_coverage=0.01,
        min_nonzero_deltas=1,
        delta_epsilon=1e-6,
        fail_on_missing_scenarios=True,
    )
    assert result.ok is True
    assert result.report["overall_ok"] is True
    assert result.report["validated_count"] == 1


def test_validate_phase5_advanced_behavior_stress_fails_on_motion(tmp_path: Path):
    module = _load_module()
    scenario = "phase4_replay/custom_advanced_v1"
    _write_run(
        tmp_path / "runs",
        scenario=scenario,
        run_id="20260224-000002",
        frame_count=6,
        max_gap=1.0,
        unchanged_streak_seconds=1.2,
        with_motion=False,
    )
    module.validate_run = lambda *_args, **_kwargs: _ParityPass()

    result = module.validate_advanced_stress(
        run_root=tmp_path / "runs",
        scenarios=[scenario],
        contract_file=tmp_path / "contract.json",
        max_frame_gap_seconds=3.2,
        max_unchanged_streak_seconds=2.2,
        min_active_coverage=0.01,
        min_nonzero_deltas=1,
        delta_epsilon=1e-6,
        fail_on_missing_scenarios=True,
    )
    assert result.ok is False
    assert any("advanced stress checks failed" in err for err in result.errors)
    record = result.report["records"][0]
    checks = {row["name"]: row["pass"] for row in record["checks"]}
    assert checks["nonzero_frame_delta_count"] is False


def test_validate_phase5_advanced_behavior_stress_fails_on_missing_scenario(tmp_path: Path):
    module = _load_module()
    result = module.validate_advanced_stress(
        run_root=tmp_path / "runs",
        scenarios=["phase4_replay/missing_v1"],
        contract_file=tmp_path / "contract.json",
        max_frame_gap_seconds=3.2,
        max_unchanged_streak_seconds=2.2,
        min_active_coverage=0.01,
        min_nonzero_deltas=1,
        delta_epsilon=1e-6,
        fail_on_missing_scenarios=True,
    )
    assert result.ok is False
    assert result.report["missing_scenarios"] == ["phase4_replay/missing_v1"]
