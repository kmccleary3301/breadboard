from __future__ import annotations

from typing import Any, Dict, List, Optional

from .branch_receipt_contract import build_branch_receipt_contract
from .candidate_a import build_candidate_a_plan
from .continuation_family_policy import apply_continuation_family_policy, continuation_family_policy_for_base_task
from .control_contract import build_control_contract
from .downstream_task_pack import resolve_phase11_downstream_base_scenario_id
from .executor_contract import build_executor_contract, executor_enabled
from .finish_closure_contract import build_finish_closure_contract
from .invocation_first_contract import build_invocation_first_contract
from .evaluation_baselines import build_deterministic_reranker_plan
from .holdout_generalization_pack import build_phase10_base_scenarios
from .runtime_policy_contract import build_runtime_policy_contract, runtime_policy_enabled
from .selector_features import node_by_id
from .verifier_executor_contract import build_verifier_executor_contract, verifier_executor_enabled


def _adapter_cfg(config: Dict[str, Any] | None) -> Dict[str, Any]:
    cfg = config if isinstance(config, dict) else {}
    phase11 = cfg.get("phase11")
    phase11 = phase11 if isinstance(phase11, dict) else {}
    adapter = phase11.get("live_protocol_adapter")
    return dict(adapter) if isinstance(adapter, dict) else {}


def _build_support_plan(
    *,
    strategy: str,
    scenario: Dict[str, Any],
    mode: str,
    focus_node_id: Optional[str],
    graph_enabled: bool,
    graph_neighborhood_enabled: bool,
    dense_enabled: bool,
) -> Dict[str, Any]:
    if strategy == "candidate_a":
        return build_candidate_a_plan(
            scenario["store"],
            mode=mode,
            focus_node_id=focus_node_id,
            graph_enabled=graph_enabled,
            graph_neighborhood_enabled=graph_neighborhood_enabled,
            dense_enabled=dense_enabled,
        )
    if strategy == "deterministic_reranker":
        return build_deterministic_reranker_plan(
            scenario["store"],
            mode=mode,
            focus_node_id=focus_node_id,
            graph_enabled=graph_enabled,
            graph_neighborhood_enabled=graph_neighborhood_enabled,
            dense_enabled=dense_enabled,
        )
    raise KeyError(f"unsupported live support strategy {strategy}")


def _build_minimal_support_bundle(plan: Dict[str, Any]) -> Dict[str, Any]:
    bundle = dict(plan.get("rehydration_bundle") or {})
    support_node_ids = [str(item) for item in list(bundle.get("support_node_ids") or []) if str(item)]
    candidate_provenance = [dict(item) for item in list(bundle.get("candidate_provenance") or []) if isinstance(item, dict)]
    bundle["support_node_ids"] = support_node_ids[:1]
    bundle["candidate_provenance"] = candidate_provenance[:1]
    return bundle


def _build_support_payload_bundle(
    *,
    strategy: str,
    plan: Dict[str, Any],
    base_task_id: str,
) -> Dict[str, Any]:
    raw_bundle = dict(plan.get("rehydration_bundle") or {})
    if strategy == "minimal_support":
        return _build_minimal_support_bundle(plan)
    if strategy == "calibrated_continuation":
        return apply_continuation_family_policy(bundle=raw_bundle, base_task_id=base_task_id)
    if strategy == "calibrated_continuation_v1b":
        return apply_continuation_family_policy(bundle=raw_bundle, base_task_id=base_task_id, variant="v1b")
    return raw_bundle


def protocol_adapter_enabled(config: Dict[str, Any] | None) -> bool:
    return bool(_adapter_cfg(config).get("enabled"))


def protocol_adapter_strategy(config: Dict[str, Any] | None) -> str:
    return str(_adapter_cfg(config).get("strategy") or "").strip()


def _scenario_by_base_id(base_scenario_id: str) -> Dict[str, Any]:
    for item in build_phase10_base_scenarios():
        if str(item.get("base_scenario_id") or "") == str(base_scenario_id):
            return item
    raise KeyError(f"missing base scenario {base_scenario_id}")


def _render_support_lines(store: Any, support_node_ids: List[str], candidate_provenance: List[Dict[str, Any]]) -> List[str]:
    provenance_by_id = {
        str(item.get("node_id") or ""): dict(item)
        for item in list(candidate_provenance or [])
        if str(item.get("node_id") or "")
    }
    lines: List[str] = []
    for node_id in list(support_node_ids or []):
        node = node_by_id(store, node_id)
        if not isinstance(node, dict):
            continue
        meta = provenance_by_id.get(str(node_id), {})
        title = str(node.get("title") or node_id)
        selector_reason = str(meta.get("reason") or "").strip()
        lane = str(meta.get("lane") or "").strip()
        status = str(node.get("status") or "").strip()
        parts = [f"- {title} [{status or 'unknown'}]"]
        if selector_reason:
            parts.append(f"reason={selector_reason}")
        if lane:
            parts.append(f"lane={lane}")
        targets = [str(item) for item in list(node.get("targets") or []) if str(item)]
        if targets:
            parts.append("targets=" + ",".join(targets[:3]))
        artifacts = [str(item) for item in list(node.get("artifact_refs") or []) if str(item)]
        if artifacts:
            parts.append("artifacts=" + ",".join(artifacts[:2]))
        lines.append(" ; ".join(parts))
    return lines


