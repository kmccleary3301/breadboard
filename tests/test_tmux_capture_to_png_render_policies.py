from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageFont


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "tmux_capture_to_png.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("tmux_capture_to_png", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_wide_cell_overflow_is_clipped_in_snapshot_mode(tmp_path: Path):
    module = _load_module()
    out_wide = tmp_path / "wide.png"
    out_space = tmp_path / "space.png"
    font = ImageFont.load_default()
    fonts = module.FontPack(regular=font, bold=None, italic=None, bold_italic=None)

    # One column cell: wide glyph should deterministically clip to blank.
    module.render_ansi_to_png(
        ansi_text="界",
        cols=1,
        rows=1,
        out_path=out_wide,
        fg_default=(241, 245, 249),
        bg_default=(31, 36, 48),
        scale=1.0,
        fonts=fonts,
        cell_width=8,
        cell_height=16,
        line_gap=0,
        pad_x=0,
        pad_y=0,
        baseline_offset=-1,
        underline_offset=-1,
        snapshot_mode=True,
    )
    module.render_ansi_to_png(
        ansi_text=" ",
        cols=1,
        rows=1,
        out_path=out_space,
        fg_default=(241, 245, 249),
        bg_default=(31, 36, 48),
        scale=1.0,
        fonts=fonts,
        cell_width=8,
        cell_height=16,
        line_gap=0,
        pad_x=0,
        pad_y=0,
        baseline_offset=-1,
        underline_offset=-1,
        snapshot_mode=True,
    )

    diff = ImageChops.difference(Image.open(out_wide).convert("RGB"), Image.open(out_space).convert("RGB"))
    assert diff.getbbox() is None


def test_box_drawing_vector_policy_draws_non_bg_pixels():
    module = _load_module()
    img = Image.new("RGB", (20, 20), (31, 36, 48))
    draw = module.ImageDraw.Draw(img)
    module.draw_box_drawing_cell(
        draw,
        x0=2,
        y0=2,
        cell_w=12,
        cell_h=12,
        ch="┌",
        color=(241, 245, 249),
    )
    px = img.load()
    changed = 0
    for y in range(img.height):
        for x in range(img.width):
            if px[x, y] != (31, 36, 48):
                changed += 1
    assert changed > 0
    assert module.is_box_drawing_char("┌") is True
    assert module.is_box_drawing_char("A") is False


def test_block_element_vector_policy_draws_contiguous_top_half():
    module = _load_module()
    img = Image.new("RGB", (20, 20), (31, 36, 48))
    draw = module.ImageDraw.Draw(img)
    module.draw_block_element_cell(
        draw,
        x0=2,
        y0=2,
        cell_w=12,
        cell_h=12,
        ch="▀",
        color=(30, 30, 30),
    )
    px = img.load()
    # Top half should be filled with no per-cell seam gaps.
    for y in range(2, 8):
        for x in range(2, 14):
            assert px[x, y] == (30, 30, 30)
    # Bottom half should remain background.
    for y in range(8, 14):
        for x in range(2, 14):
            assert px[x, y] == (31, 36, 48)


def test_box_drawing_half_segment_heavy_up_avoids_lower_half_bleed():
    module = _load_module()
    img = Image.new("RGB", (20, 20), (31, 36, 48))
    draw = module.ImageDraw.Draw(img)
    module.draw_box_drawing_cell(
        draw,
        x0=2,
        y0=2,
        cell_w=12,
        cell_h=12,
        ch="╹",
        color=(170, 130, 220),
    )
    px = img.load()
    for y in range(9, 14):
        for x in range(2, 14):
            assert px[x, y] == (31, 36, 48)


def test_vector_symbol_policy_draws_nested_square_for_u25a3():
    module = _load_module()
    img = Image.new("RGB", (20, 20), (31, 36, 48))
    draw = module.ImageDraw.Draw(img)
    module.draw_vector_symbol_cell(
        draw,
        x0=2,
        y0=2,
        cell_w=12,
        cell_h=12,
        ch="▣",
        color=(170, 130, 220),
    )
    px = img.load()
    assert px[8, 8] == (170, 130, 220)
    assert px[2, 2] == (31, 36, 48)
    assert module.is_vector_symbol_char("▣") is True
    assert module.is_vector_symbol_char("A") is False


