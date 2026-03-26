from __future__ import annotations

from typing import Any, Dict

from .branch_receipt_contract import build_branch_receipt_contract


def _phase18_cfg(config: Dict[str, Any] | None) -> Dict[str, Any]:
    cfg = config if isinstance(config, dict) else {}
    phase18 = cfg.get("phase18")
    phase18 = phase18 if isinstance(phase18, dict) else {}
    executor = phase18.get("finish_closure_executor")
    return dict(executor) if isinstance(executor, dict) else {}


def finish_closure_executor_enabled(config: Dict[str, Any] | None) -> bool:
    return bool(_phase18_cfg(config).get("enabled"))


def build_finish_closure_contract(config: Dict[str, Any] | None) -> Dict[str, Any]:
    cfg = _phase18_cfg(config)
    if not bool(cfg.get("enabled")):
        return {
            "schema_version": "phase18_finish_closure_contract_v1",
            "enabled": False,
            "family": "",
            "variant": "",
            "support_strategy": "",
            "branch_contract": {},
            "tool_allowlists": {},
            "finish_rules": {},
            "turn_rules": {},
            "tool_choice": "",
            "parallel_tool_calls": None,
            "prompt_block": "",
        }

    branch_contract = build_branch_receipt_contract(config)
    family = str(cfg.get("family") or "execution_first_closure_ready_executor_v1_mini_native").strip()
    family = family or "execution_first_closure_ready_executor_v1_mini_native"
    variant = str(cfg.get("variant") or family).strip() or family
    support_strategy = str(cfg.get("support_strategy") or branch_contract.get("support_strategy") or "minimal_support")
    tool_allowlists = dict(branch_contract.get("tool_allowlists") or {})
    tool_allowlists["close"] = ["request_finish_receipt"]

    return {
        "schema_version": "phase18_finish_closure_contract_v1",
        "enabled": True,
        "family": family,
        "variant": variant,
        "support_strategy": support_strategy,
        "branch_contract": branch_contract,
        "tool_allowlists": tool_allowlists,
        "finish_rules": {
            "require_request_finish_receipt": bool(cfg.get("require_request_finish_receipt", True)),
            "deny_generic_mark_complete": bool(cfg.get("deny_generic_mark_complete", True)),
            "require_explicit_branch_receipt": True,
            "require_closure_ready": bool(cfg.get("require_closure_ready", True)),
        },
        "turn_rules": {
            "deny_no_tool_turn_before_close": bool(cfg.get("deny_no_tool_turn_before_close", True)),
            "deny_no_tool_turn_when_closure_ready": bool(cfg.get("deny_no_tool_turn_when_closure_ready", True)),
        },
        "tool_choice": str(cfg.get("tool_choice") or "required"),
        "parallel_tool_calls": bool(cfg.get("parallel_tool_calls", False)),
        "prompt_block": (
            "[Runner-owned finish / closure note]\n"
            "Receipt visibility alone does not count as task closure.\n"
            "When the task is closure-ready, use request_finish_receipt with the active branch and a terse closure basis.\n"
            "Do not narrate through the close phase. Finish is accepted only when branch, receipts, and closure mode all line up."
        ),
    }
