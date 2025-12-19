#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
import shlex


PATCH_BLOCK_RE = re.compile(r"\*\*\* Begin Patch.*?\*\*\* End Patch", re.DOTALL)
APPLY_PATCH_CD_RE = re.compile(r"(?:^|[;&\n])\s*cd\s+([^\n;&]+?)\s*&&\s*apply_patch", re.DOTALL)
CD_CHAIN_RE = re.compile(r"(?:^|[;&\n]|&&)\s*cd\s+([^\n;&]+?)\s*&&")
CAT_HEREDOC_RE = re.compile(r"cat\s+<<['\"]?([A-Za-z0-9_]+)['\"]?\s*>\s*(.+)$")


@dataclass(frozen=True)
class PendingPatchEvent:
    timestamp: str
    source: str
    raw_patch: str
    rebased_patch: str
    touched_paths: List[str]

@dataclass(frozen=True)
class PendingWriteEvent:
    timestamp: str
    source: str
    abs_path: str
    repo_rel_path: Optional[str]
    content: str


@dataclass(frozen=True)
class PendingMoveEvent:
    timestamp: str
    source: str
    op: str  # "mv" or "cp"
    abs_src: str
    abs_dst: str

@dataclass(frozen=True)
class PatchEvent:
    session_id: str
    rollout_path: Path
    timestamp: str
    source: str
    raw_patch: str
    rebased_patch: str
    touched_paths: List[str]


def _iter_rollouts(sessions_dir: Path) -> Iterable[Path]:
    yield from sessions_dir.rglob("rollout-*.jsonl")


def _safe_json_loads(line: str) -> Optional[dict]:
    try:
        return json.loads(line)
    except Exception:
        return None


def _extract_patch_blocks(text: str) -> List[str]:
    return [m.group(0) for m in PATCH_BLOCK_RE.finditer(text)]


def _extract_patch_paths(patch_text: str) -> List[str]:
    paths: List[str] = []
    for line in patch_text.splitlines():
        if line.startswith("*** Add File: "):
            paths.append(line[len("*** Add File: ") :].strip())
        elif line.startswith("*** Update File: "):
            paths.append(line[len("*** Update File: ") :].strip())
        elif line.startswith("*** Delete File: "):
            paths.append(line[len("*** Delete File: ") :].strip())
        elif line.startswith("*** Move to: "):
            paths.append(line[len("*** Move to: ") :].strip())
    return paths


def _is_abs_path(path_str: str) -> bool:
    if path_str.startswith("/"):
        return True
    if re.match(r"^[A-Za-z]:[\\\\/]", path_str):
        return True
    return False


def _normalize_relpath(path_str: str) -> str:
    norm = os.path.normpath(path_str)
    return norm.replace("\\", "/")


def _rebase_header_path(
    *,
    path_str: str,
    repo_root: Path,
    cwd: Optional[str],
    tui_prefix: str,
) -> str:
    if _is_abs_path(path_str):
        try:
            rel = Path(path_str).resolve().relative_to(repo_root.resolve())
            return rel.as_posix()
        except Exception:
            return path_str

    if cwd:
        try:
            cwd_rel = Path(cwd).resolve().relative_to(repo_root.resolve()).as_posix()
        except Exception:
            cwd_rel = None
    else:
        cwd_rel = None

    cand1 = _normalize_relpath(path_str)
    if tui_prefix and (cand1 == tui_prefix or cand1.startswith(f"{tui_prefix}/")):
        return cand1

    if cwd_rel:
        cand2 = _normalize_relpath(f"{cwd_rel}/{path_str}")
        if tui_prefix:
            if cand2 == tui_prefix or cand2.startswith(f"{tui_prefix}/"):
                return cand2
        else:
            # When no prefix filter is provided, heuristically rebase bare filenames
            # from the tool cwd into the repo tree. This matches how Codex often
            # applies patches from within subdirectories (e.g. updating package.json).
            if "/" not in cand1 and not cand1.startswith(f"{cwd_rel}/"):
                return cand2

    return cand1


