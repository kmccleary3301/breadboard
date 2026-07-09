from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

_DIGEST_RE = re.compile(r"^(?:[a-zA-Z0-9._/:+-]+@)?sha256:[0-9a-f]{64}$")
_CONTENT_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

_TERMINAL_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "type": "function",
        "name": "shell",
        "description": "Run a shell command in the admitted BreadBoard sandbox workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_seconds": {"type": "integer", "minimum": 1},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "read_file",
        "description": "Read UTF-8 text from a workspace-relative file.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 0},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "write_file",
        "description": "Write UTF-8 text to a workspace-relative file.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "list_files",
        "description": "List files below a workspace-relative directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "depth": {"type": "integer", "minimum": 1, "maximum": 8},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "submit",
        "description": "Finish the episode after the task is complete.",
        "parameters": {
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result"],
            "additionalProperties": False,
        },
        "strict": True,
    },
)


def is_immutable_digest(value: str | None) -> bool:
    return bool(value and _DIGEST_RE.fullmatch(value))


def is_content_digest(value: str | None) -> bool:
    return bool(value and _CONTENT_DIGEST_RE.fullmatch(value))


def _text_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list")
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if not text:
            raise ValueError(f"{field_name} cannot contain empty values")
        out.append(text)
    return tuple(out)


@dataclass(frozen=True)
class HarnessProfile:
    name: str
    sandbox_driver: str
    max_turns: int
    action_timeout_seconds: int
    verifier_timeout_seconds: int
    max_observation_chars: int
    max_artifact_bytes: int
    network: str = "none"
    default_image_digest: str | None = None
    allowed_image_digests: tuple[str, ...] = ()
    require_repository_binding: bool = False
    repository_images: Mapping[str, str] = field(default_factory=dict)
    default_verifier_ref: str | None = None
    verifier_commands: Mapping[str, str] = field(default_factory=dict)
    setup_commands: tuple[str, ...] = ()
    artifact_paths: tuple[str, ...] = ()
    trusted_process: bool = False
    tools: tuple[dict[str, Any], ...] = _TERMINAL_TOOLS

    @staticmethod
    def from_mapping(name: str, data: Mapping[str, Any]) -> "HarnessProfile":
        driver = str(data.get("sandbox_driver") or "docker").strip().lower()
        if driver not in {"docker", "process"}:
            raise ValueError(
                f"profile {name!r} has unsupported sandbox_driver {driver!r}"
            )
        default_network = "host" if driver == "process" else "none"
        network = str(data.get("network") or default_network).strip().lower()
        expected_network = "host" if driver == "process" else "none"
        if network != expected_network:
            raise ValueError(
                f"profile {name!r} with driver {driver!r} must use network={expected_network!r}"
            )
        max_turns = int(data.get("max_turns", 30))
        action_timeout = int(data.get("action_timeout_seconds", 120))
        verifier_timeout = int(data.get("verifier_timeout_seconds", 300))
        max_observation_chars = int(data.get("max_observation_chars", 200_000))
        max_artifact_bytes = int(data.get("max_artifact_bytes", 8 * 1024 * 1024))
        if (
            min(
                max_turns,
                action_timeout,
                verifier_timeout,
                max_observation_chars,
                max_artifact_bytes,
            )
            < 1
        ):
            raise ValueError(
                f"profile {name!r} turn, timeout, and size limits must be positive"
            )
        trusted_process_value = data.get("trusted_process", False)
        if not isinstance(trusted_process_value, bool):
            raise ValueError(f"profile {name!r} trusted_process must be a boolean")
        trusted_process = trusted_process_value
        if driver == "process" and not trusted_process:
            raise ValueError(
                f"profile {name!r} must explicitly set trusted_process for process execution"
            )

        default_image = str(data.get("default_image_digest") or "").strip() or None
        allowed_images = _text_tuple(
            data.get("allowed_image_digests"), "allowed_image_digests"
        )
        for image in tuple(item for item in (default_image, *allowed_images) if item):
            if not is_immutable_digest(image):
                raise ValueError(
                    f"profile {name!r} image must be digest-qualified: {image!r}"
                )
        require_binding_value = data.get(
            "require_repository_binding", name == "breadboard_swe"
        )
        if not isinstance(require_binding_value, bool):
            raise ValueError(
                f"profile {name!r} require_repository_binding must be a boolean"
            )
        require_repository_binding = require_binding_value
        raw_repository_images = data.get("repository_images") or {}
        if not isinstance(raw_repository_images, Mapping):
            raise ValueError(f"profile {name!r} repository_images must be a mapping")
        repository_images = {
            str(snapshot).strip(): str(image).strip()
            for snapshot, image in raw_repository_images.items()
        }
        for snapshot, image in repository_images.items():
            if not _CONTENT_DIGEST_RE.fullmatch(snapshot):
                raise ValueError(
                    f"profile {name!r} repository snapshot keys must be sha256 digests"
                )
            if not is_immutable_digest(image):
                raise ValueError(
                    f"profile {name!r} repository image must be digest-qualified: {image!r}"
                )

        raw_verifiers = data.get("verifier_commands") or {}
        if not isinstance(raw_verifiers, Mapping):
            raise ValueError(f"profile {name!r} verifier_commands must be a mapping")
        verifier_commands = {
            str(key).strip(): str(command).strip()
            for key, command in raw_verifiers.items()
            if str(key).strip() and str(command).strip()
        }
        default_verifier = str(data.get("default_verifier_ref") or "").strip() or None
        if default_verifier and default_verifier not in verifier_commands:
            raise ValueError(f"profile {name!r} default_verifier_ref is not configured")

        return HarnessProfile(
            name=name,
            sandbox_driver=driver,
            max_turns=max_turns,
            action_timeout_seconds=action_timeout,
            verifier_timeout_seconds=verifier_timeout,
            max_observation_chars=max_observation_chars,
            max_artifact_bytes=max_artifact_bytes,
            network=network,
            default_image_digest=default_image,
            allowed_image_digests=allowed_images,
            require_repository_binding=require_repository_binding,
            repository_images=repository_images,
            default_verifier_ref=default_verifier,
            verifier_commands=verifier_commands,
            setup_commands=_text_tuple(data.get("setup_commands"), "setup_commands"),
            artifact_paths=_text_tuple(data.get("artifact_paths"), "artifact_paths"),
            trusted_process=trusted_process,
        )

    def admit_image(
        self, requested: str | None, repository_snapshot_digest: str | None = None
    ) -> str:
        image = requested or self.default_image_digest
        if not is_immutable_digest(image):
            raise ValueError(
                f"profile {self.name!r} requires an immutable sandbox image digest"
            )
        assert image is not None
        if self.require_repository_binding:
            if not repository_snapshot_digest or not _CONTENT_DIGEST_RE.fullmatch(
                repository_snapshot_digest
            ):
                raise ValueError(
                    f"profile {self.name!r} requires a repository snapshot sha256 digest"
                )
            expected_image = self.repository_images.get(repository_snapshot_digest)
            if expected_image is None:
                raise ValueError(
                    f"repository snapshot is not admitted by profile {self.name!r}"
                )
            if image != expected_image:
                raise ValueError(
                    "sandbox image does not match the admitted repository snapshot binding"
                )
            return image
        if self.allowed_image_digests:
            if image not in self.allowed_image_digests:
                raise ValueError(
                    f"sandbox image digest is not admitted by profile {self.name!r}"
                )
        elif self.default_image_digest is None or image != self.default_image_digest:
            raise ValueError(
                f"profile {self.name!r} has no server-admitted sandbox image digest"
            )
        return image

    def verifier_command(self, requested: str | None) -> tuple[str, str]:
        verifier_ref = requested or self.default_verifier_ref
        if not verifier_ref:
            raise ValueError(
                f"profile {self.name!r} requires a configured verifier_ref"
            )
        command = self.verifier_commands.get(verifier_ref)
        if not command:
            raise ValueError(
                f"verifier_ref {verifier_ref!r} is not admitted by profile {self.name!r}"
            )
        return verifier_ref, command

    def tool_definitions(self) -> list[dict[str, Any]]:
        return [dict(tool) for tool in self.tools]


