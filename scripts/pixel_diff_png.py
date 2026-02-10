#!/usr/bin/env python3
"""
Compute a simple pixel diff between two PNGs and write a diff PNG.

This is intentionally pragmatic, not a full perceptual metric:
- absolute RGB deltas
- optional rectangular masks to ignore dynamic regions
- summary JSON with changed pixel ratio

Usage:
  python scripts/pixel_diff_png.py --a a.png --b b.png --out diff.png
  python scripts/pixel_diff_png.py --a a.png --b b.png --out diff.png --mask-rect 0,0,1200,80
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw


@dataclass
class MaskRect:
    x: int
    y: int
    w: int
    h: int


def parse_rect(value: str) -> MaskRect:
    parts = [p.strip() for p in value.split(",")]
    if len(parts) != 4:
        raise ValueError("mask rect must be x,y,w,h")
    x, y, w, h = (int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))
    return MaskRect(x=x, y=y, w=w, h=h)


def apply_masks(diff: Image.Image, rects: list[MaskRect]) -> Image.Image:
    if not rects:
        return diff
    mask = Image.new("L", diff.size, 255)
    draw = ImageDraw.Draw(mask)
    for r in rects:
        draw.rectangle([r.x, r.y, r.x + max(r.w, 0), r.y + max(r.h, 0)], fill=0)
    # Zero-out masked pixels in diff.
    out = diff.copy()
    out.putalpha(mask)
    return out


def compute_summary(diff_rgb: Image.Image, alpha_mask: Image.Image | None, threshold: int) -> dict[str, object]:
    # Count pixels whose max-channel delta exceeds threshold, ignoring masked (alpha==0).
    w, h = diff_rgb.size
    diff_px = diff_rgb.load()
    alpha_px = alpha_mask.load() if alpha_mask is not None else None
    total = 0
    changed = 0
    for y in range(h):
        for x in range(w):
            if alpha_px is not None and alpha_px[x, y] == 0:
                continue
            total += 1
            r, g, b = diff_px[x, y]
            if max(r, g, b) > threshold:
                changed += 1
    ratio = (changed / total) if total else 0.0
    return {"width": w, "height": h, "threshold": threshold, "pixels_considered": total, "pixels_changed": changed, "changed_ratio": ratio}


def main() -> None:
    parser = argparse.ArgumentParser(description="Pixel diff two PNGs.")
    parser.add_argument("--a", required=True, help="path to first PNG")
    parser.add_argument("--b", required=True, help="path to second PNG")
    parser.add_argument("--out", required=True, help="output diff PNG path")
    parser.add_argument("--summary-out", default="", help="optional summary JSON output path")
    parser.add_argument("--threshold", type=int, default=12, help="delta threshold for changed pixel count")
    parser.add_argument("--mask-rect", action="append", default=[], help="ignore region x,y,w,h (repeatable)")
    args = parser.parse_args()

    path_a = Path(args.a).expanduser().resolve()
    path_b = Path(args.b).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()
    summary_path = Path(args.summary_out).expanduser().resolve() if args.summary_out else None
    rects = [parse_rect(v) for v in args.mask_rect]

    img_a = Image.open(path_a).convert("RGB")
    img_b = Image.open(path_b).convert("RGB")
    if img_a.size != img_b.size:
        raise ValueError(f"image sizes differ: {img_a.size} vs {img_b.size}")

    diff_rgb = ImageChops.difference(img_a, img_b)
    diff_masked = apply_masks(diff_rgb, rects)

    # For human visibility, boost diff contrast slightly.
    diff_vis = diff_masked.convert("RGBA")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    diff_vis.save(out_path)

    alpha = diff_vis.getchannel("A") if diff_vis.mode == "RGBA" else None
    summary = compute_summary(diff_rgb, alpha, threshold=max(0, int(args.threshold)))
    summary["a"] = str(path_a)
    summary["b"] = str(path_b)
    summary["out"] = str(out_path)
    summary["mask_rects"] = [r.__dict__ for r in rects]

    if summary_path is not None:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    else:
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

