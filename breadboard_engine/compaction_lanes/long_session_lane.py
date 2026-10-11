"""long session lane responsibility owner."""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, Optional
from breadboard_engine.compaction_lanes.mock_provider import MockProvider
from .pi_episode import run_breadboard_pi_episode
from .pi_requests import compare_recorded_requests, is_summary_request
from .pi_scenarios import build_final_over_threshold_scenario, build_long_session_scenario, build_repeated_failure_scenario
from .pi_stock import run_stock_pi
from .pi_target import REPO_ROOT, get_target_deviations, load_target_document, resolve_pi_cli, resolve_target_harness_path
from .request_bytes import raw_request_differences

def run_pi_0_73_1_lane(
    out_dir: Path,
    target_id: str = "pi-r3@0.73.1",
    context_window: Optional[int] = None,
    scenario: str = "overflow_recovery",
    runner: str = "rpc",
) -> Dict[str, Any]:
    """Execute either pinned Pi version's long-session lane end-to-end."""
    out_dir.mkdir(parents=True, exist_ok=True)
    version = target_id.split("@", 1)[1]
    pi_cli = resolve_pi_cli(version=version)
    native_config = load_target_document(target_id, "native-config.json")
    pinned_window = native_config["model"]["context_window"]
    compaction_settings = native_config["agent"]["compaction_settings"]
    if context_window is not None and context_window != pinned_window:
        raise ValueError("The lane requires the target's pinned context window")
    context_window = pinned_window
    reserve_tokens = compaction_settings["reserveTokens"]
    keep_recent_tokens = compaction_settings["keepRecentTokens"]

    deviations = get_target_deviations(target_id)
    if scenario == "long_session":
        scenario_script = build_long_session_scenario(context_window=context_window)
        task_prompt = "Perform analysis of workspace files"
    elif scenario == "final_over_threshold":
        scenario_script = build_final_over_threshold_scenario(context_window=context_window, reserve_tokens=reserve_tokens)
        task_prompt = "Execute task"
    elif scenario == "repeated_failure":
        scenario_script = build_repeated_failure_scenario(context_window=context_window)
        task_prompt = "Hello from user. " + ("Here is important context info. " * 3000)
    else:  # "overflow_recovery"
        task_prompt = "Perform multi-step investigation of workspace files"
        scenario_script = [
            # Req 0: Initial prompt -> Tool call 1
            {
                "type": "tool_call",
                "name": "bash",
                "call_id": "call_1",
                "arguments": json.dumps({"command": "echo 'investigation step 1 complete'"}),
                "usage": {"input_tokens": 1000, "output_tokens": 50, "total_tokens": 1050},
            },
            # Req 1: Tool result 1 -> Tool call 2
            {
                "type": "tool_call",
                "name": "bash",
                "call_id": "call_2",
                "arguments": json.dumps({"command": "echo 'investigation step 2 complete'"}),
                "usage": {"input_tokens": 1200, "output_tokens": 50, "total_tokens": 1250},
            },
            # Req 2: Tool result 2 -> 400 overflow (triggers compaction mid-loop)
            {
                "type": "error",
                "error": "context_length_exceeded",
                "message": f"This model maximum context length is {context_window} tokens.",
                "status_code": 400,
            },
            # Req 3: Compaction summary request
            {
                "type": "text",
                "text": (
                    "## Goal\nInvestigate workspace files\n\n## Constraints & Preferences\n- (none)\n\n"
                    "## Progress\n### Done\n- Investigation steps 1 and 2 complete\n\n### In Progress\n- Final synthesis\n\n"
                    "## Key Decisions\n- (none)\n\n## Next Steps\n1. Complete analysis"
                ),
                "usage": {"input_tokens": 1500, "output_tokens": 100, "total_tokens": 1600},
            },
            # Req 4: Retried request with compacted history -> Assistant completes turn
            {
                "type": "text",
                "text": "Investigation finished successfully. All steps completed.",
                "usage": {"input_tokens": 1800, "output_tokens": 30, "total_tokens": 1830},
            },
        ]
    # 1. Run Stock Pi against mock provider
    stock_record_file = out_dir / "stock_requests.jsonl"
    stock_mock = MockProvider(script=scenario_script, record_path=stock_record_file)
    stock_mock.start()

    with tempfile.TemporaryDirectory(prefix="stock_pi_lane_") as stock_tmp:
        stock_workdir = Path(stock_tmp)
        try:
            stock_proc = run_stock_pi(
                pi_cli,
                stock_mock.base_url,
                stock_workdir,
                task_prompt,
                target_id=target_id,
                context_window=context_window,
                reserve_tokens=reserve_tokens,
                keep_recent_tokens=keep_recent_tokens,
                runner=runner,
            )
        finally:
            stock_mock.stop()

    stock_requests = [
        json.loads(line)
        for line in stock_record_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    # 2. Replay through BreadBoard conductor
    bb_record_file = out_dir / "breadboard_requests.jsonl"
    bb_mock = MockProvider(script=scenario_script, record_path=bb_record_file)
    bb_mock.start()

    with tempfile.TemporaryDirectory(prefix="bb_pi_lane_") as bb_tmp:
        bb_workdir = Path(bb_tmp)
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            bb_result, bb_events, limit_report = loop.run_until_complete(
                run_breadboard_pi_episode(
                    bb_mock.base_url,
                    bb_workdir,
                    task_prompt,
                    target_id=target_id,
                    context_window=context_window,
                    terminal_overflow=scenario in ("long_session", "repeated_failure"),
                )
            )
        finally:
            loop.close()
            asyncio.set_event_loop(None)
            bb_mock.stop()

    bb_requests = [
        json.loads(line)
        for line in bb_record_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    trace_count = limit_report["bb_replay_trace_request_count"]
    if trace_count is not None:
        assert trace_count == len(bb_requests), "Replay trace must record every physical HTTP request exactly once"

    # 3. Compare request bodies
    deviation_records = []
    passed, diffs = compare_recorded_requests(stock_requests, bb_requests, deviations, target_id=target_id, deviation_records=deviation_records)

    harness_name = f"pi@{version}"
    summary = {
        "harness": harness_name,
        "target_id": target_id,
        "runner": runner,
        "scenario": scenario,
        "context_window": context_window,
        "compaction_settings": compaction_settings,
        "native_config_source": str(resolve_target_harness_path(target_id).with_name("native-config.json").relative_to(REPO_ROOT)),
        "stock_request_count": len(stock_requests),
        **limit_report,
        "bb_terminal_error": str(bb_result) if isinstance(bb_result, Exception) else None,
        "bb_request_count": len(bb_requests),
        "stock_compaction_count": sum(is_summary_request(req["json"]) for req in stock_requests),
        "bb_compaction_count": sum(is_summary_request(req["json"]) for req in bb_requests),
        "deviations_admitted": deviations,
        "diff_count": len(diffs),
        "diffs": diffs,
        "raw_differences": raw_request_differences(stock_requests, bb_requests),
        "deviation_records": deviation_records,
        "comparison_basis": "Raw HTTP request bytes after named source-guarded edits; representation differences fail by default",
        "passed": passed,
    }

    (out_dir / "lane_report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary

def main() -> None:
    parser = argparse.ArgumentParser(description="Long-session compaction byte-for-byte lane.")
    parser.add_argument("--harness", required=True, help="Target harness (e.g. pi@0.73.1, pi@0.57.1)")
    parser.add_argument("--target-id", default=None, help="BreadBoard target id override (default: auto)")
    parser.add_argument("--runner", default="rpc", choices=["rpc", "print"], help="Stock runner mode: rpc (parity) or print (reference)")
    parser.add_argument("--scenario", default="overflow_recovery", choices=["overflow_recovery", "long_session", "repeated_failure", "final_over_threshold"], help="Scenario to run")
    parser.add_argument("--context-window", type=int, default=None, help="Must equal the selected target's pinned model context window")
    parser.add_argument("--out", required=True, type=Path, help="Output directory for recordings and diffs")
    args = parser.parse_args()

    target_id = args.target_id
    if not target_id:
        if "0.73.1" in args.harness:
            target_id = "pi-r3@0.73.1"
        elif "0.57.1" in args.harness:
            target_id = "pi-r4@0.57.1"
        else:
            target_id = args.harness

    report = run_pi_0_73_1_lane(
        args.out,
        target_id=target_id,
        context_window=args.context_window,
        scenario=args.scenario,
        runner=args.runner,
    )
    print(json.dumps(report, indent=2))
    if not report.get("passed"):
        sys.exit(1)
