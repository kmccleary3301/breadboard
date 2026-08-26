from __future__ import annotations

from typing import Any, Dict, Optional

from ..provider.contracts import ProviderRuntimeError


def public_error_projection(
    exc: BaseException,
    *,
    default_code: str = "runtime_error",
) -> Dict[str, Any]:
    """Return stable public error fields without exception or provider text."""
    if isinstance(exc, ProviderRuntimeError):
        return {
            "error": exc.safe_code,
            "error_type": exc.kind,
            "hint": None,
        }
    return {
        "error": default_code,
        "error_type": "runtime",
        "hint": None,
    }


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

    def handle_provider_error(
        self,
        exc: Exception,
        messages: Any,
        transcript: Any,
    ) -> Dict[str, Any]:
        return public_error_projection(exc, default_code="provider_runtime_error")

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
