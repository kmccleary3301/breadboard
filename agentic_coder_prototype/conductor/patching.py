from __future__ import annotations

import re
import shlex
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from breadboard.opencode_patch import (
    PatchParseError,
    apply_update_hunks_codex,
    parse_opencode_patch,
    to_unified_diff,
)


def count_diff_hunks(text: Optional[str]) -> int:
    if not text:
        return 0
    pattern = re.compile(r"^@@", re.MULTILINE)
    matches = pattern.findall(text)
    if text.lstrip().startswith("@@") and (not matches or text[0] != "\n"):
        pass
    return len(matches)


def split_patch_blocks(patch_text: str) -> List[str]:
    if not patch_text:
        return []
    lines = patch_text.splitlines()
    blocks: List[str] = []
    current: List[str] = []
    inside_sentinel = False

    for line in lines:
        if line.startswith("*** Begin Patch"):
            if current:
                blocks.append("\n".join(current).strip() + "\n")
                current = []
            inside_sentinel = True
        if inside_sentinel or line.startswith("*** Begin Patch"):
            current.append(line)
        if line.startswith("*** End Patch"):
            inside_sentinel = False
            blocks.append("\n".join(current).strip() + "\n")
            current = []

    if current:
        blocks.append("\n".join(current).strip() + "\n")

    if blocks:
        return [block for block in blocks if block.strip()]

    segments: List[str] = []
    current = []
    for line in lines:
        if line.startswith("diff --git "):
            if current:
                segments.append("\n".join(current).strip() + "\n")
                current = []
        current.append(line)
    if current:
        segments.append("\n".join(current).strip() + "\n")
    return [seg for seg in segments if seg.strip()]


def normalize_patch_block(block_text: str) -> str:
    content = (block_text or "").strip()
    if not content:
        return ""
    content = re.sub(
        r"(\*\*\* (?:Add|Update|Delete) File:[^\n]*?)\s*\*+\s*$",
        r"\1",
        content,
        flags=re.MULTILINE,
    )
    if "*** Begin Patch" in content:
        normalized = re.sub(r"\*\*\* Begin Patch[^\n]*", "*** Begin Patch", content, count=1)
        normalized = re.sub(r"\*\*\* End Patch[^\n]*", "*** End Patch", normalized, count=1)
        return normalized if normalized.endswith("\n") else normalized + "\n"
    return f"*** Begin Patch\n{content}\n*** End Patch\n"


def normalize_workspace_path(conductor: Any, path_in: str) -> str:
    ws_path = Path(conductor.workspace).resolve()
    if not path_in:
        return str(ws_path)

    raw = str(path_in).strip()
    if not raw:
        return str(ws_path)

    try:
        abs_candidate = Path(raw)
        if abs_candidate.is_absolute():
            try:
                rel = abs_candidate.resolve(strict=False).relative_to(ws_path)
                return str((ws_path / rel).resolve(strict=False))
            except Exception:
                return str(ws_path)
    except Exception:
        pass

    normalized = raw.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized in ("", "."):
        return str(ws_path)

    ws_name = ws_path.name
    segments = [seg for seg in normalized.split("/") if seg and seg != "."]

    while segments and segments[0] == ws_name:
        segments.pop(0)

    ws_str_norm = str(ws_path).replace("\\", "/")
    if ws_str_norm in normalized:
        tail = normalized.split(ws_str_norm, 1)[1].lstrip("/\\")
        segments = [seg for seg in tail.split("/") if seg and seg != "."]
    else:
        ws_str_norm_nolead = ws_str_norm.lstrip("/")
        if ws_str_norm_nolead and normalized.startswith(ws_str_norm_nolead):
            tail = normalized[len(ws_str_norm_nolead) :].lstrip("/\\")
            segments = [seg for seg in tail.split("/") if seg and seg != "."]

    cleaned: List[str] = []
    for seg in segments:
        if seg == "..":
            if cleaned:
                cleaned.pop()
            continue
        cleaned.append(seg)

    candidate = ws_path.joinpath(*cleaned) if cleaned else ws_path
    candidate = candidate.resolve(strict=False)
    try:
        candidate.relative_to(ws_path)
    except ValueError:
        return str(ws_path)
    return str(candidate)


