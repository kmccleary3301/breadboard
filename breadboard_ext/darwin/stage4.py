from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from collections import defaultdict
from typing import Any, Mapping
from urllib import error as urllib_error
from urllib import request as urllib_request


STAGE4_EXECUTION_ENVELOPE_LANES = {"lane.harness", "lane.repo_swe"}
STAGE4_LIVE_AUTHORIZED_LANES = {"lane.repo_swe", "lane.systems"}

DEFAULT_STAGE4_WORKER_ROUTE = "openrouter/openai/gpt-5.4-mini"
DEFAULT_STAGE4_FILTER_ROUTE = "openrouter/openai/gpt-5.4-nano"
DEFAULT_STAGE4_STRONG_ROUTE = "openrouter/openai/gpt-5.4"
DEFAULT_STAGE4_DIRECT_WORKER_ROUTE = "openai/gpt-5.4-mini"
DEFAULT_STAGE4_DIRECT_FILTER_ROUTE = "openai/gpt-5.4-nano"
DEFAULT_STAGE4_DIRECT_STRONG_ROUTE = "openai/gpt-5.4"

_ROUTE_PROFILES = {
    DEFAULT_STAGE4_WORKER_ROUTE: {
        "route_class": "default_worker",
        "provider_model": "openai/gpt-5.4-mini",
        "pricing_env_prefix": "DARWIN_STAGE4_GPT54_MINI",
    },
    DEFAULT_STAGE4_DIRECT_WORKER_ROUTE: {
        "route_class": "default_worker",
        "provider_model": "openai/gpt-5.4-mini",
        "pricing_env_prefix": "DARWIN_STAGE4_GPT54_MINI",
    },
    DEFAULT_STAGE4_FILTER_ROUTE: {
        "route_class": "bulk_filter",
        "provider_model": "openai/gpt-5.4-nano",
        "pricing_env_prefix": "DARWIN_STAGE4_GPT54_NANO",
    },
    DEFAULT_STAGE4_DIRECT_FILTER_ROUTE: {
        "route_class": "bulk_filter",
        "provider_model": "openai/gpt-5.4-nano",
        "pricing_env_prefix": "DARWIN_STAGE4_GPT54_NANO",
    },
    DEFAULT_STAGE4_STRONG_ROUTE: {
        "route_class": "strong_exception",
        "provider_model": "openai/gpt-5.4",
        "pricing_env_prefix": "DARWIN_STAGE4_GPT54_STRONG",
    },
    DEFAULT_STAGE4_DIRECT_STRONG_ROUTE: {
        "route_class": "strong_exception",
        "provider_model": "openai/gpt-5.4",
        "pricing_env_prefix": "DARWIN_STAGE4_GPT54_STRONG",
    },
}

