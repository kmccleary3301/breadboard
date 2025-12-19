from __future__ import annotations

from typing import Any, Dict


class APIRequestRecorder:
    def __init__(self, logger_v2) -> None:
        self.logger_v2 = logger_v2

    def save_request(self, turn_index: int, payload: Dict[str, Any]) -> str:
        if not getattr(self.logger_v2, "run_dir", None):
            return ""
        return self.logger_v2.write_json(f"raw/requests/turn_{turn_index}.json", payload)

    def save_response(self, turn_index: int, payload: Dict[str, Any]) -> str:
        if not getattr(self.logger_v2, "run_dir", None):
            return ""
        return self.logger_v2.write_json(f"raw/responses/turn_{turn_index}.json", payload)
