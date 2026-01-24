#!/usr/bin/env python3
"""Summarize token usage and estimated cost from an event log."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _extract_usage(payload: Dict[str, Any]) -> Dict[str, Any]:
    prompt = payload.get("prompt_tokens") or payload.get("input_tokens")
    completion = payload.get("completion_tokens") or payload.get("output_tokens")
    total = payload.get("total_tokens")
    if total is None and prompt is not None and completion is not None:
        try:
            total = int(prompt) + int(completion)
        except Exception:
            total = None
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cost_usd": payload.get("cost_usd") or payload.get("cost"),
        "scope": payload.get("scope"),
    }


def _aggregate_usage(events_path: Path) -> Dict[str, Any]:
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    cost_usd = None
    run_scoped = None
    if not events_path.exists():
        return {"usage": totals, "cost_usd": cost_usd, "source": "missing"}
    for line in events_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        payload = event.get("payload")
        if event_type not in {"usage.update", "run.end"}:
            continue
        if not isinstance(payload, dict):
            continue
        usage = _extract_usage(payload)
        if usage.get("scope") == "run":
            run_scoped = usage
        cost_usd = usage.get("cost_usd") if usage.get("cost_usd") is not None else cost_usd
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if value is None:
                continue
            try:
                totals[key] += int(value)
            except Exception:
                pass
    if run_scoped:
        return {"usage": run_scoped, "cost_usd": run_scoped.get("cost_usd"), "source": "run"}
    return {"usage": totals, "cost_usd": cost_usd, "source": "sum"}


def _estimate_cost(usage: Dict[str, Any], pricing: Dict[str, Any]) -> Optional[float]:
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if prompt is None and completion is None:
        return None
    prompt = int(prompt or 0)
    completion = int(completion or 0)
    input_per_1k = pricing.get("input_per_1k")
    output_per_1k = pricing.get("output_per_1k")
    input_per_1m = pricing.get("input_per_1m")
    output_per_1m = pricing.get("output_per_1m")
    if input_per_1m is not None:
        input_per_1k = float(input_per_1m) / 1000.0
    if output_per_1m is not None:
        output_per_1k = float(output_per_1m) / 1000.0
    if input_per_1k is None and output_per_1k is None:
        return None
    cost = 0.0
    if input_per_1k is not None:
        cost += (prompt / 1000.0) * float(input_per_1k)
    if output_per_1k is not None:
        cost += (completion / 1000.0) * float(output_per_1k)
    return cost


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize token usage and cost from events.jsonl.")
    parser.add_argument("--eventlog", help="Path to events.jsonl")
    parser.add_argument("--session-dir", help="Session directory containing events.jsonl")
    parser.add_argument("--pricing", help="Optional pricing JSON file")
    parser.add_argument("--model", help="Model id for pricing lookup")
    parser.add_argument("--out", help="Optional output JSON path")
    args = parser.parse_args()

    events_path = None
    if args.eventlog:
        events_path = Path(args.eventlog).expanduser().resolve()
    elif args.session_dir:
        events_path = Path(args.session_dir).expanduser().resolve() / "events.jsonl"

    if not events_path:
        raise SystemExit("Provide --eventlog or --session-dir")

    usage_payload = _aggregate_usage(events_path)
    usage = usage_payload.get("usage") or {}

    cost_estimate = None
    if args.pricing and args.model:
        pricing_data = _load_json(Path(args.pricing).expanduser().resolve()) or {}
        pricing = pricing_data.get(args.model) if isinstance(pricing_data, dict) else None
        if pricing is None and isinstance(pricing_data, dict):
            pricing = pricing_data.get("default")
        if isinstance(pricing, dict):
            cost_estimate = _estimate_cost(usage, pricing)

    output = {
        "events_path": str(events_path),
        "usage": usage,
        "cost_usd": usage_payload.get("cost_usd"),
        "cost_estimate_usd": cost_estimate,
        "source": usage_payload.get("source"),
    }

    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    else:
        print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
