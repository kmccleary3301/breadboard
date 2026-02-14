"""Session import helpers: Import IR -> CLI bridge events JSONL."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def _text_from_ir_message(msg: Dict[str, Any]) -> str:
    parts = msg.get("parts") or []
    if not isinstance(parts, list):
        return ""
    texts: List[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            text = part.get("text")
            if isinstance(text, str):
                texts.append(text)
    return "".join(texts)


def _stable_event_id(session_id: str, seq: int, event_type: str, timestamp_ms: int) -> str:
    payload = f"{session_id}:{seq}:{event_type}:{timestamp_ms}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"evt_{digest[:24]}"


def import_ir_to_cli_bridge_events(
    import_ir: Dict[str, Any],
    *,
    session_id: str,
    protocol_version: str = "1.0",
    base_timestamp_ms: int = 0,
) -> List[Dict[str, Any]]:
    """Convert Import IR v1 to a list of CLI bridge event envelope dicts."""
    conv = import_ir.get("conversation") if isinstance(import_ir, dict) else None
    if not isinstance(conv, dict):
        raise ValueError("import_ir missing conversation")
    messages = conv.get("messages") or []
    if not isinstance(messages, list):
        raise ValueError("conversation.messages must be a list")

    out: List[Dict[str, Any]] = []
    seq = 0
    timestamp_ms = int(base_timestamp_ms)
    turn: Optional[int] = None

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "")
        if role not in {"system", "user", "assistant", "tool"}:
            continue

        if role == "user":
            turn = 0 if turn is None else (turn + 1)

        if role == "system":
            # System messages are not currently part of the CLI bridge stream contract.
            continue

        event_type = "assistant_message" if role == "assistant" else "user_message"
        if role == "tool":
            event_type = "tool_result"

        seq += 1
        timestamp_ms += 1
        envelope: Dict[str, Any] = {
            "id": _stable_event_id(session_id, seq, event_type, timestamp_ms),
            "seq": seq,
            "type": event_type,
            "session_id": session_id,
            "turn": turn,
            "timestamp": timestamp_ms,
            "timestamp_ms": timestamp_ms,
            "protocol_version": protocol_version,
        }

        if event_type in {"assistant_message", "user_message"}:
            envelope["payload"] = {
                "text": _text_from_ir_message(msg),
                "message": msg,
                "source": "import_ir",
            }
        else:
            envelope["payload"] = {
                "status": "ok",
                "error": False,
                "result": msg,
            }

        out.append(envelope)

    return out


def write_events_jsonl(events: Iterable[Dict[str, Any]], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False))
            f.write("\n")


def load_json(path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))

