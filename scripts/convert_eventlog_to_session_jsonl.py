from __future__ import annotations

import argparse
from pathlib import Path

from agentic_coder_prototype.state import session_jsonl


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert events.jsonl to session.jsonl (draft).")
    parser.add_argument("--eventlog", required=True, help="Path to events.jsonl")
    parser.add_argument("--out", help="Output path for session.jsonl (default: alongside events.jsonl)")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite output if it exists")
    args = parser.parse_args()

    eventlog_path = Path(args.eventlog).expanduser().resolve()
    if not eventlog_path.exists():
        raise FileNotFoundError(f"Event log not found: {eventlog_path}")

    out_path = Path(args.out).expanduser().resolve() if args.out else eventlog_path.parent / "session.jsonl"

    count = session_jsonl.convert_eventlog_to_session_jsonl(
        eventlog_path=eventlog_path,
        out_path=out_path,
        overwrite=bool(args.overwrite),
    )
    print(f"Converted {count} entries -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
