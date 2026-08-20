from __future__ import annotations

from typing import Any, Dict, List


def _phase12_cfg(config: Dict[str, Any] | None) -> Dict[str, Any]:
    cfg = config if isinstance(config, dict) else {}
    phase12 = cfg.get("phase12")
    phase12 = phase12 if isinstance(phase12, dict) else {}
    controller = phase12.get("live_controller")
    return dict(controller) if isinstance(controller, dict) else {}


def control_contract_enabled(config: Dict[str, Any] | None) -> bool:
    return bool(_phase12_cfg(config).get("enabled"))


def control_contract_variant(config: Dict[str, Any] | None) -> str:
    return str(_phase12_cfg(config).get("variant") or "").strip()


def build_control_contract(config: Dict[str, Any] | None) -> Dict[str, Any]:
    cfg = _phase12_cfg(config)
    if not bool(cfg.get("enabled")):
        return {
            "schema_version": "phase12_control_contract_v1",
            "enabled": False,
            "variant": "",
            "prompt_block": "",
            "rules": [],
            "completion_gate": {},
            "watchdog": {},
            "action_budget": {},
        }

    variant = str(cfg.get("variant") or "control_v1").strip() or "control_v1"
    max_no_progress_turns = int(cfg.get("max_no_progress_turns") or 3)
    require_verification = bool(cfg.get("require_verification", True))
    require_grounding = bool(cfg.get("require_grounding", True))
    max_inspection_turns = int(cfg.get("max_inspection_turns") or 2)
    mandatory_write_by_turn = int(cfg.get("mandatory_write_by_turn") or 4)
    escalation_after_no_progress = int(cfg.get("escalation_after_no_progress") or 2)

    rules: List[str] = []
    if variant.endswith("_v2"):
        rules.extend(
            [
                f"Do at most {max_inspection_turns} read-only inspection turns before you either patch, run one focused verification command, or explicitly conclude no change is needed.",
                f"If no write has happened by turn {mandatory_write_by_turn}, force a closure attempt: either make the best-supported minimal patch or run one decisive verification command and stop browsing.",
                "Do not repeat broad workspace inspection once the active file and likely edit site are known.",
                "If a verification command is run early, use its result to choose the next edit immediately rather than continuing read-only exploration.",
                "If you think no edit is needed, you must still ground that claim with a concrete workspace command result.",
            ]
        )
    else:
        rules.extend(
            [
                "Inspect only until the active code path and first concrete edit are clear.",
                "After initial inspection, switch to action quickly rather than repeating read-only loops.",
                "Before claiming completion, produce grounded workspace evidence.",
            ]
        )
    if require_verification:
        rules.append("Run at least one focused verification step before any completion claim.")
    if require_grounding:
        rules.append("Do not stop with a natural-language summary alone; ungrounded stop does not count as completion.")
    rules.append(
        f"If you make {max_no_progress_turns} consecutive turns without a write or a verification attempt, change tactic immediately."
    )
    if variant.endswith("_v2"):
        rules.append(
            f"If you make {escalation_after_no_progress} consecutive no-progress turns without a write, escalate immediately to a write-or-verify-now decision."
        )

    prompt_lines = [
        "[Controller contract]",
        f"Variant: {variant}",
        "You are operating under Phase 12 control-closure rules.",
        "Execution policy:",
    ]
    prompt_lines.extend(f"- {line}" for line in rules)

    return {
        "schema_version": "phase12_control_contract_v1",
        "enabled": True,
        "variant": variant,
        "prompt_block": "\n".join(prompt_lines),
        "rules": rules,
        "completion_gate": {
            "require_verification": require_verification,
            "require_grounding": require_grounding,
        },
        "watchdog": {
            "max_no_progress_turns": max_no_progress_turns,
        },
        "action_budget": {
            "max_inspection_turns": max_inspection_turns,
            "mandatory_write_by_turn": mandatory_write_by_turn,
            "escalation_after_no_progress": escalation_after_no_progress,
        },
    }