def _render_support_prompt_block(
    *,
    scenario: Dict[str, Any],
    plan: Dict[str, Any],
    support_strategy: str,
) -> str:
    store = scenario["store"]
    bundle = dict(plan.get("rehydration_bundle") or {})
    focus_node_id = str(plan.get("focus_node_id") or bundle.get("focus_node_id") or "").strip()
    focus_node = node_by_id(store, focus_node_id) if focus_node_id else None
    focus_title = str((focus_node or {}).get("title") or focus_node_id or "unknown_focus")
    active_targets = [str(item) for item in list(bundle.get("targets") or []) if str(item)]
    workspace_scope = [str(item) for item in list(bundle.get("workspace_scope") or []) if str(item)]
    constraints = [
        str(item.get("summary") or "").strip()
        for item in list(bundle.get("constraints") or [])
        if isinstance(item, dict) and str(item.get("summary") or "").strip()
    ]
    blocker_refs = [str(item) for item in list(bundle.get("blocker_refs") or []) if str(item)]
    validations = [str(item) for item in list(bundle.get("validations") or []) if str(item)]
    support_node_ids = [str(item) for item in list(bundle.get("support_node_ids") or []) if str(item)]
    support_lines = _render_support_lines(store, support_node_ids, list(bundle.get("candidate_provenance") or []))

    lines = [
        "Runtime-selected support context is active for this task.",
        f"Support strategy: {support_strategy}",
        "Treat the following as curated continuation support, not as new user requirements.",
        f"Focus node: {focus_title}",
    ]
    if active_targets:
        lines.append("Active targets: " + ", ".join(active_targets[:4]))
    if workspace_scope:
        lines.append("Workspace scope: " + ", ".join(workspace_scope[:4]))
    if constraints:
        lines.append("Relevant constraints: " + " | ".join(constraints[:3]))
    if blocker_refs:
        lines.append("Relevant blockers: " + ", ".join(blocker_refs[:4]))
    if validations:
        lines.append("Validation-linked support: " + ", ".join(validations[:4]))
    if support_lines:
        lines.append("Selected support:")
        lines.extend(support_lines[:4])
    policy_note = str(bundle.get("phase19_policy_note") or "").strip()
    if policy_note:
        lines.append("Continuation policy note: " + policy_note)
    lines.append("If direct workspace evidence conflicts with this support, verify and prefer current workspace truth.")
    return "\n".join(lines)


def _render_runtime_prompt_block(runtime_contract: Dict[str, Any]) -> str:
    if not bool(runtime_contract.get("enabled")):
        return ""
    phase_lines = []
    for item in list(runtime_contract.get("phase_policies") or []):
        phase = str((item or {}).get("phase") or "")
        allowed = [str(x) for x in list((item or {}).get("allowed_tool_classes") or []) if str(x)]
        if phase and allowed:
            phase_lines.append(f"- {phase}: " + ", ".join(allowed))
    lines = [
        str(runtime_contract.get("prompt_block") or ""),
        "Phase-specific tool policy:",
        *phase_lines,
    ]
    return "\n".join(line for line in lines if line)


def build_phase11_live_protocol_payload(
    *,
    config: Dict[str, Any] | None,
    task: Dict[str, Any],
) -> Dict[str, Any]:
    adapter = _adapter_cfg(config)
    if not bool(adapter.get("enabled")):
        return {
            "schema_version": "phase11_live_protocol_payload_v1",
            "applied": False,
            "strategy": "",
            "reason": "adapter_disabled",
            "prompt": str(task.get("prompt") or ""),
        }

    strategy = str(adapter.get("strategy") or "").strip()
    if strategy not in {"candidate_a", "deterministic_reranker"}:
        return {
            "schema_version": "phase11_live_protocol_payload_v1",
            "applied": False,
            "strategy": strategy,
            "reason": "unsupported_strategy",
            "prompt": str(task.get("prompt") or ""),
        }

    base_task_id = str(task.get("base_task_id") or task.get("task_id") or task.get("id") or "").strip()
    base_scenario_id = resolve_phase11_downstream_base_scenario_id(base_task_id)
    scenario = _scenario_by_base_id(base_scenario_id)
    mode = str(adapter.get("mode") or scenario.get("mode") or "active_continuation")
    graph_enabled = bool(adapter.get("graph_enabled", True))
    graph_neighborhood_enabled = bool(adapter.get("graph_neighborhood_enabled", False))
    dense_enabled = bool(adapter.get("dense_enabled", False))
    focus_node_id = str(scenario.get("focus_node_id") or "").strip() or None

    plan = _build_support_plan(
        strategy=strategy,
        scenario=scenario,
        mode=mode,
        focus_node_id=focus_node_id,
        graph_enabled=graph_enabled,
        graph_neighborhood_enabled=graph_neighborhood_enabled,
        dense_enabled=dense_enabled,
    )
    prompt_block = _render_support_prompt_block(
        scenario=scenario,
        plan=plan,
        support_strategy=strategy,
    )
    control = build_control_contract(config)
    original_prompt = str(task.get("prompt") or "")
    prompt_parts = ["[Protocol-selected continuation context]", prompt_block]
    if control.get("enabled"):
        prompt_parts.extend(["", str(control.get("prompt_block") or "")])
    prompt_parts.extend(["", "[Current task]", original_prompt])
    augmented_prompt = "\n".join(part for part in prompt_parts if part is not None)
    bundle = dict(plan.get("rehydration_bundle") or {})
    return {
        "schema_version": "phase11_live_protocol_payload_v1",
        "applied": True,
        "strategy": strategy,
        "reason": "live_prompt_injection",
        "base_task_id": base_task_id,
        "base_scenario_id": base_scenario_id,
        "mode": mode,
        "focus_node_id": plan.get("focus_node_id"),
        "support_node_ids": [str(item) for item in list(bundle.get("support_node_ids") or []) if str(item)],
        "support_bundle": bundle,
        "retrieval_substrate": dict(plan.get("retrieval_substrate") or {}),
        "control_contract": control,
        "prompt": augmented_prompt,
    }


