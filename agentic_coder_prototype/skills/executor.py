from __future__ import annotations

from typing import Any, Callable, Dict, List, Tuple


def _resolve_inputs_reference(value: Any, *, inputs: Dict[str, Any]) -> Any:
    if not isinstance(value, str):
        return value
    raw = value.strip()
    if raw in {"$inputs", "${inputs}", "{{inputs}}"}:
        return inputs
    if raw.startswith("$inputs."):
        cursor: Any = inputs
        for part in raw[len("$inputs.") :].split("."):
            if not isinstance(cursor, dict) or part not in cursor:
                return value
            cursor = cursor[part]
        return cursor
    return value


def _resolve_args_template(obj: Any, *, inputs: Dict[str, Any]) -> Any:
    if isinstance(obj, dict):
        return {k: _resolve_args_template(v, inputs=inputs) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_args_template(v, inputs=inputs) for v in obj]
    return _resolve_inputs_reference(obj, inputs=inputs)


def execute_graph_skill(
    skill: Any,
    *,
    inputs: Dict[str, Any],
    exec_func: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], bool]:
    """Execute a deterministic sequence of tool calls (graph skill).

    This is intentionally minimal: a graph skill is a list of step dictionaries.
    Each step can include:
      - function/tool: tool name
      - arguments/args: tool arguments (supports $inputs / $inputs.foo substitution)
    """
    steps_payload: List[Dict[str, Any]] = []
    raw_steps = getattr(skill, "steps", None)
    if not isinstance(raw_steps, list) or not raw_steps:
        return [{"error": "graph skill has no steps"}], False

    for idx, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            steps_payload.append({"index": idx, "error": "invalid step (not an object)"})
            return steps_payload, False
        name = raw.get("function") or raw.get("tool") or raw.get("name")
        if not isinstance(name, str) or not name.strip():
            steps_payload.append({"index": idx, "error": "step missing tool name"})
            return steps_payload, False
        args = raw.get("arguments")
        if args is None:
            args = raw.get("args")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            steps_payload.append({"index": idx, "tool": name, "error": "step arguments must be an object"})
            return steps_payload, False

        call = {
            "function": name.strip(),
            "arguments": _resolve_args_template(dict(args), inputs=inputs),
        }
        result = exec_func(call)
        steps_payload.append({"index": idx, "tool": name.strip(), "arguments": call["arguments"], "result": result})
        if isinstance(result, dict) and "error" in result:
            return steps_payload, False

    return steps_payload, True