_SEARCH_POLICY_PILOTS = {
    "lane.repo_swe": {
        "policy_id": "darwin.stage4.search_policy.repo_swe.v1",
        "campaign_class": "C0 Scout",
        "max_mutation_arms": 3,
        "repetition_count": 2,
        "topology_priors": [
            {
                "topology_id": "policy.topology.pev_v0",
                "priority": 0.92,
                "reason": "broadest reusable topology signal from Stage-3 repo_swe promotion",
            },
            {
                "topology_id": "policy.topology.single_v0",
                "priority": 0.55,
                "reason": "retained control topology for matched-budget control arms",
            },
        ],
        "operator_priors": [
            {
                "operator_id": "mut.topology.single_to_pev_v1",
                "priority": 0.95,
                "reason": "highest current family prior and strongest Stage-3 promotion evidence",
            },
            {
                "operator_id": "mut.tool_scope.add_git_diff_v1",
                "priority": 0.79,
                "reason": "repo_swe-local leverage without widening runtime truth",
            },
            {
                "operator_id": "mut.budget.class_a_to_class_b_v1",
                "priority": 0.64,
                "reason": "tests budget sensitivity under explicit reserve discipline",
            },
            {
                "operator_id": "mut.policy.shadow_memory_enable_v1",
                "priority": 0.31,
                "reason": "kept below topology and tool scope until stronger replay-backed signal exists",
            },
        ],
        "abort_conditions": [
            "provider_telemetry_missing",
            "support_envelope_digest_mismatch",
            "claim_ineligible_live_rows",
            "matched_budget_invalidity_rate_gt_0_25",
        ],
    },
    "lane.systems": {
        "policy_id": "darwin.stage4.search_policy.systems.v1",
        "campaign_class": "C0 Scout",
        "max_mutation_arms": 2,
        "repetition_count": 2,
        "topology_priors": [
            {
                "topology_id": "policy.topology.pev_v0",
                "priority": 0.89,
                "reason": "strongest bounded topology prior for systems under the current reward-regression evaluator",
            },
            {
                "topology_id": "policy.topology.single_v0",
                "priority": 0.58,
                "reason": "retained control topology for systems matched-budget control arms",
            },
        ],
        "operator_priors": [
            {
                "operator_id": "mut.topology.single_to_pev_v1",
                "priority": 0.91,
                "reason": "highest current systems-family prior from the Stage-3 mutation canary",
            },
            {
                "operator_id": "mut.policy.shadow_memory_enable_v1",
                "priority": 0.62,
                "reason": "bounded policy-family probe for systems without widening runtime truth",
            },
        ],
        "abort_conditions": [
            "provider_telemetry_missing",
            "comparison_envelope_digest_mismatch",
            "claim_ineligible_live_rows",
            "matched_budget_invalidity_rate_gt_0_25",
        ],
    }
}

_CAMPAIGN_CLASS_RULES = {
    "C0 Scout": {"max_mutation_arms": 3, "repetition_count": 2, "replication_reserve_fraction": 0.20, "control_reserve_fraction": 0.10},
    "C1 Discovery": {"max_mutation_arms": 3, "repetition_count": 3, "replication_reserve_fraction": 0.25, "control_reserve_fraction": 0.10},
    "C2 Validation": {"max_mutation_arms": 2, "repetition_count": 4, "replication_reserve_fraction": 0.30, "control_reserve_fraction": 0.15},
}


@dataclass(frozen=True)
class Stage4MatchedBudgetCheck:
    ok: bool
    reason: str | None = None


@dataclass(frozen=True)
class Stage4PowerSignal:
    positive: bool
    signal_class: str


def stage4_provider_ready() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY"))


def stage4_live_execution_requested() -> bool:
    return str(os.environ.get("DARWIN_STAGE4_ENABLE_LIVE") or "").strip().lower() in {"1", "true", "yes", "on"}


def stage4_live_lane_authorized(lane_id: str) -> bool:
    return lane_id in STAGE4_LIVE_AUTHORIZED_LANES


def resolve_stage4_route(
    *,
    task_class: str,
    role: str = "worker",
    stronger_tier: bool = False,
    actual_provider_used: bool = False,
) -> dict[str, Any]:
    if stronger_tier:
        route_id = DEFAULT_STAGE4_STRONG_ROUTE
    elif role == "filter":
        route_id = DEFAULT_STAGE4_FILTER_ROUTE
    else:
        route_id = DEFAULT_STAGE4_WORKER_ROUTE
    profile = _ROUTE_PROFILES[route_id]
    execution_mode = "live" if actual_provider_used and stage4_provider_ready() else "scaffold"
    return {
        "route_id": route_id,
        "provider_model": profile["provider_model"],
        "route_class": profile["route_class"],
        "task_class": task_class,
        "provider_ready": stage4_provider_ready(),
        "actual_provider_used": bool(actual_provider_used),
        "execution_mode": execution_mode,
        "claim_eligible": False,
        "claim_eligibility_basis": "provider_backed_telemetry_required" if execution_mode == "live" else "execution_mode_not_live",
    }


