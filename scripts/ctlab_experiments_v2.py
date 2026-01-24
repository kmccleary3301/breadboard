#!/usr/bin/env python3
"""C-Trees experiments v2 harness (offline playback + scoring)."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sys
import re

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agentic_coder_prototype.ctrees.context_engine import select_ctree_context
from agentic_coder_prototype.ctrees.store import CTreeStore


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


_QUICK_SCENARIO_IDS = {
    "standard_kv_recall",
    "standard_no_tool_hello",
    "standard_tool_chain",
    "standard_blob_summary",
    "horizon_long_context_recall",
    "discipline_no_tool_math",
    "stress_lite_tight_budget",
}


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _sha256_json(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return _sha256_bytes(canonical.encode("utf-8"))


def _load_events(path: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = json.loads(line)
        if isinstance(parsed, dict) and parsed.get("_type") == "ctree_eventlog_header":
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def _load_run_summary(scenario: Dict[str, Any], events_path: Path) -> Optional[Dict[str, Any]]:
    explicit = scenario.get("run_summary_path")
    if isinstance(explicit, str):
        candidate = Path(explicit)
        if not candidate.is_absolute():
            candidate = _REPO_ROOT / candidate
        if candidate.exists():
            return _load_json(candidate)
    candidates: List[Path] = []
    if events_path.parent.name == "ctrees":
        candidates.append(events_path.parent.parent / "meta" / "run_summary.json")
    candidates.append(events_path.parent / "meta" / "run_summary.json")
    for candidate in candidates:
        if candidate.exists():
            return _load_json(candidate)
    return None


def _normalize_model_name(name: str) -> str:
    if "/" in name:
        return name.split("/")[-1]
    return name


def _load_pricing_snapshot(run_summary: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    explicit = None
    if isinstance(run_summary, dict):
        explicit = run_summary.get("pricing_snapshot")
    if isinstance(explicit, str):
        candidate = Path(explicit)
        if not candidate.is_absolute():
            candidate = _REPO_ROOT / candidate
        if candidate.exists():
            return _load_json(candidate)
    default_path = _REPO_ROOT / "experiments/ctrees/pricing/openai_api_pricing.json"
    if default_path.exists():
        return _load_json(default_path)
    return None


def _extract_usage_summary(run_summary: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(run_summary, dict):
        return None
    usage = run_summary.get("usage_summary")
    if isinstance(usage, dict):
        return usage
    return None


def _compute_usd_estimate(
    usage_summary: Optional[Dict[str, Any]],
    pricing: Optional[Dict[str, Any]],
) -> Optional[float]:
    if not isinstance(usage_summary, dict) or not isinstance(pricing, dict):
        return None
    models = usage_summary.get("by_model")
    if not isinstance(models, dict):
        return None
    prices = pricing.get("models") if isinstance(pricing.get("models"), dict) else {}
    if not prices:
        return None
    total = 0.0
    for model_name, usage in models.items():
        if not isinstance(model_name, str) or not isinstance(usage, dict):
            continue
        normalized = _normalize_model_name(model_name)
        price = prices.get(normalized)
        if not isinstance(price, dict):
            continue
        input_tokens = float(usage.get("input_tokens") or 0)
        output_tokens = float(usage.get("output_tokens") or 0)
        cached_tokens = float(usage.get("cached_input_tokens") or 0)
        input_rate = float(price.get("input") or 0)
        output_rate = float(price.get("output") or 0)
        cached_rate = float(price.get("cached_input") or 0)
        total += (input_tokens / 1_000_000.0) * input_rate
        total += (output_tokens / 1_000_000.0) * output_rate
        if cached_tokens and cached_rate:
            total += (cached_tokens / 1_000_000.0) * cached_rate
    return total


def _extract_total_time_ms(run_summary: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(run_summary, dict):
        return None
    for key in ("total_time_ms", "duration_ms"):
        value = run_summary.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    completion = run_summary.get("completion_summary")
    if isinstance(completion, dict):
        value = completion.get("total_time_ms")
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _last_assistant_message(events: List[Dict[str, Any]]) -> Optional[str]:
    for entry in reversed(events):
        if entry.get("kind") != "message":
            continue
        payload = entry.get("payload")
        if isinstance(payload, dict) and payload.get("role") == "assistant":
            content = payload.get("content")
            if isinstance(content, str):
                return content
            return None
    return None


def _tool_calls_seen(events: List[Dict[str, Any]]) -> List[str]:
    calls: List[str] = []
    for entry in events:
        if entry.get("kind") != "tool_call":
            continue
        payload = entry.get("payload")
        if isinstance(payload, dict):
            name = payload.get("tool") or payload.get("name")
            if isinstance(name, str) and name:
                calls.append(name)
    return calls


def _tool_pairing_check(events: List[Dict[str, Any]]) -> Tuple[bool, str]:
    call_ids: set[str] = set()
    result_ids: set[str] = set()
    pending_no_id: List[str] = []
    unmatched_results = 0
    for entry in events:
        kind = entry.get("kind")
        payload = entry.get("payload") if isinstance(entry, dict) else None
        if kind == "tool_call" and isinstance(payload, dict):
            call_id = payload.get("id") or payload.get("call_id")
            if call_id:
                call_ids.add(str(call_id))
            else:
                name = payload.get("tool") or payload.get("name") or ""
                pending_no_id.append(str(name))
        if kind == "tool_result" and isinstance(payload, dict):
            call_id = payload.get("call_id") or payload.get("id")
            if call_id:
                result_ids.add(str(call_id))
            else:
                if pending_no_id:
                    pending_no_id.pop(0)
                else:
                    unmatched_results += 1
    if call_ids or result_ids:
        missing = sorted(call_ids - result_ids)
        extra = sorted(result_ids - call_ids)
        if missing:
            return False, f"missing_results:{len(missing)}"
        if extra:
            return False, f"extra_results:{len(extra)}"
        return True, "ok"
    if pending_no_id:
        return False, f"missing_results:{len(pending_no_id)}"
    if unmatched_results:
        return False, f"unmatched_results:{unmatched_results}"
    return True, "ok"


def _is_subsequence(expected: List[str], observed: List[str]) -> bool:
    if not expected:
        return True
    idx = 0
    for item in observed:
        if item == expected[idx]:
            idx += 1
            if idx >= len(expected):
                return True
    return False


def _jaccard(a: List[str], b: List[str]) -> float:
    set_a = set(a or [])
    set_b = set(b or [])
    if not set_a and not set_b:
        return 1.0
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return inter / union if union else 1.0


def _turns_from_events(events: List[Dict[str, Any]]) -> List[int]:
    turns = sorted({int(e.get("turn")) for e in events if isinstance(e.get("turn"), int)})
    return turns


def _selection_churn(
    events: List[Dict[str, Any]],
    *,
    selection_config: Dict[str, Any],
    header_config: Dict[str, Any],
    collapse_target: Any,
    collapse_mode: str,
    stage: str,
) -> Dict[str, Any]:
    turns = _turns_from_events(events)
    if len(turns) <= 1:
        return {"pairs": 0, "avg": 0.0, "max": 0.0}
    prev_kept: Optional[List[str]] = None
    churn_vals: List[float] = []
    for t in turns:
        subset = [e for e in events if isinstance(e.get("turn"), int) and e.get("turn") <= t]
        store = CTreeStore.from_events(subset)
        selection, _ = select_ctree_context(
            store,
            selection_config=dict(selection_config),
            header_config=dict(header_config),
            collapse_target=collapse_target,
            collapse_mode=str(collapse_mode),
            stage=str(stage),
            pin_latest=True,
        )
        kept = selection.get("kept_ids") or []
        kept = [str(x) for x in kept if str(x)]
        if prev_kept is not None:
            churn = 1.0 - _jaccard(prev_kept, kept)
            churn_vals.append(churn)
        prev_kept = kept
    if not churn_vals:
        return {"pairs": 0, "avg": 0.0, "max": 0.0}
    return {
        "pairs": len(churn_vals),
        "avg": sum(churn_vals) / len(churn_vals),
        "max": max(churn_vals),
    }


def _score_tool_chain(
    calls: List[str],
    expected_tools: Optional[List[str]],
    allow_tools: Optional[List[str]],
) -> Tuple[bool, str]:
    if expected_tools is None:
        return True, "no_expected_tools"
    if not expected_tools:
        if calls:
            return False, "unexpected_tool_calls"
        return True, "no_tools_expected"
    if allow_tools is not None and allow_tools:
        for name in calls:
            if name not in allow_tools:
                return False, "disallowed_tool"
    if not _is_subsequence(expected_tools, calls):
        return False, "missing_expected_tools"
    return True, "ok"


def _score_tool_sequence(
    calls: List[str],
    expected_sequence: Optional[List[str]],
    allow_tools: Optional[List[str]],
) -> Tuple[bool, str]:
    if expected_sequence is None:
        return True, "no_expected_sequence"
    if not expected_sequence:
        if calls:
            return False, "unexpected_tool_calls"
        return True, "no_tools_expected"
    if allow_tools is not None and allow_tools:
        for name in calls:
            if name not in allow_tools:
                return False, "disallowed_tool"
    if calls != expected_sequence:
        return False, "sequence_mismatch"
    return True, "ok"


def _tool_counts(calls: List[str]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for name in calls:
        counts[name] = counts.get(name, 0) + 1
    return counts


def _score_tool_counts(
    calls: List[str],
    expected_counts: Optional[Dict[str, int]],
    allow_tools: Optional[List[str]],
) -> Tuple[bool, str, Dict[str, int]]:
    observed = _tool_counts(calls)
    if expected_counts is None:
        return True, "no_expected_counts", observed
    if allow_tools is not None and allow_tools:
        for name in calls:
            if name not in allow_tools:
                return False, "disallowed_tool", observed
    if not expected_counts:
        if calls:
            return False, "unexpected_tool_calls", observed
        return True, "no_tools_expected", observed
    for tool, expected in expected_counts.items():
        if observed.get(tool, 0) != int(expected):
            return False, "count_mismatch", observed
    if allow_tools is None:
        extras = [name for name in observed.keys() if name not in expected_counts]
        if extras:
            return False, "unexpected_tool", observed
    return True, "ok", observed


def _score_tool_coverage(
    observed: Dict[str, int],
    expected_counts: Optional[Dict[str, int]],
) -> Tuple[bool, str]:
    if expected_counts is None:
        return True, "no_expected_counts"
    required = [tool for tool, expected in expected_counts.items() if int(expected) > 0]
    missing = [tool for tool in required if observed.get(tool, 0) <= 0]
    if missing:
        return False, "missing_required_tools"
    return True, "ok"


def _score_exact_json(text: Optional[str], expected: Dict[str, Any]) -> Dict[str, Any]:
    if not text:
        return {"ok": False, "reason": "missing_text"}
    try:
        parsed = json.loads(text)
    except Exception:
        return {"ok": False, "reason": "invalid_json"}
    ok = parsed == expected
    return {"ok": ok, "reason": None if ok else "mismatch"}


def _score_contains_text(text: Optional[str], expected: str) -> Dict[str, Any]:
    if not text:
        return {"ok": False, "reason": "missing_text"}
    ok = expected.lower() in text.lower()
    return {"ok": ok, "reason": None if ok else "missing_expected_substring"}


def _score_contains_all(text: Optional[str], expected: List[str]) -> Dict[str, Any]:
    if not text:
        return {"ok": False, "reason": "missing_text"}
    missing: List[str] = []
    for item in expected:
        if str(item).lower() not in text.lower():
            missing.append(str(item))
    return {"ok": len(missing) == 0, "reason": None if not missing else f"missing:{','.join(missing)}"}


def _score_exact_text(text: Optional[str], expected: str) -> Dict[str, Any]:
    if text is None:
        return {"ok": False, "reason": "missing_text"}
    normalized = " ".join(str(text).split())
    expected_norm = " ".join(str(expected).split())
    ok = normalized == expected_norm
    return {"ok": ok, "reason": None if ok else "mismatch"}


def _score_regex_match(text: Optional[str], pattern: str) -> Dict[str, Any]:
    if not text:
        return {"ok": False, "reason": "missing_text"}
    try:
        ok = re.search(pattern, text, flags=re.IGNORECASE) is not None
    except Exception:
        return {"ok": False, "reason": "invalid_regex"}
    return {"ok": ok, "reason": None if ok else "no_match"}


def _is_ok(item: Dict[str, Any]) -> bool:
    ok = bool((item.get("score") or {}).get("ok"))
    ok = ok and bool(item.get("tool_chain_ok", True))
    ok = ok and bool(item.get("tool_sequence_ok", True))
    ok = ok and bool(item.get("tool_count_ok", True))
    ok = ok and bool(item.get("tool_coverage_ok", True))
    ok = ok and bool(item.get("qc_tool_pairing_ok", True))
    return ok


def _score(text: Optional[str], scorer: str, expected: Any) -> Dict[str, Any]:
    if scorer == "exact_json" and isinstance(expected, dict):
        return _score_exact_json(text, expected)
    if scorer == "contains_text" and isinstance(expected, str):
        return _score_contains_text(text, expected)
    if scorer == "contains_all" and isinstance(expected, list):
        return _score_contains_all(text, [str(x) for x in expected])
    if scorer == "exact_text" and isinstance(expected, str):
        return _score_exact_text(text, expected)
    if scorer == "regex_match" and isinstance(expected, str):
        return _score_regex_match(text, expected)
    return {"ok": False, "reason": "unsupported_scorer"}


def run_experiments(
    registry_path: Path,
    *,
    out_path: Path,
    preset: Optional[str] = None,
    policy_ids: Optional[List[str]] = None,
    tiers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    registry = _load_json(registry_path)
    scenarios = registry.get("scenarios") if isinstance(registry.get("scenarios"), list) else []
    policies = registry.get("policies") if isinstance(registry.get("policies"), list) else []
    if policy_ids:
        policy_ids_set = {str(pid) for pid in policy_ids}
        policies = [p for p in policies if isinstance(p, dict) and str(p.get("id")) in policy_ids_set]
    if preset == "quick":
        scenarios = [s for s in scenarios if isinstance(s, dict) and s.get("id") in _QUICK_SCENARIO_IDS]
    if preset == "offline":
        scenarios = [s for s in scenarios if isinstance(s, dict) and str(s.get("tier")) == "offline"]
    if tiers:
        tier_set = {str(t).lower() for t in tiers}
        scenarios = [
            s for s in scenarios if isinstance(s, dict) and str(s.get("tier") or "").lower() in tier_set
        ]

    results: List[Dict[str, Any]] = []
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            continue
        scenario_id = scenario.get("id")
        scenario_path = scenario.get("path")
        if not isinstance(scenario_id, str) or not isinstance(scenario_path, str):
            continue
        events_path = (_REPO_ROOT / scenario_path).resolve()
        if not events_path.exists():
            raise FileNotFoundError(f"Scenario not found: {events_path}")
        events = _load_events(events_path)
        scenario_sha256 = _sha256_file(events_path)
        store = CTreeStore.from_events(events)
        last_message = _last_assistant_message(events)
        score = _score(last_message, str(scenario.get("scorer")), scenario.get("expected"))
        tier = scenario.get("tier") or "standard"
        run_summary = _load_run_summary(scenario, events_path)
        pricing_snapshot = _load_pricing_snapshot(run_summary)
        usage_summary = _extract_usage_summary(run_summary)
        usd_estimate = _compute_usd_estimate(usage_summary, pricing_snapshot)
        total_time_ms = _extract_total_time_ms(run_summary)
        metrics_path = scenario.get("metrics_turns_path")
        if isinstance(metrics_path, str):
            metrics_path = Path(metrics_path)
            if not metrics_path.is_absolute():
                metrics_path = _REPO_ROOT / metrics_path
        else:
            metrics_path = None
        metrics_rows = _load_metrics_turns(metrics_path) if metrics_path else []
        prefix_stability = _prefix_stability(metrics_rows) if metrics_rows else None
        latency_proxy = _latency_proxy(metrics_rows) if metrics_rows else None
        expected_tools = scenario.get("expected_tools")
        expected_sequence = scenario.get("expected_tool_sequence")
        expected_counts = scenario.get("expected_tool_counts")
        allow_tools = scenario.get("allow_tools")
        tool_calls = _tool_calls_seen(events)
        qc_tool_pairing_ok, qc_tool_pairing_reason = _tool_pairing_check(events)
        tool_chain_ok, tool_chain_reason = _score_tool_chain(
            tool_calls,
            expected_tools if isinstance(expected_tools, list) else None,
            allow_tools if isinstance(allow_tools, list) else None,
        )
        tool_sequence_ok, tool_sequence_reason = _score_tool_sequence(
            tool_calls,
            expected_sequence if isinstance(expected_sequence, list) else None,
            allow_tools if isinstance(allow_tools, list) else None,
        )
        tool_count_ok, tool_count_reason, observed_counts = _score_tool_counts(
            tool_calls,
            expected_counts if isinstance(expected_counts, dict) else None,
            allow_tools if isinstance(allow_tools, list) else None,
        )
        tool_coverage_ok, tool_coverage_reason = _score_tool_coverage(
            observed_counts,
            expected_counts if isinstance(expected_counts, dict) else None,
        )

        for policy in policies:
            if not isinstance(policy, dict):
                continue
            policy_id = policy.get("id")
            if not isinstance(policy_id, str):
                continue
            policy_sha256 = _sha256_json(policy)
            selection_config = policy.get("selection_config") if isinstance(policy.get("selection_config"), dict) else {}
            header_config = policy.get("header_config") if isinstance(policy.get("header_config"), dict) else {}
            collapse_target = policy.get("collapse_target")
            collapse_mode = policy.get("collapse_mode") or "all_but_last"
            stage = policy.get("stage") or "FROZEN"

            selection, _compiled = select_ctree_context(
                store,
                selection_config=dict(selection_config),
                header_config=dict(header_config),
                collapse_target=collapse_target,
                collapse_mode=str(collapse_mode),
                stage=str(stage),
                pin_latest=True,
            )
            churn = _selection_churn(
                events,
                selection_config=dict(selection_config),
                header_config=dict(header_config),
                collapse_target=collapse_target,
                collapse_mode=str(collapse_mode),
                stage=str(stage),
            )
            results.append(
                {
                    "scenario_id": scenario_id,
                    "scenario_sha256": scenario_sha256,
                    "tier": tier,
                    "policy_id": policy_id,
                    "policy_sha256": policy_sha256,
                    "selection_sha256": selection.get("selection_sha256"),
                    "candidate_count": selection.get("candidate_count"),
                    "kept_count": selection.get("kept_count"),
                    "dropped_count": selection.get("dropped_count"),
                    "collapsed_count": selection.get("collapsed_count"),
                    "score": score,
                    "expected_tools": expected_tools if isinstance(expected_tools, list) else None,
                    "expected_tool_sequence": expected_sequence if isinstance(expected_sequence, list) else None,
                    "expected_tool_counts": expected_counts if isinstance(expected_counts, dict) else None,
                    "allow_tools": allow_tools if isinstance(allow_tools, list) else None,
                    "tool_calls_seen": tool_calls,
                    "tool_counts_seen": observed_counts,
                    "tool_chain_ok": tool_chain_ok,
                    "tool_chain_reason": tool_chain_reason,
                    "tool_sequence_ok": tool_sequence_ok,
                    "tool_sequence_reason": tool_sequence_reason,
                    "tool_count_ok": tool_count_ok,
                    "tool_count_reason": tool_count_reason,
                    "tool_coverage_ok": tool_coverage_ok,
                    "tool_coverage_reason": tool_coverage_reason,
                    "qc_tool_pairing_ok": qc_tool_pairing_ok,
                    "qc_tool_pairing_reason": qc_tool_pairing_reason,
                    "total_time_ms": total_time_ms,
                    "usd_estimate": usd_estimate,
                    "prefix_stability": prefix_stability,
                    "latency_proxy_seconds": latency_proxy,
                    "selection_churn": churn,
                }
            )

    payload = {
        "generated_at": _now_iso(),
        "registry": str(registry_path),
        "preset": preset,
        "results": results,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def _summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"by_tier": {}, "by_policy": {}}
    for item in results:
        tier = str(item.get("tier") or "unknown")
        policy = str(item.get("policy_id") or "unknown")
        ok = bool((item.get("score") or {}).get("ok"))
        ok = ok and bool(item.get("tool_chain_ok", True))
        ok = ok and bool(item.get("tool_sequence_ok", True))
        ok = ok and bool(item.get("tool_count_ok", True))
        ok = ok and bool(item.get("tool_coverage_ok", True))
        ok = ok and bool(item.get("qc_tool_pairing_ok", True))
        summary["by_tier"].setdefault(
            tier,
            {
                "ok": 0,
                "count": 0,
                "usd_total": 0.0,
                "usd_samples": 0,
                "time_ms_total": 0.0,
                "time_ms_samples": 0,
                "prefix_stability_total": 0.0,
                "prefix_stability_samples": 0,
            },
        )
        summary["by_policy"].setdefault(
            policy,
            {
                "ok": 0,
                "count": 0,
                "usd_total": 0.0,
                "usd_samples": 0,
                "time_ms_total": 0.0,
                "time_ms_samples": 0,
                "prefix_stability_total": 0.0,
                "prefix_stability_samples": 0,
            },
        )
        summary["by_tier"][tier]["count"] += 1
        summary["by_policy"][policy]["count"] += 1
        if ok:
            summary["by_tier"][tier]["ok"] += 1
            summary["by_policy"][policy]["ok"] += 1
        usd = item.get("usd_estimate")
        if isinstance(usd, (int, float)):
            summary["by_tier"][tier]["usd_total"] += float(usd)
            summary["by_tier"][tier]["usd_samples"] += 1
            summary["by_policy"][policy]["usd_total"] += float(usd)
            summary["by_policy"][policy]["usd_samples"] += 1
        time_ms = item.get("total_time_ms")
        if isinstance(time_ms, (int, float)):
            summary["by_tier"][tier]["time_ms_total"] += float(time_ms)
            summary["by_tier"][tier]["time_ms_samples"] += 1
            summary["by_policy"][policy]["time_ms_total"] += float(time_ms)
            summary["by_policy"][policy]["time_ms_samples"] += 1
        latency_proxy = item.get("latency_proxy_seconds")
        if isinstance(latency_proxy, (int, float)):
            summary["by_tier"][tier].setdefault("latency_proxy_total", 0.0)
            summary["by_tier"][tier].setdefault("latency_proxy_samples", 0)
            summary["by_policy"][policy].setdefault("latency_proxy_total", 0.0)
            summary["by_policy"][policy].setdefault("latency_proxy_samples", 0)
            summary["by_tier"][tier]["latency_proxy_total"] += float(latency_proxy)
            summary["by_tier"][tier]["latency_proxy_samples"] += 1
            summary["by_policy"][policy]["latency_proxy_total"] += float(latency_proxy)
            summary["by_policy"][policy]["latency_proxy_samples"] += 1
        prefix = item.get("prefix_stability")
        if isinstance(prefix, dict):
            rate = prefix.get("stability_rate")
            if isinstance(rate, (int, float)):
                summary["by_tier"][tier]["prefix_stability_total"] += float(rate)
                summary["by_tier"][tier]["prefix_stability_samples"] += 1
                summary["by_policy"][policy]["prefix_stability_total"] += float(rate)
                summary["by_policy"][policy]["prefix_stability_samples"] += 1
    for bucket in (summary["by_tier"], summary["by_policy"]):
        for _, stats in bucket.items():
            if stats.get("usd_samples"):
                stats["usd_avg"] = stats["usd_total"] / stats["usd_samples"]
            if stats.get("time_ms_samples"):
                stats["time_ms_avg"] = stats["time_ms_total"] / stats["time_ms_samples"]
            if stats.get("prefix_stability_samples"):
                stats["prefix_stability_avg"] = (
                    stats["prefix_stability_total"] / stats["prefix_stability_samples"]
                )
            if stats.get("latency_proxy_samples"):
                stats["latency_proxy_avg"] = stats["latency_proxy_total"] / stats["latency_proxy_samples"]
    summary["paired"] = _paired_summary(results)
    return summary


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    values = sorted(values)
    mid = len(values) // 2
    if len(values) % 2 == 1:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2


def _bootstrap_mean_ci(values: List[float], *, samples: int = 2000, seed: int = 0) -> Optional[Tuple[float, float]]:
    if not values:
        return None
    rng = random.Random(seed)
    n = len(values)
    if n == 1:
        return (values[0], values[0])
    draws: List[float] = []
    for _ in range(samples):
        sample = [values[rng.randrange(0, n)] for _ in range(n)]
        draws.append(sum(sample) / n)
    draws.sort()
    low_idx = int(0.025 * samples)
    high_idx = int(0.975 * samples) - 1
    low = draws[max(0, low_idx)]
    high = draws[min(len(draws) - 1, high_idx)]
    return (low, high)


def _sign_test_pvalue(wins: int, losses: int) -> Optional[float]:
    n = wins + losses
    if n <= 0:
        return None
    k = min(wins, losses)
    p = 0.0
    for i in range(0, k + 1):
        p += math.comb(n, i) * (0.5 ** n)
    return min(1.0, 2 * p)


def _paired_summary(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_map: Dict[str, Dict[str, List[bool]]] = {}
    for item in results:
        scenario_id = str(item.get("scenario_id") or "")
        policy_id = str(item.get("policy_id") or "")
        if not scenario_id or not policy_id:
            continue
        scenario_map.setdefault(scenario_id, {}).setdefault(policy_id, []).append(_is_ok(item))

    deltas: List[float] = []
    wins = losses = ties = 0
    for scenario_id, policy_map in scenario_map.items():
        baseline_ids = [pid for pid in policy_map if "baseline" in pid or pid == "default"]
        ctrees_ids = [pid for pid in policy_map if "ctree" in pid]
        if not baseline_ids or not ctrees_ids:
            continue
        baseline_vals = [ok for pid in baseline_ids for ok in policy_map[pid]]
        ctrees_vals = [ok for pid in ctrees_ids for ok in policy_map[pid]]
        baseline_rate = _mean([1.0 if ok else 0.0 for ok in baseline_vals]) or 0.0
        ctrees_rate = _mean([1.0 if ok else 0.0 for ok in ctrees_vals]) or 0.0
        delta = ctrees_rate - baseline_rate
        deltas.append(delta)
        if delta > 0:
            wins += 1
        elif delta < 0:
            losses += 1
        else:
            ties += 1

    overall_baseline = []
    overall_ctrees = []
    for item in results:
        policy_id = str(item.get("policy_id") or "")
        if "baseline" in policy_id or policy_id == "default":
            overall_baseline.append(1.0 if _is_ok(item) else 0.0)
        if "ctree" in policy_id:
            overall_ctrees.append(1.0 if _is_ok(item) else 0.0)

    baseline_rate = _mean(overall_baseline)
    ctrees_rate = _mean(overall_ctrees)
    cohen_h = None
    if baseline_rate is not None and ctrees_rate is not None:
        try:
            cohen_h = 2 * math.asin(math.sqrt(ctrees_rate)) - 2 * math.asin(math.sqrt(baseline_rate))
        except Exception:
            cohen_h = None

    return {
        "paired_scenarios": len(deltas),
        "delta_mean": _mean(deltas),
        "delta_median": _median(deltas),
        "delta_ci95": _bootstrap_mean_ci(deltas),
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "sign_test_p": _sign_test_pvalue(wins, losses),
        "baseline_rate": baseline_rate,
        "ctrees_rate": ctrees_rate,
        "cohen_h": cohen_h,
    }


def _baseline_delta_by_tier(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[str, Dict[str, List[bool]]] = {}
    for item in results:
        tier = str(item.get("tier") or "unknown")
        policy_id = str(item.get("policy_id") or "")
        if "baseline" in policy_id or policy_id == "default":
            buckets.setdefault(tier, {}).setdefault("baseline", []).append(_is_ok(item))
        if "ctree" in policy_id:
            buckets.setdefault(tier, {}).setdefault("ctrees", []).append(_is_ok(item))
    rows: List[Dict[str, Any]] = []
    for tier, data in sorted(buckets.items()):
        baseline_vals = data.get("baseline") or []
        ctrees_vals = data.get("ctrees") or []
        if not baseline_vals or not ctrees_vals:
            continue
        baseline_rate = _mean([1.0 if ok else 0.0 for ok in baseline_vals]) or 0.0
        ctrees_rate = _mean([1.0 if ok else 0.0 for ok in ctrees_vals]) or 0.0
        rows.append(
            {
                "tier": tier,
                "baseline_rate": baseline_rate,
                "ctrees_rate": ctrees_rate,
                "delta": ctrees_rate - baseline_rate,
            }
        )
    return rows


def _format_rate(ok: int, total: int) -> str:
    if total <= 0:
        return "0%"
    return f"{(ok / total) * 100:.1f}%"


def _render_report(results: List[Dict[str, Any]], summary: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# C-Trees v2 Experiments Report")
    lines.append("")
    lines.append("## Summary by tier")
    for tier, stats in sorted(summary.get("by_tier", {}).items()):
        ok = int(stats.get("ok") or 0)
        count = int(stats.get("count") or 0)
        rate = _format_rate(ok, count)
        usd_avg = stats.get("usd_avg")
        time_avg = stats.get("time_ms_avg")
        prefix_avg = stats.get("prefix_stability_avg")
        latency_avg = stats.get("latency_proxy_avg")
        extra = []
        if isinstance(usd_avg, (int, float)):
            extra.append(f"usd_avg=${usd_avg:.6f}")
        if isinstance(time_avg, (int, float)):
            extra.append(f"time_ms_avg={time_avg:.1f}")
        if isinstance(prefix_avg, (int, float)):
            extra.append(f"prefix_stability={prefix_avg:.3f}")
        if isinstance(latency_avg, (int, float)):
            extra.append(f"latency_proxy_s={latency_avg:.2f}")
        suffix = f" ({', '.join(extra)})" if extra else ""
        lines.append(f"- {tier}: {ok}/{count} ok ({rate}){suffix}")
    lines.append("")
    lines.append("## Summary by policy")
    for policy, stats in sorted(summary.get("by_policy", {}).items()):
        ok = int(stats.get("ok") or 0)
        count = int(stats.get("count") or 0)
        rate = _format_rate(ok, count)
        usd_avg = stats.get("usd_avg")
        time_avg = stats.get("time_ms_avg")
        prefix_avg = stats.get("prefix_stability_avg")
        latency_avg = stats.get("latency_proxy_avg")
        extra = []
        if isinstance(usd_avg, (int, float)):
            extra.append(f"usd_avg=${usd_avg:.6f}")
        if isinstance(time_avg, (int, float)):
            extra.append(f"time_ms_avg={time_avg:.1f}")
        if isinstance(prefix_avg, (int, float)):
            extra.append(f"prefix_stability={prefix_avg:.3f}")
        if isinstance(latency_avg, (int, float)):
            extra.append(f"latency_proxy_s={latency_avg:.2f}")
        suffix = f" ({', '.join(extra)})" if extra else ""
        lines.append(f"- {policy}: {ok}/{count} ok ({rate}){suffix}")
    lines.append("")
    lines.append("## Baseline vs C-Trees")
    baseline_ids = {pid for pid in summary.get("by_policy", {}) if "baseline" in pid or pid == "default"}
    ctrees_ids = {pid for pid in summary.get("by_policy", {}) if "ctree" in pid}
    def _group_rate(policy_ids: set[str]) -> str:
        if not policy_ids:
            return "n/a"
        ok = 0
        count = 0
        for item in results:
            policy = item.get("policy_id")
            if policy not in policy_ids:
                continue
            count += 1
            if _is_ok(item):
                ok += 1
        if count == 0:
            return "n/a"
        return f"{ok}/{count} ({(ok / count) * 100:.1f}%)"
    lines.append(f"- baseline: {_group_rate(baseline_ids)}")
    lines.append(f"- ctrees: {_group_rate(ctrees_ids)}")
    lines.append("")
    lines.append("## Baseline deltas by tier")
    delta_rows = _baseline_delta_by_tier(results)
    if not delta_rows:
        lines.append("- n/a")
    else:
        lines.append("| tier | baseline_rate | ctrees_rate | delta |")
        lines.append("| --- | --- | --- | --- |")
        for row in delta_rows:
            lines.append(
                f"| {row['tier']} | {row['baseline_rate']:.3f} | {row['ctrees_rate']:.3f} | {row['delta']:+.3f} |"
            )
    lines.append("")
    lines.append("## Paired baseline vs C-Trees (scenario-level)")
    paired = summary.get("paired") if isinstance(summary.get("paired"), dict) else {}
    paired_count = int(paired.get("paired_scenarios") or 0)
    if paired_count <= 0:
        lines.append("- n/a (no paired scenarios found)")
    else:
        delta_mean = paired.get("delta_mean")
        delta_median = paired.get("delta_median")
        ci = paired.get("delta_ci95")
        wins = paired.get("wins")
        losses = paired.get("losses")
        ties = paired.get("ties")
        sign_p = paired.get("sign_test_p")
        baseline_rate = paired.get("baseline_rate")
        ctrees_rate = paired.get("ctrees_rate")
        cohen_h = paired.get("cohen_h")
        lines.append(f"- paired_scenarios: {paired_count}")
        if isinstance(baseline_rate, (int, float)) and isinstance(ctrees_rate, (int, float)):
            lines.append(f"- baseline_rate: {baseline_rate:.3f}")
            lines.append(f"- ctrees_rate: {ctrees_rate:.3f}")
        if isinstance(delta_mean, (int, float)):
            lines.append(f"- delta_mean: {delta_mean:+.3f}")
        if isinstance(delta_median, (int, float)):
            lines.append(f"- delta_median: {delta_median:+.3f}")
        if isinstance(ci, (list, tuple)) and len(ci) == 2:
            low, high = ci
            if isinstance(low, (int, float)) and isinstance(high, (int, float)):
                lines.append(f"- delta_ci95: [{low:+.3f}, {high:+.3f}]")
        if isinstance(wins, int) and isinstance(losses, int) and isinstance(ties, int):
            lines.append(f"- wins/losses/ties: {wins}/{losses}/{ties}")
        if isinstance(sign_p, (int, float)):
            lines.append(f"- sign_test_p: {sign_p:.4f}")
        if isinstance(cohen_h, (int, float)):
            lines.append(f"- cohen_h: {cohen_h:+.3f}")
    lines.append("")
    lines.append("## Failures (first 20)")
    failure_count = 0
    for item in results:
        ok = bool((item.get("score") or {}).get("ok"))
        ok = ok and bool(item.get("tool_chain_ok", True))
        ok = ok and bool(item.get("tool_sequence_ok", True))
        ok = ok and bool(item.get("tool_count_ok", True))
        ok = ok and bool(item.get("tool_coverage_ok", True))
        ok = ok and bool(item.get("qc_tool_pairing_ok", True))
        if ok:
            continue
        reasons = []
        score = item.get("score") or {}
        if not score.get("ok", True):
            reasons.append(f"score:{score.get('reason')}")
        if not item.get("tool_chain_ok", True):
            reasons.append(f"tool_chain:{item.get('tool_chain_reason')}")
        if not item.get("tool_sequence_ok", True):
            reasons.append(f"tool_sequence:{item.get('tool_sequence_reason')}")
        if not item.get("tool_count_ok", True):
            reasons.append(f"tool_count:{item.get('tool_count_reason')}")
        if not item.get("tool_coverage_ok", True):
            reasons.append(f"tool_coverage:{item.get('tool_coverage_reason')}")
        if not item.get("qc_tool_pairing_ok", True):
            reasons.append(f"tool_pairing:{item.get('qc_tool_pairing_reason')}")
        scenario_id = item.get("scenario_id")
        policy_id = item.get("policy_id")
        lines.append(f"- {scenario_id} / {policy_id}: {', '.join(reasons) if reasons else 'failed'}")
        failure_count += 1
        if failure_count >= 20:
            break
    if failure_count == 0:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def _write_csv(results: List[Dict[str, Any]], out_path: Path) -> None:
    fieldnames = [
        "scenario_id",
        "scenario_sha256",
        "tier",
        "policy_id",
        "policy_sha256",
        "score_ok",
        "score_reason",
        "tool_chain_ok",
        "tool_sequence_ok",
        "tool_count_ok",
        "tool_coverage_ok",
        "qc_tool_pairing_ok",
        "usd_estimate",
        "total_time_ms",
        "selection_sha256",
        "candidate_count",
        "kept_count",
        "dropped_count",
        "collapsed_count",
        "prefix_stability_rate",
        "latency_proxy_seconds",
        "selection_churn_avg",
        "selection_churn_max",
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in results:
            score = item.get("score") or {}
            selection = item.get("selection_churn") or {}
            prefix = item.get("prefix_stability") or {}
            writer.writerow(
                {
                    "scenario_id": item.get("scenario_id"),
                    "scenario_sha256": item.get("scenario_sha256"),
                    "tier": item.get("tier"),
                    "policy_id": item.get("policy_id"),
                    "policy_sha256": item.get("policy_sha256"),
                    "score_ok": score.get("ok"),
                    "score_reason": score.get("reason"),
                    "tool_chain_ok": item.get("tool_chain_ok"),
                    "tool_sequence_ok": item.get("tool_sequence_ok"),
                    "tool_count_ok": item.get("tool_count_ok"),
                    "tool_coverage_ok": item.get("tool_coverage_ok"),
                    "qc_tool_pairing_ok": item.get("qc_tool_pairing_ok"),
                    "usd_estimate": item.get("usd_estimate"),
                    "total_time_ms": item.get("total_time_ms"),
                    "selection_sha256": item.get("selection_sha256"),
                    "candidate_count": item.get("candidate_count"),
                    "kept_count": item.get("kept_count"),
                    "dropped_count": item.get("dropped_count"),
                    "collapsed_count": item.get("collapsed_count"),
                    "prefix_stability_rate": prefix.get("stability_rate"),
                    "selection_churn_avg": selection.get("avg"),
                    "selection_churn_max": selection.get("max"),
                    "latency_proxy_seconds": item.get("latency_proxy_seconds"),
                }
            )


def _prefix_stability(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"pairs": 0, "stable_pairs": 0, "stability_rate": 1.0, "changes": 0}
    rows = sorted(rows, key=lambda r: (r.get("turn") or 0, r.get("call_index") or 0))
    pairs = 0
    stable_pairs = 0
    changes = 0
    prev = None
    for row in rows:
        if prev is not None:
            pairs += 1
            if row.get("selection_sha256") == prev.get("selection_sha256"):
                stable_pairs += 1
            else:
                changes += 1
        prev = row
    return {
        "pairs": pairs,
        "stable_pairs": stable_pairs,
        "changes": changes,
        "stability_rate": (stable_pairs / pairs) if pairs else 1.0,
    }


def _load_metrics_turns(metrics_path: Path) -> List[Dict[str, Any]]:
    if not metrics_path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in metrics_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _latency_proxy(rows: List[Dict[str, Any]]) -> Optional[float]:
    vals: List[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        latency = row.get("latency_seconds")
        if isinstance(latency, (int, float)):
            vals.append(float(latency))
    if not vals:
        return None
    return sum(vals) / len(vals)


def main() -> int:
    parser = argparse.ArgumentParser(description="C-Trees experiments v2 harness.")
    parser.add_argument(
        "--registry",
        default="experiments/ctrees/registry_v2.json",
        help="Path to v2 experiment registry JSON (default: experiments/ctrees/registry_v2.json).",
    )
    parser.add_argument(
        "--out",
        default="experiments/ctrees/results_v2.json",
        help="Output path for results JSON (default: experiments/ctrees/results_v2.json).",
    )
    parser.add_argument(
        "--preset",
        choices=["quick", "offline"],
        help="Optional preset selection (quick, offline).",
    )
    parser.add_argument(
        "--policies",
        nargs="+",
        help="Optional policy ids to include (space-separated).",
    )
    parser.add_argument(
        "--tiers",
        nargs="+",
        help="Optional tier filters to include (space-separated).",
    )
    parser.add_argument("--report", help="Optional markdown report output path.")
    parser.add_argument("--csv", help="Optional CSV output path.")
    parser.add_argument("--summary", action="store_true", help="Print a tier/policy summary.")
    parser.add_argument("--fail-fast", action="store_true", help="Fail on missing scenarios or invalid specs.")
    args = parser.parse_args()
    registry_path = Path(args.registry)
    out_path = Path(args.out)
    if not registry_path.is_absolute():
        registry_path = _REPO_ROOT / registry_path
    if not out_path.is_absolute():
        out_path = _REPO_ROOT / out_path
    try:
        payload = run_experiments(
            registry_path,
            out_path=out_path,
            preset=args.preset,
            policy_ids=args.policies,
            tiers=args.tiers,
        )
    except Exception as exc:
        if args.fail_fast:
            raise
        print(f"[ctlab] error: {exc}", file=sys.stderr)
        return 1
    summary = None
    if args.summary or args.report:
        summary = _summarize(payload.get("results") or [])
    if args.summary and summary is not None:
        print(json.dumps(summary, indent=2, sort_keys=True))
    if args.report and summary is not None:
        report_path = Path(args.report)
        if not report_path.is_absolute():
            report_path = _REPO_ROOT / report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(_render_report(payload.get("results") or [], summary), encoding="utf-8")
        print(f"[ctlab] wrote report {report_path}")
    if args.csv:
        csv_path = Path(args.csv)
        if not csv_path.is_absolute():
            csv_path = _REPO_ROOT / csv_path
        _write_csv(payload.get("results") or [], csv_path)
        print(f"[ctlab] wrote csv {csv_path}")
    print(f"[ctlab] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
