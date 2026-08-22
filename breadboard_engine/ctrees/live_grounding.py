from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def load_json_if_exists(path: Path) -> Dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_run_artifacts(run_dir: str | Path) -> Dict[str, Any]:
    run_root = Path(run_dir)
    summary_path = run_root / "meta" / "run_summary.json"
    tool_usage_path = run_root / "meta" / "tool_usage.json"
    conversation_path = run_root / "conversation" / "conversation.md"

    summary = load_json_if_exists(summary_path)
    tool_usage = load_json_if_exists(tool_usage_path)
    conversation_excerpt = None
    if conversation_path.exists():
        conversation_excerpt = conversation_path.read_text(encoding="utf-8")[:1200]

    return {
        "run_root": str(run_root),
        "summary_path": str(summary_path),
        "tool_usage_path": str(tool_usage_path),
        "conversation_path": str(conversation_path),
        "run_summary_present": summary is not None,
        "tool_usage_present": tool_usage is not None,
        "conversation_present": conversation_excerpt is not None,
        "run_summary": summary,
        "tool_usage": tool_usage,
        "conversation_excerpt": conversation_excerpt,
    }


def tool_counts_from_artifacts(artifacts: Dict[str, Any]) -> Dict[str, int]:
    run_summary = dict(artifacts.get("run_summary") or {})
    summary_counts = dict(run_summary.get("tool_usage") or {})
    turns_payload = dict((artifacts.get("tool_usage") or {}).get("turns") or {})

    shell_attempts = 0
    shell_successes = 0
    write_calls_fallback = 0
    successful_writes_fallback = 0
    proof_receipt_calls = 0
    verification_receipt_calls = 0
    branch_receipt_calls = 0
    for turn in turns_payload.values():
        tools = list((turn or {}).get("tools") or [])
        for tool in tools:
            name = str((tool or {}).get("name") or "")
            success = bool((tool or {}).get("success"))
            if name == "shell_command":
                shell_attempts += 1
                if success:
                    shell_successes += 1
            if name in {"apply_patch", "write_file"}:
                write_calls_fallback += 1
                if success:
                    successful_writes_fallback += 1
            if name == "record_proof_receipt" and success:
                proof_receipt_calls += 1
            if name == "record_verification_receipt" and success:
                verification_receipt_calls += 1
            if name == "record_branch_decision" and success:
                branch_receipt_calls += 1
            if name == "request_finish_receipt" and success:
                summary_counts["finish_receipt_calls"] = int(summary_counts.get("finish_receipt_calls") or 0) + 1

    return {
        "total_calls": int(summary_counts.get("total_calls") or 0),
        "write_calls": int(summary_counts.get("write_calls") or write_calls_fallback),
        "successful_writes": int(summary_counts.get("successful_writes") or successful_writes_fallback),
        "run_shell_calls": int(summary_counts.get("run_shell_calls") or shell_successes),
        "test_commands": int(summary_counts.get("test_commands") or 0),
        "successful_tests": int(summary_counts.get("successful_tests") or 0),
        "proof_receipt_calls": int(proof_receipt_calls),
        "verification_receipt_calls": int(verification_receipt_calls),
        "branch_receipt_calls": int(branch_receipt_calls),
        "finish_receipt_calls": int(summary_counts.get("finish_receipt_calls") or 0),
        "todo_calls": int(summary_counts.get("todo_calls") or 0),
        "shell_attempts": int(shell_attempts),
    }


def _turn_indices(artifacts: Dict[str, Any]) -> List[int]:
    turns_payload = dict((artifacts.get("tool_usage") or {}).get("turns") or {})
    indices: List[int] = []
    for key in turns_payload.keys():
        try:
            indices.append(int(key))
        except Exception:
            continue
    return sorted(indices)


def _turn_ordinals(artifacts: Dict[str, Any]) -> Dict[int, int]:
    indices = _turn_indices(artifacts)
    return {turn_idx: ordinal for ordinal, turn_idx in enumerate(indices, start=1)}


