#!/usr/bin/env python3
"""
Build a compact markdown index for phase4 tier3 run galleries.
"""

from __future__ import annotations

import argparse
import glob
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _find_latest_run_dir(base: Path) -> Path | None:
    manifests = sorted(glob.glob(str(base / "*" / "scenario_manifest.json")))
    if not manifests:
        return None
    return Path(manifests[-1]).parent


def _scenario_from_manifest(run_dir: Path) -> str:
    manifest = run_dir / "scenario_manifest.json"
    if not manifest.exists():
        return ""
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return str(payload.get("scenario") or "")
    return ""


def _row(run_dir: Path | None) -> dict[str, Any]:
    if run_dir is None:
        return {"present": False}
    gallery = run_dir / "gallery.html"
    return {
        "present": True,
        "run_dir": str(run_dir),
        "scenario": _scenario_from_manifest(run_dir),
        "gallery": str(gallery) if gallery.exists() else "",
        "gallery_exists": gallery.exists(),
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build phase4 tier3 gallery markdown index.")
    p.add_argument(
        "--scenarios-root",
        default="docs_tmp/tmux_captures/scenarios",
        help="root containing scenario run directories",
    )
    p.add_argument(
        "--output-md",
        default="docs_tmp/tmux_captures/phase4_tier3_gallery_index.md",
        help="output markdown path",
    )
    p.add_argument(
        "--output-json",
        default="docs_tmp/tmux_captures/phase4_tier3_gallery_index.json",
        help="output json path",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.scenarios_root).expanduser().resolve()
    lanes = {
        "resize_storm": _row(_find_latest_run_dir(root / "phase4_replay" / "resize_storm_overlay_v1")),
        "concurrency_20": _row(_find_latest_run_dir(root / "phase4_replay" / "subagents_concurrency_20_v1")),
        "provider_codex": _row(_find_latest_run_dir(root / "nightly_provider" / "codex_e2e_compact_semantic_v1")),
        "provider_claude": _row(_find_latest_run_dir(root / "nightly_provider" / "claude_e2e_compact_semantic_v1")),
    }

    payload = {
        "schema_version": "phase4_tier3_gallery_index_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "lanes": lanes,
    }

    out_json = Path(args.output_json).expanduser().resolve()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Phase4 Tier3 Gallery Index",
        "",
        f"Generated: {payload['generated_at_utc']}",
        "",
        "| Lane | Scenario | Run Dir | Gallery |",
        "|---|---|---|---|",
    ]
    for lane, row in lanes.items():
        if not row.get("present"):
            lines.append(f"| {lane} | missing | missing | missing |")
            continue
        scenario = str(row.get("scenario") or "")
        run_dir = str(row.get("run_dir") or "")
        gallery = str(row.get("gallery") or "")
        gallery_cell = gallery if gallery else "missing"
        lines.append(f"| {lane} | `{scenario}` | `{run_dir}` | `{gallery_cell}` |")

    out_md = Path(args.output_md).expanduser().resolve()
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[phase4-tier3-gallery-index] wrote: {out_md}")
    print(f"[phase4-tier3-gallery-index] wrote: {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
