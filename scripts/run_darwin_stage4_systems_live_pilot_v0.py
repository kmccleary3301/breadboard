from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_darwin_stage4_live_economics_pilot_v0 import run_stage4_live_pilot  # noqa: E402


OUT_DIR = ROOT / "artifacts" / "darwin" / "stage4" / "systems_live"


def run_stage4_systems_live_pilot(out_dir: Path = OUT_DIR) -> dict:
    return run_stage4_live_pilot(
        lane_id="lane.systems",
        out_dir=out_dir,
        include_watchdog=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Stage-4 narrow systems live-provider pilot.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_stage4_systems_live_pilot()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage4_systems_live_pilot={summary['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
