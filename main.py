#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from agentic_coder_prototype.agent import create_agent
from agentic_coder_prototype.parity import EquivalenceLevel
from agentic_coder_prototype.parity_runner import run_parity_checks


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BreadBoard CLI entrypoint")
    parser.add_argument("config", help="Path to agent config YAML")
    parser.add_argument("-t", "--task", help="Task text or file path")
    parser.add_argument("-w", "--workspace", help="Workspace directory")
    parser.add_argument("-i", "--interactive", action="store_true", help="Interactive session")
    parser.add_argument("--max-iterations", type=int, default=None, help="Max loop iterations")
    parser.add_argument("--replay-session", help="Replay session JSON path")
    parser.add_argument("--result-json", help="Write result JSON to path")

    # Parity flags
    parser.add_argument("--parity-fail-mode", choices={"fail", "warn"}, default="fail")
    parser.add_argument("--parity-todo", help="Expected todo journal JSONL")
    parser.add_argument("--parity-guardrails", help="Expected guardrail JSON")
    parser.add_argument("--parity-golden-workspace", help="Golden workspace path")
    parser.add_argument("--parity-summary", help="Expected summary JSON")
    parser.add_argument(
        "--parity-level",
        choices={"semantic", "structural", "normalized_trace", "bitwise_trace"},
        default="normalized_trace",
    )
    return parser.parse_args()


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def main() -> int:
    args = _parse_args()
    config_path = str(Path(args.config).resolve())
    workspace_dir = str(Path(args.workspace).resolve()) if args.workspace else None

    agent = create_agent(config_path, workspace_dir)
    if args.interactive:
        agent.interactive_session()
        return 0

    if not args.task:
        print("No task provided. Use --task or --interactive.", file=sys.stderr)
        return 2

    result = agent.run_task(
        args.task,
        args.max_iterations,
        stream=False,
        replay_session=args.replay_session,
        parity_guardrails=args.parity_guardrails,
    )

    if not isinstance(result, dict):
        print("Agent returned unexpected result", file=sys.stderr)
        return 1

    parity_payload: Optional[Dict[str, Any]] = None
    if args.parity_golden_workspace:
        try:
            level = EquivalenceLevel(args.parity_level)
        except ValueError:
            level = EquivalenceLevel.NORMALIZED_TRACE
        parity_payload = run_parity_checks(
            run_dir=Path(result.get("run_dir") or result.get("logging_dir") or ""),
            golden_workspace=Path(args.parity_golden_workspace),
            guardrail_path=Path(args.parity_guardrails) if args.parity_guardrails else None,
            todo_journal_path=Path(args.parity_todo) if args.parity_todo else None,
            summary_path=Path(args.parity_summary) if args.parity_summary else None,
            target_level=level,
        )

    output = {
        "result": result,
        "parity": parity_payload,
    }
    if args.result_json:
        _write_json(Path(args.result_json), output)

    if parity_payload and parity_payload.get("status") == "failed" and args.parity_fail_mode == "fail":
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
