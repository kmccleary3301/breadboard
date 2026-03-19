from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.contracts import (
    validate_component_ref,
    validate_decision_record,
    validate_evolution_ledger,
)
from breadboard_ext.darwin.ledger import build_evolution_ledger


OUT_PATH = ROOT / "artifacts" / "darwin" / "search" / "evolution_ledger_v0.json"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_evolution_ledger(out_path: Path = OUT_PATH) -> dict:
    payload = build_evolution_ledger()
    component_issues = [issue for row in payload["component_refs"] for issue in validate_component_ref(row)]
    decision_issues = [issue for row in payload["decision_records"] for issue in validate_decision_record(row)]
    ledger_issues = validate_evolution_ledger(payload)
    if component_issues or decision_issues or ledger_issues:
        issues = component_issues + decision_issues + ledger_issues
        raise ValueError("; ".join(f"{issue.path}: {issue.message}" for issue in issues))
    _write_json(out_path, payload)
    return {
        "out_path": str(out_path),
        "component_count": len(payload["component_refs"]),
        "decision_count": len(payload["decision_records"]),
        "case_count": len(payload["reconstructed_cases"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit the DARWIN tranche-1 EvolutionLedger reconstruction artifact.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_evolution_ledger()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"evolution_ledger={summary['out_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
