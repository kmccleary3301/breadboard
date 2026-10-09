"""Snapcompact frame rendering using Pillow.

Ports crates/pi-natives/src/snapcompact.rs:
- Bundled pixel fonts: 5x8 (BDF), 8x8 (unscii hex), 6x12 (BDF), 8x13 (BDF), Silver.ttf
- 10-color indexed palette (white background, 6 sentence hues, black, repeat band, dim gray)
- Frame geometry with tight content hugging (used rows)
- Non-stretch indexed PNG path and Lanczos-stretched RGB PNG path
- Deterministic PNG encoding (no timestamps or auxiliary chunks)
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import gzip
import io
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Pillow is imported lazily or guarded where required.
try:
    from PIL import Image, ImageFont
    _PILLOW_AVAILABLE = True
except ImportError:
    _PILLOW_AVAILABLE = False


FONTS_DIR = Path(__file__).parent / "fonts"

# Palette: 0 is white background, 1-6 are dark sentence hues, 7 is black ink,
# 8 is pale highlight band behind repeated line copies, 9 is dim gray ink.
PALETTE: List[Tuple[int, int, int]] = [
    (255, 255, 255),  # 0: background
    (109, 2, 2),      # 1: red
    (109, 53, 2),     # 2: amber
    (24, 109, 2),     # 3: green
    (2, 109, 109),    # 4: teal
    (2, 32, 109),     # 5: blue
    (75, 2, 109),     # 6: violet
    (0, 0, 0),        # 7: bw ink
    (255, 247, 194),  # 8: repeat highlight band
    (128, 128, 128),  # 9: dim ink
]

FLAT_PALETTE: List[int] = [c for rgb in PALETTE for c in rgb] + [0] * (768 - len(PALETTE) * 3)

INK_COLORS = 6
INK_BLACK = 7
BG_REPEAT = 8
INK_DIM = 9

DIM_ON = 0x0E
DIM_OFF = 0x0F
FULL_BLOCK = 0x2588
GUTTER = 3


def is_wide(cp: int) -> bool:
    """East Asian Wide / Fullwidth code points occupying two grid cells."""
    return (
        0x1100 <= cp <= 0x115F
        or 0x2E80 <= cp <= 0x2EFF
        or 0x2F00 <= cp <= 0x2FDF
        or 0x3000 <= cp <= 0x303E
        or 0x3041 <= cp <= 0x33FF
        or 0x3400 <= cp <= 0x4DBF
        or 0x4E00 <= cp <= 0x9FFF
        or 0xA000 <= cp <= 0xA4CF
        or 0xAC00 <= cp <= 0xD7A3
        or 0xF900 <= cp <= 0xFAFF
        or 0xFE30 <= cp <= 0xFE4F
        or 0xFF00 <= cp <= 0xFF60
        or 0xFFE0 <= cp <= 0xFFE6
        or 0x20000 <= cp <= 0x2FFFD
        or 0x30000 <= cp <= 0x3FFFD
    )


def cell_units(code: int, wide_cells: bool) -> int:
    if code in (DIM_ON, DIM_OFF):
        return 0
    if wide_cells and is_wide(code):
        return 2
    return 1


def place_cell(cursor: int, cols: int, code: int, wide_cells: bool) -> Optional[Tuple[int, int, int]]:
    units = cell_units(code, wide_cells)
    if units == 0:
        return None
    cell = cursor
    if units == 2 and cols >= 2 and cell % cols == cols - 1:
        cell += 1  # pad: never split a wide glyph across two rows
    return (cell, units, cell + units)


def used_rows(text: str, cols: int, rows: int, doc: bool, wide_cells: bool) -> int:
    if doc:
        r = len(text.split("\n"))
    else:
        cursor = 0
        for ch in text:
            placed = place_cell(cursor, cols, ord(ch), wide_cells)
            if placed is not None:
                cursor = placed[2]
        r = (cursor + cols - 1) // cols if cols > 0 else 1
    return max(1, min(rows, r))


@dataclass(frozen=True)
class BdfGlyph:
    w: int
    h: int
    xoff: int
    yoff: int
    rows: Tuple[int, ...]


class BitmapFont:
    def __init__(self, glyphs: Dict[int, BdfGlyph], ascent: int, cell_w: int, cell_h: int) -> None:
        self.glyphs = glyphs
        self.ascent = ascent
        self.cell_w = cell_w
        self.cell_h = cell_h

    def supports(self, cp: int) -> bool:
        return cp in self.glyphs


def parse_bdf(text: str, cell_w: int, cell_h: int) -> BitmapFont:
    glyphs: Dict[int, BdfGlyph] = {}
    ascent = 0
    enc = -1
    bbx = [0, 0, 0, 0]
    lines = iter(text.splitlines())
    for line in lines:
        if line.startswith("FONT_ASCENT"):
            try:
                ascent = int(line[len("FONT_ASCENT"):].strip())
            except ValueError:
                pass
        elif line.startswith("ENCODING"):
            try:
                enc = int(line[len("ENCODING"):].strip())
            except ValueError:
                enc = -1
        elif line.startswith("BBX"):
            parts = line[len("BBX"):].split()
            bbx = [int(p) for p in parts[:4]]
        elif line.startswith("BITMAP"):
            rows = []
            for row_line in lines:
                if row_line.startswith("ENDCHAR"):
                    break
                rows.append(int(row_line.strip(), 16))
            if enc >= 0:
                glyphs[enc] = BdfGlyph(
                    w=max(0, min(8, bbx[0])),
                    h=bbx[1],
                    xoff=bbx[2],
                    yoff=bbx[3],
                    rows=tuple(rows),
                )
    return BitmapFont(glyphs, ascent, cell_w, cell_h)


def parse_hex(text: str) -> BitmapFont:
    glyphs: Dict[int, BdfGlyph] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        cp_str, bits = line.split(":", 1)
        try:
            enc = int(cp_str.strip(), 16)
        except ValueError:
            continue
        bits = bits.strip()
        if len(bits) != 16:
            continue
        rows = tuple(int(bits[i * 2 : i * 2 + 2], 16) for i in range(8))
        glyphs[enc] = BdfGlyph(w=8, h=8, xoff=0, yoff=-1, rows=rows)
    return BitmapFont(glyphs, ascent=7, cell_w=8, cell_h=8)


# Font caches
_FONT_5X8: Optional[BitmapFont] = None
_FONT_8X8: Optional[BitmapFont] = None
_FONT_6X12: Optional[BitmapFont] = None
_FONT_8X13: Optional[BitmapFont] = None
_FONT_SILVER_FACE: Optional[Any] = None


def get_font_5x8() -> BitmapFont:
    global _FONT_5X8
    if _FONT_5X8 is None:
        with gzip.open(FONTS_DIR / "5x8.bdf.gz", "rt", encoding="latin-1") as f:
            _FONT_5X8 = parse_bdf(f.read(), 5, 8)
    return _FONT_5X8


def get_font_8x8() -> BitmapFont:
    global _FONT_8X8
    if _FONT_8X8 is None:
        _FONT_8X8 = parse_hex((FONTS_DIR / "unscii-8.hex").read_text("ascii"))
    return _FONT_8X8


def get_font_6x12() -> BitmapFont:
    global _FONT_6X12
    if _FONT_6X12 is None:
        with gzip.open(FONTS_DIR / "6x12.bdf.gz", "rt", encoding="latin-1") as f:
            _FONT_6X12 = parse_bdf(f.read(), 6, 12)
    return _FONT_6X12


def get_font_8x13() -> BitmapFont:
    global _FONT_8X13
    if _FONT_8X13 is None:
        with gzip.open(FONTS_DIR / "8x13.bdf.gz", "rt", encoding="latin-1") as f:
            _FONT_8X13 = parse_bdf(f.read(), 8, 13)
    return _FONT_8X13


def get_silver_ttf(size: int = 16) -> Any:
    global _FONT_SILVER_FACE
    if not _PILLOW_AVAILABLE:
        raise RuntimeError("Pillow is required for snapcompact font rendering")
    return ImageFont.truetype(str(FONTS_DIR / "Silver.ttf"), size)


def resolve_font(name: str) -> Optional[Any]:
    if name == "5x8":
        return get_font_5x8()
    if name == "8x8":
        return get_font_8x8()
    if name == "6x12":
        return get_font_6x12()
    if name == "8x13":
        return get_font_8x13()
    if name == "silver":
        return get_silver_ttf(16)
    return None


def snapcompact_supported_chars(font_name: str, chars: str) -> str:
    font = resolve_font(font_name)
    if font is None:
        raise ValueError(f"Unknown snapcompact font: {font_name}")
    out = []
    for ch in chars:
        cp = ord(ch)
        if cp in (DIM_ON, DIM_OFF, FULL_BLOCK, 0x0A):
            out.append(ch)
        elif isinstance(font, BitmapFont):
            if font.supports(cp):
                out.append(ch)
        else:
            # Silver TTF
            bbox = font.getbbox(ch)
            if bbox is not None and bbox != (0, 0, 0, 0):
                out.append(ch)
    return "".join(out)


def _blit_glyph(pixels: bytearray, width: int, height: int, glyph: BdfGlyph, left: int, top: int, ink: int) -> None:
    for r, bits in enumerate(glyph.rows):
        if bits == 0:
            continue
        y = top + r
        if y < 0 or y >= height:
            continue
        row_base = y * width
        for b in range(glyph.w):
            if bits & (0x80 >> b):
                x = left + b
                if 0 <= x < width:
                    pixels[row_base + x] = ink


def _blit_ttf_mask(
    pixels: bytearray,
    width: int,
    height: int,
    mask: Any,
    left: int,
    top: int,
    ink: int,
) -> None:
    mw, mh = mask.size
    data = list(mask)
    for y in range(mh):
        dst_y = top + y
        if dst_y < 0 or dst_y >= height:
            continue
        row_base = dst_y * width
        for x in range(mw):
            coverage = data[y * mw + x]
            if coverage >= 170:
                cell_ink = ink
            elif ink == INK_BLACK and coverage >= 56:
                cell_ink = INK_DIM
            elif coverage >= 110:
                cell_ink = ink
            else:
                continue
            dst_x = left + x
            if 0 <= dst_x < width:
                pixels[row_base + dst_x] = cell_ink


def _fill_cell(pixels: bytearray, width: int, height: int, cell_w: int, cell_h: int, repeat: int, x_origin: int, row: int, ink: int) -> None:
    x0 = min(x_origin, width)
    x1 = min(x_origin + cell_w, width)
    if x0 >= x1:
        return
    for copy in range(repeat):
        top = (row * repeat + copy) * cell_h
        for y in range(top, min(top + cell_h, height)):
            row_base = y * width
            for x in range(x0, x1):
                pixels[row_base + x] = ink


def _fill_repeat_bands(pixels: bytearray, width: int, height: int, cell_h: int, repeat: int, rows: int) -> None:
    if repeat <= 1:
        return
    for row in range(rows):
        for copy in range(1, repeat):
            band_top = (row * repeat + copy) * cell_h
            for y in range(band_top, min(band_top + cell_h, height)):
                row_base = y * width
                for x in range(width):
                    pixels[row_base + x] = BG_REPEAT


def render_bitmap(
    text: str,
    width: int,
    height: int,
    font: BitmapFont,
    cols: int,
    rows: int,
    repeat: int,
    cell_w: int,
    cell_h: int,
    black_ink: bool,
) -> bytearray:
    pixels = bytearray(width * height)
    capacity = cols * rows
    if capacity == 0:
        return pixels

    _fill_repeat_bands(pixels, width, height, cell_h, repeat, rows)
    codes = [ord(c) for c in text]
    sentence = 0
    dim = False
    cursor = 0
    silver_font_narrow = get_silver_ttf(cell_h)
    silver_font_wide = get_silver_ttf(cell_h)

    for i, code in enumerate(codes):
        if cursor >= capacity:
            break
        if code == DIM_ON:
            dim = True
            continue
        if code == DIM_OFF:
            dim = False
            continue

        if dim:
            ink = INK_DIM
        elif black_ink:
            ink = INK_BLACK
        else:
            ink = 1 + (sentence % INK_COLORS)

        if code in (0x2E, 0x21, 0x3F) and i + 1 < len(codes) and codes[i + 1] in (0x20, FULL_BLOCK):
            sentence += 1

        placed = place_cell(cursor, cols, code, True)
        if placed is None:
            continue
        at, units, cursor = placed
        if at >= capacity:
            break

        row = at // cols
        col = at - row * cols

        if code == FULL_BLOCK:
            _fill_cell(pixels, width, height, cell_w, cell_h, repeat, col * cell_w, row, INK_BLACK)
            continue

        glyph = font.glyphs.get(code)
        if glyph is not None:
            if not glyph.rows:
                continue
            left = col * cell_w + glyph.xoff
            for copy in range(repeat):
                cell_top = (row * repeat + copy) * cell_h
                top = cell_top + font.ascent - glyph.h - glyph.yoff
                _blit_glyph(pixels, width, height, glyph, left, top, ink)
        else:
            # Silver fallback
            try:
                ch = chr(code)
                s_font = silver_font_wide if units == 2 else silver_font_narrow
                mask = s_font.getmask(ch)
                mw, mh = mask.size
                bbox = s_font.getbbox(ch)
                if bbox:
                    span = units * cell_w
                    left = col * cell_w + max(0, span - mw) // 2
                    for copy in range(repeat):
                        cell_top = (row * repeat + copy) * cell_h
                        top = cell_top + max(0, cell_h - mh) // 2
                        _blit_ttf_mask(pixels, width, height, mask, left, top, ink)
            except Exception:
                pass

    return pixels


def render_doc_bitmap(
    text: str,
    width: int,
    height: int,
    font: BitmapFont,
    cols: int,
    rows: int,
    repeat: int,
    cell_w: int,
    cell_h: int,
    black_ink: bool,
) -> bytearray:
    pixels = bytearray(width * height)
    col_w = max(0, cols - GUTTER) // 2
    if col_w == 0 or rows == 0:
        return pixels

    _fill_repeat_bands(pixels, width, height, cell_h, repeat, rows)
    codes = [ord(c) for c in text]
    sentence = 0
    dim = False
    line = 0
    col = 0
    silver_font = get_silver_ttf(cell_h)

    for i, code in enumerate(codes):
        if code == DIM_ON:
            dim = True
            continue
        if code == DIM_OFF:
            dim = False
            continue
        if code == 0x0A:
            line += 1
            col = 0
            if line >= rows * 2:
                break
            continue

        if dim:
            ink = INK_DIM
        elif black_ink:
            ink = INK_BLACK
        else:
            ink = 1 + (sentence % INK_COLORS)

        if code in (0x2E, 0x21, 0x3F) and i + 1 < len(codes) and codes[i + 1] in (0x20, 0x0A, FULL_BLOCK):
            sentence += 1

        units = cell_units(code, True)
        cell = col
        if units == 2 and col_w >= 2 and cell == col_w - 1:
            cell += 1
        col = cell + units
        if cell + units > col_w:
            continue

        column = line // rows
        row = line - column * rows
        x_origin = column * (col_w + GUTTER) * cell_w

        if code == FULL_BLOCK:
            _fill_cell(pixels, width, height, cell_w, cell_h, repeat, x_origin + cell * cell_w, row, INK_BLACK)
            continue

        glyph = font.glyphs.get(code)
        if glyph is not None:
            if not glyph.rows:
                continue
            left = x_origin + cell * cell_w + glyph.xoff
            for copy in range(repeat):
                cell_top = (row * repeat + copy) * cell_h
                top = cell_top + font.ascent - glyph.h - glyph.yoff
                _blit_glyph(pixels, width, height, glyph, left, top, ink)
        else:
            try:
                ch = chr(code)
                mask = silver_font.getmask(ch)
                mw, mh = mask.size
                span = units * cell_w
                left = x_origin + cell * cell_w + max(0, span - mw) // 2
                for copy in range(repeat):
                    cell_top = (row * repeat + copy) * cell_h
                    top = cell_top + max(0, cell_h - mh) // 2
                    _blit_ttf_mask(pixels, width, height, mask, left, top, ink)
            except Exception:
                pass

    return pixels


def render_snapcompact_png(
    text: str,
    *,
    size: int = 1568,
    font: str = "5x8",
    cell_width: Optional[int] = None,
    cell_height: Optional[int] = None,
    variant: str = "sent",
    line_repeat: int = 1,
    stretch: Optional[bool] = None,
    columns: int = 1,
) -> str:
    """Render text onto a snapcompact frame and return base64-encoded PNG."""
    if not _PILLOW_AVAILABLE:
        raise RuntimeError("Pillow is required for snapcompact rendering")

    if font not in ("5x8", "8x8", "6x12", "8x13", "silver"):
        from .shapes import SHAPE_VARIANTS
        if font in SHAPE_VARIANTS:
            sh = SHAPE_VARIANTS[font]
            font_name = sh.font
            if cell_width is None:
                cell_width = sh.cell_width
            if cell_height is None:
                cell_height = sh.cell_height
            if stretch is None:
                stretch = sh.stretch
            if line_repeat == 1:
                line_repeat = sh.line_repeat
            if variant == "sent":
                variant = sh.variant
        else:
            font_name = font
    else:
        font_name = font

    loaded_font = resolve_font(font_name)
    if loaded_font is None:
        raise ValueError(f"Unknown font: {font!r}")
    black_ink = (variant == "bw")
    repeat = max(1, line_repeat)
    doc = (columns == 2)

    if isinstance(loaded_font, BitmapFont):
        natural_w = loaded_font.cell_w
        natural_h = loaded_font.cell_h
        target_w = cell_width or natural_w
        target_h = cell_height or natural_h
        cols = size // target_w
        rows = size // target_h // repeat
        if cols == 0 or rows == 0:
            raise ValueError(f"Frame size {size} cannot fit {target_w}x{target_h} cell grid")

        used = used_rows(text, cols, rows, doc, True)
        tight_h = used * repeat * target_h

        is_stretched = (stretch is not False) and ((target_w, target_h) != (natural_w, natural_h))
        if not is_stretched:
            # Indexed path
            pixels = (
                render_doc_bitmap(text, size, tight_h, loaded_font, cols, rows, repeat, target_w, target_h, black_ink)
                if doc
                else render_bitmap(text, size, tight_h, loaded_font, cols, rows, repeat, target_w, target_h, black_ink)
            )
            img = Image.frombytes("P", (size, tight_h), bytes(pixels))
            img.putpalette(FLAT_PALETTE)
        else:
            # Stretch path: render at natural size on tight canvas, Lanczos-resize to target
            src_w = cols * natural_w
            src_h = used * repeat * natural_h
            dst_w = cols * target_w
            dst_h = tight_h
            pixels = (
                render_doc_bitmap(text, src_w, src_h, loaded_font, cols, rows, repeat, natural_w, natural_h, black_ink)
                if doc
                else render_bitmap(text, src_w, src_h, loaded_font, cols, rows, repeat, natural_w, natural_h, black_ink)
            )
            small_img = Image.frombytes("P", (src_w, src_h), bytes(pixels))
            small_img.putpalette(FLAT_PALETTE)
            rgb_small = small_img.convert("RGB")
            rgb_resized = rgb_small.resize((dst_w, dst_h), resample=Image.Resampling.LANCZOS)
            # Paste onto white frame of width `size`
            img = Image.new("RGB", (size, dst_h), (255, 255, 255))
            img.paste(rgb_resized, (0, 0))
    else:
        # Silver TrueType font (16px grid)
        cell_w = cell_width or 16
        cell_h = cell_height or 16
        cols = size // cell_w
        rows = size // cell_h // repeat
        used = used_rows(text, cols, rows, doc, False)
        tight_h = used * repeat * cell_h

        img = Image.new("RGB", (size, tight_h), (255, 255, 255))
        # Draw characters
        capacity = cols * rows
        codes = [ord(c) for c in text]
        sentence = 0
        dim = False
        cell_idx = 0
        col_w = max(0, cols - GUTTER) // 2 if doc else cols
        line = 0
        col = 0

        for i, code in enumerate(codes):
            if cell_idx >= capacity:
                break
            if code == DIM_ON:
                dim = True
                continue
            if code == DIM_OFF:
                dim = False
                continue
            if doc and code == 0x0A:
                line += 1
                col = 0
                if line >= rows * 2:
                    break
                continue

            if dim:
                ink_rgb = PALETTE[INK_DIM]
            elif black_ink:
                ink_rgb = PALETTE[INK_BLACK]
            else:
                ink_rgb = PALETTE[1 + (sentence % INK_COLORS)]

            if code in (0x2E, 0x21, 0x3F) and i + 1 < len(codes) and codes[i + 1] in (0x20, 0x0A, FULL_BLOCK):
                sentence += 1

            if doc:
                curr_cell = col
                col += 1
                if curr_cell >= col_w:
                    continue
                column = line // rows
                row = line - column * rows
                x_origin = (column * (col_w + GUTTER) + curr_cell) * cell_w
            else:
                row = cell_idx // cols
                curr_col = cell_idx - row * cols
                cell_idx += 1
                x_origin = curr_col * cell_w

            if code == FULL_BLOCK:
                for c_y in range(row * cell_h, min((row + 1) * cell_h, tight_h)):
                    for c_x in range(x_origin, min(x_origin + cell_w, size)):
                        img.putpixel((c_x, c_y), (0, 0, 0))
                continue

            ch = chr(code)
            mask = loaded_font.getmask(ch)
            mw, mh = mask.size
            if mw > 0 and mh > 0:
                data = list(mask)
                left = x_origin + max(0, cell_w - mw) // 2
                top = row * cell_h + max(0, cell_h - mh) // 2
                for m_y in range(mh):
                    dst_y = top + m_y
                    if dst_y >= tight_h:
                        continue
                    for m_x in range(mw):
                        dst_x = left + m_x
                        if dst_x >= size:
                            continue
                        alpha = data[m_y * mw + m_x]
                        if alpha == 0:
                            continue
                        inv = 255 - alpha
                        bg = img.getpixel((dst_x, dst_y))
                        r = (bg[0] * inv + ink_rgb[0] * alpha + 127) // 255
                        g = (bg[1] * inv + ink_rgb[1] * alpha + 127) // 255
                        b = (bg[2] * inv + ink_rgb[2] * alpha + 127) // 255
                        img.putpixel((dst_x, dst_y), (r, g, b))

    bio = io.BytesIO()
    img.save(bio, format="PNG")
    return base64.b64encode(bio.getvalue()).decode("ascii")