def fetch_workspace_text(conductor: Any, path: str) -> str:
    normalized = normalize_workspace_path(conductor, path)
    try:
        result = conductor._ray_get(conductor.sandbox.read_text.remote(normalized))
    except Exception:
        return ""
    if isinstance(result, dict):
        return str(result.get("content") or "")
    if isinstance(result, str):
        return result
    return ""


def convert_patch_to_unified(conductor: Any, patch_text: str) -> Optional[str]:
    normalized = normalize_patch_block(patch_text)
    if not normalized:
        return None

    def _fetch_current(path: str) -> str:
        return fetch_workspace_text(conductor, path)

    try:
        return to_unified_diff(normalized, _fetch_current)
    except PatchParseError:
        return None


def apply_patch_operations_direct(conductor: Any, patch_text: str) -> Optional[Dict[str, Any]]:
    normalized = normalize_patch_block(patch_text)
    if not normalized:
        return None
    applied_paths: List[str] = []

    def _write_file(rel_path: str, content: str) -> bool:
        if not rel_path:
            return False
        abs_path = normalize_workspace_path(conductor, rel_path)
        try:
            conductor._ray_get(conductor.sandbox.write_text.remote(abs_path, content))
            applied_paths.append(rel_path)
            return True
        except Exception:
            return False

    def _delete_file(rel_path: str) -> bool:
        if not rel_path:
            return False
        abs_path = normalize_workspace_path(conductor, rel_path)
        try:
            cmd = f"rm -f {shlex.quote(abs_path)}"
            conductor._ray_get(conductor.sandbox.run_shell.remote(cmd, 30))
            return True
        except Exception:
            return False

    def _render_hunks_fallback(hunks: List[Any]) -> str:
        lines: List[str] = []
        for hunk in hunks:
            for change in getattr(hunk, "changes", []) or []:
                if getattr(change, "kind", None) in {"keep", "add"}:
                    lines.append(getattr(change, "content", ""))
        if not lines:
            return ""
        return "\n".join(lines) + "\n"

    try:
        operations = parse_opencode_patch(normalized)
    except PatchParseError:
        operations = []
    if operations:
        supported = {"add", "update", "delete"}
        if any(getattr(op, "kind", None) not in supported for op in operations):
            operations = []
        else:
            for op in operations:
                rel_path = str(getattr(op, "file_path", "")).strip()
                if not rel_path:
                    return None
                if op.kind == "add":
                    content = getattr(op, "content", "") or ""
                    if not _write_file(rel_path, content):
                        return None
                    continue
                if op.kind == "delete":
                    if not _delete_file(rel_path):
                        return None
                    continue
                if op.kind == "update":
                    original = fetch_workspace_text(conductor, rel_path)
                    hunks = getattr(op, "hunks", None) or []
                    try:
                        updated = apply_update_hunks_codex(original, hunks, file_label=rel_path)
                    except PatchParseError:
                        updated = _render_hunks_fallback(hunks)
                        if not updated:
                            return None
                    target = str(getattr(op, "move_to", "") or rel_path).strip()
                    if not target:
                        return None
                    if not _write_file(target, updated):
                        return None
                    if target != rel_path:
                        if not _delete_file(rel_path):
                            return None
                    continue

    if not operations:
        diff_adds: List[Tuple[str, str]] = []
        current: Optional[Dict[str, Any]] = None
        for line in patch_text.splitlines():
            if line.startswith("diff --git "):
                if (
                    current
                    and current.get("is_new")
                    and current.get("path")
                    and current.get("lines")
                ):
                    diff_adds.append(
                        (
                            current["path"],
                            "\n".join(current["lines"]).rstrip("\n"),
                        )
                    )
                current = {"path": None, "is_new": False, "collect": False, "lines": []}
            if current is None:
                continue
            if line.startswith("--- "):
                if line.strip() == "--- /dev/null":
                    current["is_new"] = True
            elif line.startswith("+++ "):
                path = line[4:].strip()
                if path.startswith(("a/", "b/")):
                    path = path[2:]
                current["path"] = path
            elif line.startswith("@@"):
                current["collect"] = True
            elif current["collect"]:
                if line.startswith("diff --git "):
                    continue
                if line.startswith("+") and not line.startswith("+++"):
                    current["lines"].append(line[1:])
        if (
            current
            and current.get("is_new")
            and current.get("path")
            and current.get("lines")
        ):
            diff_adds.append(
                (
                    current["path"],
                    "\n".join(current["lines"]).rstrip("\n"),
                )
            )
        if diff_adds:
            for rel_path, content in diff_adds:
                if not _write_file(rel_path, content):
                    return None

    if not applied_paths:
        return None
    try:
        conductor.vcs({"action": "add", "params": {"paths": applied_paths}})
    except Exception:
        pass
    return {
        "ok": True,
        "action": "apply_patch",
        "exit": 0,
        "stdout": "",
        "stderr": "",
        "data": {"manual_fallback": True, "paths": applied_paths},
    }


