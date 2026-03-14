from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.search import build_mutation_operator_registry, build_search_enabled_lane_selection


DEFAULT_OUT_DIR = ROOT / "artifacts" / "darwin" / "search"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_typed_search_core(out_dir: Path = DEFAULT_OUT_DIR) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    mutation_registry = build_mutation_operator_registry()
    lane_selection = build_search_enabled_lane_selection()
    mutation_path = out_dir / "mutation_operator_registry_v1.json"
    selection_path = out_dir / "search_enabled_lane_selection_v1.json"
    _write_json(mutation_path, mutation_registry)
    _write_json(selection_path, lane_selection)
    return {
        "mutation_registry_path": str(mutation_path),
        "lane_selection_path": str(selection_path),
        "operator_count": len(mutation_registry["operators"]),
        "lane_count": len(lane_selection["lanes"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit the DARWIN typed-search core v1 registry artifacts.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_typed_search_core(Path(args.out_dir))
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"mutation_registry={summary['mutation_registry_path']}")
        print(f"lane_selection={summary['lane_selection_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