def first_write_step_from_artifacts(artifacts: Dict[str, Any]) -> int | None:
    turns_payload = dict((artifacts.get("tool_usage") or {}).get("turns") or {})
    ordinals = _turn_ordinals(artifacts)
    for step in _turn_indices(artifacts):
        tools = list((turns_payload.get(str(step)) or {}).get("tools") or [])
        for tool in tools:
            name = str((tool or {}).get("name") or "")
            if name in {"apply_patch", "write_file"}:
                return int(ordinals.get(step) or 0) or None
    return None


def first_verify_step_from_artifacts(artifacts: Dict[str, Any]) -> int | None:
    turns_payload = dict((artifacts.get("tool_usage") or {}).get("turns") or {})
    ordinals = _turn_ordinals(artifacts)
    for step in _turn_indices(artifacts):
        tools = list((turns_payload.get(str(step)) or {}).get("tools") or [])
        for tool in tools:
            name = str((tool or {}).get("name") or "")
            if name == "shell_command":
                return int(ordinals.get(step) or 0) or None
    return None


def no_progress_streak_max_from_artifacts(artifacts: Dict[str, Any]) -> int:
    turns_payload = dict((artifacts.get("tool_usage") or {}).get("turns") or {})
    max_streak = 0
    current = 0
    for step in _turn_indices(artifacts):
        tools = list((turns_payload.get(str(step)) or {}).get("tools") or [])
        productive = False
        for tool in tools:
            name = str((tool or {}).get("name") or "")
            if name in {"apply_patch", "write_file"}:
                productive = True
                break
        if productive:
            current = 0
        else:
            current += 1
            max_streak = max(max_streak, current)
    return max_streak


def classify_grounded_completion(
    *,
    completed: bool,
    reason: str,
    artifacts: Dict[str, Any],
) -> Dict[str, Any]:
    counts = tool_counts_from_artifacts(artifacts)
    grounding_kind = "none"
    grounded_completion = False

    if bool(artifacts.get("run_summary_present")):
        if counts["successful_writes"] > 0:
            grounding_kind = "write"
            grounded_completion = completed
        elif counts["successful_tests"] > 0 or counts["test_commands"] > 0:
            grounding_kind = "test"
            grounded_completion = completed
        elif counts["run_shell_calls"] > 0:
            grounding_kind = "shell"
            grounded_completion = completed

    ungrounded_stop = completed and not grounded_completion and (
        "finish_reason:stop" in reason or "explicit_completion_marker" in reason or reason == "completed"
    )

    return {
        "grounded_completion": grounded_completion,
        "grounding_kind": grounding_kind,
        "ungrounded_stop": ungrounded_stop,
        "completed": completed,
        "raw_reason": reason,
    }


def classify_failure_family(
    *,
    completed: bool,
    reason: str,
    steps: int,
    artifacts: Dict[str, Any],
) -> Dict[str, Any]:
    counts = tool_counts_from_artifacts(artifacts)
    grounded = classify_grounded_completion(completed=completed, reason=reason, artifacts=artifacts)
    run_summary = dict(artifacts.get("run_summary") or {})
    completion_summary = dict(run_summary.get("completion_summary") or {})
    max_steps = int(completion_summary.get("max_steps") or run_summary.get("max_steps") or 0)

    family = "unknown"
    secondary_flags: List[str] = []
    evidence_parts: List[str] = []

    if not artifacts.get("run_summary_present"):
        family = "runner_harness_instability"
        secondary_flags.append("missing_run_summary")
        evidence_parts.append("missing meta/run_summary.json")
    elif "run_loop_exception" in reason or "exception" in reason:
        family = "runner_harness_instability"
        evidence_parts.append(f"reason={reason}")
    elif grounded["ungrounded_stop"]:
        family = "premature_natural_language_stop"
        if counts["total_calls"] == 0:
            secondary_flags.append("zero_tool_episode")
        evidence_parts.append(f"completed={completed}")
        evidence_parts.append(f"reason={reason}")
    elif completed and grounded["grounded_completion"]:
        family = "grounded_completion"
        evidence_parts.append(f"grounding={grounded['grounding_kind']}")
    elif counts["write_calls"] > 0 and counts["successful_tests"] == 0 and counts["test_commands"] == 0:
        family = "patch_without_verification"
        evidence_parts.append(f"write_calls={counts['write_calls']}")
    elif reason == "max_steps_exhausted" and counts["write_calls"] == 0 and counts["test_commands"] == 0:
        family = "read_heavy_no_closure_loop"
        if steps >= max_steps and max_steps > 0:
            secondary_flags.append("budget_bound")
        if counts["shell_attempts"] > 0 and counts["run_shell_calls"] == 0:
            secondary_flags.append("tool_friction")
        evidence_parts.append(f"steps={steps}/{max_steps or steps}")
        evidence_parts.append(f"shell_attempts={counts['shell_attempts']}")
    elif reason == "max_steps_exhausted":
        family = "budget_difficulty_mismatch"
        secondary_flags.append("budget_bound")
        evidence_parts.append(f"steps={steps}/{max_steps or steps}")
    else:
        evidence_parts.append(f"reason={reason}")

    if grounded["grounding_kind"] == "none" and completed:
        secondary_flags.append("weak_grounding")
    if counts["test_commands"] > 0 and not grounded["grounded_completion"]:
        secondary_flags.append("late_verify")

    return {
        "failure_family": family,
        "secondary_flags": sorted(set(secondary_flags)),
        "evidence_note": "; ".join(evidence_parts),
    }


