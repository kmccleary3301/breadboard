from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional


class PatchParseError(RuntimeError):
    pass


@dataclass(frozen=True)
class PatchHunk:
    header: str
    lines: List[str]


@dataclass
class PatchOp:
    kind: str  # add | update | delete | move | copy
    file_path: str
    move_to: Optional[str] = None
    content: str = ""
    hunks: List[PatchHunk] = None  # type: ignore[assignment]


def parse_opencode_patch(text: str) -> List[PatchOp]:
    lines = (text or "").splitlines()
    if not any(line.strip() == "*** Begin Patch" for line in lines):
        raise PatchParseError("Missing '*** Begin Patch'")

    ops: List[PatchOp] = []
    i = 0

    def at(idx: int) -> str:
        return lines[idx] if 0 <= idx < len(lines) else ""

    while i < len(lines):
        line = at(i).rstrip("\n")
        if line.strip() in ("", "*** Begin Patch", "*** End Patch"):
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
                if cur.startswith("+"):
                    content_lines.append(cur[1:])
                elif cur == "":
                    # Some patches may include blank lines without a '+'; treat them as blanks.
                    content_lines.append("")
                else:
                    raise PatchParseError(f"Unexpected line in Add File {file_path}: {cur!r}")
                i += 1
            content = "\n".join(content_lines)
            if content_lines:
                content += "\n"
            ops.append(PatchOp(kind="add", file_path=file_path, content=content, hunks=[]))
            continue

        if line.startswith("*** Delete File: "):
            file_path = line[len("*** Delete File: ") :].strip()
            ops.append(PatchOp(kind="delete", file_path=file_path, hunks=[]))
            i += 1
            continue

        if line.startswith("*** Move File: ") or line.startswith("*** Copy File: "):
            verb = "move" if line.startswith("*** Move File: ") else "copy"
            hdr = "*** Move File: " if verb == "move" else "*** Copy File: "
            file_path = line[len(hdr) :].strip()
            i += 1
            move_to: Optional[str] = None
            while i < len(lines):
                cur = at(i)
                if cur.startswith("*** To File: "):
                    move_to = cur[len("*** To File: ") :].strip()
                    i += 1
                    break
                if cur.startswith("*** "):
                    break
                if cur.strip() == "":
                    i += 1
                    continue
                raise PatchParseError(f"Unexpected line in {verb} op for {file_path}: {cur!r}")
            if not move_to:
                raise PatchParseError(f"Missing '*** To File:' for {verb} op: {file_path}")
            ops.append(PatchOp(kind=verb, file_path=file_path, move_to=move_to, hunks=[]))
            continue

        if line.startswith("*** Update File: "):
            file_path = line[len("*** Update File: ") :].strip()
            i += 1
            move_to: Optional[str] = None
            hunks: List[PatchHunk] = []

            current_hunk_header: Optional[str] = None
            current_hunk_lines: List[str] = []

            def flush_hunk():
                nonlocal current_hunk_header, current_hunk_lines, hunks
                if current_hunk_header is None:
                    return
                hunks.append(PatchHunk(header=current_hunk_header, lines=current_hunk_lines))
                current_hunk_header = None
                current_hunk_lines = []

            while i < len(lines):
                cur = at(i)
                if cur.startswith("*** "):
                    break
                if cur.startswith("@@"):
                    flush_hunk()
                    current_hunk_header = cur
                    current_hunk_lines = []
                    i += 1
                    continue
                if cur.startswith("*** Move to: "):
                    move_to = cur[len("*** Move to: ") :].strip()
                    i += 1
                    continue
                if cur.strip() == "*** End of File":
                    i += 1
                    continue
                if cur == "":
                    # Treat empty as a context line with empty payload.
                    if current_hunk_header is None:
                        current_hunk_header = "@@"
                    current_hunk_lines.append(" ")
                    i += 1
                    continue
                if cur[0] in (" ", "+", "-"):
                    if current_hunk_header is None:
                        current_hunk_header = "@@"
                    current_hunk_lines.append(cur)
                    i += 1
                    continue
                raise PatchParseError(f"Unexpected line in Update File {file_path}: {cur!r}")

            flush_hunk()
            ops.append(PatchOp(kind="update", file_path=file_path, move_to=move_to, hunks=hunks))
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


def apply_update_hunks_codex(
    original: str,
    hunks: Iterable[PatchHunk],
    *,
    file_label: str = "<file>",
    allow_idempotent: bool = True,
) -> str:
    # Preserve line endings as LF for patch application.
    lines = (original or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # If original ended with newline, split() leaves a trailing "", which we preserve.
    for hunk in hunks:
        before: List[str] = []
        after: List[str] = []
        for raw in hunk.lines:
            if raw == "":
                # shouldn't happen, but treat as context blank
                before.append("")
                after.append("")
                continue
            tag = raw[0]
            payload = raw[1:] if len(raw) > 1 else ""
            if tag == " ":
                before.append(payload)
                after.append(payload)
            elif tag == "-":
                before.append(payload)
            elif tag == "+":
                after.append(payload)
            else:
                raise PatchParseError(f"Bad hunk line prefix {tag!r} in {file_label}")

        idx = _find_subsequence(lines, before)
        if idx is None and allow_idempotent:
            # If the "after" already exists, treat as already applied.
            if after and _find_subsequence(lines, after) is not None:
                continue
        if idx is None:
            raise PatchParseError(f"Failed to apply hunk in {file_label}: could not locate context")
        lines = lines[:idx] + after + lines[idx + len(before) :]

    return "\n".join(lines)

