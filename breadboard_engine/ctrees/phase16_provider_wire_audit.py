from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _load_json(path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _iter_request_logs(log_dir: str | Path) -> Iterable[Dict[str, Any]]:
    base = Path(log_dir)
    if not base.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for item in sorted(base.glob("*_request.json")):
        payload = _load_json(item)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _iter_structured_request_logs(run_root: str | Path) -> Iterable[Dict[str, Any]]:
    base = Path(run_root)
    requests_dir = base / "meta" / "requests"
    tools_dir = base / "provider_native" / "tools_provided"
    if not requests_dir.exists():
        return []

    rows: List[Dict[str, Any]] = []
    for item in sorted(requests_dir.glob("turn_*.json")):
        payload = _load_json(item)
        if not isinstance(payload, dict):
            continue
        turn_suffix = item.stem.split("_")[-1]
        tool_payload_path = tools_dir / f"turn_{turn_suffix}.json"
        tool_payload = []
        if tool_payload_path.exists():
            loaded = _load_json(tool_payload_path)
            if isinstance(loaded, list):
                tool_payload = loaded
        rows.append(
            {
                "source": "structured_request",
                "turn": payload.get("turn"),
                "provider": payload.get("provider"),
                "model": payload.get("model"),
                "tool_count": payload.get("tool_count"),
                "request": payload.get("request") or {},
                "extra": payload.get("extra") or {},
                "tools_provided": tool_payload,
            }
        )
    return rows


def _request_body(entry: Dict[str, Any]) -> Dict[str, Any]:
    return dict((((entry.get("body") or {}).get("json")) or {}))


def _extract_tool_names(body: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    for tool in list(body.get("tools") or []):
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "function":
            fn = dict(tool.get("function") or {})
            name = str(tool.get("name") or fn.get("name") or "").strip()
            if name:
                names.append(name)
            continue
        name = str(tool.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _extract_phase_label(body: Dict[str, Any], metadata: Dict[str, Any]) -> str | None:
    explicit = metadata.get("phase_label") or metadata.get("phase")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    extra = body.get("metadata")
    if isinstance(extra, dict):
        candidate = extra.get("phase_label") or extra.get("phase")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _extract_tool_choice_from_excerpt(text: str) -> Any:
    required_match = re.search(r'"tool_choice"\s*:\s*"([^"]+)"', text)
    if required_match:
        return required_match.group(1)
    object_match = re.search(r'"tool_choice"\s*:\s*\{', text)
    if object_match:
        return {"type": "object"}
    return None


def _extract_parallel_tool_calls_from_excerpt(text: str) -> Any:
    match = re.search(r'"parallel_tool_calls"\s*:\s*(true|false|null)', text)
    if not match:
        return None
    token = match.group(1)
    if token == "true":
        return True
    if token == "false":
        return False
    return None


def _normalize_structured_request(entry: Dict[str, Any]) -> Dict[str, Any]:
    request = dict(entry.get("request") or {})
    excerpt = str(request.get("body_excerpt") or "")
    tools_payload = list(entry.get("tools_provided") or [])
    tool_names = _extract_tool_names({"tools": tools_payload})
    tool_choice = _extract_tool_choice_from_excerpt(excerpt)
    parallel_tool_calls = _extract_parallel_tool_calls_from_excerpt(excerpt)
    tool_count = entry.get("tool_count")
    if not isinstance(tool_count, int):
        tool_count = len(tool_names)
    return {
        "request_id": f"turn-{entry.get('turn')}",
        "provider": entry.get("provider"),
        "model": entry.get("model"),
        "tool_count": tool_count,
        "tool_names": tool_names,
        "tool_choice": tool_choice,
        "parallel_tool_calls": parallel_tool_calls,
        "has_tools": bool(tool_count or tool_names),
        "requires_tool_use": str(tool_choice).lower() == "required",
        "phase_label": None,
        "metadata": {
            "source": "structured_request",
            "has_tools_flag": bool((entry.get("extra") or {}).get("has_tools")),
        },
    }


def summarize_provider_wire_log(
    log_dir: str | Path,
    *,
    max_requests: int | None = 2,
    run_root: str | Path | None = None,
) -> Dict[str, Any]:
    requests = list(_iter_request_logs(log_dir))
    source_kind = "provider_dump"
    if not requests and run_root is not None:
        requests = list(_iter_structured_request_logs(run_root))
        source_kind = "structured_request"
    if isinstance(max_requests, int) and max_requests > 0:
        requests = requests[:max_requests]

    rows: List[Dict[str, Any]] = []
    for entry in requests:
        if source_kind == "structured_request":
            rows.append(_normalize_structured_request(entry))
            continue

        body = _request_body(entry)
        metadata = dict(entry.get("metadata") or {})
        tool_names = _extract_tool_names(body)
        tool_choice = body.get("tool_choice")
        parallel_tool_calls = body.get("parallel_tool_calls")
        rows.append(
            {
                "request_id": entry.get("requestId"),
                "provider": entry.get("provider"),
                "model": entry.get("model"),
                "tool_count": len(tool_names),
                "tool_names": tool_names,
                "tool_choice": tool_choice,
                "parallel_tool_calls": parallel_tool_calls,
                "has_tools": bool(tool_names),
                "requires_tool_use": str(tool_choice).lower() == "required",
                "phase_label": _extract_phase_label(body, metadata),
                "metadata": metadata,
            }
        )

    return {
        "schema_version": "phase16_provider_wire_audit_v1",
        "log_dir": str(log_dir),
        "source_kind": source_kind,
        "run_root": str(run_root) if run_root is not None else None,
        "request_count": len(rows),
        "requests_with_tools": sum(1 for row in rows if row["has_tools"]),
        "requests_with_required_tool_choice": sum(1 for row in rows if row["requires_tool_use"]),
        "requests_with_parallel_tool_calls_flag": sum(1 for row in rows if row["parallel_tool_calls"] is not None),
        "phase_labels": sorted(
            {
                str(row["phase_label"])
                for row in rows
                if isinstance(row.get("phase_label"), str) and str(row["phase_label"]).strip()
            }
        ),
        "rows": rows,
    }