def test_cursor_overlay_draws_visible_block(tmp_path: Path):
    module = _load_module()
    out_png = tmp_path / "cursor.png"
    font = ImageFont.load_default()
    fonts = module.FontPack(regular=font, bold=None, italic=None, bold_italic=None)

    module.render_ansi_to_png(
        ansi_text="  ",
        cols=2,
        rows=1,
        out_path=out_png,
        fg_default=(241, 245, 249),
        bg_default=(31, 36, 48),
        scale=1.0,
        fonts=fonts,
        cell_width=8,
        cell_height=16,
        line_gap=0,
        pad_x=0,
        pad_y=0,
        baseline_offset=-1,
        underline_offset=-1,
        snapshot_mode=True,
        cursor_state=(0, 0, True),
    )

    img = Image.open(out_png).convert("RGB")
    left_cell = img.crop((0, 0, 8, 16))
    right_cell = img.crop((8, 0, 16, 16))
    assert left_cell.getbbox() is not None
    # right cell should remain background-only
    assert right_cell.getbbox() is not None
    # verify left cell has white cursor fill while right does not
    assert (255, 255, 255) in left_cell.getdata()
    assert (255, 255, 255) not in right_cell.getdata()


def test_render_provenance_records_vector_symbol_entry(tmp_path: Path):
    module = _load_module()
    out_png = tmp_path / "symbol.png"
    font = ImageFont.truetype(
        "renderer_assets/fonts/Consolas-Regular.ttf",
        14,
    )
    fonts = module.FontPack(
        regular=font,
        bold=None,
        italic=None,
        bold_italic=None,
        codepoint_map={id(font): set()},
    )
    glyph_provenance: list[dict[str, object]] = []

    module.render_ansi_to_png(
        ansi_text="▣",
        cols=1,
        rows=1,
        out_path=out_png,
        fg_default=(241, 245, 249),
        bg_default=(31, 36, 48),
        scale=1.0,
        fonts=fonts,
        cell_width=8,
        cell_height=16,
        line_gap=0,
        pad_x=0,
        pad_y=0,
        baseline_offset=-1,
        underline_offset=-1,
        snapshot_mode=True,
        glyph_provenance=glyph_provenance,
        glyph_provenance_mode="all",
    )

    assert glyph_provenance
    first = glyph_provenance[0]
    assert first["codepoint"] == "U+25A3"
    assert first["source"] == "vector_symbol_v1"
    assert first["fallback_rank"] == -1


def test_scaled_font_pack_preserves_codepoint_map_for_scaled_font_ids():
    module = _load_module()
    regular = ImageFont.truetype(
        "renderer_assets/fonts/Consolas-Regular.ttf",
        14,
    )
    fallback = ImageFont.truetype(
        "renderer_assets/fonts/DejaVuSansMono.ttf",
        14,
    )
    source = module.FontPack(
        regular=regular,
        fallback_regular=fallback,
        codepoint_map={
            id(regular): {0x41},
            id(fallback): {0x25A3},
        },
    )
    scaled = module._scaled_font_pack(source, factor=2, base_size=14)
    assert id(scaled.regular) in scaled.codepoint_map
    assert id(scaled.fallback_regular) in scaled.codepoint_map
    assert 0x41 in (scaled.codepoint_map[id(scaled.regular)] or set())
    assert 0x25A3 in (scaled.codepoint_map[id(scaled.fallback_regular)] or set())


def test_should_prefer_symbol_fallback_targets_terminal_symbol_ranges():
    module = _load_module()
    assert module.should_prefer_symbol_fallback("▣") is True
    assert module.should_prefer_symbol_fallback("┃") is True
    assert module.should_prefer_symbol_fallback("•") is True
    assert module.should_prefer_symbol_fallback("A") is False


def test_resolve_symbol_fallback_candidates_prefers_profile_chain():
    module = _load_module()
    chain = module.resolve_symbol_fallback_candidates("phase4_locked_v5")
    assert chain[:2] == [
        "renderer_assets/fonts/Segoe UI Symbol.ttf",
        "renderer_assets/fonts/seguisym.ttf",
    ]


def test_apply_row_controls_override_and_cap():
    module = _load_module()
    assert module.apply_row_controls(40, rows_override=0, max_rows=0) == 40
    assert module.apply_row_controls(40, rows_override=18, max_rows=0) == 18
    assert module.apply_row_controls(40, rows_override=0, max_rows=25) == 25
    assert module.apply_row_controls(40, rows_override=30, max_rows=20) == 20
