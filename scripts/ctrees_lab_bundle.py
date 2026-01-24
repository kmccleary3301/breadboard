#!/usr/bin/env python3
"""Build a minimal C-Trees run bundle from an existing logging run.

This script collects:
- run_summary.json (if present)
- C-Trees artifacts (.breadboard/ctrees/meta)
- a frozen tree view + hashes
- an optional reduced review via scripts/log_reduce.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional
import hashlib

# Ensure repo root is on sys.path when invoked via scripts/...
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agentic_coder_prototype.ctrees.backfill import try_backfill_ctrees_from_eventlog
from agentic_coder_prototype.ctrees.store import CTreeStore
from agentic_coder_prototype.ctrees.tree_view import build_ctree_tree_view


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha256_json(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _git_rev() -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except Exception:
        return None
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a minimal C-Trees run bundle from a logging run directory."
    )
    parser.add_argument(
        "log_dir",
        help="Path to a logging run directory (e.g. logging/20260106-023026_ray_SCE)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output directory for the bundle (default: <log_dir>/ctrees_lab_bundle)",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="Workspace path (defaults to run_summary.json workspace_path when available).",
    )
    parser.add_argument(
        "--ctrees",
        default=None,
        help="C-Trees root directory (default: <workspace>/.breadboard/ctrees).",
    )
    parser.add_argument(
        "--eventlog",
        default=None,
        help="Optional path to events.jsonl (used to backfill ctrees artifacts if missing).",
    )
    parser.add_argument(
        "--stage",
        default="FROZEN",
        help="Tree stage to render (RAW, SPEC, HEADER, FROZEN). Default: FROZEN",
    )
    parser.add_argument(
        "--include-previews",
        action="store_true",
        help="Include sanitized content previews in the tree view metadata.",
    )
    parser.add_argument(
        "--collapse-target",
        type=int,
        default=None,
        help="Optional collapse target for tree view selection.",
    )
    parser.add_argument(
        "--collapse-mode",
        default="all_but_last",
        help="Collapse mode for tree view selection (default: all_but_last).",
    )
    parser.add_argument(
        "--compiler-config",
        default=None,
        help="Optional path to a JSON compiler config for tree view selection.",
    )
    parser.add_argument(
        "--no-log-reduce",
        action="store_true",
        help="Skip running scripts/log_reduce.py.",
    )
    parser.add_argument(
        "--log-reduce-args",
        default=None,
        help="Extra args passed to scripts/log_reduce.py (quote as a single string).",
    )
    parser.add_argument(
        "--tags",
        default=None,
        help="Comma-separated experiment tags to embed in bundle metadata.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite the output directory if it already exists.",
    )
    return parser.parse_args()


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _copy_if_exists(src: Path, dest: Path) -> bool:
    if not src.exists():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return True


def _ensure_empty_dir(path: Path, overwrite: bool) -> None:
    if path.exists():
        if not overwrite:
            raise FileExistsError(f"output directory exists: {path}")
        if path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _load_compiler_config(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    cfg_path = Path(path).expanduser().resolve()
    if not cfg_path.exists():
        raise FileNotFoundError(f"compiler config not found: {cfg_path}")
    payload = _load_json(cfg_path)
    if not isinstance(payload, dict):
        raise ValueError("compiler config must be a JSON object")
    return dict(payload)


def _run_log_reduce(log_dir: Path, output_path: Path, extra_args: Optional[list[str]]) -> None:
    script = Path(__file__).resolve().parent / "log_reduce.py"
    if not script.exists():
        raise FileNotFoundError(f"log_reduce.py not found at {script}")
    cmd = [sys.executable, str(script), str(log_dir)]
    if extra_args:
        cmd.extend([str(arg) for arg in extra_args])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "log_reduce failed")
    output_path.write_text(result.stdout, encoding="utf-8")


def _selection_deltas(store: CTreeStore, view: Dict[str, Any]) -> Dict[str, Any]:
    selection = view.get("selection") if isinstance(view.get("selection"), dict) else {}
    node_by_id = {str(node.get("id")): node for node in (getattr(store, "nodes", []) or []) if node.get("id")}

    def count_by_turn(ids: list[str]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for node_id in ids:
            node = node_by_id.get(node_id)
            turn = node.get("turn") if isinstance(node, dict) else None
            key = str(turn) if isinstance(turn, int) else "unknown"
            counts[key] = counts.get(key, 0) + 1
        return counts

    candidate_ids = [str(x) for x in selection.get("candidate_ids") or [] if str(x)]
    kept_ids = [str(x) for x in selection.get("kept_ids") or selection.get("selected_ids") or [] if str(x)]
    dropped_ids = [str(x) for x in selection.get("dropped_ids") or [] if str(x)]
    collapsed_ids = [str(x) for x in selection.get("collapsed_ids") or [] if str(x)]

    candidate_counts = count_by_turn(candidate_ids)
    kept_counts = count_by_turn(kept_ids)
    dropped_counts = count_by_turn(dropped_ids)
    collapsed_counts = count_by_turn(collapsed_ids)

    turns = sorted(
        {*(candidate_counts.keys()), *(kept_counts.keys()), *(dropped_counts.keys()), *(collapsed_counts.keys())},
        key=lambda v: (0, int(v)) if v.isdigit() else (1, v),
    )
    per_turn = []
    for turn in turns:
        per_turn.append(
            {
                "turn": None if turn == "unknown" else int(turn),
                "candidate_count": candidate_counts.get(turn, 0),
                "kept_count": kept_counts.get(turn, 0),
                "dropped_count": dropped_counts.get(turn, 0),
                "collapsed_count": collapsed_counts.get(turn, 0),
            }
        )

    return {
        "stage": view.get("stage"),
        "selection_sha256": selection.get("selection_sha256"),
        "totals": {
            "candidate_count": len(candidate_ids),
            "kept_count": len(kept_ids),
            "dropped_count": len(dropped_ids),
            "collapsed_count": len(collapsed_ids),
        },
        "per_turn": per_turn,
    }


def _append_selection_deltas(markdown_path: Path, summary: Dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("\n== C-Trees Selection Deltas ==")
    stage = summary.get("stage")
    selection_sha = summary.get("selection_sha256")
    if stage:
        lines.append(f"stage: {stage}")
    if selection_sha:
        lines.append(f"selection_sha256: {selection_sha}")
    totals = summary.get("totals") if isinstance(summary.get("totals"), dict) else {}
    if totals:
        lines.append(
            "totals: "
            f"candidates={totals.get('candidate_count', 0)} "
            f"kept={totals.get('kept_count', 0)} "
            f"dropped={totals.get('dropped_count', 0)} "
            f"collapsed={totals.get('collapsed_count', 0)}"
        )
    per_turn = summary.get("per_turn") if isinstance(summary.get("per_turn"), list) else []
    if per_turn:
        lines.append("per_turn:")
        for row in per_turn:
            if not isinstance(row, dict):
                continue
            turn = row.get("turn")
            label = "unknown" if turn is None else f"{turn}"
            lines.append(
                f"- turn {label}: kept={row.get('kept_count', 0)} "
                f"dropped={row.get('dropped_count', 0)} "
                f"collapsed={row.get('collapsed_count', 0)} "
                f"candidates={row.get('candidate_count', 0)}"
            )

    markdown_path.write_text(markdown_path.read_text(encoding="utf-8") + "\n" + "\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    log_dir = Path(args.log_dir).expanduser().resolve()
    if not log_dir.exists():
        print(f"[error] log_dir not found: {log_dir}", file=sys.stderr)
        return 1

    run_summary_path = log_dir / "meta" / "run_summary.json"
    run_summary = _load_json(run_summary_path) if run_summary_path.exists() else None

    workspace_path = args.workspace
    if not workspace_path and isinstance(run_summary, dict):
        workspace_path = run_summary.get("workspace_path")
    workspace_root = Path(workspace_path).expanduser().resolve() if workspace_path else None

    ctrees_root = args.ctrees
    if not ctrees_root and workspace_root:
        ctrees_root = str(workspace_root / ".breadboard" / "ctrees")
    ctrees_root_path = Path(ctrees_root).expanduser().resolve() if ctrees_root else None

    eventlog_path = Path(args.eventlog).expanduser().resolve() if args.eventlog else None

    out_dir = Path(args.out).expanduser().resolve() if args.out else log_dir / "ctrees_lab_bundle"
    try:
        _ensure_empty_dir(out_dir, overwrite=bool(args.overwrite))
    except FileExistsError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    # Bundle manifest
    manifest = {
        "created_at": _now_iso(),
        "log_dir": str(log_dir),
        "workspace_path": str(workspace_root) if workspace_root else None,
        "ctrees_root": str(ctrees_root_path) if ctrees_root_path else None,
        "eventlog_path": str(eventlog_path) if eventlog_path else None,
        "run_summary_path": str(run_summary_path) if run_summary_path.exists() else None,
        "stage": str(args.stage).upper(),
        "include_previews": bool(args.include_previews),
        "collapse_target": args.collapse_target,
        "collapse_mode": str(args.collapse_mode),
        "compiler_config": None,
        "compiler_config_sha256": None,
        "run_summary_sha256": _sha256_json(run_summary) if isinstance(run_summary, dict) else None,
        "git_rev": _git_rev(),
        "log_reduce_args": args.log_reduce_args,
    }
    if args.tags:
        tags = [tag.strip() for tag in str(args.tags).split(",") if tag.strip()]
        manifest["tags"] = tags
    compiler_config = _load_compiler_config(args.compiler_config)
    if compiler_config:
        manifest["compiler_config"] = compiler_config
        manifest["compiler_config_sha256"] = _sha256_json(compiler_config)
    (out_dir / "bundle_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )

    # Copy run summary (if available)
    if run_summary_path.exists():
        _copy_if_exists(run_summary_path, out_dir / "meta" / "run_summary.json")

    # Ensure C-Trees artifacts
    ctrees_out = out_dir / "ctrees"
    ctrees_meta_out = ctrees_out / "meta"
    ctrees_meta_out.mkdir(parents=True, exist_ok=True)

    events_copied = False
    snapshot_copied = False
    if ctrees_root_path and ctrees_root_path.exists():
        events_copied = _copy_if_exists(
            ctrees_root_path / "meta" / "ctree_events.jsonl",
            ctrees_meta_out / "ctree_events.jsonl",
        )
        snapshot_copied = _copy_if_exists(
            ctrees_root_path / "meta" / "ctree_snapshot.json",
            ctrees_meta_out / "ctree_snapshot.json",
        )

    if not events_copied and eventlog_path and eventlog_path.exists():
        backfill = try_backfill_ctrees_from_eventlog(
            eventlog_path=eventlog_path,
            out_dir=ctrees_out,
            overwrite=True,
        )
        if backfill:
            events_copied = (ctrees_meta_out / "ctree_events.jsonl").exists()
            snapshot_copied = (ctrees_meta_out / "ctree_snapshot.json").exists()
            (out_dir / "ctrees" / "backfill.json").write_text(
                json.dumps(backfill, indent=2, sort_keys=True), encoding="utf-8"
            )

    if not events_copied:
        print("[warn] ctree_events.jsonl not found or backfilled; tree view may be empty.", file=sys.stderr)

    # Build tree view + hashes
    # Reuse compiler_config payload from manifest population
    store = CTreeStore.from_dir(ctrees_out)
    view = build_ctree_tree_view(
        store,
        stage=str(args.stage).upper(),
        compiler_config=compiler_config,
        collapse_target=args.collapse_target,
        collapse_mode=str(args.collapse_mode),
        include_previews=bool(args.include_previews),
    )

    tree_dir = out_dir / "ctrees" / "tree"
    tree_dir.mkdir(parents=True, exist_ok=True)
    stage_key = str(args.stage).strip().lower()
    tree_path = tree_dir / f"{stage_key}.json"
    tree_path.write_text(json.dumps(view, indent=2, sort_keys=True), encoding="utf-8")

    hashes = {
        "node_hash": (store.hashes() or {}).get("node_hash"),
        "tree_sha256": (view.get("hashes") or {}).get("tree_sha256"),
        "z1": (view.get("hashes") or {}).get("z1"),
        "z2": (view.get("hashes") or {}).get("z2"),
        "z3": (view.get("hashes") or {}).get("z3"),
        "selection_sha256": (view.get("selection") or {}).get("selection_sha256"),
    }
    (out_dir / "ctrees" / "hashes.json").write_text(
        json.dumps(hashes, indent=2, sort_keys=True), encoding="utf-8"
    )

    selection_delta_summary = _selection_deltas(store, view)
    (out_dir / "ctrees" / "selection_deltas.json").write_text(
        json.dumps(selection_delta_summary, indent=2, sort_keys=True), encoding="utf-8"
    )

    # Reduced review
    if not args.no_log_reduce:
        review_dir = out_dir / "review"
        review_dir.mkdir(parents=True, exist_ok=True)
        reduced_path = review_dir / "reduced.md"
        extra_args = shlex.split(args.log_reduce_args) if args.log_reduce_args else None
        try:
            _run_log_reduce(log_dir, reduced_path, extra_args)
            try:
                _append_selection_deltas(reduced_path, selection_delta_summary)
            except Exception:
                pass
        except Exception as exc:
            print(f"[warn] log_reduce failed: {exc}", file=sys.stderr)

    print(f"[ctrees-bundle] Wrote {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
