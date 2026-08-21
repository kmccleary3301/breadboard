from __future__ import annotations

import base64
import dataclasses
import json
from collections.abc import Mapping
from typing import Any

from breadboard.product.evidence.replay import ReplayPlan, ReplayWorkerIntegrityError, ReplayWorkerResult
from breadboard.product.evidence.replay.plan import canonical_json


def load_request(input_bytes: bytes, schema_version: str) -> dict[str, Any]:
    try:
        value = json.loads(input_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("replay port input must be JSON") from error
    if not isinstance(value, dict) or value.get("schema_version") != schema_version:
        raise ValueError(f"unsupported replay port input; expected {schema_version}")
    return value


def json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return json_value(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("replay port result keys must be strings")
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    for name in ("as_dict", "to_dict"):
        method = getattr(value, name, None)
        if callable(method):
            return json_value(method())
    raise TypeError(f"replay port result is not JSON-compatible: {type(value).__name__}")


def worker_result(
    plan: ReplayPlan,
    *,
    output_path: str,
    output: Any,
    port_kind: str,
    request: Mapping[str, Any],
    response: Mapping[str, Any],
) -> ReplayWorkerResult:
    if output_path == plan.transcript_path:
        raise ReplayWorkerIntegrityError("replay port output cannot replace the normalized transcript")
    request_span = f"{port_kind}.request"
    transcript = (
        {
            "sequence": 1,
            "kind": request_span,
            "span_id": request_span,
            "parent_span_id": None,
            "payload": json_value(request),
        },
        {
            "sequence": 2,
            "kind": f"{port_kind}.response",
            "span_id": f"{port_kind}.response",
            "parent_span_id": request_span,
            "payload": json_value(response),
        },
    )
    return ReplayWorkerResult({output_path: canonical_json(json_value(output)) + b"\n"}, transcript)
