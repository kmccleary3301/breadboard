"""Native Pi 0.73.1 tool dispatch, separate from the immutable Pi 0.57.1 adapter.

The adapter delegates source-derived execution to ``runners.pi_semantics`` and
keeps the stdin protocol intentionally compatible with ``pi_tools.mjs`` for
focused harness smoke tests.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Mapping, Sequence

from breadboard.rl.harness.runners.pi_semantics import PiToolResult, execute_pi_tool


def execute_native_tool(
    tool_id: str,
    arguments: Mapping[str, Any] | Any,
    *,
    cwd: str | os.PathLike[str],
    image_delivery: bool = False,
    call_id: str = "",
) -> dict[str, Any]:
    result = execute_pi_tool(tool_id, arguments, cwd, image_delivery=image_delivery)
    result = PiToolResult(call_id or result.call_id, result.name, result.content, result.is_error, result.details, result.terminate)
    return {
        "content": [{"type": "text", "text": result.content}],
        "isError": result.is_error,
        "details": dict(result.details),
        "terminate": result.terminate,
    }


def dispatch_native_tools(
    calls: Sequence[Mapping[str, Any]],
    *,
    cwd: str | os.PathLike[str],
    image_delivery: bool = False,
) -> list[dict[str, Any]]:
    """Dispatch a batch while preserving model order.

    ``PiSemanticsState`` provides the parallel execution and per-path mutation
    queue used by the agent loop. This lower-level adapter intentionally keeps
    one-call-at-a-time behavior for the installed-tool protocol.
    """
    output: list[dict[str, Any]] = []
    for call in calls:
        output.append(
            execute_native_tool(
                str(call.get("name", call.get("tool_id", ""))),
                call.get("arguments", {}),
                cwd=cwd,
                image_delivery=image_delivery,
                call_id=str(call.get("id", call.get("call_id", ""))),
            )
        )
    return output


def main() -> int:
    raw = sys.stdin.read()
    if len(raw.encode("utf-8")) > 1024 * 1024:
        raise ValueError("request exceeds 1 MiB")
    request = json.loads(raw)
    if not isinstance(request, Mapping):
        raise ValueError("request must be an object")
    result = execute_native_tool(
        str(request.get("tool_id", "")),
        request.get("arguments", {}),
        cwd=str(request.get("cwd", os.getcwd())),
        image_delivery=bool(request.get("image_delivery", False)),
        call_id=str(request.get("call_id", "")),
    )
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        sys.stderr.write(f"pi native tool adapter: {exc}\n")
        raise SystemExit(1)
