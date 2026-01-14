#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


CAT_RE = re.compile(r"(?m)^\s*cat\s+([^\s|;&]+)(?:\s*\|.*)?\s*$")
SED_RE = re.compile(r"(?m)^\s*sed\s+-n\s+['\"](\d+),(\d+)p['\"]\s+([^\s;&]+)(?:\s*\|.*)?\s*$")
NL_SED_RE = re.compile(r"(?m)^\s*nl\s+-ba\s+([^\s|;&]+)\s*\|\s*sed\s+-n\s+['\"](\d+),(\d+)p['\"](?:\s*\|.*)?\s*$")


def _iter_rollouts(sessions_dir: Path) -> Iterable[Path]:
    yield from sessions_dir.rglob("rollout-*.jsonl")


def _safe_json_loads(line: str) -> Optional[dict]:
    try:
        return json.loads(line)
    except Exception:
        return None


def _normalize_path_token(token: str) -> str:
    token = token.strip().strip("'").strip('"')
    return os.path.normpath(token).replace("\\", "/")


def _maybe_relativize(token: str, repo_root: Path) -> str:
    if not token:
        return token
    if os.path.isabs(token):
        try:
            rel = Path(token).resolve().relative_to(repo_root)
        except Exception:
            return token
        return rel.as_posix()
    return token


def _extract_output_text(output_raw) -> str:
    if output_raw is None:
        return ""
    if isinstance(output_raw, str):
        # Some tool outputs are JSON-encoded strings.
        try:
            parsed = json.loads(output_raw)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            return str(parsed.get("output") or "")
        return output_raw
    if isinstance(output_raw, dict):
        return str(output_raw.get("output") or "")
    return str(output_raw)


def _strip_shell_prefix(text: str) -> str:
    marker = "Output:\n"
    idx = text.find(marker)
    return text[idx + len(marker) :] if idx >= 0 else text


@dataclass(frozen=True)
class Candidate:
    path: str
    kind: str  # cat | sed | nl_sed
    start: Optional[int]
    end: Optional[int]
    content: str
    rollout: str
    timestamp: str


def _looks_truncated(text: str) -> bool:
    if not text:
        return False
    if "chars truncated" in text.lower():
        return True
    if "…chars truncated" in text.lower():
        return True
    # Some harnesses inject literal ellipsis when truncating.
    if "…" in text:
        return True
    return False


def _parse_nl_block(content: str) -> Dict[int, str]:
    mapping: Dict[int, str] = {}
    for line in content.splitlines():
        if "\t" in line:
            num_str, rest = line.split("\t", 1)
            num_str = num_str.strip()
            if num_str.isdigit():
                mapping[int(num_str)] = rest
                continue
        m = re.match(r"^\s*(\d+)\s+(.*)$", line)
        if not m:
            continue
        mapping[int(m.group(1))] = m.group(2)
    return mapping


def _choose_best(cands: List[Candidate]) -> Optional[Candidate]:
    if not cands:
        return None

    def score(c: Candidate) -> Tuple[int, int, int]:
        # Prefer complete captures.
        if c.kind == "cat":
            return (3, len(c.content), 0)
        if c.kind == "sed":
            span = (c.end or 0) - (c.start or 0)
            starts_at_1 = 1 if (c.start or 0) == 1 else 0
            return (2, starts_at_1, span)
        if c.kind == "nl_sed":
            span = (c.end or 0) - (c.start or 0)
            starts_at_1 = 1 if (c.start or 0) == 1 else 0
            return (1, starts_at_1, span)
        return (0, 0, 0)

    return sorted(cands, key=score, reverse=True)[0]