def _rebase_patch(
    *,
    patch_text: str,
    repo_root: Path,
    cwd: Optional[str],
    tui_prefix: str,
) -> Tuple[str, List[str]]:
    rebased_lines: List[str] = []
    touched: List[str] = []

    for line in patch_text.splitlines():
        for hdr in ("*** Add File: ", "*** Update File: ", "*** Delete File: ", "*** Move to: "):
            if line.startswith(hdr):
                raw_path = line[len(hdr) :].strip()
                new_path = _rebase_header_path(
                    path_str=raw_path, repo_root=repo_root, cwd=cwd, tui_prefix=tui_prefix
                )
                touched.append(new_path)
                line = f"{hdr}{new_path}"
                break
        rebased_lines.append(line)

    return "\n".join(rebased_lines) + "\n", touched


def _infer_patch_cwd_from_shell(
    *,
    session_cwd: Optional[str],
    tool_workdir: Optional[str],
    command_text: str,
) -> Optional[str]:
    if not session_cwd:
        return None
    base = Path(session_cwd).resolve()
    workdir = (tool_workdir or "").strip()
    if workdir:
        wd = Path(workdir)
        base = wd.resolve() if wd.is_absolute() else (base / wd).resolve()

    matches = list(APPLY_PATCH_CD_RE.finditer(command_text or ""))
    if matches:
        raw = matches[-1].group(1).strip()
        raw = raw.strip("'").strip('"').strip()
        if raw:
            cd_path = Path(raw)
            return str(cd_path.resolve()) if cd_path.is_absolute() else str((base / cd_path).resolve())

    return str(base)


def _infer_shell_cwd_from_shell(
    *,
    session_cwd: Optional[str],
    tool_workdir: Optional[str],
    command_text: str,
) -> Optional[str]:
    """Infer the effective cwd for a shell tool call by applying workdir + chained `cd ... &&` segments."""
    if not session_cwd:
        return None
    base = Path(session_cwd).resolve()
    workdir = (tool_workdir or "").strip()
    if workdir:
        wd = Path(workdir)
        base = wd.resolve() if wd.is_absolute() else (base / wd).resolve()

    matches = list(CD_CHAIN_RE.finditer(command_text or ""))
    if matches:
        raw = matches[-1].group(1).strip()
        raw = raw.strip("'").strip('"').strip()
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
        return _normalize_relpath(raw)
    try:
        abs_path = (Path(effective_cwd).resolve() / raw).resolve()
        return abs_path.relative_to(repo_root.resolve()).as_posix()
    except Exception:
        return None


def _resolve_to_abs_path(effective_cwd: Optional[str], path_str: str) -> Optional[Path]:
    raw = (path_str or "").strip().strip("'").strip('"')
    if not raw:
        return None
    try:
        if _is_abs_path(raw):
            return Path(raw).resolve()
        if not effective_cwd:
            return None
        return (Path(effective_cwd).resolve() / raw).resolve()
    except Exception:
        return None


def _build_add_patch(path_str: str, content: str) -> str:
    lines = ["*** Begin Patch", f"*** Add File: {path_str}"]
    for line in (content or "").splitlines():
        lines.append(f"+{line}")
    lines.append("*** End Patch")
    return "\n".join(lines) + "\n"

def _build_mv_cp_patch(op: str, src_path: str, dst_path: str) -> str:
    if op not in ("mv", "cp"):
        raise ValueError(f"Unsupported op for mv/cp patch: {op}")
    verb = "Move" if op == "mv" else "Copy"
    lines = [
        "*** Begin Patch",
        f"*** {verb} File: {src_path}",
        f"*** To File: {dst_path}",
        "*** End Patch",
    ]
    return "\n".join(lines) + "\n"


