#!/usr/bin/env python3
"""
Scaffold and bless a new phase4 locked render profile version in the freeze manifest.

By default this runs in dry-run mode and writes a plan artifact.
Use --apply to mutate the manifest.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from check_render_profile_freeze import DEFAULT_MANIFEST
from check_render_profile_freeze import SCHEMA_VERSION


PHASE4_LOCKED_ID_RE = re.compile(r"^phase4_locked_v([0-9]+)$")


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"manifest must be object: {path}")
    if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version in manifest: {path}")
    return payload


def _parse_version(profile_id: str) -> int | None:
    m = PHASE4_LOCKED_ID_RE.match(profile_id)
    if not m:
        return None
    return int(m.group(1))


def _insert_supported_order(existing: list[str], new_profile_id: str) -> list[str]:
    without_new = [x for x in existing if x != new_profile_id]
    locked = [x for x in without_new if _parse_version(x) is not None]
    other = [x for x in without_new if _parse_version(x) is None]
    locked.append(new_profile_id)
    locked.sort(key=lambda x: _parse_version(x) or -1, reverse=True)
    return [*locked, *other]


def _render_changelog_stub(*, new_profile_id: str, source_profile_id: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return "\n".join(
        [
            f"# Render Profile Bless Changelog Stub ({new_profile_id})",
            "",
            f"- Generated: `{now}`",
            f"- Source profile: `{source_profile_id}`",
            f"- New profile: `{new_profile_id}`",
            "",
            "## Required validation checklist",
            "1. Run lock checker:",
            "   - `python scripts/check_render_profile_freeze.py --manifest config/render_profile_freeze_manifest.json`",
            "2. Run ground-truth evaluator (>=2 repeats):",
            "   - `python scripts/evaluate_screenshot_ground_truth.py --render-profile <new_profile_id> --repeats 2 --out-root ../docs_tmp/cli_phase_5/ground_truth_eval --run-id <run_id>`",
            "3. Validate run artifacts:",
            "   - `python scripts/validate_ground_truth_eval_run.py --run-dir <run_dir>`",
            "4. Strict compare vs current canonical run:",
            "   - `python scripts/compare_ground_truth_eval_runs.py --run-a <current_run> --run-b <new_run> --strict --out-json <out.json> --out-md <out.md>`",
            "5. Run phase4 scenario validation (todo/subagents):",
            "   - `python scripts/run_tmux_capture_scenario.py ...`",
            "   - `python scripts/validate_tmux_capture_run.py --run-dir <scenario_run> --strict`",
            "",
            "## Notes",
            "- Update `scripts/tmux_capture_render_profile.py` with the new profile constants and ordering.",
            "- Do not mutate previously frozen profile constants in-place.",
            "",
        ]
    ) + "\n"


def bless_profile(
    *,
    manifest_path: Path,
    new_profile_id: str,
    source_profile_id: str,
    changelog_dir: Path,
    apply: bool,
) -> dict[str, Any]:
    manifest = _load_manifest(manifest_path)
    supported = list(manifest.get("supported_render_profiles") or [])
    frozen = list(manifest.get("frozen_profile_ids") or [])
    profiles = dict(manifest.get("profiles") or {})

    if _parse_version(new_profile_id) is None:
        raise ValueError(
            f"new profile id must match {PHASE4_LOCKED_ID_RE.pattern}: {new_profile_id!r}"
        )
    if new_profile_id in supported or new_profile_id in profiles:
        raise ValueError(f"new profile id already exists in manifest: {new_profile_id}")

    source_profile = profiles.get(source_profile_id)
    if not isinstance(source_profile, dict):
        raise ValueError(f"source profile id missing in manifest profiles: {source_profile_id}")

    new_profile = copy.deepcopy(source_profile)
    new_profile["id"] = new_profile_id
    new_profile["description"] = (
        f"Deterministic phase4 replay profile blessed from {source_profile_id}."
    )

    new_manifest = copy.deepcopy(manifest)
    new_manifest["profiles"][new_profile_id] = new_profile
    new_manifest["supported_render_profiles"] = _insert_supported_order(
        existing=supported,
        new_profile_id=new_profile_id,
    )
    if new_profile_id not in new_manifest["frozen_profile_ids"]:
        new_manifest["frozen_profile_ids"] = [*frozen, new_profile_id]

    changelog_dir.mkdir(parents=True, exist_ok=True)
    changelog_path = changelog_dir / f"render_profile_bless_{new_profile_id}.md"
    changelog_content = _render_changelog_stub(
        new_profile_id=new_profile_id,
        source_profile_id=source_profile_id,
    )
    changelog_path.write_text(changelog_content, encoding="utf-8")

    if apply:
        manifest_path.write_text(json.dumps(new_manifest, indent=2) + "\n", encoding="utf-8")

    return {
        "ok": True,
        "manifest_path": str(manifest_path),
        "apply": bool(apply),
        "new_profile_id": new_profile_id,
        "source_profile_id": source_profile_id,
        "changelog_stub": str(changelog_path),
        "new_supported_render_profiles": new_manifest["supported_render_profiles"],
        "new_frozen_profile_ids": new_manifest["frozen_profile_ids"],
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Bless a new render profile version in freeze manifest.")
    p.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    p.add_argument("--new-profile-id", required=True)
    p.add_argument("--source-profile-id", default="phase4_locked_v5")
    p.add_argument(
        "--changelog-dir",
        default=str((DEFAULT_MANIFEST.parents[1] / "docs_tmp" / "cli_phase_5").resolve()),
    )
    p.add_argument("--apply", action="store_true", help="Apply manifest mutation (default dry-run).")
    p.add_argument("--output-json", default="")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = bless_profile(
            manifest_path=Path(args.manifest).expanduser().resolve(),
            new_profile_id=str(args.new_profile_id).strip(),
            source_profile_id=str(args.source_profile_id).strip(),
            changelog_dir=Path(args.changelog_dir).expanduser().resolve(),
            apply=bool(args.apply),
        )
        if args.output_json:
            out = Path(args.output_json).expanduser().resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
