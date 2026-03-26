from __future__ import annotations

from typing import Any, Dict


def build_runtime_state(
    *,
    family: str,
    support_strategy: str,
    phase: str = "localize",
) -> Dict[str, Any]:
    return {
        "schema_version": "phase13_runtime_state_v1",
        "family": family,
        "support_strategy": support_strategy,
        "phase": phase,
        "edit_hypothesis": None,
        "verification_target": None,
        "inspection_turns": 0,
        "edit_attempts": 0,
        "finish_attempts": 0,
        "reanchor_count": 0,
        "forced_phase_transitions": 0,
    }


def commit_edit_hypothesis(state: Dict[str, Any], *, hypothesis: str) -> Dict[str, Any]:
    out = dict(state)
    out["edit_hypothesis"] = str(hypothesis)
    out["phase"] = "edit"
    return out


def record_verification_target(state: Dict[str, Any], *, target: str) -> Dict[str, Any]:
    out = dict(state)
    out["verification_target"] = str(target)
    return out