class HarnessProfileRegistry:
    def __init__(self, profiles: Mapping[str, HarnessProfile]):
        self._profiles = dict(profiles)
        if not self._profiles:
            raise ValueError("at least one harness profile is required")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "HarnessProfileRegistry":
        profiles: dict[str, HarnessProfile] = {}
        for raw_name, raw_profile in data.items():
            name = str(raw_name or "").strip()
            if not name or not isinstance(raw_profile, Mapping):
                raise ValueError(
                    "harness profile names and values must be non-empty mappings"
                )
            profiles[name] = HarnessProfile.from_mapping(name, raw_profile)
        return cls(profiles)

    @classmethod
    def from_environment(cls) -> "HarnessProfileRegistry":
        path = os.environ.get("BREADBOARD_HARNESS_PROFILES_FILE", "").strip()
        inline = os.environ.get("BREADBOARD_HARNESS_PROFILES_JSON", "").strip()
        if path and inline:
            raise ValueError(
                "set only one of BREADBOARD_HARNESS_PROFILES_FILE or BREADBOARD_HARNESS_PROFILES_JSON"
            )
        if path:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        elif inline:
            payload = json.loads(inline)
        else:
            payload = {
                "breadboard_swe": {
                    "sandbox_driver": "docker",
                    "max_turns": 60,
                    "require_repository_binding": True,
                },
                "breadboard_terminal": {"sandbox_driver": "docker", "max_turns": 30},
            }
        if not isinstance(payload, Mapping):
            raise ValueError("harness profiles root must be a mapping")
        return cls.from_mapping(payload)

    def get(self, name: str) -> HarnessProfile:
        try:
            return self._profiles[name]
        except KeyError as exc:
            raise KeyError(f"unknown harness profile {name!r}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._profiles))


__all__ = [
    "HarnessProfile",
    "HarnessProfileRegistry",
    "is_content_digest",
    "is_immutable_digest",
]
