from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from PIL import Image


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "diff_terminal_semantics.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("diff_terminal_semantics", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_png(path: Path, rgb: tuple[int, int, int] = (10, 20, 30)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), rgb).save(path)


def _build_capture_run(run_dir: Path, *, text: str) -> None:
    (run_dir / "frames").mkdir(parents=True, exist_ok=True)
    (run_dir / "meta.json").write_text(json.dumps({"render_png": True}), encoding="utf-8")
    (run_dir / "initial.txt").write_text("init\n", encoding="utf-8")
    (run_dir / "initial.ansi").write_text("init\n", encoding="utf-8")
    txt = run_dir / "frames" / "frame_0001.txt"
    ansi = run_dir / "frames" / "frame_0001.ansi"
    png = run_dir / "frames" / "frame_0001.png"
    lock = run_dir / "frames" / "frame_0001.render_lock.json"
    parity = run_dir / "frames" / "frame_0001.row_parity.json"
    txt.write_text(text, encoding="utf-8")
    ansi.write_text(text, encoding="utf-8")
    _write_png(png)
    lock.write_text(
        json.dumps(
            {
                "schema_version": "tmux_render_lock_frame_v1",
                "row_occupancy": {
                    "missing_count": 0,
                    "extra_count": 0,
                    "row_span_delta": 0,
                    "text_sha256_normalized": "x",
                },
            }
        ),
        encoding="utf-8",
    )
    parity.write_text(
        json.dumps(
            {
                "schema_version": "tmux_row_parity_summary_v1",
                "parity": {
                    "missing_count": 0,
                    "extra_count": 0,
                    "row_span_delta": 0,
                    "text_sha256_normalized": "x",
                    "mismatch_localization": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "index.jsonl").write_text(
        json.dumps(
            {
                "frame": 1,
                "text": "frames/frame_0001.txt",
                "ansi": "frames/frame_0001.ansi",
                "png": "frames/frame_0001.png",
                "render_lock": "frames/frame_0001.render_lock.json",
                "render_parity_summary": "frames/frame_0001.row_parity.json",
                "render_parity": {"missing_count": 0, "extra_count": 0, "row_span_delta": 0},
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _build_eval_run(run_dir: Path, *, text_hash: str = "abc", mismatch_localization: list[dict] | None = None):
    if mismatch_localization is None:
        mismatch_localization = []
    (run_dir / "repeat_01" / "renders").mkdir(parents=True, exist_ok=True)
    base = run_dir / "repeat_01" / "renders" / "screenshot_ground_truth_1"
    png = base.with_suffix(".png")
    txt = base.with_suffix(".txt")
    ansi = base.with_suffix(".ansi")
    lock = base.with_suffix(".render_lock.json")
    parity = base.with_suffix(".row_parity.json")
    _write_png(png)
    txt.write_text("hello\n", encoding="utf-8")
    ansi.write_text("hello\n", encoding="utf-8")
    lock.write_text(
        json.dumps(
            {
                "schema_version": "tmux_render_lock_frame_v1",
                "row_occupancy": {
                    "missing_count": 0,
                    "extra_count": 0,
                    "row_span_delta": 0,
                    "text_sha256_normalized": text_hash,
                },
            }
        ),
        encoding="utf-8",
    )
    parity.write_text(
        json.dumps(
            {
                "schema_version": "tmux_row_parity_summary_v1",
                "parity": {
                    "missing_count": 0,
                    "extra_count": 0,
                    "row_span_delta": 0,
                    "text_sha256_normalized": text_hash,
                    "mismatch_localization": mismatch_localization,
                },
            }
        ),
        encoding="utf-8",
    )
    payload = {
        "schema_version": "ground_truth_eval_run_v1",
        "run_id": "x",
        "run_dir": str(run_dir.resolve()),
        "render_profile": "phase4_locked_v4",
        "repeats": 1,
        "targets": {
            "screenshot_ground_truth_1": {
                "repeats": [
                    {
                        "repeat": 1,
                        "png": str(png.resolve()),
                        "txt": str(txt.resolve()),
                        "ansi": str(ansi.resolve()),
                        "render_lock_json": str(lock.resolve()),
                        "row_parity_summary_json": str(parity.resolve()),
                        "metrics": {
                            "row_occupancy": {
                                "missing_count": 0,
                                "extra_count": 0,
                                "row_span_delta": 0,
                                "text_sha256_normalized": text_hash,
                                "mismatch_localization": mismatch_localization,
                            }
                        },
                    }
                ]
            }
        },
    }
    (run_dir / "comparison_metrics.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )


def test_compare_capture_runs_detects_text_mismatch(tmp_path: Path):
    module = _load_module()
    ref = tmp_path / "ref_capture"
    cand = tmp_path / "cand_capture"
    _build_capture_run(ref, text="hello\n")
    _build_capture_run(cand, text="HELLO\n")
    payload = module.compare_runs(ref, cand, mode="capture-run")
    assert payload["strict_equal"] is False
    assert payload["mismatch_count"] >= 1


def test_compare_eval_runs_passes_on_identical_payload(tmp_path: Path):
    module = _load_module()
    ref = tmp_path / "ref_eval"
    cand = tmp_path / "cand_eval"
    _build_eval_run(ref, text_hash="abc", mismatch_localization=[])
    _build_eval_run(cand, text_hash="abc", mismatch_localization=[])
    payload = module.compare_runs(ref, cand, mode="eval-run")
    assert payload["strict_equal"] is True
    assert payload["mismatch_count"] == 0