def summarize_live_run(
    *,
    run_dir: str | Path | None,
    completion_summary: Dict[str, Any] | None,
) -> Dict[str, Any]:
    if not run_dir:
        return {
            "artifacts": {
                "run_summary_present": False,
                "tool_usage_present": False,
                "conversation_present": False,
                "run_root": "",
            },
            "tool_counts": {
                "total_calls": 0,
                "write_calls": 0,
                "successful_writes": 0,
                "run_shell_calls": 0,
                "test_commands": 0,
                "successful_tests": 0,
                "todo_calls": 0,
                "shell_attempts": 0,
            },
            "grounded": {
                "grounded_completion": False,
                "grounding_kind": "none",
                "ungrounded_stop": False,
                "completed": bool((completion_summary or {}).get("completed")),
                "raw_reason": str((completion_summary or {}).get("reason") or ""),
            },
            "classification": {
                "failure_family": "runner_harness_instability",
                "secondary_flags": ["missing_run_dir"],
                "evidence_note": "missing run_dir",
            },
        }

    artifacts = load_run_artifacts(Path(run_dir))
    completed = bool((completion_summary or {}).get("completed"))
    reason = str((completion_summary or {}).get("reason") or "")
    steps = int((completion_summary or {}).get("steps_taken") or (completion_summary or {}).get("steps") or 0)
    grounded = classify_grounded_completion(
        completed=completed,
        reason=reason,
        artifacts=artifacts,
    )
    first_write_step = first_write_step_from_artifacts(artifacts)
    first_verify_step = first_verify_step_from_artifacts(artifacts)
    no_progress_streak_max = no_progress_streak_max_from_artifacts(artifacts)
    verified_completion = bool(grounded["grounded_completion"] and tool_counts_from_artifacts(artifacts)["successful_tests"] > 0)
    completion_gate_satisfied = bool(grounded["grounded_completion"])

    return {
        "artifacts": {
            "run_summary_present": bool(artifacts.get("run_summary_present")),
            "tool_usage_present": bool(artifacts.get("tool_usage_present")),
            "conversation_present": bool(artifacts.get("conversation_present")),
            "run_root": str(artifacts.get("run_root") or ""),
        },
        "tool_counts": tool_counts_from_artifacts(artifacts),
        "controller_metrics": {
            "first_write_step": first_write_step,
            "first_verify_step": first_verify_step,
            "no_progress_streak_max": no_progress_streak_max,
            "completion_gate_satisfied": completion_gate_satisfied,
            "verified_completion": verified_completion,
        },
        "grounded": grounded,
        "classification": classify_failure_family(
            completed=completed,
            reason=reason,
            steps=steps,
            artifacts=artifacts,
        ),
    }
