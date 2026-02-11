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

Notes:
- Requires: tmux, Python Pillow (PIL), wcwidth.
- This renders xterm-256 and truecolor SGR sequences (38/48;5 and 38/48;2).
- Default background/foreground are approximations; adjust with --bg/--fg to match your terminal theme.
- Optional renderer: --renderer xterm-xvfb uses a real xterm + Xvfb + xwd + ImageMagick convert.
"""

from __future__ import annotations

import argparse
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


DEFAULT_FG = (241, 245, 249)
DEFAULT_BG = (15, 23, 42)


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


@dataclass
class FontPack:
    regular: ImageFont.ImageFont
    bold: ImageFont.ImageFont | None = None
    italic: ImageFont.ImageFont | None = None
    bold_italic: ImageFont.ImageFont | None = None


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
    pad_x: int,
    pad_y: int,
) -> None:
    cols = max(1, int(cols))
    rows = max(1, int(rows))

    # Apply user defaults
    global DEFAULT_FG, DEFAULT_BG
    DEFAULT_FG = fg_default
    DEFAULT_BG = bg_default

    grid = [[Cell(" ", Style()) for _ in range(cols)] for _ in range(rows)]
    last_nonempty_grid = None

    def clone_grid(source):
        return [[Cell(cell.ch, cell.style.copy()) for cell in row_cells] for row_cells in source]

    def grid_has_content(source):
        for row_cells in source:
            for cell in row_cells:
                if cell.ch != " ":
                    return True
        return False

    style = Style()
    row = 0
    col = 0
    idx = 0
    # tmux emits fixed-width lines (often fully padded). When a rendered row
    # exactly fills terminal width, our cell writer naturally wraps once and
    # tmux may still emit a newline for that same visual row. Suppress the
    # immediate newline after a hard wrap to avoid double-spacing artifacts.
    just_wrapped = False

    while idx < len(ansi_text) and row < rows:
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
                elif final == "A":
                    n = nums[0] if nums else 1
                    row = max(0, row - n)
                elif final == "B":
                    n = nums[0] if nums else 1
                    row = min(rows - 1, row + n)
                elif final == "C":
                    n = nums[0] if nums else 1
                    col = min(cols - 1, col + n)
                elif final == "D":
                    n = nums[0] if nums else 1
                    col = max(0, col - n)
                elif final == "J":
                    mode = nums[0] if nums else 0
                    if mode == 2 or mode == 3 or (mode == 0 and row == 0 and col == 0):
                        if grid_has_content(grid):
                            last_nonempty_grid = clone_grid(grid)
                        grid = [[Cell(" ", Style()) for _ in range(cols)] for _ in range(rows)]
                        row = 0
                        col = 0
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
            if just_wrapped:
                just_wrapped = False
            else:
                row += 1
                col = 0
            idx += 1
            continue
        if ch == "\r":
            col = 0
            just_wrapped = False
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
            just_wrapped = True
        else:
            just_wrapped = False

        idx += 1

    font_regular = fonts.regular
    font_bold = fonts.bold
    font_italic = fonts.italic
    font_bold_italic = fonts.bold_italic

    # Pillow uses y as the *top* of the glyph bounding box, not the baseline.
    # If we size cells using ascent+descent but draw at y0, the glyph will sit
    # toward the top of the cell and the remaining vertical space reads as a
    # blank line in PNG output. Compute a reference bbox and align it instead.
    bbox_w = font_regular.getbbox("M")
    bbox_h = font_regular.getbbox("Mg")
    cell_w = max(8, bbox_w[2] - bbox_w[0])
    glyph_h = max(1, bbox_h[3] - bbox_h[1])
    # Align bbox top to cell top; bbox_h[1] may be negative.
    text_y_offset = -bbox_h[1]
    # Add a small pad so underline doesn't clip.
    cell_h = max(12, glyph_h + 2)
    if cell_width > 0:
        cell_w = cell_width
    if cell_height > 0:
        cell_h = cell_height

    if last_nonempty_grid is not None and not grid_has_content(grid):
        grid = last_nonempty_grid

    img = Image.new("RGB", (cols * cell_w + pad_x * 2, rows * cell_h + pad_y * 2), bg_default)
    draw = ImageDraw.Draw(img)

    for r in range(rows):
        y0 = pad_y + r * cell_h
        for c in range(cols):
            x0 = pad_x + c * cell_w
            cell = grid[r][c]
            draw.rectangle([x0, y0, x0 + cell_w, y0 + cell_h], fill=cell.style.bg)
            if cell.ch != " ":
                cell_font = font_regular
                if cell.style.bold and cell.style.italic and font_bold_italic is not None:
                    cell_font = font_bold_italic
                elif cell.style.bold and font_bold is not None:
                    cell_font = font_bold
                elif cell.style.italic and font_italic is not None:
                    cell_font = font_italic
                draw.text((x0, y0 + text_y_offset), cell.ch, font=cell_font, fill=cell.style.fg)
                if cell.style.bold and cell_font == font_regular and font_bold is None:
                    draw.text((x0 + 1, y0 + text_y_offset), cell.ch, font=cell_font, fill=cell.style.fg)
            if cell.style.underline:
                underline_y = y0 + cell_h - 2
                draw.line([x0, underline_y, x0 + cell_w - 1, underline_y], fill=cell.style.fg)

    if scale and abs(scale - 1.0) > 1e-3:
        new_w = max(1, int(img.width * scale))
        new_h = max(1, int(img.height * scale))
        img = img.resize((new_w, new_h), resample=Image.NEAREST)

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
    return tmux_pane_size_with_socket(target, "")


def tmux_base_args(socket_name: str) -> list[str]:
    socket_name = (socket_name or "").strip()
    if not socket_name:
        return ["tmux"]
    return ["tmux", "-L", socket_name]


def tmux_pane_size_with_socket(target: str, socket_name: str) -> Tuple[int, int]:
    out = subprocess.check_output(
        [*tmux_base_args(socket_name), "display-message", "-p", "-t", target, "#{pane_width} #{pane_height}"],
        text=True,
    ).strip()
    parts = out.split()
    if len(parts) != 2:
        raise RuntimeError(f"Unexpected tmux size output: {out!r}")
    return int(parts[0]), int(parts[1])


def tmux_capture(
    target: str,
    out_ansi: Path,
    out_txt: Path,
    out_scrollback_ansi: Path | None = None,
    out_scrollback_txt: Path | None = None,
) -> None:
    tmux_capture_with_socket(target, out_ansi, out_txt, out_scrollback_ansi, out_scrollback_txt, "")


def tmux_capture_with_socket(
    target: str,
    out_ansi: Path,
    out_txt: Path,
    out_scrollback_ansi: Path | None,
    out_scrollback_txt: Path | None,
    socket_name: str,
) -> None:
    out_ansi.parent.mkdir(parents=True, exist_ok=True)
    out_txt.parent.mkdir(parents=True, exist_ok=True)
    # -e: include escape sequences; -p: print to stdout
    ansi = subprocess.check_output([*tmux_base_args(socket_name), "capture-pane", "-ep", "-t", target], text=False)
    out_ansi.write_bytes(ansi)
    txt = subprocess.check_output([*tmux_base_args(socket_name), "capture-pane", "-p", "-t", target], text=True)
    out_txt.write_text(txt)
    if out_scrollback_ansi is not None:
        ansi_full = subprocess.check_output(
            [*tmux_base_args(socket_name), "capture-pane", "-ep", "-t", target, "-S", "-"],
            text=False,
        )
        out_scrollback_ansi.write_bytes(ansi_full)
    if out_scrollback_txt is not None:
        txt_full = subprocess.check_output(
            [*tmux_base_args(socket_name), "capture-pane", "-p", "-t", target, "-S", "-"],
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", help="tmux pane target (e.g. session:window.pane or session:window)")
    parser.add_argument(
        "--tmux-socket",
        default="",
        help="tmux socket name (tmux -L <name>). Use this for isolated capture servers.",
    )
    parser.add_argument("--ansi", help="path to ANSI capture file (skips tmux)")
    parser.add_argument("--cols", type=int, default=0, help="columns when rendering --ansi capture")
    parser.add_argument("--rows", type=int, default=0, help="rows when rendering --ansi capture")
    parser.add_argument("--out", default="", help="output PNG path (default: docs_tmp/tmux_captures/<ts>_<target>.png)")
    parser.add_argument("--bg", default="0f172a", help="default background hex (e.g. 0f172a)")
    parser.add_argument("--fg", default="f1f5f9", help="default foreground hex (e.g. f1f5f9)")
    parser.add_argument("--font-size", type=int, default=18, help="font size for rendering")
    parser.add_argument("--font-path", default="", help="path to regular font (ttf/otf)")
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
    parser.add_argument("--pad-x", type=int, default=0, help="horizontal padding in pixels")
    parser.add_argument("--pad-y", type=int, default=0, help="vertical padding in pixels")
    parser.add_argument("--scrollback", action="store_true", help="also capture full scrollback to .scrollback.txt/.ansi")
    # Keep .ansi/.txt by default; callers can opt out to reduce artifact volume.
    parser.add_argument("--keep", action="store_true", help="keep alongside .ansi and .txt (default: keep)")
    parser.add_argument("--no-keep", dest="keep", action="store_false", help="delete .ansi and .txt after rendering")
    parser.set_defaults(keep=True)
    args = parser.parse_args()

    if not args.target and not args.ansi:
        raise SystemExit("Provide --target (tmux) or --ansi (file) to render.")

    ts = time.strftime("%Y%m%d-%H%M%S")
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
        if cols <= 0 or rows <= 0:
            raise SystemExit("When using --ansi, please provide --cols and --rows.")
        ansi_text = out_ansi.read_text(errors="replace")
        out_txt.write_text(strip_ansi(ansi_text))
    else:
        cols, rows = tmux_pane_size_with_socket(args.target, str(args.tmux_socket or "").strip())
        safe_target = re.sub(r"[^A-Za-z0-9_.-]+", "_", args.target)
        default_dir = Path("docs_tmp") / "tmux_captures"
        out_png = Path(args.out) if args.out else (default_dir / f"{ts}_{safe_target}.png")
        out_ansi = out_png.with_suffix(".ansi")
        out_txt = out_png.with_suffix(".txt")
        scroll_ansi = out_png.with_suffix(".scrollback.ansi") if args.scrollback else None
        scroll_txt = out_png.with_suffix(".scrollback.txt") if args.scrollback else None
        tmux_capture_with_socket(
            args.target,
            out_ansi,
            out_txt,
            scroll_ansi,
            scroll_txt,
            str(args.tmux_socket or "").strip(),
        )
        ansi_text = out_ansi.read_text(errors="replace")
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
    fonts = FontPack(
        regular=font_regular,
        bold=font_bold,
        italic=font_italic,
        bold_italic=font_bold_italic,
    )
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
            pad_x=args.pad_x,
            pad_y=args.pad_y,
        )

    if args.target:
        print(f"target: {args.target}")
    if args.ansi:
        print(f"ansi:   {out_ansi}")
    if args.scrollback and not args.ansi:
        print(f"scroll: {scroll_txt}")
    print(f"size:   {cols}x{rows}")
    print(f"text:   {out_txt}")
    print(f"png:    {out_png}")

    if not args.keep and not args.ansi:
        try:
            out_ansi.unlink(missing_ok=True)
            out_txt.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
