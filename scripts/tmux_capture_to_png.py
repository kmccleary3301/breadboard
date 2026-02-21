#!/usr/bin/env python3
"""
Capture a tmux pane (including ANSI colors) and render it to a PNG.

Why:
- Lets agentic tooling compare terminal UIs with full color fidelity (Claude/Codex/OpenCode/Breadboard/Crush/etc).
- Produces both the raw ANSI capture and a rendered PNG for quick review/diffing.

Usage:
  python scripts/tmux_capture_to_png.py --target crush_color_demo:0
  python scripts/tmux_capture_to_png.py --target cc_transcript_viewer_manual:0 --out docs_tmp/captures/cc.png
  python scripts/tmux_capture_to_png.py --target crush_color_demo:0 --scale 0.75
  python scripts/tmux_capture_to_png.py --target screenshot_ground_truth_3:0 --height-mode scrollback
  python scripts/tmux_capture_to_png.py --target screenshot_ground_truth_3:0 --height-mode pane --rows-override 30

Notes:
- Requires: tmux, Python Pillow (PIL), wcwidth.
- This renders xterm-256 and truecolor SGR sequences (38/48;5 and 38/48;2).
- Default background/foreground are approximations; adjust with --bg/--fg to match your terminal theme.
- Optional renderer: --renderer xterm-xvfb uses a real xterm + Xvfb + xwd + ImageMagick convert.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple

from PIL import Image, ImageDraw, ImageFont
from wcwidth import wcwidth
try:
    from fontTools.ttLib import TTFont
except Exception:  # pragma: no cover - optional dependency
    TTFont = None
from tmux_capture_render_profile import (
    DEFAULT_RENDER_PROFILE_ID,
    LEGACY_PROFILE_ID,
    SUPPORTED_RENDER_PROFILES,
    append_profile_cli_args,
    assert_render_profile_lock,
    profile_lock_manifest,
    resolve_render_profile,
)
from render_parity_diagnostics import (
    build_row_parity_summary,
    summarize_row_occupancy_from_paths,
)


DEFAULT_FG = (241, 245, 249)
DEFAULT_BG = (31, 36, 48)
VS_CODE_LIKE_RENDER_PROFILES = {"phase4_locked_v5"}
SYMBOL_FALLBACK_CHAIN_BY_PROFILE: dict[str, list[str]] = {
    "phase4_locked_v5": [
        "renderer_assets/fonts/Segoe UI Symbol.ttf",
        "renderer_assets/fonts/seguisym.ttf",
        "renderer_assets/fonts/Cambria Math.ttf",
        "renderer_assets/fonts/CambriaMath.ttf",
    ],
}
DEFAULT_SYMBOL_FALLBACK_CHAIN: list[str] = [
    "renderer_assets/fonts/CambriaMath.ttf",
    "renderer_assets/fonts/Cambria Math.ttf",
    "renderer_assets/fonts/seguisym.ttf",
    "renderer_assets/fonts/Segoe UI Symbol.ttf",
]


XTERM_16 = {
    0: (0, 0, 0),
    1: (205, 49, 49),
    2: (13, 188, 121),
    3: (229, 229, 16),
    4: (36, 114, 200),
    5: (188, 63, 188),
    6: (17, 168, 205),
    7: (229, 229, 229),
    8: (102, 102, 102),
    9: (241, 76, 76),
    10: (35, 209, 139),
    11: (245, 245, 67),
    12: (59, 142, 234),
    13: (214, 112, 214),
    14: (41, 184, 219),
    15: (255, 255, 255),
}


def parse_rgb(value: str) -> Tuple[int, int, int]:
    value = value.strip()
    if value.startswith("#"):
        value = value[1:]
    if len(value) != 6:
        raise ValueError("Expected hex RGB like 262626 or #262626")
    r = int(value[0:2], 16)
    g = int(value[2:4], 16)
    b = int(value[4:6], 16)
    return (r, g, b)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def resolve_cell_geometry(
    font_regular: ImageFont.ImageFont,
    *,
    line_gap: int,
    cell_width_override: int,
    cell_height_override: int,
) -> tuple[int, int]:
    bbox = font_regular.getbbox("M")
    cell_w = max(8, bbox[2] - bbox[0])
    ascent, descent = font_regular.getmetrics()
    cell_h = max(1, ascent + descent + line_gap)
    if cell_width_override > 0:
        cell_w = cell_width_override
    if cell_height_override > 0:
        cell_h = cell_height_override
    return cell_w, cell_h


def xterm_256(index: int, fallback: Tuple[int, int, int]) -> Tuple[int, int, int]:
    if index < 0:
        return fallback
    if index < 16:
        return XTERM_16.get(index, fallback)
    if 16 <= index <= 231:
        idx = index - 16
        r = idx // 36
        g = (idx % 36) // 6
        b = idx % 6

        def level(v: int) -> int:
            return 0 if v == 0 else 55 + 40 * v

        return (level(r), level(g), level(b))
    if 232 <= index <= 255:
        gray = 8 + (index - 232) * 10
        return (gray, gray, gray)
    return fallback


@dataclass
class Style:
    fg: Tuple[int, int, int] = DEFAULT_FG
    bg: Tuple[int, int, int] = DEFAULT_BG
    bold: bool = False
    dim: bool = False
    underline: bool = False
    italic: bool = False
    inverse: bool = False

    def copy(self) -> "Style":
        return Style(self.fg, self.bg, self.bold, self.dim, self.underline, self.italic, self.inverse)

    def effective_colors(self) -> Tuple[Tuple[int, int, int], Tuple[int, int, int]]:
        fg, bg = self.fg, self.bg
        if self.inverse:
            fg, bg = bg, fg
        if self.dim:
            fg = (int(fg[0] * 0.65), int(fg[1] * 0.65), int(fg[2] * 0.65))
        return fg, bg


@dataclass
class Cell:
    ch: str = " "
    style: Style = field(default_factory=Style)
    wide_continuation: bool = False


BOX_DRAWING_SEGMENTS: dict[str, tuple[str, ...]] = {
    "─": ("h",),
    "━": ("h",),
    "═": ("h",),
    "│": ("v",),
    "┃": ("v",),
    "║": ("v",),
    "┌": ("h_right", "v_down"),
    "┐": ("h_left", "v_down"),
    "└": ("h_right", "v_up"),
    "┘": ("h_left", "v_up"),
    "╭": ("h_right", "v_down"),
    "╮": ("h_left", "v_down"),
    "╰": ("h_right", "v_up"),
    "╯": ("h_left", "v_up"),
    "├": ("v", "h_right"),
    "┤": ("v", "h_left"),
    "┬": ("h", "v_down"),
    "┴": ("h", "v_up"),
    "┼": ("h", "v"),
    "╴": ("h_left",),
    "╵": ("v_up",),
    "╶": ("h_right",),
    "╷": ("v_down",),
    "╸": ("h_left",),
    "╹": ("v_up",),
    "╺": ("h_right",),
    "╻": ("v_down",),
    "╼": ("h",),
    "╽": ("v",),
    "╾": ("h",),
    "╿": ("v",),
}


def is_box_drawing_char(ch: str) -> bool:
    return ch in BOX_DRAWING_SEGMENTS


BLOCK_ELEMENT_RECTS: dict[str, tuple[tuple[float, float, float, float], ...]] = {
    # (x0, y0, x1, y1) fractions inside the cell.
    "▀": ((0.0, 0.0, 1.0, 0.5),),
    "▄": ((0.0, 0.5, 1.0, 1.0),),
    "█": ((0.0, 0.0, 1.0, 1.0),),
    "▌": ((0.0, 0.0, 0.5, 1.0),),
    "▐": ((0.5, 0.0, 1.0, 1.0),),
    "▖": ((0.0, 0.5, 0.5, 1.0),),
    "▗": ((0.5, 0.5, 1.0, 1.0),),
    "▘": ((0.0, 0.0, 0.5, 0.5),),
    "▝": ((0.5, 0.0, 1.0, 0.5),),
    "▙": ((0.0, 0.0, 0.5, 1.0), (0.5, 0.5, 1.0, 1.0)),
    "▛": ((0.0, 0.0, 1.0, 0.5), (0.0, 0.5, 0.5, 1.0)),
    "▜": ((0.0, 0.0, 1.0, 0.5), (0.5, 0.5, 1.0, 1.0)),
    "▟": ((0.5, 0.0, 1.0, 1.0), (0.0, 0.5, 0.5, 1.0)),
}


def is_block_element_char(ch: str) -> bool:
    return ch in BLOCK_ELEMENT_RECTS


def is_vector_symbol_char(ch: str) -> bool:
    return ch == "▣"


def draw_vector_symbol_cell(
    draw: ImageDraw.ImageDraw,
    *,
    x0: int,
    y0: int,
    cell_w: int,
    cell_h: int,
    ch: str,
    color: Tuple[int, int, int],
) -> None:
    if ch != "▣":
        return
    outer_inset = max(1, int(round(min(cell_w, cell_h) * 0.08)))
    left = x0 + outer_inset
    right = x0 + cell_w - 1 - outer_inset
    top = y0 + outer_inset
    bottom = y0 + cell_h - 1 - outer_inset
    if right <= left or bottom <= top:
        return
    stroke = 1 if min(cell_w, cell_h) <= 16 else 2
    draw.rectangle([left, top, right, bottom], outline=color, width=stroke)
    inner_margin = max(stroke + 1, int(round(min(cell_w, cell_h) * 0.26)))
    inner_left = left + inner_margin
    inner_right = right - inner_margin
    inner_top = top + inner_margin
    inner_bottom = bottom - inner_margin
    if inner_right >= inner_left and inner_bottom >= inner_top:
        draw.rectangle([inner_left, inner_top, inner_right, inner_bottom], fill=color)


def draw_box_drawing_cell(
    draw: ImageDraw.ImageDraw,
    *,
    x0: int,
    y0: int,
    cell_w: int,
    cell_h: int,
    ch: str,
    color: Tuple[int, int, int],
) -> None:
    segments = BOX_DRAWING_SEGMENTS.get(ch)
    if not segments:
        return
    stroke = 1 if min(cell_w, cell_h) <= 16 else 2
    # Heavier box-drawing forms should render thicker than light forms.
    if ch in {"┃", "━", "║", "═", "╸", "╹", "╺", "╻", "╼", "╽", "╾", "╿"}:
        stroke = max(stroke, 2)
    left = x0
    right = x0 + cell_w - 1
    top = y0
    bottom = y0 + cell_h - 1
    cx = x0 + (cell_w // 2)
    cy = y0 + (cell_h // 2)
    half_segment_chars = {"╴", "╵", "╶", "╷", "╸", "╹", "╺", "╻"}
    half_segment_mode = ch in half_segment_chars

    def _draw_half_segment_rect(seg: str) -> bool:
        if not half_segment_mode:
            return False
        if seg in {"h_left", "h_right"}:
            y_start = max(top, cy - (stroke // 2))
            y_end = min(bottom, y_start + stroke - 1)
            if seg == "h_left":
                x_start, x_end = left, cx
            else:
                x_start, x_end = cx, right
            draw.rectangle([x_start, y_start, x_end, y_end], fill=color)
            return True
        if seg in {"v_up", "v_down"}:
            x_start = max(left, cx - (stroke // 2))
            x_end = min(right, x_start + stroke - 1)
            if seg == "v_up":
                y_start, y_end = top, cy
            else:
                y_start, y_end = cy, bottom
            draw.rectangle([x_start, y_start, x_end, y_end], fill=color)
            return True
        return False

    for seg in segments:
        if seg == "h":
            draw.line([left, cy, right, cy], fill=color, width=stroke)
        elif seg == "v":
            draw.line([cx, top, cx, bottom], fill=color, width=stroke)
        elif seg == "h_left":
            if not _draw_half_segment_rect(seg):
                draw.line([left, cy, cx, cy], fill=color, width=stroke)
        elif seg == "h_right":
            if not _draw_half_segment_rect(seg):
                draw.line([cx, cy, right, cy], fill=color, width=stroke)
        elif seg == "v_up":
            if not _draw_half_segment_rect(seg):
                draw.line([cx, top, cx, cy], fill=color, width=stroke)
        elif seg == "v_down":
            if not _draw_half_segment_rect(seg):
                draw.line([cx, cy, cx, bottom], fill=color, width=stroke)


def draw_block_element_cell(
    draw: ImageDraw.ImageDraw,
    *,
    x0: int,
    y0: int,
    cell_w: int,
    cell_h: int,
    ch: str,
    color: Tuple[int, int, int],
) -> None:
    rects = BLOCK_ELEMENT_RECTS.get(ch)
    if not rects:
        return
    for rx0, ry0, rx1, ry1 in rects:
        left = int(round(x0 + cell_w * rx0))
        top = int(round(y0 + cell_h * ry0))
        right = int(round(x0 + cell_w * rx1)) - 1
        bottom = int(round(y0 + cell_h * ry1)) - 1
        right = max(left, right)
        bottom = max(top, bottom)
        draw.rectangle([left, top, right, bottom], fill=color)


CSI_RE = re.compile(r"\x1b\[([0-9;?$>< ]*)([@-~])")
OSC_START = "\x1b]"
OSC_END = "\x1b\\"

FONT_CANDIDATES = [
    {
        "regular": "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "bold": "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "italic": "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Oblique.ttf",
        "bold_italic": "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-BoldOblique.ttf",
    },
    {
        "regular": "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "bold": "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
        "italic": "/usr/share/fonts/truetype/liberation/LiberationMono-Italic.ttf",
        "bold_italic": "/usr/share/fonts/truetype/liberation/LiberationMono-BoldItalic.ttf",
    },
    {
        "regular": "/usr/share/fonts/truetype/ubuntu/UbuntuMono-R.ttf",
        "bold": "/usr/share/fonts/truetype/ubuntu/UbuntuMono-B.ttf",
        "italic": "/usr/share/fonts/truetype/ubuntu/UbuntuMono-RI.ttf",
        "bold_italic": "/usr/share/fonts/truetype/ubuntu/UbuntuMono-BI.ttf",
    },
    {
        "regular": "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",
        "bold": "/usr/share/fonts/truetype/noto/NotoSansMono-Bold.ttf",
        "italic": "/usr/share/fonts/truetype/noto/NotoSansMono-Italic.ttf",
        "bold_italic": "/usr/share/fonts/truetype/noto/NotoSansMono-BoldItalic.ttf",
    },
    {
        "regular": "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
        "bold": "/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf",
        "italic": "/usr/share/fonts/truetype/freefont/FreeMonoOblique.ttf",
        "bold_italic": "/usr/share/fonts/truetype/freefont/FreeMonoBoldOblique.ttf",
    },
]

PROFILE_CONTROLLED_FLAGS: tuple[str, ...] = (
    "--bg",
    "--fg",
    "--font-size",
    "--font-path",
    "--font-bold-path",
    "--font-italic-path",
    "--font-bold-italic-path",
    "--renderer",
    "--xterm-font",
    "--xterm-sleep",
    "--scale",
    "--cell-width",
    "--cell-height",
    "--line-gap",
    "--pad-x",
    "--pad-y",
    "--baseline-offset",
    "--underline-offset",
)


def argv_contains_flag(argv: list[str], flag: str) -> bool:
    return any(arg == flag or arg.startswith(f"{flag}=") for arg in argv)


def resolve_first_existing_path(candidates: list[str]) -> str:
    for raw in candidates:
        p = Path(str(raw)).expanduser()
        if p.exists() and p.is_file():
            return str(p)
    return ""


def resolve_existing_paths(candidates: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        p = Path(str(raw)).expanduser()
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.exists() and p.is_file():
            out.append(str(p))
    return out


def resolve_symbol_fallback_candidates(render_profile: str) -> list[str]:
    profile_chain = SYMBOL_FALLBACK_CHAIN_BY_PROFILE.get(str(render_profile), [])
    merged = [*profile_chain, *DEFAULT_SYMBOL_FALLBACK_CHAIN]
    out: list[str] = []
    seen: set[str] = set()
    for raw in merged:
        token = str(raw).strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def should_prefer_symbol_fallback(ch: str) -> bool:
    if not ch:
        return False
    cp = ord(ch)
    return (
        (0x2500 <= cp <= 0x259F)  # Box Drawing + Block Elements
        or (0x25A0 <= cp <= 0x25FF)  # Geometric Shapes
        or cp in {0x2022, 0x00B7}  # Bullets frequently seen in terminal UIs
    )


@dataclass
class FontPack:
    regular: ImageFont.ImageFont
    bold: ImageFont.ImageFont | None = None
    italic: ImageFont.ImageFont | None = None
    bold_italic: ImageFont.ImageFont | None = None
    symbol_fallback: ImageFont.ImageFont | None = None
    symbol_fallback_chain: tuple[ImageFont.ImageFont, ...] = ()
    fallback_regular: ImageFont.ImageFont | None = None
    fallback_bold: ImageFont.ImageFont | None = None
    fallback_italic: ImageFont.ImageFont | None = None
    fallback_bold_italic: ImageFont.ImageFont | None = None
    codepoint_map: dict[int, set[int] | None] = field(default_factory=dict)
    font_path_map: dict[int, str] = field(default_factory=dict)


def _resample_filter(name: str) -> int:
    resample = getattr(Image, "Resampling", Image)
    key = str(name or "").strip().lower()
    if key in {"nearest", "none"}:
        return int(getattr(resample, "NEAREST"))
    if key in {"bilinear", "linear"}:
        return int(getattr(resample, "BILINEAR"))
    if key in {"bicubic", "cubic"}:
        return int(getattr(resample, "BICUBIC"))
    if key in {"lanczos", "antialias"}:
        return int(getattr(resample, "LANCZOS"))
    return int(getattr(resample, "NEAREST"))


def _font_variant_safe(font: ImageFont.ImageFont | None, size: int) -> ImageFont.ImageFont | None:
    if font is None:
        return None
    if hasattr(font, "font_variant"):
        try:
            return font.font_variant(size=size)
        except Exception:
            return font
    return font


def _scaled_font_pack(fonts: FontPack, factor: int, base_size: int) -> FontPack:
    if factor <= 1:
        return fonts
    scaled_size = max(1, int(base_size) * int(factor))
    scaled_regular = _font_variant_safe(fonts.regular, scaled_size) or fonts.regular
    scaled_bold = _font_variant_safe(fonts.bold, scaled_size)
    scaled_italic = _font_variant_safe(fonts.italic, scaled_size)
    scaled_bold_italic = _font_variant_safe(fonts.bold_italic, scaled_size)
    scaled_symbol_fallback = _font_variant_safe(fonts.symbol_fallback, scaled_size)
    scaled_symbol_fallback_chain = tuple(
        sf
        for sf in (_font_variant_safe(symbol_font, scaled_size) for symbol_font in fonts.symbol_fallback_chain)
        if sf is not None
    )
    scaled_fallback_regular = _font_variant_safe(fonts.fallback_regular, scaled_size)
    scaled_fallback_bold = _font_variant_safe(fonts.fallback_bold, scaled_size)
    scaled_fallback_italic = _font_variant_safe(fonts.fallback_italic, scaled_size)
    scaled_fallback_bold_italic = _font_variant_safe(fonts.fallback_bold_italic, scaled_size)

    scaled_codepoint_map: dict[int, set[int] | None] = {}
    scaled_font_path_map: dict[int, str] = {}

    def _carry_font_metadata(
        original_font: ImageFont.ImageFont | None,
        scaled_font: ImageFont.ImageFont | None,
    ) -> None:
        if scaled_font is None:
            return
        if original_font is None:
            scaled_codepoint_map[id(scaled_font)] = None
            return
        original_id = id(original_font)
        scaled_codepoint_map[id(scaled_font)] = fonts.codepoint_map.get(original_id)
        original_path = fonts.font_path_map.get(original_id)
        if original_path:
            scaled_font_path_map[id(scaled_font)] = original_path

    _carry_font_metadata(fonts.regular, scaled_regular)
    _carry_font_metadata(fonts.bold, scaled_bold)
    _carry_font_metadata(fonts.italic, scaled_italic)
    _carry_font_metadata(fonts.bold_italic, scaled_bold_italic)
    _carry_font_metadata(fonts.symbol_fallback, scaled_symbol_fallback)
    for original_symbol_font, scaled_symbol_font in zip(fonts.symbol_fallback_chain, scaled_symbol_fallback_chain):
        _carry_font_metadata(original_symbol_font, scaled_symbol_font)
    _carry_font_metadata(fonts.fallback_regular, scaled_fallback_regular)
    _carry_font_metadata(fonts.fallback_bold, scaled_fallback_bold)
    _carry_font_metadata(fonts.fallback_italic, scaled_fallback_italic)
    _carry_font_metadata(fonts.fallback_bold_italic, scaled_fallback_bold_italic)
    return FontPack(
        regular=scaled_regular,
        bold=scaled_bold,
        italic=scaled_italic,
        bold_italic=scaled_bold_italic,
        symbol_fallback=scaled_symbol_fallback,
        symbol_fallback_chain=scaled_symbol_fallback_chain,
        fallback_regular=scaled_fallback_regular,
        fallback_bold=scaled_fallback_bold,
        fallback_italic=scaled_fallback_italic,
        fallback_bold_italic=scaled_fallback_bold_italic,
        # Keep coverage map aligned to the actual scaled font object IDs.
        codepoint_map=scaled_codepoint_map,
        font_path_map=scaled_font_path_map,
    )


def _load_font_codepoints(font: ImageFont.ImageFont | None) -> set[int] | None:
    if font is None or TTFont is None:
        return None
    font_path = str(getattr(font, "path", "") or "")
    if not font_path:
        return None
    try:
        tt = TTFont(font_path, lazy=True)
        cmap_table = tt.get("cmap")
        if cmap_table is None:
            tt.close()
            return None
        points: set[int] = set()
        for table in cmap_table.tables:
            points.update(int(k) for k in table.cmap.keys())
        tt.close()
        return points
    except Exception:
        return None


def _font_supports_char(font: ImageFont.ImageFont | None, ch: str, codepoint_map: dict[int, set[int] | None]) -> bool:
    if font is None or not ch:
        return False
    cp = ord(ch)
    # Fast-path: standard ASCII always handled by monospace terminal fonts in this pipeline.
    if 0x20 <= cp <= 0x7E:
        return True
    supported = codepoint_map.get(id(font))
    if supported is None:
        # Unknown coverage map: avoid false negatives.
        return True
    return cp in supported


def _font_path(font: ImageFont.ImageFont | None, font_path_map: dict[int, str]) -> str:
    if font is None:
        return ""
    mapped = font_path_map.get(id(font))
    if mapped:
        return mapped
    return str(getattr(font, "path", "") or "")


def render_ansi_to_png(
    ansi_text: str,
    cols: int,
    rows: int,
    out_path: Path,
    fg_default: Tuple[int, int, int],
    bg_default: Tuple[int, int, int],
    scale: float,
    fonts: FontPack,
    cell_width: int,
    cell_height: int,
    line_gap: int,
    pad_x: int,
    pad_y: int,
    baseline_offset: int,
    underline_offset: int,
    font_size: int = 14,
    raster_oversample: int = 1,
    raster_downsample_resample: str = "nearest",
    scale_resample: str = "nearest",
    snapshot_mode: bool = True,
    cursor_state: tuple[int, int, bool] | None = None,
    glyph_provenance: list[dict[str, object]] | None = None,
    glyph_provenance_mode: str = "interesting",
) -> None:
    cols = max(1, int(cols))
    rows = max(1, int(rows))

    # Apply user defaults
    global DEFAULT_FG, DEFAULT_BG
    DEFAULT_FG = fg_default
    DEFAULT_BG = bg_default

    def blank_row() -> list[Cell]:
        return [Cell(" ", Style()) for _ in range(cols)]

    grid = [blank_row() for _ in range(rows)]
    last_nonempty_grid = None

    def clone_grid(source):
        return [
            [Cell(cell.ch, cell.style.copy(), cell.wide_continuation) for cell in row_cells]
            for row_cells in source
        ]

    def grid_has_content(source):
        for row_cells in source:
            for cell in row_cells:
                if cell.ch != " " and not cell.wide_continuation:
                    return True
        return False

    style = Style()
    row = 0
    col = 0
    idx = 0
    line_clipped = False
    wrapped_by_overflow = False

    def scroll_up(lines: int = 1) -> None:
        nonlocal grid, row, col, line_clipped, wrapped_by_overflow
        if lines <= 0:
            return
        for _ in range(lines):
            if grid:
                grid.pop(0)
            grid.append(blank_row())
        row = max(0, rows - 1)
        col = 0
        line_clipped = False
        wrapped_by_overflow = False

    while idx < len(ansi_text):
        ch = ansi_text[idx]
        if ch == "\x1b":
            if idx + 1 < len(ansi_text) and ansi_text[idx + 1] == "]":
                # OSC sequence, skip until BEL or ST (ESC \)
                osc_end = ansi_text.find("\x07", idx + 2)
                st_end = ansi_text.find("\x1b\\", idx + 2)
                if st_end != -1 and (osc_end == -1 or st_end < osc_end):
                    idx = st_end + 2
                    continue
                if osc_end != -1:
                    idx = osc_end + 1
                    continue
                idx += 1
                continue
            m = CSI_RE.match(ansi_text, idx)
            if m:
                params_raw, final = m.group(1), m.group(2)
                idx = m.end()
                # params
                if params_raw.startswith("?"):
                    params = [p for p in params_raw[1:].split(";") if p]
                else:
                    params = [p for p in params_raw.split(";") if p]
                nums = []
                for p in params:
                    try:
                        nums.append(int(p))
                    except ValueError:
                        pass

                if final == "m":
                    if not nums:
                        nums = [0]
                    i = 0
                    while i < len(nums):
                        code = nums[i]
                        if code == 0:
                            style = Style()
                        elif code == 1:
                            style.bold = True
                        elif code == 2:
                            style.dim = True
                        elif code == 3:
                            style.italic = True
                        elif code == 4:
                            style.underline = True
                        elif code == 7:
                            style.inverse = True
                        elif code == 22:
                            style.bold = False
                            style.dim = False
                        elif code == 23:
                            style.italic = False
                        elif code == 24:
                            style.underline = False
                        elif code == 27:
                            style.inverse = False
                        elif code == 39:
                            style.fg = DEFAULT_FG
                        elif code == 49:
                            style.bg = DEFAULT_BG
                        elif 30 <= code <= 37:
                            style.fg = xterm_256(code - 30, DEFAULT_FG)
                        elif 90 <= code <= 97:
                            style.fg = xterm_256((code - 90) + 8, DEFAULT_FG)
                        elif 40 <= code <= 47:
                            style.bg = xterm_256(code - 40, DEFAULT_BG)
                        elif 100 <= code <= 107:
                            style.bg = xterm_256((code - 100) + 8, DEFAULT_BG)
                        elif code in (38, 48):
                            is_fg = code == 38
                            if i + 1 < len(nums):
                                mode = nums[i + 1]
                                if mode == 5 and i + 2 < len(nums):
                                    color = xterm_256(nums[i + 2], DEFAULT_FG if is_fg else DEFAULT_BG)
                                    if is_fg:
                                        style.fg = color
                                    else:
                                        style.bg = color
                                    i += 2
                                elif mode == 2 and i + 4 < len(nums):
                                    color = (
                                        nums[i + 2] & 255,
                                        nums[i + 3] & 255,
                                        nums[i + 4] & 255,
                                    )
                                    if is_fg:
                                        style.fg = color
                                    else:
                                        style.bg = color
                                    i += 4
                        i += 1
                elif final in ("H", "f"):
                    row_val = nums[0] if len(nums) >= 1 and nums[0] > 0 else 1
                    col_val = nums[1] if len(nums) >= 2 and nums[1] > 0 else 1
                    row = max(0, min(rows - 1, row_val - 1))
                    col = max(0, min(cols - 1, col_val - 1))
                    line_clipped = False
                elif final == "A":
                    n = nums[0] if nums else 1
                    row = max(0, row - n)
                    line_clipped = False
                elif final == "B":
                    n = nums[0] if nums else 1
                    row = min(rows - 1, row + n)
                    line_clipped = False
                elif final == "C":
                    n = nums[0] if nums else 1
                    col = min(cols - 1, col + n)
                    line_clipped = False
                elif final == "D":
                    n = nums[0] if nums else 1
                    col = max(0, col - n)
                    line_clipped = False
                elif final == "J":
                    mode = nums[0] if nums else 0
                    if mode == 2 or mode == 3 or (mode == 0 and row == 0 and col == 0):
                        if grid_has_content(grid):
                            last_nonempty_grid = clone_grid(grid)
                        grid = [blank_row() for _ in range(rows)]
                        row = 0
                        col = 0
                        line_clipped = False
                    elif mode == 1:
                        for r in range(0, row + 1):
                            start = 0
                            end = cols if r < row else col + 1
                            for c in range(start, end):
                                grid[r][c] = Cell(" ", style.copy())
                    else:
                        for r in range(row, rows):
                            start = col if r == row else 0
                            for c in range(start, cols):
                                grid[r][c] = Cell(" ", style.copy())
                elif final == "K":
                    mode = nums[0] if nums else 0
                    if 0 <= row < rows:
                        if mode == 1:
                            for c in range(0, col + 1):
                                grid[row][c] = Cell(" ", style.copy())
                        elif mode == 2:
                            for c in range(cols):
                                grid[row][c] = Cell(" ", style.copy())
                        else:
                            for c in range(col, cols):
                                grid[row][c] = Cell(" ", style.copy())
                # ignore other CSI
                continue
            if idx + 1 < len(ansi_text):
                idx += 2
                continue
            idx += 1
            continue

        if ch == "\n":
            # Avoid double row-advance for fixed-width ANSI lines:
            # if we already wrapped when col reached terminal width, a following
            # newline should finalize that wrapped line, not skip an extra row.
            if wrapped_by_overflow:
                wrapped_by_overflow = False
                col = 0
                line_clipped = False
                idx += 1
                continue
            row += 1
            col = 0
            line_clipped = False
            if row >= rows:
                if snapshot_mode:
                    break
                scroll_up(row - rows + 1)
            idx += 1
            continue
        if ch == "\r":
            col = 0
            line_clipped = False
            wrapped_by_overflow = False
            idx += 1
            continue
        if ch == "\t":
            wrapped_by_overflow = False
            if line_clipped:
                idx += 1
                continue
            next_tab = ((col // 8) + 1) * 8
            while col < next_tab and col < cols:
                grid[row][col] = Cell(" ", style.copy())
                col += 1
            idx += 1
            continue
        if ord(ch) < 32:
            wrapped_by_overflow = False
            idx += 1
            continue
        if line_clipped:
            idx += 1
            continue

        wrapped_by_overflow = False
        w = wcwidth(ch)
        if w <= 0:
            idx += 1
            continue

        fg_eff, bg_eff = style.effective_colors()
        effective_style = Style(fg_eff, bg_eff, style.bold, style.dim, style.underline, style.italic, style.inverse)

        if row < rows:
            if w == 2 and col == cols - 1:
                # Deterministic wide-cell overflow policy in snapshot mode:
                # reserve final cell as blank and clip remaining printable chars
                # until newline/reset to prevent jitter from repeated overwrite.
                if col < cols:
                    grid[row][col] = Cell(" ", effective_style)
                col += 1
            else:
                if col < cols:
                    grid[row][col] = Cell(ch, effective_style, False)
                col += 1
                if w == 2 and col < cols:
                    grid[row][col] = Cell(" ", effective_style, True)
                    col += 1

        if col >= cols:
            if snapshot_mode:
                col = cols
                line_clipped = True
            else:
                row += 1
                col = 0
                line_clipped = False
                wrapped_by_overflow = True
                if row >= rows:
                    scroll_up(row - rows + 1)

        idx += 1

    oversample = max(1, int(raster_oversample))
    draw_fonts = _scaled_font_pack(fonts, oversample, font_size)
    font_regular = draw_fonts.regular
    font_bold = draw_fonts.bold
    font_italic = draw_fonts.italic
    font_bold_italic = draw_fonts.bold_italic
    symbol_fallback = draw_fonts.symbol_fallback
    fallback_regular = draw_fonts.fallback_regular
    fallback_bold = draw_fonts.fallback_bold
    fallback_italic = draw_fonts.fallback_italic
    fallback_bold_italic = draw_fonts.fallback_bold_italic

    render_cell_width = int(cell_width) * oversample if int(cell_width) > 0 else int(cell_width)
    render_cell_height = int(cell_height) * oversample if int(cell_height) > 0 else int(cell_height)
    render_line_gap = int(line_gap) * oversample
    render_pad_x = int(pad_x) * oversample
    render_pad_y = int(pad_y) * oversample
    render_baseline_offset = int(baseline_offset) * oversample if int(baseline_offset) >= 0 else -1
    render_underline_offset = int(underline_offset) * oversample if int(underline_offset) >= 0 else -1

    cell_w, cell_h = resolve_cell_geometry(
        font_regular,
        line_gap=render_line_gap,
        cell_width_override=render_cell_width,
        cell_height_override=render_cell_height,
    )
    ascent, _descent = font_regular.getmetrics()

    if last_nonempty_grid is not None and not grid_has_content(grid):
        grid = last_nonempty_grid

    img = Image.new(
        "RGB",
        (cols * cell_w + render_pad_x * 2, rows * cell_h + render_pad_y * 2),
        bg_default,
    )
    draw = ImageDraw.Draw(img)
    # Baseline-anchored text placement prevents per-font vertical jitter.
    baseline = render_baseline_offset if render_baseline_offset >= 0 else ascent

    font_y_offset_cache: dict[int, int] = {}

    def font_y_offset(font: ImageFont.ImageFont) -> int:
        key = id(font)
        cached = font_y_offset_cache.get(key)
        if cached is not None:
            return cached
        font_ascent, _ = font.getmetrics()
        offset = baseline - font_ascent
        font_y_offset_cache[key] = offset
        return offset

    def should_record_provenance(
        *,
        ch: str,
        source: str,
        fallback_rank: int,
    ) -> bool:
        mode = str(glyph_provenance_mode or "interesting").strip().lower()
        if mode in {"", "off", "none", "false", "0"}:
            return False
        if mode == "all":
            return True
        cp = ord(ch) if ch else 0
        if source != "font_chain":
            return True
        if fallback_rank > 0:
            return True
        return cp < 0x20 or cp > 0x7E

    def record_provenance(
        *,
        row: int,
        col: int,
        ch: str,
        source: str,
        selected_font: ImageFont.ImageFont | None,
        fallback_rank: int,
        fallback_chain_size: int,
        bold: bool,
        italic: bool,
    ) -> None:
        if glyph_provenance is None or not ch:
            return
        if not should_record_provenance(ch=ch, source=source, fallback_rank=fallback_rank):
            return
        glyph_provenance.append(
            {
                "row": int(row),
                "col": int(col),
                "char": ch,
                "codepoint": f"U+{ord(ch):04X}",
                "source": source,
                "fallback_rank": int(fallback_rank),
                "fallback_chain_size": int(fallback_chain_size),
                "font_path": _font_path(selected_font, draw_fonts.font_path_map),
                "bold": bool(bold),
                "italic": bool(italic),
            }
        )

    for r in range(rows):
        y0 = render_pad_y + r * cell_h
        for c in range(cols):
            x0 = render_pad_x + c * cell_w
            cell = grid[r][c]
            draw.rectangle([x0, y0, x0 + cell_w, y0 + cell_h], fill=cell.style.bg)
            if cell.ch != " " and not cell.wide_continuation:
                if is_box_drawing_char(cell.ch):
                    draw_box_drawing_cell(
                        draw,
                        x0=x0,
                        y0=y0,
                        cell_w=cell_w,
                        cell_h=cell_h,
                        ch=cell.ch,
                        color=cell.style.fg,
                    )
                    record_provenance(
                        row=r,
                        col=c,
                        ch=cell.ch,
                        source="vector_box_v1",
                        selected_font=None,
                        fallback_rank=-1,
                        fallback_chain_size=0,
                        bold=cell.style.bold,
                        italic=cell.style.italic,
                    )
                elif is_block_element_char(cell.ch):
                    draw_block_element_cell(
                        draw,
                        x0=x0,
                        y0=y0,
                        cell_w=cell_w,
                        cell_h=cell_h,
                        ch=cell.ch,
                        color=cell.style.fg,
                    )
                    record_provenance(
                        row=r,
                        col=c,
                        ch=cell.ch,
                        source="vector_block_v1",
                        selected_font=None,
                        fallback_rank=-1,
                        fallback_chain_size=0,
                        bold=cell.style.bold,
                        italic=cell.style.italic,
                    )
                else:
                    # Match terminal behavior more closely: if the primary terminal font
                    # misses a glyph, fall back to secondary monospace coverage.
                    preferred_fonts: list[ImageFont.ImageFont | None]
                    symbol_preferred = (
                        list(draw_fonts.symbol_fallback_chain) or ([symbol_fallback] if symbol_fallback is not None else [])
                    ) if should_prefer_symbol_fallback(cell.ch) else []
                    if cell.style.bold and cell.style.italic:
                        preferred_fonts = [
                            *symbol_preferred,
                            font_bold_italic,
                            font_bold,
                            font_italic,
                            font_regular,
                            fallback_bold_italic,
                            fallback_bold,
                            fallback_italic,
                            fallback_regular,
                        ]
                    elif cell.style.bold:
                        preferred_fonts = [
                            *symbol_preferred,
                            font_bold,
                            font_regular,
                            fallback_bold,
                            fallback_regular,
                            fallback_bold_italic,
                            fallback_italic,
                        ]
                    elif cell.style.italic:
                        preferred_fonts = [
                            *symbol_preferred,
                            font_italic,
                            font_regular,
                            fallback_italic,
                            fallback_regular,
                            fallback_bold_italic,
                            fallback_bold,
                        ]
                    else:
                        preferred_fonts = [
                            *symbol_preferred,
                            font_regular,
                            fallback_regular,
                            fallback_bold,
                            fallback_italic,
                            fallback_bold_italic,
                        ]
                    deduped_fonts: list[ImageFont.ImageFont] = []
                    seen_font_ids: set[int] = set()
                    for candidate in preferred_fonts:
                        if candidate is None:
                            continue
                        fid = id(candidate)
                        if fid in seen_font_ids:
                            continue
                        seen_font_ids.add(fid)
                        deduped_fonts.append(candidate)
                    cell_font = font_regular
                    selected_rank = -1
                    for idx, candidate_font in enumerate(deduped_fonts):
                        if _font_supports_char(candidate_font, cell.ch, draw_fonts.codepoint_map):
                            cell_font = candidate_font or font_regular
                            selected_rank = idx
                            break
                    if selected_rank < 0 and is_vector_symbol_char(cell.ch):
                        draw_vector_symbol_cell(
                            draw,
                            x0=x0,
                            y0=y0,
                            cell_w=cell_w,
                            cell_h=cell_h,
                            ch=cell.ch,
                            color=cell.style.fg,
                        )
                        record_provenance(
                            row=r,
                            col=c,
                            ch=cell.ch,
                            source="vector_symbol_v1",
                            selected_font=None,
                            fallback_rank=-1,
                            fallback_chain_size=len(deduped_fonts),
                            bold=cell.style.bold,
                            italic=cell.style.italic,
                        )
                    else:
                        text_y = y0 + font_y_offset(cell_font)
                        draw.text((x0, text_y), cell.ch, font=cell_font, fill=cell.style.fg)
                        if cell.style.bold and cell_font == font_regular and font_bold is None:
                            draw.text((x0 + 1, text_y), cell.ch, font=cell_font, fill=cell.style.fg)
                        record_provenance(
                            row=r,
                            col=c,
                            ch=cell.ch,
                            source="font_chain",
                            selected_font=cell_font,
                            fallback_rank=selected_rank,
                            fallback_chain_size=len(deduped_fonts),
                            bold=cell.style.bold,
                            italic=cell.style.italic,
                        )
            if cell.style.underline:
                underline_y = (
                    y0 + baseline + render_underline_offset
                    if render_underline_offset >= 0
                    else y0 + cell_h - 3
                )
                underline_y = max(y0, min(y0 + cell_h - 1, underline_y))
                draw.line([x0, underline_y, x0 + cell_w - 1, underline_y], fill=cell.style.fg)

    if cursor_state is not None:
        cursor_x, cursor_y, cursor_visible = cursor_state
        if cursor_visible and 0 <= int(cursor_x) < cols and 0 <= int(cursor_y) < rows:
            x0 = render_pad_x + int(cursor_x) * cell_w
            y0 = render_pad_y + int(cursor_y) * cell_h
            draw.rectangle([x0, y0, x0 + cell_w - 1, y0 + cell_h - 1], fill=(255, 255, 255))

    if oversample > 1:
        base_w = max(1, int(round(img.width / float(oversample))))
        base_h = max(1, int(round(img.height / float(oversample))))
        img = img.resize((base_w, base_h), resample=_resample_filter(raster_downsample_resample))

    if scale and abs(scale - 1.0) > 1e-3:
        new_w = max(1, int(img.width * scale))
        new_h = max(1, int(img.height * scale))
        img = img.resize((new_w, new_h), resample=_resample_filter(scale_resample))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)


def render_ansi_with_xterm(
    ansi_path: Path,
    cols: int,
    rows: int,
    out_path: Path,
    bg_hex: str,
    fg_hex: str,
    font_name: str,
    font_size: int,
    sleep_seconds: float,
) -> None:
    for cmd in ("Xvfb", "xterm", "xwd", "convert", "xwininfo"):
        if not shutil.which(cmd):
            raise RuntimeError(f"Missing required command for xterm renderer: {cmd}")

    # Pick an unused display number.
    display_num = 90 + (int(time.time()) % 50)
    for _ in range(50):
        candidate = f"/tmp/.X11-unix/X{display_num}"
        if not Path(candidate).exists():
            break
        display_num += 1
    display = f":{display_num}"
    screen_w = max(640, cols * 12 + 40)
    screen_h = max(480, rows * 24 + 40)
    xvfb = subprocess.Popen(
        ["Xvfb", display, "-screen", "0", f"{screen_w}x{screen_h}x24", "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        env = os.environ.copy()
        env["DISPLAY"] = display
        time.sleep(0.1)
        title = f"tmux-ansi-render-{int(time.time() * 1000)}"
        render_cmd = (
            "printf '\\033[?1049h\\033[H\\033[2J'; "
            f"cat {shlex.quote(str(ansi_path))}; "
            f"sleep {sleep_seconds}"
        )
        xterm = subprocess.Popen(
            [
                "xterm",
                "-T",
                title,
                "-fa",
                font_name,
                "-fs",
                str(font_size),
                "-geometry",
                f"{cols}x{rows}",
                "+sb",
                "-b",
                "0",
                "-xrm",
                "XTerm*internalBorder:0",
                "-xrm",
                "XTerm*scrollBar:false",
                "-xrm",
                "XTerm*borderWidth:0",
                "-xrm",
                "XTerm*cursorBlink:false",
                "-bg",
                f"#{bg_hex}",
                "-fg",
                f"#{fg_hex}",
                "-e",
                "bash",
                "-lc",
                render_cmd,
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            window_id = ""
            for _ in range(80):
                try:
                    info = subprocess.check_output(
                        ["xwininfo", "-root", "-tree"],
                        env=env,
                        text=True,
                        errors="ignore",
                    )
                except subprocess.CalledProcessError:
                    time.sleep(0.05)
                    continue
                for line in info.splitlines():
                    if title in line:
                        window_id = line.strip().split()[0]
                        break
                if window_id:
                    break
                time.sleep(0.05)
            if not window_id:
                raise RuntimeError("xterm window not found for screenshot.")
            xwd = None
            for _ in range(20):
                try:
                    xwd = subprocess.check_output(["xwd", "-silent", "-id", window_id], env=env)
                    break
                except subprocess.CalledProcessError:
                    time.sleep(0.05)
            if xwd is None:
                raise RuntimeError("xwd failed to capture xterm window.")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["convert", "xwd:-", str(out_path)], input=xwd, check=True)
        finally:
            xterm.terminate()
            try:
                xterm.wait(timeout=1)
            except Exception:
                xterm.kill()
    finally:
        xvfb.terminate()
        try:
            xvfb.wait(timeout=1)
        except Exception:
            xvfb.kill()


def tmux_pane_size(target: str) -> Tuple[int, int]:
    out = subprocess.check_output(
        ["tmux", "display-message", "-p", "-t", target, "#{pane_width} #{pane_height}"],
        text=True,
    ).strip()
    parts = out.split()
    if len(parts) != 2:
        raise RuntimeError(f"Unexpected tmux size output: {out!r}")
    return int(parts[0]), int(parts[1])


def tmux_target_format(target: str, fmt: str) -> str:
    return subprocess.check_output(
        ["tmux", "display-message", "-p", "-t", target, fmt],
        text=True,
    ).strip()


def tmux_session_name(target: str) -> str:
    return tmux_target_format(target, "#{session_name}")


def tmux_window_size(target: str) -> Tuple[int, int]:
    out = tmux_target_format(target, "#{window_width} #{window_height}")
    parts = out.split()
    if len(parts) != 2:
        raise RuntimeError(f"Unexpected tmux window size output: {out!r}")
    return int(parts[0]), int(parts[1])


def tmux_cursor_state(target: str) -> tuple[int, int, bool] | None:
    out = tmux_target_format(target, "#{cursor_x} #{cursor_y} #{cursor_flag}")
    parts = out.split()
    if len(parts) != 3:
        return None
    try:
        x = int(parts[0])
        y = int(parts[1])
        visible = int(parts[2]) != 0
    except Exception:
        return None
    return (x, y, visible)


def tmux_focus_target(target: str) -> None:
    subprocess.run(["tmux", "select-window", "-t", target], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["tmux", "select-pane", "-t", target], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _terminate_process(proc: subprocess.Popen | None) -> None:
    if not proc:
        return
    proc.terminate()
    try:
        proc.wait(timeout=1)
    except Exception:
        proc.kill()


def attach_tmux_client_headless(
    target: str,
    cols: int,
    rows: int,
    bg_hex: str,
    fg_hex: str,
    font_name: str,
    font_size: int,
    sleep_seconds: float,
) -> tuple[subprocess.Popen, subprocess.Popen, dict]:
    for cmd in ("Xvfb", "xterm"):
        if not shutil.which(cmd):
            raise RuntimeError(f"Missing required command for attach: {cmd}")

    session_name = tmux_session_name(target)
    tmux_focus_target(target)

    display_num = 90 + (int(time.time()) % 50)
    for _ in range(50):
        candidate = f"/tmp/.X11-unix/X{display_num}"
        if not Path(candidate).exists():
            break
        display_num += 1
    display = f":{display_num}"
    screen_w = max(640, cols * 12 + 40)
    screen_h = max(480, rows * 24 + 40)
    xvfb = subprocess.Popen(
        ["Xvfb", display, "-screen", "0", f"{screen_w}x{screen_h}x24", "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    env = os.environ.copy()
    env["DISPLAY"] = display
    time.sleep(0.1)
    title = f"tmux-attach-{int(time.time() * 1000)}"
    attach_cmd = f"tmux attach -t {shlex.quote(session_name)} -r"
    xterm = subprocess.Popen(
        [
            "xterm",
            "-T",
            title,
            "-fa",
            font_name,
            "-fs",
            str(font_size),
            "-geometry",
            f"{cols}x{rows}",
            "+sb",
            "-b",
            "0",
            "-xrm",
            "XTerm*internalBorder:0",
            "-xrm",
            "XTerm*scrollBar:false",
            "-xrm",
            "XTerm*borderWidth:0",
            "-xrm",
            "XTerm*cursorBlink:false",
            "-bg",
            f"#{bg_hex}",
            "-fg",
            f"#{fg_hex}",
            "-e",
            "bash",
            "-lc",
            attach_cmd,
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(max(0.0, sleep_seconds))
    return xvfb, xterm, env


def tmux_capture(
    target: str,
    out_ansi: Path,
    out_txt: Path,
    out_scrollback_ansi: Path | None = None,
    out_scrollback_txt: Path | None = None,
) -> None:
    out_ansi.parent.mkdir(parents=True, exist_ok=True)
    out_txt.parent.mkdir(parents=True, exist_ok=True)
    # -e: include escape sequences; -p: print to stdout
    ansi = subprocess.check_output(["tmux", "capture-pane", "-eNp", "-t", target], text=False)
    out_ansi.write_bytes(ansi)
    txt = subprocess.check_output(["tmux", "capture-pane", "-pJN", "-t", target], text=True)
    out_txt.write_text(txt)
    if out_scrollback_ansi is not None:
        ansi_full = subprocess.check_output(
            ["tmux", "capture-pane", "-eNp", "-t", target, "-S", "-"],
            text=False,
        )
        out_scrollback_ansi.write_bytes(ansi_full)
    if out_scrollback_txt is not None:
        txt_full = subprocess.check_output(
            ["tmux", "capture-pane", "-pJN", "-t", target, "-S", "-"],
            text=True,
        )
        out_scrollback_txt.write_text(txt_full)


def resolve_font_paths(
    regular: str,
    bold: str,
    italic: str,
    bold_italic: str,
) -> dict:
    if regular:
        base = {
            "regular": regular,
            "bold": bold,
            "italic": italic,
            "bold_italic": bold_italic,
        }
    else:
        base = {}
        for cand in FONT_CANDIDATES:
            if Path(cand["regular"]).exists():
                base = cand
                break
    resolved = {}
    for key in ("regular", "bold", "italic", "bold_italic"):
        override = {"regular": regular, "bold": bold, "italic": italic, "bold_italic": bold_italic}[key]
        candidate = base.get(key)
        chosen = override or candidate or ""
        resolved[key] = chosen if chosen and Path(chosen).exists() else ""
    return resolved


def strip_ansi(payload: str) -> str:
    payload = CSI_RE.sub("", payload)
    while True:
        start = payload.find(OSC_START)
        if start == -1:
            break
        end = payload.find("\x07", start + 2)
        esc_end = payload.find(OSC_END, start + 2)
        if esc_end != -1 and (end == -1 or esc_end < end):
            end = esc_end + len(OSC_END)
        elif end != -1:
            end = end + 1
        if end == -1:
            payload = payload[:start]
            break
        payload = payload[:start] + payload[end:]
    return payload


def _display_width(text: str) -> int:
    width = 0
    for ch in text:
        w = wcwidth(ch)
        if w > 0:
            width += w
    return width


def count_rows_from_text(text: str, cols: int) -> int:
    cols = max(1, int(cols))
    total = 0
    for line in text.splitlines():
        width = _display_width(line)
        if width <= 0:
            total += 1
        else:
            total += (width + cols - 1) // cols
    return max(1, total)


def count_ansi_rows(ansi_text: str, cols: int) -> int:
    cols = max(1, int(cols))
    row = 0
    col = 0
    idx = 0
    max_row = 0

    while idx < len(ansi_text):
        ch = ansi_text[idx]
        if ch == "\x1b":
            if idx + 1 < len(ansi_text) and ansi_text[idx + 1] == "]":
                osc_end = ansi_text.find("\x07", idx + 2)
                st_end = ansi_text.find("\x1b\\", idx + 2)
                if st_end != -1 and (osc_end == -1 or st_end < osc_end):
                    idx = st_end + 2
                    continue
                if osc_end != -1:
                    idx = osc_end + 1
                    continue
                idx += 1
                continue
            m = CSI_RE.match(ansi_text, idx)
            if m:
                params_raw, final = m.group(1), m.group(2)
                idx = m.end()
                params = [p for p in (params_raw[1:] if params_raw.startswith("?") else params_raw).split(";") if p]
                nums = []
                for p in params:
                    try:
                        nums.append(int(p))
                    except ValueError:
                        pass
                if final in ("H", "f"):
                    row_val = nums[0] if len(nums) >= 1 and nums[0] > 0 else 1
                    col_val = nums[1] if len(nums) >= 2 and nums[1] > 0 else 1
                    row = max(0, row_val - 1)
                    col = max(0, col_val - 1)
                elif final == "A":
                    n = nums[0] if nums else 1
                    row = max(0, row - n)
                elif final == "B":
                    n = nums[0] if nums else 1
                    row = row + n
                elif final == "C":
                    n = nums[0] if nums else 1
                    col = col + n
                elif final == "D":
                    n = nums[0] if nums else 1
                    col = max(0, col - n)
                max_row = max(max_row, row)
                continue
            idx += 1
            continue

        if ch == "\n":
            row += 1
            col = 0
            max_row = max(max_row, row)
            idx += 1
            continue
        if ch == "\r":
            col = 0
            idx += 1
            continue
        if ch == "\t":
            next_tab = ((col // 8) + 1) * 8
            if next_tab >= cols:
                row += next_tab // cols
                col = next_tab % cols
            else:
                col = next_tab
            max_row = max(max_row, row)
            idx += 1
            continue
        if ord(ch) < 32:
            idx += 1
            continue

        w = wcwidth(ch)
        if w <= 0:
            idx += 1
            continue
        col += w
        if col >= cols:
            row += col // cols
            col = col % cols
        max_row = max(max_row, row)
        idx += 1

    return max_row + 1


def apply_row_controls(rows: int, *, rows_override: int = 0, max_rows: int = 0) -> int:
    resolved = int(rows)
    if int(rows_override) > 0:
        resolved = int(rows_override)
    if int(max_rows) > 0:
        resolved = min(resolved, int(max_rows))
    return max(1, resolved)


def main() -> None:
    argv_raw = list(os.sys.argv[1:])
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", help="tmux pane target (e.g. session:window.pane or session:window)")
    parser.add_argument("--ansi", help="path to ANSI capture file (skips tmux)")
    parser.add_argument("--cols", type=int, default=0, help="columns when rendering --ansi capture")
    parser.add_argument("--rows", type=int, default=0, help="rows when rendering --ansi capture")
    parser.add_argument("--out", default="", help="output PNG path (default: docs_tmp/tmux_captures/<ts>_<target>.png)")
    env_bg = os.environ.get("BREADBOARD_TERMINAL_BG", "").strip().lstrip("#") or "1f2430"
    env_line_gap_raw = os.environ.get("BREADBOARD_TERMINAL_LINE_GAP", "").strip()
    env_line_gap = int(env_line_gap_raw) if env_line_gap_raw else 0
    env_font = os.environ.get("BREADBOARD_TERMINAL_FONT", "").strip()
    parser.add_argument("--bg", default=env_bg, help="default background hex (e.g. 1f2430)")
    parser.add_argument("--fg", default="f1f5f9", help="default foreground hex (e.g. f1f5f9)")
    parser.add_argument("--font-size", type=int, default=18, help="font size for rendering")
    parser.add_argument("--font-path", default=env_font, help="path to regular font (ttf/otf)")
    parser.add_argument("--font-bold-path", default="", help="path to bold font (ttf/otf)")
    parser.add_argument("--font-italic-path", default="", help="path to italic font (ttf/otf)")
    parser.add_argument("--font-bold-italic-path", default="", help="path to bold-italic font (ttf/otf)")
    parser.add_argument(
        "--renderer",
        choices=["pillow", "xterm-xvfb"],
        default="pillow",
        help="render backend (pillow draws cells; xterm-xvfb screenshots real xterm)",
    )
    parser.add_argument("--xterm-font", default="DejaVu Sans Mono", help="xterm font name (Xft)")
    parser.add_argument("--xterm-sleep", type=float, default=0.2, help="seconds to wait for xterm render")
    parser.add_argument("--scale", type=float, default=1.0, help="scale output image (e.g. 0.75)")
    parser.add_argument("--cell-width", type=int, default=0, help="override cell width in pixels")
    parser.add_argument("--cell-height", type=int, default=0, help="override cell height in pixels")
    parser.add_argument("--line-gap", type=int, default=env_line_gap, help="extra vertical pixels between rows")
    parser.add_argument("--pad-x", type=int, default=0, help="horizontal padding in pixels")
    parser.add_argument("--pad-y", type=int, default=0, help="vertical padding in pixels")
    parser.add_argument(
        "--baseline-offset",
        type=int,
        default=-1,
        help="baseline offset from cell top in pixels (-1 uses ascent).",
    )
    parser.add_argument(
        "--underline-offset",
        type=int,
        default=-1,
        help="underline offset from baseline in pixels (-1 uses legacy bottom-3).",
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="validate render profile lock assets and print lock manifest, then exit.",
    )
    parser.add_argument("--scrollback", action="store_true", help="also capture full scrollback to .scrollback.txt/.ansi")
    parser.add_argument(
        "--scrollback-render",
        action="store_true",
        help="render the full scrollback into a single tall PNG",
    )
    parser.add_argument("--keep", action="store_true", help="keep alongside .ansi and .txt (default keeps)")
    parser.add_argument(
        "--snapshot",
        action="store_true",
        help="render ANSI as a top-of-buffer snapshot (do not scroll; stop when rows are filled)",
    )
    parser.add_argument(
        "--attach",
        action="store_true",
        help="attach a headless tmux client before capture (helps render input bars)",
    )
    parser.add_argument(
        "--attach-sleep",
        type=float,
        default=0.4,
        help="seconds to wait after attaching before capture",
    )
    parser.add_argument(
        "--render-profile",
        default=DEFAULT_RENDER_PROFILE_ID,
        choices=SUPPORTED_RENDER_PROFILES,
        help=(
            "render profile lock. "
            f"default={DEFAULT_RENDER_PROFILE_ID}; use '{LEGACY_PROFILE_ID}' for ad-hoc overrides."
        ),
    )
    parser.add_argument(
        "--height-mode",
        choices=["pane", "scrollback"],
        default="pane",
        help=(
            "render height strategy. 'pane' keeps fixed pane/window height; "
            "'scrollback' renders full captured history as a tall image."
        ),
    )
    parser.add_argument(
        "--rows-override",
        type=int,
        default=0,
        help="force final rendered row count (>0 overrides pane/scrollback-derived height)",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=0,
        help="cap final rendered row count (>0 limits pane/scrollback-derived or overridden height)",
    )
    parser.add_argument(
        "--glyph-provenance",
        choices=["off", "interesting", "all"],
        default="interesting",
        help="emit glyph provenance JSON sidecar (off, interesting non-ASCII/fallback/vector, or all)",
    )
    args = parser.parse_args()

    profile = resolve_render_profile(args.render_profile)
    if profile is not None:
        assert_render_profile_lock(profile)
        forbidden = sorted(
            flag for flag in PROFILE_CONTROLLED_FLAGS if argv_contains_flag(argv_raw, flag)
        )
        if forbidden:
            raise SystemExit(
                "render profile lock is enabled; remove profile-controlled overrides "
                f"or pass --render-profile {LEGACY_PROFILE_ID}: {', '.join(forbidden)}"
            )
        locked_overrides = append_profile_cli_args([], profile)
        # Apply deterministic renderer settings after argparse resolution.
        for i in range(0, len(locked_overrides), 2):
            key = locked_overrides[i].lstrip("-").replace("-", "_")
            value = locked_overrides[i + 1]
            if key in {
                "font_size",
                "cell_width",
                "cell_height",
                "line_gap",
                "pad_x",
                "pad_y",
                "baseline_offset",
                "underline_offset",
            }:
                setattr(args, key, int(value))
            elif key == "scale":
                setattr(args, key, float(value))
            else:
                setattr(args, key, value)
        if args.self_check:
            payload = profile_lock_manifest(profile)
            print(json.dumps(payload, indent=2))
            print("render-lock:self-check:ok")
            return

    if not args.target and not args.ansi:
        raise SystemExit("Provide --target (tmux) or --ansi (file) to render.")

    ts = time.strftime("%Y%m%d-%H%M%S")
    row_source = "unknown"
    height_mode_effective = str(args.height_mode)
    use_scrollback_render = bool(args.scrollback_render) or str(args.height_mode) == "scrollback"
    if args.ansi:
        ansi_path = Path(args.ansi).expanduser().resolve()
        if not ansi_path.exists():
            raise SystemExit(f"ANSI capture not found: {ansi_path}")
        safe_target = re.sub(r"[^A-Za-z0-9_.-]+", "_", ansi_path.stem)
        default_dir = Path("docs_tmp") / "ansi_captures"
        out_png = Path(args.out) if args.out else (default_dir / f"{ts}_{safe_target}.png")
        out_ansi = ansi_path
        out_txt = out_png.with_suffix(".txt")
        cols = args.cols
        rows = args.rows
        if cols <= 0:
            raise SystemExit("When using --ansi, please provide --cols.")
        ansi_text = out_ansi.read_text(errors="replace")
        out_txt.write_text(strip_ansi(ansi_text))
        if rows <= 0:
            if str(args.height_mode) == "scrollback":
                rows = count_ansi_rows(ansi_text, cols)
                row_source = "ansi_scrollback_inferred"
                height_mode_effective = "scrollback"
            elif int(args.rows_override) > 0:
                rows = int(args.rows_override)
                row_source = "rows_override_only"
            else:
                raise SystemExit(
                    "When using --ansi with fixed height, provide --rows or --rows-override "
                    "(or pass --height-mode scrollback)."
                )
        else:
            row_source = "ansi_rows_arg"
        rows = apply_row_controls(rows, rows_override=args.rows_override, max_rows=args.max_rows)
    else:
        cols, rows = tmux_pane_size(args.target)
        row_source = "pane_size"
        height_mode_effective = "scrollback" if use_scrollback_render else "pane"
        cursor_state = None
        attach_cols, attach_rows = cols, rows
        if args.attach:
            try:
                attach_cols, attach_rows = tmux_window_size(args.target)
            except Exception:
                attach_cols, attach_rows = cols, rows
        safe_target = re.sub(r"[^A-Za-z0-9_.-]+", "_", args.target)
        default_dir = Path("docs_tmp") / "tmux_captures"
        out_png = Path(args.out) if args.out else (default_dir / f"{ts}_{safe_target}.png")
        out_ansi = out_png.with_suffix(".ansi")
        out_txt = out_png.with_suffix(".txt")
        want_scrollback = args.scrollback or use_scrollback_render
        scroll_ansi = out_png.with_suffix(".scrollback.ansi") if want_scrollback else None
        scroll_txt = out_png.with_suffix(".scrollback.txt") if want_scrollback else None
        xvfb_proc = None
        xterm_proc = None
        if args.attach:
            xvfb_proc, xterm_proc, _ = attach_tmux_client_headless(
                target=args.target,
                cols=attach_cols,
                rows=attach_rows,
                bg_hex=args.bg,
                fg_hex=args.fg,
                font_name=args.xterm_font,
                font_size=args.font_size,
                sleep_seconds=args.attach_sleep,
            )
        try:
            tmux_capture(args.target, out_ansi, out_txt, scroll_ansi, scroll_txt)
            if not use_scrollback_render:
                cursor_state = tmux_cursor_state(args.target)
        finally:
            _terminate_process(xterm_proc)
            _terminate_process(xvfb_proc)
        if use_scrollback_render and scroll_ansi and scroll_txt:
            ansi_text = scroll_ansi.read_text(errors="replace")
            try:
                rows = count_rows_from_text(scroll_txt.read_text(errors="replace"), cols)
                row_source = "scrollback_text"
            except Exception:
                rows = count_ansi_rows(ansi_text, cols)
                row_source = "scrollback_ansi_fallback"
        else:
            ansi_text = out_ansi.read_text(errors="replace")
            row_source = "pane_size"
        rows = apply_row_controls(rows, rows_override=args.rows_override, max_rows=args.max_rows)
    font_paths = resolve_font_paths(
        args.font_path,
        args.font_bold_path,
        args.font_italic_path,
        args.font_bold_italic_path,
    )
    font_regular = (
        ImageFont.truetype(font_paths["regular"], args.font_size)
        if font_paths.get("regular")
        else ImageFont.load_default()
    )
    font_bold = (
        ImageFont.truetype(font_paths["bold"], args.font_size)
        if font_paths.get("bold")
        else None
    )
    font_italic = (
        ImageFont.truetype(font_paths["italic"], args.font_size)
        if font_paths.get("italic")
        else None
    )
    font_bold_italic = (
        ImageFont.truetype(font_paths["bold_italic"], args.font_size)
        if font_paths.get("bold_italic")
        else None
    )
    fallback_paths = resolve_font_paths("", "", "", "")
    fallback_regular = (
        ImageFont.truetype(fallback_paths["regular"], args.font_size)
        if fallback_paths.get("regular")
        else None
    )
    fallback_bold = (
        ImageFont.truetype(fallback_paths["bold"], args.font_size)
        if fallback_paths.get("bold")
        else None
    )
    fallback_italic = (
        ImageFont.truetype(fallback_paths["italic"], args.font_size)
        if fallback_paths.get("italic")
        else None
    )
    fallback_bold_italic = (
        ImageFont.truetype(fallback_paths["bold_italic"], args.font_size)
        if fallback_paths.get("bold_italic")
        else None
    )
    symbol_fallback_candidates = resolve_symbol_fallback_candidates(str(args.render_profile))
    symbol_fallback_paths = resolve_existing_paths(symbol_fallback_candidates)
    symbol_fallback_chain: list[ImageFont.ImageFont] = []
    for symbol_font_path in symbol_fallback_paths:
        try:
            symbol_fallback_chain.append(ImageFont.truetype(symbol_font_path, args.font_size))
        except Exception:
            continue
    symbol_fallback = symbol_fallback_chain[0] if symbol_fallback_chain else None
    codepoint_map: dict[int, set[int] | None] = {}
    font_path_map: dict[int, str] = {}

    def _register_font(loaded_font: ImageFont.ImageFont | None, explicit_path: str = "") -> None:
        if loaded_font is None:
            return
        codepoint_map[id(loaded_font)] = _load_font_codepoints(loaded_font)
        resolved_path = explicit_path or str(getattr(loaded_font, "path", "") or "")
        if resolved_path:
            font_path_map[id(loaded_font)] = resolved_path

    _register_font(font_regular, str(font_paths.get("regular") or ""))
    _register_font(font_bold, str(font_paths.get("bold") or ""))
    _register_font(font_italic, str(font_paths.get("italic") or ""))
    _register_font(font_bold_italic, str(font_paths.get("bold_italic") or ""))
    for symbol_font_path, symbol_font in zip(symbol_fallback_paths, symbol_fallback_chain):
        _register_font(symbol_font, symbol_font_path)
    _register_font(fallback_regular, str(fallback_paths.get("regular") or ""))
    _register_font(fallback_bold, str(fallback_paths.get("bold") or ""))
    _register_font(fallback_italic, str(fallback_paths.get("italic") or ""))
    _register_font(fallback_bold_italic, str(fallback_paths.get("bold_italic") or ""))
    fonts = FontPack(
        regular=font_regular,
        bold=font_bold,
        italic=font_italic,
        bold_italic=font_bold_italic,
        symbol_fallback=symbol_fallback,
        symbol_fallback_chain=tuple(symbol_fallback_chain),
        fallback_regular=fallback_regular,
        fallback_bold=fallback_bold,
        fallback_italic=fallback_italic,
        fallback_bold_italic=fallback_bold_italic,
        codepoint_map=codepoint_map,
        font_path_map=font_path_map,
    )
    resolved_cell_width, resolved_cell_height = resolve_cell_geometry(
        font_regular,
        line_gap=args.line_gap,
        cell_width_override=args.cell_width,
        cell_height_override=args.cell_height,
    )
    if args.renderer == "xterm-xvfb" and use_scrollback_render:
        print("warning: --scrollback-render not supported by xterm-xvfb; falling back to pillow renderer")
        args.renderer = "pillow"

    snapshot_mode = bool(args.target) or args.snapshot
    glyph_provenance_entries: list[dict[str, object]] = []
    if args.renderer == "xterm-xvfb":
        if not out_ansi.exists():
            out_ansi.write_text(ansi_text)
        render_ansi_with_xterm(
            ansi_path=out_ansi,
            cols=cols,
            rows=rows,
            out_path=out_png,
            bg_hex=args.bg,
            fg_hex=args.fg,
            font_name=args.xterm_font,
            font_size=args.font_size,
            sleep_seconds=args.xterm_sleep,
        )
    else:
        vs_code_like_mode = str(args.render_profile) in VS_CODE_LIKE_RENDER_PROFILES
        render_ansi_to_png(
            ansi_text=ansi_text,
            cols=cols,
            rows=rows,
            out_path=out_png,
            fg_default=parse_rgb(args.fg),
            bg_default=parse_rgb(args.bg),
            scale=args.scale,
            fonts=fonts,
            cell_width=args.cell_width,
            cell_height=args.cell_height,
            line_gap=args.line_gap,
            pad_x=args.pad_x,
            pad_y=args.pad_y,
            baseline_offset=args.baseline_offset,
            underline_offset=args.underline_offset,
            font_size=args.font_size,
            raster_oversample=2 if vs_code_like_mode else 1,
            raster_downsample_resample="lanczos" if vs_code_like_mode else "nearest",
            scale_resample="lanczos" if vs_code_like_mode else "nearest",
            snapshot_mode=snapshot_mode,
            cursor_state=cursor_state if args.target and not use_scrollback_render else None,
            glyph_provenance=glyph_provenance_entries,
            glyph_provenance_mode=str(args.glyph_provenance),
        )

    glyph_provenance_path: Path | None = None
    if str(args.glyph_provenance).strip().lower() != "off":
        glyph_provenance_path = out_png.with_suffix(".glyph_provenance.json")
        glyph_provenance_payload = {
            "schema_version": "tmux_glyph_provenance_v1",
            "render_profile": str(args.render_profile),
            "mode": str(args.glyph_provenance),
            "count": int(len(glyph_provenance_entries)),
            "items": glyph_provenance_entries,
        }
        glyph_provenance_path.write_text(
            json.dumps(glyph_provenance_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    row_occupancy = summarize_row_occupancy_from_paths(
        render_png=out_png,
        render_txt=out_txt,
        bg_default=parse_rgb(args.bg),
        cell_height=int(resolved_cell_height),
        pad_y=int(args.pad_y),
        declared_rows=int(rows),
    )
    row_parity_summary_path = out_png.with_suffix(".row_parity.json")
    row_parity_summary_payload = build_row_parity_summary(
        row_occupancy=row_occupancy,
        render_png=out_png,
        render_txt=out_txt,
        render_profile=str(args.render_profile),
    )
    row_parity_summary_path.write_text(
        json.dumps(row_parity_summary_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    render_lock_payload = {
        "schema_version": "tmux_render_lock_frame_v1",
        "render_profile": str(args.render_profile),
        "render_profile_locked": profile is not None,
        "render_profile_manifest": profile_lock_manifest(profile) if profile is not None else None,
        "renderer": str(args.renderer),
        "snapshot_mode": bool(snapshot_mode),
        "height_mode": str(height_mode_effective),
        "row_source": str(row_source),
        "render_policies": {
            "text_y_anchor": "baseline_offset",
            "underline_y_anchor": "baseline_plus_underline_offset",
            "wide_cell_overflow": "clip_line_until_newline_in_snapshot_mode",
            "wide_continuation": "reserve_next_cell_no_glyph",
            "box_drawing": "vector_stroke_v1",
            "vector_symbol": "vector_symbol_v1_for_U25A3",
            "cursor_overlay": "tmux_cursor_block_v1_when_available",
            "glyph_fallback": "primary_style_then_monospace_fallback_chain_v1",
            "symbol_fallback_chain": symbol_fallback_paths,
            "aa_mode": (
                "oversample2_lanczos_for_vscode_like_profiles"
                if str(args.render_profile) in VS_CODE_LIKE_RENDER_PROFILES
                else "legacy_nearest"
            ),
            "glyph_provenance_mode": str(args.glyph_provenance),
            "rows_override": int(args.rows_override),
            "max_rows": int(args.max_rows),
        },
        "cursor": (
            {
                "x": int(cursor_state[0]),
                "y": int(cursor_state[1]),
                "visible": bool(cursor_state[2]),
            }
            if args.target and not use_scrollback_render and cursor_state is not None
            else None
        ),
        "geometry": {
            "cols": int(cols),
            "rows": int(rows),
            "cell_width": int(resolved_cell_width),
            "cell_height": int(resolved_cell_height),
            "line_gap": int(args.line_gap),
            "pad_x": int(args.pad_x),
            "pad_y": int(args.pad_y),
            "baseline_offset": int(args.baseline_offset),
            "underline_offset": int(args.underline_offset),
        },
        "theme": {
            "bg": str(args.bg),
            "fg": str(args.fg),
        },
        "artifacts": {
            "ansi_basename": out_ansi.name,
            "text_basename": out_txt.name,
            "png_basename": out_png.name,
            "row_parity_summary_basename": row_parity_summary_path.name,
            "glyph_provenance_basename": glyph_provenance_path.name if glyph_provenance_path else None,
            "ansi_sha256": sha256_file(out_ansi) if out_ansi.exists() else None,
            "text_sha256": sha256_file(out_txt) if out_txt.exists() else None,
            "png_sha256": sha256_file(out_png),
            "row_parity_summary_sha256": sha256_file(row_parity_summary_path),
            "glyph_provenance_sha256": (
                sha256_file(glyph_provenance_path) if glyph_provenance_path and glyph_provenance_path.exists() else None
            ),
        },
        "row_occupancy": row_occupancy,
    }
    render_lock_path = out_png.with_suffix(".render_lock.json")
    render_lock_path.write_text(
        json.dumps(render_lock_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    if args.target:
        print(f"target: {args.target}")
    if args.ansi:
        print(f"ansi:   {out_ansi}")
    if (args.scrollback or args.scrollback_render) and not args.ansi:
        print(f"scroll: {scroll_txt}")
    print(f"profile:{args.render_profile}")
    print(f"height: {height_mode_effective} ({row_source})")
    print(f"size:   {cols}x{rows}")
    print(f"text:   {out_txt}")
    print(f"png:    {out_png}")
    print(f"lock:   {render_lock_path}")
    print(f"parity: {row_parity_summary_path}")
    if glyph_provenance_path is not None:
        print(f"glyphs: {glyph_provenance_path}")

    if not args.keep and not args.ansi:
        try:
            out_ansi.unlink(missing_ok=True)
            out_txt.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
