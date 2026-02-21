from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "bless_render_profile_version.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("bless_render_profile_version", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_manifest(tmp_path: Path) -> Path:
    manifest_path = tmp_path / "manifest.json"
    payload = {
        "schema_version": "render_profile_freeze_manifest_v1",
        "default_render_profile_id": "phase4_locked_v3",
        "supported_render_profiles": [
            "phase4_locked_v4",
            "phase4_locked_v3",
            "phase4_locked_v2",
            "legacy",
        ],
        "frozen_profile_ids": ["phase4_locked_v2", "phase4_locked_v3", "phase4_locked_v4"],
        "profiles": {
            "phase4_locked_v2": {"id": "phase4_locked_v2", "description": "v2", "font_size": 18},
            "phase4_locked_v3": {"id": "phase4_locked_v3", "description": "v3", "font_size": 15},
            "phase4_locked_v4": {"id": "phase4_locked_v4", "description": "v4", "font_size": 14},
        },
    }
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def test_bless_dry_run_does_not_mutate_manifest(tmp_path: Path):
    module = _load_module()
    manifest = _write_manifest(tmp_path)
    before = manifest.read_text(encoding="utf-8")
    result = module.bless_profile(
        manifest_path=manifest,
        new_profile_id="phase4_locked_v5",
        source_profile_id="phase4_locked_v4",
        changelog_dir=tmp_path,
        apply=False,
    )
    assert result["ok"] is True
    assert "phase4_locked_v5" in result["new_supported_render_profiles"]
    assert "phase4_locked_v5" in result["new_frozen_profile_ids"]
    assert manifest.read_text(encoding="utf-8") == before
    assert Path(result["changelog_stub"]).exists()


def test_bless_apply_updates_supported_order(tmp_path: Path):
    module = _load_module()
    manifest = _write_manifest(tmp_path)
    module.bless_profile(
        manifest_path=manifest,
        new_profile_id="phase4_locked_v5",
        source_profile_id="phase4_locked_v4",
        changelog_dir=tmp_path,
        apply=True,
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["supported_render_profiles"][:4] == [
        "phase4_locked_v5",
        "phase4_locked_v4",
        "phase4_locked_v3",
        "phase4_locked_v2",
    ]


def test_bless_rejects_duplicate_id(tmp_path: Path):
    module = _load_module()
    manifest = _write_manifest(tmp_path)
    try:
        module.bless_profile(
            manifest_path=manifest,
            new_profile_id="phase4_locked_v4",
            source_profile_id="phase4_locked_v4",
            changelog_dir=tmp_path,
            apply=False,
        )
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("expected duplicate-id failure")


def test_bless_rejects_malformed_id(tmp_path: Path):
    module = _load_module()
    manifest = _write_manifest(tmp_path)
    try:
        module.bless_profile(
            manifest_path=manifest,
            new_profile_id="phase4_locked_candidate",
            source_profile_id="phase4_locked_v4",
            changelog_dir=tmp_path,
            apply=False,
        )
    except ValueError as exc:
        assert "must match" in str(exc)
    else:
        raise AssertionError("expected malformed-id failure")
