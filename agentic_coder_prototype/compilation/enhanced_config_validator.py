from __future__ import annotations

from typing import Any, Dict, List


class EnhancedConfigValidator:
    """Minimal validator stub for recovery builds."""

    def __init__(self, rules_path: str) -> None:
        self.rules_path = rules_path

    def validate_tool_calls(self, tool_calls: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {"valid": True, "errors": []}
