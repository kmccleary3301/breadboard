from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import OUT_DIR, load_json, write_json, write_text  # noqa: E402


TRANSFER = OUT_DIR / "bounded_transfer_outcomes_v0.json"
OUT_JSON = OUT_DIR / "failed_transfer_taxonomy_v0.json"
OUT_MD = OUT_DIR / "failed_transfer_taxonomy_v0.md"


def build_stage4_failed_transfer_taxonomy() -> dict[str, str | int]:
    transfer = load_json(TRANSFER)
    failed_rows = [row for row in transfer.get("rows") or [] if row["transfer_status"] != "retained"]
    reason_counts = Counter(str(row["invalid_reason"] or row["transfer_status"]) for row in failed_rows)
    payload = {
        "schema": "breadboard.darwin.stage4.failed_transfer_taxonomy.v0",
        "row_count": len(failed_rows),
        "reason_counts": dict(reason_counts),
        "rows": failed_rows,
    }
    lines = ["# Stage-4 Failed Transfer Taxonomy", ""]
    for reason, count in sorted(reason_counts.items()):
        lines.append(f"- `{reason}`: `{count}`")
    write_json(OUT_JSON, payload)
    write_text(OUT_MD, "\n".join(lines) + "\n")
    return {"out_json": str(OUT_JSON), "row_count": len(failed_rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-4 failed transfer taxonomy.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage4_failed_transfer_taxonomy()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage4_failed_transfer_taxonomy={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
