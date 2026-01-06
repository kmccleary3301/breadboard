from __future__ import annotations

import pytest

from agentic_coder_prototype.reward.aggregator import aggregate_reward_v1, validate_reward_v1


def test_reward_v1_normalizes_te_le_and_aggregates() -> None:
    reward_payload = {
        "metric_names": ["SVS", "TE", "LE", "TPF_DELTA"],
        "turns": [
            {"turn": 1, "metrics": {"SVS": 1.0, "TE": 2000, "LE": 500, "TPF_DELTA": 0.2}},
            {"turn": 2, "metrics": {"SVS": 0.5, "TE": 4000, "LE": 500, "TPF_DELTA": 0.1}},
        ],
    }
    cfg = {
        "weights": {"SVS": 1.0, "TE": 1.0, "LE": 1.0, "TPF_DELTA": 1.0},
        "terminal": {"pass_all_bonus": 1.0, "final_tpf_weight": 1.0, "winrate_weight": 0.0, "normalized_cost_weight": 0.0},
        "normalization": {"token_budget_by_task_type": {"general": 4000}},
    }
    result = aggregate_reward_v1(
        reward_payload,
        completion_summary={"completed": True},
        config=cfg,
        context={"task_type": "general", "latency_budget_ms": 1000, "initial_tpf": 0.0},
    )
    per_turn = result["per_turn"]
    assert per_turn[0]["metrics_normalized"]["TE"] == 0.5  # 1 - (2000/4000)
    assert per_turn[1]["metrics_normalized"]["TE"] == 0.0  # 1 - min(1, 4000/4000)
    assert per_turn[0]["metrics_normalized"]["LE"] == 0.5  # 1 - (500/1000)
    assert result["terminal"]["final_tpf"] == pytest.approx(0.3)
    assert result["terminal"]["reward"] >= 1.0  # pass_all bonus + final_tpf

    validation = validate_reward_v1(result)
    assert validation["ok"] is True


def test_reward_v1_applies_winrate_weight() -> None:
    reward_payload = {"turns": []}
    cfg = {
        "weights": {},
        "terminal": {
            "pass_all_bonus": 0.0,
            "final_tpf_weight": 0.0,
            "winrate_weight": 2.0,
            "normalized_cost_weight": 0.0,
        },
        "normalization": {"token_budget_by_task_type": {"general": 4000}},
    }
    result = aggregate_reward_v1(
        reward_payload,
        completion_summary={"completed": False},
        config=cfg,
        context={"task_type": "general", "winrate_vs_baseline": 0.5},
    )
    assert result["terminal"]["reward"] == pytest.approx(1.0)


def test_reward_v1_penalties_from_turn_meta() -> None:
    reward_payload = {
        "turns": [
            {
                "turn": 1,
                "metrics": {"SVS": 1.0},
                "meta": {"invalid_diff_block": True, "multi_bash_violation": 2},
            }
        ]
    }
    cfg = {
        "weights": {"SVS": 1.0},
        "terminal": {},
        "normalization": {},
        "penalties": {"invalid_diff_block": -0.5, "multi_bash_violation": -1.0},
    }
    result = aggregate_reward_v1(reward_payload, completion_summary={"completed": False}, config=cfg)
    per_turn = result["per_turn"][0]
    assert per_turn["penalty_total"] == pytest.approx(-2.5)
    assert per_turn["reward"] == pytest.approx(1.0 - 2.5)
