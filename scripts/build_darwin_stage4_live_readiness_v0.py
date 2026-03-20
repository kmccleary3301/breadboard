from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PILOT_SUMMARY = ROOT / "artifacts" / "darwin" / "stage4" / "live_economics" / "live_economics_pilot_v0.json"
OUT_JSON = ROOT / "artifacts" / "darwin" / "stage4" / "live_economics" / "live_readiness_v0.json"
OUT_MD = ROOT / "artifacts" / "darwin" / "stage4" / "live_economics" / "live_readiness_v0.md"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_stage4_live_readiness() -> dict:
    pilot_summary = _load_json(PILOT_SUMMARY)
    env_presence = {
        "openai_api_key": bool(os.environ.get("OPENAI_API_KEY")),
        "openrouter_api_key": bool(os.environ.get("OPENROUTER_API_KEY")),
        "darwin_stage4_enable_live": bool(os.environ.get("DARWIN_STAGE4_ENABLE_LIVE")),
        "gpt54_mini_input_cost_per_1m": bool(os.environ.get("DARWIN_STAGE4_GPT54_MINI_INPUT_COST_PER_1M")),
        "gpt54_mini_output_cost_per_1m": bool(os.environ.get("DARWIN_STAGE4_GPT54_MINI_OUTPUT_COST_PER_1M")),
        "gpt54_mini_cached_input_cost_per_1m": bool(os.environ.get("DARWIN_STAGE4_GPT54_MINI_CACHED_INPUT_COST_PER_1M")),
    }
    provider_ready = env_presence["openai_api_key"] or env_presence["openrouter_api_key"]
    pricing_ready = (
        env_presence["gpt54_mini_input_cost_per_1m"]
        and env_presence["gpt54_mini_output_cost_per_1m"]
        and env_presence["gpt54_mini_cached_input_cost_per_1m"]
    )
    live_requested = env_presence["darwin_stage4_enable_live"]
    blockers: list[str] = []
    if not provider_ready:
        blockers.append("provider_credentials_missing")
    if not live_requested:
        blockers.append("darwin_stage4_enable_live_missing")
    if not pricing_ready:
        blockers.append("stage4_mini_pricing_env_missing")
    ready_for_live_claims = provider_ready and live_requested and pricing_ready
    payload = {
        "schema": "breadboard.darwin.stage4.live_readiness.v0",
        "provider_ready": provider_ready,
        "pricing_ready": pricing_ready,
        "live_requested": live_requested,
        "ready_for_live_claims": ready_for_live_claims,
        "blockers": blockers,
        "env_presence": env_presence,
        "pilot_ref": str(PILOT_SUMMARY.relative_to(ROOT)),
        "pilot_execution_modes": list(pilot_summary.get("execution_modes") or []),
        "selected_repo_swe_operator_ids": list(pilot_summary.get("selected_repo_swe_operator_ids") or []),
    }
    lines = [
        "# Stage-4 Live Readiness",
        "",
        f"- `provider_ready`: `{provider_ready}`",
        f"- `pricing_ready`: `{pricing_ready}`",
        f"- `live_requested`: `{live_requested}`",
        f"- `ready_for_live_claims`: `{ready_for_live_claims}`",
        f"- `blockers`: `{', '.join(blockers) if blockers else 'none'}`",
        f"- `pilot_execution_modes`: `{', '.join(payload['pilot_execution_modes'])}`",
    ]
    _write_json(OUT_JSON, payload)
    _write_text(OUT_MD, "\n".join(lines) + "\n")
    return {"out_json": str(OUT_JSON), "ready_for_live_claims": ready_for_live_claims, "blocker_count": len(blockers)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Stage-4 live readiness artifact.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = build_stage4_live_readiness()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"stage4_live_readiness={summary['out_json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
