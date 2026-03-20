from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage3_component_transfer import OUT_DIR, write_json, write_text, load_json


TRANSFER_OUTCOMES = OUT_DIR / "bounded_transfer_outcomes_v0.json"
OUT_JSON = OUT_DIR / "failed_transfer_taxonomy_v0.json"
OUT_MD = OUT_DIR / "failed_transfer_taxonomy_v0.md"


def write_failed_transfer_taxonomy() -> dict[str, str | int]:
    payload = load_json(TRANSFER_OUTCOMES)
    failed_rows = [row for row in payload.get("rows") or [] if row["transfer_status"] != "retained"]
    reason_counts = Counter(row["invalid_reason"] or row["transfer_status"] for row in failed_rows)
    out = {
        "schema": "breadboard.darwin.stage3.failed_transfer_taxonomy.v0",
        "row_count": len(failed_rows),
        "reason_counts": dict(reason_counts),
        "rows": failed_rows,
    }
    lines = ["# Stage-3 Failed Transfer Taxonomy", ""]
    for reason, count in sorted(reason_counts.items()):
        lines.append(f"- `{reason}`: `{count}`")
    write_json(OUT_JSON, out)
    write_text(OUT_MD, "\n".join(lines) + "\n")
    return {"out_json": str(OUT_JSON), "out_md": str(OUT_MD), "row_count": len(failed_rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-3 failed-transfer taxonomy.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_failed_transfer_taxonomy()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"failed_transfer_taxonomy={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
