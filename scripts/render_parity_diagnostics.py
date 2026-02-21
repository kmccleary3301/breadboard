#!/usr/bin/env python3
"""
Deterministic text/render parity helpers for screenshot diagnostics.

This module intentionally has no tmux-specific behavior; it only compares
normalized text rows against rendered PNG row occupancy.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from PIL import Image


def normalize_text_for_parity(text: str) -> str:
    value = str(text or "")
    return value.replace("\r\n", "\n").replace("\r", "\n")


def normalized_text_lines(text: str) -> list[str]:
    return normalize_text_for_parity(text).splitlines()


def text_nonempty_rows(lines: list[str]) -> set[int]:
    return {idx for idx, line in enumerate(lines) if bool(str(line).strip())}


def _to_one_based_rows(rows: set[int] | list[int]) -> list[int]:
    return [int(idx) + 1 for idx in sorted(int(x) for x in rows)]


def render_nonbg_rows(
    *,
    img: Image.Image,
    bg_default: tuple[int, int, int],
    total_rows: int,
    cell_height: int,
    pad_y: int,
    min_delta_rgb_sum: int = 8,
    min_row_pixel_ratio: float = 0.001,
) -> set[int]:
    nonbg: set[int] = set()
    pixels = img.load()
    for row_idx in range(max(1, int(total_rows))):
        y0 = int(pad_y + row_idx * cell_height)
        y1 = int(min(img.height, y0 + cell_height))
        if y0 >= img.height or y1 <= y0:
            continue
        changed = 0
        total = (y1 - y0) * img.width
        for y in range(y0, y1):
            for x in range(img.width):
                p = pixels[x, y]
                delta = (
                    abs(int(p[0]) - int(bg_default[0]))
                    + abs(int(p[1]) - int(bg_default[1]))
                    + abs(int(p[2]) - int(bg_default[2]))
                )
                if delta > min_delta_rgb_sum:
                    changed += 1
        if total > 0 and (changed / total) >= float(min_row_pixel_ratio):
            nonbg.add(row_idx)
    return nonbg


def render_content_rows(
    *,
    img: Image.Image,
    total_rows: int,
    cell_height: int,
    pad_y: int,
    min_edge_delta_rgb_sum: int = 10,
    min_row_edge_ratio: float = 0.0014,
) -> tuple[set[int], list[dict[str, float]]]:
    """
    Detect row occupancy from local edge structure (glyph/stroke signal).

    This avoids false positives from large non-default background panes that
    contain no text glyphs.
    """
    content_rows: set[int] = set()
    diagnostics: list[dict[str, float]] = []
    pixels = img.load()

    width = int(img.width)
    for row_idx in range(max(1, int(total_rows))):
        y0 = int(pad_y + row_idx * cell_height)
        y1 = int(min(img.height, y0 + cell_height))
        if y0 >= img.height or y1 <= y0:
            diagnostics.append(
                {
                    "row": int(row_idx + 1),
                    "edge_ratio": 0.0,
                    "edge_count": 0,
                    "edge_samples": 0,
                }
            )
            continue

        edge_count = 0
        edge_samples = 0

        # Horizontal local edges.
        if width > 1:
            for y in range(y0, y1):
                for x in range(1, width):
                    p = pixels[x, y]
                    left = pixels[x - 1, y]
                    delta = (
                        abs(int(p[0]) - int(left[0]))
                        + abs(int(p[1]) - int(left[1]))
                        + abs(int(p[2]) - int(left[2]))
                    )
                    edge_samples += 1
                    if delta >= int(min_edge_delta_rgb_sum):
                        edge_count += 1

        # Vertical local edges only within the same cell row window.
        if (y1 - y0) > 1:
            for y in range(y0 + 1, y1):
                for x in range(width):
                    p = pixels[x, y]
                    up = pixels[x, y - 1]
                    delta = (
                        abs(int(p[0]) - int(up[0]))
                        + abs(int(p[1]) - int(up[1]))
                        + abs(int(p[2]) - int(up[2]))
                    )
                    edge_samples += 1
                    if delta >= int(min_edge_delta_rgb_sum):
                        edge_count += 1

        edge_ratio = 0.0
        if edge_samples > 0:
            edge_ratio = float(edge_count) / float(edge_samples)

        if edge_ratio >= float(min_row_edge_ratio):
            content_rows.add(row_idx)

        diagnostics.append(
            {
                "row": int(row_idx + 1),
                "edge_ratio": round(edge_ratio, 6),
                "edge_count": int(edge_count),
                "edge_samples": int(edge_samples),
            }
        )

    return content_rows, diagnostics


def summarize_row_occupancy(
    *,
    render_png: Path,
    text_payload: str,
    bg_default: tuple[int, int, int],
    cell_height: int,
    pad_y: int,
    declared_rows: int = 0,
    min_delta_rgb_sum: int = 8,
    min_row_pixel_ratio: float = 0.001,
    min_edge_delta_rgb_sum: int = 10,
    min_row_edge_ratio: float = 0.0014,
) -> dict[str, Any]:
    img = Image.open(render_png).convert("RGB")
    normalized = normalize_text_for_parity(text_payload)
    lines = normalized_text_lines(normalized)

    base_rows = len(lines)
    total_rows = max(1, int(base_rows))
    if int(declared_rows) > 0:
        total_rows = max(total_rows, int(declared_rows))

    txt_nonempty = text_nonempty_rows(lines)
    png_nonbg = render_nonbg_rows(
        img=img,
        bg_default=bg_default,
        total_rows=total_rows,
        cell_height=int(cell_height),
        pad_y=int(pad_y),
        min_delta_rgb_sum=int(min_delta_rgb_sum),
        min_row_pixel_ratio=float(min_row_pixel_ratio),
    )

    png_content, edge_row_diagnostics = render_content_rows(
        img=img,
        total_rows=total_rows,
        cell_height=int(cell_height),
        pad_y=int(pad_y),
        min_edge_delta_rgb_sum=int(min_edge_delta_rgb_sum),
        min_row_edge_ratio=float(min_row_edge_ratio),
    )

    # Robust parity source of truth.
    missing_rows = sorted(txt_nonempty - png_content)
    extra_rows = sorted(png_content - txt_nonempty)

    # Legacy non-bg parity retained for drift debugging.
    missing_rows_legacy = sorted(txt_nonempty - png_nonbg)
    extra_rows_legacy = sorted(png_nonbg - txt_nonempty)
    mismatch_rows = sorted(set(missing_rows) | set(extra_rows))

    edge_lookup: dict[int, dict[str, float]] = {
        int(row.get("row", 0)): row for row in edge_row_diagnostics if isinstance(row, dict)
    }
    mismatch_localization: list[dict[str, Any]] = []
    for row_idx in mismatch_rows:
        row_1 = int(row_idx) + 1
        edge_payload = edge_lookup.get(row_1, {})
        mismatch_localization.append(
            {
                "row": row_1,
                "text_nonempty": row_idx in txt_nonempty,
                "render_content": row_idx in png_content,
                "render_nonbg": row_idx in png_nonbg,
                "edge_ratio": round(float(edge_payload.get("edge_ratio", 0.0)), 6),
                "edge_count": int(edge_payload.get("edge_count", 0)),
                "edge_samples": int(edge_payload.get("edge_samples", 0)),
            }
        )

    text_span: list[int] | None = None
    render_span: list[int] | None = None
    render_span_legacy: list[int] | None = None
    if txt_nonempty:
        text_span = [min(txt_nonempty) + 1, max(txt_nonempty) + 1]
    if png_content:
        render_span = [min(png_content) + 1, max(png_content) + 1]
    if png_nonbg:
        render_span_legacy = [min(png_nonbg) + 1, max(png_nonbg) + 1]

    row_span_delta = 0
    if text_span and render_span:
        row_span_delta = abs(text_span[0] - render_span[0]) + abs(text_span[1] - render_span[1])
    row_span_delta_legacy = 0
    if text_span and render_span_legacy:
        row_span_delta_legacy = abs(text_span[0] - render_span_legacy[0]) + abs(
            text_span[1] - render_span_legacy[1]
        )

    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    return {
        "total_rows": total_rows,
        "source_line_count": int(base_rows),
        "declared_rows": int(declared_rows),
        "text_nonempty_row_count": len(txt_nonempty),
        "render_content_row_count": len(png_content),
        "render_nonbg_row_count": len(png_nonbg),
        "text_nonempty_rows": _to_one_based_rows(txt_nonempty),
        "render_content_rows": _to_one_based_rows(png_content),
        "render_nonbg_rows": _to_one_based_rows(png_nonbg),
        "missing_text_rows": [idx + 1 for idx in missing_rows],
        "extra_render_rows": [idx + 1 for idx in extra_rows],
        "mismatch_rows": [idx + 1 for idx in mismatch_rows],
        "missing_count": len(missing_rows),
        "extra_count": len(extra_rows),
        "text_row_span": text_span,
        "render_row_span": render_span,
        "row_span_delta": int(row_span_delta),
        "mismatch_localization": mismatch_localization,
        "missing_text_rows_legacy_nonbg": [idx + 1 for idx in missing_rows_legacy],
        "extra_render_rows_legacy_nonbg": [idx + 1 for idx in extra_rows_legacy],
        "missing_count_legacy_nonbg": len(missing_rows_legacy),
        "extra_count_legacy_nonbg": len(extra_rows_legacy),
        "render_row_span_legacy_nonbg": render_span_legacy,
        "row_span_delta_legacy_nonbg": int(row_span_delta_legacy),
        "text_sha256_normalized": digest,
        "min_delta_rgb_sum": int(min_delta_rgb_sum),
        "min_row_pixel_ratio": float(min_row_pixel_ratio),
        "min_edge_delta_rgb_sum": int(min_edge_delta_rgb_sum),
        "min_row_edge_ratio": float(min_row_edge_ratio),
        "edge_row_diagnostics": edge_row_diagnostics,
    }


def build_row_parity_summary(
    *,
    row_occupancy: dict[str, Any],
    render_png: Path,
    render_txt: Path,
    render_profile: str,
    schema_version: str = "tmux_row_parity_summary_v1",
) -> dict[str, Any]:
    """
    Build a compact, deterministic row parity summary sidecar.

    This is intended for fast validators and triage tooling that do not need
    the full render lock payload.
    """
    payload = dict(row_occupancy or {})
    return {
        "schema_version": str(schema_version),
        "render_profile": str(render_profile),
        "artifacts": {
            "png_basename": render_png.name,
            "txt_basename": render_txt.name,
        },
        "parity": {
            "total_rows": int(payload.get("total_rows", 0)),
            "source_line_count": int(payload.get("source_line_count", 0)),
            "declared_rows": int(payload.get("declared_rows", 0)),
            "text_sha256_normalized": str(payload.get("text_sha256_normalized", "")),
            "missing_count": int(payload.get("missing_count", 0)),
            "extra_count": int(payload.get("extra_count", 0)),
            "row_span_delta": int(payload.get("row_span_delta", 0)),
            "missing_text_rows": [int(x) for x in payload.get("missing_text_rows", [])],
            "extra_render_rows": [int(x) for x in payload.get("extra_render_rows", [])],
            "mismatch_rows": [int(x) for x in payload.get("mismatch_rows", [])],
            "mismatch_localization": [
                {
                    "row": int(item.get("row", 0)),
                    "text_nonempty": bool(item.get("text_nonempty", False)),
                    "render_content": bool(item.get("render_content", False)),
                    "render_nonbg": bool(item.get("render_nonbg", False)),
                    "edge_ratio": float(item.get("edge_ratio", 0.0)),
                    "edge_count": int(item.get("edge_count", 0)),
                    "edge_samples": int(item.get("edge_samples", 0)),
                }
                for item in payload.get("mismatch_localization", [])
                if isinstance(item, dict)
            ],
        },
    }


def summarize_row_occupancy_from_paths(
    *,
    render_png: Path,
    render_txt: Path,
    bg_default: tuple[int, int, int],
    cell_height: int,
    pad_y: int,
    declared_rows: int = 0,
    min_delta_rgb_sum: int = 8,
    min_row_pixel_ratio: float = 0.001,
    min_edge_delta_rgb_sum: int = 10,
    min_row_edge_ratio: float = 0.0014,
) -> dict[str, Any]:
    payload = render_txt.read_text(encoding="utf-8", errors="replace")
    return summarize_row_occupancy(
        render_png=render_png,
        text_payload=payload,
        bg_default=bg_default,
        cell_height=cell_height,
        pad_y=pad_y,
        declared_rows=declared_rows,
        min_delta_rgb_sum=min_delta_rgb_sum,
        min_row_pixel_ratio=min_row_pixel_ratio,
        min_edge_delta_rgb_sum=min_edge_delta_rgb_sum,
        min_row_edge_ratio=min_row_edge_ratio,
    )
