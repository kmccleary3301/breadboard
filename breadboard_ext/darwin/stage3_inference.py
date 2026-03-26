from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from breadboard_ext.darwin.stage4 import (
    DEFAULT_STAGE4_FILTER_ROUTE,
    DEFAULT_STAGE4_STRONG_ROUTE,
    DEFAULT_STAGE4_WORKER_ROUTE,
    resolve_stage4_route,
    validate_stage4_claim_eligibility,
    validate_stage4_matched_budget_pair,
)

DEFAULT_WORKER_ROUTE = DEFAULT_STAGE4_WORKER_ROUTE
DEFAULT_FILTER_ROUTE = DEFAULT_STAGE4_FILTER_ROUTE
DEFAULT_STRONG_ROUTE = DEFAULT_STAGE4_STRONG_ROUTE

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
    route = resolve_stage4_route(task_class=task_class, role=role, stronger_tier=stronger_tier, actual_provider_used=False)
    profile = ROUTE_PROFILES[route["route_id"]]
    route["role"] = profile["role"]
    route["cost_classification"] = profile["cost_classification"]
    return route


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
    cost_source = "estimated_from_pricing_table" if route.get("execution_mode") == "live" else "scaffold_placeholder"
    claim_check = validate_stage4_claim_eligibility(
        {
            "execution_mode": route["execution_mode"],
            "cost_source": cost_source,
        }
    )
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
        "route_class": route["route_class"],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
        "estimated_cost_usd": 0.0,
        "cost_classification": route["cost_classification"],
        "cost_source": cost_source,
        "claim_eligible": claim_check.ok,
        "claim_ineligible_reason": claim_check.reason,
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
        "route_class": route["route_class"],
        "route_role": route["role"],
        "execution_mode": route["execution_mode"],
        "claim_eligible": route["claim_eligible"],
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
    result = validate_stage4_matched_budget_pair(baseline=baseline, candidate=candidate)
    return MatchedBudgetCheck(ok=result.ok, reason=result.reason)
