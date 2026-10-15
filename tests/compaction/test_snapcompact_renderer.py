"""Tests for snapcompact font rendering, geometry, normalization, and determinism."""

import base64
import io
from PIL import Image

from breadboard_engine.compaction.snapcompact.normalize import (
    NEWLINE_GLYPH,
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
    SHAPES,
    ShapeGeometry,
    geometry,
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


def test_archive_pairs_each_call_with_its_canonical_result():
    from breadboard_engine.provider.adapters import AnthropicAdapter
    from breadboard_engine.provider.contract_messages import responses_image_tool_result_message

    call = lambda call_id: {"id": call_id, "type": "function", "function": {"name": "read", "arguments": "{}"}}
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [call("c1"), call("c2"), call("c3")]},
        responses_image_tool_result_message("c1", [{"type": "input_text", "text": "FIRST"},
                                                   {"type": "input_image", "image_url": "data:image/png;base64,AA=="}]),
        AnthropicAdapter().create_tool_result_batch_message([{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "c2", "content": "SECOND"},
            {"type": "tool_result", "tool_use_id": "c3", "content": "THIRD"}]}]),
    ]
    text = serialize_conversation(messages, dim_tool_results=False)
    assert text == "¶call:" + "\n".join(f"read()\n<out>\n{out}\n</out>" for out in ("FIRST", "SECOND", "THIRD"))


def _anthropic_replay_result(native_messages):
    from breadboard_engine.provider.adapters import AnthropicAdapter

    return AnthropicAdapter().create_tool_result_message(
        "c1", "view_image", {"__claude_tool_result_messages": native_messages},
    )


def _archive_text(messages):
    from breadboard_engine.compaction.snapcompact import compact_archive

    _, _, archive = compact_archive(messages, shape=SHAPES["legacy"])
    return archive.text or ""


def test_archive_reads_anthropic_image_results_before_truncation():
    import json

    result = _anthropic_replay_result([{"role": "user", "content": [{
        "type": "tool_result",
        "tool_use_id": "c1",
        "content": [
            {"type": "image", "source": {
                "type": "base64", "media_type": "image/png", "data": "A" * 12000,
            }},
            {"type": "text", "text": "SCREEN_OBSERVATION_AFTER_IMAGE"},
        ],
    }]}])
    source_before = json.dumps(result, sort_keys=True)

    text = _archive_text([result])

    assert "SCREEN_OBSERVATION_AFTER_IMAGE" in text
    assert "A" * 256 not in text
    assert json.dumps(result, sort_keys=True) == source_before


def test_archive_keeps_native_observation_when_result_is_empty():
    import json

    result = _anthropic_replay_result([{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "c1", "content": []},
        {"type": "text", "text": "EMPTY_RESULT_SIDE_OBSERVATION"},
    ]}])
    source_before = json.dumps(result, sort_keys=True)

    text = _archive_text([result])

    assert "¶user:EMPTY_RESULT_SIDE_OBSERVATION" in text
    assert "provider_replay" not in text
    assert json.dumps(result, sort_keys=True) == source_before
