#!/usr/bin/env python3
"""
Hard-gate footer contrast/legibility for Phase5 footer QC packs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from PIL import Image


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object JSON at {path}")
    return payload


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _infer_contrast(final_crop_path: Path) -> dict[str, Any]:
    try:
        img = Image.open(final_crop_path).convert("RGB")
    except Exception:
        return {}
    px = list(img.getdata())
    if not px:
        return {}
    chans = [[rgb[i] for rgb in px] for i in range(3)]
    bg = [sorted(ch)[len(ch) // 2] for ch in chans]
    deltas = [max(abs(rgb[0] - bg[0]), abs(rgb[1] - bg[1]), abs(rgb[2] - bg[2])) for rgb in px]
    if not deltas:
        return {}
    sorted_d = sorted(deltas)

    def _pct(p: float) -> float:
        idx = int(round((len(sorted_d) - 1) * p))
        idx = max(0, min(len(sorted_d) - 1, idx))
        return float(sorted_d[idx])

    total = max(1, len(deltas))
    return {
        "background_rgb": [int(x) for x in bg],
        "max_channel_delta": float(max(deltas)),
        "p99_channel_delta": _pct(0.99),
        "p995_channel_delta": _pct(0.995),
        "ratio_delta_gt_20": float(sum(1 for d in deltas if d > 20) / total),
        "ratio_delta_gt_40": float(sum(1 for d in deltas if d > 40) / total),
    }


def _resolve_target_contrast(target: dict[str, Any], footer_qc_dir: Path) -> tuple[dict[str, Any], str]:
    contrast = target.get("final_footer_contrast", {})
    if isinstance(contrast, dict) and contrast:
        return contrast, "embedded"
    outputs = target.get("outputs", {})
    if not isinstance(outputs, dict):
        return {}, "missing"
    rel = str(outputs.get("final_crop") or "").strip()
    if not rel:
        return {}, "missing"
    path = (footer_qc_dir / rel).resolve()
    if not path.exists():
        return {}, "missing"
    inferred = _infer_contrast(path)
    return inferred, "inferred" if inferred else "missing"


def build_report(
    *,
    footer_qc_pack: dict[str, Any],
    footer_qc_dir: Path,
    min_p99: float,
    min_ratio_gt20: float,
    blank_p99: float,
    blank_ratio_gt20: float,
) -> dict[str, Any]:
    targets = footer_qc_pack.get("targets", {})
    if not isinstance(targets, dict):
        targets = {}
    rows: list[dict[str, Any]] = []
    for key, raw in sorted(targets.items()):
        target = raw if isinstance(raw, dict) else {}
        contrast, source = _resolve_target_contrast(target, footer_qc_dir)
        p99 = _safe_float(contrast.get("p99_channel_delta"))
        ratio20 = _safe_float(contrast.get("ratio_delta_gt_20"))
        status = "pass"
        reason = "ok"
        if not contrast:
            status = "fail"
            reason = "missing_contrast"
        elif p99 < blank_p99 or ratio20 < blank_ratio_gt20:
            status = "fail"
            reason = "blank_or_severely_under_rendered"
        elif p99 < min_p99 or ratio20 < min_ratio_gt20:
            status = "fail"
            reason = "weak_legibility"
        rows.append(
            {
                "target": key,
                "scenario": str(target.get("scenario") or ""),
                "status": status,
                "reason": reason,
                "contrast_source": source,
                "p99_channel_delta": round(p99, 6),
                "ratio_delta_gt_20": round(ratio20, 8),
                "thresholds": {
                    "min_p99": min_p99,
                    "min_ratio_gt20": min_ratio_gt20,
                    "blank_p99": blank_p99,
                    "blank_ratio_gt20": blank_ratio_gt20,
                },
            }
        )

    failures = [r for r in rows if r.get("status") == "fail"]
    return {
        "schema_version": "phase5_footer_contrast_gate_v1",
        "footer_qc_pack_schema_version": str(footer_qc_pack.get("schema_version") or ""),
        "target_count": len(rows),
        "failure_count": len(failures),
        "overall_ok": len(failures) == 0,
        "rows": rows,
    }


def _render_markdown(report: dict[str, Any], footer_pack_path: Path) -> str:
    lines = [
        "# Phase5 Footer Contrast Gate",
        "",
        f"- footer_qc_pack: `{footer_pack_path}`",
        f"- target_count: `{int(report.get('target_count') or 0)}`",
        f"- failure_count: `{int(report.get('failure_count') or 0)}`",
        f"- overall_ok: `{bool(report.get('overall_ok'))}`",
        "",
        "| Target | Status | Reason | Source | p99 | ratio>20 |",
        "|---|---|---|---|---:|---:|",
    ]
    for row in report.get("rows", []):
        if not isinstance(row, dict):
            continue
        lines.append(
            f"| {row.get('target')} | {row.get('status')} | {row.get('reason')} | "
            f"{row.get('contrast_source')} | {float(row.get('p99_channel_delta') or 0.0):.3f} | "
            f"{float(row.get('ratio_delta_gt_20') or 0.0):.6f} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--footer-qc-pack-json", required=True)
    p.add_argument("--min-p99-channel-delta", type=float, default=24.0)
    p.add_argument("--min-ratio-delta-gt20", type=float, default=0.003)
    p.add_argument("--blank-p99-channel-delta", type=float, default=8.0)
    p.add_argument("--blank-ratio-delta-gt20", type=float, default=0.0005)
    p.add_argument("--output-json", required=True)
    p.add_argument("--output-md", required=True)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    footer_pack_path = Path(args.footer_qc_pack_json).expanduser().resolve()
    footer_pack = _load_json(footer_pack_path)
    report = build_report(
        footer_qc_pack=footer_pack,
        footer_qc_dir=footer_pack_path.parent,
        min_p99=float(args.min_p99_channel_delta),
        min_ratio_gt20=float(args.min_ratio_delta_gt20),
        blank_p99=float(args.blank_p99_channel_delta),
        blank_ratio_gt20=float(args.blank_ratio_delta_gt20),
    )

    out_json = Path(args.output_json).expanduser().resolve()
    out_md = Path(args.output_md).expanduser().resolve()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(_render_markdown(report, footer_pack_path), encoding="utf-8")

    print(f"overall_ok={bool(report.get('overall_ok'))}")
    print(f"failures={int(report.get('failure_count') or 0)}")
    print(f"json={out_json}")
    return 0 if bool(report.get("overall_ok")) else 1


if __name__ == "__main__":
    raise SystemExit(main())

