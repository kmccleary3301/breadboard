from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.stage4_family_program import path_ref, write_json, write_text  # noqa: E402

DEFAULT_TRANSFER = ROOT / "artifacts" / "darwin" / "stage6" / "tranche2" / "broader_transfer_matrix" / "transfer_outcome_summary_v1.json"
DEFAULT_COMPOUNDING = ROOT / "artifacts" / "darwin" / "stage6" / "tranche3" / "broader_compounding" / "broader_compounding_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage6" / "tranche3" / "replay_posture"
OUT_JSON = OUT_DIR / "replay_posture_v0.json"
OUT_MD = OUT_DIR / "replay_posture_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_stage6_replay_posture(
    *,
    transfer_path: Path = DEFAULT_TRANSFER,
    compounding_path: Path = DEFAULT_COMPOUNDING,
    out_dir: Path = OUT_DIR,
) -> dict[str, object]:
    transfer = _load_json(transfer_path)
    compounding = _load_json(compounding_path)
    rows = []
    for row in list(transfer.get("rows") or []):
        rows.append(
            {
                "subject_type": "transfer",
                "subject_id": str(row.get("transfer_case_id") or ""),
                "replay_status": str(row.get("replay_status") or "unknown"),
                "reason": str(row.get("transfer_status") or ""),
            }
        )
    status = "supported" if int(compounding.get("positive_count") or 0) >= 2 else "observed"
    rows.append(
        {
            "subject_type": "broader_compounding",
            "subject_id": "stage6.tranche3.scheduling_retained_family",
            "replay_status": status,
            "reason": "repeated_positive_rounds" if status == "supported" else "single_positive_or_mixed_rounds",
        }
    )
    out = {
        "schema": "breadboard.darwin.stage6.replay_posture.v0",
        "source_refs": {
            "transfer_summary_ref": path_ref(transfer_path),
            "broader_compounding_ref": path_ref(compounding_path),
        },
        "row_count": len(rows),
        "rows": rows,
    }
    write_json(out_dir / OUT_JSON.name, out)
    lines = ["# Stage 6 Replay Posture", ""]
    for row in rows:
        lines.append(f"- `{row['subject_type']}` / `{row['subject_id']}`: `{row['replay_status']}`")
    write_text(out_dir / OUT_MD.name, "\n".join(lines) + "\n")
    return {"out_json": str(out_dir / OUT_JSON.name), "row_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage 6 replay posture surface.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage6_replay_posture()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage6_replay_posture={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
