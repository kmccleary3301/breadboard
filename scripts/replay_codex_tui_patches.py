#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.opencode_patch import PatchParseError, apply_update_hunks_codex, parse_opencode_patch


@dataclass(frozen=True)
class PatchRecord:
    timestamp: str
    session_id: str
    index: int
    patch_path: Path


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _safe_path(repo_root: Path, rel_path: str) -> Path:
    target = (repo_root / rel_path).resolve()
    if not _is_within(repo_root, target):
        raise ValueError(f"Refusing to write outside repo_root: {rel_path}")
    return target


def _iter_patch_records(manifest_path: Path, out_dir: Path) -> List[PatchRecord]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records: List[PatchRecord] = []
    for sess in manifest.get("sessions") or []:
        session_id = sess.get("session_id") or "unknown"
        for p in sess.get("patches") or []:
            patch_file = p.get("patch_file")
            if not patch_file:
                continue
            patch_path = out_dir / patch_file
            records.append(
                PatchRecord(
                    timestamp=str(p.get("timestamp") or ""),
                    session_id=session_id,
                    index=int(p.get("index") or 0),
                    patch_path=patch_path,
                )
            )
    records.sort(key=lambda r: (r.timestamp, r.session_id, r.index))
    return records


def _op_touches_prefix(op, prefix: str) -> bool:
    file_path = str(getattr(op, "file_path", "") or "")
    move_to = str(getattr(op, "move_to", "") or "")
    for path in (file_path, move_to):
        if path == prefix or path.startswith(f"{prefix}/"):
            return True
    return False


def _op_excluded(op, excluded_prefixes: List[str]) -> bool:
    if not excluded_prefixes:
        return False
    file_path = str(getattr(op, "file_path", "") or "")
    move_to = str(getattr(op, "move_to", "") or "")
    candidates = [file_path, move_to]
    for prefix in excluded_prefixes:
        if not prefix:
            continue
        for cand in candidates:
            if cand and cand.startswith(prefix):
                return True
    return False


def _contains_glob(path_str: str) -> bool:
    return any(ch in path_str for ch in ("*", "?", "["))


def _apply_operations(repo_root: Path, ops: Iterable, *, tui_prefix: str, dry_run: bool) -> int:
    raise RuntimeError("_apply_operations signature changed; use _apply_operations_with_stats")


