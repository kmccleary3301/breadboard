"""File operations tracking across conversation messages and compaction records.

Ports OMP ``utils.ts`` (lines 17-205) and ``compaction.ts`` (lines 107-140) for
BreadBoard tool schemas and Pi/OMP legacy names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Callable, Generator, Iterable, Mapping, NamedTuple, Optional, Sequence, Set

from .prompts import render_prompt

RANGE_CHUNK_SRC = r"L?\d+(?:(?:[-+]|\.\.)L?\d+|-|\.\.)?"
RANGE_LIST_SRC = rf"{RANGE_CHUNK_SRC}(?:,{RANGE_CHUNK_SRC})*"
READ_SELECTOR_RE = re.compile(rf"^(?:{RANGE_LIST_SRC}|raw|conflicts)$", re.IGNORECASE)
READ_RANGE_ONLY_RE = re.compile(rf"^{RANGE_LIST_SRC}$", re.IGNORECASE)
READ_RAW_ONLY_RE = re.compile(r"^raw$", re.IGNORECASE)
URL_SCHEME_RE = re.compile(r"[a-z][a-z0-9+.-]*://", re.IGNORECASE)

FILE_OPERATION_SUMMARY_LIMIT = 20


@dataclass
class FileOperations:
    """Accumulator for files touched across a session."""

    read: Set[str] = field(default_factory=set)
    written: Set[str] = field(default_factory=set)
    edited: Set[str] = field(default_factory=set)


def create_file_ops() -> FileOperations:
    return FileOperations()


def split_read_selector(path: str) -> tuple[str, Optional[str]]:
    """Split a read-tool path into base path and trailing selector."""
    colon = path.rfind(":")
    if colon <= 0:
        return path, None
    candidate = path[colon + 1 :]
    if not READ_SELECTOR_RE.match(candidate):
        return path, None
    base = path[:colon]
    sel = candidate

    inner = base.rfind(":")
    if inner > 0:
        inner_cand = base[inner + 1 :]
        inner_is_raw = bool(READ_RAW_ONLY_RE.match(inner_cand))
        outer_is_raw = bool(READ_RAW_ONLY_RE.match(candidate))
        inner_is_range = bool(READ_RANGE_ONLY_RE.match(inner_cand))
        outer_is_range = bool(READ_RANGE_ONLY_RE.match(candidate))
        if (inner_is_raw and outer_is_range) or (inner_is_range and outer_is_raw):
            sel = f"{inner_cand}:{candidate}"
            base = base[:inner]

    return base, sel


def strip_read_selector(path: str) -> str:
    """Strip a trailing selector so line ranges deduplicate to the base file path."""
    return split_read_selector(path)[0]


def is_url_scheme_path(path: str) -> bool:
    """Return True if path contains a URI scheme (conflict://, artifact://, https://, etc.)."""
    return bool(URL_SCHEME_RE.search(path))


class FileLists(NamedTuple):
    read_files: list[str]
    modified_files: list[str]



def compute_file_lists(file_ops: FileOperations) -> FileLists:
    """Compute deduplicated read-only and modified file lists."""
    modified = {f for f in (file_ops.edited | file_ops.written) if not is_url_scheme_path(f)}
    read_only = sorted(f for f in file_ops.read if not is_url_scheme_path(f) and f not in modified)
    modified_files = sorted(modified)
    return FileLists(read_files=read_only, modified_files=modified_files)


# -----------------------------------------------------------------------------
# Path Tree Formatting (OMP path-tree.ts)
# -----------------------------------------------------------------------------


@dataclass
class _PathTreeNode:
    files: list[tuple[str, str]] = field(default_factory=list)  # (name, key)
    file_names: set[str] = field(default_factory=set)
    subdirs: list[tuple[str, "_PathTreeNode"]] = field(default_factory=list)
    dir_index: dict[str, "_PathTreeNode"] = field(default_factory=dict)


def _build_path_tree(paths: Sequence[str]) -> _PathTreeNode:
    root = _PathTreeNode()
    for raw_path in paths:
        norm = raw_path.replace("\\", "/")
        file_key = norm
        if is_url_scheme_path(norm):
            if norm not in root.file_names:
                root.file_names.add(norm)
                root.files.append((norm, file_key))
            continue
        is_dir = norm.endswith("/")
        trimmed = norm[:-1] if is_dir else norm
        if not trimmed:
            continue
        segments = trimmed.split("/")
        dir_count = len(segments) if is_dir else len(segments) - 1
        node = root
        for i in range(dir_count):
            segment = segments[i]
            child = node.dir_index.get(segment)
            if not child:
                child = _PathTreeNode()
                node.dir_index[segment] = child
                node.subdirs.append((segment, child))
            node = child
        if not is_dir:
            name = segments[-1]
            if name not in node.file_names:
                node.file_names.add(name)
                node.files.append((name, file_key))
    return root


def _walk_path_tree(
    node: _PathTreeNode, depth: int = 0
) -> Generator[tuple[str, int, str, str], None, None]:
    # yields (kind: "dir"|"file", depth, name, key)
    for name, key in node.files:
        yield ("file", depth, name, key)
    for subdir_name, subdir_node in node.subdirs:
        dir_node = subdir_node
        parts = [subdir_name]
        while len(dir_node.files) == 0 and len(dir_node.subdirs) == 1:
            only_name, only_node = dir_node.subdirs[0]
            parts.append(only_name)
            dir_node = only_node
        yield ("dir", depth, "/".join(parts), "")
        yield from _walk_path_tree(dir_node, depth + 1)


def format_grouped_paths(
    paths: Sequence[str],
    annotate: Optional[Callable[[str], str]] = None,
) -> str:
    """Render a flat path list as a grouped, prefix-folded directory tree."""
    if not paths:
        return ""
    tree = _build_path_tree(paths)
    lines: list[str] = []
    for kind, depth, name, key in _walk_path_tree(tree):
        if kind == "dir":
            lines.append(f"{'#' * (depth + 1)} {name}/")
        else:
            annotation = annotate(key) if annotate else ""
            lines.append(f"{name}{annotation}")
    return "\n".join(lines)


formatGroupedPaths = format_grouped_paths


def strip_file_operation_tags(summary: str) -> str:
    """Strip legacy and current file-operation tags from a summary string."""
    cleaned = re.sub(r"<files>[\s\S]*?</files>\s*", "", summary)
    cleaned = re.sub(r"<read-files>[\s\S]*?</read-files>\s*", "", cleaned)
    cleaned = re.sub(r"<modified-files>[\s\S]*?</modified-files>\s*", "", cleaned)
    return cleaned.rstrip()


def format_file_operations(
    read_files: Sequence[str],
    modified_files: Sequence[str],
    read_set: Optional[Iterable[str]] = None,
) -> str:
    """Format file operations into an XML `<files>` tag block."""
    if not read_files and not modified_files:
        return ""
    read_lookup = set(read_set) if read_set is not None else set()
    mode: dict[str, str] = {}
    for f in read_files:
        mode[f] = "Read"
    for f in modified_files:
        mode[f] = "RW" if f in read_lookup else "Write"

    all_paths = sorted(mode.keys())
    shown = all_paths[:FILE_OPERATION_SUMMARY_LIMIT]
    files_tree = format_grouped_paths(shown, lambda p: f" ({mode[p]})")
    if len(all_paths) > FILE_OPERATION_SUMMARY_LIMIT:
        files_tree += f"\n[…{len(all_paths) - FILE_OPERATION_SUMMARY_LIMIT} files elided…]"

    return render_prompt("file-operations", {"files": files_tree})


formatFileOperations = format_file_operations


def upsert_file_operations(
    summary: str,
    read_files: Sequence[str],
    modified_files: Sequence[str],
    read_set: Optional[Iterable[str]] = None,
) -> str:
    """Update or append the <files> tag block in a summary."""
    base_summary = strip_file_operation_tags(summary)
    file_ops_tag = format_file_operations(read_files, modified_files, read_set)
    if not file_ops_tag:
        return base_summary
    if not base_summary:
        return file_ops_tag
    return f"{base_summary}\n\n{file_ops_tag}"


# -----------------------------------------------------------------------------
# Tool Call Extraction
# -----------------------------------------------------------------------------

_READ_TOOLS = {"read", "read_file", "blob.put_file_slice"}
_WRITE_TOOLS = {"write", "create_file", "create_file_from_block"}
_EDIT_TOOLS = {"edit", "apply_search_replace", "apply_unified_patch", "apply_patch", "patch"}

_ADD_FILE_RE = re.compile(r"^\*\*\*\s+Add\s+File:\s*(.+)$", re.MULTILINE)
_DEL_FILE_RE = re.compile(r"^\*\*\*\s+Delete\s+File:\s*(.+)$", re.MULTILINE)
_UPD_FILE_RE = re.compile(r"^\*\*\*\s+Update\s+File:\s*(.+)$", re.MULTILINE)
_MOV_FILE_RE = re.compile(r"^\*\*\*\s+Move\s+to:\s*(.+)$", re.MULTILINE)
_DIFF_FILE_RE = re.compile(r"^(?:---|\+\+\+)\s+(?:[ab]/)?(.+)$", re.MULTILINE)


def _extract_path_from_args(args: Mapping[str, Any]) -> Optional[str]:
    for key in ("path", "filePath", "file_path", "file_name", "filename"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def extract_file_ops_from_message(
    message: Mapping[str, Any],
    file_ops: FileOperations,
) -> None:
    """Extract read/write/edit paths from tool calls in an assistant message."""
    role = message.get("role")
    if role != "assistant":
        return

    calls: list[Mapping[str, Any]] = []
    for call in message.get("tool_calls") or ():
        if isinstance(call, Mapping):
            calls.append(call)

    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, Mapping) and block.get("type") in ("toolCall", "tool_call"):
                calls.append(block)

    for call in calls:
        function = call.get("function") if isinstance(call.get("function"), Mapping) else call
        name = str(function.get("name") or call.get("name") or "")
        args = function.get("arguments") or call.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        if not isinstance(args, Mapping):
            args = {}

        path = _extract_path_from_args(args)

        # Check apply_patch input
        if name in ("apply_patch", "patch"):
            patch_input = args.get("input") or args.get("patch")
            if isinstance(patch_input, str):
                for m in _ADD_FILE_RE.finditer(patch_input):
                    p = m.group(1).strip()
                    if p and not is_url_scheme_path(p):
                        file_ops.written.add(p)
                for m in _DEL_FILE_RE.finditer(patch_input):
                    p = m.group(1).strip()
                    if p and not is_url_scheme_path(p):
                        file_ops.edited.add(p)
                for m in _UPD_FILE_RE.finditer(patch_input):
                    p = m.group(1).strip()
                    if p and not is_url_scheme_path(p):
                        file_ops.edited.add(p)
                for m in _MOV_FILE_RE.finditer(patch_input):
                    p = m.group(1).strip()
                    if p and not is_url_scheme_path(p):
                        file_ops.edited.add(p)
                for m in _DIFF_FILE_RE.finditer(patch_input):
                    p = m.group(1).strip()
                    if p != "/dev/null" and not is_url_scheme_path(p):
                        file_ops.edited.add(p)

        if not path:
            continue
        if is_url_scheme_path(path):
            continue

        if name in _READ_TOOLS:
            file_ops.read.add(strip_read_selector(path))
        elif name in _WRITE_TOOLS:
            file_ops.written.add(path)
        elif name in _EDIT_TOOLS:
            file_ops.edited.add(path)


def extract_file_operations(
    messages: Sequence[Mapping[str, Any]],
    previous_records: Sequence[Any] = (),
) -> FileOperations:
    """Collect file operations from prior compaction details and recent messages."""
    file_ops = create_file_ops()

    # Rehydrate from newest compaction details
    for record in reversed(previous_records):
        details = getattr(record, "details", None)
        if isinstance(details, Mapping):
            read_files = details.get("read_files") or details.get("readFiles")
            if isinstance(read_files, (list, tuple)):
                for f in read_files:
                    if isinstance(f, str) and not is_url_scheme_path(f):
                        file_ops.read.add(strip_read_selector(f))
            mod_files = details.get("modified_files") or details.get("modifiedFiles")
            if isinstance(mod_files, (list, tuple)):
                for f in mod_files:
                    if isinstance(f, str) and not is_url_scheme_path(f):
                        file_ops.edited.add(f)
            break

    for msg in messages:
        extract_file_ops_from_message(msg, file_ops)

    return file_ops
