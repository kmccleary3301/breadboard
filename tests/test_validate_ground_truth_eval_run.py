from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "validate_ground_truth_eval_run.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("validate_ground_truth_eval_run", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_png(path: Path, rgb: tuple[int, int, int] = (31, 36, 48)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 16), rgb).save(path)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _canonical_json_hash(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write_run(tmp_path: Path, *, leak_diagnostic_into_render: bool = False, bad_diag_prefix: bool = False) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    targets = (
        "screenshot_ground_truth_1",
        "screenshot_ground_truth_2",
        "screenshot_ground_truth_3",
    )
    target_payload: dict[str, dict[str, object]] = {}
    for target in targets:
        repeat_idx = 1
        repeat_dir = run_dir / f"repeat_{repeat_idx:02d}"
        renders = repeat_dir / "renders"
        diagnostics = repeat_dir / "diagnostics"
        renders.mkdir(parents=True, exist_ok=True)
        diagnostics.mkdir(parents=True, exist_ok=True)

        png_name = f"{target}.png"
        if leak_diagnostic_into_render and target == "screenshot_ground_truth_2":
            png_name = f"{target}.DIAGNOSTIC_bad.png"

        diag_name = f"{target}.DIAGNOSTIC_diff_heat_x4.png"
        if bad_diag_prefix and target == "screenshot_ground_truth_3":
            diag_name = f"{target}.diff_heat_x4.png"

        png_path = renders / png_name
        txt_path = renders / f"{target}.txt"
        ansi_path = renders / f"{target}.ansi"
        if bad_diag_prefix and target == "screenshot_ground_truth_3":
            diag_path = renders / diag_name
        else:
            diag_path = diagnostics / diag_name
        render_lock_path = renders / f"{target}.render_lock.json"
        row_parity_summary_path = renders / f"{target}.row_parity.json"

        _write_png(png_path)
        txt_path.write_text("line\n", encoding="utf-8")
        ansi_path.write_text("line\n", encoding="utf-8")
        _write_png(diag_path, rgb=(255, 0, 255))
        render_lock_path.write_text(
            json.dumps(
                {
                    "schema_version": "tmux_render_lock_frame_v1",
                    "row_occupancy": {
                        "missing_count": 0,
                        "extra_count": 0,
                        "row_span_delta": 0,
                        "text_sha256_normalized": "abc123",
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        row_parity_summary_path.write_text(
            json.dumps(
                {
                    "schema_version": "tmux_row_parity_summary_v1",
                    "parity": {
                        "missing_count": 0,
                        "extra_count": 0,
                        "row_span_delta": 0,
                        "text_sha256_normalized": "abc123",
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        target_payload[target] = {
            "sample_png": str((run_dir / f"{target}_sample.png").resolve()),
            "limits": {},
            "variance_limits": {},
            "aggregate": {},
            "threshold_eval": {"pass": True},
            "variance_eval": {"pass": True},
            "pass": True,
            "repeats": [
                {
                    "repeat": 1,
                    "png": str(png_path.resolve()),
                    "txt": str(txt_path.resolve()),
                    "ansi": str(ansi_path.resolve()),
                    "render_lock_json": str(render_lock_path.resolve()),
                    "render_lock_sha256": _sha256_file(render_lock_path),
                    "row_parity_summary_json": str(row_parity_summary_path.resolve()),
                    "row_parity_summary_sha256": _sha256_file(row_parity_summary_path),
                    "diagnostic_heatmap_png": str(diag_path.resolve()),
                    "metrics": {
                        "heatmap_path": str(diag_path.resolve()),
                        "row_occupancy": {
                            "missing_count": 0,
                            "extra_count": 0,
                            "row_span_delta": 0,
                            "text_sha256_normalized": "abc123",
                        },
                    },
                    "threshold_eval": {"pass": True},
                }
            ],
        }

    deterministic_inputs = {
        "schema_version": "ground_truth_deterministic_inputs_v1",
        "render_profile": "phase4_locked_v4",
        "repeats": 1,
    }
    payload = {
        "schema_version": "ground_truth_eval_run_v1",
        "run_id": "test-run",
        "run_dir": str(run_dir.resolve()),
        "render_profile": "phase4_locked_v4",
        "repeats": 1,
        "thresholds_path": str((run_dir / "thresholds.json").resolve()),
        "deterministic_inputs": deterministic_inputs,
        "deterministic_input_hash": _canonical_json_hash(deterministic_inputs),
        "overall_pass": True,
        "targets": target_payload,
    }
    (run_dir / "comparison_metrics.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return run_dir


def test_validate_ground_truth_eval_run_passes_for_valid_layout(tmp_path: Path):
    module = _load_module()
    run_dir = _write_run(tmp_path)
    result = module.validate_run(run_dir)
    assert result.ok is True
    assert result.errors == []


def test_validate_ground_truth_eval_run_fails_on_diagnostic_leak_into_render_path(tmp_path: Path):
    module = _load_module()
    run_dir = _write_run(tmp_path, leak_diagnostic_into_render=True)
    result = module.validate_run(run_dir)
    assert result.ok is False
    assert any("diagnostic artifact leaked into png path" in err for err in result.errors)


def test_validate_ground_truth_eval_run_fails_when_diagnostic_not_under_diagnostics_dir(tmp_path: Path):
    module = _load_module()
    run_dir = _write_run(tmp_path, bad_diag_prefix=True)
    result = module.validate_run(run_dir)
    assert result.ok is False
    assert any("diagnostic_heatmap_png must be under" in err for err in result.errors)
    assert any("filename must include `DIAGNOSTIC`" in err for err in result.errors)


def test_validate_ground_truth_eval_run_fails_when_deterministic_hash_mismatch(tmp_path: Path):
    module = _load_module()
    run_dir = _write_run(tmp_path)
    metrics_path = run_dir / "comparison_metrics.json"
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    payload["deterministic_input_hash"] = "0" * 64
    metrics_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    result = module.validate_run(run_dir)
    assert result.ok is False
    assert any("deterministic_input_hash mismatch" in err for err in result.errors)
