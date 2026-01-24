"""OpenCode patch parsing and application helpers."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence


class PatchParseError(RuntimeError):
    """Raised when an OpenCode patch cannot be parsed."""


@dataclass
class PatchChange:
    kind: str  # "keep", "add", "remove"
    content: str


@dataclass
class PatchHunk:
    context: str
    changes: List[PatchChange]
    is_end_of_file: bool = False


@dataclass
class PatchOperation:
    kind: str  # "add", "update", "delete"
    file_path: str
    hunks: List[PatchHunk] | None = None
    content: str | None = None
    move_to: str | None = None


_PATCH_BLOCK_RE = re.compile(r"\*\*\* Begin Patch[\t ]*\r?\n(?P<body>[\s\S]*?)\*\*\* End Patch", re.M)


def _iter_patch_blocks(text: str) -> Iterable[str]:
    for match in _PATCH_BLOCK_RE.finditer(text):
        yield match.group("body")

    if text.strip() and not _PATCH_BLOCK_RE.search(text):
        raise PatchParseError("No *** Begin Patch ... *** End Patch block found")


def parse_opencode_patch(text: str) -> List[PatchOperation]:
    ops: List[PatchOperation] = []
    for block in _iter_patch_blocks(text):
        lines = block.splitlines()
        i = 0

        def at(idx: int) -> str:
            return lines[idx] if 0 <= idx < len(lines) else ""

        while i < len(lines):
            line = at(i)
            if not line.strip():
                i += 1
                continue

            if line.startswith("*** Add File: "):
                file_path = line[len("*** Add File: ") :].strip()
                i += 1
                content_lines: List[str] = []
                while i < len(lines):
                    cur = at(i)
                    if cur.startswith("*** "):
                        break
                    if cur.startswith("@@"):
                        i += 1
                        continue
                    if cur.startswith("+"):
                        content_lines.append(cur[1:])
                    elif cur == "":
                        content_lines.append("")
                    else:
                        raise PatchParseError(f"Unexpected line in Add File {file_path}: {cur!r}")
                    i += 1
                content = "\n".join(content_lines)
                if content_lines:
                    content += "\n"
                ops.append(PatchOperation(kind="add", file_path=file_path, content=content, hunks=[]))
                continue

            if line.startswith("*** Delete File: "):
                file_path = line[len("*** Delete File: ") :].strip()
                ops.append(PatchOperation(kind="delete", file_path=file_path, hunks=[]))
                i += 1
                continue

            if line.startswith("*** Update File: "):
                file_path = line[len("*** Update File: ") :].strip()
                i += 1
                move_to: Optional[str] = None
                hunks: List[PatchHunk] = []

                current_header: Optional[str] = None
                current_changes: List[PatchChange] = []
                current_eof = False

                def flush_hunk() -> None:
                    nonlocal current_header, current_changes, current_eof
                    if current_header is None:
                        return
                    if not current_changes:
                        raise PatchParseError(f"Empty hunk in Update File {file_path}")
                    hunks.append(
                        PatchHunk(
                            context=current_header,
                            changes=current_changes,
                            is_end_of_file=current_eof,
                        )
                    )
                    current_header = None
                    current_changes = []
                    current_eof = False

                while i < len(lines):
                    cur = at(i)
                    if cur.startswith("@@"):
                        flush_hunk()
                        current_header = cur
                        current_changes = []
                        current_eof = False
                        i += 1
                        continue
                    if cur.startswith("*** Move to: "):
                        move_to = cur[len("*** Move to: ") :].strip()
                        i += 1
                        continue
                    if cur.strip() == "*** End of File":
                        current_eof = True
                        i += 1
                        continue
                    if cur.startswith("*** "):
                        break
                    if cur == "":
                        if current_header is None:
                            current_header = "@@"
                        current_changes.append(PatchChange("keep", ""))
                        i += 1
                        continue
                    if cur[0] in (" ", "+", "-"):
                        if current_header is None:
                            current_header = "@@"
                        kind = {" ": "keep", "+": "add", "-": "remove"}[cur[0]]
                        current_changes.append(PatchChange(kind, cur[1:]))
                        i += 1
                        continue
                    raise PatchParseError(f"Unexpected line in Update File {file_path}: {cur!r}")

                flush_hunk()
                ops.append(
                    PatchOperation(
                        kind="update",
                        file_path=file_path,
                        hunks=hunks,
                        move_to=move_to,
                    )
                )
                continue

            raise PatchParseError(f"Unexpected patch line: {line!r}")

    return ops


def _find_subsequence(haystack: List[str], needle: List[str]) -> Optional[int]:
    if not needle:
        return 0
    if len(needle) > len(haystack):
        return None
    first = needle[0]
    for i in range(0, len(haystack) - len(needle) + 1):
        if haystack[i] != first:
            continue
        if haystack[i : i + len(needle)] == needle:
            return i
    return None


def apply_update_hunks(original: str, hunks: Iterable[PatchHunk]) -> str:
    lines = (original or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for hunk in hunks:
        before: List[str] = []
        after: List[str] = []
        for change in hunk.changes:
            if change.kind == "keep":
                before.append(change.content)
                after.append(change.content)
            elif change.kind == "remove":
                before.append(change.content)
            elif change.kind == "add":
                after.append(change.content)
            else:
                raise PatchParseError(f"Unknown change kind {change.kind!r}")

        idx = _find_subsequence(lines, before)
        if idx is None:
            raise PatchParseError("Failed to apply update hunk: context not found")
        lines = lines[:idx] + after + lines[idx + len(before) :]

    return "\n".join(lines)


def _normalize_codex_line(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    normalized = normalized.replace("—", "-").replace("–", "-").replace("−", "-")
    return normalized.rstrip()


def seek_sequence_codex(lines: Sequence[str], target: Sequence[str], *, start: int = 0, eof: bool = False) -> Optional[int]:
    if not target:
        return len(lines) if eof else start
    start = max(start, 0)
    haystack = [_normalize_codex_line(line) for line in lines]
    needle = [_normalize_codex_line(line) for line in target]
    indices = range(start, len(haystack) - len(needle) + 1)
    if eof:
        indices = range(len(haystack) - len(needle), start - 1, -1)
    for i in indices:
        if haystack[i : i + len(needle)] == list(needle):
            return i
    return None


def apply_update_hunks_codex(
    original: str,
    hunks: Iterable[PatchHunk],
    *,
    file_label: str = "<file>",
) -> str:
    normalized = (original or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if normalized.endswith("\n"):
        # Preserve exactly one trailing newline (Codex CLI parity behavior).
        while len(lines) > 1 and lines[-1] == "" and lines[-2] == "":
            lines.pop()
    for hunk in hunks:
        before: List[str] = []
        after: List[str] = []
        for change in hunk.changes:
            if change.kind == "keep":
                before.append(change.content)
                after.append(change.content)
            elif change.kind == "remove":
                before.append(change.content)
            elif change.kind == "add":
                after.append(change.content)
            else:
                raise PatchParseError(f"Unknown change kind {change.kind!r} in {file_label}")

        idx = seek_sequence_codex(lines, before, start=0, eof=hunk.is_end_of_file)
        if idx is None:
            raise PatchParseError(f"Failed to apply Codex hunk in {file_label}: context not found")
        lines = lines[:idx] + after + lines[idx + len(before) :]

    return "\n".join(lines)


def to_unified_diff(patch_text: str, fetch_current) -> str:
    """Best-effort conversion from OpenCode patch to unified diff.

    Recovery stub: return input verbatim when no conversion is available.
    """
    return patch_text or ""
