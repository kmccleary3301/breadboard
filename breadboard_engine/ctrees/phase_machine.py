from __future__ import annotations

from typing import Any, Dict, List


_NEXT = {
    "localize": ["commit_edit", "verify"],
    "commit_edit": ["edit", "verify"],
    "edit": ["verify", "commit_edit"],
    "verify": ["close", "commit_edit", "localize"],
    "close": [],
}


def allowed_next_phases(phase: str) -> List[str]:
    return list(_NEXT.get(str(phase), []))


def can_transition(current_phase: str, target_phase: str) -> bool:
    return str(target_phase) in allowed_next_phases(str(current_phase))


def transition_runtime_phase(
    state: Dict[str, Any],
    *,
    target_phase: str,
    forced: bool = False,
) -> Dict[str, Any]:
    current = str(state.get("phase") or "localize")
    if not forced and not can_transition(current, target_phase):
        raise ValueError(f"invalid phase transition {current} -> {target_phase}")
    out = dict(state)
    out["phase"] = str(target_phase)
    if forced:
        out["forced_phase_transitions"] = int(out.get("forced_phase_transitions") or 0) + 1
    return out

