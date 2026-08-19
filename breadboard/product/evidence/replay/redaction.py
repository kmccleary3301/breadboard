from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .plan import canonical_json
from .ports import ReplayWorkerResult

_SECRET_NAME_PARTS = (
    "access_key",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "cookie",
    "credential",
    "password",
    "passphrase",
    "private_key",
    "secret",
    "session_key",
    "token",
)
_REDACTED = "[REDACTED]"
_WORKSPACE = "<workspace>"


def is_secret_environment_name(name: str) -> bool:
    normalized = name.strip().lower().replace("-", "_")
    segments = frozenset(normalized.split("_"))
    return any(part in normalized for part in _SECRET_NAME_PARTS) or bool(
        segments.intersection({"auth", "key"})
    )


@dataclass(frozen=True, slots=True)
class ReplayRedactor:
    """Remove explicitly bound secrets and the absolute workspace from durable replay data."""

    secrets: tuple[str, ...] = ()
    workspace: str | None = None

    def __init__(
        self, secrets: Iterable[str] = (), workspace: str | Path | None = None
    ) -> None:
        values = tuple(
            sorted(
                {value for value in secrets if isinstance(value, str) and value},
                key=len,
                reverse=True,
            )
        )
        resolved = (
            str(Path(workspace).expanduser().resolve())
            if workspace is not None
            else None
        )
        object.__setattr__(self, "secrets", values)
        object.__setattr__(self, "workspace", resolved)

    def text(self, value: str) -> str:
        redacted = value
        for secret in self.secrets:
            redacted = redacted.replace(secret, _REDACTED)
        if self.workspace:
            redacted = redacted.replace(self.workspace, _WORKSPACE)
        return redacted

    def value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, Mapping):
            return {
                self.text(str(key)): self.value(item) for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self.value(item) for item in value]
        return value

    def bytes(self, value: bytes, *, media_type: str) -> bytes:
        if not self.secrets and not self.workspace:
            return value
        if media_type == "application/json" or media_type.endswith("+json"):
            try:
                document = json.loads(value)
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
            else:
                return canonical_json(self.value(document)) + b"\n"
        redacted = value
        for secret in self.secrets:
            redacted = redacted.replace(secret.encode(), _REDACTED.encode())
        if self.workspace:
            redacted = redacted.replace(self.workspace.encode(), _WORKSPACE.encode())
        return redacted

    def worker_result(
        self,
        result: ReplayWorkerResult,
        media_types: Mapping[str, str],
        transcript_path: str,
    ) -> ReplayWorkerResult:
        outputs = {
            path: self.bytes(content, media_type=media_types[path])
            for path, content in result.outputs.items()
        }
        transcript = tuple(self.value(row) for row in result.transcript)
        if transcript_path in outputs:
            raise ValueError("worker outputs cannot contain the normalized transcript")
        return ReplayWorkerResult(outputs, transcript)
