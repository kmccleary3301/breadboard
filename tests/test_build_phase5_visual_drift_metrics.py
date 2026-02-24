from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from PIL import Image


def _write_png(path: Path, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (4, 4), color).save(path)


def test_build_phase5_visual_drift_metrics(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    lane_dir = bundle / "todo"
    _write_png(lane_dir / "04_prev_final.png", (10, 10, 10))
    _write_png(lane_dir / "03_final.png", (20, 20, 20))
    (bundle / "manifest.json").write_text(
      json.dumps(
        {
          "lanes": {
            "todo": {
              "scenario": "phase4_replay/todo_preview_v1_fullpane_v7",
            }
          }
        }
      ),
      encoding="utf-8",
    )

    out_json = tmp_path / "metrics.json"
    proc = subprocess.run(
      [
        sys.executable,
        "scripts/build_phase5_visual_drift_metrics.py",
        "--flat-bundle-dir",
        str(bundle),
        "--out-json",
        str(out_json),
      ],
      cwd=str(Path(__file__).resolve().parents[1]),
      capture_output=True,
      text=True,
      check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "phase5_visual_drift_rows_v2"
    assert payload["count"] == 1
    row = payload["rows_sorted"][0]
    assert row["scenario"] == "phase4_replay/todo_preview_v1_fullpane_v7"
    assert row["frame"] == "frame_final"
    assert row["changed_px_pct"] > 0
    assert row["changed_px_pct_header"] > 0
    assert row["changed_px_pct_transcript"] > 0
    assert row["changed_px_pct_footer"] > 0
    assert row["dominant_band"] in {"header", "transcript", "footer"}
    assert isinstance(row["diff_bbox"], dict)


def test_build_phase5_visual_drift_metrics_top_level_manifest(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    lane_dir = bundle / "streaming"
    _write_png(lane_dir / "04_prev_final.png", (5, 5, 5))
    _write_png(lane_dir / "03_final.png", (5, 5, 5))
    (bundle / "manifest.json").write_text(
      json.dumps(
        {
          "streaming": {
            "scenario": "phase4_replay/streaming_v1_fullpane_v8",
            "selected": {
              "final": {"png": "streaming/03_final.png"},
              "prev_final": {"png": "streaming/04_prev_final.png"},
            },
          }
        }
      ),
      encoding="utf-8",
    )

    out_json = tmp_path / "metrics.json"
    proc = subprocess.run(
      [
        sys.executable,
        "scripts/build_phase5_visual_drift_metrics.py",
        "--flat-bundle-dir",
        str(bundle),
        "--out-json",
        str(out_json),
      ],
      cwd=str(Path(__file__).resolve().parents[1]),
      capture_output=True,
      text=True,
      check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["count"] == 1
    row = payload["rows_sorted"][0]
    assert row["scenario"] == "phase4_replay/streaming_v1_fullpane_v8"
    assert row["changed_px_pct"] == 0
    assert row["changed_px_pct_header"] == 0
    assert row["changed_px_pct_transcript"] == 0
    assert row["changed_px_pct_footer"] == 0
    assert row["dominant_band"] == "none"
    assert row["diff_bbox"] is None
