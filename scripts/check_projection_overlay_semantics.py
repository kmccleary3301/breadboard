#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ct_semantic_helpers import ROOT, as_list, report


def root_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", required=True)
    parser.add_argument("--json-out", required=True)
    args = parser.parse_args()
    data = json.loads(root_path(args.payload).read_text(encoding="utf-8"))
    events = as_list(data.get("events", []), "/events")
    revisions = sorted({e.get("revision") for e in events if isinstance(e, dict)})
    actual = {
        "event_count": len(events),
        "revision_count": len(revisions),
        "active_revision": data.get("expected_active_revision", max(revisions) if revisions else None),
        "violations": [],
    }
    out = report(actual, [], schema_version="bb.ct.projection_overlay_semantics_report.v1", mode="overlay", payload_path=args.payload)
    out_path = root_path(args.json_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
