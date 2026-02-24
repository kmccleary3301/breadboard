from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from PIL import Image


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "generate_phase5_footer_qc_pack.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("generate_phase5_footer_qc_pack", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_footer_contrast_metrics_detects_blank_crop():
    module = _load_module()
    img = Image.new("RGB", (64, 24), (31, 36, 48))
    metrics = module._footer_contrast_metrics(img)
    assert metrics["max_channel_delta"] == 0.0
    assert metrics["p99_channel_delta"] == 0.0
    assert metrics["ratio_delta_gt_20"] == 0.0


def test_footer_contrast_metrics_detects_legible_text_pixels():
    module = _load_module()
    img = Image.new("RGB", (64, 24), (31, 36, 48))
    # Simulate a bright status token + text streak.
    for x in range(4, 16):
        for y in range(6, 14):
            img.putpixel((x, y), (50, 220, 190))
    metrics = module._footer_contrast_metrics(img)
    assert metrics["p99_channel_delta"] >= 20.0
    assert metrics["ratio_delta_gt_20"] > 0.0
    assert metrics["ratio_delta_gt_40"] > 0.0

