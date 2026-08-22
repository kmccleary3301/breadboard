from __future__ import annotations

from typing import Any, Dict


def _phase16_cfg(config: Dict[str, Any] | None) -> Dict[str, Any]:
    cfg = config if isinstance(config, dict) else {}
    phase16 = cfg.get("phase16")
    phase16 = phase16 if isinstance(phase16, dict) else {}
    executor = phase16.get("invocation_first_executor")
    return dict(executor) if isinstance(executor, dict) else {}


def invocation_first_executor_enabled(config: Dict[str, Any] | None) -> bool:
    return bool(_phase16_cfg(config).get("enabled"))


def build_invocation_first_contract(config: Dict[str, Any] | None) -> Dict[str, Any]:
    cfg = _phase16_cfg(config)
    if not bool(cfg.get("enabled")):
        return {
            "schema_version": "phase16_invocation_first_contract_v1",
            "enabled": False,
            "family": "",
            "variant": "",
            "support_strategy": "",
            "tool_choice": "",
            "parallel_tool_calls": None,
            "prompt_block": "",
        }

    family = str(cfg.get("family") or "execution_first_invocation_first_executor_v1_mini_native").strip()
    family = family or "execution_first_invocation_first_executor_v1_mini_native"
    variant = str(cfg.get("variant") or family).strip() or family
    support_strategy = str(cfg.get("support_strategy") or "minimal_support").strip() or "minimal_support"
    return {
        "schema_version": "phase16_invocation_first_contract_v1",
        "enabled": True,
        "family": family,
        "variant": variant,
        "support_strategy": support_strategy,
        "tool_choice": str(cfg.get("tool_choice") or "required"),
        "parallel_tool_calls": bool(cfg.get("parallel_tool_calls", False)),
        "prompt_block": (
            "[Invocation-first contract]\n"
            "Use an allowed tool immediately when tools are available.\n"
            "Do not narrate intent before the first tool call.\n"
            "Explanation-only progress does not count on this probe."
        ),
    }
