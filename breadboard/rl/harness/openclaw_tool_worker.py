"""Tool-only JSONL worker; it never samples a model or owns history/recovery."""
from __future__ import annotations

import json
import sys
from typing import Any

from breadboard.rl.harness.openclaw_native_tools import OpenClawNativeTools


def run(stdin: Any = sys.stdin, stdout: Any = sys.stdout, workspace: str = ".") -> int:
    tools = OpenClawNativeTools(workspace)
    try:
        for line in stdin:
            if not line.strip():
                continue
            request = json.loads(line)
            try:
                result = tools.execute(str(request["name"]), request.get("arguments", {}))
                response = {"ok": True, "tool_call_id": request.get("tool_call_id"), "result": result}
            except Exception as exc:
                response = {"ok": False, "tool_call_id": request.get("tool_call_id"), "error": str(exc)}
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()
    finally:
        tools.scope.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
