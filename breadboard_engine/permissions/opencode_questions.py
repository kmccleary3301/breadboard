"""Native OpenCode question adapter over the canonical permission broker."""
from __future__ import annotations

import time
from collections import deque
from typing import Any, Dict, List, Optional

from .broker import PermissionBroker


def request_stock_question(broker: PermissionBroker, session_state: Any, question: Dict[str, Any]) -> Optional[List[List[str]]]:
    """Carry stock OpenCode answers through the existing approval queue."""
    queue = session_state.get_provider_metadata("permission_queue")
    if queue is None:
        return None
    request_id = broker._next_permission_id(session_state)
    metadata = {"stock_kind": "opencode_question", "stock_request": question}
    items = [
        {"item_id": f"{request_id}_item_{index}", "category": "question",
         "pattern": info["question"], "metadata": {**metadata, "question": info}}
        for index, info in enumerate(question["questions"], start=1)
    ]
    payload = {"event_version": 1, "request_id": request_id, "id": request_id,
               "category": "question", "metadata": metadata, "items": items}
    with broker._pending_lock:
        state = broker._permission_state(session_state)
        if state.get("cancelled") or broker._stock_request_cancelled(state, payload):
            return None
        pending = state.setdefault("pending", {})
        pending[request_id] = payload
        broker._concurrent_routes[(id(queue), request_id)] = deque()
        session_state.set_provider_metadata(broker.STATE_KEY, state)
    broker._emit_permission_event(session_state, "permission_request", payload)
    answers = None
    deadline = time.monotonic() + broker._decision_timeout_s if broker._decision_timeout_s is not None else None
    try:
        while True:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            item = broker._concurrent_queue_get(queue, request_id, timeout_s=remaining)
            if not isinstance(item, dict):
                continue
            if item.get("answers") is not None:
                candidate = item["answers"]
                if not isinstance(candidate, list) or not all(
                    isinstance(answer, list) and all(isinstance(value, str) for value in answer)
                    for answer in candidate
                ):
                    raise ValueError("Stock question answers must be an array of string arrays")
                answers = candidate
            break
    finally:
        with broker._pending_lock:
            broker._concurrent_routes.pop((id(queue), request_id), None)
            state = broker._permission_state(session_state)
            state.setdefault("pending", {}).pop(request_id, None)
            session_state.set_provider_metadata(broker.STATE_KEY, state)
        broker._emit_permission_event(
            session_state, "permission_response",
            {"event_version": 1, "request_id": request_id, "id": request_id,
             "response": "once" if answers is not None else "reject", "answers": answers},
        )
    return answers
