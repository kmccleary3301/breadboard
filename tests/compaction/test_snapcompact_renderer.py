"""Tests for snapcompact font rendering, geometry, normalization, and determinism."""

import base64
import io
import pytest
from PIL import Image

from breadboard_engine.compaction.snapcompact.normalize import (
    NEWLINE_GLYPH,
    compute_file_lists,
    dim_stopwords,
    normalize,
    scan_renderability,
    serialize_conversation,
)
from breadboard_engine.compaction.snapcompact.renderer import (
    render_snapcompact_png,
    snapcompact_supported_chars,
)
from breadboard_engine.compaction.snapcompact.shapes import (
    SHAPE_VARIANTS,
    SHAPES,
    ShapeGeometry,
    geometry,
    resolve_shape,
)


def test_determinism():
    """Identical text and shape options MUST produce identical PNG bytes."""
    text = "First sentence here. Second one is longer and has more words to lay out."
    png1 = render_snapcompact_png(text, size=320, font="5x8")
    png2 = render_snapcompact_png(text, size=320, font="5x8")
    assert png1 == png2
    raw1 = base64.b64decode(png1)
    raw2 = base64.b64decode(png2)
    assert raw1 == raw2
    # Verify it opens cleanly via Pillow
    img = Image.open(io.BytesIO(raw1))
    assert img.width == 320
    assert img.height > 0


def test_capacity_and_geometry():
    """Geometry calculation matches OMP formula for grid and doc column shapes."""
    legacy = geometry(SHAPES["legacy"], 320)
    assert legacy.cols == 64
    assert legacy.rows == 40
    assert legacy.capacity == 2560

    anthropic_shape = SHAPES["anthropic"]  # 11on16-bw
    anthropic_geo = geometry(anthropic_shape, 320)
    assert anthropic_geo.cols == 320 // 11  # 29
    assert anthropic_geo.rows == 320 // 16  # 20
    assert anthropic_geo.capacity == 29 * 20

    # Doc shape (2 columns with 3-cell gutter)
    doc_shape = ShapeGeometry(
        font="8x13",
        cell_width=8,
        cell_height=13,
        columns=2,
    )
    doc_geo = geometry(doc_shape, 320)
    grid_cols = 320 // 8  # 40
    expected_col_w = (grid_cols - 3) // 2  # 18
    assert doc_geo.cols == expected_col_w
    assert doc_geo.rows == 320 // 13
    assert doc_geo.capacity == 2 * expected_col_w * (320 // 13)


def test_tight_height_hugging():
    """Output image height hugs the rows the text actually needs instead of full square."""
    short_text = "Just one short line of text."
    png_short = render_snapcompact_png(short_text, size=1568, font="5x8")
    img_short = Image.open(io.BytesIO(base64.b64decode(png_short)))
    assert img_short.width == 1568
    # 5x8 cell has height 8. One line of text -> height 8.
    assert img_short.height == 8


def test_normalization():
    """Whitespace collapse, ANSI stripping, and newline folding to FULL_BLOCK."""
    assert normalize("a \t b   c") == "a b c"
    assert normalize("x → y ✓ “quoted” — em…") == 'x -> y v "quoted" - em...'
    assert normalize("café größe") == "café größe"
    assert normalize("box │─┌") == "box |-+"

    # Newlines fold to full block
    assert normalize("a\n\n\tb   c\r\nd") == f"a{NEWLINE_GLYPH}b c{NEWLINE_GLYPH}d"
    assert normalize("\n\nbody\n") == "body"

    # ANSI removal
    assert normalize("\x1b[31mred\x1b[0m plain") == "red plain"

    # Emoji status fold vs decorative drop
    assert normalize("✅ pass ⚠️ warn ❌ fail 😄") == "[OK] pass [WARN] warn [FAIL] fail"
    assert normalize("✗ ✘") == "x x"


def test_stopword_dimming():
    """High-frequency function words wrapped in DIM_ON / DIM_OFF."""
    res = dim_stopwords("the quick brown fox jumps over a lazy dog")
    assert "\u000ethe\u000f" in res
    assert "\u000ea\u000f" in res
    assert "\u000eover\u000f" in res
    assert "\u000ebrown\u000f" not in res


def test_silver_fallback_cjk():
    """Silver TTF fallback renders CJK glyphs safely."""
    text = "const greeting = '你好世界';"
    is_safe, unrenderable_ratio = scan_renderability(text)
    assert is_safe
    assert unrenderable_ratio == 0.0

    png_b64 = render_snapcompact_png(text, size=320, font="8on16-bw")
    img = Image.open(io.BytesIO(base64.b64decode(png_b64)))
    assert img.width == 320
    assert img.height > 0


def test_supported_chars():
    """snapcompact_supported_chars filters characters accurately."""
    ascii_chars = "abcdef123!@#"
    assert snapcompact_supported_chars("5x8", ascii_chars) == ascii_chars
    cjk = "你好"
    # 5x8 does not support CJK directly, but silver does
    assert snapcompact_supported_chars("silver", cjk) == cjk