def _extract_cat_heredoc_writes(
    *,
    command_text: str,
    effective_cwd: Optional[str],
    repo_root: Path,
) -> List[Tuple[str, Optional[str], str]]:
    writes: List[Tuple[str, Optional[str], str]] = []
    lines = command_text.splitlines()
    for i, line in enumerate(lines):
        m = CAT_HEREDOC_RE.search(line)
        if not m:
            continue
        marker = m.group(1)
        file_part = m.group(2).strip()
        abs_path = _resolve_to_abs_path(effective_cwd, file_part)
        if not abs_path:
            continue
        rel_path: Optional[str]
        try:
            rel_path = abs_path.relative_to(repo_root.resolve()).as_posix()
        except Exception:
            rel_path = None
        content_lines: List[str] = []
        j = i + 1
        while j < len(lines) and lines[j].strip() != marker:
            content_lines.append(lines[j])
            j += 1
        if j >= len(lines) or lines[j].strip() != marker:
            continue
        content = "\n".join(content_lines) + "\n"
        writes.append((str(abs_path), rel_path, content))
    return writes


def _extract_mv_cp_ops(command_text: str) -> List[Tuple[str, str, str]]:
    ops: List[Tuple[str, str, str]] = []
    for line in command_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            tokens = shlex.split(stripped)
        except Exception:
            continue
        for idx, tok in enumerate(tokens):
            if tok not in ("mv", "cp"):
                continue
            j = idx + 1
            while j < len(tokens) and tokens[j].startswith("-"):
                j += 1
            if j + 1 >= len(tokens):
                continue
            src = tokens[j]
            dst = tokens[j + 1]
            ops.append((tok, src, dst))
    return ops


