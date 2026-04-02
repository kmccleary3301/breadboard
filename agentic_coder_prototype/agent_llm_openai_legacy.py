"""Compatibility wrapper for the legacy OpenAI conductor implementation."""

from importlib import import_module
from typing import Any


def __getattr__(name: str) -> Any:
    legacy = import_module(".legacy.openai_conductor", __package__)
    return getattr(legacy, name)


def __dir__() -> list[str]:
    return sorted(globals().keys())
