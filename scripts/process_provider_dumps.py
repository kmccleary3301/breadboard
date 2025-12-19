#!/usr/bin/env python3
"""
Utility script to normalize OpenCode provider dumps into ordered turn files.

Given a directory full of `*_request.json` / `*_response.json` blobs emitted by
the instrumented OpenCode CLI, we sort them by timestamp and write
`turn_XXX_request.json` (and `turn_XXX_response.json` when available) into a
clean output directory. A manifest is also produced to make downstream mapping
to golden transcripts easier.

Optionally, the script can strip leading NUL bytes from a provider log file so
that archived logs stay readable.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass
class DumpEntry:
    kind: str
    request_id: str
    path: Path
    payload: Dict[str, Any]
    timestamp: Optional[dt.datetime]
    provider_id: Optional[str]
    model_id: Optional[str]
    workspace_path: Optional[str]
    session_id: Optional[str]
    phase: Optional[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize provider dumps into ordered turn artifacts."
    )
    parser.add_argument(
        "--input-dir",
        required=True,
        type=Path,
        help="Directory containing *_request.json / *_response.json files.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory to hold normalized turn files (will be created).",
    )
    parser.add_argument(
        "--clean-log",
        type=Path,
        default=None,
        help="Optional provider log file to clean (leading NUL bytes removed).",
    )
    parser.add_argument(
        "--clean-log-output",
        type=Path,
        default=None,
        help="Optional path for the cleaned log (defaults to <log>.clean).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show the operations that would be performed.",
    )
    return parser.parse_args()


def parse_timestamp(raw: Optional[str]) -> Optional[dt.datetime]:
    if not raw or not isinstance(raw, str):
        return None
    normalized = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        return dt.datetime.fromisoformat(normalized)
    except ValueError:
        return None


def classify_entry(path: Path) -> Tuple[str, Optional[str]]:
    stem = path.stem
    if stem.endswith("_request"):
        return "request", stem[: -len("_request")]
    if stem.endswith("_response"):
        return "response", stem[: -len("_response")]
    return "unknown", None


def iter_entries(input_dir: Path) -> Iterable[DumpEntry]:
    for path in sorted(input_dir.glob("*.json")):
        kind, request_id = classify_entry(path)
        if kind == "unknown" or not request_id:
            continue
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Failed to parse {path}: {exc}") from exc
        yield DumpEntry(
            kind=kind,
            request_id=request_id,
            path=path,
            payload=payload,
            timestamp=parse_timestamp(payload.get("timestamp")),
            provider_id=payload.get("providerID"),
            model_id=payload.get("modelID"),
            workspace_path=payload.get("workspacePath"),
            session_id=payload.get("sessionId"),
            phase=payload.get("phase"),
        )


def pair_requests_and_responses(entries: Iterable[DumpEntry]) -> Tuple[List[DumpEntry], Dict[str, DumpEntry], List[DumpEntry]]:
    requests: List[DumpEntry] = []
    responses: Dict[str, DumpEntry] = {}
    for entry in entries:
        if entry.kind == "request":
            requests.append(entry)
        elif entry.kind == "response":
            existing = responses.get(entry.request_id)
            if existing is None or _is_newer(entry, existing):
                responses[entry.request_id] = entry
    request_ids = {req.request_id for req in requests}
    orphan_responses = [resp for resp in responses.values() if resp.request_id not in request_ids]
    return requests, responses, orphan_responses


def _is_newer(candidate: DumpEntry, current: DumpEntry) -> bool:
    if candidate.timestamp and current.timestamp:
        return candidate.timestamp > current.timestamp
    if candidate.timestamp and not current.timestamp:
        return True
    return False


def write_turn_files(
    requests: List[DumpEntry],
    responses: Dict[str, DumpEntry],
    output_dir: Path,
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    requests.sort(key=lambda entry: (entry.timestamp or dt.datetime.min, entry.path.name))
    summary: List[Dict[str, Any]] = []

    for idx, request in enumerate(requests, start=1):
        response = responses.get(request.request_id)
        req_out = output_dir / f"turn_{idx:03d}_request.json"
        resp_out = output_dir / f"turn_{idx:03d}_response.json"

        if not dry_run:
            with req_out.open("w", encoding="utf-8") as handle:
                json.dump(request.payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
            if response:
                with resp_out.open("w", encoding="utf-8") as handle:
                    json.dump(response.payload, handle, indent=2, ensure_ascii=False)
                    handle.write("\n")
            elif resp_out.exists():
                resp_out.unlink()

        record = {
            "turn": idx,
            "request_id": request.request_id,
            "request": {
                "timestamp": request.payload.get("timestamp"),
                "source_file": str(request.path),
                "output_file": str(req_out),
                "provider_id": request.provider_id,
                "model_id": request.model_id,
                "workspace_path": request.workspace_path,
                "session_id": request.session_id,
            },
            "response": None,
        }
        if response:
            record["response"] = {
                "timestamp": response.payload.get("timestamp"),
                "source_file": str(response.path),
                "output_file": str(resp_out),
                "provider_id": response.provider_id,
                "model_id": response.model_id,
                "workspace_path": response.workspace_path,
                "session_id": response.session_id,
            }
        summary.append(record)
    return summary


def write_manifest_file(
    manifest_data: List[Dict[str, Any]], output_dir: Path, dry_run: bool = False
) -> None:
    manifest_path = output_dir / "manifest.json"
    if dry_run:
        return
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest_data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def clean_log_file(
    log_path: Path, output_path: Optional[Path], dry_run: bool = False
) -> Optional[Path]:
    if output_path is None:
        output_path = log_path.with_suffix(log_path.suffix + ".clean")
    if dry_run:
        return output_path
    raw = log_path.read_bytes()
    idx = 0
    while idx < len(raw) and raw[idx] == 0:
        idx += 1
    cleaned = raw[idx:]
    output_path.write_bytes(cleaned)
    return output_path


def main() -> int:
    args = parse_args()
    input_dir: Path = args.input_dir
    output_dir: Path = args.output_dir

    if not input_dir.exists():
        print(f"[error] Input dir {input_dir} does not exist.", file=sys.stderr)
        return 1

    entries = list(iter_entries(input_dir))
    if not entries:
        print(f"[warn] No JSON files found under {input_dir}", file=sys.stderr)
        return 0

    requests, responses, orphans = pair_requests_and_responses(entries)
    manifest_data = write_turn_files(requests, responses, output_dir, dry_run=args.dry_run)
    write_manifest_file(manifest_data, output_dir, dry_run=args.dry_run)

    if orphans:
        print(f"[warn] {len(orphans)} response file(s) without matching request entries were skipped.", file=sys.stderr)

    if args.clean_log:
        cleaned_path = clean_log_file(
            args.clean_log, args.clean_log_output, dry_run=args.dry_run
        )
        if cleaned_path:
            print(f"[info] Clean log written to {cleaned_path}")

    print(
        f"[info] Wrote {len(manifest_data)} turn pairs to {output_dir}"
        + (" (dry run)" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
