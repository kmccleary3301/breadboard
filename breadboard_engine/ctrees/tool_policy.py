from __future__ import annotations

from typing import Any, Dict, List


_PHASE_TOOL_CLASSES = {
    "localize": ["read", "shell_inspect"],
    "commit_edit": ["read", "shell_inspect", "todo"],
    "edit": ["apply_patch", "write_file", "read"],
    "verify": ["shell_verify", "read"],
    "close": ["shell_verify"],
}


def allowed_tool_classes_for_phase(phase: str) -> List[str]:
    return list(_PHASE_TOOL_CLASSES.get(str(phase), []))


def build_phase_tool_policy(phase: str) -> Dict[str, Any]:
    return {
        "phase": str(phase),
        "allowed_tool_classes": allowed_tool_classes_for_phase(phase),
    }


def should_force_edit_commit(state: Dict[str, Any], *, max_localize_turns: int) -> bool:
    return str(state.get("phase") or "") == "localize" and int(state.get("inspection_turns") or 0) >= int(
        max_localize_turns
    )

