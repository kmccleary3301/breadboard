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


DEFAULT_RATE = ROOT / "artifacts" / "darwin" / "stage5" / "compounding_rate" / "compounding_rate_v0.json"
DEFAULT_REGISTRY = ROOT / "artifacts" / "darwin" / "stage5" / "family_registry" / "family_registry_v0.json"
DEFAULT_THIRD = ROOT / "artifacts" / "darwin" / "stage5" / "third_family_decision" / "third_family_decision_v0.json"
DEFAULT_TRANSFER = ROOT / "artifacts" / "darwin" / "stage5" / "bounded_transfer" / "bounded_transfer_outcomes_v0.json"
DEFAULT_COMPOSITION = ROOT / "artifacts" / "darwin" / "stage5" / "composition_canary" / "composition_canary_v0.json"
DEFAULT_REPLAY = ROOT / "artifacts" / "darwin" / "stage5" / "replay_posture" / "replay_posture_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "stage5" / "scaled_memo"
OUT_JSON = OUT_DIR / "scaled_memo_v0.json"
OUT_MD = OUT_DIR / "scaled_memo_v0.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_stage5_scaled_memo(
    *,
    compounding_rate_path: Path = DEFAULT_RATE,
    registry_path: Path = DEFAULT_REGISTRY,
    third_family_path: Path = DEFAULT_THIRD,
    transfer_path: Path = DEFAULT_TRANSFER,
    composition_path: Path = DEFAULT_COMPOSITION,
    replay_path: Path = DEFAULT_REPLAY,
    out_dir: Path = OUT_DIR,
) -> dict[str, object]:
    compounding_rate = _load_json(compounding_rate_path)
    registry = _load_json(registry_path)
    third_family = _load_json(third_family_path)
    transfer = _load_json(transfer_path)
    composition = _load_json(composition_path)
    replay = _load_json(replay_path)
    payload = {
        "schema": "breadboard.darwin.stage5.scaled_memo.v0",
        "source_refs": {
            "compounding_rate_ref": path_ref(compounding_rate_path),
            "family_registry_ref": path_ref(registry_path),
            "third_family_decision_ref": path_ref(third_family_path),
            "bounded_transfer_ref": path_ref(transfer_path),
            "composition_canary_ref": path_ref(composition_path),
            "replay_posture_ref": path_ref(replay_path),
        },
        "summary": {
            "primary_lane_id": "lane.systems",
            "challenge_lane_id": "lane.repo_swe",
            "third_family_decision": str(third_family.get("decision") or ""),
            "retained_transfer_count": int(transfer.get("retained_transfer_count") or 0),
            "composition_result": str(composition.get("result") or ""),
            "tracked_family_count": int(registry.get("row_count") or 0),
            "replay_subject_count": int(replay.get("row_count") or 0),
        },
    }
    lines = [
        "# Stage-5 Scaled Compounding Memo",
        "",
        "- Stage 5 now has a round-series compounding surface rather than only a proving-repair surface.",
        f"- Systems remains the primary proving lane, while Repo_SWE remains the bounded challenge lane.",
        f"- Third-family decision: `{payload['summary']['third_family_decision']}`.",
        f"- Retained bounded transfer count: `{payload['summary']['retained_transfer_count']}`.",
        f"- Composition result: `{payload['summary']['composition_result']}`.",
        "",
        "## Claim boundary",
        "",
        "- Stage 5 demonstrates scaled compounding behavior under bounded live economics.",
        "- Stage 5 does not yet demonstrate broad transfer or broad composition viability.",
    ]
    write_json(out_dir / OUT_JSON.name, payload)
    write_text(out_dir / OUT_MD.name, "\n".join(lines) + "\n")
    return {"out_json": str(out_dir / OUT_JSON.name), "retained_transfer_count": payload["summary"]["retained_transfer_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-5 scaled compounding memo.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage5_scaled_memo()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage5_scaled_memo={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