def build_phase11_live_context_metadata(protocol_payload: Dict[str, Any]) -> Dict[str, Any]:
    if not bool(protocol_payload.get("applied")):
        return {
            "phase11_live_protocol_applied": False,
            "phase11_live_protocol_strategy": "",
        }
    return {
        "phase11_live_protocol_applied": True,
        "phase11_live_protocol_strategy": str(protocol_payload.get("strategy") or ""),
        "phase11_live_protocol_mode": str(protocol_payload.get("mode") or ""),
        "phase11_live_protocol_base_task_id": str(protocol_payload.get("base_task_id") or ""),
        "phase11_live_protocol_base_scenario_id": str(protocol_payload.get("base_scenario_id") or ""),
        "phase11_live_protocol_support_count": len(list(protocol_payload.get("support_node_ids") or [])),
        "phase11_live_protocol_focus_node_id": str(protocol_payload.get("focus_node_id") or ""),
        "phase12_control_enabled": bool((protocol_payload.get("control_contract") or {}).get("enabled")),
        "phase12_control_variant": str((protocol_payload.get("control_contract") or {}).get("variant") or ""),
    }


def build_phase13_runtime_protocol_payload(
    *,
    config: Dict[str, Any] | None,
    task: Dict[str, Any],
) -> Dict[str, Any]:
    runtime_contract = build_runtime_policy_contract(config)
    if not bool(runtime_contract.get("enabled")):
        return {
            "schema_version": "phase13_runtime_protocol_payload_v1",
            "applied": False,
            "family": "",
            "support_strategy": "",
            "reason": "runtime_protocol_disabled",
            "prompt": str(task.get("prompt") or ""),
        }

    strategy = str(runtime_contract.get("support_strategy") or "").strip()
    base_task_id = str(task.get("base_task_id") or task.get("task_id") or task.get("id") or "").strip()
    base_scenario_id = resolve_phase11_downstream_base_scenario_id(base_task_id)
    scenario = _scenario_by_base_id(base_scenario_id)
    mode = str(scenario.get("mode") or "active_continuation")
    focus_node_id = str(scenario.get("focus_node_id") or "").strip() or None
    plan_strategy = "deterministic_reranker" if strategy in {"deterministic_reranker", "minimal_support", "calibrated_continuation"} else "candidate_a"
    plan = _build_support_plan(
        strategy=plan_strategy,
        scenario=scenario,
        mode=mode,
        focus_node_id=focus_node_id,
        graph_enabled=True,
        graph_neighborhood_enabled=False,
        dense_enabled=False,
    )
    bundle = _build_support_payload_bundle(strategy=strategy, plan=plan, base_task_id=base_task_id)
    support_payload = {
        "strategy": strategy,
        "base_strategy": plan_strategy,
        "focus_node_id": plan.get("focus_node_id"),
        "support_node_ids": [str(item) for item in list(bundle.get("support_node_ids") or []) if str(item)],
        "support_bundle": bundle,
    }
    if strategy == "calibrated_continuation":
        policy = continuation_family_policy_for_base_task(base_task_id)
        support_payload["continuation_policy"] = policy
    support_prompt = _render_support_prompt_block(
        scenario=scenario,
        plan={**plan, "rehydration_bundle": bundle},
        support_strategy=strategy,
    )
    runtime_prompt = _render_runtime_prompt_block(runtime_contract)
    original_prompt = str(task.get("prompt") or "")
    augmented_prompt = "\n".join(
        [
            "[Runtime-selected support context]",
            support_prompt,
            "",
            runtime_prompt,
            "",
            "[Current task]",
            original_prompt,
        ]
    )
    return {
        "schema_version": "phase13_runtime_protocol_payload_v1",
        "applied": True,
        "family": str(runtime_contract.get("family") or ""),
        "support_strategy": strategy,
        "reason": "runtime_protocol_injection",
        "base_task_id": base_task_id,
        "base_scenario_id": base_scenario_id,
        "mode": mode,
        "support_payload": support_payload,
        "runtime_contract": runtime_contract,
        "prompt": augmented_prompt,
    }


def build_phase13_runtime_context_metadata(protocol_payload: Dict[str, Any]) -> Dict[str, Any]:
    if not bool(protocol_payload.get("applied")):
        return {
            "phase13_runtime_protocol_applied": False,
            "phase13_runtime_family": "",
            "phase13_runtime_support_strategy": "",
        }
    support_payload = dict(protocol_payload.get("support_payload") or {})
    return {
        "phase13_runtime_protocol_applied": True,
        "phase13_runtime_family": str(protocol_payload.get("family") or ""),
        "phase13_runtime_support_strategy": str(protocol_payload.get("support_strategy") or ""),
        "phase13_runtime_base_task_id": str(protocol_payload.get("base_task_id") or ""),
        "phase13_runtime_base_scenario_id": str(protocol_payload.get("base_scenario_id") or ""),
        "phase13_runtime_support_count": len(list(support_payload.get("support_node_ids") or [])),
    }


