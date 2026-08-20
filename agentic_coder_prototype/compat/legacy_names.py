"""Version-aware legacy-name resolution (rename plan R-0B2/R-0B3, ruling A-3).

Frozen evidence, accepted P3 compiler configs, old locks, and historical
records embed the engine package's dotted module paths and repo-relative file
paths as *data*. Those bytes are never rewritten. Instead, this module
translates legacy names to canonical names at the resolution seam only:

- dotted module paths (``agentic_coder_prototype.compilation.x`` ->
  ``<canonical>.compilation.x``) for imports;
- repo-relative source paths (``agentic_coder_prototype/compilation/x.py`` ->
  ``<canonical>/compilation/x.py``) for file lookups.

"Version-aware" means translation is driven by an explicit, ordered epoch
table of ``(legacy, canonical)`` package-name pairs rather than blind string
replacement: only whole-segment prefix matches translate, unknown names pass
through unchanged, and the hashed/stored view always keeps the original
string. Pre-rename the current epoch is the identity mapping; when the
package is renamed (R-1) ``CANONICAL_PACKAGE`` flips automatically because it
is derived from this module's own package name.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

# Derived from the package actually providing this module; flips to
# "breadboard_engine" automatically when the tree is renamed at R-1.
CANONICAL_PACKAGE = __name__.split(".")[0]

# Ordered rename epochs: (legacy_name, canonical_name_at_that_epoch).
# R-1 will append ("agentic_coder_prototype", "breadboard_engine").
RENAME_EPOCHS: tuple[tuple[str, str], ...] = (
    ("agentic_coder_prototype", CANONICAL_PACKAGE),
)

# Every name the engine package has ever had (legacy first, current last).
KNOWN_PACKAGE_NAMES: tuple[str, ...] = tuple(
    dict.fromkeys([legacy for legacy, _ in RENAME_EPOCHS] + [CANONICAL_PACKAGE])
)


def _split_prefix(dotted: str) -> tuple[str, str]:
    head, sep, rest = dotted.partition(".")
    return head, rest if sep else ""


def canonical_dotted(dotted: str) -> str:
    """Translate a legacy dotted module path to its canonical form.

    Whole-segment prefix match only: ``agentic_coder_prototype_ext.x`` is not
    translated. Unknown prefixes pass through unchanged. Idempotent.
    """
    if not isinstance(dotted, str) or not dotted:
        return dotted
    head, rest = _split_prefix(dotted)
    for legacy, canonical in RENAME_EPOCHS:
        if head == legacy:
            return canonical + ("." + rest if rest else "")
    return dotted


def legacy_dotted(dotted: str) -> str:
    """Inverse translation (canonical -> oldest legacy name); idempotent."""
    if not isinstance(dotted, str) or not dotted:
        return dotted
    head, rest = _split_prefix(dotted)
    for legacy, canonical in reversed(RENAME_EPOCHS):
        if head == canonical:
            return legacy + ("." + rest if rest else "")
    return dotted


def canonical_repo_path(path: str) -> str:
    """Translate a legacy repo-relative path to its canonical form.

    Whole-segment prefix match on the first path component only; unknown
    paths pass through unchanged. Idempotent.
    """
    if not isinstance(path, str) or not path:
        return path
    head, sep, rest = path.replace("\\", "/").partition("/")
    for legacy, canonical in RENAME_EPOCHS:
        if head == legacy:
            return canonical + (sep + rest if sep else "")
    return path


def legacy_repo_path(path: str) -> str:
    """Inverse repo-path translation; idempotent."""
    if not isinstance(path, str) or not path:
        return path
    head, sep, rest = path.replace("\\", "/").partition("/")
    for legacy, canonical in reversed(RENAME_EPOCHS):
        if head == canonical:
            return legacy + (sep + rest if sep else "")
    return path


def resolve_module(dotted: str) -> ModuleType:
    """Import the module named by a (possibly legacy) dotted path."""
    return importlib.import_module(canonical_dotted(dotted))


def resolve_callable(dotted: str) -> Any:
    """Resolve ``pkg.mod:attr`` or ``pkg.mod.attr`` to the named object.

    Without a ``:`` separator the whole string is first tried as a module;
    if that fails the final segment is treated as an attribute.
    """
    if ":" in dotted:
        module_path, _, attr = dotted.partition(":")
        target = resolve_module(module_path)
        for part in filter(None, attr.split(".")):
            target = getattr(target, part)
        return target
    try:
        return resolve_module(dotted)
    except ModuleNotFoundError:
        module_path, _, attr = dotted.rpartition(".")
        if not module_path:
            raise
        return getattr(resolve_module(module_path), attr)
