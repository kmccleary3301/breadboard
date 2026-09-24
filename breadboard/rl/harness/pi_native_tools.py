"""Client for the pinned Pi 0.73.1 native Node tool worker.

The worker owns TypeBox validation, ``prepareArguments`` and all filesystem,
shell, image and mutation-queue behavior. Python only marshals one JSON request
and projects the worker's JSON tool result for the BB loop.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Mapping, Sequence


_MAX_REQUEST_BYTES = 1024 * 1024
_WORKER = Path(__file__).with_name("pi_tools_0_73_1.mjs")


class PiNativeWorkerError(RuntimeError):
    """The pinned Node worker could not produce a protocol result."""


def _node_executable() -> str:
    return os.environ.get("PI_NODE", shutil.which("node") or "node")


def _run_worker(request: Mapping[str, Any], *, cwd: str | os.PathLike[str]) -> dict[str, Any]:
    # ASCII escapes carry lone surrogates losslessly to JSON.parse in Node.
    payload = json.dumps(dict(request), ensure_ascii=True, separators=(",", ":"))
    if len(payload.encode("utf-8")) > _MAX_REQUEST_BYTES:
        raise PiNativeWorkerError(f"request exceeds {_MAX_REQUEST_BYTES} bytes")
    environment = os.environ.copy()
    try:
        process = subprocess.run(
            [_node_executable(), str(_WORKER)],
            input=payload.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd),
            env=environment,
            check=False,
        )
    except OSError as exc:
        raise PiNativeWorkerError(f"native worker could not start: {exc}") from exc
    if process.returncode != 0:
        diagnostics = process.stderr.decode("utf-8", "replace").strip()
        raise PiNativeWorkerError(diagnostics or f"native worker exited with code {process.returncode}")
    lines = process.stdout.decode("utf-8", "replace").splitlines()
    if len(lines) != 1:
        raise PiNativeWorkerError("native worker returned an invalid JSONL response")
    try:
        result = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise PiNativeWorkerError(f"native worker returned invalid JSON: {exc}") from exc
    if not isinstance(result, dict):
        raise PiNativeWorkerError("native worker result must be an object")
    return result


def _project_result(result: Mapping[str, Any], *, tool_id: str, call_id: str, image_delivery: bool) -> dict[str, Any]:
    content = result.get("content", [])
    if not isinstance(content, list):
        content = [{"type": "text", "text": str(content)}]
    details = result.get("details", {})
    if not isinstance(details, Mapping):
        details = {}
    details = dict(details)
    details["native_content"] = content
    details["image_delivery"] = bool(image_delivery)
    text = "\n".join(
        str(block.get("text", ""))
        for block in content
        if isinstance(block, Mapping) and block.get("type") == "text"
    )
    return {
        "content": content,
        "text": text,
        "isError": bool(result.get("isError", False)),
        "details": details,
        "terminate": bool(result.get("terminate", False)),
        "call_id": call_id,
        "tool_id": tool_id,
    }


def execute_native_tool(
    tool_id: str,
    arguments: Mapping[str, Any] | Any,
    *,
    cwd: str | os.PathLike[str],
    image_delivery: bool = False,
    call_id: str = "",
) -> dict[str, Any]:
    """Execute one source-derived Pi tool through the Node worker."""
    result = _run_worker(
        {
            "operation": "execute",
            "tool_id": tool_id,
            "arguments": arguments,
            "cwd": str(Path(cwd).resolve()),
            "call_id": call_id,
        },
        cwd=cwd,
    )
    return _project_result(result, tool_id=tool_id, call_id=call_id, image_delivery=image_delivery)


def dispatch_native_tools(
    calls: Sequence[Mapping[str, Any]],
    *,
    cwd: str | os.PathLike[str],
    image_delivery: bool = False,
) -> list[dict[str, Any]]:
    """Run a batch in one pinned Node process, preserving native queue ordering."""
    normalized = [
        {
            "tool_id": str(call.get("name", call.get("tool_id", ""))),
            "arguments": call.get("arguments"),
            "call_id": str(call.get("id", call.get("call_id", ""))),
        }
        for call in calls
    ]
    result = _run_worker({"operation": "batch", "calls": normalized, "cwd": str(Path(cwd).resolve())}, cwd=cwd)
    raw_results = result.get("results")
    if not isinstance(raw_results, list) or len(raw_results) != len(normalized):
        raise PiNativeWorkerError("native worker batch result count does not match request")
    return [
        _project_result(raw, tool_id=call["tool_id"], call_id=call["call_id"], image_delivery=image_delivery)
        for call, raw in zip(normalized, raw_results)
    ]

def parse_streaming_json_batch(
    texts: Sequence[str | None], *, cwd: str | os.PathLike[str] | None = None
) -> list[Any]:
    """Parse argument texts with pinned ``parseStreamingJson`` in one worker process."""
    inputs = list(texts)
    if not inputs:
        return []
    if any(text is not None and not isinstance(text, str) for text in inputs):
        raise PiNativeWorkerError("streaming JSON input must be a string or None")
    result = _run_worker(
        {"operation": "parse_streaming_json_batch", "inputs": inputs},
        cwd=cwd or Path.cwd(),
    )
    results = result.get("results")
    if not isinstance(results, list) or len(results) != len(inputs):
        raise PiNativeWorkerError("native worker parse result count does not match request")
    return results


def main() -> int:
    raw = sys.stdin.buffer.read(_MAX_REQUEST_BYTES + 1)
    if len(raw) > _MAX_REQUEST_BYTES:
        raise ValueError(f"request exceeds {_MAX_REQUEST_BYTES} bytes")
    request = json.loads(raw.decode("utf-8"))
    if not isinstance(request, Mapping):
        raise ValueError("request must be an object")
    result = execute_native_tool(
        str(request.get("tool_id", "")),
        request.get("arguments", {}),
        cwd=str(request.get("cwd", os.getcwd())),
        image_delivery=bool(request.get("image_delivery", False)),
        call_id=str(request.get("call_id", "")),
    )
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        sys.stderr.write(f"pi native tool client: {exc}\n")
        raise SystemExit(1)