def build_phase14_executor_protocol_payload(
    *,
    config: Dict[str, Any] | None,
    task: Dict[str, Any],
) -> Dict[str, Any]:
    executor_contract = build_executor_contract(config)
    if not bool(executor_contract.get("enabled")):
        return {
            "schema_version": "phase14_executor_protocol_payload_v1",
            "applied": False,
            "family": "",
            "support_strategy": "",
            "reason": "executor_disabled",
            "prompt": str(task.get("prompt") or ""),
        }

    strategy = str(executor_contract.get("support_strategy") or "").strip()
    base_task_id = str(task.get("base_task_id") or task.get("task_id") or task.get("id") or "").strip()
    base_scenario_id = resolve_phase11_downstream_base_scenario_id(base_task_id)
    scenario = _scenario_by_base_id(base_scenario_id)
    mode = str(scenario.get("mode") or "active_continuation")
    focus_node_id = str(scenario.get("focus_node_id") or "").strip() or None
    plan_strategy = (
        "deterministic_reranker"
        if strategy in {"deterministic_reranker", "minimal_support", "calibrated_continuation", "calibrated_continuation_v1b"}
        else "candidate_a"
    )
    plan = _build_support_plan(
        strategy=plan_strategy,
        scenario=scenario,
        mode=mode,
        focus_node_id=focus_node_id,
        graph_enabled=True,
        graph_neighborhood_enabled=False,
        dense_enabled=False,
    )
    bundle = _build_support_payload_bundle(strategy=strategy, plan=plan, base_task_id=base_task_id)
    support_payload = {
        "strategy": strategy,
        "base_strategy": plan_strategy,
        "focus_node_id": plan.get("focus_node_id"),
        "support_node_ids": [str(item) for item in list(bundle.get("support_node_ids") or []) if str(item)],
        "support_bundle": bundle,
    }
    if strategy in {"calibrated_continuation", "calibrated_continuation_v1b"}:
        variant = "v1b" if strategy == "calibrated_continuation_v1b" else "v1"
        support_payload["continuation_policy"] = continuation_family_policy_for_base_task(base_task_id, variant=variant)
    support_prompt = _render_support_prompt_block(
        scenario=scenario,
        plan={**plan, "rehydration_bundle": bundle},
        support_strategy=strategy,
    )
    original_prompt = str(task.get("prompt") or "")
    augmented_prompt = "\n".join(
        [
            "[Executor-selected support context]",
            support_prompt,
            "",
            str(executor_contract.get("prompt_block") or ""),
            "",
            "[Current task]",
            original_prompt,
        ]
    )
    return {
        "schema_version": "phase14_executor_protocol_payload_v1",
        "applied": True,
        "family": str(executor_contract.get("family") or ""),
        "support_strategy": strategy,
        "reason": "executor_protocol_injection",
        "base_task_id": base_task_id,
        "base_scenario_id": base_scenario_id,
        "mode": mode,
        "support_payload": support_payload,
        "executor_contract": executor_contract,
        "prompt": augmented_prompt,
    }


def build_phase14_executor_context_metadata(protocol_payload: Dict[str, Any]) -> Dict[str, Any]:
    if not bool(protocol_payload.get("applied")):
        return {
            "phase14_executor_protocol_applied": False,
            "phase14_executor_family": "",
            "phase14_executor_support_strategy": "",
        }
    support_payload = dict(protocol_payload.get("support_payload") or {})
    return {
        "phase14_executor_protocol_applied": True,
        "phase14_executor_family": str(protocol_payload.get("family") or ""),
        "phase14_executor_support_strategy": str(protocol_payload.get("support_strategy") or ""),
        "phase14_executor_base_task_id": str(protocol_payload.get("base_task_id") or ""),
        "phase14_executor_base_scenario_id": str(protocol_payload.get("base_scenario_id") or ""),
        "phase14_executor_support_count": len(list(support_payload.get("support_node_ids") or [])),
    }


