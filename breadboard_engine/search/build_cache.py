from __future__ import annotations

import importlib
import inspect
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
from threading import RLock
from types import ModuleType
from typing import Callable, Iterator, TypeVar, cast

_T = TypeVar("_T")
_ACTIVE_CACHE: ContextVar[dict[Callable[[], object], object] | None] = ContextVar(
    "search_builder_cache",
    default=None,
)
_INSTALL_LOCK = RLock()
_WRAPPERS: dict[Callable[[], object], Callable[[], object]] = {}
_MODULE_PREFIXES = ("breadboard_engine.search", "breadboard_engine.rl")


def _memoized_builder(builder: Callable[[], _T]) -> Callable[[], _T]:
    existing = _WRAPPERS.get(cast(Callable[[], object], builder))
    if existing is not None:
        return cast(Callable[[], _T], existing)

    @wraps(builder)
    def memoized() -> _T:
        cache = _ACTIVE_CACHE.get()
        if cache is None:
            return builder()
        key = cast(Callable[[], object], builder)
        if key not in cache:
            cache[key] = builder()
        return cast(_T, deepcopy(cache[key]))

    setattr(memoized, "__search_builder_memoized__", True)
    _WRAPPERS[cast(Callable[[], object], builder)] = cast(
        Callable[[], object], memoized
    )
    return memoized


def _eligible_builder(name: str, value: object) -> bool:
    if not name.startswith("build_") or not inspect.isfunction(value):
        return False
    if getattr(value, "__search_builder_memoized__", False):
        return False
    return not inspect.signature(value).parameters


def _install_loaded_builder_wrappers() -> None:
    with _INSTALL_LOCK:
        modules = tuple(
            module
            for name, module in sys.modules.copy().items()
            if isinstance(module, ModuleType)
            and name != __name__
            and name.startswith(_MODULE_PREFIXES)
        )
        for module in modules:
            for name, value in vars(module).copy().items():
                if (
                    _eligible_builder(name, value)
                    and getattr(module, name, None) is value
                ):
                    setattr(module, name, _memoized_builder(value))


def search_build_entry(builder: Callable[[], _T]) -> Callable[[], _T]:
    """Run one public zero-argument builder inside a request-local cache."""

    @wraps(builder)
    def evaluate() -> _T:
        with search_build_request():
            return builder()

    return evaluate


@contextmanager
def search_build_request() -> Iterator[None]:
    """Memoize deterministic zero-argument packet builders for one request.

    Cached templates never escape directly: every lookup returns a deep copy.
    Nested requests share the current cache, while separate requests rebuild
    from current process inputs.
    """

    # Search consumers import RL builders lazily from the package root. Load
    # that export surface before installing wrappers so the first request and
    # later requests share the same canonical function identities.
    importlib.import_module("breadboard_engine.rl")
    _install_loaded_builder_wrappers()
    if _ACTIVE_CACHE.get() is not None:
        yield
        return
    token = _ACTIVE_CACHE.set({})
    try:
        yield
    finally:
        _ACTIVE_CACHE.reset(token)
