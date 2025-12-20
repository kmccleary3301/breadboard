"""
BreadBoard CLI backend service package.

This module exposes convenience helpers for importing the FastAPI app
without requiring callers to know the internal package layout.
"""

try:
    from .app import create_app  # noqa: F401
except ModuleNotFoundError:
    # FastAPI is optional in some test environments; defer hard failure until
    # the caller actually attempts to construct the app.
    create_app = None  # type: ignore[assignment]
    __all__ = []
else:
    __all__ = ["create_app"]