def build_phase15_verifier_executor_protocol_payload(
    *,
    config: Dict[str, Any] | None,
    task: Dict[str, Any],
) -> Dict[str, Any]:
    verifier_contract = build_verifier_executor_contract(config)
    if not bool(verifier_contract.get("enabled")):
        return {
            "schema_version": "phase15_verifier_executor_protocol_payload_v1",
            "applied": False,
            "family": "",
            "support_strategy": "",
            "reason": "verifier_executor_disabled",
            "prompt": str(task.get("prompt") or ""),
        }

    strategy = str(verifier_contract.get("support_strategy") or "").strip()
    base_task_id = str(task.get("base_task_id") or task.get("task_id") or task.get("id") or "").strip()
    base_scenario_id = resolve_phase11_downstream_base_scenario_id(base_task_id)
    scenario = _scenario_by_base_id(base_scenario_id)
    mode = str(scenario.get("mode") or "active_continuation")
    focus_node_id = str(scenario.get("focus_node_id") or "").strip() or None
    plan_strategy = (
        "deterministic_reranker"
        if strategy in {"deterministic_reranker", "minimal_support", "calibrated_continuation", "calibrated_continuation_v1b"}
        else "candidate_a"
    )
    plan = _build_support_plan(
        strategy=plan_strategy,
        scenario=scenario,
        mode=mode,
        focus_node_id=focus_node_id,
        graph_enabled=True,
        graph_neighborhood_enabled=False,
        dense_enabled=False,
    )
    bundle = _build_support_payload_bundle(strategy=strategy, plan=plan, base_task_id=base_task_id)
    support_payload = {
        "strategy": strategy,
        "base_strategy": plan_strategy,
        "focus_node_id": plan.get("focus_node_id"),
        "support_node_ids": [str(item) for item in list(bundle.get("support_node_ids") or []) if str(item)],
        "support_bundle": bundle,
    }
    if strategy in {"calibrated_continuation", "calibrated_continuation_v1b"}:
        variant = "v1b" if strategy == "calibrated_continuation_v1b" else "v1"
        support_payload["continuation_policy"] = continuation_family_policy_for_base_task(base_task_id, variant=variant)
    support_prompt = _render_support_prompt_block(
        scenario=scenario,
        plan={**plan, "rehydration_bundle": bundle},
        support_strategy=strategy,
    )
    task_context = {
        "task_id": str(task.get("id") or ""),
        "closure_mode": str(task.get("closure_mode") or "edit_or_proof"),
        "invalid_finish_trap": bool(task.get("invalid_finish_trap")),
        "required_receipts": [str(item) for item in list(task.get("required_receipts") or []) if str(item)],
        "requires_edit_commitment": bool(task.get("requires_edit_commitment")),
        "requires_no_edit_proof": bool(task.get("requires_no_edit_proof")),
        "probe_kind": str(task.get("probe_kind") or ""),
    }
    original_prompt = str(task.get("prompt") or "")
    augmented_prompt = "\n".join(
        [
            "[Verifier-owned executor support context]",
            support_prompt,
            "",
            str(verifier_contract.get("prompt_block") or ""),
            "",
            "[Task closure rule]",
            f"- closure_mode: {task_context['closure_mode']}",
            f"- required_receipts: {', '.join(task_context['required_receipts']) or 'none'}",
            f"- invalid_finish_trap: {task_context['invalid_finish_trap']}",
            "",
            "[Current task]",
            original_prompt,
        ]
    )
    return {
        "schema_version": "phase15_verifier_executor_protocol_payload_v1",
        "applied": True,
        "family": str(verifier_contract.get("family") or ""),
        "support_strategy": strategy,
        "reason": "verifier_executor_protocol_injection",
        "base_task_id": base_task_id,
        "base_scenario_id": base_scenario_id,
        "mode": mode,
        "support_payload": support_payload,
        "verifier_executor_contract": verifier_contract,
        "task_context": task_context,
        "prompt": augmented_prompt,
    }


def build_phase15_verifier_executor_context_metadata(protocol_payload: Dict[str, Any]) -> Dict[str, Any]:
    if not bool(protocol_payload.get("applied")):
        return {
            "phase15_verifier_executor_protocol_applied": False,
            "phase15_verifier_executor_family": "",
            "phase15_verifier_executor_support_strategy": "",
        }
    support_payload = dict(protocol_payload.get("support_payload") or {})
    task_context = dict(protocol_payload.get("task_context") or {})
    return {
        "phase15_verifier_executor_protocol_applied": True,
        "phase15_verifier_executor_family": str(protocol_payload.get("family") or ""),
        "phase15_verifier_executor_support_strategy": str(protocol_payload.get("support_strategy") or ""),
        "phase15_verifier_executor_base_task_id": str(protocol_payload.get("base_task_id") or ""),
        "phase15_verifier_executor_base_scenario_id": str(protocol_payload.get("base_scenario_id") or ""),
        "phase15_verifier_executor_support_count": len(list(support_payload.get("support_node_ids") or [])),
        "phase15_task_id": str(task_context.get("task_id") or ""),
        "phase15_closure_mode": str(task_context.get("closure_mode") or "edit_or_proof"),
        "phase15_invalid_finish_trap": bool(task_context.get("invalid_finish_trap")),
        "phase15_required_receipts": [str(item) for item in list(task_context.get("required_receipts") or []) if str(item)],
        "phase15_requires_edit_commitment": bool(task_context.get("requires_edit_commitment")),
        "phase15_requires_no_edit_proof": bool(task_context.get("requires_no_edit_proof")),
        "phase15_probe_kind": str(task_context.get("probe_kind") or ""),
    }


