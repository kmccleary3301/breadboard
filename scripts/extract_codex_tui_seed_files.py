#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.opencode_patch import PatchParseError, parse_opencode_patch


CD_CHAIN_RE = re.compile(r"(?:^|[;&\n]|&&)\s*cd\s+([^\n;&]+?)\s*&&")
CAT_HEREDOC_RE = re.compile(r"cat\s+<<['\"]?([A-Za-z0-9_]+)['\"]?\s*>\s*(.+)$")
CAT_SIMPLE_RE = re.compile(r"\bcat\s+(['\"]?)([^'\"\s|;&]+)\1")


def _safe_json_loads(line: str) -> Optional[dict]:
    try:
        return json.loads(line)
    except Exception:
        return None


def _is_abs_path(path_str: str) -> bool:
    return path_str.startswith("/") or bool(re.match(r"^[A-Za-z]:[\\\\/]", path_str))


def _normalize_rel(path_str: str) -> str:
    return os.path.normpath(path_str).replace("\\", "/")


def _infer_effective_cwd(session_cwd: Optional[str], tool_workdir: Optional[str], command_text: str) -> Optional[str]:
    if not session_cwd:
        return None
    base = Path(session_cwd).resolve()
    if tool_workdir:
        wd = Path(tool_workdir.strip())
        if wd.is_absolute():
            base = wd.resolve()
        else:
            base = (base / wd).resolve()

    matches = list(CD_CHAIN_RE.finditer(command_text or ""))
    if matches:
        raw = matches[-1].group(1).strip().strip("'").strip('"').strip()
        if raw:
            cd_path = Path(raw)
            base = cd_path.resolve() if cd_path.is_absolute() else (base / cd_path).resolve()
    return str(base)


def _resolve_to_repo_root(repo_root: Path, effective_cwd: Optional[str], path_str: str) -> Optional[str]:
    raw = (path_str or "").strip().strip("'").strip('"')
    if not raw:
        return None
    if _is_abs_path(raw):
        try:
            return Path(raw).resolve().relative_to(repo_root.resolve()).as_posix()
        except Exception:
            return None
    if not effective_cwd:
        return _normalize_rel(raw)
    try:
        abs_path = (Path(effective_cwd).resolve() / raw).resolve()
        return abs_path.relative_to(repo_root.resolve()).as_posix()
    except Exception:
        return None


def _iter_patch_files(out_dir: Path, manifest: dict) -> Iterable[Path]:
    for sess in manifest.get("sessions") or []:
        for p in sess.get("patches") or []:
            patch_file = p.get("patch_file")
            if patch_file:
                yield out_dir / patch_file


def _compute_updated_but_not_added(out_dir: Path, manifest: dict, tui_prefix: str) -> List[str]:
    added: Set[str] = set()
    updated: Set[str] = set()

    patch_paths = list(_iter_patch_files(out_dir, manifest))
    for patch_path in patch_paths:
        text = patch_path.read_text(encoding="utf-8")
        try:
            ops = parse_opencode_patch(text)
        except PatchParseError:
            continue
        for op in ops:
            for candidate in [getattr(op, "file_path", None), getattr(op, "move_to", None)]:
                if not candidate:
                    continue
                path_str = str(candidate)
                if tui_prefix and not (path_str == tui_prefix or path_str.startswith(f"{tui_prefix}/")):
                    continue
                if op.kind == "add" or (op.kind in ("copy", "move") and path_str == str(getattr(op, "move_to", "") or "")):
                    added.add(path_str)
                elif op.kind == "update":
                    updated.add(path_str)

    missing = sorted(p for p in updated if p not in added)
    return missing


def _extract_cat_heredoc(
    *,
    command_text: str,
    effective_cwd: Optional[str],
    repo_root: Path,
    targets: Set[str],
) -> Dict[str, str]:
    lines = command_text.splitlines()
    extracted: Dict[str, str] = {}
    for i, line in enumerate(lines):
        m = CAT_HEREDOC_RE.search(line)
        if not m:
            continue
        marker = m.group(1)
        file_part = m.group(2).strip()
        rel_path = _resolve_to_repo_root(repo_root, effective_cwd, file_part)
        if not rel_path or rel_path not in targets or rel_path in extracted:
            continue
        content_lines: List[str] = []
        j = i + 1
        while j < len(lines) and lines[j].strip() != marker:
            content_lines.append(lines[j])
            j += 1
        if j >= len(lines) or lines[j].strip() != marker:
            continue
        extracted[rel_path] = "\n".join(content_lines) + "\n"
    return extracted


