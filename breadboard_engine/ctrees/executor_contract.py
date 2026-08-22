from __future__ import annotations

from typing import Any, Dict


def _phase14_cfg(config: Dict[str, Any] | None) -> Dict[str, Any]:
    cfg = config if isinstance(config, dict) else {}
    phase14 = cfg.get("phase14")
    phase14 = phase14 if isinstance(phase14, dict) else {}
    executor = phase14.get("executor")
    return dict(executor) if isinstance(executor, dict) else {}


def executor_enabled(config: Dict[str, Any] | None) -> bool:
    return bool(_phase14_cfg(config).get("enabled"))


def build_executor_contract(config: Dict[str, Any] | None) -> Dict[str, Any]:
    cfg = _phase14_cfg(config)
    if not bool(cfg.get("enabled")):
        return {
            "schema_version": "phase14_executor_contract_v1",
            "enabled": False,
            "family": "",
            "variant": "",
            "support_strategy": "",
            "phase_order": [],
            "tool_allowlists": {},
            "budgets": {},
            "finish_rules": {},
        }

    family = str(cfg.get("family") or "candidate_a_executor_v1").strip() or "candidate_a_executor_v1"
    variant = str(cfg.get("variant") or family).strip() or family
    support_strategy = str(cfg.get("support_strategy") or "candidate_a").strip() or "candidate_a"
    max_localize_turns = max(int(cfg.get("max_localize_turns") or 1), 1)
    max_commit_turns = max(int(cfg.get("max_commit_turns") or 1), 1)
    allow_no_edit_close = bool(cfg.get("allow_no_edit_close", True))
    require_mark_task_complete = bool(cfg.get("require_mark_task_complete", True))

    return {
        "schema_version": "phase14_executor_contract_v1",
        "enabled": True,
        "family": family,
        "variant": variant,
        "support_strategy": support_strategy,
        "phase_order": ["localize", "commit_edit", "verify", "close"],
        "tool_allowlists": {
            "localize": ["shell_command"],
            "commit_edit": ["shell_command", "apply_patch"],
            "verify": ["shell_command"],
            "close": ["mark_task_complete"],
        },
        "budgets": {
            "max_localize_turns": max_localize_turns,
            "max_commit_turns": max_commit_turns,
        },
        "finish_rules": {
            "allow_no_edit_close": allow_no_edit_close,
            "require_mark_task_complete": require_mark_task_complete,
            "require_verify_evidence": True,
        },
        "prompt_block": (
            "[Runner-owned executor note]\n"
            "Tool availability and finish acceptance are enforced by the runner.\n"
            "Do not assume a natural-language stop is sufficient. Use the available tools and only finish when the runner admits it."
        ),
    }
