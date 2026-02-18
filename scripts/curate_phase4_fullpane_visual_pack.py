#!/usr/bin/env python3
"""
Curate a compact visual review pack for phase4 fullpane replay runs.

For each scenario lane, this tool copies:
- landing frame (frame 1)
- active frame (configurable hint index)
- final frame (last frame)
- previous baseline final frame (prior run when available, fallback image optional)

It also emits:
- previous-vs-new side-by-side PNG
- amplified heatmap diff PNG (x3)
- JSON metrics and pack manifest/index
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops


@dataclass(frozen=True)
class LaneSpec:
    key: str
    scenario_dir: str
    scenario_id: str
    active_hint_index: int
    active_hint_text: str
    fallback_prev_png: str = ""


DEFAULT_SPECS: tuple[LaneSpec, ...] = (
    LaneSpec(
        key="streaming",
        scenario_dir="streaming_v1_fullpane_v8",
        scenario_id="phase4_replay/streaming_v1_fullpane_v8",
        active_hint_index=6,
        active_hint_text="Deciphering",
    ),
    LaneSpec(
        key="todo",
        scenario_dir="todo_preview_v1_fullpane_v7",
        scenario_id="phase4_replay/todo_preview_v1_fullpane_v7",
        active_hint_index=7,
        active_hint_text="Todos",
    ),
    LaneSpec(
        key="subagents",
        scenario_dir="subagents_v1_fullpane_v7",
        scenario_id="phase4_replay/subagents_v1_fullpane_v7",
        active_hint_index=12,
        active_hint_text="Background tasks",
    ),
    LaneSpec(
        key="everything",
        scenario_dir="everything_showcase_v1_fullpane_v1",
        scenario_id="phase4_replay/everything_showcase_v1_fullpane_v1",
        active_hint_index=8,
        active_hint_text="Markdown Showcase",
        fallback_prev_png="docs_tmp/tmux_captures/visual_review_pack_20260217_phase4_fullpane_tight_v7/everything_showcase_repro_v7_raw.png",
    ),
)


def _pick_workspace_root(repo_root: Path) -> Path:
    candidates = (repo_root, repo_root.parent)
    for base in candidates:
        if (base / "docs_tmp").exists() and (base / "config").exists():
            return base
    for base in candidates:
        if (base / "docs_tmp").exists():
            return base
    return repo_root.parent


def _resolve_workspace_path(path: Path, workspace_root: Path, repo_root: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    for base in (workspace_root, repo_root, repo_root.parent):
        candidate = (base / path).resolve()
        if candidate.exists():
            return candidate
    return (workspace_root / path).resolve()


def _display_path(path: Path, workspace_root: Path) -> str:
    try:
        return str(path.relative_to(workspace_root))
    except ValueError:
        return str(path)


def _list_runs(base: Path) -> list[Path]:
    return [p for p in sorted(base.glob("*")) if p.is_dir()]


def _frame_path(run_dir: Path, idx: int, ext: str) -> Path:
    return run_dir / "frames" / f"frame_{idx:04d}.{ext}"


def _frame_count(run_dir: Path) -> int:
    return len(list((run_dir / "frames").glob("*.png")))


def _copy_frame_triplet(run_dir: Path, idx: int, out_dir: Path, stem: str, pack_root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"index": idx}
    for ext in ("png", "txt", "ansi"):
        src = _frame_path(run_dir, idx, ext)
        if src.exists():
            dst = out_dir / f"{stem}.{ext}"
            shutil.copy2(src, dst)
            result[ext] = str(dst.relative_to(pack_root))
        else:
            result[ext] = None
    return result


def _side_by_side(left: Image.Image, right: Image.Image, bg: tuple[int, int, int, int]) -> Image.Image:
    canvas = Image.new("RGBA", (left.width + right.width, max(left.height, right.height)), bg)
    canvas.paste(left, (0, 0))
    canvas.paste(right, (left.width, 0))
    return canvas


def _diff_heat_and_metrics(prev_img: Image.Image, new_img: Image.Image) -> tuple[Image.Image, Image.Image, dict[str, Any]]:
    new_fit = new_img.resize(prev_img.size, Image.Resampling.BICUBIC)
    diff = ImageChops.difference(prev_img, new_fit).convert("RGB")
    heat = Image.eval(diff, lambda v: min(255, int(v * 3)))

    px = list(diff.getdata())
    n = max(1, len(px))
    mean_abs = [sum(p[c] for p in px) / n for c in range(3)]
    rms = [math.sqrt(sum((p[c] ** 2) for p in px) / n) for c in range(3)]

    metrics = {
        "mean_abs_diff_rgb": [round(v, 3) for v in mean_abs],
        "rms_diff_rgb": [round(v, 3) for v in rms],
        "prev_size": [prev_img.width, prev_img.height],
        "new_size": [new_img.width, new_img.height],
        "new_fit_size": [new_fit.width, new_fit.height],
    }
    return new_fit, heat, metrics


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Curate phase4 fullpane visual review pack.")
    p.add_argument("--out-dir", required=True, help="output pack directory")
    p.add_argument(
        "--scenarios-root",
        default="docs_tmp/tmux_captures/scenarios/phase4_replay",
        help="root containing scenario run dirs",
    )
    p.add_argument(
        "--reference",
        default="docs_tmp/tui_design/everything_showcase_v2.png",
        help="reference screenshot copied into pack and hash-tracked in index",
    )
    p.add_argument(
        "--allow-missing",
        action="store_true",
        help="skip lanes missing run data instead of failing",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    workspace_root = _pick_workspace_root(repo_root)

    out_dir = Path(args.out_dir).expanduser().resolve()
    scenarios_root = _resolve_workspace_path(Path(args.scenarios_root).expanduser(), workspace_root, repo_root)
    reference = _resolve_workspace_path(Path(args.reference).expanduser(), workspace_root, repo_root)

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {}
    summary_rows: list[tuple[str, str, str, str, dict[str, Any]]] = []

    for spec in DEFAULT_SPECS:
        run_base = scenarios_root / spec.scenario_dir
        runs = _list_runs(run_base) if run_base.exists() else []
        if not runs:
            if args.allow_missing:
                continue
            raise FileNotFoundError(f"no runs found for {spec.scenario_dir}: {run_base}")

        new_run = runs[-1]
        prev_run = runs[-2] if len(runs) >= 2 else None
        new_frames = _frame_count(new_run)
        if new_frames <= 0:
            if args.allow_missing:
                continue
            raise ValueError(f"no PNG frames in run: {new_run}")

        lane_dir = out_dir / spec.key
        lane_dir.mkdir(parents=True, exist_ok=True)

        landing_idx = 1
        active_idx = min(max(1, int(spec.active_hint_index)), new_frames)
        final_idx = new_frames

        landing = _copy_frame_triplet(new_run, landing_idx, lane_dir, "01_landing", out_dir)
        active = _copy_frame_triplet(new_run, active_idx, lane_dir, "02_active", out_dir)
        final = _copy_frame_triplet(new_run, final_idx, lane_dir, "03_final", out_dir)
        new_final_png = lane_dir / "03_final.png"

        prev_source = ""
        prev_final: dict[str, Any]
        if prev_run is not None:
            prev_frames = _frame_count(prev_run)
            prev_idx = max(1, prev_frames)
            prev_final = _copy_frame_triplet(prev_run, prev_idx, lane_dir, "04_prev_final", out_dir)
            prev_source = _display_path(prev_run, workspace_root)
        else:
            fallback = _resolve_workspace_path(Path(spec.fallback_prev_png), workspace_root, repo_root)
            if not fallback.exists():
                # Deterministic fallback: use the current final frame as synthetic previous baseline.
                if not new_final_png.exists():
                    if args.allow_missing:
                        continue
                    raise FileNotFoundError(
                        f"no previous run or fallback for {spec.scenario_dir}, and new final missing: {new_final_png}"
                    )
                dst = lane_dir / "04_prev_final.png"
                shutil.copy2(new_final_png, dst)
                prev_final = {
                    "index": None,
                    "png": str(dst.relative_to(out_dir)),
                    "txt": None,
                    "ansi": None,
                }
                prev_source = "self:03_final.png"
            else:
                dst = lane_dir / "04_prev_final.png"
                shutil.copy2(fallback, dst)
                prev_final = {
                    "index": None,
                    "png": str(dst.relative_to(out_dir)),
                    "txt": None,
                    "ansi": None,
                }
                prev_source = _display_path(fallback, workspace_root)

        prev_final_png = lane_dir / "04_prev_final.png"
        prev_im = Image.open(prev_final_png).convert("RGBA")
        new_im = Image.open(new_final_png).convert("RGBA")

        side = _side_by_side(prev_im, new_im, bg=(31, 36, 48, 255))
        side_path = lane_dir / "compare_prev_vs_new_side_by_side.png"
        side.save(side_path)

        new_fit, heat, metrics = _diff_heat_and_metrics(prev_im, new_im)
        fit_path = lane_dir / "03_final_fit_to_prev.png"
        heat_path = lane_dir / "compare_prev_vs_new_diff_heat_x3.png"
        new_fit.save(fit_path)
        heat.save(heat_path)

        metrics.update(
            {
                "scenario": spec.scenario_id,
                "new_run": _display_path(new_run, workspace_root),
                "prev_source": prev_source,
                "active_hint_text": spec.active_hint_text,
                "selected_frame_indices": {
                    "landing": landing_idx,
                    "active": active_idx,
                    "final": final_idx,
                    "prev_final": prev_final.get("index"),
                },
            }
        )
        metrics_path = lane_dir / "compare_prev_vs_new_metrics.json"
        metrics_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")

        manifest[spec.key] = {
            "scenario": spec.scenario_id,
            "new_run": _display_path(new_run, workspace_root),
            "prev_source": prev_source,
            "new_frame_count": new_frames,
            "selected": {
                "landing": landing,
                "active": active,
                "final": final,
                "prev_final": prev_final,
                "final_fit_to_prev_png": str(fit_path.relative_to(out_dir)),
                "compare_side_by_side_png": str(side_path.relative_to(out_dir)),
                "compare_heat_x3_png": str(heat_path.relative_to(out_dir)),
                "compare_metrics_json": str(metrics_path.relative_to(out_dir)),
            },
        }
        summary_rows.append(
            (
                spec.key,
                spec.scenario_id,
                _display_path(new_run, workspace_root),
                prev_source,
                metrics,
            )
        )

    ref_copy = out_dir / "everything_showcase_ref_v2.png"
    ref_source = _display_path(reference, workspace_root)
    if reference.exists():
        shutil.copy2(reference, ref_copy)
        ref_hash = hashlib.sha256(reference.read_bytes()).hexdigest()
    else:
        fallback_reference = out_dir / "everything" / "03_final.png"
        if not fallback_reference.exists():
            raise FileNotFoundError(
                f"reference screenshot not found: {reference}; fallback also missing: {fallback_reference}"
            )
        shutil.copy2(fallback_reference, ref_copy)
        ref_hash = hashlib.sha256(fallback_reference.read_bytes()).hexdigest()
        ref_source = f"{_display_path(fallback_reference, workspace_root)} (fallback)"

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    lines: list[str] = []
    lines.append("# Visual Review Pack (Phase4 Fullpane Lock V1)")
    lines.append("")
    lines.append("- Date: 2026-02-18")
    lines.append("- Purpose: curated frame set from latest locked-profile fullpane replay runs (landing/active/final), plus previous-vs-new visual diffs.")
    lines.append(f"- Pack path: `{_display_path(out_dir, workspace_root)}`")
    lines.append("- Locked render profile: `phase4_locked_v1`")
    lines.append("- Source manifest: `manifest.json`")
    lines.append("")
    lines.append("## Reference Screenshot Provenance")
    lines.append(f"- `{ref_source}` SHA256: `{ref_hash}`")
    lines.append("- Same hash copy included as `everything_showcase_ref_v2.png`.")
    lines.append("")
    lines.append("## Scenario Summary")
    for key, scenario_id, new_run_rel, prev_source, metrics in summary_rows:
        lines.append(f"### {key}")
        lines.append(f"- Scenario: `{scenario_id}`")
        lines.append(f"- New run: `{new_run_rel}`")
        lines.append(f"- Previous baseline: `{prev_source}`")
        lines.append(
            f"- Diff metrics (prev vs new-fit): mean_abs={metrics['mean_abs_diff_rgb']}, rms={metrics['rms_diff_rgb']}"
        )
        lines.append(f"- Folder: `{key}/`")
        lines.append("")
    lines.append("## Files Per Scenario Folder")
    lines.append("- `01_landing.(png|txt|ansi)`")
    lines.append("- `02_active.(png|txt|ansi)`")
    lines.append("- `03_final.(png|txt|ansi)`")
    lines.append("- `03_final_fit_to_prev.png`")
    lines.append("- `04_prev_final.png` (+ txt/ansi when baseline is a prior scenario run)")
    lines.append("- `compare_prev_vs_new_side_by_side.png`")
    lines.append("- `compare_prev_vs_new_diff_heat_x3.png`")
    lines.append("- `compare_prev_vs_new_metrics.json`")
    lines.append("")
    lines.append("## Notes")
    lines.append("- Streaming active frame targets `Deciphering` state.")
    lines.append("- Todo active frame targets first visible TODO list frame.")
    lines.append("- Subagents active frame targets open `Background tasks` modal state.")
    lines.append("- Everything active frame targets first stable `Markdown Showcase` frame.")
    (out_dir / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
