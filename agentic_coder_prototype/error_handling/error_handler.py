from __future__ import annotations

from typing import Any, Dict, Optional


class ErrorHandler:
    """Minimal error handler for provider/loop failures."""

    def __init__(self, output_json_path: Optional[str] = None) -> None:
        self.output_json_path = output_json_path

    def handle_empty_response(self, choice: Any) -> Dict[str, Any]:
        return {
            "error": "Empty response from provider",
            "error_type": "empty_response",
            "finish_reason": getattr(choice, "finish_reason", None),
            "index": getattr(choice, "index", None),
        }

    def handle_provider_error(self, exc: Exception, messages: Any, transcript: Any) -> Dict[str, Any]:
        return {
            "error": str(exc),
            "error_type": exc.__class__.__name__,
            "hint": None,
        }