def expand_multi_file_patches(
    conductor: Any,
    parsed_calls: List[Any],
    session_state: Any,
    markdown_logger: Any,
) -> List[Any]:
    if not parsed_calls:
        return parsed_calls

    expanded: List[Any] = []
    policy = getattr(conductor, "patch_splitting_policy", {}) or {}
    policy_mode = policy.get("mode", "auto_split")
    max_files = policy.get("max_files", 1)
    validation_message = policy.get("message")
    violation_strategy = str(policy.get("on_violation", "reject") or "").lower()
    violation_logged = False

    additional_calls = 0

    for call in parsed_calls:
        function_name = getattr(call, "function", None)
        if function_name not in {"apply_unified_patch", "patch"}:
            expanded.append(call)
            continue

        args = getattr(call, "arguments", {}) or {}
        patch_text = str(args.get("patch") or args.get("patchText") or "")
        chunks = split_patch_blocks(patch_text)
        if len(chunks) <= 1:
            expanded.append(call)
            continue

        chunk_limit = max_files if isinstance(max_files, int) and max_files > 0 else 1
        exceeds_limit = len(chunks) > chunk_limit

        if policy_mode == "reject_multi_file" and exceeds_limit:
            allow_split = violation_strategy in {"auto_split", "warn_split", "warn_and_split"}
            emit_patch_policy_violation(
                conductor,
                session_state,
                markdown_logger,
                chunk_count=len(chunks),
                policy_mode=policy_mode,
                validation_message=validation_message,
            )
            violation_logged = True
            if not allow_split:
                continue

        additional_calls += len(chunks) - 1
        for chunk in chunks:
            chunk_text = chunk if chunk.endswith("\n") else chunk + "\n"
            new_args = dict(args)
            if function_name == "patch" or "patchText" in new_args:
                new_args.pop("patch", None)
                new_args["patchText"] = chunk_text
            else:
                new_args.pop("patchText", None)
                new_args["patch"] = chunk_text
            call_payload = vars(call).copy()
            call_payload["arguments"] = new_args
            expanded.append(SimpleNamespace(**call_payload))

    if violation_logged:
        try:
            session_state.set_provider_metadata("patch_policy_violation", True)
        except Exception:
            pass
    if additional_calls > 0:
        try:
            session_state.add_transcript_entry({
                "patch_auto_split": {
                    "original_calls": len(parsed_calls),
                    "expanded_calls": len(expanded),
                    "additional_calls": additional_calls,
                }
            })
            session_state.set_provider_metadata("auto_split_patch", True)
        except Exception:
            pass
        label = getattr(parsed_calls[0], "function", None) or "apply_unified_patch"
        note = (
            f"[tooling] Auto-split multi-file patch into {len(expanded)} {label} calls "
            f"(added {additional_calls} extra operations)."
        )
        try:
            markdown_logger.log_system_message(note)
        except Exception:
            pass

    return expanded


def emit_patch_policy_violation(
    conductor: Any,
    session_state: Any,
    markdown_logger: Any,
    *,
    chunk_count: int,
    policy_mode: str,
    validation_message: Optional[str],
) -> None:
    message = validation_message or (
        "<VALIDATION_ERROR>\n"
        "Multi-file patches are disabled in this configuration. Submit one patch (single file) per call.\n"
        "</VALIDATION_ERROR>"
    )
    try:
        session_state.add_message({"role": "user", "content": message}, to_provider=True)
    except Exception:
        pass
    try:
        markdown_logger.log_user_message(message)
    except Exception:
        pass
    try:
        session_state.increment_guardrail_counter("patch_policy_violations")
    except Exception:
        pass
    try:
        session_state.add_transcript_entry({
            "patch_policy_violation": {
                "chunks": chunk_count,
                "policy": policy_mode,
            }
        })
    except Exception:
        pass


