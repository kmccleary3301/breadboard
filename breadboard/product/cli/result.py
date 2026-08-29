from __future__ import annotations

import json
import sys

from breadboard.product.operations.model import OperationResult


def render_human(result: OperationResult) -> str:
    if result.ok:
        lines = ["ok"] + [
            (
                f"{key}: {json.dumps(value, sort_keys=True)}"
                if isinstance(value, (dict, list))
                else f"{key}: {value}"
            )
            for key, value in sorted(result.data.items())
        ]
        if result.record_refs:
            lines.append("refs: " + ", ".join(result.record_refs))
        if result.next_actions:
            lines.append("next: " + "; ".join(result.next_actions))
        return "\n".join(lines)
    error = result.error or {}
    lines = [
        "error "
        f"[{error.get('error_code', 'error')}]: "
        f"{error.get('message', 'command failed')}"
    ]
    if error.get("hint"):
        lines.append("hint: " + str(error["hint"]))
    if result.next_actions:
        lines.append("next: " + "; ".join(result.next_actions))
    return "\n".join(lines)


def emit(
    result: OperationResult,
    json_output: bool = False,
    quiet: bool = False,
) -> int:
    if json_output:
        print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
    elif not quiet or not result.ok:
        print(
            render_human(result),
            file=sys.stdout if result.ok else sys.stderr,
        )
    return result.exit_code