def _stitch_segments(
    cands: List[Candidate],
    *,
    fill_missing_only: bool,
    prefer_latest: bool,
) -> Optional[str]:
    """Stitch sed/nl_sed segments into a best-effort full file."""
    segs: List[Tuple[str, int, List[str]]] = []
    for c in cands:
        if c.kind not in ("sed", "nl_sed"):
            continue
        if c.start is None:
            continue
        lines = c.content.splitlines()
        segs.append((c.timestamp, c.start, lines))

    if not segs:
        return None

    # Prefer earlier or later captures for overlaps.
    segs.sort(key=lambda s: (s[0], s[1]), reverse=prefer_latest)

    # Determine max line we can address based on captured data.
    max_line = 0
    for _, start, lines in segs:
        max_line = max(max_line, start + len(lines) - 1)

    out: List[Optional[str]] = [None] * (max_line + 1)  # 1-indexed
    for _, start, lines in segs:
        for idx, line in enumerate(lines):
            ln = start + idx
            if ln <= 0 or ln >= len(out):
                continue
            if fill_missing_only and out[ln] is not None:
                continue
            out[ln] = line

    # Fill gaps with explicit markers so we never silently fabricate content.
    for ln in range(1, len(out)):
        if out[ln] is None:
            out[ln] = f"# [RECOVERY MISSING LINE {ln}]"

    return "\n".join(out[1:]) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover missing files by mining Codex rollout tool outputs (cat/sed/nl).")
    parser.add_argument("--sessions-dir", default=str(Path.home() / ".codex" / "sessions"))
    parser.add_argument("--repo-root", default=".", help="Repo root used to resolve absolute paths in tool commands.")
    parser.add_argument("--out-root", required=True, help="Directory to write recovered files into.")
    parser.add_argument("--paths-file", required=True, help="Newline-delimited file paths to recover (repo-relative).")
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Overwrite files that already exist in out-root (useful to repair truncated captures).",
    )
    parser.add_argument(
        "--allow-truncated",
        action="store_true",
        help="Keep truncated tool outputs and salvage any parseable lines.",
    )
    parser.add_argument(
        "--fill-missing-only",
        action="store_true",
        help="Do not overwrite existing stitched lines when merging segments.",
    )
    parser.add_argument(
        "--prefer-latest",
        action="store_true",
        help="When stitching, apply newer segments first (useful with --fill-missing-only).",
    )
    parser.add_argument("--max-rollouts", type=int, default=0, help="Limit number of rollouts scanned (0 = no limit).")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    sessions_dir = Path(os.path.expanduser(args.sessions_dir)).resolve()
    out_root = Path(args.out_root).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    targets = [p.strip() for p in Path(args.paths_file).read_text(encoding="utf-8").splitlines() if p.strip()]
    target_set = set(_normalize_path_token(p) for p in targets)

    pending: Dict[str, List[Tuple[str, str, Optional[int], Optional[int], str, str]]] = {}
    # call_id -> list of (target_path, kind, start, end, rollout_path, timestamp)
    cands: Dict[str, List[Candidate]] = {t: [] for t in target_set}

    rollouts = sorted(_iter_rollouts(sessions_dir))
    if args.max_rollouts and args.max_rollouts > 0:
        rollouts = rollouts[: args.max_rollouts]

    for rollout in rollouts:
        with rollout.open("r", encoding="utf-8") as f:
            for line in f:
                obj = _safe_json_loads(line)
                if not obj or obj.get("type") != "response_item":
                    continue
                payload = obj.get("payload") or {}
                ptype = payload.get("type")
                timestamp = str(obj.get("timestamp") or "")

                if ptype == "function_call":
                    call_id = payload.get("call_id") or ""
                    if not call_id:
                        continue
                    args_raw = payload.get("arguments") or ""
                    try:
                        call_args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    except Exception:
                        call_args = {}
                    command = ""
                    cmd = call_args.get("command") if isinstance(call_args, dict) else None
                    if isinstance(cmd, str):
                        command = cmd
                    elif isinstance(cmd, list):
                        command = "\n".join(str(x) for x in cmd)
                    if not command:
                        continue

                    # cat captures
                    for m in CAT_RE.finditer(command):
                        path_token = _normalize_path_token(m.group(1))
                        path_token = _maybe_relativize(path_token, repo_root)
                        if path_token in target_set:
                            pending.setdefault(call_id, []).append((path_token, "cat", None, None, str(rollout), timestamp))

                    # sed captures
                    for m in SED_RE.finditer(command):
                        start = int(m.group(1))
                        end = int(m.group(2))
                        path_token = _normalize_path_token(m.group(3))
                        path_token = _maybe_relativize(path_token, repo_root)
                        if path_token in target_set:
                            pending.setdefault(call_id, []).append((path_token, "sed", start, end, str(rollout), timestamp))

                    # nl -ba | sed captures
                    for m in NL_SED_RE.finditer(command):
                        path_token = _normalize_path_token(m.group(1))
                        path_token = _maybe_relativize(path_token, repo_root)
                        start = int(m.group(2))
                        end = int(m.group(3))
                        if path_token in target_set:
                            pending.setdefault(call_id, []).append((path_token, "nl_sed", start, end, str(rollout), timestamp))

                if ptype == "function_call_output":
                    call_id = payload.get("call_id") or ""
                    if not call_id or call_id not in pending:
                        continue
                    output_text = _extract_output_text(payload.get("output"))
                    content = _strip_shell_prefix(output_text)
                    if _looks_truncated(content) and not args.allow_truncated:
                        pending.pop(call_id, None)
                        continue

                    for target_path, kind, start, end, roll_path, ts in pending.pop(call_id, []):
                        if kind == "nl_sed":
                            mapping = _parse_nl_block(content)
                            if mapping:
                                # Reconstruct contiguous range if possible.
                                lines: List[str] = []
                                for ln in range(start or 1, (end or 0) + 1):
                                    if ln in mapping:
                                        lines.append(mapping[ln])
                                content_norm = "\n".join(lines) + ("\n" if lines else "")
                            else:
                                content_norm = content
                        else:
                            content_norm = content
                        cands[target_path].append(
                            Candidate(
                                path=target_path,
                                kind=kind,
                                start=start,
                                end=end,
                                content=content_norm,
                                rollout=roll_path,
                                timestamp=ts,
                            )
                        )

    recovered: Dict[str, dict] = {}
    for target in sorted(target_set):
        dst = (out_root / target).resolve()
        if dst.exists() and not args.overwrite_existing:
            continue

        candidates = cands.get(target) or []
        # Prefer full cats, otherwise stitch segments.
        best = _choose_best(candidates)
        content: Optional[str] = None
        meta: dict = {}
        if best and best.kind == "cat":
            content = best.content
            meta = {
                "kind": best.kind,
                "start": best.start,
                "end": best.end,
                "rollout": best.rollout,
                "timestamp": best.timestamp,
            }
        else:
            stitched = _stitch_segments(
                candidates,
                fill_missing_only=args.fill_missing_only,
                prefer_latest=args.prefer_latest,
            )
            if stitched:
                content = stitched
                meta = {"kind": "stitched", "segments": len([c for c in candidates if c.kind in ("sed", "nl_sed")])}
            elif best:
                content = best.content
                meta = {
                    "kind": best.kind,
                    "start": best.start,
                    "end": best.end,
                    "rollout": best.rollout,
                    "timestamp": best.timestamp,
                }

        if content is None:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(content, encoding="utf-8")
        recovered[target] = {**meta, "bytes": len(content.encode("utf-8"))}

    (out_root / "recovered_manifest.json").write_text(json.dumps(recovered, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    missing = [t for t in sorted(target_set) if t not in recovered]
    print(json.dumps({"out_root": str(out_root), "requested": len(target_set), "recovered": len(recovered), "missing": len(missing)}, indent=2))
    if missing:
        print("\nMissing paths (tail):")
        for p in missing[-20:]:
            print(f"- {p}")


if __name__ == "__main__":
    main()