def _extract_simple_cat_targets(
    *,
    command_text: str,
    effective_cwd: Optional[str],
    repo_root: Path,
    targets: Set[str],
) -> Optional[str]:
    if "<<" in command_text:
        return None
    m = CAT_SIMPLE_RE.search(command_text)
    if not m:
        return None
    file_part = m.group(2)
    rel_path = _resolve_to_repo_root(repo_root, effective_cwd, file_part)
    if not rel_path or rel_path not in targets:
        return None
    return rel_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract seed files for the TUI from Codex rollouts.")
    parser.add_argument(
        "--patch-out-dir",
        default="misc/codex_tui_patch_recovery_v2",
        help="Patch recovery directory (contains manifest.json and per-session patches)",
    )
    parser.add_argument(
        "--manifest",
        default="misc/codex_tui_patch_recovery_v2/manifest.json",
        help="Manifest generated by extract_codex_rollout_patches.py",
    )
    parser.add_argument(
        "--tui-prefix",
        default="kylecode_cli_skeleton",
        help="TUI path prefix to scope seed extraction",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write recovered seed files into the workspace (default: dry run)",
    )
    cli_args = parser.parse_args()

    out_dir = (REPO_ROOT / cli_args.patch_out_dir).resolve()
    manifest_path = (REPO_ROOT / cli_args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    targets = set(_compute_updated_but_not_added(out_dir, manifest, cli_args.tui_prefix))
    # Always seed these if present; they are known base files.
    targets.update(
        {
            f"{cli_args.tui_prefix}/package.json",
            f"{cli_args.tui_prefix}/tsconfig.json",
            f"{cli_args.tui_prefix}/src/main.ts",
        }
    )

    rollouts: List[Path] = []
    for sess in manifest.get("sessions") or []:
        rollout = sess.get("rollout")
        if rollout:
            rollouts.append(Path(rollout))

    recovered: Dict[str, str] = {}
    pending_cat: Dict[str, str] = {}
    session_cwd: Optional[str] = None

    for rollout_path in rollouts:
        if not rollout_path.exists():
            continue
        with rollout_path.open("r", encoding="utf-8") as f:
            for line in f:
                obj = _safe_json_loads(line)
                if not obj:
                    continue
                etype = obj.get("type")
                payload = obj.get("payload") or {}

                if etype == "session_meta":
                    session_cwd = payload.get("cwd") or session_cwd
                    continue

                if etype != "response_item":
                    continue

                ptype = payload.get("type")
                if ptype == "function_call":
                    args_raw = payload.get("arguments") or ""
                    try:
                        call_args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    except Exception:
                        call_args = {}
                    command = call_args.get("command")
                    command_text = ""
                    if isinstance(command, str):
                        command_text = command
                    elif isinstance(command, list):
                        command_text = "\n".join(str(x) for x in command)
                    if not command_text:
                        continue
                    effective_cwd = _infer_effective_cwd(
                        session_cwd,
                        str(call_args.get("workdir")) if isinstance(call_args, dict) and call_args.get("workdir") else None,
                        command_text,
                    )

                    for path, content in _extract_cat_heredoc(
                        command_text=command_text,
                        effective_cwd=effective_cwd,
                        repo_root=REPO_ROOT,
                        targets=targets,
                    ).items():
                        recovered.setdefault(path, content)

                    cat_target = _extract_simple_cat_targets(
                        command_text=command_text,
                        effective_cwd=effective_cwd,
                        repo_root=REPO_ROOT,
                        targets=targets,
                    )
                    if cat_target and cat_target not in recovered:
                        call_id = payload.get("call_id")
                        if call_id:
                            pending_cat[call_id] = cat_target
                    continue

                if ptype == "function_call_output":
                    call_id = payload.get("call_id")
                    if not call_id or call_id not in pending_cat:
                        continue
                    rel_path = pending_cat.pop(call_id)
                    if rel_path in recovered:
                        continue
                    output_raw = payload.get("output") or ""
                    try:
                        output_obj = json.loads(output_raw) if isinstance(output_raw, str) else output_raw
                        content = str(output_obj.get("output") or "")
                    except Exception:
                        continue
                    recovered[rel_path] = content
                    continue

        if len(recovered) >= len(targets):
            break

    missing = sorted(p for p in targets if p not in recovered)
    print(json.dumps({"targets": len(targets), "recovered": len(recovered), "missing": missing[:50]}, indent=2))

    if not cli_args.write:
        return

    for rel_path, content in recovered.items():
        if not (rel_path == cli_args.tui_prefix or rel_path.startswith(f"{cli_args.tui_prefix}/")):
            continue
        dst = (REPO_ROOT / rel_path).resolve()
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()