def _load_rollout_events(rollout_path: Path, *, tui_prefix: str) -> List[PatchEvent]:
    repo_root = Path(__file__).resolve().parents[1]

    session_id: Optional[str] = None
    session_cwd: Optional[str] = None
    patch_events: List[PatchEvent] = []
    pending_shell_events: Dict[str, List[PendingPatchEvent]] = {}
    pending_shell_writes: Dict[str, List[PendingWriteEvent]] = {}
    pending_shell_moves: Dict[str, List[PendingMoveEvent]] = {}
    pending_custom_patches: Dict[str, PendingPatchEvent] = {}
    virtual_files: Dict[str, str] = {}

    with rollout_path.open("r", encoding="utf-8") as f:
        for line in f:
            obj = _safe_json_loads(line)
            if not obj:
                continue

            timestamp = str(obj.get("timestamp") or "")
            event_type = obj.get("type")
            payload = obj.get("payload") or {}

            if event_type == "session_meta":
                meta = payload or {}
                session_id = meta.get("id") or session_id
                session_cwd = meta.get("cwd") or session_cwd
                continue

            if event_type != "response_item":
                continue

            ptype = payload.get("type")
            if ptype == "custom_tool_call" and payload.get("name") == "apply_patch":
                if payload.get("status") not in (None, "completed"):
                    continue
                call_id = payload.get("call_id") or ""
                raw_patch = payload.get("input") or ""
                if "*** Begin Patch" not in raw_patch:
                    continue
                rebased_patch, touched = _rebase_patch(
                    patch_text=raw_patch, repo_root=repo_root, cwd=session_cwd, tui_prefix=tui_prefix
                )
                pending = PendingPatchEvent(
                    timestamp=timestamp,
                    source="custom_tool_call.apply_patch",
                    raw_patch=raw_patch.rstrip() + "\n",
                    rebased_patch=rebased_patch,
                    touched_paths=touched,
                )
                if call_id:
                    pending_custom_patches[call_id] = pending
                else:
                    # If we can't correlate to an output, assume success.
                    patch_events.append(
                        PatchEvent(
                            session_id=session_id or "unknown",
                            rollout_path=rollout_path,
                            timestamp=pending.timestamp,
                            source=pending.source,
                            raw_patch=pending.raw_patch,
                            rebased_patch=pending.rebased_patch,
                            touched_paths=pending.touched_paths,
                        )
                    )
                continue

            if ptype == "custom_tool_call_output":
                call_id = payload.get("call_id") or ""
                pending = pending_custom_patches.pop(call_id, None) if call_id else None
                if not pending:
                    continue
                output_raw = payload.get("output") or ""
                output_text = str(output_raw)
                exit_code: Optional[int] = None
                if isinstance(output_raw, str):
                    try:
                        parsed = json.loads(output_raw)
                    except Exception:
                        parsed = None
                    if isinstance(parsed, dict):
                        output_text = str(parsed.get("output") or "")
                        meta = parsed.get("metadata") or {}
                        if isinstance(meta, dict):
                            code = meta.get("exit_code")
                            if isinstance(code, int):
                                exit_code = code
                            elif isinstance(code, str) and code.isdigit():
                                exit_code = int(code)

                lowered = output_text.lower()
                failed_markers = ("verification failed", "failed to find expected lines", "traceback", "error:")
                if exit_code is not None:
                    succeeded = bool(exit_code == 0) and not any(marker in lowered for marker in failed_markers)
                else:
                    # Some tool outputs may be plain text without metadata.
                    succeeded = ("success" in lowered) and not any(marker in lowered for marker in failed_markers)
                if not succeeded:
                    continue
                patch_events.append(
                    PatchEvent(
                        session_id=session_id or "unknown",
                        rollout_path=rollout_path,
                        timestamp=pending.timestamp,
                        source=pending.source,
                        raw_patch=pending.raw_patch,
                        rebased_patch=pending.rebased_patch,
                        touched_paths=pending.touched_paths,
                    )
                )
                continue

            if ptype == "function_call":
                name = payload.get("name") or ""
                call_id = payload.get("call_id") or ""
                args_raw = payload.get("arguments") or ""
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                except Exception:
                    args = {}

                tool_workdir = args.get("workdir") if isinstance(args, dict) else None
                command = args.get("command")
                command_text = ""
                if isinstance(command, str):
                    command_text = command
                elif isinstance(command, list):
                    command_text = "\n".join(str(x) for x in command)

                if not command_text:
                    continue

                if not call_id:
                    continue

                tool_workdir_str = str(tool_workdir) if tool_workdir is not None else None
                shell_cwd = _infer_shell_cwd_from_shell(
                    session_cwd=session_cwd,
                    tool_workdir=tool_workdir_str,
                    command_text=command_text,
                )

                # Track apply_patch blocks (filtered on tool output success).
                if "apply_patch" in command_text:
                    patch_cwd = _infer_patch_cwd_from_shell(
                        session_cwd=session_cwd,
                        tool_workdir=tool_workdir_str,
                        command_text=command_text,
                    )
                    for raw_patch in _extract_patch_blocks(command_text):
                        rebased_patch, touched = _rebase_patch(
                            patch_text=raw_patch,
                            repo_root=repo_root,
                            cwd=patch_cwd,
                            tui_prefix=tui_prefix,
                        )
                        pending_shell_events.setdefault(call_id, []).append(
                            PendingPatchEvent(
                                timestamp=timestamp,
                                source=f"function_call.{name}",
                                raw_patch=raw_patch.rstrip() + "\n",
                                rebased_patch=rebased_patch,
                                touched_paths=touched,
                            )
                        )

                # Track `cat <<EOF > file` writes (e.g. wholesale rewrites not expressed as apply_patch blocks).
                for abs_path, rel_path, content in _extract_cat_heredoc_writes(
                    command_text=command_text,
                    effective_cwd=shell_cwd,
                    repo_root=repo_root,
                ):
                    pending_shell_writes.setdefault(call_id, []).append(
                        PendingWriteEvent(
                            timestamp=timestamp,
                            source=f"function_call.{name}.cat_heredoc",
                            abs_path=abs_path,
                            repo_rel_path=rel_path,
                            content=content,
                        )
                    )
                    if rel_path and (not tui_prefix or rel_path == tui_prefix or rel_path.startswith(f"{tui_prefix}/")):
                        patch_text = _build_add_patch(rel_path, content)
                        pending_shell_events.setdefault(call_id, []).append(
                            PendingPatchEvent(
                                timestamp=timestamp,
                                source=f"function_call.{name}.cat_heredoc",
                                raw_patch=patch_text,
                                rebased_patch=patch_text,
                                touched_paths=[rel_path],
                            )
                        )

                # Track simple mv/cp operations into the TUI tree (used after writing temp files).
                for op, src, dst in _extract_mv_cp_ops(command_text):
                    abs_src = _resolve_to_abs_path(shell_cwd, src)
                    abs_dst = _resolve_to_abs_path(shell_cwd, dst)
                    if not abs_src or not abs_dst:
                        continue
                    pending_shell_moves.setdefault(call_id, []).append(
                        PendingMoveEvent(
                            timestamp=timestamp,
                            source=f"function_call.{name}.{op}",
                            op=op,
                            abs_src=str(abs_src),
                            abs_dst=str(abs_dst),
                        )
                    )
                continue

            if ptype == "function_call_output":
                call_id = payload.get("call_id") or ""
                has_pending = call_id and (
                    call_id in pending_shell_events
                    or call_id in pending_shell_writes
                    or call_id in pending_shell_moves
                )
                if not has_pending:
                    continue
                output_raw = payload.get("output") or ""
                exit_code: Optional[int] = None
                output_text = str(output_raw)
                if isinstance(output_raw, str):
                    try:
                        parsed = json.loads(output_raw)
                    except Exception:
                        parsed = None
                    if isinstance(parsed, dict):
                        output_text = str(parsed.get("output") or "")
                        meta = parsed.get("metadata") or {}
                        if isinstance(meta, dict):
                            code = meta.get("exit_code")
                            if isinstance(code, int):
                                exit_code = code
                            elif isinstance(code, str) and code.isdigit():
                                exit_code = int(code)

                failed_markers = ("verification failed", "failed to find expected lines", "traceback", "error:")
                lowered = output_text.lower()
                succeeded = bool(exit_code == 0) and not any(marker in lowered for marker in failed_markers)

                if not succeeded:
                    pending_shell_events.pop(call_id, None)
                    pending_shell_writes.pop(call_id, None)
                    pending_shell_moves.pop(call_id, None)
                    continue

                for pending_write in pending_shell_writes.pop(call_id, []):
                    virtual_files[pending_write.abs_path] = pending_write.content

                for pending_move in pending_shell_moves.pop(call_id, []):
                    rel_src: Optional[str]
                    rel_dst: Optional[str]
                    try:
                        rel_src = Path(pending_move.abs_src).resolve().relative_to(repo_root.resolve()).as_posix()
                    except Exception:
                        rel_src = None
                    try:
                        rel_dst = Path(pending_move.abs_dst).resolve().relative_to(repo_root.resolve()).as_posix()
                    except Exception:
                        rel_dst = None

                    # If this is a repo-to-repo move/copy, record it explicitly so the replay can reproduce it
                    # even when we don't know file contents at extraction time.
                    if rel_src and rel_dst:
                        if (
                            not tui_prefix
                            or rel_src == tui_prefix
                            or rel_src.startswith(f"{tui_prefix}/")
                            or rel_dst == tui_prefix
                            or rel_dst.startswith(f"{tui_prefix}/")
                        ):
                            patch_text = _build_mv_cp_patch(pending_move.op, rel_src, rel_dst)
                            patch_events.append(
                                PatchEvent(
                                    session_id=session_id or "unknown",
                                    rollout_path=rollout_path,
                                    timestamp=pending_move.timestamp,
                                    source=pending_move.source,
                                    raw_patch=patch_text,
                                    rebased_patch=patch_text,
                                    touched_paths=[rel_src, rel_dst],
                                )
                            )
                        continue

                    # Otherwise, best-effort reconstruction when the src is outside the repo (e.g., /tmp)
                    # but we observed its content via heredoc.
                    content = virtual_files.get(pending_move.abs_src)
                    if content is None:
                        continue
                    virtual_files[pending_move.abs_dst] = content
                    if pending_move.op == "mv":
                        virtual_files.pop(pending_move.abs_src, None)

                    if not rel_dst or (tui_prefix and not (rel_dst == tui_prefix or rel_dst.startswith(f"{tui_prefix}/"))):
                        continue
                    patch_text = _build_add_patch(rel_dst, content)
                    patch_events.append(
                        PatchEvent(
                            session_id=session_id or "unknown",
                            rollout_path=rollout_path,
                            timestamp=pending_move.timestamp,
                            source=pending_move.source,
                            raw_patch=patch_text,
                            rebased_patch=patch_text,
                            touched_paths=[rel_dst],
                        )
                    )

                for pending in pending_shell_events.pop(call_id, []):
                    rebased_patch = pending.rebased_patch
                    touched = pending.touched_paths
                    patch_events.append(
                        PatchEvent(
                            session_id=session_id or "unknown",
                            rollout_path=rollout_path,
                            timestamp=pending.timestamp,
                            source=pending.source,
                            raw_patch=pending.raw_patch.rstrip() + "\n",
                            rebased_patch=rebased_patch,
                            touched_paths=touched,
                        )
                    )
                continue

    return patch_events