def validate_structural_artifacts(conductor: Any, session_state: Any) -> List[str]:
    if not session_state.get_provider_metadata("requires_build_guard", False):
        return []

    issues: List[str] = []
    workspace_root = Path(conductor.workspace)

    def _read_text(path: Path) -> Optional[str]:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return None

    candidates = [
        ("filesystem.h", "filesystem.c", "test_filesystem.c"),
        ("fs.h", "fs.c", "test.c"),
        ("protofs.h", "protofs.c", "test_protofs.c"),
        ("protofilesystem.h", "fs.c", "test.c"),
        ("protofilesystem.h", "protofilesystem.c", "test_filesystem.c"),
    ]

    def _candidate_score(candidate: tuple[str, str, str]) -> int:
        return sum(1 for name in candidate if (workspace_root / name).exists())

    best = max(candidates, key=_candidate_score)
    if _candidate_score(best) == 0:
        best = ("fs.h", "fs.c", "test.c")

    header_name, source_name, test_name = best

    def _first_existing(candidates: List[str]) -> Path:
        for rel in candidates:
            path = workspace_root / rel
            if path.exists():
                return path
        return workspace_root / candidates[0]

    filesystem_h = _first_existing([header_name, f"src/{header_name}", f"include/{header_name}"])
    filesystem_c = _first_existing([source_name, f"src/{source_name}"])
    test_c = _first_existing([test_name, f"tests/{test_name}", f"test/{test_name}"])

    header_text = _read_text(filesystem_h)
    if header_text is None:
        issues.append(f"Missing required header file {filesystem_h.relative_to(workspace_root)}.")
    else:
        normalized = header_text.lower()
        has_guard = "#ifndef" in normalized or "#pragma once" in normalized
        if not has_guard:
            issues.append(f"{header_name} missing include guard (#ifndef or #pragma once).")
        if f'#include "{header_name}"' in header_text:
            issues.append(f"{header_name} includes itself; header appears to embed implementation code.")

    source_text = _read_text(filesystem_c)
    if source_text is None:
        issues.append(f"Missing required implementation file {filesystem_c.relative_to(workspace_root)}.")
    else:
        if f'#include "{header_name}"' not in source_text:
            issues.append(
                f'{filesystem_c.relative_to(workspace_root)} should include "{header_name}" to stay in sync with the header.'
            )

    test_text = _read_text(test_c)
    if test_text is None:
        issues.append(f"Missing test harness {test_c.relative_to(workspace_root)}.")
    else:
        stripped_lines = [
            line.strip()
            for line in test_text.splitlines()
            if line.strip() and not line.strip().startswith("//")
        ]
        if not stripped_lines:
            issues.append(f"{test_c.relative_to(workspace_root)} is empty.")
        else:
            first_token = stripped_lines[0]
            if not first_token.startswith("#include"):
                issues.append(
                    f"{test_c.relative_to(workspace_root)} should start with #include; found '{first_token}'."
                )

    filesystem_store = workspace_root / "filesystem.fs"
    if not filesystem_store.exists():
        corpus = "\n".join([t for t in (header_text, source_text, test_text) if t])
        if "filesystem.fs" not in corpus:
            issues.append("Missing backing store file filesystem.fs.")

    return issues


