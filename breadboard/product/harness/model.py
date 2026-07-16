"""Immutable author-side representation of a BreadBoard harness definition."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, TypeAlias

JsonScalar: TypeAlias = None | bool | int | float | str
FrozenJsonValue: TypeAlias = (
    JsonScalar | Mapping[str, "FrozenJsonValue"] | tuple["FrozenJsonValue", ...]
)


def _freeze(value: Any) -> FrozenJsonValue:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: FrozenJsonValue) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


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
        """Construct from a mapping that has already passed public validation."""
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
