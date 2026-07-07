from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--component", required=True)
    parser.add_argument("--phase-dir", required=True, type=Path)
    parser.add_argument("--require-passed", action="store_true")
    args = parser.parse_args()
    matches = list((args.phase_dir / "runs").rglob(f"*{args.component}*.json"))
    if not matches:
        print(json.dumps({"errors": ["component report not found"]}))
        return 2
    report = json.loads(matches[0].read_text())
    errors = []
    if args.require_passed and report.get("passed") is not True:
        errors.append("passed must be true")
    if report.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false")
    print(json.dumps({"report": str(matches[0]), "errors": errors}, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