def build_phase16_invocation_first_protocol_payload(
    *,
    config: Dict[str, Any] | None,
    task: Dict[str, Any],
) -> Dict[str, Any]:
    invocation_contract = build_invocation_first_contract(config)
    if not bool(invocation_contract.get("enabled")):
        return {
            "schema_version": "phase16_invocation_first_protocol_payload_v1",
            "applied": False,
            "family": "",
            "support_strategy": "",
            "reason": "invocation_first_executor_disabled",
            "prompt": str(task.get("prompt") or ""),
        }

    strategy = str(invocation_contract.get("support_strategy") or "").strip()
    base_task_id = str(task.get("base_task_id") or task.get("task_id") or task.get("id") or "").strip()
    base_scenario_id = resolve_phase11_downstream_base_scenario_id(base_task_id)
    scenario = _scenario_by_base_id(base_scenario_id)
    mode = str(scenario.get("mode") or "active_continuation")
    focus_node_id = str(scenario.get("focus_node_id") or "").strip() or None
    plan_strategy = "deterministic_reranker" if strategy in {"deterministic_reranker", "minimal_support"} else "candidate_a"
    plan = _build_support_plan(
        strategy=plan_strategy,
        scenario=scenario,
        mode=mode,
        focus_node_id=focus_node_id,
        graph_enabled=True,
        graph_neighborhood_enabled=False,
        dense_enabled=False,
    )
    bundle = _build_minimal_support_bundle(plan) if strategy == "minimal_support" else dict(plan.get("rehydration_bundle") or {})
    support_payload = {
        "strategy": strategy,
        "base_strategy": plan_strategy,
        "focus_node_id": plan.get("focus_node_id"),
        "support_node_ids": [str(item) for item in list(bundle.get("support_node_ids") or []) if str(item)],
        "support_bundle": bundle,
    }
    support_prompt = _render_support_prompt_block(
        scenario=scenario,
        plan={**plan, "rehydration_bundle": bundle},
        support_strategy=strategy,
    )
    task_context = {
        "task_id": str(task.get("id") or ""),
        "probe_kind": str(task.get("probe_kind") or ""),
        "allowed_tool_mode": str(task.get("allowed_tool_mode") or ""),
        "expected_first_tool_family": str(task.get("expected_first_tool_family") or ""),
        "invalid_finish_trap": bool(task.get("invalid_finish_trap")),
        "requires_edit_commitment": bool(task.get("requires_edit_commitment")),
    }
    original_prompt = str(task.get("prompt") or "")
    augmented_prompt = "\n".join(
        [
            "[Invocation-first support context]",
            support_prompt,
            "",
            str(invocation_contract.get("prompt_block") or ""),
            "",
            "[Action probe contract]",
            f"- probe_kind: {task_context['probe_kind']}",
            f"- allowed_tool_mode: {task_context['allowed_tool_mode']}",
            f"- expected_first_tool_family: {task_context['expected_first_tool_family']}",
            "",
            "[Current task]",
            original_prompt,
        ]
    )
    return {
        "schema_version": "phase16_invocation_first_protocol_payload_v1",
        "applied": True,
        "family": str(invocation_contract.get("family") or ""),
        "support_strategy": strategy,
        "reason": "invocation_first_protocol_injection",
        "base_task_id": base_task_id,
        "base_scenario_id": base_scenario_id,
        "mode": mode,
        "support_payload": support_payload,
        "invocation_first_contract": invocation_contract,
        "task_context": task_context,
        "prompt": augmented_prompt,
    }


def build_phase16_invocation_first_context_metadata(protocol_payload: Dict[str, Any]) -> Dict[str, Any]:
    if not bool(protocol_payload.get("applied")):
        return {
            "phase16_invocation_protocol_applied": False,
            "phase16_invocation_family": "",
            "phase16_invocation_support_strategy": "",
        }
    support_payload = dict(protocol_payload.get("support_payload") or {})
    task_context = dict(protocol_payload.get("task_context") or {})
    return {
        "phase16_invocation_protocol_applied": True,
        "phase16_invocation_family": str(protocol_payload.get("family") or ""),
        "phase16_invocation_support_strategy": str(protocol_payload.get("support_strategy") or ""),
        "phase16_invocation_base_task_id": str(protocol_payload.get("base_task_id") or ""),
        "phase16_invocation_base_scenario_id": str(protocol_payload.get("base_scenario_id") or ""),
        "phase16_invocation_support_count": len(list(support_payload.get("support_node_ids") or [])),
        "phase16_task_id": str(task_context.get("task_id") or ""),
        "phase16_probe_kind": str(task_context.get("probe_kind") or ""),
        "phase16_allowed_tool_mode": str(task_context.get("allowed_tool_mode") or ""),
        "phase16_expected_first_tool_family": str(task_context.get("expected_first_tool_family") or ""),
        "phase16_invalid_finish_trap": bool(task_context.get("invalid_finish_trap")),
        "phase16_requires_edit_commitment": bool(task_context.get("requires_edit_commitment")),
    }


