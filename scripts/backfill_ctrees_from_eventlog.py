from __future__ import annotations

import argparse
import json

from agentic_coder_prototype.ctrees.backfill import backfill_ctrees_from_eventlog


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill .breadboard/ctrees artifacts from a session events.jsonl file.")
    parser.add_argument("--eventlog", required=True, help="Path to a session events.jsonl file")
    parser.add_argument(
        "--out",
        default=".breadboard/ctrees",
        help="Output directory for ctrees artifacts (default: .breadboard/ctrees)",
    )
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing artifacts if present")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable JSON summary")

    args = parser.parse_args()

    result = backfill_ctrees_from_eventlog(eventlog_path=args.eventlog, out_dir=args.out, overwrite=bool(args.overwrite))
    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2))
    else:
        counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
        print(f"Backfilled C-Trees to: {result.get('out_dir')}")
        print(f"Extracted events: {result.get('extracted_events')}")
        print(f"Nodes: {counts.get('nodes')}  Events: {counts.get('events')}")
        paths = result.get("paths") if isinstance(result.get("paths"), dict) else {}
        print(f"Events file: {paths.get('events')}")
        print(f"Snapshot file: {paths.get('snapshot')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

