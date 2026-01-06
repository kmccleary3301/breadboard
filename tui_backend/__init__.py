"""Compatibility wrapper for the CLI bridge FastAPI service."""

try:
    from .app import create_app  # noqa: F401
except ModuleNotFoundError:
    create_app = None  # type: ignore[assignment]
    __all__ = []
else:
    __all__ = ["create_app"]