def build_phase17_branch_receipt_protocol_payload(
    *,
    config: Dict[str, Any] | None,
    task: Dict[str, Any],
) -> Dict[str, Any]:
    contract = build_branch_receipt_contract(config)
    if not bool(contract.get("enabled")):
        return {
            "schema_version": "phase17_branch_receipt_protocol_payload_v1",
            "applied": False,
            "family": "",
            "support_strategy": "",
            "reason": "branch_receipt_executor_disabled",
            "prompt": str(task.get("prompt") or ""),
        }

    strategy = str(contract.get("support_strategy") or "").strip()
    base_task_id = str(task.get("base_task_id") or task.get("task_id") or task.get("id") or "").strip()
    base_scenario_id = resolve_phase11_downstream_base_scenario_id(base_task_id)
    scenario = _scenario_by_base_id(base_scenario_id)
    mode = str(scenario.get("mode") or "active_continuation")
    focus_node_id = str(scenario.get("focus_node_id") or "").strip() or None
    plan_strategy = "deterministic_reranker" if strategy in {"deterministic_reranker", "minimal_support"} else "candidate_a"
    plan = _build_support_plan(
        strategy=plan_strategy,
        scenario=scenario,
        mode=mode,
        focus_node_id=focus_node_id,
        graph_enabled=True,
        graph_neighborhood_enabled=False,
        dense_enabled=False,
    )
    bundle = _build_minimal_support_bundle(plan) if strategy == "minimal_support" else dict(plan.get("rehydration_bundle") or {})
    support_payload = {
        "strategy": strategy,
        "base_strategy": plan_strategy,
        "focus_node_id": plan.get("focus_node_id"),
        "support_node_ids": [str(item) for item in list(bundle.get("support_node_ids") or []) if str(item)],
        "support_bundle": bundle,
    }
    support_prompt = _render_support_prompt_block(
        scenario=scenario,
        plan={**plan, "rehydration_bundle": bundle},
        support_strategy=strategy,
    )
    task_context = {
        "task_id": str(task.get("id") or ""),
        "probe_kind": str(task.get("probe_kind") or ""),
        "closure_mode": str(task.get("closure_mode") or "edit_or_proof"),
        "required_branch_mode": str(task.get("required_branch_mode") or ""),
        "required_receipts": [str(item) for item in list(task.get("required_receipts") or []) if str(item)],
        "invalid_finish_trap": bool(task.get("invalid_finish_trap")),
        "allow_shell_branch_proxy": bool(task.get("allow_shell_branch_proxy")),
        "requires_edit_commitment": bool(task.get("requires_edit_commitment")),
        "requires_no_edit_proof": bool(task.get("requires_no_edit_proof")),
    }
    original_prompt = str(task.get("prompt") or "")
    augmented_prompt = "\n".join(
        [
            "[Branch-and-receipt support context]",
            support_prompt,
            "",
            str(contract.get("prompt_block") or ""),
            "",
            "[Task branch rule]",
            f"- closure_mode: {task_context['closure_mode']}",
            f"- required_branch_mode: {task_context['required_branch_mode'] or 'either'}",
            f"- required_receipts: {', '.join(task_context['required_receipts']) or 'none'}",
            f"- allow_shell_branch_proxy: {task_context['allow_shell_branch_proxy']}",
            "",
            "[Current task]",
            original_prompt,
        ]
    )
    return {
        "schema_version": "phase17_branch_receipt_protocol_payload_v1",
        "applied": True,
        "family": str(contract.get("family") or ""),
        "support_strategy": strategy,
        "reason": "branch_receipt_protocol_injection",
        "base_task_id": base_task_id,
        "base_scenario_id": base_scenario_id,
        "mode": mode,
        "support_payload": support_payload,
        "branch_receipt_contract": contract,
        "task_context": task_context,
        "prompt": augmented_prompt,
    }


def build_phase17_branch_receipt_context_metadata(protocol_payload: Dict[str, Any]) -> Dict[str, Any]:
    if not bool(protocol_payload.get("applied")):
        return {
            "phase17_branch_receipt_protocol_applied": False,
            "phase17_branch_receipt_family": "",
            "phase17_branch_receipt_support_strategy": "",
        }
    support_payload = dict(protocol_payload.get("support_payload") or {})
    task_context = dict(protocol_payload.get("task_context") or {})
    return {
        "phase17_branch_receipt_protocol_applied": True,
        "phase17_branch_receipt_family": str(protocol_payload.get("family") or ""),
        "phase17_branch_receipt_support_strategy": str(protocol_payload.get("support_strategy") or ""),
        "phase17_branch_receipt_base_task_id": str(protocol_payload.get("base_task_id") or ""),
        "phase17_branch_receipt_base_scenario_id": str(protocol_payload.get("base_scenario_id") or ""),
        "phase17_branch_receipt_support_count": len(list(support_payload.get("support_node_ids") or [])),
        "phase17_task_id": str(task_context.get("task_id") or ""),
        "phase17_probe_kind": str(task_context.get("probe_kind") or ""),
        "phase17_closure_mode": str(task_context.get("closure_mode") or "edit_or_proof"),
        "phase17_required_branch_mode": str(task_context.get("required_branch_mode") or ""),
        "phase17_required_receipts": [str(item) for item in list(task_context.get("required_receipts") or []) if str(item)],
        "phase17_invalid_finish_trap": bool(task_context.get("invalid_finish_trap")),
        "phase17_allow_shell_branch_proxy": bool(task_context.get("allow_shell_branch_proxy")),
        "phase17_requires_edit_commitment": bool(task_context.get("requires_edit_commitment")),
        "phase17_requires_no_edit_proof": bool(task_context.get("requires_no_edit_proof")),
    }


