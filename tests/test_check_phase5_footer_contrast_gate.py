from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from PIL import Image


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "check_phase5_footer_contrast_gate.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("check_phase5_footer_contrast_gate", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_pack(path: Path, target_key: str, contrast: dict | None):
    target = {
        "scenario": f"phase4_replay/{target_key}",
        "outputs": {"final_crop": f"{target_key}_final_boosted.png"},
    }
    if contrast is not None:
        target["final_footer_contrast"] = contrast
    payload = {
        "schema_version": "phase5_footer_qc_pack_v2",
        "targets": {target_key: target},
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_footer_contrast_gate_passes_with_embedded_contrast(tmp_path: Path):
    module = _load_module()
    pack = tmp_path / "footer_qc_pack.json"
    _write_pack(
        pack,
        "todo",
        {
            "p99_channel_delta": 62.0,
            "ratio_delta_gt_20": 0.019,
        },
    )
    report = module.build_report(
        footer_qc_pack=json.loads(pack.read_text(encoding="utf-8")),
        footer_qc_dir=tmp_path,
        min_p99=24.0,
        min_ratio_gt20=0.003,
        blank_p99=8.0,
        blank_ratio_gt20=0.0005,
    )
    assert report["overall_ok"] is True
    assert report["failure_count"] == 0


def test_footer_contrast_gate_fails_blank_crop_inferred(tmp_path: Path):
    module = _load_module()
    pack = tmp_path / "footer_qc_pack.json"
    _write_pack(pack, "todo", None)
    Image.new("RGB", (64, 24), (31, 36, 48)).save(tmp_path / "todo_final_boosted.png")
    report = module.build_report(
        footer_qc_pack=json.loads(pack.read_text(encoding="utf-8")),
        footer_qc_dir=tmp_path,
        min_p99=24.0,
        min_ratio_gt20=0.003,
        blank_p99=8.0,
        blank_ratio_gt20=0.0005,
    )
    assert report["overall_ok"] is False
    assert report["failure_count"] == 1
    row = report["rows"][0]
    assert row["contrast_source"] == "inferred"
    assert row["reason"] == "blank_or_severely_under_rendered"


def test_footer_contrast_gate_passes_legacy_pack_with_legible_crop(tmp_path: Path):
    module = _load_module()
    pack = tmp_path / "footer_qc_pack.json"
    _write_pack(pack, "todo", None)
    img = Image.new("RGB", (64, 24), (31, 36, 48))
    for x in range(4, 16):
        for y in range(6, 14):
            img.putpixel((x, y), (50, 220, 190))
    img.save(tmp_path / "todo_final_boosted.png")
    report = module.build_report(
        footer_qc_pack=json.loads(pack.read_text(encoding="utf-8")),
        footer_qc_dir=tmp_path,
        min_p99=24.0,
        min_ratio_gt20=0.003,
        blank_p99=8.0,
        blank_ratio_gt20=0.0005,
    )
    assert report["overall_ok"] is True
    assert report["failure_count"] == 0
    row = report["rows"][0]
    assert row["contrast_source"] == "inferred"