def _resolve_stage4_fallback_route(route_id: str, *, task_class: str) -> dict[str, Any] | None:
    fallback_lookup = {
        DEFAULT_STAGE4_WORKER_ROUTE: DEFAULT_STAGE4_DIRECT_WORKER_ROUTE,
        DEFAULT_STAGE4_FILTER_ROUTE: DEFAULT_STAGE4_DIRECT_FILTER_ROUTE,
        DEFAULT_STAGE4_STRONG_ROUTE: DEFAULT_STAGE4_DIRECT_STRONG_ROUTE,
    }
    fallback_route_id = fallback_lookup.get(route_id)
    if not fallback_route_id or not os.environ.get("OPENAI_API_KEY"):
        return None
    profile = _ROUTE_PROFILES[fallback_route_id]
    return {
        "route_id": fallback_route_id,
        "provider_model": profile["provider_model"],
        "route_class": profile["route_class"],
        "task_class": task_class,
        "provider_ready": stage4_provider_ready(),
        "actual_provider_used": True,
        "execution_mode": "live",
        "claim_eligible": False,
        "claim_eligibility_basis": "provider_backed_telemetry_required",
    }


def stage4_evaluator_pack_version(*, lane_id: str, task_id: str) -> str:
    normalized_task_id = str(task_id).strip()
    parts = [part for part in normalized_task_id.split(".") if part]
    if len(parts) >= 4 and parts[0] == "task" and parts[2] == "lane":
        task_family = ".".join(parts[:4])
    elif len(parts) >= 3 and parts[0] == "task":
        task_family = ".".join(parts[:3])
    else:
        task_family = normalized_task_id
    task_family = task_family.replace(".", "_").replace("-", "_")
    return f"stage4.evalpack.{lane_id}.{task_family}.v1"


def _stage4_task_family_id(task_id: str) -> str:
    normalized_task_id = str(task_id).strip()
    parts = [part for part in normalized_task_id.split(".") if part]
    if len(parts) >= 4 and parts[0] == "task" and parts[2] == "lane":
        return ".".join(parts[:4])
    elif len(parts) >= 3 and parts[0] == "task":
        return ".".join(parts[:3])
    return normalized_task_id


