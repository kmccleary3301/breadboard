#!/usr/bin/env python3
"""C-Trees lab utilities: validate/diff bundles and quick checks."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agentic_coder_prototype.ctrees.store import CTreeStore
from agentic_coder_prototype.ctrees.tree_view import build_ctree_tree_view


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _resolve_stage(manifest: Dict[str, Any], bundle_dir: Path) -> str:
    stage = manifest.get("stage")
    if isinstance(stage, str) and stage.strip():
        return stage.strip().upper()
    tree_dir = bundle_dir / "ctrees" / "tree"
    if tree_dir.exists():
        for entry in tree_dir.iterdir():
            if entry.suffix == ".json":
                return entry.stem.upper()
    return "FROZEN"


def _resolve_compiler_config(manifest: Dict[str, Any]) -> Dict[str, Any]:
    cfg = manifest.get("compiler_config")
    return cfg if isinstance(cfg, dict) else {}


def _compute_hashes(bundle_dir: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    ctrees_root = bundle_dir / "ctrees"
    store = CTreeStore.from_dir(str(ctrees_root))

    stage = _resolve_stage(manifest, bundle_dir)
    include_previews = bool(manifest.get("include_previews"))
    collapse_target = manifest.get("collapse_target")
    collapse_mode = str(manifest.get("collapse_mode") or "all_but_last")
    compiler_config = _resolve_compiler_config(manifest)

    view = build_ctree_tree_view(
        store,
        stage=stage,
        compiler_config=dict(compiler_config),
        collapse_target=collapse_target,
        collapse_mode=collapse_mode,
        include_previews=include_previews,
    )
    hashes = {
        "node_hash": (store.hashes() or {}).get("node_hash"),
        "tree_sha256": (view.get("hashes") or {}).get("tree_sha256"),
        "z1": (view.get("hashes") or {}).get("z1"),
        "z2": (view.get("hashes") or {}).get("z2"),
        "z3": (view.get("hashes") or {}).get("z3"),
        "selection_sha256": (view.get("selection") or {}).get("selection_sha256"),
    }
    return {"hashes": hashes, "view": view, "stage": stage}


@dataclass
class ValidationResult:
    ok: bool
    errors: List[str]
    computed: Dict[str, Any]


def validate_bundle(bundle_dir: Path) -> ValidationResult:
    errors: List[str] = []
    bundle_dir = bundle_dir.expanduser().resolve()
    manifest = _load_json(bundle_dir / "bundle_manifest.json") or {}

    hashes_path = bundle_dir / "ctrees" / "hashes.json"
    expected_hashes = _load_json(hashes_path) if hashes_path.exists() else None
    if expected_hashes is None:
        errors.append("missing hashes.json in bundle/ctrees")

    computed = _compute_hashes(bundle_dir, manifest)
    computed_hashes = computed.get("hashes") or {}

    if isinstance(expected_hashes, dict):
        for key, expected in expected_hashes.items():
            actual = computed_hashes.get(key)
            if expected != actual:
                errors.append(f"hash mismatch {key}: expected={expected} actual={actual}")

    stage = computed.get("stage")
    tree_path = bundle_dir / "ctrees" / "tree" / f"{str(stage).lower()}.json"
    if tree_path.exists():
        tree_payload = _load_json(tree_path) or {}
        tree_hashes = tree_payload.get("hashes") if isinstance(tree_payload.get("hashes"), dict) else {}
        expected_tree_sha = tree_hashes.get("tree_sha256")
        actual_tree_sha = computed_hashes.get("tree_sha256")
        if expected_tree_sha and actual_tree_sha and expected_tree_sha != actual_tree_sha:
            errors.append(f"tree hash mismatch tree_sha256: expected={expected_tree_sha} actual={actual_tree_sha}")

    return ValidationResult(ok=len(errors) == 0, errors=errors, computed=computed)


def diff_bundles(bundle_a: Path, bundle_b: Path) -> Dict[str, Any]:
    bundle_a = bundle_a.expanduser().resolve()
    bundle_b = bundle_b.expanduser().resolve()
    manifest_a = _load_json(bundle_a / "bundle_manifest.json") or {}
    manifest_b = _load_json(bundle_b / "bundle_manifest.json") or {}

    hashes_a = _load_json(bundle_a / "ctrees" / "hashes.json") or {}
    hashes_b = _load_json(bundle_b / "ctrees" / "hashes.json") or {}

    diff: Dict[str, Any] = {"hashes": {}, "selection": {}}
    for key in sorted(set(hashes_a.keys()) | set(hashes_b.keys())):
        if hashes_a.get(key) != hashes_b.get(key):
            diff["hashes"][key] = {"a": hashes_a.get(key), "b": hashes_b.get(key)}

    stage_a = _resolve_stage(manifest_a, bundle_a)
    stage_b = _resolve_stage(manifest_b, bundle_b)
    tree_a = _load_json(bundle_a / "ctrees" / "tree" / f"{str(stage_a).lower()}.json") or {}
    tree_b = _load_json(bundle_b / "ctrees" / "tree" / f"{str(stage_b).lower()}.json") or {}
    selection_a = tree_a.get("selection") if isinstance(tree_a.get("selection"), dict) else {}
    selection_b = tree_b.get("selection") if isinstance(tree_b.get("selection"), dict) else {}

    selection_keys = ["candidate_count", "kept_count", "dropped_count", "collapsed_count", "selection_sha256"]
    for key in selection_keys:
        if selection_a.get(key) != selection_b.get(key):
            diff["selection"][key] = {"a": selection_a.get(key), "b": selection_b.get(key)}

    diff["bundle_a"] = str(bundle_a)
    diff["bundle_b"] = str(bundle_b)
    return diff


def quick_check(bundle_dir: Path) -> ValidationResult:
    bundle_dir = bundle_dir.expanduser().resolve()
    errors: List[str] = []
    if not (bundle_dir / "ctrees" / "meta" / "ctree_events.jsonl").exists():
        errors.append("missing ctrees/meta/ctree_events.jsonl")
    computed = _compute_hashes(bundle_dir, _load_json(bundle_dir / "bundle_manifest.json") or {})
    return ValidationResult(ok=len(errors) == 0, errors=errors, computed=computed)


def lint_bundle(bundle_dir: Path) -> ValidationResult:
    bundle_dir = bundle_dir.expanduser().resolve()
    errors: List[str] = []
    manifest_path = bundle_dir / "bundle_manifest.json"
    manifest = _load_json(manifest_path)
    if manifest is None:
        errors.append("missing bundle_manifest.json")
        manifest = {}
    required_files = [
        bundle_dir / "ctrees" / "meta" / "ctree_events.jsonl",
        bundle_dir / "ctrees" / "hashes.json",
    ]
    for path in required_files:
        if not path.exists():
            errors.append(f"missing required file: {path.relative_to(bundle_dir)}")
    return ValidationResult(ok=len(errors) == 0, errors=errors, computed={"manifest": manifest or {}})


def open_review(bundle_dir: Path) -> Tuple[bool, str]:
    bundle_dir = bundle_dir.expanduser().resolve()
    review_path = bundle_dir / "review" / "reduced.md"
    if not review_path.exists():
        return False, f"missing review file: {review_path}"
    return True, review_path.read_text(encoding="utf-8")


def _find_bundles(root: Path) -> List[Path]:
    bundles: List[Path] = []
    if not root.exists():
        return bundles
    for path in root.rglob("ctrees_lab_bundle"):
        if path.is_dir() and (path / "bundle_manifest.json").exists():
            bundles.append(path)
    return sorted(bundles)


def gauntlet(root: Path, *, mode: str) -> Dict[str, Any]:
    bundles = _find_bundles(root)
    results: List[Dict[str, Any]] = []
    for bundle in bundles:
        if mode == "validate":
            result = validate_bundle(bundle)
        elif mode == "lint":
            result = lint_bundle(bundle)
        else:
            result = quick_check(bundle)
        results.append(
            {
                "bundle": str(bundle),
                "ok": result.ok,
                "errors": result.errors,
                "hashes": (result.computed.get("hashes") if isinstance(result.computed, dict) else None),
            }
        )
    return {
        "root": str(root),
        "mode": mode,
        "count": len(results),
        "ok": sum(1 for item in results if item.get("ok")),
        "failed": sum(1 for item in results if not item.get("ok")),
        "results": results,
    }


def _print_validation(result: ValidationResult, *, label: str) -> int:
    if result.ok:
        print(f"[ctlab] {label}: ok")
        return 0
    print(f"[ctlab] {label}: failed")
    for err in result.errors:
        print(f"  - {err}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="C-Trees bundle tooling (validate/diff/quick/lint).")
    sub = parser.add_subparsers(dest="command", required=True)

    validate_parser = sub.add_parser("validate", help="Recompute hashes and compare to bundle.")
    validate_parser.add_argument("bundle", help="Path to a ctrees_lab_bundle directory.")

    diff_parser = sub.add_parser("diff", help="Compare two bundles' hashes and selection summaries.")
    diff_parser.add_argument("bundle_a", help="Path to bundle A.")
    diff_parser.add_argument("bundle_b", help="Path to bundle B.")

    quick_parser = sub.add_parser("quick", help="Fast check: required artifacts + recompute hashes.")
    quick_parser.add_argument("bundle", help="Path to a ctrees_lab_bundle directory.")

    lint_parser = sub.add_parser("lint", help="Validate bundle structure + manifest presence.")
    lint_parser.add_argument("bundle", help="Path to a ctrees_lab_bundle directory.")

    review_parser = sub.add_parser("review", help="Print reduced review markdown from a bundle.")
    review_parser.add_argument("bundle", help="Path to a ctrees_lab_bundle directory.")

    gauntlet_parser = sub.add_parser("gauntlet", help="Run quick/validate/lint across all bundles.")
    gauntlet_parser.add_argument(
        "--root",
        default="logging",
        help="Root directory to scan for ctrees_lab_bundle (default: logging)",
    )
    gauntlet_parser.add_argument(
        "--mode",
        choices=["quick", "validate", "lint"],
        default="quick",
        help="Validation mode (default: quick)",
    )
    gauntlet_parser.add_argument("--out", help="Optional output JSON path")

    args = parser.parse_args()
    cmd = args.command

    if cmd == "validate":
        result = validate_bundle(Path(args.bundle))
        return _print_validation(result, label="validate")
    if cmd == "quick":
        result = quick_check(Path(args.bundle))
        return _print_validation(result, label="quick")
    if cmd == "lint":
        result = lint_bundle(Path(args.bundle))
        return _print_validation(result, label="lint")
    if cmd == "diff":
        diff = diff_bundles(Path(args.bundle_a), Path(args.bundle_b))
        print(json.dumps(diff, indent=2, sort_keys=True))
        return 0
    if cmd == "review":
        ok, payload = open_review(Path(args.bundle))
        if ok:
            print(payload)
            return 0
        print(f"[ctlab] {payload}", file=sys.stderr)
        return 1
    if cmd == "gauntlet":
        payload = gauntlet(Path(args.root), mode=str(args.mode))
        if args.out:
            out_path = Path(args.out).expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"[ctlab] gauntlet: {payload['ok']}/{payload['count']} ok")
        return 0 if payload["failed"] == 0 else 1

    print(f"[ctlab] unknown command: {cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
