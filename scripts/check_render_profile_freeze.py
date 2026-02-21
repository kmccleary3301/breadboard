#!/usr/bin/env python3
"""
Validate render-profile freeze contract against an immutable manifest.

Exit codes:
- 0: pass
- 2: freeze mismatch
- 3: invalid input/runtime error
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from tmux_capture_render_profile import DEFAULT_RENDER_PROFILE_ID
from tmux_capture_render_profile import SUPPORTED_RENDER_PROFILES
from tmux_capture_render_profile import resolve_render_profile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "config" / "render_profile_freeze_manifest.json"
SCHEMA_VERSION = "render_profile_freeze_manifest_v1"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"manifest must be a JSON object: {path}")
    return payload


def _canonical_snapshot(*, frozen_ids: list[str]) -> dict[str, Any]:
    profiles: dict[str, Any] = {}
    for profile_id in frozen_ids:
        profile = resolve_render_profile(profile_id)
        if profile is None:
            raise ValueError(f"frozen profile id resolves to legacy profile: {profile_id}")
        profiles[profile_id] = asdict(profile)
    return {
        "schema_version": SCHEMA_VERSION,
        "default_render_profile_id": DEFAULT_RENDER_PROFILE_ID,
        "supported_render_profiles": list(SUPPORTED_RENDER_PROFILES),
        "frozen_profile_ids": list(frozen_ids),
        "profiles": profiles,
    }


def _compare_profile(
    errors: list[str],
    *,
    profile_id: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> None:
    expected_keys = set(expected.keys())
    actual_keys = set(actual.keys())
    for key in sorted(expected_keys - actual_keys):
        errors.append(f"{profile_id}: missing field in code snapshot: {key}")
    for key in sorted(actual_keys - expected_keys):
        errors.append(f"{profile_id}: unexpected field in code snapshot: {key}")
    for key in sorted(expected_keys & actual_keys):
        if expected.get(key) != actual.get(key):
            errors.append(
                f"{profile_id}: frozen field mismatch for `{key}` "
                f"(manifest={expected.get(key)!r}, code={actual.get(key)!r})"
            )


def validate_manifest(manifest_path: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    manifest = _load_json(manifest_path)

    schema = str(manifest.get("schema_version") or "")
    if schema != SCHEMA_VERSION:
        errors.append(f"schema_version mismatch: expected {SCHEMA_VERSION!r}, got {schema!r}")

    raw_frozen = manifest.get("frozen_profile_ids")
    if not isinstance(raw_frozen, list) or not raw_frozen:
        errors.append("frozen_profile_ids must be a non-empty array")
        frozen_ids: list[str] = []
    else:
        frozen_ids = [str(x) for x in raw_frozen]
        if len(frozen_ids) != len(set(frozen_ids)):
            errors.append("frozen_profile_ids contains duplicates")

    expected_default = str(manifest.get("default_render_profile_id") or "")
    expected_supported = manifest.get("supported_render_profiles")
    expected_profiles = manifest.get("profiles")
    if not isinstance(expected_supported, list):
        errors.append("supported_render_profiles must be an array")
        expected_supported = []
    if not isinstance(expected_profiles, dict):
        errors.append("profiles must be an object")
        expected_profiles = {}

    if expected_default and expected_default != DEFAULT_RENDER_PROFILE_ID:
        errors.append(
            "default_render_profile_id mismatch: "
            f"manifest={expected_default!r} code={DEFAULT_RENDER_PROFILE_ID!r}"
        )
    if list(expected_supported) and list(expected_supported) != list(SUPPORTED_RENDER_PROFILES):
        errors.append(
            "supported_render_profiles mismatch: "
            f"manifest={list(expected_supported)!r} code={list(SUPPORTED_RENDER_PROFILES)!r}"
        )

    for profile_id in frozen_ids:
        if profile_id not in SUPPORTED_RENDER_PROFILES:
            errors.append(f"frozen profile id not in supported_render_profiles: {profile_id}")
        expected = expected_profiles.get(profile_id)
        if not isinstance(expected, dict):
            errors.append(f"manifest missing profile snapshot for frozen id: {profile_id}")
            continue
        profile = resolve_render_profile(profile_id)
        if profile is None:
            errors.append(f"frozen id resolves to legacy profile: {profile_id}")
            continue
        actual = asdict(profile)
        _compare_profile(errors, profile_id=profile_id, expected=expected, actual=actual)

    current_snapshot = _canonical_snapshot(frozen_ids=frozen_ids) if frozen_ids else {}
    result = {
        "ok": len(errors) == 0,
        "manifest_path": str(manifest_path),
        "errors": errors,
        "warnings": warnings,
        "manifest_snapshot": manifest,
        "current_snapshot": current_snapshot,
    }
    return result


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate immutable render-profile freeze manifest.")
    p.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Path to freeze manifest JSON.")
    p.add_argument("--output-json", default="", help="Optional path to write validation JSON.")
    p.add_argument(
        "--write-current-snapshot",
        default="",
        help="Optional path to write the computed current snapshot JSON.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest_path = Path(args.manifest).expanduser().resolve()
        if not manifest_path.exists():
            raise FileNotFoundError(f"manifest not found: {manifest_path}")
        result = validate_manifest(manifest_path)
        if args.output_json:
            out_json = Path(args.output_json).expanduser().resolve()
            out_json.parent.mkdir(parents=True, exist_ok=True)
            out_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        if args.write_current_snapshot:
            out_snapshot = Path(args.write_current_snapshot).expanduser().resolve()
            out_snapshot.parent.mkdir(parents=True, exist_ok=True)
            out_snapshot.write_text(
                json.dumps(result.get("current_snapshot", {}), indent=2) + "\n",
                encoding="utf-8",
            )
        print(json.dumps({"ok": result["ok"], "errors": result["errors"]}, indent=2))
        return 0 if result["ok"] else 2
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
