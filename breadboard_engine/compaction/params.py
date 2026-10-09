"""Strict parameter reading for preset components.

Every component is built from a mapping. Reading a key consumes it;
``done()`` rejects whatever is left, naming the keys the component accepts.
Errors carry the preset path (``pipeline.stages[1].select``) so a bad preset
or override points at its own line.

A value written ``{setting: <name>}`` reads the preset's native settings
object (``CompactionSettings`` for OMP, where the harness's own config
parser owns key spelling and migration) instead of a literal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Optional, Sequence


class PresetError(ValueError):
    pass


_MISSING = object()


@dataclass(frozen=True)
class BuildEnv:
    """What components may read while being built."""

    settings: Any
    """Native settings object (``CompactionSettings``)."""
    prompt_dir: Optional[Any] = None
    """``importlib.resources`` traversable holding the preset's prompt files."""


class Params:
    def __init__(self, raw: Any, where: str, env: BuildEnv) -> None:
        if raw is None:
            raw = {}
        if not isinstance(raw, Mapping):
            raise PresetError(f"{where} must be a mapping")
        self._raw = dict(raw)
        self._used: List[str] = []
        self.where = where
        self.env = env

    def child(self, raw: Any, key: str) -> "Params":
        return Params(raw, f"{self.where}.{key}", self.env)

    def _take(self, key: str, default: Any) -> Any:
        self._used.append(key)
        value = self._raw.pop(key, _MISSING)
        if value is _MISSING:
            if default is _MISSING:
                raise PresetError(f"{self.where}.{key} is required")
            return default
        if isinstance(value, Mapping) and set(value) == {"setting"}:
            name = value["setting"]
            if not isinstance(name, str) or not hasattr(self.env.settings, name):
                raise PresetError(f"{self.where}.{key} names unknown setting {name!r}")
            return getattr(self.env.settings, name)
        return value

    def value(self, key: str, default: Any = _MISSING) -> Any:
        """Untyped read, for values a component validates itself."""
        return self._take(key, default)

    def kind(self, kinds: Iterable[str]) -> str:
        known = sorted(kinds)
        value = self._take("kind", _MISSING)
        if value not in known:
            raise PresetError(f"{self.where}.kind {value!r} is unknown; expected one of {known}")
        return value

    def str(self, key: str, default: Any = _MISSING) -> Any:
        value = self._take(key, default)
        if value is not None and not isinstance(value, str):
            raise PresetError(f"{self.where}.{key} must be a string")
        return value

    def int(self, key: str, default: Any = _MISSING, *, minimum: Optional[int] = None) -> Any:
        value = self._take(key, default)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise PresetError(f"{self.where}.{key} must be an integer")
        if minimum is not None and value < minimum:
            raise PresetError(f"{self.where}.{key} must be >= {minimum}")
        return value

    def number(self, key: str, default: Any = _MISSING) -> Any:
        value = self._take(key, default)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PresetError(f"{self.where}.{key} must be a number")
        return value

    def bool(self, key: str, default: Any = _MISSING) -> Any:
        value = self._take(key, default)
        if not isinstance(value, bool):
            raise PresetError(f"{self.where}.{key} must be a boolean")
        return value

    def choice(self, key: str, choices: Sequence[str], default: Any = _MISSING) -> Any:
        value = self._take(key, default)
        if value not in choices:
            raise PresetError(f"{self.where}.{key} must be one of {list(choices)}")
        return value

    def mapping(self, key: str, default: Any = _MISSING) -> Any:
        value = self._take(key, default)
        if value is not None and not isinstance(value, Mapping):
            raise PresetError(f"{self.where}.{key} must be a mapping")
        return value

    def list(self, key: str, default: Any = _MISSING) -> Any:
        value = self._take(key, default)
        if value is not None and not isinstance(value, (list, tuple)):
            raise PresetError(f"{self.where}.{key} must be a list")
        return value

    def done(self) -> None:
        if self._raw:
            unknown = sorted(self._raw)
            accepted = sorted(set(self._used))
            raise PresetError(f"{self.where} has unknown keys {unknown}; accepted keys are {accepted}")
