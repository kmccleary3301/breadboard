#!/usr/bin/env python3
"""
Capture and evaluate screenshot ground-truth against either:
- live tmux targets, or
- frozen ANSI fixtures.

This tool performs deterministic, evidence-rich evaluation:
1) optional preflight lock checks,
2) repeated capture runs against fixed tmux targets,
3) metric computation (MAE/RMSE/dimension drift) + heatmaps,
4) threshold + run-to-run variance gating,
5) JSON + Markdown reports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat
from diff_terminal_semantics import compare_runs as compare_semantic_runs
from render_parity_diagnostics import summarize_row_occupancy_from_paths
from tmux_capture_render_profile import DEFAULT_RENDER_PROFILE_ID
from tmux_capture_render_profile import profile_lock_manifest
from tmux_capture_render_profile import resolve_render_profile


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = ROOT.parent
GROUND_TRUTH_SAMPLES = (
    WORKSPACE_ROOT / "docs_tmp" / "E2E_tooling" / "screenshot_rendering" / "ground_truth_samples"
)
DEFAULT_OUT_ROOT = (
    WORKSPACE_ROOT / "docs_tmp" / "E2E_tooling" / "screenshot_rendering" / "ground_truth_eval" / "live_recheck"
)
DEFAULT_BASELINE = WORKSPACE_ROOT / "docs_tmp" / "cli_phase_5" / "RENDER_BASELINE_LOCK_V1.json"
DEFAULT_THRESHOLDS = (
    WORKSPACE_ROOT / "docs_tmp" / "cli_phase_5" / "RENDER_GROUND_TRUTH_THRESHOLDS_V1.json"
)

TARGETS = {
    "screenshot_ground_truth_1": GROUND_TRUTH_SAMPLES / "crush_01.png",
    "screenshot_ground_truth_2": GROUND_TRUTH_SAMPLES / "opencode_01.png",
    "screenshot_ground_truth_3": GROUND_TRUTH_SAMPLES / "claude_code_01.png",
}

EVAL_SCHEMA_VERSION = "ground_truth_eval_run_v1"
FROZEN_FIXTURE_SCHEMA_VERSION = "ground_truth_frozen_ansi_fixture_v1"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _canonical_json_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _run_capture(target: str, out_png: Path, render_profile: str) -> None:
    cmd = [
        "python3",
        str(ROOT / "scripts" / "tmux_capture_to_png.py"),
        "--target",
        target,
        "--render-profile",
        render_profile,
        "--keep",
        "--out",
        str(out_png),
    ]
    subprocess.run(cmd, check=True, cwd=str(ROOT))


def _run_capture_from_ansi(
    *,
    ansi_path: Path,
    cols: int,
    rows: int,
    out_png: Path,
    render_profile: str,
) -> None:
    local_ansi_path = out_png.with_suffix(".ansi")
    if ansi_path.resolve() != local_ansi_path.resolve():
        local_ansi_path.write_bytes(ansi_path.read_bytes())
    cmd = [
        "python3",
        str(ROOT / "scripts" / "tmux_capture_to_png.py"),
        "--ansi",
        str(local_ansi_path),
        "--cols",
        str(cols),
        "--rows",
        str(rows),
        "--render-profile",
        render_profile,
        "--snapshot",
        "--keep",
        "--out",
        str(out_png),
    ]
    subprocess.run(cmd, check=True, cwd=str(ROOT))


def _run_preflight(baseline: Path, out_json: Path) -> dict[str, Any]:
    cmd = [
        "python3",
        str(ROOT / "scripts" / "render_preflight_check.py"),
        "--baseline",
        str(baseline),
        "--out",
        str(out_json),
    ]
    subprocess.run(cmd, check=True, cwd=str(ROOT))
    return json.loads(out_json.read_text(encoding="utf-8"))


def _open_rgb(path: Path) -> Image.Image:
    return Image.open(path).convert("RGB")


def _parse_hex_rgb(value: str) -> tuple[int, int, int]:
    raw = str(value or "").strip()
    if raw.startswith("#"):
        raw = raw[1:]
    if len(raw) != 6:
        raise ValueError(f"invalid hex rgb value: {value!r}")
    return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))


def _annotate_diagnostic_heatmap(img: Image.Image) -> Image.Image:
    out = img.copy().convert("RGB")
    draw = ImageDraw.Draw(out)
    label = "DIAGNOSTIC DIFF HEATMAP (NOT A RENDERED SCREENSHOT)"
    try:
        font = ImageFont.truetype(
            str(ROOT / "renderer_assets" / "fonts" / "DejaVuSansMono-Bold.ttf"),
            16,
        )
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), label, font=font)
    box_w = bbox[2] - bbox[0]
    box_h = bbox[3] - bbox[1]
    margin = 10
    draw.rectangle(
        (margin - 6, margin - 6, margin + box_w + 6, margin + box_h + 6),
        fill=(0, 0, 0),
    )
    draw.text((margin, margin), label, fill=(255, 80, 160), font=font)
    return out


def _compute_metrics(render_png: Path, gt_png: Path, heatmap_path: Path, heat_scale: int = 4) -> dict[str, Any]:
    render_img = _open_rgb(render_png)
    gt_img = _open_rgb(gt_png)

    overlap_w = min(render_img.width, gt_img.width)
    overlap_h = min(render_img.height, gt_img.height)
    render_overlap = render_img.crop((0, 0, overlap_w, overlap_h))
    gt_overlap = gt_img.crop((0, 0, overlap_w, overlap_h))

    diff = ImageChops.difference(render_overlap, gt_overlap)
    stat = ImageStat.Stat(diff)
    mae_channels = [float(v) for v in stat.mean]
    rms_channels = [float(v) for v in stat.rms]
    mae_rgb = sum(mae_channels) / 3.0
    rmse_rgb = math.sqrt(sum((v * v for v in rms_channels)) / 3.0)

    heat = Image.eval(diff, lambda px: min(255, int(px * heat_scale)))
    heat = _annotate_diagnostic_heatmap(heat)
    heatmap_path.parent.mkdir(parents=True, exist_ok=True)
    heat.save(heatmap_path)

    delta_w = render_img.width - gt_img.width
    delta_h = render_img.height - gt_img.height
    dim_abs_sum = abs(delta_w) + abs(delta_h)

    return {
        "render_size": [render_img.width, render_img.height],
        "gt_size": [gt_img.width, gt_img.height],
        "overlap_size": [overlap_w, overlap_h],
        "delta_size": [delta_w, delta_h],
        "dim_abs_sum": dim_abs_sum,
        "mae_rgb": round(mae_rgb, 4),
        "rmse_rgb": round(rmse_rgb, 4),
        "mae_channels": [round(v, 4) for v in mae_channels],
        "rms_channels": [round(v, 4) for v in rms_channels],
        "heatmap_path": str(heatmap_path),
    }


def _load_thresholds(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"thresholds must be JSON object: {path}")
    return payload


def _load_frozen_fixture_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"fixture manifest must be JSON object: {path}")
    schema_version = str(payload.get("schema_version") or "")
    if schema_version != FROZEN_FIXTURE_SCHEMA_VERSION:
        raise ValueError(
            f"fixture manifest schema_version must be `{FROZEN_FIXTURE_SCHEMA_VERSION}`: {path}"
        )
    targets = payload.get("targets")
    if not isinstance(targets, dict):
        raise ValueError(f"fixture manifest missing `targets` object: {path}")
    normalized: dict[str, dict[str, Any]] = {}
    for target in TARGETS:
        row = targets.get(target)
        if not isinstance(row, dict):
            raise ValueError(f"fixture manifest missing target `{target}`: {path}")
        ansi_path = Path(str(row.get("ansi") or "")).expanduser().resolve()
        txt_value = str(row.get("txt") or "").strip()
        txt_path = Path(txt_value).expanduser().resolve() if txt_value else None
        cols = int(row.get("cols", 0) or 0)
        rows = int(row.get("rows", 0) or 0)
        if not ansi_path.exists():
            raise FileNotFoundError(f"fixture ANSI path missing for `{target}`: {ansi_path}")
        if cols <= 0 or rows <= 0:
            raise ValueError(f"fixture `{target}` has invalid cols/rows in manifest: {path}")
        normalized[target] = {
            "ansi_path": ansi_path,
            "txt_path": txt_path,
            "cols": cols,
            "rows": rows,
        }
    return {"manifest": payload, "targets": normalized}


def _threshold_for_target(thresholds: dict[str, Any], target: str) -> dict[str, float]:
    default = thresholds.get("default", {})
    target_overrides = (thresholds.get("targets", {}) or {}).get(target, {})
    merged = {**default, **target_overrides}
    return {
        "max_mae_rgb": float(merged.get("max_mae_rgb", 9999)),
        "max_rmse_rgb": float(merged.get("max_rmse_rgb", 9999)),
        "max_dim_abs_sum": float(merged.get("max_dim_abs_sum", 9999)),
        "max_missing_text_rows": float(merged.get("max_missing_text_rows", 9999)),
        "max_extra_render_rows": float(merged.get("max_extra_render_rows", 9999)),
        "max_row_span_delta": float(merged.get("max_row_span_delta", 9999)),
    }


def _evaluate_thresholds(metrics: dict[str, Any], limits: dict[str, float]) -> dict[str, Any]:
    row_occupancy = metrics.get("row_occupancy") or {}
    checks = {
        "mae_rgb": metrics["mae_rgb"] <= limits["max_mae_rgb"],
        "rmse_rgb": metrics["rmse_rgb"] <= limits["max_rmse_rgb"],
        "dim_abs_sum": metrics["dim_abs_sum"] <= limits["max_dim_abs_sum"],
        "missing_text_rows": float(row_occupancy.get("missing_count", 9999)) <= limits["max_missing_text_rows"],
        "extra_render_rows": float(row_occupancy.get("extra_count", 9999)) <= limits["max_extra_render_rows"],
        "row_span_delta": float(row_occupancy.get("row_span_delta", 9999)) <= limits["max_row_span_delta"],
    }
    return {
        "limits": limits,
        "checks": checks,
        "pass": all(checks.values()),
    }


def _aggregate_target(repeats: list[dict[str, Any]]) -> dict[str, Any]:
    maes = [float(item["metrics"]["mae_rgb"]) for item in repeats]
    rmses = [float(item["metrics"]["rmse_rgb"]) for item in repeats]
    dims = [int(item["metrics"]["dim_abs_sum"]) for item in repeats]
    missing_counts = [int((item["metrics"].get("row_occupancy") or {}).get("missing_count", 0)) for item in repeats]
    extra_counts = [int((item["metrics"].get("row_occupancy") or {}).get("extra_count", 0)) for item in repeats]
    span_deltas = [int((item["metrics"].get("row_occupancy") or {}).get("row_span_delta", 0)) for item in repeats]

    def _std(values: list[float]) -> float:
        return 0.0 if len(values) < 2 else float(statistics.pstdev(values))

    return {
        "runs": len(repeats),
        "mae_rgb": {
            "mean": round(float(statistics.fmean(maes)), 4),
            "min": round(min(maes), 4),
            "max": round(max(maes), 4),
            "stddev": round(_std(maes), 4),
        },
        "rmse_rgb": {
            "mean": round(float(statistics.fmean(rmses)), 4),
            "min": round(min(rmses), 4),
            "max": round(max(rmses), 4),
            "stddev": round(_std(rmses), 4),
        },
        "dim_abs_sum": {
            "mean": round(float(statistics.fmean(dims)), 4),
            "min": int(min(dims)),
            "max": int(max(dims)),
            "range": int(max(dims) - min(dims)),
        },
        "row_occupancy": {
            "missing_count": {
                "mean": round(float(statistics.fmean(missing_counts)), 4),
                "min": int(min(missing_counts)),
                "max": int(max(missing_counts)),
                "range": int(max(missing_counts) - min(missing_counts)),
            },
            "extra_count": {
                "mean": round(float(statistics.fmean(extra_counts)), 4),
                "min": int(min(extra_counts)),
                "max": int(max(extra_counts)),
                "range": int(max(extra_counts) - min(extra_counts)),
            },
            "row_span_delta": {
                "mean": round(float(statistics.fmean(span_deltas)), 4),
                "min": int(min(span_deltas)),
                "max": int(max(span_deltas)),
                "range": int(max(span_deltas) - min(span_deltas)),
            },
        },
    }


def _evaluate_variance(
    aggregate: dict[str, Any], variance_limits: dict[str, float]
) -> dict[str, Any]:
    checks = {
        "mae_rgb_stddev": aggregate["mae_rgb"]["stddev"] <= float(
            variance_limits.get("max_mae_rgb_stddev", 9999.0)
        ),
        "rmse_rgb_stddev": aggregate["rmse_rgb"]["stddev"] <= float(
            variance_limits.get("max_rmse_rgb_stddev", 9999.0)
        ),
        "dim_abs_sum_range": aggregate["dim_abs_sum"]["range"] <= float(
            variance_limits.get("max_dim_abs_sum_range", 9999.0)
        ),
    }
    return {"limits": variance_limits, "checks": checks, "pass": all(checks.values())}


def _write_markdown(
    run_dir: Path,
    *,
    capture_source: str,
    render_profile: str,
    repeats: int,
    thresholds_path: Path,
    preflight_path: Path | None,
    frozen_fixture_manifest: Path | None,
    results: dict[str, Any],
) -> None:
    lines = [
        "# Ground Truth Live Recheck",
        "",
        f"- Capture source: `{capture_source}`",
        f"- Timestamp: `{run_dir.name}`",
        f"- Render profile: `{render_profile}`",
        f"- Repeats: `{repeats}`",
        f"- Thresholds: `{thresholds_path}`",
    ]
    if preflight_path is not None:
        lines.append(f"- Preflight report: `{preflight_path}`")
    if frozen_fixture_manifest is not None:
        lines.append(f"- Frozen fixture manifest: `{frozen_fixture_manifest}`")
    lines.extend(["", "## Target Metrics"])

    for target in TARGETS:
        target_block = results["targets"][target]
        agg = target_block["aggregate"]
        t_eval = target_block["threshold_eval"]
        v_eval = target_block["variance_eval"]
        lines.extend(
            [
                f"### {target}",
                f"- pass: `{target_block['pass']}`",
                f"- threshold pass: `{t_eval['pass']}`",
                f"- variance pass: `{v_eval['pass']}`",
                f"- MAE mean/min/max/stddev: `{agg['mae_rgb']['mean']}` / `{agg['mae_rgb']['min']}` / `{agg['mae_rgb']['max']}` / `{agg['mae_rgb']['stddev']}`",
                f"- RMSE mean/min/max/stddev: `{agg['rmse_rgb']['mean']}` / `{agg['rmse_rgb']['min']}` / `{agg['rmse_rgb']['max']}` / `{agg['rmse_rgb']['stddev']}`",
                f"- dim abs sum mean/min/max/range: `{agg['dim_abs_sum']['mean']}` / `{agg['dim_abs_sum']['min']}` / `{agg['dim_abs_sum']['max']}` / `{agg['dim_abs_sum']['range']}`",
                f"- row occupancy missing mean/min/max: `{agg['row_occupancy']['missing_count']['mean']}` / `{agg['row_occupancy']['missing_count']['min']}` / `{agg['row_occupancy']['missing_count']['max']}`",
                f"- row occupancy extra mean/min/max: `{agg['row_occupancy']['extra_count']['mean']}` / `{agg['row_occupancy']['extra_count']['min']}` / `{agg['row_occupancy']['extra_count']['max']}`",
                f"- row span delta mean/min/max: `{agg['row_occupancy']['row_span_delta']['mean']}` / `{agg['row_occupancy']['row_span_delta']['min']}` / `{agg['row_occupancy']['row_span_delta']['max']}`",
                "",
            ]
        )

    lines.extend(
        [
            "## Overall",
            f"- overall pass: `{results['overall_pass']}`",
            "",
            "## Artifacts",
            "- `comparison_metrics.json`",
            "- `comparison_metrics.md`",
            "- `preflight.json` (if preflight enabled)",
            "- per-repeat captures under `repeat_XX/renders/` (`.png`, `.txt`, `.ansi`, `.render_lock.json`, `.row_parity.json`)",
            "- per-repeat diagnostics under `repeat_XX/diagnostics/` (`*.DIAGNOSTIC_diff_heat_x4.png`)",
            "",
        ]
    )
    (run_dir / "comparison_metrics.md").write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--capture-source",
        choices=("live-tmux", "frozen-ansi"),
        default="live-tmux",
        help="capture source for render generation",
    )
    parser.add_argument(
        "--frozen-fixture-manifest",
        default="",
        help="required when --capture-source=frozen-ansi",
    )
    parser.add_argument("--render-profile", default=DEFAULT_RENDER_PROFILE_ID)
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--thresholds", default=str(DEFAULT_THRESHOLDS))
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument(
        "--semantic-reference-run",
        default="",
        help="Optional reference eval run dir for differential semantic compare.",
    )
    parser.add_argument(
        "--semantic-strict",
        action="store_true",
        help="Exit non-zero if semantic diff vs --semantic-reference-run is not strict-equal.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.repeats <= 0:
        raise ValueError("--repeats must be > 0")

    out_root = Path(args.out_root).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    run_id = args.run_id.strip() or time.strftime("%Y%m%d-%H%M%S")
    run_dir = out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    thresholds_path = Path(args.thresholds).expanduser().resolve()
    if not thresholds_path.exists():
        raise FileNotFoundError(f"thresholds file missing: {thresholds_path}")
    thresholds = _load_thresholds(thresholds_path)
    profile = resolve_render_profile(args.render_profile)
    if profile is None:
        raise ValueError(
            "ground-truth evaluator does not support legacy render profile; use locked profile id"
        )
    if profile.cell_height <= 0:
        raise ValueError(
            f"render profile {profile.id} missing deterministic cell_height; cannot compute row occupancy parity"
        )
    bg_default = _parse_hex_rgb(profile.bg)

    preflight_path: Path | None = None
    preflight_payload: dict[str, Any] | None = None
    baseline_path: Path | None = None
    frozen_fixture_manifest_path: Path | None = None
    frozen_targets: dict[str, dict[str, Any]] = {}

    if args.capture_source == "frozen-ansi":
        manifest_arg = str(args.frozen_fixture_manifest or "").strip()
        if not manifest_arg:
            raise ValueError(
                "--frozen-fixture-manifest is required when --capture-source=frozen-ansi"
            )
        frozen_fixture_manifest_path = Path(manifest_arg).expanduser().resolve()
        loaded = _load_frozen_fixture_manifest(frozen_fixture_manifest_path)
        frozen_targets = dict(loaded["targets"])

    if not args.skip_preflight and args.capture_source == "live-tmux":
        baseline_path = Path(args.baseline).expanduser().resolve()
        preflight_path = run_dir / "preflight.json"
        preflight_payload = _run_preflight(baseline=baseline_path, out_json=preflight_path)

    deterministic_targets: dict[str, Any] = {}
    for target, sample in TARGETS.items():
        base_target = {
            "sample_png": str(sample),
            "sample_sha256": _sha256_file(sample),
        }
        if args.capture_source == "frozen-ansi":
            fixture = frozen_targets[target]
            deterministic_targets[target] = {
                **base_target,
                "capture_source": "frozen-ansi",
                "fixture_ansi_path": str(fixture["ansi_path"]),
                "fixture_ansi_sha256": _sha256_file(fixture["ansi_path"]),
                "fixture_txt_path": str(fixture["txt_path"]) if fixture["txt_path"] is not None else "",
                "fixture_txt_sha256": (
                    _sha256_file(fixture["txt_path"]) if fixture["txt_path"] is not None and fixture["txt_path"].exists() else ""
                ),
                "cols": int(fixture["cols"]),
                "rows": int(fixture["rows"]),
            }
        else:
            deterministic_targets[target] = {
                **base_target,
                "capture_source": "live-tmux",
                "session": target,
                "cols": 153,
                "rows": 30,
            }
    deterministic_inputs = {
        "schema_version": "ground_truth_deterministic_inputs_v1",
        "capture_source": str(args.capture_source),
        "frozen_fixture_manifest": str(frozen_fixture_manifest_path) if frozen_fixture_manifest_path is not None else "",
        "frozen_fixture_manifest_sha256": (
            _sha256_file(frozen_fixture_manifest_path) if frozen_fixture_manifest_path is not None else ""
        ),
        "evaluator_script_sha256": _sha256_file(Path(__file__).resolve()),
        "render_profile": str(args.render_profile),
        "render_profile_manifest": profile_lock_manifest(profile),
        "repeats": int(args.repeats),
        "thresholds_path": str(thresholds_path),
        "thresholds_sha256": _sha256_file(thresholds_path),
        "baseline_path": str(baseline_path) if baseline_path is not None else "",
        "baseline_sha256": _sha256_file(baseline_path) if baseline_path is not None else "",
        "targets": deterministic_targets,
    }
    deterministic_input_hash = _canonical_json_hash(deterministic_inputs)

    per_target_results: dict[str, Any] = {}
    for target, sample in TARGETS.items():
        if not sample.exists():
            raise FileNotFoundError(f"Missing ground-truth sample: {sample}")

        repeat_records: list[dict[str, Any]] = []
        limits = _threshold_for_target(thresholds, target)
        for repeat_idx in range(1, args.repeats + 1):
            repeat_dir = run_dir / f"repeat_{repeat_idx:02d}"
            repeat_dir.mkdir(parents=True, exist_ok=True)
            render_dir = repeat_dir / "renders"
            diagnostic_dir = repeat_dir / "diagnostics"
            render_dir.mkdir(parents=True, exist_ok=True)
            diagnostic_dir.mkdir(parents=True, exist_ok=True)

            out_png = render_dir / f"{target}.png"
            if args.capture_source == "frozen-ansi":
                fixture = frozen_targets[target]
                _run_capture_from_ansi(
                    ansi_path=fixture["ansi_path"],
                    cols=int(fixture["cols"]),
                    rows=int(fixture["rows"]),
                    out_png=out_png,
                    render_profile=args.render_profile,
                )
            else:
                _run_capture(target=target, out_png=out_png, render_profile=args.render_profile)
            heatmap_path = diagnostic_dir / f"{target}.DIAGNOSTIC_diff_heat_x4.png"
            metrics = _compute_metrics(
                render_png=out_png,
                gt_png=sample,
                heatmap_path=heatmap_path,
                heat_scale=4,
            )
            metrics["row_occupancy"] = summarize_row_occupancy_from_paths(
                render_png=out_png,
                render_txt=out_png.with_suffix(".txt"),
                bg_default=bg_default,
                cell_height=int(profile.cell_height),
                pad_y=int(profile.pad_y),
                declared_rows=int(
                    frozen_targets[target]["rows"] if args.capture_source == "frozen-ansi" else 30
                ),
            )
            render_lock_json = out_png.with_suffix(".render_lock.json")
            if not render_lock_json.exists():
                raise FileNotFoundError(f"render_lock sidecar missing for {target} repeat {repeat_idx}: {render_lock_json}")
            row_parity_summary_json = out_png.with_suffix(".row_parity.json")
            if not row_parity_summary_json.exists():
                raise FileNotFoundError(
                    f"row parity summary sidecar missing for {target} repeat {repeat_idx}: {row_parity_summary_json}"
                )
            threshold_eval = _evaluate_thresholds(metrics=metrics, limits=limits)
            repeat_records.append(
                {
                    "repeat": repeat_idx,
                    "png": str(out_png),
                    "txt": str(out_png.with_suffix(".txt")),
                    "ansi": str(out_png.with_suffix(".ansi")),
                    "render_lock_json": str(render_lock_json),
                    "render_lock_sha256": _sha256_file(render_lock_json),
                    "row_parity_summary_json": str(row_parity_summary_json),
                    "row_parity_summary_sha256": _sha256_file(row_parity_summary_json),
                    "diagnostic_heatmap_png": str(heatmap_path),
                    "metrics": metrics,
                    "threshold_eval": threshold_eval,
                }
            )

        aggregate = _aggregate_target(repeat_records)
        variance_limits = dict(thresholds.get("variance", {}))
        variance_eval = _evaluate_variance(aggregate=aggregate, variance_limits=variance_limits)
        pass_repeats = all(bool(r["threshold_eval"]["pass"]) for r in repeat_records)
        target_pass = bool(pass_repeats and variance_eval["pass"])
        per_target_results[target] = {
            "sample_png": str(sample),
            "limits": limits,
            "variance_limits": variance_limits,
            "repeats": repeat_records,
            "aggregate": aggregate,
            "threshold_eval": {"pass": pass_repeats},
            "variance_eval": variance_eval,
            "pass": target_pass,
        }

    overall_pass = all(bool(per_target_results[t]["pass"]) for t in TARGETS)
    payload = {
        "schema_version": EVAL_SCHEMA_VERSION,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "capture_source": str(args.capture_source),
        "frozen_fixture_manifest": str(frozen_fixture_manifest_path) if frozen_fixture_manifest_path is not None else "",
        "render_profile": args.render_profile,
        "repeats": args.repeats,
        "thresholds_path": str(thresholds_path),
        "deterministic_inputs": deterministic_inputs,
        "deterministic_input_hash": deterministic_input_hash,
        "overall_pass": overall_pass,
        "preflight": preflight_payload,
        "targets": per_target_results,
    }

    semantic_payload: dict[str, Any] | None = None

    metrics_json = run_dir / "comparison_metrics.json"
    metrics_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _write_markdown(
        run_dir=run_dir,
        capture_source=str(args.capture_source),
        render_profile=args.render_profile,
        repeats=args.repeats,
        thresholds_path=thresholds_path,
        preflight_path=preflight_path,
        frozen_fixture_manifest=frozen_fixture_manifest_path,
        results=payload,
    )

    if args.semantic_reference_run:
        semantic_reference = Path(args.semantic_reference_run).expanduser().resolve()
        semantic_payload = compare_semantic_runs(
            semantic_reference,
            run_dir,
            mode="eval-run",
        )
        semantic_path = run_dir / "semantic_diff.json"
        semantic_md_path = run_dir / "semantic_diff.md"
        semantic_path.write_text(json.dumps(semantic_payload, indent=2) + "\n", encoding="utf-8")
        lines = [
            "# Semantic Diff",
            "",
            f"- reference: `{semantic_reference}`",
            f"- candidate: `{run_dir}`",
            f"- strict_equal: `{semantic_payload.get('strict_equal')}`",
            f"- mismatch_count: `{semantic_payload.get('mismatch_count')}`",
            "",
        ]
        semantic_md_path.write_text("\n".join(lines), encoding="utf-8")
        payload["semantic_diff"] = {
            "reference_run_dir": str(semantic_reference),
            "json": str(semantic_path),
            "md": str(semantic_md_path),
            "strict_equal": bool(semantic_payload.get("strict_equal", False)),
            "mismatch_count": int(semantic_payload.get("mismatch_count", 0) or 0),
        }
        metrics_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        _write_markdown(
            run_dir=run_dir,
            capture_source=str(args.capture_source),
            render_profile=args.render_profile,
            repeats=args.repeats,
            thresholds_path=thresholds_path,
            preflight_path=preflight_path,
            frozen_fixture_manifest=frozen_fixture_manifest_path,
            results=payload,
        )

    print(str(run_dir))
    if not overall_pass:
        raise SystemExit(2)
    if args.semantic_strict and semantic_payload is not None and not bool(
        semantic_payload.get("strict_equal", False)
    ):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
