#!/usr/bin/env python3
"""
Generate focused footer/overlay QC crops (including x3 zoom) for Phase-5 runs.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat


@dataclass(frozen=True)
class Target:
    key: str
    scenario: str
    hint: str
    lane: str


TARGETS = [
    Target("todo", "phase4_replay/todo_preview_v1_fullpane_v7", "Todos", "hard"),
    Target("subagents", "phase4_replay/subagents_v1_fullpane_v7", "Background tasks", "hard"),
    Target("thinking", "phase4_replay/thinking_preview_v1", "[responding]", "nightly"),
]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _collect_runs(signoff_json: Path) -> dict[str, Path]:
    payload = _load_json(signoff_json)
    out: dict[str, Path] = {}
    lanes = payload.get("lanes", {})
    for lane in lanes.values():
        iterations = lane.get("iterations", []) if isinstance(lane, dict) else []
        for iteration in iterations:
            for row in iteration.get("scenario_runs", []) or []:
                scenario = str(row.get("scenario") or "")
                run_dir = str(row.get("run_dir") or "")
                if scenario and run_dir:
                    out[scenario] = Path(run_dir).expanduser().resolve()
    return out


def _index_records(run_dir: Path) -> list[dict[str, Any]]:
    index = run_dir / "index.jsonl"
    rows: list[dict[str, Any]] = []
    for line in index.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if isinstance(obj, dict):
            rows.append(obj)
    if not rows:
        raise ValueError(f"No frame rows in {index}")
    return rows


def _frame_name(row: dict[str, Any]) -> str:
    ansi_rel = str(row.get("ansi") or "")
    if ansi_rel:
        return Path(ansi_rel).stem
    frame = row.get("frame")
    if isinstance(frame, int):
        return f"frame_{frame:04d}"
    if isinstance(frame, str):
        return Path(frame).stem
    raise ValueError("Cannot infer frame name")


def _pick_active_row(run_dir: Path, rows: list[dict[str, Any]], hint: str) -> dict[str, Any]:
    for row in rows:
        text_rel = str(row.get("text") or "")
        if not text_rel:
            continue
        text_path = run_dir / text_rel
        if text_path.exists() and hint in text_path.read_text(encoding="utf-8", errors="replace"):
            return row
    # fallback: second-to-last frame (active-ish state)
    return rows[-2] if len(rows) >= 2 else rows[-1]


def _load_png(run_dir: Path, row: dict[str, Any]) -> Image.Image:
    png_rel = str(row.get("png") or "")
    if not png_rel:
        raise ValueError(f"Missing png in row for {run_dir}")
    png_path = run_dir / png_rel
    if not png_path.exists():
        raise FileNotFoundError(png_path)
    return Image.open(png_path).convert("RGB")


def _resolve_render_lock(run_dir: Path, row: dict[str, Any]) -> dict[str, Any] | None:
    rel = str(row.get("render_lock") or "")
    candidates: list[Path] = []
    if rel:
        candidates.append(run_dir / rel)
    frame = _frame_name(row)
    candidates.append(run_dir / "frames" / f"{frame}.render_lock.json")
    for path in candidates:
        if path.exists():
            return _load_json(path)
    return None


def _footer_crop(img: Image.Image, run_dir: Path, row: dict[str, Any]) -> Image.Image:
    w, h = img.size
    lock = _resolve_render_lock(run_dir, row)
    if lock:
        geom = lock.get("geometry", {}) if isinstance(lock, dict) else {}
        occ = lock.get("row_occupancy", {}) if isinstance(lock, dict) else {}
        diagnostics = occ.get("edge_row_diagnostics", []) if isinstance(occ, dict) else []
        if isinstance(diagnostics, list):
            occupied_rows = []
            for item in diagnostics:
                if not isinstance(item, dict):
                    continue
                edge_count = int(item.get("edge_count") or 0)
                edge_ratio = float(item.get("edge_ratio") or 0.0)
                row_idx = int(item.get("row") or 0)
                if row_idx > 0 and (edge_count > 0 or edge_ratio > 0):
                    occupied_rows.append(row_idx)
            if occupied_rows:
                last_row = max(occupied_rows)
                rows_declared = int(geom.get("rows") or 45)
                cell_h = int(geom.get("cell_height") or max(1, h // max(rows_declared, 1)))
                pad_y = int(geom.get("pad_y") or 0)
                top_row = max(1, last_row - 10)
                bottom_row = min(rows_declared, last_row + 8)
                y0 = max(0, min(h, pad_y + (top_row - 1) * cell_h))
                y1 = max(y0 + 1, min(h, pad_y + bottom_row * cell_h))
                return img.crop((0, y0, w, y1))
    # Fallback when lock metadata is unavailable.
    top = max(0, int(h * 0.42))
    return img.crop((0, top, w, h))


def _save_crops(
    base_img: Image.Image,
    out_prefix: Path,
    run_dir: Path,
    row: dict[str, Any],
) -> tuple[Path, Path, Image.Image]:
    crop = _footer_crop(base_img, run_dir, row)
    crop_path = out_prefix.with_suffix(".png")
    crop.save(crop_path)
    x3 = crop.resize((crop.width * 3, crop.height * 3), Image.Resampling.NEAREST)
    x3_path = out_prefix.with_name(out_prefix.name + "_x3").with_suffix(".png")
    x3.save(x3_path)
    return crop_path, x3_path, crop


def _diff_metrics(a: Image.Image, b: Image.Image) -> tuple[list[float], list[float]]:
    diff = ImageChops.difference(a, b)
    stat = ImageStat.Stat(diff)
    mean = [round(v, 4) for v in stat.mean]
    rms = [round(v, 4) for v in stat.rms]
    return mean, rms


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hard-signoff-json", required=True)
    p.add_argument("--nightly-signoff-json", required=True)
    p.add_argument("--flat-bundle-dir", required=True, help="Existing flat bundle path for prev_final references.")
    p.add_argument("--out-dir", required=True)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    hard_runs = _collect_runs(Path(args.hard_signoff_json).expanduser().resolve())
    nightly_runs = _collect_runs(Path(args.nightly_signoff_json).expanduser().resolve())
    flat_bundle = Path(args.flat_bundle_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    summary: dict[str, Any] = {
        "schema_version": "phase5_footer_qc_pack_v1",
        "hard_signoff_json": str(Path(args.hard_signoff_json).expanduser().resolve()),
        "nightly_signoff_json": str(Path(args.nightly_signoff_json).expanduser().resolve()),
        "flat_bundle_dir": str(flat_bundle),
        "targets": {},
    }

    for t in TARGETS:
        run_map = hard_runs if t.lane == "hard" else nightly_runs
        run_dir = run_map.get(t.scenario)
        if run_dir is None:
            raise KeyError(f"Scenario not found in signoff inputs: {t.scenario}")

        rows = _index_records(run_dir)
        active_row = _pick_active_row(run_dir, rows, t.hint)
        final_row = rows[-1]

        active_img = _load_png(run_dir, active_row)
        final_img = _load_png(run_dir, final_row)

        active_crop, active_crop_x3, _active_crop_img = _save_crops(
            active_img, out_dir / f"{t.key}_active_boosted", run_dir, active_row
        )
        final_crop, final_crop_x3, final_crop_img = _save_crops(
            final_img, out_dir / f"{t.key}_final_boosted", run_dir, final_row
        )

        target_summary: dict[str, Any] = {
            "scenario": t.scenario,
            "run_dir": str(run_dir),
            "active_frame": _frame_name(active_row),
            "final_frame": _frame_name(final_row),
            "active_hint": t.hint,
            "outputs": {
                "active_crop": str(active_crop.relative_to(out_dir)),
                "active_crop_x3": str(active_crop_x3.relative_to(out_dir)),
                "final_crop": str(final_crop.relative_to(out_dir)),
                "final_crop_x3": str(final_crop_x3.relative_to(out_dir)),
            },
        }

        # Diff against previous final where available in flat bundle (todo/subagents).
        prev_final = flat_bundle / t.key / "04_prev_final.png"
        if prev_final.exists():
            prev_img = Image.open(prev_final).convert("RGB")
            prev_crop = _footer_crop(prev_img, run_dir, final_row)
            prev_crop_path = out_dir / f"{t.key}_prev_final_boosted.png"
            prev_crop.save(prev_crop_path)
            prev_crop_x3_path = out_dir / f"{t.key}_prev_final_boosted_x3.png"
            prev_crop.resize(
                (prev_crop.width * 3, prev_crop.height * 3),
                Image.Resampling.NEAREST,
            ).save(prev_crop_x3_path)
            mean_abs, rms = _diff_metrics(final_crop_img, prev_crop)
            target_summary["outputs"]["prev_final_crop"] = str(prev_crop_path.relative_to(out_dir))
            target_summary["outputs"]["prev_final_crop_x3"] = str(prev_crop_x3_path.relative_to(out_dir))
            target_summary["prev_vs_new_final_footer_diff"] = {
                "mean_abs_diff_rgb": mean_abs,
                "rms_diff_rgb": rms,
            }

        summary["targets"][t.key] = target_summary

    out_json = out_dir / "footer_qc_pack.json"
    out_json.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Phase5 Footer QC Pack",
        "",
        f"- hard signoff: `{summary['hard_signoff_json']}`",
        f"- nightly signoff: `{summary['nightly_signoff_json']}`",
        "",
        "## Targets",
        "",
    ]
    for key, item in summary["targets"].items():
        lines.append(f"### {key}")
        lines.append(f"- scenario: `{item['scenario']}`")
        lines.append(f"- run_dir: `{item['run_dir']}`")
        lines.append(f"- active_frame: `{item['active_frame']}`")
        lines.append(f"- final_frame: `{item['final_frame']}`")
        lines.append(f"- active_crop_x3: `{item['outputs']['active_crop_x3']}`")
        lines.append(f"- final_crop_x3: `{item['outputs']['final_crop_x3']}`")
        if "prev_vs_new_final_footer_diff" in item:
            diff = item["prev_vs_new_final_footer_diff"]
            lines.append(f"- prev_vs_new_final_footer_diff mean_abs_rgb: `{diff['mean_abs_diff_rgb']}`")
            lines.append(f"- prev_vs_new_final_footer_diff rms_rgb: `{diff['rms_diff_rgb']}`")
        lines.append("")
    (out_dir / "README.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"footer_qc_pack={out_json}")


if __name__ == "__main__":
    main()
