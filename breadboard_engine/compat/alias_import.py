"""Prefix-wide legacy-import alias layer (rename plan R-0C).

Installs a :class:`LegacyAliasFinder` at the front of ``sys.meta_path`` that
maps every ``<legacy_root>[.suffix]`` import onto the canonical package while
keeping *one* module object per module (old-first or new-first, identity
holds). Post-rename (R-1), the ``agentic_coder_prototype`` shim package
installs this for the ``agentic_coder_prototype`` -> ``breadboard_engine``
epoch; pre-rename the epoch is an identity mapping and the finder aliases
nothing.

Design notes (R-0C2):
- ``create_module`` returns the already-imported canonical module.
  ``module_from_spec`` initializes attributes with ``override=False``, so the
  canonical module's ``__name__``/``__spec__``/``__loader__`` are preserved -
  asserted per Python version by the matrix tests, not assumed.
- The canonical module is re-fetched from ``sys.modules`` after import, so
  self-replacing root wrappers (``sys.modules[__name__] = _module``, e.g.
  ``provider_runtime.py``) compose correctly (R-0C4).
- ``get_code``/``get_source``/``get_filename`` delegate to the canonical
  loader so ``python -m <legacy path>`` works under ``runpy``.

Policy (R-0C1): ``BREADBOARD_LEGACY_IMPORTS`` = ``allow`` (default, silent) |
``warn`` (one warning per process) | ``error`` (reject the import).
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import os
import sys
import warnings
from typing import Any, Optional

POLICY_ENV = "BREADBOARD_LEGACY_IMPORTS"
_VALID_POLICIES = ("allow", "warn", "error")
_warned_processes: set[str] = set()


class LegacyImportError(ModuleNotFoundError):
    """Legacy import rejected under ``BREADBOARD_LEGACY_IMPORTS=error``."""


def current_policy() -> str:
    policy = os.environ.get(POLICY_ENV, "allow").strip().lower() or "allow"
    if policy not in _VALID_POLICIES:
        raise ValueError(
            f"{POLICY_ENV}={policy!r} invalid; expected one of {', '.join(_VALID_POLICIES)}"
        )
    return policy


def _enforce_policy(legacy_root: str, fullname: str) -> None:
    policy = current_policy()
    if policy == "error":
        raise LegacyImportError(
            f"legacy import {fullname!r} rejected ({POLICY_ENV}=error); "
            f"import the canonical package instead",
            name=fullname,
        )
    if policy == "warn" and legacy_root not in _warned_processes:
        _warned_processes.add(legacy_root)
        warnings.warn(
            f"legacy package name {legacy_root!r} is deprecated; imports are "
            f"aliased to the canonical package ({POLICY_ENV}=warn)",
            DeprecationWarning,
            stacklevel=3,
        )


def announce_root_import(legacy_root: str) -> None:
    """Policy hook for the physical shim package's own import.

    The root legacy package is found by the ordinary path finder (a real
    directory), bypassing :class:`LegacyAliasFinder`; its ``__init__`` must
    call this so ``error``/``warn`` policies apply to the root import too.
    """
    _enforce_policy(legacy_root, legacy_root)


class LegacyAliasLoader(importlib.abc.Loader):
    _RESTORED_ATTRS = ("__name__", "__spec__", "__loader__", "__package__")

    def __init__(self, legacy_fullname: str, canonical_fullname: str) -> None:
        self.legacy_fullname = legacy_fullname
        self.canonical_fullname = canonical_fullname
        self._canonical_attrs: dict[str, Any] = {}

    def _canonical_spec(self) -> importlib.machinery.ModuleSpec:
        spec = importlib.util.find_spec(self.canonical_fullname)
        if spec is None:  # pragma: no cover - guarded by finder
            raise ModuleNotFoundError(self.canonical_fullname, name=self.canonical_fullname)
        return spec

    def create_module(self, spec: importlib.machinery.ModuleSpec):
        importlib.import_module(self.canonical_fullname)
        # Re-fetch: canonical modules may self-replace their sys.modules entry
        # during exec (provider_runtime.py pattern).
        module = sys.modules[self.canonical_fullname]
        # module_from_spec stomps __name__/__spec__/__loader__ with the alias
        # spec (verified on CPython 3.13); stash canonical values so
        # exec_module can restore them after attribute initialization.
        self._canonical_attrs = {
            attr: getattr(module, attr) for attr in self._RESTORED_ATTRS if hasattr(module, attr)
        }
        return module

    def exec_module(self, module: Any) -> None:
        # Canonical module is already executed; restore its identity metadata.
        for attr, value in self._canonical_attrs.items():
            setattr(module, attr, value)

    # --- runpy / introspection support -----------------------------------
    def is_package(self, fullname: str) -> bool:
        return self._canonical_spec().submodule_search_locations is not None

    def get_code(self, fullname: str):
        spec = self._canonical_spec()
        return spec.loader.get_code(spec.name)  # type: ignore[union-attr]

    def get_source(self, fullname: str):
        spec = self._canonical_spec()
        return spec.loader.get_source(spec.name)  # type: ignore[union-attr]

    def get_filename(self, fullname: str) -> str:
        spec = self._canonical_spec()
        if spec.origin is None:  # pragma: no cover
            raise ImportError(f"no origin for {spec.name}")
        return spec.origin

    def __repr__(self) -> str:  # pragma: no cover
        return f"LegacyAliasLoader({self.legacy_fullname!r} -> {self.canonical_fullname!r})"


class LegacyAliasFinder(importlib.abc.MetaPathFinder):
    """Aliases every import under ``legacy_root`` to ``canonical_root``."""

    def __init__(self, legacy_root: str, canonical_root: str) -> None:
        if not legacy_root or not canonical_root:
            raise ValueError("legacy_root and canonical_root are required")
        self.legacy_root = legacy_root
        self.canonical_root = canonical_root

    def find_spec(
        self,
        fullname: str,
        path: Optional[Any] = None,
        target: Optional[Any] = None,
    ) -> Optional[importlib.machinery.ModuleSpec]:
        if self.legacy_root == self.canonical_root:
            return None  # identity epoch: nothing to alias
        if fullname != self.legacy_root and not fullname.startswith(self.legacy_root + "."):
            return None
        _enforce_policy(self.legacy_root, fullname)
        canonical = self.canonical_root + fullname[len(self.legacy_root):]
        try:
            canonical_spec = importlib.util.find_spec(canonical)
        except (ImportError, AttributeError, ValueError):
            return None
        if canonical_spec is None:
            return None
        loader = LegacyAliasLoader(fullname, canonical)
        spec = importlib.util.spec_from_loader(
            fullname,
            loader,
            origin=canonical_spec.origin,
            is_package=canonical_spec.submodule_search_locations is not None,
        )
        if canonical_spec.submodule_search_locations is not None and spec is not None:
            spec.submodule_search_locations = list(canonical_spec.submodule_search_locations)
        return spec

    def __repr__(self) -> str:  # pragma: no cover
        return f"LegacyAliasFinder({self.legacy_root!r} -> {self.canonical_root!r})"


def install(legacy_root: str, canonical_root: str) -> LegacyAliasFinder:
    """Idempotently install a finder at the front of ``sys.meta_path``."""
    for finder in sys.meta_path:
        if (
            isinstance(finder, LegacyAliasFinder)
            and finder.legacy_root == legacy_root
            and finder.canonical_root == canonical_root
        ):
            return finder
    finder = LegacyAliasFinder(legacy_root, canonical_root)
    sys.meta_path.insert(0, finder)
    return finder


def uninstall(finder: LegacyAliasFinder) -> None:
    try:
        sys.meta_path.remove(finder)
    except ValueError:
        pass