def _apply_operations_with_stats(
    repo_root: Path,
    ops: Iterable,
    *,
    tui_prefix: str,
    dry_run: bool,
    lenient: bool,
    skip_add_existing: bool,
    errors: List[str],
    excluded_prefixes: List[str],
) -> Tuple[int, int]:
    applied = 0
    skipped = 0
    for op in ops:
        if tui_prefix and not _op_touches_prefix(op, tui_prefix):
            continue
        if _op_excluded(op, excluded_prefixes):
            skipped += 1
            continue

        kind = getattr(op, "kind", None)
        file_path = str(getattr(op, "file_path", "") or "").strip()
        move_to = str(getattr(op, "move_to", "") or "").strip() or None

        if not file_path:
            continue

        if kind == "add":
            dst = _safe_path(repo_root, file_path)
            if skip_add_existing and dst.exists():
                skipped += 1
                continue
            if dry_run:
                applied += 1
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(getattr(op, "content", "") or "", encoding="utf-8")
            applied += 1
            continue

        if kind == "delete":
            dst = _safe_path(repo_root, file_path)
            if dry_run:
                applied += 1
                continue
            if dst.exists() and dst.is_file():
                dst.unlink()
            applied += 1
            continue

        if kind in ("copy", "move"):
            if not move_to:
                raise PatchParseError(f"Missing move_to for {kind} operation: {file_path}")
            if dry_run:
                applied += 1
                continue

            # Support shell-glob moves like `mv path/* dest/` by expanding at replay time.
            if _contains_glob(file_path):
                dst_dir = _safe_path(repo_root, move_to)
                dst_dir.mkdir(parents=True, exist_ok=True)
                matches = sorted(repo_root.glob(file_path))
                if not matches:
                    if not lenient:
                        raise PatchParseError(f"No matches for glob {file_path} during {kind}")
                    skipped += 1
                    errors.append(f"[{kind}-skip] {file_path} -> {move_to}: glob had no matches")
                    continue
                for match in matches:
                    if not _is_within(repo_root, match):
                        continue
                    target = dst_dir / match.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if kind == "copy":
                        if match.is_dir():
                            shutil.copytree(match, target, dirs_exist_ok=True)
                        else:
                            shutil.copy2(match, target)
                    else:
                        shutil.move(str(match), str(target))
                applied += 1
                continue

            src = _safe_path(repo_root, file_path)
            dst = _safe_path(repo_root, move_to)
            if not src.exists():
                if not lenient:
                    raise PatchParseError(f"Source for {kind} does not exist: {file_path}")
                skipped += 1
                errors.append(f"[{kind}-skip] {file_path} -> {move_to}: source missing")
                continue
            if dst.exists() and dst.is_dir():
                dst = dst / src.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            if kind == "copy":
                if src.is_dir():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)
            else:
                shutil.move(str(src), str(dst))
            applied += 1
            continue

        if kind == "update":
            src = _safe_path(repo_root, file_path)
            original = ""
            if src.exists() and src.is_file():
                original = src.read_text(encoding="utf-8", errors="ignore")
            try:
                updated = apply_update_hunks_codex(
                    original,
                    getattr(op, "hunks", None) or [],
                    file_label=file_path,
                    allow_idempotent=True,
                )
            except PatchParseError as exc:
                if not lenient:
                    raise
                skipped += 1
                errors.append(f"[update-skip] {file_path}: {exc}")
                continue

            dst_rel = move_to or file_path
            dst = _safe_path(repo_root, dst_rel)
            if dry_run:
                applied += 1
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(updated, encoding="utf-8")
            if move_to and move_to != file_path and src.exists() and src.is_file():
                src.unlink()
            applied += 1
            continue

        raise PatchParseError(f"Unsupported operation kind: {kind}")
    return applied, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay extracted Codex CLI patches to reconstruct the TUI.")
    parser.add_argument(
        "--patch-out-dir",
        default="misc/codex_tui_patch_recovery",
        help="Directory produced by extract_codex_rollout_patches.py",
    )
    parser.add_argument(
        "--manifest",
        default="misc/codex_tui_patch_recovery/manifest.json",
        help="Path to the extraction manifest.json",
    )
    parser.add_argument(
        "--tui-prefix",
        default="tui_skeleton",
        help="Only apply operations touching this prefix",
    )
    parser.add_argument(
        "--exclude-prefix",
        action="append",
        default=[],
        help="Skip operations whose source/dest path starts with this prefix (repeatable).",
    )
    parser.add_argument(
        "--lenient",
        action="store_true",
        help="Skip update operations that fail to apply (useful when seeding from partial snapshots).",
    )
    parser.add_argument(
        "--skip-add-existing",
        action="store_true",
        help="When replaying add operations, do not overwrite files that already exist.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse and count operations without writing files")
    parser.add_argument(
        "--max-patches",
        type=int,
        default=0,
        help="Limit number of patch files to apply (0 means no limit)",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    out_dir = (repo_root / args.patch_out_dir).resolve()
    manifest_path = (repo_root / args.manifest).resolve()

    records = _iter_patch_records(manifest_path, out_dir)
    if args.max_patches and args.max_patches > 0:
        records = records[: args.max_patches]

    applied_ops = 0
    skipped_ops = 0
    errors: List[str] = []
    for rec in records:
        patch_text = rec.patch_path.read_text(encoding="utf-8")
        try:
            ops = parse_opencode_patch(patch_text)
        except PatchParseError as exc:
            if not args.lenient:
                raise
            skipped_ops += 1
            errors.append(f"[patch-parse-skip] {rec.patch_path}: {exc}")
            continue
        try:
            applied, skipped = _apply_operations_with_stats(
                repo_root,
                ops,
                tui_prefix=args.tui_prefix,
                dry_run=args.dry_run,
                lenient=bool(args.lenient),
                skip_add_existing=bool(args.skip_add_existing),
                errors=errors,
                excluded_prefixes=list(args.exclude_prefix or []),
            )
        except PatchParseError as exc:
            if not args.lenient:
                raise PatchParseError(f"{exc}\n[while applying] {rec.patch_path}") from exc
            skipped_ops += 1
            errors.append(f"[patch-apply-skip] {rec.patch_path}: {exc}")
            continue
        applied_ops += applied
        skipped_ops += skipped

    print(
        json.dumps(
            {
                "repo_root": str(repo_root),
                "patch_files": len(records),
                "applied_operations": applied_ops,
                "skipped_operations": skipped_ops,
                "errors_count": len(errors),
                "lenient": bool(args.lenient),
                "skip_add_existing": bool(args.skip_add_existing),
                "dry_run": bool(args.dry_run),
                "tui_prefix": args.tui_prefix,
            },
            indent=2,
            sort_keys=True,
        )
    )
    if errors:
        # Print a short tail so callers can triage without trawling megabytes of output.
        tail = errors[-10:]
        print("\nRecent replay warnings/errors (tail):")
        for line in tail:
            print(f"- {line}")


if __name__ == "__main__":
    main()

