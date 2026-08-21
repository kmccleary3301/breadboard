from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _clamp(value: Optional[float], low: float, high: float) -> Optional[float]:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return max(low, min(high, v))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


DEFAULT_WEIGHTS: Dict[str, float] = {
    "SVS": 0.5,
    "ACS": 0.5,
    "CPS": 0.5,
    "PAS": 1.4,
    "HMR": 0.6,
    "LED": 1.0,
    "SBS": 0.5,
    "TPF_DELTA": 2.0,
    "TE": 0.125,
    "LE": 0.125,
    "TOE": 0.25,
    "SPA": 0.25,
}

DEFAULT_TERMINAL: Dict[str, float] = {
    "pass_all_bonus": 5.0,
    "final_tpf_weight": 2.0,
    "winrate_weight": 1.0,
    "normalized_cost_weight": -1.0,
}

DEFAULT_NORMALIZATION: Dict[str, Any] = {
    "token_budget_by_task_type": {
        "refactor": 12_000,
        "bugfix": 18_000,
        "function": 6_000,
    },
}


@dataclass
class RewardConfig:
    weights: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    terminal: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_TERMINAL))
    normalization: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_NORMALIZATION))
    penalties: Dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: Optional[Dict[str, Any]]) -> "RewardConfig":
        if not isinstance(payload, dict):
            return cls()
        weights = dict(DEFAULT_WEIGHTS)
        terminal = dict(DEFAULT_TERMINAL)
        normalization = dict(DEFAULT_NORMALIZATION)
        penalties = {}
        try:
            weights.update(payload.get("weights") or {})
        except Exception:
            pass
        try:
            terminal.update(payload.get("terminal") or {})
        except Exception:
            pass
        try:
            normalization.update(payload.get("normalization") or {})
        except Exception:
            pass
        try:
            penalties.update(payload.get("penalties") or {})
        except Exception:
            pass
        return cls(weights=weights, terminal=terminal, normalization=normalization, penalties=penalties)


def _normalize_efficiency(raw_value: Optional[float], budget: Optional[float]) -> Optional[float]:
    if raw_value is None or budget in (None, 0):
        return None
    value = _safe_float(raw_value, default=None)
    if value is None:
        return None
    ratio = value / float(budget)
    return _clamp(1.0 - min(1.0, ratio), 0.0, 1.0)


