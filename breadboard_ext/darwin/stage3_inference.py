from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping


DEFAULT_WORKER_ROUTE = "openrouter/openai/gpt-5.4-mini"
DEFAULT_FILTER_ROUTE = "openrouter/openai/gpt-5.4-nano"
DEFAULT_STRONG_ROUTE = "openrouter/openai/gpt-5.4"

ROUTE_PROFILES = {
    DEFAULT_WORKER_ROUTE: {
        "role": "worker",
        "provider_model": "openai/gpt-5.4-mini",
        "cost_classification": "route_declared_cost_unavailable",
    },
    DEFAULT_FILTER_ROUTE: {
        "role": "filter",
        "provider_model": "openai/gpt-5.4-nano",
        "cost_classification": "route_declared_cost_unavailable",
    },
    DEFAULT_STRONG_ROUTE: {
        "role": "strong",
        "provider_model": "openai/gpt-5.4",
        "cost_classification": "route_declared_cost_unavailable",
    },
}


def _rough_token_count(text: str) -> int:
    normalized = str(text or "").strip()
    if not normalized:
        return 0
    return max(1, (len(normalized) + 3) // 4)


def stage3_provider_ready() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY"))


def resolve_stage3_route(*, task_class: str, role: str = "worker", stronger_tier: bool = False) -> dict[str, Any]:
    if stronger_tier:
        route_id = DEFAULT_STRONG_ROUTE
    elif role == "filter":
        route_id = DEFAULT_FILTER_ROUTE
    else:
        route_id = DEFAULT_WORKER_ROUTE
    profile = ROUTE_PROFILES[route_id]
    return {
        "route_id": route_id,
        "provider_model": profile["provider_model"],
        "role": profile["role"],
        "task_class": task_class,
        "provider_ready": stage3_provider_ready(),
        "execution_mode": "live" if stage3_provider_ready() else "scaffold_only",
        "cost_classification": profile["cost_classification"],
    }


def build_stage3_proposal_prompt(
    *,
    lane_id: str,
    operator_id: str,
    topology_id: str,
    budget_class: str,
    target_id: str,
) -> str:
    return (
        f"Stage-3 bounded mutation proposal.\n"
        f"lane={lane_id}\n"
        f"operator={operator_id}\n"
        f"topology={topology_id}\n"
        f"budget_class={budget_class}\n"
        f"target={target_id}\n"
        "Respect the declared support envelope, mutate only the selected bounded locus, and do not widen runtime truth."
    )


def build_stage3_usage_telemetry(
    *,
    lane_id: str,
    operator_id: str,
    route: Mapping[str, Any],
    proposal_prompt: str,
    campaign_arm_id: str,
    repetition_index: int,
    control_tag: str,
    wall_clock_ms: int,
) -> dict[str, Any]:
    prompt_tokens = _rough_token_count(proposal_prompt)
    completion_tokens = 0 if route.get("execution_mode") != "live" else max(8, prompt_tokens // 5)
    total_tokens = prompt_tokens + completion_tokens
    return {
        "schema": "breadboard.darwin.stage3.usage_telemetry.v0",
        "campaign_arm_id": campaign_arm_id,
        "lane_id": lane_id,
        "operator_id": operator_id,
        "repetition_index": repetition_index,
        "control_tag": control_tag,
        "route_id": route["route_id"],
        "provider_model": route["provider_model"],
        "execution_mode": route["execution_mode"],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
        "estimated_cost_usd": 0.0,
        "cost_classification": route["cost_classification"],
        "wall_clock_ms": int(wall_clock_ms),
    }


def build_stage3_campaign_arm(
    *,
    arm_id: str,
    lane_id: str,
    operator_id: str,
    topology_id: str,
    budget_class: str,
    route: Mapping[str, Any],
    repetition_count: int,
    control_tag: str,
    task_class: str,
) -> dict[str, Any]:
    return {
        "schema": "breadboard.darwin.stage3.campaign_arm.v0",
        "campaign_arm_id": arm_id,
        "lane_id": lane_id,
        "operator_id": operator_id,
        "topology_id": topology_id,
        "budget_class": budget_class,
        "route_id": route["route_id"],
        "provider_model": route["provider_model"],
        "route_role": route["role"],
        "execution_mode": route["execution_mode"],
        "repetition_count": int(repetition_count),
        "control_tag": control_tag,
        "task_class": task_class,
    }


def matched_budget_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("lane_id") or ""),
        str(row.get("budget_class") or ""),
        str(row.get("comparison_class") or ""),
        str(row.get("route_id") or ""),
    )


@dataclass(frozen=True)
class MatchedBudgetCheck:
    ok: bool
    reason: str | None = None


def validate_matched_budget_pair(*, baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> MatchedBudgetCheck:
    required_fields = ("lane_id", "budget_class", "comparison_class", "route_id")
    for field_name in required_fields:
        if str(baseline.get(field_name) or "") != str(candidate.get(field_name) or ""):
            return MatchedBudgetCheck(ok=False, reason=f"{field_name}_mismatch")
    return MatchedBudgetCheck(ok=True, reason=None)
