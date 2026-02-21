#!/usr/bin/env python3
"""
Build a publication-facing benchmark/media pack for the Ink references effort.

This script curates the latest run artifacts for key BreadBoard lanes and
provider reference lanes, then emits:
- media files (final PNG/TXT/ANSI where available)
- JSON manifest with source pointers and checksums
- markdown media index
- markdown benchmark scorecard template
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RunLane:
    key: str
    title: str
    scenario_dir: str
    category: str


RUN_LANES: tuple[RunLane, ...] = (
    RunLane("bb_everything", "BreadBoard Everything Showcase", "phase4_replay/everything_showcase_v1_fullpane_v1", "breadboard"),
    RunLane("bb_streaming", "BreadBoard Streaming Showcase", "phase4_replay/streaming_v1_fullpane_v8", "breadboard"),
    RunLane("bb_todo", "BreadBoard Todo Showcase", "phase4_replay/todo_preview_v1_fullpane_v7", "breadboard"),
    RunLane("bb_subagents", "BreadBoard Subagents Showcase", "phase4_replay/subagents_v1_fullpane_v7", "breadboard"),
    RunLane("bb_alt_buffer", "BreadBoard Alt-Buffer Lane", "phase4_replay/alt_buffer_enter_exit_v1", "breadboard"),
    RunLane("provider_codex", "Codex Provider E2E", "nightly_provider/codex_e2e_compact_semantic_v1", "provider"),
    RunLane("provider_claude", "Claude Provider E2E", "nightly_provider/claude_e2e_compact_semantic_v1", "provider"),
)


STATIC_REFERENCES: tuple[tuple[str, str, str], ...] = (
    ("claude_ref_landing", "Claude Code Landing Reference", "docs_tmp/tui_design/claude_code_refs/landing_01.png"),
    (
        "claude_ref_todo",
        "Claude Code Todo Reference",
        "docs_tmp/tui_design/claude_code_refs/todo_list_while_running_01.png",
    ),
    (
        "claude_ref_patch",
        "Claude Code Patch Preview Reference",
        "docs_tmp/tui_design/claude_code_refs/patch_diff_preview_01.png",
    ),
)


def _workspace_root(repo_root: Path) -> Path:
    if (repo_root / "docs_tmp").exists():
        return repo_root
    if (repo_root.parent / "docs_tmp").exists():
        return repo_root.parent
    return repo_root


def _resolve(path: Path, workspace_root: Path, repo_root: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    for base in (workspace_root, repo_root, repo_root.parent):
        candidate = (base / path).resolve()
        if candidate.exists():
            return candidate
    return (workspace_root / path).resolve()


def _display(path: Path, workspace_root: Path) -> str:
    try:
        return str(path.relative_to(workspace_root))
    except ValueError:
        return str(path)


def _latest_run_dir(scenarios_root: Path, scenario_dir: str) -> Path | None:
    base = scenarios_root / scenario_dir
    manifests = sorted(glob.glob(str(base / "*" / "scenario_manifest.json")))
    if not manifests:
        return None
    return Path(manifests[-1]).parent


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 64), b""):
            h.update(chunk)
    return h.hexdigest()


def _final_frame_rel(run_dir: Path) -> tuple[str, str | None, str | None]:
    index_path = run_dir / "index.jsonl"
    if not index_path.exists():
        return ("", None, None)
    last: dict[str, Any] | None = None
    with index_path.open("r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            row = json.loads(raw)
            if isinstance(row, dict):
                last = row
    if not last:
        return ("", None, None)
    png_rel = str(last.get("png") or "")
    txt_rel = str(last.get("text") or "") or None
    ansi_rel = str(last.get("ansi") or "") or None
    return (png_rel, txt_rel, ansi_rel)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build Ink publication benchmark/media pack.")
    p.add_argument(
        "--scenarios-root",
        default="docs_tmp/tmux_captures/scenarios",
        help="Root containing scenario run directories.",
    )
    p.add_argument(
        "--out-dir",
        default="",
        help="Output directory. Defaults to docs_tmp/cli_phase_5/ink_references/publication_benchmark_pack_<utcstamp>.",
    )
    p.add_argument(
        "--allow-missing",
        action="store_true",
        help="Skip missing lanes/references instead of failing.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    workspace_root = _workspace_root(repo_root)
    scenarios_root = _resolve(Path(args.scenarios_root), workspace_root, repo_root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = _resolve(
        Path(args.out_dir) if args.out_dir else Path(f"docs_tmp/cli_phase_5/ink_references/publication_benchmark_pack_{stamp}"),
        workspace_root,
        repo_root,
    )
    media_dir = out_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "schema_version": "ink_publication_benchmark_pack_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scenarios_root": _display(scenarios_root, workspace_root),
        "pack_dir": _display(out_dir, workspace_root),
        "lanes": {},
        "static_references": {},
    }

    missing: list[str] = []

    for lane in RUN_LANES:
        run_dir = _latest_run_dir(scenarios_root, lane.scenario_dir)
        if run_dir is None:
            missing.append(f"missing run lane: {lane.scenario_dir}")
            manifest["lanes"][lane.key] = {"present": False, "title": lane.title, "category": lane.category}
            continue

        scenario_manifest = _read_json(run_dir / "scenario_manifest.json") if (run_dir / "scenario_manifest.json").exists() else {}
        run_summary = _read_json(run_dir / "run_summary.json") if (run_dir / "run_summary.json").exists() else {}
        png_rel, txt_rel, ansi_rel = _final_frame_rel(run_dir)
        if not png_rel:
            missing.append(f"missing final frame png in lane: {lane.scenario_dir}")
            manifest["lanes"][lane.key] = {"present": False, "title": lane.title, "category": lane.category}
            continue

        src_png = (run_dir / png_rel).resolve()
        src_txt = (run_dir / txt_rel).resolve() if txt_rel else None
        src_ansi = (run_dir / ansi_rel).resolve() if ansi_rel else None
        lane_prefix = f"{lane.key}__final"
        dst_png = media_dir / f"{lane_prefix}.png"
        shutil.copy2(src_png, dst_png)
        dst_txt = None
        dst_ansi = None
        if src_txt and src_txt.exists():
            dst_txt = media_dir / f"{lane_prefix}.txt"
            shutil.copy2(src_txt, dst_txt)
        if src_ansi and src_ansi.exists():
            dst_ansi = media_dir / f"{lane_prefix}.ansi"
            shutil.copy2(src_ansi, dst_ansi)

        manifest["lanes"][lane.key] = {
            "present": True,
            "title": lane.title,
            "category": lane.category,
            "scenario_dir": lane.scenario_dir,
            "run_dir": _display(run_dir, workspace_root),
            "scenario": str(scenario_manifest.get("scenario") or ""),
            "scenario_result": str(run_summary.get("scenario_result") or ""),
            "duration_seconds": run_summary.get("duration_seconds"),
            "frame_count": scenario_manifest.get("frame_validation", {}).get("frame_count"),
            "final_media": {
                "png": str(dst_png.relative_to(out_dir)),
                "txt": str(dst_txt.relative_to(out_dir)) if dst_txt else None,
                "ansi": str(dst_ansi.relative_to(out_dir)) if dst_ansi else None,
            },
            "checksums": {
                "png_sha256": _sha256(dst_png),
                "txt_sha256": _sha256(dst_txt) if dst_txt else None,
                "ansi_sha256": _sha256(dst_ansi) if dst_ansi else None,
            },
        }

    for key, title, raw_path in STATIC_REFERENCES:
        src = _resolve(Path(raw_path), workspace_root, repo_root)
        if not src.exists():
            missing.append(f"missing static reference: {raw_path}")
            manifest["static_references"][key] = {"present": False, "title": title, "source": raw_path}
            continue
        ext = src.suffix or ".bin"
        dst = media_dir / f"{key}{ext}"
        shutil.copy2(src, dst)
        manifest["static_references"][key] = {
            "present": True,
            "title": title,
            "source": _display(src, workspace_root),
            "media": str(dst.relative_to(out_dir)),
            "sha256": _sha256(dst),
        }

    if missing and not args.allow_missing:
        for item in missing:
            print(f"[benchmark-pack] {item}")
        print("[benchmark-pack] aborting due missing required inputs")
        return 2

    manifest["warnings"] = missing
    manifest_path = out_dir / "benchmark_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    media_index_lines = [
        "# Ink Publication Media Index",
        "",
        f"Generated: {manifest['generated_at_utc']}",
        "",
        "## Curated Lanes",
        "",
        "| Key | Title | Scenario Result | Final PNG | Final TXT |",
        "|---|---|---|---|---|",
    ]
    for key, lane in manifest["lanes"].items():
        if not lane.get("present"):
            media_index_lines.append(f"| `{key}` | {lane.get('title','')} | missing | missing | missing |")
            continue
        final = lane.get("final_media", {})
        media_index_lines.append(
            f"| `{key}` | {lane.get('title','')} | `{lane.get('scenario_result','')}` | `{final.get('png','')}` | `{final.get('txt','') or ''}` |"
        )

    media_index_lines.extend(
        [
            "",
            "## Static References",
            "",
            "| Key | Title | Media |",
            "|---|---|---|",
        ]
    )
    for key, ref in manifest["static_references"].items():
        if not ref.get("present"):
            media_index_lines.append(f"| `{key}` | {ref.get('title','')} | missing |")
            continue
        media_index_lines.append(f"| `{key}` | {ref.get('title','')} | `{ref.get('media','')}` |")

    (out_dir / "media_index.md").write_text("\n".join(media_index_lines) + "\n", encoding="utf-8")

    score_lines = [
        "# Ink Publication Benchmark Scorecard (V1)",
        "",
        "Use this rubric for publication-facing comparisons against references.",
        "",
        "Scoring: 1 (poor) -> 5 (excellent)",
        "",
        "| Dimension | Weight | BreadBoard | Claude Ref | Codex Ref | Notes |",
        "|---|---:|---:|---:|---:|---|",
        "| Landing clarity and density | 10 |  |  |  |  |",
        "| Streaming readability under churn | 15 |  |  |  |  |",
        "| Tool/diff artifact legibility | 15 |  |  |  |  |",
        "| Todo/task surface compactness | 10 |  |  |  |  |",
        "| Subagent visibility and control affordances | 10 |  |  |  |  |",
        "| Accessibility mode quality | 10 |  |  |  |  |",
        "| Scroll/long-output ergonomics | 10 |  |  |  |  |",
        "| Deterministic replay stability | 10 |  | n/a | n/a |  |",
        "| Operator observability / run artifacts | 10 |  | n/a | n/a |  |",
        "",
        "## Evidence Pointers",
        "",
        "- Manifest: `benchmark_manifest.json`",
        "- Media index: `media_index.md`",
        "- Curated media: `media/`",
        "",
        "## Current Lane Snapshot",
        "",
    ]
    for key, lane in manifest["lanes"].items():
        if not lane.get("present"):
            continue
        score_lines.append(
            f"- `{key}`: result=`{lane.get('scenario_result','')}`, duration=`{lane.get('duration_seconds','')}`, run=`{lane.get('run_dir','')}`"
        )
    if missing:
        score_lines.extend(["", "## Warnings", ""])
        score_lines.extend([f"- {item}" for item in missing])
    (out_dir / "benchmark_scorecard.md").write_text("\n".join(score_lines) + "\n", encoding="utf-8")

    readme_lines = [
        "# Ink Publication Benchmark Pack",
        "",
        f"Generated: {manifest['generated_at_utc']}",
        "",
        "- `benchmark_manifest.json`: machine-readable pack metadata.",
        "- `media_index.md`: human-readable lane/reference media list.",
        "- `benchmark_scorecard.md`: weighted scoring template for publication comparisons.",
        "- `media/`: copied final artifacts from latest scenario runs + static reference imagery.",
    ]
    (out_dir / "README.md").write_text("\n".join(readme_lines) + "\n", encoding="utf-8")

    print(f"[benchmark-pack] wrote: {out_dir}")
    print(f"[benchmark-pack] manifest: {manifest_path}")
    if missing:
        print(f"[benchmark-pack] warnings: {len(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

