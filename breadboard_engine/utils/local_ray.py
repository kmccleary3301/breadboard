"""Utilities to provide light-weight stand-ins for Ray actors when Ray is unavailable."""
from __future__ import annotations

from typing import Any, Callable


class _LocalMethodProxy:
    """Wrap a bound method so calls through `.remote()` execute synchronously."""

    __slots__ = ("_func",)

    def __init__(self, func: Callable[..., Any]) -> None:
        self._func = func

    def remote(self, *args: Any, **kwargs: Any) -> Any:
        return self._func(*args, **kwargs)


class LocalActorProxy:
    """Thin adapter that mimics a Ray actor handle for local instances."""

    __slots__ = ("_impl",)

    def __init__(self, impl: Any) -> None:
        self._impl = impl

    def __getattr__(self, item: str) -> Any:
        attr = getattr(self._impl, item)
        if callable(attr):
            return _LocalMethodProxy(attr)
        return attr


def identity_get(obj: Any) -> Any:
    """Replacement for ``ray.get`` that leaves objects untouched."""

    return obj
