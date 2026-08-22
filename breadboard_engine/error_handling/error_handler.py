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

    def handle_validation_error(self, payload: Dict[str, Any]) -> str:
        error = str(payload.get("error") or "validation_failed").strip()
        tool_name = str(payload.get("tool_name") or "").strip()
        details = payload.get("details")
        message = ["<VALIDATION_ERROR>", error]
        if tool_name:
            message.append(f"tool={tool_name}")
        if details:
            message.append(str(details))
        message.append("</VALIDATION_ERROR>")
        return "\n".join(message)

    def handle_constraint_violation(self, error: Any) -> str:
        return "\n".join(
            [
                "<CONSTRAINT_VIOLATION>",
                str(error),
                "</CONSTRAINT_VIOLATION>",
            ]
        )
