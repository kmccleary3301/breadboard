from __future__ import annotations

from typing import Any, Dict


def _phase15_cfg(config: Dict[str, Any] | None) -> Dict[str, Any]:
    cfg = config if isinstance(config, dict) else {}
    phase15 = cfg.get("phase15")
    phase15 = phase15 if isinstance(phase15, dict) else {}
    executor = phase15.get("verifier_executor")
    return dict(executor) if isinstance(executor, dict) else {}


def verifier_executor_enabled(config: Dict[str, Any] | None) -> bool:
    return bool(_phase15_cfg(config).get("enabled"))


def build_verifier_executor_contract(config: Dict[str, Any] | None) -> Dict[str, Any]:
    cfg = _phase15_cfg(config)
    if not bool(cfg.get("enabled")):
        return {
            "schema_version": "phase15_verifier_executor_contract_v1",
            "enabled": False,
            "family": "",
            "variant": "",
            "support_strategy": "",
            "phase_order": [],
            "tool_allowlists": {},
            "budgets": {},
            "finish_rules": {},
            "turn_rules": {},
        }

    family = str(cfg.get("family") or "execution_first_verifier_owned_executor_v2_mini_native").strip()
    family = family or "execution_first_verifier_owned_executor_v2_mini_native"
    variant = str(cfg.get("variant") or family).strip() or family
    support_strategy = str(cfg.get("support_strategy") or "minimal_support").strip() or "minimal_support"
    max_localize_turns = max(int(cfg.get("max_localize_turns") or 1), 1)
    max_branch_turns = max(int(cfg.get("max_branch_turns") or 1), 1)
    max_commit_turns = max(int(cfg.get("max_commit_turns") or 1), 1)
    max_verify_turns = max(int(cfg.get("max_verify_turns") or 2), 1)

    return {
        "schema_version": "phase15_verifier_executor_contract_v1",
        "enabled": True,
        "family": family,
        "variant": variant,
        "support_strategy": support_strategy,
        "phase_order": ["localize", "branch", "commit_edit", "prove_no_edit", "verify", "close"],
        "tool_allowlists": {
            "localize": ["shell_command"],
            "branch": ["shell_command", "apply_patch"],
            "commit_edit": ["shell_command", "apply_patch"],
            "prove_no_edit": ["shell_command"],
            "verify": ["shell_command"],
            "close": ["mark_task_complete"],
        },
        "budgets": {
            "max_localize_turns": max_localize_turns,
            "max_branch_turns": max_branch_turns,
            "max_commit_turns": max_commit_turns,
            "max_verify_turns": max_verify_turns,
        },
        "finish_rules": {
            "require_mark_task_complete": bool(cfg.get("require_mark_task_complete", True)),
            "require_runner_validated_receipt": True,
            "allow_shell_only_grounded_close": False,
        },
        "turn_rules": {
            "deny_no_tool_turn_before_close": bool(cfg.get("deny_no_tool_turn_before_close", True)),
        },
        "prompt_block": (
            "[Runner-owned verifier-executor note]\n"
            "Finish is receipt-validated by the runner.\n"
            "You must satisfy the task's closure rule with edit receipts or proof receipts, plus verification where required.\n"
            "Natural-language completion and shell-only grounding do not count by themselves.\n"
            "If the active phase exposes tools, you must actually use an allowed tool rather than only describing intent."
        ),
    }
