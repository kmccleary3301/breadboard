#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from breadboard.product.evidence.e4.session_replay import (
    _append_transcript_item,
    _iter_jsonl,
    _message_identity,
    _record_from_line,
    _transcript_content_digest,
    _transcript_item_from_event,
    _validate_record,
    replay_session_from_records,
)


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