def build_phase18_finish_closure_protocol_payload(
    *,
    config: Dict[str, Any] | None,
    task: Dict[str, Any],
) -> Dict[str, Any]:
    contract = build_finish_closure_contract(config)
    if not bool(contract.get("enabled")):
        return {
            "schema_version": "phase18_finish_closure_protocol_payload_v1",
            "applied": False,
            "family": "",
            "support_strategy": "",
            "reason": "finish_closure_executor_disabled",
            "prompt": str(task.get("prompt") or ""),
        }

    strategy = str(contract.get("support_strategy") or "").strip()
    base_task_id = str(task.get("base_task_id") or task.get("task_id") or task.get("id") or "").strip()
    base_scenario_id = resolve_phase11_downstream_base_scenario_id(base_task_id)
    scenario = _scenario_by_base_id(base_scenario_id)
    mode = str(scenario.get("mode") or "active_continuation")
    focus_node_id = str(scenario.get("focus_node_id") or "").strip() or None
    plan_strategy = (
        "deterministic_reranker"
        if strategy in {"deterministic_reranker", "minimal_support", "calibrated_continuation", "calibrated_continuation_v1b"}
        else "candidate_a"
    )
    plan = _build_support_plan(
        strategy=plan_strategy,
        scenario=scenario,
        mode=mode,
        focus_node_id=focus_node_id,
        graph_enabled=True,
        graph_neighborhood_enabled=False,
        dense_enabled=False,
    )
    bundle = _build_support_payload_bundle(strategy=strategy, plan=plan, base_task_id=base_task_id)
    support_payload = {
        "strategy": strategy,
        "base_strategy": plan_strategy,
        "focus_node_id": plan.get("focus_node_id"),
        "support_node_ids": [str(item) for item in list(bundle.get("support_node_ids") or []) if str(item)],
        "support_bundle": bundle,
    }
    if strategy in {"calibrated_continuation", "calibrated_continuation_v1b"}:
        variant = "v1b" if strategy == "calibrated_continuation_v1b" else "v1"
        support_payload["continuation_policy"] = continuation_family_policy_for_base_task(base_task_id, variant=variant)
    support_prompt = _render_support_prompt_block(
        scenario=scenario,
        plan={**plan, "rehydration_bundle": bundle},
        support_strategy=strategy,
    )
    task_context = {
        "task_id": str(task.get("id") or ""),
        "probe_kind": str(task.get("probe_kind") or ""),
        "closure_mode": str(task.get("closure_mode") or "edit_or_proof"),
        "required_branch_mode": str(task.get("required_branch_mode") or ""),
        "required_receipts": [str(item) for item in list(task.get("required_receipts") or []) if str(item)],
        "invalid_finish_trap": bool(task.get("invalid_finish_trap")),
        "allow_shell_branch_proxy": bool(task.get("allow_shell_branch_proxy")),
        "requires_edit_commitment": bool(task.get("requires_edit_commitment")),
        "requires_no_edit_proof": bool(task.get("requires_no_edit_proof")),
    }
    original_prompt = str(task.get("prompt") or "")
    augmented_prompt = "\n".join(
        [
            "[Finish-closure support context]",
            support_prompt,
            "",
            str(contract.get("prompt_block") or ""),
            "",
            "[Task closure rule]",
            f"- closure_mode: {task_context['closure_mode']}",
            f"- required_branch_mode: {task_context['required_branch_mode'] or 'either'}",
            f"- required_receipts: {', '.join(task_context['required_receipts']) or 'none'}",
            f"- invalid_finish_trap: {task_context['invalid_finish_trap']}",
            "",
            "[Current task]",
            original_prompt,
        ]
    )
    return {
        "schema_version": "phase18_finish_closure_protocol_payload_v1",
        "applied": True,
        "family": str(contract.get("family") or ""),
        "support_strategy": strategy,
        "reason": "finish_closure_protocol_injection",
        "base_task_id": base_task_id,
        "base_scenario_id": base_scenario_id,
        "mode": mode,
        "support_payload": support_payload,
        "finish_closure_contract": contract,
        "task_context": task_context,
        "prompt": augmented_prompt,
    }


def build_phase18_finish_closure_context_metadata(protocol_payload: Dict[str, Any]) -> Dict[str, Any]:
    if not bool(protocol_payload.get("applied")):
        return {
            "phase18_finish_closure_protocol_applied": False,
            "phase18_finish_closure_family": "",
            "phase18_finish_closure_support_strategy": "",
        }
    support_payload = dict(protocol_payload.get("support_payload") or {})
    task_context = dict(protocol_payload.get("task_context") or {})
    return {
        "phase18_finish_closure_protocol_applied": True,
        "phase18_finish_closure_family": str(protocol_payload.get("family") or ""),
        "phase18_finish_closure_support_strategy": str(protocol_payload.get("support_strategy") or ""),
        "phase18_finish_closure_base_task_id": str(protocol_payload.get("base_task_id") or ""),
        "phase18_finish_closure_base_scenario_id": str(protocol_payload.get("base_scenario_id") or ""),
        "phase18_finish_closure_support_count": len(list(support_payload.get("support_node_ids") or [])),
        "phase18_task_id": str(task_context.get("task_id") or ""),
        "phase18_probe_kind": str(task_context.get("probe_kind") or ""),
        "phase18_closure_mode": str(task_context.get("closure_mode") or "edit_or_proof"),
        "phase18_required_branch_mode": str(task_context.get("required_branch_mode") or ""),
        "phase18_required_receipts": [str(item) for item in list(task_context.get("required_receipts") or []) if str(item)],
        "phase18_invalid_finish_trap": bool(task_context.get("invalid_finish_trap")),
        "phase17_task_id": str(task_context.get("task_id") or ""),
        "phase17_probe_kind": str(task_context.get("probe_kind") or ""),
        "phase17_closure_mode": str(task_context.get("closure_mode") or "edit_or_proof"),
        "phase17_required_branch_mode": str(task_context.get("required_branch_mode") or ""),
        "phase17_required_receipts": [str(item) for item in list(task_context.get("required_receipts") or []) if str(item)],
        "phase17_invalid_finish_trap": bool(task_context.get("invalid_finish_trap")),
        "phase17_allow_shell_branch_proxy": bool(task_context.get("allow_shell_branch_proxy")),
        "phase17_requires_edit_commitment": bool(task_context.get("requires_edit_commitment")),
        "phase17_requires_no_edit_proof": bool(task_context.get("requires_no_edit_proof")),
    }
