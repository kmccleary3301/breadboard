from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


class MessageFormatter:
    """Minimal message formatter for tool execution output."""

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def format_execution_results(self, executed_results, failed_at_index: int, total_calls: int) -> List[str]:
        lines: List[str] = []
        for tool_parsed, tool_result in executed_results:
            name = getattr(tool_parsed, "function", "tool")
            out = tool_result
            if isinstance(tool_result, dict):
                out = tool_result.get("output") or tool_result.get("__mvi_text_output") or tool_result
            try:
                rendered = json.dumps(out, ensure_ascii=False)
            except Exception:
                rendered = str(out)
            lines.append(f"[{name}] {rendered}")
        if not lines:
            return ["(no tool output)"]
        return lines

    def write_tool_result_file(self, run_dir: str, turn_index: int, idx: int, tool_name: str, tool_result: Any) -> str:
        try:
            out_dir = Path(run_dir) / "tool_results"
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"turn_{turn_index}_{idx}_{tool_name}.json"
            path.write_text(json.dumps(tool_result, ensure_ascii=False, indent=2), encoding="utf-8")
            return str(path)
        except Exception:
            return ""

    def create_tool_result_entry(
        self,
        tool_name: str,
        tool_result: Any,
        *,
        syntax_type: str = "",
        call_id: str | None = None,
    ) -> Dict[str, Any]:
        content = tool_result
        if isinstance(tool_result, dict):
            content = tool_result.get("output") or tool_result.get("__mvi_text_output") or tool_result
        if not isinstance(content, str):
            try:
                content = json.dumps(content, ensure_ascii=False)
            except Exception:
                content = str(content)
        entry: Dict[str, Any] = {
            "role": "tool",
            "name": tool_name,
            "content": content,
        }
        if call_id:
            entry["tool_call_id"] = call_id
        return entry

    def create_enhanced_tool_calls(self, tool_calls_payload: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return list(tool_calls_payload or [])

    def format_tool_output(self, tool_name: str, tool_output: Any, tool_args: Any) -> str:
        try:
            output_text = json.dumps(tool_output, ensure_ascii=False)
        except Exception:
            output_text = str(tool_output)
        return f"[{tool_name}] {output_text}"
