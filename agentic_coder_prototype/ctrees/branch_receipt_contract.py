from __future__ import annotations

from typing import Any, Dict


def _phase17_cfg(config: Dict[str, Any] | None) -> Dict[str, Any]:
    cfg = config if isinstance(config, dict) else {}
    phase17 = cfg.get("phase17")
    phase17 = phase17 if isinstance(phase17, dict) else {}
    executor = phase17.get("branch_receipt_executor")
    return dict(executor) if isinstance(executor, dict) else {}


def branch_receipt_executor_enabled(config: Dict[str, Any] | None) -> bool:
    return bool(_phase17_cfg(config).get("enabled"))


def build_branch_receipt_contract(config: Dict[str, Any] | None) -> Dict[str, Any]:
    cfg = _phase17_cfg(config)
    if not bool(cfg.get("enabled")):
        return {
            "schema_version": "phase17_branch_receipt_contract_v1",
            "enabled": False,
            "family": "",
            "variant": "",
            "support_strategy": "",
            "phase_order": [],
            "tool_allowlists": {},
            "budgets": {},
            "finish_rules": {},
            "turn_rules": {},
            "tool_choice": "",
            "parallel_tool_calls": None,
            "prompt_block": "",
        }

    family = str(cfg.get("family") or "execution_first_branch_receipt_executor_v1_mini_native").strip()
    family = family or "execution_first_branch_receipt_executor_v1_mini_native"
    variant = str(cfg.get("variant") or family).strip() or family
    support_strategy = str(cfg.get("support_strategy") or "minimal_support").strip() or "minimal_support"
    max_localize_turns = max(int(cfg.get("max_localize_turns") or 1), 1)
    max_branch_turns = max(int(cfg.get("max_branch_turns") or 1), 1)
    max_commit_turns = max(int(cfg.get("max_commit_turns") or 2), 1)
    max_proof_turns = max(int(cfg.get("max_proof_turns") or 2), 1)
    max_verify_turns = max(int(cfg.get("max_verify_turns") or 2), 1)
    strict_receipt_forcing = bool(cfg.get("strict_receipt_forcing", False))
    phase_order = ["localize", "branch_lock", "commit_edit", "prove_no_edit", "verify", "close"]
    tool_allowlists = {
        "localize": ["shell_command"],
        "branch_lock": ["record_branch_decision"],
        "commit_edit": ["shell_command", "apply_patch"],
        "prove_no_edit": ["shell_command", "record_proof_receipt"],
        "verify": ["shell_command", "record_verification_receipt"],
        "close": ["mark_task_complete"],
    }
    if strict_receipt_forcing:
        phase_order = [
            "localize",
            "branch_lock",
            "commit_edit",
            "prove_no_edit",
            "verify_evidence",
            "verify_receipt",
            "close",
        ]
        tool_allowlists = {
            "localize": ["shell_command"],
            "branch_lock": ["record_branch_decision"],
            "commit_edit": ["apply_patch"],
            "prove_no_edit": ["record_proof_receipt"],
            "verify_evidence": ["shell_command"],
            "verify_receipt": ["record_verification_receipt"],
            "close": ["mark_task_complete"],
        }

    return {
        "schema_version": "phase17_branch_receipt_contract_v1",
        "enabled": True,
        "family": family,
        "variant": variant,
        "support_strategy": support_strategy,
        "phase_order": phase_order,
        "tool_allowlists": tool_allowlists,
        "budgets": {
            "max_localize_turns": max_localize_turns,
            "max_branch_turns": max_branch_turns,
            "max_commit_turns": max_commit_turns,
            "max_proof_turns": max_proof_turns,
            "max_verify_turns": max_verify_turns,
        },
        "finish_rules": {
            "require_mark_task_complete": bool(cfg.get("require_mark_task_complete", True)),
            "require_explicit_branch_receipt": True,
            "allow_shell_branch_proxy": False,
            "strict_receipt_forcing": strict_receipt_forcing,
        },
        "turn_rules": {
            "deny_no_tool_turn_before_close": bool(cfg.get("deny_no_tool_turn_before_close", True)),
            "deny_shell_branch_proxy": bool(cfg.get("deny_shell_branch_proxy", True)),
        },
        "tool_choice": str(cfg.get("tool_choice") or "required"),
        "parallel_tool_calls": bool(cfg.get("parallel_tool_calls", False)),
        "prompt_block": (
            "[Runner-owned branch-and-receipt note]\n"
            "You must make the task's branch choice explicit before closing.\n"
            "Shell-only evidence is not a valid proof receipt by itself.\n"
            "Use receipt tools when the branch requires proof or verification receipts.\n"
            "Finish is denied unless branch choice and required receipts are visible to the runner."
        ),
    }
