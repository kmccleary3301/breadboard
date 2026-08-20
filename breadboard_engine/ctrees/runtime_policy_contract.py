from __future__ import annotations

from typing import Any, Dict, List

from .tool_policy import build_phase_tool_policy


def _phase13_cfg(config: Dict[str, Any] | None) -> Dict[str, Any]:
    cfg = config if isinstance(config, dict) else {}
    phase13 = cfg.get("phase13")
    phase13 = phase13 if isinstance(phase13, dict) else {}
    runtime = phase13.get("runtime_protocol")
    return dict(runtime) if isinstance(runtime, dict) else {}


def runtime_policy_enabled(config: Dict[str, Any] | None) -> bool:
    return bool(_phase13_cfg(config).get("enabled"))


def build_runtime_policy_contract(config: Dict[str, Any] | None) -> Dict[str, Any]:
    cfg = _phase13_cfg(config)
    if not bool(cfg.get("enabled")):
        return {
            "schema_version": "phase13_runtime_policy_contract_v1",
            "enabled": False,
            "family": "",
            "support_strategy": "",
            "phase_sequence": [],
            "phase_policies": [],
            "finish_guard": {},
            "reanchor_policy": {},
        }

    family = str(cfg.get("family") or "candidate_a_runtime_v1").strip() or "candidate_a_runtime_v1"
    variant = str(cfg.get("variant") or family).strip() or family
    support_strategy = str(cfg.get("support_strategy") or "candidate_a").strip() or "candidate_a"
    phase_sequence: List[str] = ["localize", "commit_edit", "edit", "verify", "close"]
    max_localize_turns = int(cfg.get("max_localize_turns") or 2)
    max_edit_attempts_before_reanchor = int(cfg.get("max_edit_attempts_before_reanchor") or 1)
    require_verified_completion = bool(cfg.get("require_verified_completion", True))
    allow_reanchor_on_failed_verify = bool(cfg.get("allow_reanchor_on_failed_verify", True))
    force_edit_hypothesis_by_turn = int(cfg.get("force_edit_hypothesis_by_turn") or 1)
    mandatory_write_by_turn = int(cfg.get("mandatory_write_by_turn") or 2)
    allow_verify_before_edit = bool(cfg.get("allow_verify_before_edit", False))

    prompt_lines = [
        "[Runtime execution contract]",
        f"Family: {family}",
        f"Variant: {variant}",
        f"Support strategy: {support_strategy}",
        "Required phase order: localize -> commit_edit -> edit -> verify -> close.",
        f"Do not stay in localize beyond {max_localize_turns} inspection turns without committing to an edit hypothesis or a decisive verification path.",
        "Do not finish unless the finish guard is satisfied.",
    ]
    if variant.endswith("_v2"):
        prompt_lines.extend(
            [
                f"By turn {force_edit_hypothesis_by_turn}, commit to exactly one edit hypothesis naming the file and intended change.",
                f"By turn {mandatory_write_by_turn}, either apply a minimal patch or explicitly justify a no-edit decision with one grounding command.",
                "Do not use verification as your first move unless you are grounding a no-edit decision.",
                "If you verify before any edit, the next turn must either finish with grounded evidence or commit to a patch immediately.",
                "Treat shell inspection as subordinate to edit commitment; once the likely file is known, stop browsing and act.",
            ]
        )
    elif not allow_verify_before_edit:
        prompt_lines.append("Do not verify before you have either committed to an edit hypothesis or justified a no-edit decision.")
    if allow_reanchor_on_failed_verify:
        prompt_lines.append("Re-anchor only after failed verification, drift, or exhausted edit attempts.")

    return {
        "schema_version": "phase13_runtime_policy_contract_v1",
        "enabled": True,
        "family": family,
        "variant": variant,
        "support_strategy": support_strategy,
        "prompt_block": "\n".join(prompt_lines),
        "phase_sequence": phase_sequence,
        "phase_policies": [build_phase_tool_policy(phase) for phase in phase_sequence],
        "finish_guard": {
            "require_verified_completion": require_verified_completion,
        },
        "reanchor_policy": {
            "allow_reanchor_on_failed_verify": allow_reanchor_on_failed_verify,
            "max_edit_attempts_before_reanchor": max_edit_attempts_before_reanchor,
        },
        "localize_budget": {
            "max_localize_turns": max_localize_turns,
            "force_edit_hypothesis_by_turn": force_edit_hypothesis_by_turn,
            "mandatory_write_by_turn": mandatory_write_by_turn,
            "allow_verify_before_edit": allow_verify_before_edit,
        },
    }