def _summarize_prompt(rollout_path: Path, max_chars: int = 240) -> str:
    with rollout_path.open("r", encoding="utf-8") as f:
        for line in f:
            obj = _safe_json_loads(line)
            if not obj:
                continue
            if obj.get("type") != "response_item":
                continue
            payload = obj.get("payload") or {}
            if payload.get("type") != "message" or payload.get("role") != "user":
                continue
            content = payload.get("content") or []
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and part.get("type") in ("input_text", "text"):
                    text = str(part.get("text") or "").strip()
                    if text:
                        text = " ".join(text.split())
                        return text[:max_chars]
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract apply_patch blocks from Codex CLI rollouts.")
    parser.add_argument(
        "--sessions-dir",
        default=str(Path.home() / ".codex" / "sessions"),
        help="Path to Codex CLI sessions directory (default: ~/.codex/sessions)",
    )
    parser.add_argument(
        "--out-dir",
        default="misc/codex_tui_patch_recovery",
        help="Output directory under the repo root",
    )
    parser.add_argument(
        "--tui-prefix",
        default="kylecode_cli_skeleton",
        help="Path prefix for the TUI project",
    )
    parser.add_argument(
        "--only-tui",
        action="store_true",
        help="Only write patches that touch the TUI prefix",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    sessions_dir = Path(os.path.expanduser(args.sessions_dir)).resolve()
    out_dir = (repo_root / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    all_events: List[PatchEvent] = []
    for rollout in sorted(_iter_rollouts(sessions_dir)):
        events = _load_rollout_events(rollout, tui_prefix=args.tui_prefix)
        if not events:
            continue
        all_events.extend(events)

    # Filter and group by session.
    grouped: Dict[str, List[PatchEvent]] = {}
    for ev in all_events:
        touches_tui = any(
            p == args.tui_prefix or p.startswith(f"{args.tui_prefix}/") for p in (ev.touched_paths or [])
        )
        if args.only_tui and not touches_tui:
            continue
        grouped.setdefault(ev.session_id, []).append(ev)

    manifest = {
        "repo_root": str(repo_root),
        "sessions_dir": str(sessions_dir),
        "tui_prefix": args.tui_prefix,
        "sessions": [],
    }

    for session_id, events in sorted(grouped.items()):
        events_sorted = sorted(events, key=lambda e: e.timestamp)
        session_dir = out_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        patches_meta = []
        for idx, ev in enumerate(events_sorted, 1):
            patch_path = session_dir / f"{idx:04d}_{ev.timestamp.replace(':', '').replace('.', '')}.patch"
            patch_path.write_text(ev.rebased_patch, encoding="utf-8")
            patches_meta.append(
                {
                    "index": idx,
                    "timestamp": ev.timestamp,
                    "source": ev.source,
                    "rollout": str(ev.rollout_path),
                    "patch_file": str(patch_path.relative_to(out_dir)),
                    "touched_paths": ev.touched_paths,
                }
            )

        prompt_preview = _summarize_prompt(events_sorted[0].rollout_path)
        manifest["sessions"].append(
            {
                "session_id": session_id,
                "rollout": str(events_sorted[0].rollout_path),
                "first_timestamp": events_sorted[0].timestamp,
                "last_timestamp": events_sorted[-1].timestamp,
                "patch_count": len(events_sorted),
                "prompt_preview": prompt_preview,
                "patches": patches_meta,
            }
        )

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[extract_codex_rollout_patches] wrote {len(manifest['sessions'])} sessions to {out_dir}")


if __name__ == "__main__":
    main()

