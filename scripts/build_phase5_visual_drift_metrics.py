#!/usr/bin/env python3
"""
Build phase5 visual-drift metrics from a curated flat bundle.

Input:
- --flat-bundle-dir: output directory from curate_phase4_fullpane_visual_pack.py

Output JSON schema:
{
  "schema_version": "phase5_visual_drift_rows_v2",
  "rows_sorted": [
    {
      "scenario": "phase4_replay/...",
      "frame": "frame_final",
      "mean_abs_diff": <float>,
      "rms_diff": <float>,
      "changed_px_pct": <float>,
      "changed_px_pct_header": <float>,
      "changed_px_pct_transcript": <float>,
      "changed_px_pct_footer": <float>,
      "dominant_band": "header|transcript|footer",
      "diff_bbox": {
        "x": <int>,
        "y": <int>,
        "width": <int>,
        "height": <int>,
        "area_pct": <float>
      } | null
    }
  ]
}
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object JSON at {path}")
    return payload


def _compute_diff_metrics(prev_path: Path, new_path: Path) -> dict[str, Any]:
    prev = Image.open(prev_path).convert("RGB")
    new = Image.open(new_path).convert("RGB")
    if prev.size != new.size:
        new = new.resize(prev.size, Image.Resampling.BICUBIC)
    diff = ImageChops.difference(prev, new).convert("RGB")
    width, height = diff.size
    px = diff.load()
    n = max(1, width * height)
    sum_abs = 0.0
    sum_sq = 0.0
    changed = 0
    header_end = max(1, min(height, int(round(height * 0.18))))
    footer_start = max(header_end, min(height, int(round(height * 0.82))))
    band_total = {"header": 0, "transcript": 0, "footer": 0}
    band_changed = {"header": 0, "transcript": 0, "footer": 0}

    min_x = width
    min_y = height
    max_x = -1
    max_y = -1

    for y in range(height):
        if y < header_end:
            band = "header"
        elif y >= footer_start:
            band = "footer"
        else:
            band = "transcript"
        for x in range(width):
            r, g, b = px[x, y]
            band_total[band] += 1
            is_changed = r != 0 or g != 0 or b != 0
            if is_changed:
                changed += 1
                band_changed[band] += 1
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if x > max_x:
                    max_x = x
                if y > max_y:
                    max_y = y
            sum_abs += (r + g + b) / 3.0
            sum_sq += ((r * r) + (g * g) + (b * b)) / 3.0

    mean_abs = round(_safe_float(sum_abs / n), 3)
    rms = round(_safe_float(math.sqrt(sum_sq / n)), 3)
    changed_pct = round(_safe_float((changed / n) * 100.0), 3)
    header_pct = round(_safe_float((band_changed["header"] / max(1, band_total["header"])) * 100.0), 3)
    transcript_pct = round(_safe_float((band_changed["transcript"] / max(1, band_total["transcript"])) * 100.0), 3)
    footer_pct = round(_safe_float((band_changed["footer"] / max(1, band_total["footer"])) * 100.0), 3)
    if changed <= 0:
        dominant_band = "none"
    else:
        dominant_band = max(
            (
                ("header", header_pct),
                ("transcript", transcript_pct),
                ("footer", footer_pct),
            ),
            key=lambda kv: kv[1],
        )[0]

    bbox: dict[str, Any] | None = None
    if changed > 0 and max_x >= min_x and max_y >= min_y:
        bbox_w = (max_x - min_x) + 1
        bbox_h = (max_y - min_y) + 1
        bbox = {
            "x": int(min_x),
            "y": int(min_y),
            "width": int(bbox_w),
            "height": int(bbox_h),
            "area_pct": round(_safe_float((bbox_w * bbox_h) / n * 100.0), 3),
        }

    return {
        "mean_abs_diff": mean_abs,
        "rms_diff": rms,
        "changed_px_pct": changed_pct,
        "changed_px_pct_header": header_pct,
        "changed_px_pct_transcript": transcript_pct,
        "changed_px_pct_footer": footer_pct,
        "dominant_band": dominant_band,
        "diff_bbox": bbox,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--flat-bundle-dir", required=True, help="curated flat bundle dir")
    p.add_argument("--out-json", required=True, help="output metrics json path")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    flat_bundle_dir = Path(args.flat_bundle_dir).expanduser().resolve()
    out_json = Path(args.out_json).expanduser().resolve()
    manifest_path = flat_bundle_dir / "manifest.json"
    if not flat_bundle_dir.exists():
        raise FileNotFoundError(f"flat bundle dir not found: {flat_bundle_dir}")
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest missing: {manifest_path}")

    manifest = _load_json(manifest_path)
    lanes = manifest.get("lanes")
    if isinstance(lanes, dict):
        lane_entries = lanes
    else:
        # Curated pack manifests currently store lanes at top-level.
        lane_entries = {
            key: value
            for key, value in manifest.items()
            if isinstance(value, dict) and isinstance(value.get("selected"), dict)
        }
    if not isinstance(lane_entries, dict) or len(lane_entries) == 0:
        raise ValueError("manifest does not contain recognizable lane entries")

    rows: list[dict[str, Any]] = []
    for lane_key, lane in lane_entries.items():
        if not isinstance(lane, dict):
            continue
        scenario = str(lane.get("scenario") or lane_key)
        lane_dir = flat_bundle_dir / str(lane_key)
        prev_path = lane_dir / "04_prev_final.png"
        new_path = lane_dir / "03_final.png"
        if not prev_path.exists() or not new_path.exists():
            continue
        metrics = _compute_diff_metrics(prev_path, new_path)
        rows.append(
            {
                "scenario": scenario,
                "frame": "frame_final",
                "mean_abs_diff": metrics["mean_abs_diff"],
                "rms_diff": metrics["rms_diff"],
                "changed_px_pct": metrics["changed_px_pct"],
                "changed_px_pct_header": metrics["changed_px_pct_header"],
                "changed_px_pct_transcript": metrics["changed_px_pct_transcript"],
                "changed_px_pct_footer": metrics["changed_px_pct_footer"],
                "dominant_band": metrics["dominant_band"],
                "diff_bbox": metrics["diff_bbox"],
            }
        )

    rows_sorted = sorted(rows, key=lambda row: float(row.get("changed_px_pct", 0.0)), reverse=True)
    payload = {
        "schema_version": "phase5_visual_drift_rows_v2",
        "flat_bundle_dir": str(flat_bundle_dir),
        "count": len(rows_sorted),
        "rows_sorted": rows_sorted,
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"rows={len(rows_sorted)}")
    print(f"json={out_json}")


if __name__ == "__main__":
    main()
