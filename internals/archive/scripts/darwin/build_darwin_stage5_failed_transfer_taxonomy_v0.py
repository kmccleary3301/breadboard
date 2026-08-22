from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import write_json, write_text  # noqa: E402


DEFAULT_TRANSFER = ROOT / "artifacts" / "darwin" / "stage5" / "bounded_transfer" / "bounded_transfer_outcomes_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "failed_transfer_taxonomy"
OUT_JSON = OUT_DIR / "failed_transfer_taxonomy_v0.json"
OUT_MD = OUT_DIR / "failed_transfer_taxonomy_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_stage5_failed_transfer_taxonomy(
    *,
    transfer_path: Path = DEFAULT_TRANSFER,
    out_dir: Path = OUT_DIR,
) -> dict[str, object]:
    transfer = _load_json(transfer_path)
    failed_rows = [row for row in list(transfer.get("rows") or []) if str(row.get("transfer_status") or "") != "retained"]
    reason_counts = Counter(str(row.get("invalid_reason") or row.get("transfer_status") or "unknown") for row in failed_rows)
    payload = {
        "schema": "breadboard.darwin.stage5.failed_transfer_taxonomy.v0",
        "row_count": len(failed_rows),
        "reason_counts": dict(reason_counts),
        "rows": failed_rows,
    }
    lines = ["# Stage-5 Failed Transfer Taxonomy", ""]
    for reason, count in sorted(reason_counts.items()):
        lines.append(f"- `{reason}`: `{count}`")
    write_json(out_dir / OUT_JSON.name, payload)
    write_text(out_dir / OUT_MD.name, "\n".join(lines) + "\n")
    return {"out_json": str(out_dir / OUT_JSON.name), "row_count": len(failed_rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-5 failed transfer taxonomy.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage5_failed_transfer_taxonomy()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_failed_transfer_taxonomy={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
