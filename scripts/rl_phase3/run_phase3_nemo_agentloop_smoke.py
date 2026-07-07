from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

SMOKE_SCHEMA = "bb.rl.phase3.nemo_agentloop_smoke.v1"
SMOKE_ID = "phase3_nemo_agentloop_smoke"
SMOKE_BOUNDARY = "phase3_nemo_agentloop_smoke_wrapper_target_scope"
SMOKE_BLOCKED_BOUNDARY = "phase3_nemo_agentloop_smoke_blocked_scope"


def _sha(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


def _score_tool_call(expected: Mapping[str, Any], actual: ToolCall | None) -> float:
    if expected.get("type") == "message":
        return 1.0 if actual is None else 0.0
    if expected.get("type") != "function_call" or actual is None:
        return 0.0
    expected_name = str(expected.get("name") or "")
    expected_args = expected.get("arguments") if isinstance(expected.get("arguments"), Mapping) else {}
    return 1.0 if actual.name == expected_name and dict(actual.arguments) == dict(expected_args) else 0.0


def _row() -> dict[str, Any]:
    return {
        "id": "phase3-nemo-agentloop-smoke",
        "messages": [
            {"role": "system", "content": "Call exactly one tool when the answer requires a weather lookup."},
            {"role": "user", "content": "What is the weather in Paris?"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Return the current weather for a city.",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }
        ],
        "expected_action": {"type": "function_call", "name": "get_weather", "arguments": {"city": "Paris"}},
    }


def _dependency_status(wrapper_dir: Path) -> dict[str, Any]:
    status = {"wrapper_dir": str(wrapper_dir), "nemo_gym_loop_path": str(wrapper_dir / "src" / "zyphra_verl" / "nemo_gym_loop.py")}
    try:
        source = (wrapper_dir / "src" / "zyphra_verl" / "nemo_gym_loop.py").read_text()
    except OSError as exc:
        status.update({"canonical_agentloop_source_present": False, "blocked_reason": exc.__class__.__name__})
        return status
    required_terms = ("register(\"nemo_gym_tool_use\"", "ToolParser", "ToolCallComparator", "reward_score")
    status["canonical_agentloop_source_present"] = all(term in source for term in required_terms)
    status["nemo_gym_loop_sha256"] = _sha(source.encode())
    status["required_source_terms"] = list(required_terms)
    try:
        import verl  # type: ignore  # noqa: F401
        import nemo_gym  # type: ignore  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        status["canonical_runtime_imports_present"] = False
        status["runtime_import_blocked_reason"] = exc.__class__.__name__
    else:
        status["canonical_runtime_imports_present"] = True
        status["runtime_import_blocked_reason"] = ""
    return status


def build_smoke_report(
    *,
    wrapper_dir: Path,
    target_run_id: str,
    require_canonical_runtime: bool,
    local_diagnostic: bool,
    canonical_mode: bool = False,
) -> dict[str, Any]:
    row = _row()
    expected = row["expected_action"]
    gold = ToolCall("get_weather", {"city": "Paris"})
    wrong_name = ToolCall("get_forecast", {"city": "Paris"})
    wrong_args = ToolCall("get_weather", {"city": "Lyon"})
    controls = {
        "gold_reward": _score_tool_call(expected, gold),
        "wrong_name_reward": _score_tool_call(expected, wrong_name),
        "wrong_args_reward": _score_tool_call(expected, wrong_args),
        "missing_call_reward": _score_tool_call(expected, None),
    }
    dependency = _dependency_status(wrapper_dir)
    hashes = {
        "row_sha256": _sha(_canonical_json(row)),
        "messages_sha256": _sha(_canonical_json({"messages": row["messages"]})),
        "tools_sha256": _sha(_canonical_json({"tools": row["tools"]})),
        "expected_action_sha256": _sha(_canonical_json(expected)),
        "gold_tool_call_sha256": _sha(_canonical_json({"name": gold.name, "arguments": gold.arguments})),
        "negative_tool_call_sha256": _sha(_canonical_json({"name": wrong_name.name, "arguments": wrong_name.arguments})),
    }
    control_passed = controls == {
        "gold_reward": 1.0,
        "wrong_name_reward": 0.0,
        "wrong_args_reward": 0.0,
        "missing_call_reward": 0.0,
    }
    canonical_source = dependency.get("canonical_agentloop_source_present") is True
    canonical_runtime = dependency.get("canonical_runtime_imports_present") is True
    canonical_required = canonical_mode or require_canonical_runtime
    canonical_agentloop_executed = False
    canonical_tool_parser_used = False
    canonical_comparator_used = False
    canonical_reward_score_observed = False
    diagnostic_passed = control_passed and canonical_source and (canonical_runtime or not require_canonical_runtime)
    passed = (
        canonical_required
        and control_passed
        and canonical_source
        and canonical_runtime
        and canonical_agentloop_executed
        and canonical_tool_parser_used
        and canonical_comparator_used
        and canonical_reward_score_observed
        and not local_diagnostic
    )
    blocked_reasons = []
    if not control_passed:
        blocked_reasons.append("reward_control_failed")
    if not canonical_source:
        blocked_reasons.append("canonical_agentloop_source_missing")
    if canonical_required and not canonical_runtime:
        blocked_reasons.append("canonical_runtime_imports_missing")
    if canonical_required and canonical_runtime and not canonical_agentloop_executed:
        blocked_reasons.append("canonical_agentloop_execution_missing")
    if not canonical_required:
        blocked_reasons.append("diagnostic_only_not_promotional")
    if local_diagnostic:
        blocked_reasons.append("local_diagnostic_not_promotional")
    return {
        "schema_version": SMOKE_SCHEMA,
        "report_id": SMOKE_ID,
        "component": "nemo_gym_agentloop_smoke",
        "claim_boundary": SMOKE_BOUNDARY if passed else SMOKE_BLOCKED_BOUNDARY,
        "target_run_id": target_run_id,
        "mode": "canonical" if canonical_required else "diagnostic",
        "row": row,
        "controls": controls,
        "hashes": hashes,
        "dependency_status": dependency,
        "canonical_runtime_required": canonical_required,
        "canonical_agentloop_executed": canonical_agentloop_executed,
        "canonical_tool_parser_used": canonical_tool_parser_used,
        "canonical_comparator_used": canonical_comparator_used,
        "canonical_reward_score_observed": canonical_reward_score_observed,
        "breadboard_toy_reward_used": True,
        "diagnostic_passed": diagnostic_passed,
        "local_diagnostic": local_diagnostic,
        "promotional": False,
        "scorecard_update_allowed": False,
        "passed": passed,
        "blocked_reason": ";".join(blocked_reasons),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase-dir", required=True, type=Path)
    parser.add_argument("--wrapper-dir", required=True, type=Path)
    parser.add_argument("--target-run-id", required=True)
    parser.add_argument("--require-canonical-runtime", action="store_true")
    parser.add_argument("--canonical", action="store_true")
    parser.add_argument("--local-diagnostic", action="store_true")
    parser.add_argument("--emit-component-json", action="store_true")
    args = parser.parse_args()
    report = build_smoke_report(
        wrapper_dir=args.wrapper_dir,
        target_run_id=args.target_run_id,
        require_canonical_runtime=args.require_canonical_runtime,
        local_diagnostic=args.local_diagnostic,
        canonical_mode=args.canonical,
    )
    output = args.phase_dir / "runs" / "nemo_agentloop_smoke" / "phase3_nemo_agentloop_smoke.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    if args.emit_component_json:
        component = {
            "schema_version": "bb.rl.phase3.component_report.v1",
            "report_id": "p3_aux_nemo_agentloop_smoke",
            "component": "nemo_gym_agentloop_smoke",
            "claim_boundary": report["claim_boundary"],
            "target_run_id": args.target_run_id,
            "points": 0,
            "passed": report["passed"],
            "blocked_reason": report["blocked_reason"],
            "smoke_report": report,
            "input_hashes": {"smoke_report": _sha(output.read_bytes())},
            "artifact_paths": {"smoke_report": str(output)},
            "required_artifact_keys": ["smoke_report"],
            "scorecard_update_allowed": False,
        }
        print("PHASE3_COMPONENT_REPORT_JSON=" + json.dumps(component, sort_keys=True))
    print(json.dumps({"report": str(output), "passed": report["passed"], "blocked_reason": report["blocked_reason"]}, sort_keys=True))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
