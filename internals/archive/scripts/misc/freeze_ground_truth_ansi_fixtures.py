#!/usr/bin/env python3
"""
Freeze ground-truth ANSI captures from an evaluation run into reusable fixtures.

This creates a stable fixture directory with:
- per-target `.ansi` and `.txt`,
- per-target provenance sidecars (`.render_lock.json`, `.row_parity.json`),
- a fixture manifest consumable by evaluate_screenshot_ground_truth.py
  via `--capture-source frozen-ansi`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any


TARGETS = (
    "screenshot_ground_truth_1",
    "screenshot_ground_truth_2",
    "screenshot_ground_truth_3",
)
SCHEMA_VERSION = "ground_truth_frozen_ansi_fixture_v1"

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = ROOT.parent
DEFAULT_OUT_ROOT = WORKSPACE_ROOT / "docs_tmp" / "cli_phase_5" / "ground_truth_frozen_ansi"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Freeze ANSI fixtures from a ground-truth evaluation run.")
    p.add_argument("--source-run-dir", required=True, help="ground-truth eval run dir")
    p.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    p.add_argument("--fixture-id", default="")
    return p.parse_args()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def main() -> None:
    args = parse_args()
    src_run_dir = Path(args.source_run_dir).expanduser().resolve()
    if not src_run_dir.exists():
        raise FileNotFoundError(f"source run dir missing: {src_run_dir}")

    metrics_path = src_run_dir / "comparison_metrics.json"
    if not metrics_path.exists():
        raise FileNotFoundError(f"comparison_metrics.json missing in source run dir: {src_run_dir}")
    metrics = _load_json(metrics_path)
    if not isinstance(metrics.get("targets"), dict):
        raise ValueError(f"invalid comparison_metrics targets block: {metrics_path}")

    out_root = Path(args.out_root).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    fixture_id = args.fixture_id.strip() or time.strftime("fixture_%Y%m%d-%H%M%S")
    fixture_dir = out_root / fixture_id
    fixture_dir.mkdir(parents=True, exist_ok=True)

    manifest_targets: dict[str, Any] = {}
    for target in TARGETS:
        block = metrics["targets"].get(target)
        if not isinstance(block, dict):
            raise ValueError(f"missing target block in source metrics: {target}")
        repeats = block.get("repeats")
        if not isinstance(repeats, list) or not repeats:
            raise ValueError(f"missing repeats for target `{target}` in source metrics")
        first = repeats[0]
        if not isinstance(first, dict):
            raise ValueError(f"invalid repeat row for target `{target}`")

        ansi_src = Path(str(first.get("ansi") or "")).expanduser().resolve()
        txt_src = Path(str(first.get("txt") or "")).expanduser().resolve()
        lock_src = Path(str(first.get("render_lock_json") or "")).expanduser().resolve()
        parity_src = Path(str(first.get("row_parity_summary_json") or "")).expanduser().resolve()
        if not ansi_src.exists() or not txt_src.exists() or not lock_src.exists() or not parity_src.exists():
            raise FileNotFoundError(f"source artifacts missing for `{target}` in `{src_run_dir}`")

        lock_payload = _load_json(lock_src)
        geometry = lock_payload.get("geometry")
        if not isinstance(geometry, dict):
            raise ValueError(f"render lock geometry missing for `{target}`: {lock_src}")
        cols = int(geometry.get("cols", 0) or 0)
        rows = int(geometry.get("rows", 0) or 0)
        if cols <= 0 or rows <= 0:
            raise ValueError(f"invalid cols/rows in render lock for `{target}`: {lock_src}")

        ansi_dst = fixture_dir / f"{target}.ansi"
        txt_dst = fixture_dir / f"{target}.txt"
        lock_dst = fixture_dir / f"{target}.render_lock.json"
        parity_dst = fixture_dir / f"{target}.row_parity.json"
        shutil.copy2(ansi_src, ansi_dst)
        shutil.copy2(txt_src, txt_dst)
        shutil.copy2(lock_src, lock_dst)
        shutil.copy2(parity_src, parity_dst)

        manifest_targets[target] = {
            "ansi": str(ansi_dst),
            "txt": str(txt_dst),
            "cols": cols,
            "rows": rows,
            "ansi_sha256": _sha256_file(ansi_dst),
            "txt_sha256": _sha256_file(txt_dst),
            "source_run_dir": str(src_run_dir),
            "source_artifacts": {
                "ansi": str(ansi_src),
                "txt": str(txt_src),
                "render_lock_json": str(lock_src),
                "row_parity_summary_json": str(parity_src),
            },
        }

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "fixture_id": fixture_id,
        "fixture_dir": str(fixture_dir),
        "source_run_dir": str(src_run_dir),
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "targets": manifest_targets,
    }
    manifest_path = fixture_dir / "fixture_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (out_root / "LATEST_FIXTURE_PATH.txt").write_text(str(manifest_path) + "\n", encoding="utf-8")
    print(str(manifest_path))


if __name__ == "__main__":
    main()
