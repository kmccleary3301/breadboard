"""Immutable author-side representation of a BreadBoard harness definition."""

from __future__ import annotations

import json
import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, TypeAlias

JsonScalar: TypeAlias = None | bool | int | float | str
FrozenJsonValue: TypeAlias = (
    JsonScalar | Mapping[str, "FrozenJsonValue"] | tuple["FrozenJsonValue", ...]
)
_SCALAR_TYPES = (bool, str)
_MAX_JSON_INTEGER = 10**640 - 1


def _copy_json(value: Any, freeze: bool) -> Any:
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TypeError("JSON object keys must be strings")
        items = {key: _copy_json(item, freeze) for key, item in value.items()}
        return MappingProxyType(items) if freeze else items
    sequence = list if freeze else tuple
    if isinstance(value, sequence):
        items = (_copy_json(item, freeze) for item in value)
        return tuple(items) if freeze else list(items)
    kind = type(value)
    if (value is None or kind in _SCALAR_TYPES or kind is int and abs(value) <= _MAX_JSON_INTEGER
            or kind is float and math.isfinite(value)):
        return value
    raise TypeError(f"unsupported JSON value: {kind.__name__}")


def _freeze(value: Any) -> FrozenJsonValue:
    return _copy_json(value, True)


def _thaw(value: FrozenJsonValue) -> Any:
    return _copy_json(value, False)


@dataclass(frozen=True, slots=True, init=False)
class HarnessDefinition(Mapping[str, FrozenJsonValue]):
    """A validated harness definition with recursively immutable contents."""

    _document: Mapping[str, FrozenJsonValue] = field(repr=False)

    @classmethod
    def from_mapping(cls, document: Mapping[str, Any]) -> HarnessDefinition:
        """Validate and detach an author mapping before exposing it as immutable."""
        from .validate import HarnessDefinitionValidationError, validate_harness_definition

        findings = validate_harness_definition(document)
        if findings:
            raise HarnessDefinitionValidationError(findings)
        return cls._from_validated(document)

    @classmethod
    def _from_validated(cls, document: Mapping[str, Any]) -> HarnessDefinition:
        instance = object.__new__(cls)
        frozen = _freeze(document)
        if not isinstance(frozen, Mapping):
            raise TypeError("a harness definition must be a mapping")
        object.__setattr__(instance, "_document", frozen)
        return instance

    def __getitem__(self, key: str) -> FrozenJsonValue:
        return self._document[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._document)

    def __len__(self) -> int:
        return len(self._document)

    def as_dict(self) -> dict[str, Any]:
        """Return a fully detached mutable copy of the author document."""
        return {key: _thaw(value) for key, value in self._document.items()}

    def canonical_json(self) -> str:
        """Return stable compact JSON without changing any author values."""
        return json.dumps(
            self.as_dict(),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
