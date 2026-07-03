#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentic_coder_prototype.compilation.primitive_records import (
    PrimitiveCompileError,
    canonical_record_bytes,
    finalize_record,
    get_spec,
    sha256_ref,
)


def _iter_jsonl(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"{path}:{line_no}: expected JSON object")
        yield line_no, payload


def _record_from_line(payload: dict[str, Any]) -> dict[str, Any]:
    record = payload.get("record")
    if isinstance(record, dict):
        return record
    return payload


def _validate_record(record: dict[str, Any], *, path: Path, line_no: int) -> dict[str, Any]:
    schema_version = record.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version:
        raise ValueError(f"{path}:{line_no}: missing schema_version")
    try:
        return finalize_record(get_spec(schema_version), record, validate=True)
    except PrimitiveCompileError as exc:
        raise ValueError(f"{path}:{line_no}: {exc}") from exc


def _transcript_content_digest(record: dict[str, Any]) -> str:
    contents: list[Any] = []
    items = record.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            visibility = item.get("visibility")
            if isinstance(visibility, dict) and visibility.get("model_visible") is False:
                continue
            contents.append(item.get("content"))
    return sha256_ref(canonical_record_bytes(contents))


def _transcript_item_from_event(event: dict[str, Any], seq: int) -> dict[str, Any] | None:
    kind = event.get("kind")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    content: Any
    if kind in {"message.user", "message.assistant"}:
        content = payload.get("message") if isinstance(payload, dict) and "message" in payload else payload
    elif kind == "tool.completed":
        content = payload.get("message") if isinstance(payload, dict) and "message" in payload else payload
    else:
        return None
    item = {
        "kind": "transcript.entry",
        "visibility": event.get(
            "visibility",
            {"model_visible": True, "provider_visible": True, "host_visible": True, "redaction_state": "none"},
        ),
        "content": content,
        "content_schema_version": None,
        "seq": seq,
        "provenance": {"source": "replay"},
    }
    event_id = event.get("event_id")
    if isinstance(event_id, str) and event_id:
        item["event_id"] = event_id
    return item


def replay_session_from_records(session_dir: Path) -> dict[str, Any]:
    records_dir = session_dir / "records"
    if not records_dir.is_dir():
        raise ValueError(f"records directory not found: {records_dir}")

    final_transcript: dict[str, Any] | None = None
    manifest: dict[str, Any] = {}
    manifest_path = session_dir / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            manifest = {}

    reconstructed_items: list[dict[str, Any]] = []
    validated_count = 0
    for path in sorted(records_dir.glob("*.jsonl")):
        if path.name == "_quarantine.jsonl":
            continue
        for line_no, payload in _iter_jsonl(path):
            record = _validate_record(_record_from_line(payload), path=path, line_no=line_no)
            validated_count += 1
            if record.get("schema_version") == "bb.kernel_event.v2":
                item = _transcript_item_from_event(record, len(reconstructed_items))
                if item is not None:
                    reconstructed_items.append(item)
            elif record.get("schema_version") == "bb.session_transcript.v2":
                final_transcript = record

    if final_transcript is None:
        raise ValueError("no bb.session_transcript.v2 record found")

    replay_record = {
        "schema_version": "bb.session_transcript.v2",
        "session_id": final_transcript["session_id"],
        "items": reconstructed_items,
        "metadata": {"snapshot_reason": "replay"},
    }
    if final_transcript.get("run_id"):
        replay_record["run_id"] = final_transcript.get("run_id")
    if final_transcript.get("event_cursor") is not None:
        replay_record["event_cursor"] = final_transcript.get("event_cursor")
    replay_record = finalize_record(get_spec("bb.session_transcript.v2"), replay_record, validate=True)

    final_digest = _transcript_content_digest(final_transcript)
    replay_digest = _transcript_content_digest(replay_record)
    manifest_digest = manifest.get("session_transcript_digest")
    ok = replay_digest == final_digest and manifest_digest == final_digest
    return {
        "ok": ok,
        "transcript_digest": replay_digest,
        "final_transcript_digest": final_digest,
        "manifest_transcript_digest": manifest_digest,
        "validated_record_count": validated_count,
        "reconstructed_items": len(reconstructed_items),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay a BreadBoard session from runtime records.")
    parser.add_argument("--session-dir", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = replay_session_from_records(args.session_dir)
    except Exception as exc:  # noqa: BLE001
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print("ok" if result.get("ok") else "mismatch")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
