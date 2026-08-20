from __future__ import annotations

import argparse
import json

from scripts.run_darwin_stage5_compounding_pilot_v0 import run_stage5_compounding_pilot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Stage-5 bounded systems compounding pilot.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args([] if argv is None else argv)
    summary = run_stage5_compounding_pilot(lane_id="lane.systems")
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_systems_compounding_pilot={summary['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
