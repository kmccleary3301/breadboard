from __future__ import annotations

from typing import Any, Dict, Iterable, List

from .downstream_task_pack import (
    build_phase11_downstream_benchmark_tasks,
    build_phase12_calibration_tasks,
    build_phase13_closure_floor_tasks,
    build_phase14_executor_probe_tasks,
    build_phase15_verification_receipt_probe_tasks,
    build_phase17_branch_receipt_probe_tasks,
    build_phase18_finish_closure_probe_tasks,
    build_phase19_anchor_slice_tasks,
    build_phase19_anchor_slice_holdout_tasks,
)


ClosureRule = Dict[str, Any]


def _normalize_rule(
    *,
    task_id: str,
    closure_mode: str,
    invalid_finish_trap: bool = False,
    note: str = "",
    required_branch_mode: str = "",
    requires_finish_request: bool = False,
) -> ClosureRule:
    return {
        "task_id": task_id,
        "closure_mode": closure_mode,
        "invalid_finish_trap": bool(invalid_finish_trap),
        "required_branch_mode": required_branch_mode,
        "requires_finish_request": bool(requires_finish_request),
        "requires_edit_receipt": closure_mode == "edit_required",
        "allows_proof_receipt": closure_mode in {"proof_required", "edit_or_proof"},
        "note": note,
    }


def _phase14_probe_rules() -> Iterable[ClosureRule]:
    for task in build_phase14_executor_probe_tasks():
        task_id = str(task.get("id") or "")
        if bool(task.get("requires_edit_commitment")):
            yield _normalize_rule(
                task_id=task_id,
                closure_mode="edit_required",
                note="explicit edit commitment required by probe metadata",
            )
        elif bool(task.get("requires_no_edit_proof")):
            yield _normalize_rule(
                task_id=task_id,
                closure_mode="proof_required",
                note="explicit no-edit proof path required by probe metadata",
            )
        elif bool(task.get("invalid_finish_trap")):
            yield _normalize_rule(
                task_id=task_id,
                closure_mode="edit_or_proof",
                invalid_finish_trap=True,
                note="invalid finish trap requires task-aware branch commitment",
            )


def _phase15_probe_rules() -> Iterable[ClosureRule]:
    for task in build_phase15_verification_receipt_probe_tasks():
        task_id = str(task.get("id") or "")
        closure_mode = str(task.get("closure_mode") or "unknown")
        yield _normalize_rule(
            task_id=task_id,
            closure_mode=closure_mode,
            invalid_finish_trap=bool(task.get("invalid_finish_trap")),
            note="explicit phase15 verification receipt probe closure rule",
        )


def _phase17_probe_rules() -> Iterable[ClosureRule]:
    for task in build_phase17_branch_receipt_probe_tasks():
        task_id = str(task.get("id") or "")
        closure_mode = str(task.get("closure_mode") or "unknown")
        yield _normalize_rule(
            task_id=task_id,
            closure_mode=closure_mode,
            invalid_finish_trap=bool(task.get("invalid_finish_trap")),
            required_branch_mode=str(task.get("required_branch_mode") or ""),
            note="explicit phase17 branch-receipt probe closure rule",
        )


def _phase18_probe_rules() -> Iterable[ClosureRule]:
    for task in build_phase18_finish_closure_probe_tasks():
        task_id = str(task.get("id") or "")
        closure_mode = str(task.get("closure_mode") or "unknown")
        yield _normalize_rule(
            task_id=task_id,
            closure_mode=closure_mode,
            invalid_finish_trap=bool(task.get("invalid_finish_trap")),
            required_branch_mode=str(task.get("required_branch_mode") or ""),
            requires_finish_request=True,
            note="explicit phase18 finish-closure probe closure rule",
        )


