from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from PIL import Image


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "render_parity_diagnostics.py"
    spec = importlib.util.spec_from_file_location("render_parity_diagnostics", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_row_edge_pattern(img: Image.Image, *, row_idx: int, cell_h: int) -> None:
    px = img.load()
    y0 = row_idx * cell_h
    for y in range(y0, min(img.height, y0 + cell_h)):
        for x in range(img.width):
            px[x, y] = (240, 240, 240) if ((x + y) % 2 == 0) else (16, 16, 16)


def test_row_occupancy_includes_mismatch_localization_and_summary(tmp_path: Path):
    module = _load_module()
    png_path = tmp_path / "frame.png"
    txt_path = tmp_path / "frame.txt"

    img = Image.new("RGB", (40, 40), (31, 36, 48))
    # Make only the first row visually non-empty.
    _make_row_edge_pattern(img, row_idx=0, cell_h=10)
    img.save(png_path)

    # Text has content on row 1 and row 3 -> row 3 should be "missing in render".
    txt_path.write_text("hello\n\nworld\n\n", encoding="utf-8")
    row_occupancy = module.summarize_row_occupancy_from_paths(
        render_png=png_path,
        render_txt=txt_path,
        bg_default=(31, 36, 48),
        cell_height=10,
        pad_y=0,
        declared_rows=4,
    )
    assert row_occupancy["missing_count"] >= 1
    assert 3 in row_occupancy["mismatch_rows"]
    row3 = next(item for item in row_occupancy["mismatch_localization"] if item["row"] == 3)
    assert row3["text_nonempty"] is True
    assert row3["render_content"] is False

    summary = module.build_row_parity_summary(
        row_occupancy=row_occupancy,
        render_png=png_path,
        render_txt=txt_path,
        render_profile="phase4_locked_v4",
    )
    assert summary["schema_version"] == "tmux_row_parity_summary_v1"
    assert summary["parity"]["missing_count"] == row_occupancy["missing_count"]
    assert summary["parity"]["text_sha256_normalized"] == row_occupancy["text_sha256_normalized"]
