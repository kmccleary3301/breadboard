from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "artifacts" / "darwin" / "stage3" / "bounded_inference" / "bounded_inference_campaign_v0.json"
OPERATOR_EV = ROOT / "artifacts" / "darwin" / "stage3" / "bounded_inference" / "operator_ev_report_v0.json"
TOPOLOGY_EV = ROOT / "artifacts" / "darwin" / "stage3" / "bounded_inference" / "topology_ev_report_v0.json"
INVALIDITY = ROOT / "artifacts" / "darwin" / "stage3" / "bounded_inference" / "invalidity_summary_v0.json"
OUT_JSON = ROOT / "artifacts" / "darwin" / "stage3" / "bounded_inference" / "verification_bundle_v0.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_verification_bundle() -> dict:
    campaign = _load_json(CAMPAIGN)
    operator_ev = _load_json(OPERATOR_EV)
    topology_ev = _load_json(TOPOLOGY_EV)
    invalidity = _load_json(INVALIDITY)
    payload = {
        "schema": "breadboard.darwin.stage3.verification_bundle.v0",
        "lane_count": campaign["lane_count"],
        "arm_count": campaign["arm_count"],
        "run_count": campaign["run_count"],
        "comparison_count": campaign["comparison_count"],
        "positive_signal_count": campaign["positive_signal_count"],
        "execution_modes": campaign["execution_modes"],
        "artifact_refs": {
            "campaign_ref": str(CAMPAIGN.relative_to(ROOT)),
            "operator_ev_ref": str(OPERATOR_EV.relative_to(ROOT)),
            "topology_ev_ref": str(TOPOLOGY_EV.relative_to(ROOT)),
            "invalidity_ref": str(INVALIDITY.relative_to(ROOT)),
        },
        "invalidity_reasons": invalidity["reason_counts"],
        "operator_row_count": operator_ev["row_count"],
        "topology_row_count": topology_ev["row_count"],
    }
    _write_json(OUT_JSON, payload)
    return {"out_json": str(OUT_JSON), "comparison_count": payload["comparison_count"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-3 bounded-inference verification bundle.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_verification_bundle()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"verification_bundle={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