def build_stage4_support_envelope_digest(
    *,
    lane_id: str,
    task_id: str,
    topology_id: str,
    policy_bundle_id: str,
    budget_class: str,
    allowed_tools: list[str],
    environment_digest: str,
    claim_target: str,
) -> str:
    payload = {
        "lane_id": lane_id,
        "task_id": task_id,
        "topology_id": topology_id,
        "policy_bundle_id": policy_bundle_id,
        "budget_class": budget_class,
        "allowed_tools": list(allowed_tools),
        "environment_digest": environment_digest,
        "claim_target": claim_target,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def build_stage4_comparison_envelope_digest(
    *,
    lane_id: str,
    task_id: str,
    budget_class: str,
    comparison_class: str,
    environment_digest: str,
    claim_target: str,
) -> str:
    payload = {
        "lane_id": lane_id,
        "task_family_id": _stage4_task_family_id(task_id),
        "budget_class": budget_class,
        "comparison_class": comparison_class,
        "environment_digest": environment_digest,
        "claim_target": claim_target,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def build_stage4_budget_envelope(
    *,
    budget_class: str,
    wall_clock_ms: int,
    token_counts: Mapping[str, Any] | None,
    cost_estimate: float | int,
    comparison_class: str,
    route_id: str | None,
    provider_model: str | None,
    execution_mode: str,
    route_class: str,
    cost_source: str,
    support_envelope_digest: str,
    comparison_envelope_digest: str | None,
    evaluator_pack_version: str,
    replication_reserve_fraction: float,
    control_reserve_fraction: float,
) -> dict[str, Any]:
    token_counts_payload = dict(token_counts or {})
    prompt_tokens = int(token_counts_payload.get("prompt_tokens") or token_counts_payload.get("prompt") or 0)
    completion_tokens = int(token_counts_payload.get("completion_tokens") or token_counts_payload.get("completion") or 0)
    total_tokens = int(token_counts_payload.get("total_tokens") or (prompt_tokens + completion_tokens))
    cached_input_tokens = int(token_counts_payload.get("cached_input_tokens") or token_counts_payload.get("cached_tokens") or 0)
    cache_write_tokens = int(token_counts_payload.get("cache_write_tokens") or 0)
    normalized_cost = float(cost_estimate)
    if execution_mode == "live":
        cost_classification = "estimated_route_priced" if normalized_cost > 0.0 else "usage_present_zero_cost"
    else:
        cost_classification = "exact_local_zero" if normalized_cost == 0.0 else "estimated_local_nonzero"
    return {
        "budget_class": str(budget_class),
        "wall_clock_ms": int(wall_clock_ms),
        "token_counts": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cached_input_tokens": cached_input_tokens,
            "cache_write_tokens": cache_write_tokens,
        },
        "cost_estimate": normalized_cost,
        "cost_classification": cost_classification,
        "comparison_class": str(comparison_class),
        "route_id": str(route_id).strip() if route_id else None,
        "provider_model": str(provider_model).strip() if provider_model else None,
        "execution_mode": str(execution_mode),
        "route_class": str(route_class),
        "cost_source": str(cost_source),
        "support_envelope_digest": str(support_envelope_digest),
        "comparison_envelope_digest": str(comparison_envelope_digest).strip() if comparison_envelope_digest else None,
        "evaluator_pack_version": str(evaluator_pack_version),
        "replication_reserve_fraction": float(replication_reserve_fraction),
        "control_reserve_fraction": float(control_reserve_fraction),
        "control_reserve_policy": f"replication={float(replication_reserve_fraction):.2f};control={float(control_reserve_fraction):.2f}",
    }


def consume_execution_envelope_v2(
    execution_plan: Mapping[str, Any],
    *,
    support_envelope_digest: str,
    evaluator_pack_version: str,
) -> dict[str, Any]:
    bindings = dict(execution_plan.get("bindings") or {})
    command = [str(item) for item in bindings.get("command") or []]
    cwd = str(bindings.get("cwd") or "").strip()
    out_dir = str(bindings.get("out_dir") or "").strip()
    task_id = str(bindings.get("task_id") or "").strip()
    budget_class = str(bindings.get("budget_class") or "").strip()
    environment_digest = str(bindings.get("environment_digest") or "").strip()
    tool_bindings = [str(item) for item in bindings.get("tool_bindings") or [] if str(item or "").strip()]
    if not command:
        raise ValueError("execution envelope v2 requires bindings.command")
    if not cwd:
        raise ValueError("execution envelope v2 requires bindings.cwd")
    if not out_dir:
        raise ValueError("execution envelope v2 requires bindings.out_dir")
    if not task_id:
        raise ValueError("execution envelope v2 requires bindings.task_id")
    if not budget_class:
        raise ValueError("execution envelope v2 requires bindings.budget_class")
    if not environment_digest:
        raise ValueError("execution envelope v2 requires bindings.environment_digest")
    return {
        "command": command,
        "cwd": cwd,
        "out_dir": out_dir,
        "task_id": task_id,
        "budget_class": budget_class,
        "environment_digest": environment_digest,
        "tool_bindings": tool_bindings,
        "support_envelope_digest": str(support_envelope_digest),
        "evaluator_pack_version": str(evaluator_pack_version),
        "consumed_fields": [
            "bindings.command",
            "bindings.cwd",
            "bindings.out_dir",
            "bindings.task_id",
            "bindings.budget_class",
            "bindings.tool_bindings",
            "bindings.environment_digest",
            "support_envelope_digest",
            "evaluator_pack_version",
        ],
    }


def validate_stage4_matched_budget_pair(
    *,
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> Stage4MatchedBudgetCheck:
    required_equal_fields = (
        "lane_id",
        "budget_class",
        "comparison_class",
        "route_class",
        "execution_mode",
        "evaluator_pack_version",
        "comparison_envelope_digest",
        "control_reserve_policy",
    )
    for field_name in required_equal_fields:
        if str(baseline.get(field_name) or "") != str(candidate.get(field_name) or ""):
            return Stage4MatchedBudgetCheck(ok=False, reason=f"{field_name}_mismatch")
    return Stage4MatchedBudgetCheck(ok=True, reason=None)


def validate_stage4_claim_eligibility(row: Mapping[str, Any]) -> Stage4MatchedBudgetCheck:
    if str(row.get("execution_mode") or "") != "live":
        return Stage4MatchedBudgetCheck(ok=False, reason="execution_mode_not_live")
    if str(row.get("cost_source") or "") not in {"provider_returned", "estimated_from_pricing_table"}:
        return Stage4MatchedBudgetCheck(ok=False, reason="cost_source_not_provider_backed")
    return Stage4MatchedBudgetCheck(ok=True, reason=None)


def classify_stage4_power_signal(
    *,
    comparison_valid: bool,
    claim_eligible: bool,
    delta_score: float,
    delta_runtime_ms: int,
    delta_cost_usd: float,
) -> Stage4PowerSignal:
    if not comparison_valid:
        return Stage4PowerSignal(positive=False, signal_class="invalid_comparison")
    if not claim_eligible:
        return Stage4PowerSignal(positive=False, signal_class="not_claim_eligible")
    if float(delta_score) > 0.0:
        return Stage4PowerSignal(positive=True, signal_class="score_improved")
    if float(delta_score) < 0.0:
        return Stage4PowerSignal(positive=False, signal_class="score_degraded")
    if int(delta_runtime_ms) < 0:
        return Stage4PowerSignal(positive=True, signal_class="score_retained_runtime_improved")
    if float(delta_cost_usd) < 0.0:
        return Stage4PowerSignal(positive=True, signal_class="score_retained_cost_improved")
    return Stage4PowerSignal(positive=False, signal_class="no_signal")


def build_stage4_campaign_round_record(
    *,
    round_id: str,
    lane_id: str,
    search_policy: Mapping[str, Any],
    selected_arms: list[Mapping[str, Any]],
) -> dict[str, Any]:
    class_rules = _CAMPAIGN_CLASS_RULES[str(search_policy["campaign_class"])]
    return {
        "schema": "breadboard.darwin.stage4.campaign_round.v0",
        "round_id": str(round_id),
        "lane_id": str(lane_id),
        "policy_id": str(search_policy["policy_id"]),
        "policy_digest": str(search_policy["policy_digest"]),
        "campaign_class": str(search_policy["campaign_class"]),
        "max_mutation_arms": int(search_policy["max_mutation_arms"]),
        "repetition_count": int(search_policy["repetition_count"]),
        "replication_reserve_fraction": float(class_rules["replication_reserve_fraction"]),
        "control_reserve_fraction": float(class_rules["control_reserve_fraction"]),
        "selected_arm_ids": [str(row["campaign_arm_id"]) for row in selected_arms],
        "selected_operator_ids": [
            str(row["operator_id"]) for row in selected_arms if str(row.get("control_tag") or "") not in {"control", "watchdog"}
        ],
        "abort_conditions": list(search_policy.get("abort_conditions") or []),
    }


def advance_stage4_search_policy_v1(
    *,
    search_policy: Mapping[str, Any],
    comparison_rows: list[Mapping[str, Any]],
) -> dict[str, Any]:
    next_policy = json.loads(json.dumps(dict(search_policy)))
    lane_rows = [row for row in comparison_rows if str(row.get("lane_id") or "") == str(search_policy["lane_id"])]
    buckets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in lane_rows:
        buckets[str(row["operator_id"])].append(row)

    operator_priors = []
    strong_operator_count = 0
    for operator in next_policy.get("operator_priors") or []:
        operator_id = str(operator["operator_id"])
        rows = buckets.get(operator_id, [])
        valid_rows = [row for row in rows if bool(row.get("comparison_valid"))]
        positive_rows = [row for row in valid_rows if bool(row.get("positive_power_signal"))]
        invalid_rows = [row for row in rows if not bool(row.get("comparison_valid"))]
        priority = float(operator["priority"])
        if positive_rows:
            priority += 0.05
        if rows and len(invalid_rows) / len(rows) > 0.25:
            priority -= 0.08
        priority = max(0.05, min(priority, 0.99))
        if valid_rows and len(positive_rows) / len(valid_rows) >= 0.5:
            strong_operator_count += 1
        updated = dict(operator)
        updated["priority"] = round(priority, 3)
        updated["last_round_valid_count"] = len(valid_rows)
        updated["last_round_positive_count"] = len(positive_rows)
        updated["last_round_invalid_count"] = len(invalid_rows)
        operator_priors.append(updated)

    next_policy["operator_priors"] = sorted(operator_priors, key=lambda row: (-float(row["priority"]), str(row["operator_id"])))
    current_class = str(next_policy["campaign_class"])
    if current_class == "C0 Scout" and strong_operator_count >= 1:
        next_class = "C1 Discovery"
    elif current_class == "C1 Discovery" and strong_operator_count >= 2:
        next_class = "C2 Validation"
    else:
        next_class = current_class
    next_policy["campaign_class"] = next_class
    class_rules = _CAMPAIGN_CLASS_RULES[next_class]
    next_policy["max_mutation_arms"] = int(min(int(next_policy.get("max_mutation_arms") or class_rules["max_mutation_arms"]), class_rules["max_mutation_arms"]))
    next_policy["repetition_count"] = int(class_rules["repetition_count"])
    next_policy["policy_digest"] = hashlib.sha256(json.dumps(next_policy, sort_keys=True).encode("utf-8")).hexdigest()
    return next_policy


def _stage4_pricing_fields(route_id: str) -> tuple[str, str, str]:
    prefix = str(_ROUTE_PROFILES[route_id]["pricing_env_prefix"])
    return (
        f"{prefix}_INPUT_COST_PER_1M",
        f"{prefix}_OUTPUT_COST_PER_1M",
        f"{prefix}_CACHED_INPUT_COST_PER_1M",
    )


def estimate_stage4_route_cost(
    *,
    route_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_input_tokens: int = 0,
) -> tuple[float, str]:
    input_env, output_env, cached_input_env = _stage4_pricing_fields(route_id)
    input_cost_raw = os.environ.get(input_env)
    output_cost_raw = os.environ.get(output_env)
    if input_cost_raw is None or output_cost_raw is None:
        return 0.0, "provider_usage_only"
    cached_input_tokens = max(int(cached_input_tokens), 0)
    cached_input_cost_raw = os.environ.get(cached_input_env)
    if cached_input_tokens > 0 and cached_input_cost_raw is None:
        return 0.0, "cached_pricing_missing"
    input_cost = float(input_cost_raw)
    output_cost = float(output_cost_raw)
    cached_input_cost = float(cached_input_cost_raw) if cached_input_cost_raw is not None else input_cost
    uncached_prompt_tokens = max(int(prompt_tokens) - cached_input_tokens, 0)
    estimate = (
        ((uncached_prompt_tokens / 1_000_000.0) * input_cost)
        + ((cached_input_tokens / 1_000_000.0) * cached_input_cost)
        + ((completion_tokens / 1_000_000.0) * output_cost)
    )
    return round(estimate, 8), "estimated_from_pricing_table"


def _extract_cached_usage_fields(usage: Mapping[str, Any]) -> tuple[int, int]:
    usage_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    cached_tokens = int(usage_details.get("cached_tokens") or 0)
    cache_write_tokens = int(usage_details.get("cache_write_tokens") or 0)
    return cached_tokens, cache_write_tokens


def _parse_openai_response_text(payload: Mapping[str, Any]) -> str:
    direct = str(payload.get("output_text") or "").strip()
    if direct:
        return direct
    for row in payload.get("output") or []:
        for content in row.get("content") or []:
            text = str(content.get("text") or "").strip()
            if text:
                return text
    return ""


def _perform_stage4_live_call(
    *,
    route: Mapping[str, Any],
    prompt: str,
    max_output_tokens: int = 256,
    timeout_s: int = 60,
) -> dict[str, Any]:
    route_id = str(route["route_id"])
    provider_model = str(route["provider_model"])
    if route_id.startswith("openrouter/"):
        api_key = os.environ["OPENROUTER_API_KEY"]
        endpoint = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions")
        payload = {
            "model": provider_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": int(max_output_tokens),
        }
        request_obj = urllib_request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib_request.urlopen(request_obj, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
        choice = ((payload.get("choices") or [{}])[0].get("message") or {})
        usage = payload.get("usage") or {}
        cached_tokens, cache_write_tokens = _extract_cached_usage_fields(usage)
        return {
            "response_id": payload.get("id"),
            "text": str(choice.get("content") or "").strip(),
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
            "cached_input_tokens": cached_tokens,
            "cache_write_tokens": cache_write_tokens,
            "provider_cost_usd": payload.get("total_cost"),
        }
    api_key = os.environ["OPENAI_API_KEY"]
    endpoint = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1/responses")
    model_name = provider_model.split("/", 1)[-1] if provider_model.startswith("openai/") else provider_model
    payload = {
        "model": model_name,
        "input": prompt,
        "max_output_tokens": int(max_output_tokens),
    }
    request_obj = urllib_request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib_request.urlopen(request_obj, timeout=timeout_s) as response:
        response_payload = json.loads(response.read().decode("utf-8"))
    usage = response_payload.get("usage") or {}
    cached_tokens, cache_write_tokens = _extract_cached_usage_fields(usage)
    return {
        "response_id": response_payload.get("id"),
        "text": _parse_openai_response_text(response_payload),
        "prompt_tokens": int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "cached_input_tokens": cached_tokens,
        "cache_write_tokens": cache_write_tokens,
        "provider_cost_usd": response_payload.get("cost_usd"),
    }


def execute_stage4_provider_prompt(
    *,
    lane_id: str,
    task_class: str,
    prompt: str,
    role: str = "worker",
    stronger_tier: bool = False,
    max_output_tokens: int = 256,
) -> dict[str, Any]:
    route = resolve_stage4_route(
        task_class=task_class,
        role=role,
        stronger_tier=stronger_tier,
        actual_provider_used=False,
    )
    if not stage4_live_execution_requested():
        return {
            **route,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "cost_estimate": 0.0,
            "cost_source": "scaffold_placeholder",
            "response_text": "",
            "response_id": None,
            "claim_ineligible_reason": "execution_mode_not_live",
            "live_block_reason": "live_execution_not_requested",
        }
    if not stage4_provider_ready():
        return {
            **route,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "cost_estimate": 0.0,
            "cost_source": "scaffold_placeholder",
            "response_text": "",
            "response_id": None,
            "claim_ineligible_reason": "execution_mode_not_live",
            "live_block_reason": "provider_not_ready",
        }
    if not stage4_live_lane_authorized(lane_id):
        return {
            **route,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "cost_estimate": 0.0,
            "cost_source": "scaffold_placeholder",
            "response_text": "",
            "response_id": None,
            "claim_ineligible_reason": "execution_mode_not_live",
            "live_block_reason": "lane_not_authorized_for_live",
        }
    try:
        live_call = _perform_stage4_live_call(
            route=route,
            prompt=prompt,
            max_output_tokens=max_output_tokens,
        )
        live_route = resolve_stage4_route(
            task_class=task_class,
            role=role,
            stronger_tier=stronger_tier,
            actual_provider_used=True,
        )
    except urllib_error.HTTPError as exc:
        fallback_route = None
        if exc.code == 401 and str(route["route_id"]).startswith("openrouter/"):
            fallback_route = _resolve_stage4_fallback_route(str(route["route_id"]), task_class=task_class)
        if fallback_route is None:
            raise RuntimeError(f"stage4 live provider call failed: {exc}") from exc
        try:
            live_call = _perform_stage4_live_call(
                route=fallback_route,
                prompt=prompt,
                max_output_tokens=max_output_tokens,
            )
        except urllib_error.URLError as fallback_exc:
            raise RuntimeError(f"stage4 live provider call failed: {fallback_exc}") from fallback_exc
        live_route = fallback_route
    except urllib_error.URLError as exc:
        raise RuntimeError(f"stage4 live provider call failed: {exc}") from exc
    prompt_tokens = int(live_call["prompt_tokens"])
    completion_tokens = int(live_call["completion_tokens"])
    total_tokens = int(live_call["total_tokens"] or (prompt_tokens + completion_tokens))
    cached_input_tokens = int(live_call.get("cached_input_tokens") or 0)
    cache_write_tokens = int(live_call.get("cache_write_tokens") or 0)
    provider_cost = live_call.get("provider_cost_usd")
    if provider_cost is None:
        cost_estimate, cost_source = estimate_stage4_route_cost(
            route_id=live_route["route_id"],
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_input_tokens=cached_input_tokens,
        )
    else:
        cost_estimate = float(provider_cost)
        cost_source = "provider_returned"
    claim_check = validate_stage4_claim_eligibility(
        {
            "execution_mode": live_route["execution_mode"],
            "cost_source": cost_source,
        }
    )
    return {
        **live_route,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cached_input_tokens": cached_input_tokens,
            "cache_write_tokens": cache_write_tokens,
        },
        "cost_estimate": float(cost_estimate),
        "cost_source": cost_source,
        "response_text": str(live_call.get("text") or ""),
        "response_id": live_call.get("response_id"),
        "claim_eligible": claim_check.ok,
        "claim_ineligible_reason": claim_check.reason,
        "live_block_reason": None,
    }


def build_stage4_search_policy_v1(
    *,
    lane_id: str,
    budget_class: str,
) -> dict[str, Any]:
    pilot = _SEARCH_POLICY_PILOTS[lane_id]
    payload = {
        "schema": "breadboard.darwin.stage4.search_policy.v1",
        "policy_id": pilot["policy_id"],
        "lane_id": lane_id,
        "budget_class": budget_class,
        "campaign_class": pilot["campaign_class"],
        "worker_route_id": DEFAULT_STAGE4_WORKER_ROUTE,
        "filter_route_id": DEFAULT_STAGE4_FILTER_ROUTE,
        "max_mutation_arms": int(pilot["max_mutation_arms"]),
        "repetition_count": int(pilot["repetition_count"]),
        "topology_priors": list(pilot["topology_priors"]),
        "operator_priors": list(pilot["operator_priors"]),
        "abort_conditions": list(pilot["abort_conditions"]),
    }
    payload["policy_digest"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return payload


def select_stage4_search_policy_arms(
    *,
    search_policy: Mapping[str, Any],
    candidate_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    lane_id = str(search_policy["lane_id"])
    max_mutation_arms = int(search_policy["max_mutation_arms"])
    priority_lookup = {
        str(row["operator_id"]): float(row["priority"])
        for row in search_policy.get("operator_priors") or []
    }
    lane_rows = [row for row in candidate_rows if row["lane_id"] == lane_id]
    other_rows = [row for row in candidate_rows if row["lane_id"] != lane_id]
    control_rows = [dict(row) for row in lane_rows if row["control_tag"] == "control"]
    mutation_rows = [dict(row) for row in lane_rows if row["control_tag"] != "control"]
    ranked_rows = sorted(
        mutation_rows,
        key=lambda row: (-priority_lookup.get(str(row["operator_id"]), -1.0), str(row["operator_id"])),
    )
    selected_rows: list[dict[str, Any]] = []
    for row in control_rows:
        row["search_policy_selection"] = {
            "selected": True,
            "selection_reason": "matched_budget_control",
            "policy_id": search_policy["policy_id"],
            "campaign_class": search_policy["campaign_class"],
            "policy_digest": search_policy["policy_digest"],
            "priority": 1.0,
        }
        selected_rows.append(row)
    for row in ranked_rows[:max_mutation_arms]:
        row["search_policy_selection"] = {
            "selected": True,
            "selection_reason": "operator_priority_rank",
            "policy_id": search_policy["policy_id"],
            "campaign_class": search_policy["campaign_class"],
            "policy_digest": search_policy["policy_digest"],
            "priority": priority_lookup.get(str(row["operator_id"]), 0.0),
        }
        selected_rows.append(row)
    for row in other_rows:
        selected_rows.append(dict(row))
    return selected_rows
