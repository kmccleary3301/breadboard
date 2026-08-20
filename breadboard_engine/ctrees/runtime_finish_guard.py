from __future__ import annotations

from typing import Any, Dict


def evaluate_runtime_finish_guard(
    state: Dict[str, Any],
    *,
    grounded_completion: bool,
    verified_completion: bool,
    require_verified_completion: bool,
) -> Dict[str, Any]:
    phase = str(state.get("phase") or "")
    if phase != "close":
        return {"allowed": False, "reason": "phase_not_close"}
    if not grounded_completion:
        return {"allowed": False, "reason": "grounding_missing"}
    if require_verified_completion and not verified_completion:
        return {"allowed": False, "reason": "verification_missing"}
    return {"allowed": True, "reason": "finish_guard_satisfied"}