def record_diff_metrics(
    conductor: Any,
    tool_call: Any,
    result: Dict[str, Any],
    *,
    session_state: Optional[Any] = None,
    turn_index: Optional[int] = None,
) -> None:
    function = getattr(tool_call, "function", "")
    dialect = getattr(tool_call, "dialect", None) or "unknown"
    pas: Optional[float] = None
    hmr: Optional[float] = None

    if function in ("apply_unified_patch", "patch"):
        patch_text = ""
        try:
            if function == "patch":
                patch_text = str(tool_call.arguments.get("patchText", ""))
            else:
                patch_text = str(tool_call.arguments.get("patch", ""))
        except Exception:
            patch_text = ""
        total_hunks = count_diff_hunks(patch_text)
        rejects = (result.get("data") or {}).get("rejects") if isinstance(result, dict) else None
        failed_hunks = 0
        if isinstance(rejects, dict):
            for rej_text in rejects.values():
                failed_hunks += count_diff_hunks(rej_text)
        pas = 1.0 if result.get("ok") else 0.0
        if total_hunks <= 0:
            hmr = 1.0 if pas else 0.0
        else:
            matched = max(total_hunks - failed_hunks, 0)
            hmr = matched / total_hunks
        if session_state is not None and isinstance(turn_index, int):
            meta: Dict[str, Any] = {}
            if not result.get("ok"):
                meta["invalid_diff_block"] = True
            if patch_text and ("new file mode" in patch_text or "--- /dev/null" in patch_text):
                meta["add_file_via_diff"] = True
            if meta:
                try:
                    session_state.add_reward_metrics(turn_index, metadata=meta)
                except Exception:
                    pass
    elif function == "apply_search_replace":
        replacements = None
        try:
            replacements = int(result.get("replacements", 0))
        except Exception:
            replacements = 0
        pas = 1.0 if replacements and replacements > 0 else 0.0
        hmr = pas
    elif function == "create_file_from_block":
        pas = 1.0 if not result.get("error") else 0.0
        hmr = pas

    if pas is not None:
        try:
            conductor.provider_metrics.add_dialect_metric(
                dialect=dialect,
                tool=function,
                pas=float(pas),
                hmr=float(hmr) if hmr is not None else None,
            )
        except Exception:
            pass
        if session_state is not None and isinstance(turn_index, int):
            try:
                session_state.add_reward_metric(turn_index, "PAS", float(pas))
                if hmr is not None:
                    session_state.add_reward_metric(turn_index, "HMR", float(hmr))
            except Exception:
                pass


def retry_diff_with_aider(patch_text: str) -> Optional[Dict[str, Any]]:
    """Attempt to recover unified diff failures by applying Aider SEARCH/REPLACE blocks."""
    matcher = re.compile(
        r"^diff --git a/(?P<path>.+?) b/(?P=path).*?(^@@.*?$.*?)(?=^diff --git|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    file_blocks: Dict[str, Dict[str, List[str]]] = {}

    for match in matcher.finditer(patch_text):
        path = match.group("path")
        section = match.group(2) or ""
        block = file_blocks.setdefault(path, {"search": [], "replace": []})

        hunk_lines = section.splitlines()
        local_search: List[str] = []
        local_replace: List[str] = []
        for line in hunk_lines:
            if not line:
                continue
            if line.startswith("diff --git") or line.startswith("new file mode") or line.startswith("index ") or line.startswith("---") or line.startswith("+++"):
                continue
            if line.startswith("@@"):
                if local_search or local_replace:
                    block["search"].append("\n".join(local_search))
                    block["replace"].append("\n".join(local_replace))
                    local_search = []
                    local_replace = []
                continue
            if line.startswith("-"):
                local_search.append(line[1:])
            elif line.startswith("+"):
                local_replace.append(line[1:])
            else:
                local_search.append(line[1:])
                local_replace.append(line[1:])
        if local_search or local_replace:
            block["search"].append("\n".join(local_search))
            block["replace"].append("\n".join(local_replace))

    if len(file_blocks) != 1:
        return None

    file_name, data = next(iter(file_blocks.items()))
    if not data["search"]:
        return None

    search_text = "\n".join(filter(None, data["search"]))
    replace_text = "\n".join(filter(None, data["replace"]))
    payload = {
        "function": "apply_search_replace",
        "arguments": {
            "file_name": file_name,
            "search": search_text,
            "replace": replace_text,
        },
    }
    return payload


def synthesize_patch_blocks(message_text: Optional[str]) -> List[str]:
    if not message_text:
        return []
    blocks: List[str] = []
    fence_pattern = re.compile(r"```(?:patch|diff)\s+(.*?)```", re.IGNORECASE | re.DOTALL)
    for block in fence_pattern.findall(message_text):
        normalized = block.strip()
        if not normalized:
            continue
        if not normalized.endswith("\n"):
            normalized += "\n"
        blocks.append(normalized)
    if not blocks and "*** Begin Patch" in message_text:
        begin_end_pattern = re.compile(r"\*\*\* Begin Patch.*?\*\*\* End Patch", re.IGNORECASE | re.DOTALL)
        for block in begin_end_pattern.findall(message_text):
            normalized = block.strip()
            if not normalized:
                continue
            if not normalized.endswith("\n"):
                normalized += "\n"
            blocks.append(normalized)
    if not blocks and message_text.strip().lower().startswith("patch"):
        blocks.append(f"*** Begin Patch ***\n{message_text.strip()}\n*** End Patch ***")
    return blocks
