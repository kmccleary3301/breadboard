#!/usr/bin/env python3
"""
Replay an OpenCode session using the native KyleCode harness.

This is now a thin wrapper around `main.py` that wires the replay session
and parity expectation files into the engine so the full conductor logic
is exercised.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import shutil
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent


@dataclass
class ToolCall:
    tool: str
    input: Dict[str, Any]
    status: str
    output: str
    metadata: Dict[str, Any]

    def expected_exit(self) -> Optional[int]:
        try:
            exit_code = self.metadata.get("exit")
        except Exception:
            exit_code = None
        if exit_code is None:
            return None
        try:
            return int(exit_code)
        except Exception:
            return None


def load_opencode_tool_calls(session_path: Path) -> List[ToolCall]:
    payload = json.loads(Path(session_path).read_text(encoding="utf-8"))
    calls: List[ToolCall] = []
    for msg in payload or []:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        for part in msg.get("parts") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") != "tool":
                continue
            tool = str(part.get("tool") or part.get("name") or "")
            state = ((part.get("meta") or {}).get("state") or {})
            calls.append(
                ToolCall(
                    tool=tool,
                    input=dict(state.get("input") or {}),
                    status=str(state.get("status") or ""),
                    output=str(state.get("output") or ""),
                    metadata=dict(state.get("metadata") or {}),
                )
            )
    return calls


def parse_iterable_status(status: str) -> Tuple[str, Optional[int]]:
    try:
        data = ast.literal_eval(status)
    except Exception:
        return str(status), None
    if not isinstance(data, list):
        return str(status), None
    stdout_lines: List[str] = []
    exit_code: Optional[int] = None
    for item in data:
        if isinstance(item, dict) and "exit" in item:
            try:
                exit_code = int(item.get("exit"))
            except Exception:
                exit_code = None
            continue
        if isinstance(item, str):
            if item.strip() == ">>>>> ITERABLE":
                continue
            stdout_lines.append(item)
    stdout = "\n".join(stdout_lines)
    return stdout, exit_code


def summarize_result(tool: str, raw_result: Dict[str, Any]) -> Dict[str, Any]:
    stdout = ""
    exit_code: Optional[int] = None
    status = raw_result.get("status")
    if isinstance(status, str) and "ITERABLE" in status:
        stdout, exit_code = parse_iterable_status(status)
    elif isinstance(status, list):
        stdout, exit_code = parse_iterable_status(str(status))
    else:
        if isinstance(raw_result.get("stdout"), str):
            stdout = raw_result.get("stdout") or ""
        elif isinstance(raw_result.get("output"), str):
            stdout = raw_result.get("output") or ""
        try:
            exit_code = int(raw_result.get("exit"))
        except Exception:
            exit_code = raw_result.get("exit_code")
            if exit_code is not None:
                try:
                    exit_code = int(exit_code)
                except Exception:
                    exit_code = None
    return {"stdout": stdout, "exit": exit_code}


def compare_expected(call: ToolCall, summary: Dict[str, Any], path_normalizers: Iterable[str]) -> List[str]:
    mismatches: List[str] = []
    expected_exit = call.expected_exit()
    actual_exit = summary.get("exit")
    if expected_exit is not None and actual_exit != expected_exit:
        mismatches.append(f"exit mismatch (expected {expected_exit}, got {actual_exit})")

    expected_stdout = call.output or ""
    actual_stdout = summary.get("stdout") or ""

    for prefix in path_normalizers or []:
        if not prefix:
            continue
        expected_stdout = expected_stdout.replace(prefix, "<PATH>")
        actual_stdout = actual_stdout.replace(prefix, "<PATH>")

    if expected_stdout and not actual_stdout:
        mismatches.append("expected stdout present but summary was empty")
    elif expected_stdout and expected_stdout != actual_stdout:
        mismatches.append("stdout mismatch")

    return mismatches


def _normalize_path(path: str, workspace_path: Path, strip_prefix: Optional[str]) -> str:
    raw = str(path or "")
    if strip_prefix and raw.startswith(strip_prefix):
        raw = raw[len(strip_prefix) :]
        raw = raw.lstrip("/\\")
    else:
        try:
            raw = str(Path(raw).resolve().relative_to(workspace_path.resolve()))
        except Exception:
            raw = raw.lstrip("/\\")
    return raw


async def replay_tool_call(
    executor: Any,
    tool: str,
    payload: Dict[str, Any],
    *,
    workspace_path: Path,
    todo_manager: Any,
    strip_prefix: Optional[str] = None,
) -> Any:
    tool = str(tool or "")
    args: Dict[str, Any] = {}
    if tool == "read":
        args = {
            "path": _normalize_path(payload.get("filePath", ""), workspace_path, strip_prefix),
            "offset": payload.get("offset"),
            "limit": payload.get("limit"),
        }
        call = {"function": "read", "arguments": args}
    elif tool == "list":
        args = {
            "path": _normalize_path(payload.get("path", ""), workspace_path, strip_prefix),
            "depth": payload.get("depth"),
        }
        call = {"function": "list", "arguments": args}
    elif tool == "edit":
        replace_all = bool(payload.get("replaceAll", False))
        args = {
            "path": _normalize_path(payload.get("filePath", ""), workspace_path, strip_prefix),
            "old": payload.get("oldString"),
            "new": payload.get("newString"),
            "count": 0 if replace_all else 1,
        }
        call = {"function": "edit", "arguments": args}
    else:
        # default passthrough
        call = {"function": tool, "arguments": dict(payload or {})}

    return await executor.execute_tool_call(call)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay OpenCode session via native harness.")
    parser.add_argument("--config", required=True, help="Path to KyleCode agent config (YAML).")
    parser.add_argument("--session", required=True, help="Path to recorded OpenCode session JSON.")
    parser.add_argument("--workspace", default=str(ROOT_DIR / "opencode_replay_ws"), help="Workspace directory.")
    parser.add_argument("--limit", type=int, default=40, help="Maximum iterations (default: 40).")
    parser.add_argument("--todo-expected", help="Expected todo journal JSONL path.")
    parser.add_argument("--guardrail-expected", help="Expected guardrail events JSON path.")
    parser.add_argument("--golden-workspace", help="Golden workspace directory for file diff.")
    parser.add_argument("--parity-summary", help="Expected completion summary JSON path.")
    parser.add_argument("--result-json", help="Path to store the final session result JSON.")
    parser.add_argument(
        "--parity-fail-mode",
        choices={"fail", "warn"},
        default="fail",
        help="Fail run on parity mismatch (default: fail).",
    )
    parser.add_argument(
        "--parity-level",
        choices={"semantic", "structural", "normalized_trace", "bitwise_trace"},
        default="normalized_trace",
        help="Equivalence level required for parity success.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = Path(args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    result_json = Path(args.result_json).resolve() if args.result_json else workspace / "session_result.json"

    env = os.environ.copy()
    env.setdefault("RAY_SCE_SKIP_LSP", "1")

    # Auto-seed Claude Code replay fixtures when present.
    # Convention: `misc/claude_code_fixtures/<scenario>/` where scenario is the
    # parent directory name of the replay session JSON.
    try:
        session_path = Path(args.session).resolve()
        scenario = session_path.parent.name
        fixture_dir = (ROOT_DIR / "misc" / "claude_code_fixtures" / scenario).resolve()
        if fixture_dir.is_dir():
            shutil.copytree(fixture_dir, workspace, dirs_exist_ok=True)
            env.setdefault("PRESERVE_SEEDED_WORKSPACE", "1")
    except Exception:
        pass

    cmd = [
        sys.executable,
        str(ROOT_DIR / "main.py"),
        str(Path(args.config).resolve()),
        "--workspace",
        str(workspace),
        "--task",
        str(Path(args.session).resolve()),
        "--max-iterations",
        str(args.limit),
        "--replay-session",
        str(Path(args.session).resolve()),
        "--parity-fail-mode",
        args.parity_fail_mode,
        "--result-json",
        str(result_json),
    ]
    if args.todo_expected:
        cmd += ["--parity-todo", str(Path(args.todo_expected).resolve())]
    if args.guardrail_expected:
        cmd += ["--parity-guardrails", str(Path(args.guardrail_expected).resolve())]
    if args.golden_workspace:
        cmd += ["--parity-golden-workspace", str(Path(args.golden_workspace).resolve())]
    if args.parity_summary:
        cmd += ["--parity-summary", str(Path(args.parity_summary).resolve())]
    if args.parity_level:
        cmd += ["--parity-level", args.parity_level]

    subprocess.run(cmd, cwd=ROOT_DIR, env=env, check=True)


if __name__ == "__main__":
    main()