def _phase13_floor_rules() -> Iterable[ClosureRule]:
    explicit_modes = {
        "phase13_floor_interrupt_single_edit_v1": ("edit_required", "single-edit floor task"),
        "phase13_floor_dependency_single_fact_v1": ("edit_required", "single-fact dependency floor task"),
        "phase13_floor_pivot_single_target_v1": ("edit_or_proof", "prompt explicitly allows no-change if justified"),
        "phase13_floor_subtree_single_child_v1": ("edit_or_proof", "prompt explicitly allows grounded no-change decision"),
    }
    for task in build_phase13_closure_floor_tasks():
        task_id = str(task.get("id") or "")
        closure_mode, note = explicit_modes[task_id]
        yield _normalize_rule(task_id=task_id, closure_mode=closure_mode, note=note)


def _phase12_calibration_rules() -> Iterable[ClosureRule]:
    explicit_modes = {
        "phase12_calibration_interrupt_focus_v1": ("edit_or_proof", "minimal fix if needed"),
        "phase12_calibration_dependency_focus_v1": ("edit_or_proof", "ground next action; edit is not always required"),
        "phase12_calibration_pivot_focus_v1": ("edit_or_proof", "prompt explicitly allows no-edit decision"),
        "phase12_calibration_subtree_focus_v1": ("edit_or_proof", "repair only if justified"),
    }
    for task in build_phase12_calibration_tasks():
        task_id = str(task.get("id") or "")
        closure_mode, note = explicit_modes[task_id]
        yield _normalize_rule(task_id=task_id, closure_mode=closure_mode, note=note)


def _phase11_benchmark_rules() -> Iterable[ClosureRule]:
    explicit_modes = {
        "downstream_interrupt_repair_v1": (
            "edit_required",
            "interrupted continuation repair requires a concrete code repair before close",
        ),
        "downstream_dependency_shift_v1": (
            "edit_or_proof",
            "dependency-shift continuation may close on an explicit no-change proof, but shell-only evidence is insufficient",
        ),
        "downstream_semantic_pivot_v1": (
            "edit_required",
            "semantic pivot continuation requires the requested target update, not just evidence gathering",
        ),
        "downstream_subtree_pressure_v1": (
            "edit_required",
            "subtree-pressure continuation requires the targeted change, not only subtree inspection",
        ),
    }
    for task in build_phase11_downstream_benchmark_tasks(expand_repeats=True):
        task_id = str(task.get("id") or "")
        base_task_id = str(task.get("base_task_id") or "")
        closure_mode, note = explicit_modes[base_task_id]
        yield _normalize_rule(task_id=task_id, closure_mode=closure_mode, note=note)


def _phase19_anchor_slice_rules() -> Iterable[ClosureRule]:
    for task in list(build_phase19_anchor_slice_tasks()) + list(build_phase19_anchor_slice_holdout_tasks()):
        task_id = str(task.get("id") or "")
        base_task_id = str(task.get("base_task_id") or "")
        base_rule_map = {rule["task_id"]: rule for rule in _phase11_benchmark_rules()}
        base_rule = dict(base_rule_map.get(task_id) or {})
        if not base_rule:
            continue
        yield _normalize_rule(
            task_id=task_id,
            closure_mode=str(base_rule.get("closure_mode") or "unknown"),
            invalid_finish_trap=bool(base_rule.get("invalid_finish_trap")),
            note="phase19 anchor-slice task inherits unchanged-anchor closure rule",
        )


def build_phase15_task_closure_rule_map() -> Dict[str, ClosureRule]:
    rules: Dict[str, ClosureRule] = {}
    for rule in (
        list(_phase11_benchmark_rules())
        + list(_phase15_probe_rules())
        + list(_phase14_probe_rules())
        + list(_phase13_floor_rules())
        + list(_phase12_calibration_rules())
    ):
        rules[str(rule["task_id"])] = rule
    return rules


def build_phase17_task_closure_rule_map() -> Dict[str, ClosureRule]:
    rules = build_phase15_task_closure_rule_map()
    for rule in list(_phase17_probe_rules()):
        rules[str(rule["task_id"])] = rule
    return rules


def build_phase18_task_closure_rule_map() -> Dict[str, ClosureRule]:
    rules = build_phase17_task_closure_rule_map()
    for rule in list(_phase18_probe_rules()):
        rules[str(rule["task_id"])] = rule
    for rule in list(_phase19_anchor_slice_rules()):
        rules[str(rule["task_id"])] = rule
    return rules
