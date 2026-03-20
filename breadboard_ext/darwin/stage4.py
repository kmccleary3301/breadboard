from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Mapping


STAGE4_EXECUTION_ENVELOPE_LANES = {"lane.harness", "lane.repo_swe"}

DEFAULT_STAGE4_WORKER_ROUTE = "openrouter/openai/gpt-5.4-mini"
DEFAULT_STAGE4_FILTER_ROUTE = "openrouter/openai/gpt-5.4-nano"
DEFAULT_STAGE4_STRONG_ROUTE = "openrouter/openai/gpt-5.4"

_ROUTE_PROFILES = {
    DEFAULT_STAGE4_WORKER_ROUTE: {
        "route_class": "default_worker",
        "provider_model": "openai/gpt-5.4-mini",
    },
    DEFAULT_STAGE4_FILTER_ROUTE: {
        "route_class": "bulk_filter",
        "provider_model": "openai/gpt-5.4-nano",
    },
    DEFAULT_STAGE4_STRONG_ROUTE: {
        "route_class": "strong_exception",
        "provider_model": "openai/gpt-5.4",
    },
}


@dataclass(frozen=True)
class Stage4MatchedBudgetCheck:
    ok: bool
    reason: str | None = None


def stage4_provider_ready() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY"))


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
    claim_eligible = execution_mode == "live"
    return {
        "route_id": route_id,
        "provider_model": profile["provider_model"],
        "route_class": profile["route_class"],
        "task_class": task_class,
        "provider_ready": stage4_provider_ready(),
        "actual_provider_used": bool(actual_provider_used),
        "execution_mode": execution_mode,
        "claim_eligible": claim_eligible,
    }


def stage4_evaluator_pack_version(*, lane_id: str, task_id: str) -> str:
    return f"stage4.evalpack.{lane_id}.{task_id}.v1"


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
    evaluator_pack_version: str,
    replication_reserve_fraction: float,
    control_reserve_fraction: float,
) -> dict[str, Any]:
    token_counts_payload = dict(token_counts or {})
    prompt_tokens = int(token_counts_payload.get("prompt_tokens") or token_counts_payload.get("prompt") or 0)
    completion_tokens = int(token_counts_payload.get("completion_tokens") or token_counts_payload.get("completion") or 0)
    total_tokens = int(token_counts_payload.get("total_tokens") or (prompt_tokens + completion_tokens))
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
        "support_envelope_digest",
        "task_id",
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
