"""Security substrate: centralized redaction primitives (C-G0c)."""

from .redaction import (
    REDACTED,
    RedactionProblem,
    clear_registered_secret_values,
    is_secret_key,
    iter_registered_secret_values,
    register_secret_value,
    scrub_headers,
    scrub_structure,
    scrub_text,
)

__all__ = [
    "REDACTED",
    "RedactionProblem",
    "clear_registered_secret_values",
    "is_secret_key",
    "iter_registered_secret_values",
    "register_secret_value",
    "scrub_headers",
    "scrub_structure",
    "scrub_text",
]
