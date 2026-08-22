from __future__ import annotations

import base64
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Mapping

from pydantic import BaseModel


IPC_SCHEMA = "bb.rl.phase5.authority-ipc.v1"
_MAX_CODEC_DEPTH = 32
_MAX_COLLECTION_ITEMS = 4_096


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def encode_value(value: object, *, _depth: int = 0) -> object:
    if _depth > _MAX_CODEC_DEPTH:
        raise ValueError("authority IPC value nesting limit exceeded")
    if value is None or type(value) in {bool, int, float, str}:
        return value
    if isinstance(value, bytes):
        return {"type": "bytes", "value": base64.b64encode(value).decode("ascii")}
    if isinstance(value, datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, Enum):
        return {
            "type": "enum",
            "class": value.__class__.__name__,
            "value": value.value,
        }
    if (
        isinstance(value, BaseModel)
        and value.__class__.__name__ == "ScoreEvaluation"
    ):
        return {
            "type": "score_evaluation",
            "value": encode_value(
                {
                    "catalog": value.catalog,
                    "decisions": value.decisions,
                },
                _depth=_depth + 1,
            ),
        }
    if isinstance(value, BaseModel):
        return {
            "type": "model",
            "class": value.__class__.__name__,
            "value": encode_value(
                {
                    name: getattr(value, name)
                    for name in value.__class__.model_fields
                },
                _depth=_depth + 1,
            ),
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "type": "dataclass",
            "class": value.__class__.__name__,
            "value": {
                field.name: encode_value(
                    getattr(value, field.name), _depth=_depth + 1
                )
                for field in fields(value)
            },
        }
    if isinstance(value, (tuple, list, Mapping)) and len(value) > _MAX_COLLECTION_ITEMS:
        raise ValueError("authority IPC collection limit exceeded")
    if isinstance(value, tuple):
        return {
            "type": "tuple",
            "value": [
                encode_value(item, _depth=_depth + 1) for item in value
            ],
        }
    if isinstance(value, list):
        return {
            "type": "list",
            "value": [
                encode_value(item, _depth=_depth + 1) for item in value
            ],
        }
    if isinstance(value, Mapping) and all(isinstance(key, str) for key in value):
        return {
            "type": "mapping",
            "value": {
                key: encode_value(item, _depth=_depth + 1)
                for key, item in value.items()
            },
        }
    raise TypeError(f"unsupported authority IPC value: {type(value).__name__}")


def decode_value(
    value: object,
    registry: Mapping[str, type[object]],
    *,
    _depth: int = 0,
) -> object:
    if _depth > _MAX_CODEC_DEPTH:
        raise ValueError("authority IPC value nesting limit exceeded")
    if value is None or type(value) in {bool, int, float, str}:
        return value
    if not isinstance(value, dict) or set(value) not in (
        {"type", "value"},
        {"class", "type", "value"},
    ):
        raise ValueError("authority IPC value schema is invalid")
    kind = value["type"]
    encoded = value["value"]
    if isinstance(encoded, (list, dict)) and len(encoded) > _MAX_COLLECTION_ITEMS:
        raise ValueError("authority IPC collection limit exceeded")
    if kind == "bytes" and isinstance(encoded, str):
        return base64.b64decode(encoded, validate=True)
    if kind == "datetime" and isinstance(encoded, str):
        return datetime.fromisoformat(encoded)
    if kind in {"tuple", "list"} and isinstance(encoded, list):
        decoded = [
            decode_value(item, registry, _depth=_depth + 1)
            for item in encoded
        ]
        return tuple(decoded) if kind == "tuple" else decoded
    if kind == "mapping" and isinstance(encoded, dict):
        return {
            key: decode_value(item, registry, _depth=_depth + 1)
            for key, item in encoded.items()
        }
    if kind == "score_evaluation":
        payload = decode_value(encoded, registry, _depth=_depth + 1)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"catalog", "decisions"}
            or not isinstance(payload["catalog"], tuple)
            or not isinstance(payload["decisions"], tuple)
        ):
            raise ValueError("authority IPC score DTO payload is invalid")
        return payload
    class_name = value.get("class")
    cls = registry.get(class_name) if isinstance(class_name, str) else None
    if cls is None:
        raise ValueError("authority IPC value type is not allowlisted")
    if kind == "enum":
        return cls(encoded)
    if kind == "model":
        payload = decode_value(encoded, registry, _depth=_depth + 1)
        if not isinstance(payload, dict):
            raise ValueError("authority IPC model payload is invalid")
        if issubclass(cls, BaseModel):
            return cls.model_validate(payload)
        if is_dataclass(cls):
            return cls(**payload)
        raise ValueError("authority IPC model target is not allowlisted")
    if kind == "dataclass":
        payload = decode_value(
            {"type": "mapping", "value": encoded},
            registry,
            _depth=_depth + 1,
        )
        if not isinstance(payload, dict):
            raise ValueError("authority IPC dataclass payload is invalid")
        return cls(**payload)
    raise ValueError("authority IPC value kind is not allowlisted")


def parse_canonical_object(raw: bytes) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("authority IPC message is not JSON") from error
    if not isinstance(value, dict) or canonical_bytes(value) != raw:
        raise ValueError("authority IPC message is not canonical JSON")
    return value


__all__ = [
    "IPC_SCHEMA",
    "canonical_bytes",
    "decode_value",
    "digest",
    "encode_value",
    "parse_canonical_object",
]
