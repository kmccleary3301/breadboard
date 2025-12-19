from __future__ import annotations

from typing import Any, List


class ProviderNativeLogger:
    def __init__(self, logger_v2) -> None:
        self.logger_v2 = logger_v2

    def save_tool_calls(self, turn_index: int, payload: List[Any]) -> str:
        if not getattr(self.logger_v2, "run_dir", None):
            return ""
        return self.logger_v2.write_json(f"provider_native/tool_calls/turn_{turn_index}.json", payload)

    def save_tool_results(self, turn_index: int, payload: List[Any]) -> str:
        if not getattr(self.logger_v2, "run_dir", None):
            return ""
        return self.logger_v2.write_json(f"provider_native/tool_results/turn_{turn_index}.json", payload)

    def save_tools_provided(self, turn_index: int, payload: Any) -> str:
        if not getattr(self.logger_v2, "run_dir", None):
            return ""
        return self.logger_v2.write_json(f"provider_native/tools_provided/turn_{turn_index}.json", payload)
