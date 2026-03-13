#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List

from _cross_system_eval_v1 import dump_json


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_ROOT = REPO_ROOT / "artifacts" / "benchmarks" / "hilbert_comparison_packs_v2"

PACKS_PATH = REPO_ROOT / "scripts" / "build_hilbert_comparison_packs_v2.py"
sys.path.insert(0, str(PACKS_PATH.parent))
_packs_spec = importlib.util.spec_from_file_location("build_hilbert_comparison_packs_v2", PACKS_PATH)
assert _packs_spec and _packs_spec.loader
packs = importlib.util.module_from_spec(_packs_spec)
_packs_spec.loader.exec_module(packs)


def build_payload() -> Dict[str, Any]:
    entries: List[Dict[str, Any]] = []
    for task_id, reason in sorted(packs.EXCLUDED_TASKS.items()):
        affected_packs = sorted(
            pack_id
            for pack_id, task_ids in packs.PACKS.items()
            if task_id in task_ids
        )
        entries.append(
            {
                "task_id": task_id,
                "reason": reason,
                "affected_packs": affected_packs,
                "affected_pack_count": len(affected_packs),
            }
        )
    return {
        "schema": "breadboard.atp_invalid_extract_ledger.v1",
        "entry_count": len(entries),
        "entries": entries,
    }


def _to_markdown(payload: Dict[str, Any]) -> str:
    lines = [
        "# ATP Invalid Extract Ledger v1",
        "",
        f"- entry_count: `{payload['entry_count']}`",
        "",
        "| task_id | affected packs | reason |",
        "| --- | ---: | --- |",
    ]
    for entry in payload["entries"]:
        lines.append(
            f"| {entry['task_id']} | {entry['affected_pack_count']} | {entry['reason']} |"
        )
    lines.extend(["", "## Pack Mapping", ""])
    for entry in payload["entries"]:
        packs_list = ", ".join(f"`{pack_id}`" for pack_id in entry["affected_packs"]) or "_none_"
        lines.append(f"- `{entry['task_id']}` → {packs_list}")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-json", default=str(DEFAULT_OUT_ROOT / "invalid_extract_ledger_v1.json"))
    parser.add_argument("--out-md", default=str(DEFAULT_OUT_ROOT / "invalid_extract_ledger_v1.md"))
    args = parser.parse_args()
    payload = build_payload()
    out_json = Path(args.out_json).resolve()
    out_md = Path(args.out_md).resolve()
    dump_json(out_json, payload)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(_to_markdown(payload), encoding="utf-8")
    print(f"[atp-invalid-extract-ledger-v1] entries={payload['entry_count']} out_json={out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
