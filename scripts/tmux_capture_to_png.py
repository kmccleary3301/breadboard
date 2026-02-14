#!/usr/bin/env python3
"""
Capture a tmux pane (including ANSI colors) and render it to a PNG.

Why:
- Lets agentic tooling compare terminal UIs with full color fidelity (Claude/Codex/OpenCode/Breadboard/Crush/etc).
- Produces both the raw ANSI capture and a rendered PNG for quick review/diffing.

Usage:
  python scripts/tmux_capture_to_png.py --target crush_color_demo:0
  python scripts/tmux_capture_to_png.py --target cc_transcript_viewer_manual:0 --out docs_tmp/captures/cc.png

Notes:
- Requires: tmux, Python Pillow (PIL), wcwidth.
- This renders xterm-256 and truecolor SGR sequences (38/48;5 and 38/48;2).
- Default background/foreground are approximations; adjust with --bg/--fg to match your terminal theme.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple

from PIL import Image, ImageDraw, ImageFont
from wcwidth import wcwidth


DEFAULT_FG = (235, 235, 235)
DEFAULT_BG = (38, 38, 38)


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


CSI_RE = re.compile(r"\x1b\[([0-9;?]*)([@-~])")


def render_ansi_to_png(
    ansi_text: str,
    cols: int,
    rows: int,
    out_path: Path,
    fg_default: Tuple[int, int, int],
    bg_default: Tuple[int, int, int],
    font_size: int,
) -> None:
    cols = max(1, int(cols))
    rows = max(1, int(rows))

    # Apply user defaults
    global DEFAULT_FG, DEFAULT_BG
    DEFAULT_FG = fg_default
    DEFAULT_BG = bg_default

    grid = [[Cell(" ", Style()) for _ in range(cols)] for _ in range(rows)]

    style = Style()
    row = 0
    col = 0
    idx = 0

    while idx < len(ansi_text) and row < rows:
        ch = ansi_text[idx]
        if ch == "\x1b":
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
                elif final == "K":
                    # erase to end of line
                    if 0 <= row < rows:
                        for c in range(col, cols):
                            grid[row][c] = Cell(" ", style.copy())
                # ignore other CSI
                continue
            idx += 1
            continue

        if ch == "\n":
            row += 1
            col = 0
            idx += 1
            continue
        if ch == "\r":
            col = 0
            idx += 1
            continue
        if ch == "\t":
            next_tab = ((col // 8) + 1) * 8
            while col < next_tab and col < cols:
                grid[row][col] = Cell(" ", style.copy())
                col += 1
            idx += 1
            continue
        if ord(ch) < 32:
            idx += 1
            continue

        w = wcwidth(ch)
        if w <= 0:
            idx += 1
            continue

        fg_eff, bg_eff = style.effective_colors()
        effective_style = Style(fg_eff, bg_eff, style.bold, style.dim, style.underline, style.italic, style.inverse)

        if col < cols and row < rows:
            grid[row][col] = Cell(ch, effective_style)
        col += 1
        if w == 2 and col < cols:
            grid[row][col] = Cell(" ", effective_style)
            col += 1

        if col >= cols:
            row += 1
            col = 0

        idx += 1

    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
    ]
    font_path = next((p for p in font_paths if Path(p).exists()), None)
    font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()

    bbox = font.getbbox("M")
    cell_w = max(8, bbox[2] - bbox[0])
    ascent, descent = font.getmetrics()
    cell_h = max(16, ascent + descent + 4)

    img = Image.new("RGB", (cols * cell_w, rows * cell_h), bg_default)
    draw = ImageDraw.Draw(img)

    for r in range(rows):
        y0 = r * cell_h
        for c in range(cols):
            x0 = c * cell_w
            cell = grid[r][c]
            draw.rectangle([x0, y0, x0 + cell_w, y0 + cell_h], fill=cell.style.bg)
            if cell.ch != " ":
                draw.text((x0, y0 + 1), cell.ch, font=font, fill=cell.style.fg)
                if cell.style.bold:
                    draw.text((x0 + 1, y0 + 1), cell.ch, font=font, fill=cell.style.fg)
            if cell.style.underline:
                underline_y = y0 + cell_h - 3
                draw.line([x0, underline_y, x0 + cell_w - 1, underline_y], fill=cell.style.fg)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)


def tmux_base_args(socket_name: str) -> list[str]:
    socket_name = (socket_name or "").strip()
    if not socket_name:
        return ["tmux"]
    return ["tmux", "-L", socket_name]


def tmux_pane_size(target: str, socket_name: str = "") -> Tuple[int, int]:
    out = subprocess.check_output(
        [*tmux_base_args(socket_name), "display-message", "-p", "-t", target, "#{pane_width} #{pane_height}"],
        text=True,
    ).strip()
    parts = out.split()
    if len(parts) != 2:
        raise RuntimeError(f"Unexpected tmux size output: {out!r}")
    return int(parts[0]), int(parts[1])


def tmux_capture(target: str, out_ansi: Path, out_txt: Path, socket_name: str = "") -> None:
    out_ansi.parent.mkdir(parents=True, exist_ok=True)
    out_txt.parent.mkdir(parents=True, exist_ok=True)
    # -e: include escape sequences; -p: print to stdout
    ansi = subprocess.check_output([*tmux_base_args(socket_name), "capture-pane", "-ep", "-t", target], text=False)
    out_ansi.write_bytes(ansi)
    txt = subprocess.check_output([*tmux_base_args(socket_name), "capture-pane", "-p", "-t", target], text=True)
    out_txt.write_text(txt)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--target",
        default="",
        help="tmux pane target (e.g. session:window.pane or session:window). Required unless --ansi is provided.",
    )
    parser.add_argument(
        "--tmux-socket",
        default="",
        help="tmux socket name (tmux -L <name>). Use this for isolated capture servers.",
    )
    parser.add_argument(
        "--ansi",
        default="",
        help="render from an existing ANSI capture file instead of capturing from tmux",
    )
    parser.add_argument("--cols", type=int, default=0, help="required with --ansi: terminal columns")
    parser.add_argument("--rows", type=int, default=0, help="required with --ansi: terminal rows")
    parser.add_argument(
        "--out",
        default="",
        help="output PNG path (default: docs_tmp/tmux_captures/<ts>_<target>.png). Required with --ansi.",
    )
    parser.add_argument("--bg", default="262626", help="default background hex (e.g. 262626)")
    parser.add_argument("--fg", default="EBEBEB", help="default foreground hex (e.g. EBEBEB)")
    parser.add_argument("--font-size", type=int, default=18, help="font size for rendering")
    parser.add_argument("--keep", action="store_true", help="keep alongside .ansi and .txt (default keeps)")
    args = parser.parse_args()

    ansi_path = Path(args.ansi).expanduser().resolve() if args.ansi else None
    target = str(args.target or "").strip()
    tmux_socket = str(args.tmux_socket or "").strip()

    if ansi_path is None and not target:
        raise SystemExit("must provide either --target or --ansi")

    if ansi_path is not None:
        if not ansi_path.exists():
            raise SystemExit(f"ansi file not found: {ansi_path}")
        cols = int(args.cols)
        rows = int(args.rows)
        if cols <= 0 or rows <= 0:
            raise SystemExit("--cols and --rows are required and must be >0 when using --ansi")
        if not args.out:
            raise SystemExit("--out is required when using --ansi")
        out_png = Path(args.out).expanduser().resolve()
        ansi_text = ansi_path.read_text(errors="replace")
        render_ansi_to_png(
            ansi_text=ansi_text,
            cols=cols,
            rows=rows,
            out_path=out_png,
            fg_default=parse_rgb(args.fg),
            bg_default=parse_rgb(args.bg),
            font_size=args.font_size,
        )
        print(f"ansi: {ansi_path}")
        print(f"size: {cols}x{rows}")
        print(f"png:  {out_png}")
        return

    cols, rows = tmux_pane_size(target, socket_name=tmux_socket)
    ts = time.strftime("%Y%m%d-%H%M%S")
    safe_target = re.sub(r"[^A-Za-z0-9_.-]+", "_", target)
    default_dir = Path("docs_tmp") / "tmux_captures"
    out_png = Path(args.out) if args.out else (default_dir / f"{ts}_{safe_target}.png")
    out_ansi = out_png.with_suffix(".ansi")
    out_txt = out_png.with_suffix(".txt")

    tmux_capture(target, out_ansi, out_txt, socket_name=tmux_socket)
    ansi_text = out_ansi.read_text(errors="replace")
    render_ansi_to_png(
        ansi_text=ansi_text,
        cols=cols,
        rows=rows,
        out_path=out_png,
        fg_default=parse_rgb(args.fg),
        bg_default=parse_rgb(args.bg),
        font_size=args.font_size,
    )

    print(f"target: {target}")
    print(f"size:   {cols}x{rows}")
    print(f"ansi:   {out_ansi}")
    print(f"text:   {out_txt}")
    print(f"png:    {out_png}")

    if not args.keep:
        try:
            out_ansi.unlink(missing_ok=True)
            out_txt.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