def normalize_turn_metrics(
    metrics: Dict[str, Any],
    *,
    task_type: str,
    cfg: RewardConfig,
    latency_budget_ms: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    normalized: Dict[str, Optional[float]] = {}

    for key, value in metrics.items():
        try:
            normalized[key] = float(value) if value is not None else None
        except (TypeError, ValueError):
            normalized[key] = None

    # TE/LE currently stored as raw tokens/latency; convert to efficiency if possible.
    token_budget = None
    budgets = cfg.normalization.get("token_budget_by_task_type") or {}
    if isinstance(budgets, dict):
        token_budget = budgets.get(task_type) or budgets.get("general")
    token_budget = _safe_float(token_budget, default=None)

    te_raw = normalized.get("TE")
    te_eff = _normalize_efficiency(te_raw, token_budget)
    if te_eff is not None:
        normalized["TE"] = te_eff

    le_raw = normalized.get("LE")
    le_eff = _normalize_efficiency(le_raw, latency_budget_ms)
    if le_eff is not None:
        normalized["LE"] = le_eff

    # Clamp common bounded metrics.
    for key in ("SVS", "ACS", "CPS", "PAS", "HMR", "SBS", "TOE", "SPA"):
        if key in normalized:
            normalized[key] = _clamp(normalized.get(key), 0.0, 1.0)

    if "TPF_DELTA" in normalized:
        normalized["TPF_DELTA"] = _clamp(normalized.get("TPF_DELTA"), -1.0, 1.0)

    if "LED" in normalized:
        normalized["LED"] = _clamp(normalized.get("LED"), -1.0, 1.0)

    return normalized


def turn_reward(metrics: Dict[str, Optional[float]], weights: Dict[str, float]) -> float:
    reward = 0.0
    for key, weight in weights.items():
        value = metrics.get(key)
        if value is None:
            continue
        reward += float(weight) * float(value)
    return reward


def terminal_reward(
    *,
    pass_all: bool,
    final_tpf: float,
    winrate: float,
    normalized_cost: float,
    terminal_weights: Dict[str, float],
) -> float:
    reward = 0.0
    if pass_all:
        reward += float(terminal_weights.get("pass_all_bonus", 0.0))
    reward += float(terminal_weights.get("final_tpf_weight", 0.0)) * float(final_tpf)
    reward += float(terminal_weights.get("winrate_weight", 0.0)) * float(winrate)
    reward += float(terminal_weights.get("normalized_cost_weight", 0.0)) * float(normalized_cost)
    return reward


def aggregate_reward_v1(
    reward_payload: Dict[str, Any],
    *,
    completion_summary: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    cfg = RewardConfig.from_payload(config)
    ctx = dict(context or {})
    task_type = str(ctx.get("task_type") or "general")
    latency_budget_ms = _safe_float(ctx.get("latency_budget_ms"), default=None)
    initial_tpf = _safe_float(ctx.get("initial_tpf"), default=0.0)
    winrate = _safe_float(ctx.get("winrate_vs_baseline"), default=0.0)

    turns = reward_payload.get("turns") or []
    per_turn: List[Dict[str, Any]] = []
    tpf_sum = 0.0
    te_eff_values: List[float] = []
    le_eff_values: List[float] = []

    for turn in turns:
        metrics = turn.get("metrics") or {}
        meta = turn.get("meta") or {}
        if not isinstance(meta, dict):
            meta = {}
        normalized = normalize_turn_metrics(
            metrics,
            task_type=task_type,
            cfg=cfg,
            latency_budget_ms=latency_budget_ms,
        )
        reward = turn_reward(normalized, cfg.weights)
        penalty_total = 0.0
        penalties_applied: Dict[str, float] = {}
        if cfg.penalties and meta:
            for penalty_name, weight in cfg.penalties.items():
                if penalty_name not in meta:
                    continue
                meta_val = meta.get(penalty_name)
                if isinstance(meta_val, bool):
                    count = 1.0 if meta_val else 0.0
                elif isinstance(meta_val, (int, float)):
                    count = float(meta_val)
                else:
                    count = 0.0
                if count == 0.0:
                    continue
                applied = float(weight) * count
                penalties_applied[penalty_name] = applied
                penalty_total += applied
        if penalty_total:
            reward += penalty_total
        tpf_delta = normalized.get("TPF_DELTA") or 0.0
        tpf_sum += float(tpf_delta)
        if normalized.get("TE") is not None:
            te_eff_values.append(float(normalized["TE"]))
        if normalized.get("LE") is not None:
            le_eff_values.append(float(normalized["LE"]))
        per_turn.append(
            {
                "turn": turn.get("turn"),
                "metrics_raw": dict(metrics),
                "metrics_normalized": dict(normalized),
                "reward": reward,
                "penalties": penalties_applied or None,
                "penalty_total": penalty_total if penalty_total else 0.0,
            }
        )

    final_tpf = _clamp(initial_tpf + tpf_sum, 0.0, 1.0) or 0.0

    if te_eff_values or le_eff_values:
        te_avg = sum(te_eff_values) / max(len(te_eff_values), 1)
        le_avg = sum(le_eff_values) / max(len(le_eff_values), 1)
        normalized_cost = 1.0 - ((te_avg + le_avg) / 2.0)
    else:
        normalized_cost = 0.0

    completed = bool((completion_summary or {}).get("completed"))

    terminal = terminal_reward(
        pass_all=completed,
        final_tpf=final_tpf,
        winrate=winrate,
        normalized_cost=normalized_cost,
        terminal_weights=cfg.terminal,
    )
    episode_return = terminal + sum(item["reward"] for item in per_turn)

    return {
        "config": {
            "weights": cfg.weights,
            "terminal": cfg.terminal,
            "normalization": cfg.normalization,
            "penalties": cfg.penalties,
        },
        "context": {
            "task_type": task_type,
            "latency_budget_ms": latency_budget_ms,
            "initial_tpf": initial_tpf,
            "winrate_vs_baseline": winrate,
        },
        "per_turn": per_turn,
        "terminal": {
            "pass_all": completed,
            "final_tpf": final_tpf,
            "winrate_vs_baseline": winrate,
            "normalized_cost": normalized_cost,
            "reward": terminal,
        },
        "episode_return": episode_return,
    }


def validate_reward_v1(payload: Dict[str, Any]) -> Dict[str, Any]:
    issues: List[str] = []
    per_turn = payload.get("per_turn") or []
    total_tpf_delta = 0.0
    computed_return = 0.0
    for turn in per_turn:
        norm = turn.get("metrics_normalized") or {}
        try:
            computed_return += float(turn.get("reward") or 0.0)
        except (TypeError, ValueError):
            issues.append("turn_reward_not_numeric")
        for key in ("SVS", "ACS", "CPS", "PAS", "HMR", "SBS", "TE", "LE", "TOE", "SPA"):
            value = norm.get(key)
            if value is None:
                continue
            if not (0.0 <= float(value) <= 1.0):
                issues.append(f"{key} out_of_range: {value}")
        if "TPF_DELTA" in norm and norm.get("TPF_DELTA") is not None:
            val = float(norm["TPF_DELTA"])
            if not (-1.0 <= val <= 1.0):
                issues.append(f"TPF_DELTA out_of_range: {val}")
            total_tpf_delta += val
        if "LED" in norm and norm.get("LED") is not None:
            val = float(norm["LED"])
            if not (-1.0 <= val <= 1.0):
                issues.append(f"LED out_of_range: {val}")
    terminal = payload.get("terminal") or {}
    try:
        computed_return += float(terminal.get("reward") or 0.0)
    except (TypeError, ValueError):
        issues.append("terminal_reward_not_numeric")
    final_tpf = terminal.get("final_tpf")
    if final_tpf is not None and not (0.0 <= float(final_tpf) <= 1.0):
        issues.append(f"final_tpf out_of_range: {final_tpf}")
    recorded_return = payload.get("episode_return")
    if recorded_return is not None:
        try:
            recorded_val = float(recorded_return)
            if abs(recorded_val - computed_return) > 1e-6:
                issues.append("episode_return_mismatch")
        except (TypeError, ValueError):
            issues.append("episode_return_not_numeric")
    return {
        "ok": not issues,
        "issues": issues,
        "tpf_delta_sum": total_tpf_delta,
        "computed_return": computed_return,
    }
