"""Immutable effective-lock records and their canonical hash encoding."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any


def _copy(value: Any, *, freeze: bool) -> Any:
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TypeError("JSON object keys must be strings")
        copied = {key: _copy(item, freeze=freeze) for key, item in value.items()}
        return MappingProxyType(copied) if freeze else copied
    if isinstance(value, (list, tuple)):
        copied = [_copy(item, freeze=freeze) for item in value]
        return tuple(copied) if freeze else copied
    json.dumps(value, allow_nan=False)
    return value


def canonical_json_bytes(value: Any) -> bytes:
    """Return the frozen graph records' canonical UTF-8 encoding."""
    plain = _copy(value, freeze=False)
    text = json.dumps(plain, allow_nan=False, ensure_ascii=False, indent=2,
                      separators=(",", ": "), sort_keys=True) + "\n"
    return text.encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def graph_content_hash(record: Mapping[str, Any]) -> str:
    preimage = _copy(record, freeze=False)
    preimage["graph_hash"] = None
    return sha256_json(preimage)


@dataclass(frozen=True, slots=True, init=False)
class _FrozenRecord(Mapping[str, Any]):
    _record: Mapping[str, Any] = field(repr=False)

    @classmethod
    def _from_record(cls, record: Mapping[str, Any]):
        instance = object.__new__(cls)
        object.__setattr__(instance, "_record", _copy(record, freeze=True))
        return instance

    def __getitem__(self, key: str) -> Any:
        return self._record[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._record)

    def __len__(self) -> int:
        return len(self._record)

    def as_dict(self) -> dict[str, Any]:
        return _copy(self._record, freeze=False)

    def canonical_json(self) -> str:
        return canonical_json_bytes(self._record).decode("utf-8")


class EffectiveHarnessLock(_FrozenRecord):
    """Recursively immutable ``bb.effective_config_graph.v1`` record."""
    __slots__ = ()
