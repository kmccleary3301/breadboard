from __future__ import annotations

from typing import Any, Dict

from breadboard_engine.security import redaction


def _redact_payload(value: Any) -> Any:
    # C-G0d: central substrate replaces the local five-key deny-list.
    scrubbed, _problems = redaction.scrub_structure(value)
    return scrubbed


class APIRequestRecorder:
    def __init__(self, logger_v2) -> None:
        self.logger_v2 = logger_v2

    def save_request(self, turn_index: int, payload: Dict[str, Any]) -> str:
        if not getattr(self.logger_v2, "run_dir", None):
            return ""
        sanitized = _redact_payload(payload)
        canonical_path = self.logger_v2.write_json(f"raw/requests/turn_{turn_index}.request.json", sanitized)
        self.logger_v2.write_json(f"raw/requests/turn_{turn_index}.json", sanitized)
        return canonical_path

    def save_response(self, turn_index: int, payload: Dict[str, Any]) -> str:
        if not getattr(self.logger_v2, "run_dir", None):
            return ""
        sanitized = _redact_payload(payload)
        canonical_path = self.logger_v2.write_json(f"raw/responses/turn_{turn_index}.response.json", sanitized)
        self.logger_v2.write_json(f"raw/responses/turn_{turn_index}.json", sanitized)
        return canonical_path
