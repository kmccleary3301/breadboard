from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import re
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

_NORMALIZATION_KINDS = frozenset({"timestamp", "pid", "temporary_path"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_OID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:/")
_TRACE_KEYS = frozenset(
    {
        "schema_version",
        "target_id",
        "fixture_id",
        "events",
        "provider_requests",
        "provider_responses",
        "process",
        "workspace",
        "terminal",
    }
)
_MAX_JSON_DEPTH = 128
_MAX_JSON_NODES = 100_000
_MAX_JSON_BYTES = 64 * 1024 * 1024
_MAX_JSON_STRING_BYTES = 8 * 1024 * 1024
_MAX_JSON_INTEGER_BITS = 4096
_MAX_MISMATCHES = 10_000
_MAX_NORMALIZATION_RULES = 1024
_MAX_METADATA_TEXT_BYTES = 1024
_MAX_POINTER_DISPLAY_BYTES = 1024
_MAX_WORKSPACE_DEPTH = 128
_MAX_WORKSPACE_ENTRIES = 16_000
_MAX_WORKSPACE_BYTES = 4 * 1024 * 1024 * 1024
_MAX_WORKSPACE_FILE_BYTES = 1024 * 1024 * 1024
_MAX_WORKSPACE_PATH_BYTES = 4096
_MAX_WORKSPACE_COMPONENT_BYTES = 255
_PROCESS_KEYS = frozenset({"stdout_base64", "stderr_base64", "exit_code", "signal"})


class E4ParityError(ValueError):
    """Raised when an E4 trace or comparison policy is invalid."""


def _require_bounded_text(
    value: Any,
    field_name: str,
    *,
    max_bytes: int = _MAX_METADATA_TEXT_BYTES,
) -> str:
    if type(value) is not str or not value or not value.isprintable():
        raise E4ParityError(f"{field_name} must be nonempty printable text")
    try:
        encoded_bytes = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise E4ParityError(f"{field_name} must be valid Unicode text") from exc
    if encoded_bytes > max_bytes:
        raise E4ParityError(f"{field_name} exceeds {max_bytes} UTF-8 bytes")
    return value


@dataclass(frozen=True, slots=True)
class _PointerEvidence:
    display: str | None
    sha256: str
    depth: int

    @property
    def label(self) -> str:
        if self.display is not None:
            return self.display
        return f"<pointer sha256:{self.sha256}>"

    def as_dict(self) -> dict[str, Any]:
        return {
            "pointer_sha256": self.sha256,
            "pointer_depth": self.depth,
        }


@dataclass(slots=True)
class _JsonPointer:
    display: str | None
    depth: int
    _hasher: Any

    @classmethod
    def root(cls) -> _JsonPointer:
        return cls("", 0, hashlib.sha256())

    def child(self, token: str) -> _JsonPointer:
        escaped = token.replace("~", "~0").replace("/", "~1")
        encoded = escaped.encode("utf-8")
        hasher = self._hasher.copy()
        hasher.update(b"/")
        hasher.update(encoded)
        display = None
        if self.display is not None:
            candidate = f"{self.display}/{escaped}"
            if len(candidate.encode("utf-8")) <= _MAX_POINTER_DISPLAY_BYTES:
                display = candidate
        return _JsonPointer(display, self.depth + 1, hasher)

    @property
    def label(self) -> str:
        return self.evidence().label or "/"

    def evidence(self) -> _PointerEvidence:
        return _PointerEvidence(self.display, self._hasher.hexdigest(), self.depth)


def _pointer_evidence(pointer: str) -> dict[str, Any]:
    return {
        "pointer_sha256": hashlib.sha256(pointer.encode("utf-8")).hexdigest(),
        "pointer_depth": pointer.count("/"),
    }


def _pointer_location(pointer: str) -> _PointerEvidence:
    if type(pointer) is not str:
        raise TypeError("pointer must be an exact string")
    encoded = pointer.encode("utf-8")
    display = pointer if len(encoded) <= _MAX_POINTER_DISPLAY_BYTES else None
    return _PointerEvidence(
        display,
        hashlib.sha256(encoded).hexdigest(),
        pointer.count("/"),
    )


@dataclass(frozen=True, slots=True)
class NormalizationRule:
    pointer: str
    kind: str

    def __post_init__(self) -> None:
        if type(self.pointer) is not str or not self.pointer.startswith("/"):
            raise E4ParityError("normalization pointer must be a non-root JSON pointer")
        pointer = _require_bounded_text(self.pointer, "normalization pointer")
        if re.search(r"~(?:[^01]|$)", pointer):
            raise E4ParityError("normalization pointer contains an invalid escape")
        if type(self.kind) is not str or self.kind not in _NORMALIZATION_KINDS:
            raise E4ParityError(
                "normalization kind must be timestamp, pid, or temporary_path"
            )


@dataclass(frozen=True, slots=True, init=False)
class TraceMismatch:
    _location: _PointerEvidence
    reason: str
    reference: Any
    clone: Any

    def __init__(
        self,
        pointer: str | _PointerEvidence,
        reason: str,
        reference: Any,
        clone: Any,
    ) -> None:
        location = (
            pointer
            if isinstance(pointer, _PointerEvidence)
            else _pointer_location(pointer)
        )
        object.__setattr__(self, "_location", location)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "reference", reference)
        object.__setattr__(self, "clone", clone)

    @property
    def pointer(self) -> str:
        return self._location.label

    def as_dict(self) -> dict[str, Any]:
        return {
            **self._location.as_dict(),
            "reason": self.reason,
            "reference_type": type(self.reference).__name__,
            "clone_type": type(self.clone).__name__,
        }


@dataclass(frozen=True, slots=True, init=False)
class NormalizedField:
    _location: _PointerEvidence
    kind: str
    reference: Any
    clone: Any
    normalized: str

    def __init__(
        self,
        pointer: str | _PointerEvidence,
        kind: str,
        reference: Any,
        clone: Any,
        normalized: str,
    ) -> None:
        location = (
            pointer
            if isinstance(pointer, _PointerEvidence)
            else _pointer_location(pointer)
        )
        object.__setattr__(self, "_location", location)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "reference", reference)
        object.__setattr__(self, "clone", clone)
        object.__setattr__(self, "normalized", normalized)

    @property
    def pointer(self) -> str:
        return self._location.label

    def as_dict(self) -> dict[str, Any]:
        return {
            **self._location.as_dict(),
            "kind": self.kind,
            "normalized_sha256": hashlib.sha256(
                self.normalized.encode("utf-8")
            ).hexdigest(),
        }


@dataclass(frozen=True, slots=True)
class TraceComparison:
    mismatches: tuple[TraceMismatch, ...]
    normalized_fields: tuple[NormalizedField, ...]

    @property
    def matches(self) -> bool:
        return not self.mismatches


@dataclass(frozen=True, slots=True)
class TemporaryPathRoots:
    reference: str
    clone: str

    def __post_init__(self) -> None:
        _validate_absolute_path(self.reference, "reference temporary root")
        _validate_absolute_path(self.clone, "clone temporary root")


def _json_string_bytes(value: str, pointer: _JsonPointer) -> int:
    try:
        raw_bytes = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise E4ParityError(f"invalid Unicode string at {pointer.label}") from exc
    if raw_bytes > _MAX_JSON_STRING_BYTES:
        raise E4ParityError(
            f"JSON string byte size exceeds {_MAX_JSON_STRING_BYTES} at {pointer.label}"
        )
    return (raw_bytes * 6) + 2


def _account_json_bytes(state: list[int], amount: int, pointer: _JsonPointer) -> None:
    state[1] += amount
    if state[1] > _MAX_JSON_BYTES:
        raise E4ParityError(
            f"JSON byte size exceeds {_MAX_JSON_BYTES} at {pointer.label}"
        )


def _validate_closed_json(
    value: Any,
    *,
    pointer: _JsonPointer | None = None,
    ancestors: frozenset[int] = frozenset(),
    depth: int = 0,
    state: list[int] | None = None,
) -> None:
    if pointer is None:
        pointer = _JsonPointer.root()
    if depth > _MAX_JSON_DEPTH:
        raise E4ParityError(f"JSON depth exceeds {_MAX_JSON_DEPTH} at {pointer.label}")
    if state is None:
        state = [0, 0]
    state[0] += 1
    _account_json_bytes(state, 8, pointer)
    if state[0] > _MAX_JSON_NODES:
        raise E4ParityError(f"JSON node count exceeds {_MAX_JSON_NODES}")
    if value is None:
        _account_json_bytes(state, 4, pointer)
    elif type(value) is bool:
        _account_json_bytes(state, 4 if value else 5, pointer)
    elif type(value) is int:
        if value.bit_length() > _MAX_JSON_INTEGER_BITS:
            raise E4ParityError(
                f"integer exceeds {_MAX_JSON_INTEGER_BITS} bits at {pointer.label}"
            )
        try:
            integer_bytes = len(str(value))
        except (ValueError, MemoryError) as exc:
            raise E4ParityError(
                f"integer cannot be serialized at {pointer.label}"
            ) from exc
        _account_json_bytes(state, integer_bytes, pointer)
    elif type(value) is str:
        _account_json_bytes(state, _json_string_bytes(value, pointer), pointer)
    elif type(value) is float:
        if not math.isfinite(value):
            raise E4ParityError(f"non-finite number at {pointer.label}")
        _account_json_bytes(state, len(repr(value)), pointer)
    elif type(value) is list:
        identity = id(value)
        if identity in ancestors:
            raise E4ParityError(f"cyclic array at {pointer.label}")
        descendants = ancestors | {identity}
        for index, child in enumerate(value):
            _validate_closed_json(
                child,
                pointer=_child_pointer(pointer, str(index)),
                ancestors=descendants,
                depth=depth + 1,
                state=state,
            )
    elif type(value) is dict:
        identity = id(value)
        if identity in ancestors:
            raise E4ParityError(f"cyclic object at {pointer.label}")
        descendants = ancestors | {identity}
        for key, child in value.items():
            if type(key) is not str:
                raise E4ParityError(f"non-string object key at {pointer.label}")
            _account_json_bytes(
                state,
                _json_string_bytes(key, pointer) + 1,
                pointer,
            )
            _validate_closed_json(
                child,
                pointer=_child_pointer(pointer, key),
                ancestors=descendants,
                depth=depth + 1,
                state=state,
            )
    else:
        raise E4ParityError(f"non-JSON {type(value).__name__} value at {pointer.label}")


def canonical_json_bytes(value: Any) -> bytes:
    _validate_closed_json(value)
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RuntimeError, MemoryError) as exc:
        raise E4ParityError("value is not closed JSON") from exc
    if len(encoded) > _MAX_JSON_BYTES:
        raise E4ParityError(f"canonical JSON exceeds {_MAX_JSON_BYTES} bytes")
    return encoded


def json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _resolve_json_pointer(value: Any, pointer: str) -> tuple[bool, Any]:
    current = value
    for encoded_token in pointer[1:].split("/"):
        token = encoded_token.replace("~1", "/").replace("~0", "~")
        if type(current) is dict:
            if token not in current:
                return False, None
            current = current[token]
            continue
        if type(current) is list:
            canonical_index = (
                token.isascii()
                and token.isdecimal()
                and (token == "0" or not token.startswith("0"))
            )
            if not canonical_index:
                return False, None
            index = int(token)
            if index >= len(current):
                return False, None
            current = current[index]
            continue
        return False, None
    return True, current


def compare_e4_traces(
    reference: Any,
    clone: Any,
    *,
    rules: Sequence[NormalizationRule] = (),
    temporary_roots: TemporaryPathRoots | None = None,
) -> TraceComparison:
    _validate_closed_json(reference)
    _validate_closed_json(clone)
    if type(rules) not in {list, tuple}:
        raise TypeError("rules must be an exact list or tuple")
    if len(rules) > _MAX_NORMALIZATION_RULES:
        raise E4ParityError(
            f"normalization rule count exceeds {_MAX_NORMALIZATION_RULES}"
        )
    rules_by_pointer: dict[str, NormalizationRule] = {}
    for rule in rules:
        if type(rule) is not NormalizationRule:
            raise TypeError("rules must contain exact NormalizationRule values")
        if rule.pointer in rules_by_pointer:
            raise E4ParityError(f"duplicate normalization pointer {rule.pointer!r}")
        rules_by_pointer[rule.pointer] = rule
    sorted_rule_pointers = sorted(rules_by_pointer)
    for index, pointer in enumerate(sorted_rule_pointers):
        prefix = pointer + "/"
        if index + 1 < len(sorted_rule_pointers) and sorted_rule_pointers[
            index + 1
        ].startswith(prefix):
            raise E4ParityError(f"normalization pointers overlap at {pointer!r}")
    if any(rule.kind == "temporary_path" for rule in rules) and temporary_roots is None:
        raise E4ParityError("temporary_path normalization requires both trace roots")

    mismatches: list[TraceMismatch] = []
    normalized_fields: list[NormalizedField] = []
    truncated = False

    def record_mismatch(mismatch: TraceMismatch) -> None:
        nonlocal truncated
        if len(mismatches) >= _MAX_MISMATCHES:
            truncated = True
            return
        mismatches.append(mismatch)

    for pointer in sorted_rule_pointers:
        rule = rules_by_pointer[pointer]
        location = _pointer_location(pointer)
        reference_exists, reference_value = _resolve_json_pointer(reference, pointer)
        clone_exists, clone_value = _resolve_json_pointer(clone, pointer)
        if not reference_exists and not clone_exists:
            raise E4ParityError(
                f"normalization rules did not match trace fields: {pointer}"
            )
        if (
            not reference_exists
            or not clone_exists
            or type(reference_value) is not type(clone_value)
        ):
            continue
        try:
            reference_normalized = _normalize_value(
                reference_value,
                rule.kind,
                temporary_roots.reference if temporary_roots else None,
            )
            clone_normalized = _normalize_value(
                clone_value,
                rule.kind,
                temporary_roots.clone if temporary_roots else None,
            )
        except E4ParityError as exc:
            record_mismatch(
                TraceMismatch(
                    location,
                    f"invalid {rule.kind} normalization input: {exc}",
                    reference_value,
                    clone_value,
                )
            )
            continue
        normalized_fields.append(
            NormalizedField(
                location,
                rule.kind,
                reference_value,
                clone_value,
                reference_normalized,
            )
        )
        if reference_normalized != clone_normalized:
            record_mismatch(
                TraceMismatch(
                    location,
                    f"normalized {rule.kind} values differ",
                    reference_value,
                    clone_value,
                )
            )

    def compare(reference_value: Any, clone_value: Any, pointer: _JsonPointer) -> None:
        if truncated:
            return
        if type(reference_value) is not type(clone_value):
            record_mismatch(
                TraceMismatch(
                    pointer.evidence(),
                    "JSON types differ",
                    reference_value,
                    clone_value,
                )
            )
            return
        if pointer.display is not None and pointer.display in rules_by_pointer:
            return

        if isinstance(reference_value, dict):
            reference_keys = set(reference_value)
            clone_keys = set(clone_value)
            for key in sorted(reference_keys - clone_keys):
                child_pointer = _child_pointer(pointer, key)
                record_mismatch(
                    TraceMismatch(
                        child_pointer.evidence(),
                        "field is missing from clone",
                        reference_value[key],
                        None,
                    )
                )
                if truncated:
                    break
            if truncated:
                return
            for key in sorted(clone_keys - reference_keys):
                child_pointer = _child_pointer(pointer, key)
                record_mismatch(
                    TraceMismatch(
                        child_pointer.evidence(),
                        "unexpected clone field",
                        None,
                        clone_value[key],
                    )
                )
                if truncated:
                    break
            if truncated:
                return
            for key in sorted(reference_keys & clone_keys):
                if not isinstance(key, str):
                    raise E4ParityError("trace objects must use string keys")
                compare(
                    reference_value[key],
                    clone_value[key],
                    _child_pointer(pointer, key),
                )
            return
        if isinstance(reference_value, list):
            if len(reference_value) != len(clone_value):
                record_mismatch(
                    TraceMismatch(
                        pointer.evidence(),
                        "array lengths differ",
                        len(reference_value),
                        len(clone_value),
                    )
                )
            for index, (reference_item, clone_item) in enumerate(
                zip(reference_value, clone_value)
            ):
                compare(
                    reference_item,
                    clone_item,
                    _child_pointer(pointer, str(index)),
                )
            return
        values_differ = reference_value != clone_value or (
            type(reference_value) is float
            and repr(reference_value) != repr(clone_value)
        )
        if values_differ:
            record_mismatch(
                TraceMismatch(
                    pointer.evidence(),
                    "values differ",
                    reference_value,
                    clone_value,
                )
            )

    compare(reference, clone, _JsonPointer.root())
    if truncated:
        mismatches.append(
            TraceMismatch(
                _JsonPointer.root().evidence(),
                f"mismatch count exceeds {_MAX_MISMATCHES}",
                None,
                None,
            )
        )
    return TraceComparison(tuple(mismatches), tuple(normalized_fields))


def _workspace_snapshot_once(root: Path) -> dict[str, Any]:
    root = Path(root)
    required_dir_fd = (os.open, os.stat, os.readlink)
    if (
        not getattr(os, "O_DIRECTORY", 0)
        or not getattr(os, "O_NOFOLLOW", 0)
        or any(function not in os.supports_dir_fd for function in required_dir_fd)
        or os.scandir not in os.supports_fd
    ):
        raise E4ParityError(
            "secure descriptor-relative workspace traversal is unavailable"
        )
    directory_flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    entries: list[dict[str, Any]] = []
    total_bytes = 0
    pending_names = 0

    def fingerprint(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )

    def append_entry(entry: dict[str, Any]) -> None:
        if len(entries) >= _MAX_WORKSPACE_ENTRIES:
            raise E4ParityError(
                f"workspace entry count exceeds {_MAX_WORKSPACE_ENTRIES}"
            )
        entries.append(entry)

    def bounded_directory_names(
        directory_fd: int, relative: Path, limit: int
    ) -> list[str]:
        names: list[str] = []
        try:
            with os.scandir(directory_fd) as iterator:
                for item in iterator:
                    if len(names) >= limit:
                        raise E4ParityError(
                            f"workspace entry count exceeds {_MAX_WORKSPACE_ENTRIES}"
                        )
                    names.append(item.name)
        except E4ParityError:
            raise
        except OSError as exc:
            raise E4ParityError(
                f"could not read workspace directory {relative.as_posix() or '.'}"
            ) from exc
        names.sort()
        return names

    def visit(directory_fd: int, relative: Path, depth: int) -> None:
        nonlocal pending_names, total_bytes
        if depth > _MAX_WORKSPACE_DEPTH:
            raise E4ParityError(f"workspace depth exceeds {_MAX_WORKSPACE_DEPTH}")
        initial_names = bounded_directory_names(
            directory_fd,
            relative,
            _MAX_WORKSPACE_ENTRIES - len(entries) - pending_names,
        )
        pending_names += len(initial_names)
        for name in initial_names:
            pending_names -= 1
            if (
                not name
                or name in {".", ".."}
                or "/" in name
                or "\\" in name
                or ":" in name
                or not name.isprintable()
            ):
                raise E4ParityError("workspace contains an unsafe entry name")
            try:
                component_bytes = len(name.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise E4ParityError("workspace contains an unsafe entry name") from exc
            child_relative = relative / name
            relative_text = child_relative.as_posix()
            if (
                component_bytes > _MAX_WORKSPACE_COMPONENT_BYTES
                or len(relative_text.encode("utf-8")) > _MAX_WORKSPACE_PATH_BYTES
            ):
                raise E4ParityError(
                    f"workspace path exceeds admitted bounds: {relative_text}"
                )
            if len(child_relative.parts) > _MAX_WORKSPACE_DEPTH:
                raise E4ParityError(
                    f"workspace depth exceeds {_MAX_WORKSPACE_DEPTH}: {relative_text}"
                )
            try:
                initial_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise E4ParityError(
                    f"could not inspect workspace entry {relative_text}"
                ) from exc
            mode = stat.S_IMODE(initial_stat.st_mode)
            if stat.S_ISLNK(initial_stat.st_mode):
                try:
                    target = os.readlink(name, dir_fd=directory_fd)
                    final_stat = os.stat(
                        name,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    raise E4ParityError(
                        f"could not snapshot workspace link {relative_text}"
                    ) from exc
                if fingerprint(initial_stat) != fingerprint(final_stat):
                    raise E4ParityError(
                        f"workspace link changed during snapshot: {relative_text}"
                    )
                append_entry(
                    {
                        "path": relative_text,
                        "kind": "symlink",
                        "mode": mode,
                        "target": target,
                    }
                )
                continue
            if stat.S_ISDIR(initial_stat.st_mode):
                try:
                    child_fd = os.open(
                        name,
                        directory_flags,
                        dir_fd=directory_fd,
                    )
                except OSError as exc:
                    raise E4ParityError(
                        f"could not open workspace directory {relative_text}"
                    ) from exc
                try:
                    opened_stat = os.fstat(child_fd)
                    if not stat.S_ISDIR(opened_stat.st_mode) or fingerprint(
                        initial_stat
                    ) != fingerprint(opened_stat):
                        raise E4ParityError(
                            f"workspace directory changed during snapshot: {relative_text}"
                        )
                    append_entry(
                        {
                            "path": relative_text,
                            "kind": "directory",
                            "mode": stat.S_IMODE(opened_stat.st_mode),
                        }
                    )
                    visit(child_fd, child_relative, depth + 1)
                    if fingerprint(opened_stat) != fingerprint(os.fstat(child_fd)):
                        raise E4ParityError(
                            f"workspace directory changed during snapshot: {relative_text}"
                        )
                finally:
                    os.close(child_fd)
                continue
            if stat.S_ISREG(initial_stat.st_mode):
                if initial_stat.st_size > _MAX_WORKSPACE_FILE_BYTES:
                    raise E4ParityError(
                        f"workspace file exceeds {_MAX_WORKSPACE_FILE_BYTES} bytes: "
                        f"{relative_text}"
                    )
                try:
                    file_fd = os.open(name, file_flags, dir_fd=directory_fd)
                except OSError as exc:
                    raise E4ParityError(
                        f"could not open workspace file {relative_text}"
                    ) from exc
                try:
                    opened_stat = os.fstat(file_fd)
                    if (
                        not stat.S_ISREG(opened_stat.st_mode)
                        or opened_stat.st_nlink != 1
                        or fingerprint(initial_stat) != fingerprint(opened_stat)
                    ):
                        raise E4ParityError(
                            f"workspace file changed during snapshot: {relative_text}"
                        )
                    digest = hashlib.sha256()
                    file_bytes = 0
                    while True:
                        chunk = os.read(file_fd, 1024 * 1024)
                        if not chunk:
                            break
                        file_bytes += len(chunk)
                        total_bytes += len(chunk)
                        if file_bytes > _MAX_WORKSPACE_FILE_BYTES:
                            raise E4ParityError(
                                f"workspace file exceeds "
                                f"{_MAX_WORKSPACE_FILE_BYTES} bytes: {relative_text}"
                            )
                        if total_bytes > _MAX_WORKSPACE_BYTES:
                            raise E4ParityError(
                                f"workspace bytes exceed {_MAX_WORKSPACE_BYTES}"
                            )
                        digest.update(chunk)
                    final_stat = os.fstat(file_fd)
                    if (
                        fingerprint(opened_stat) != fingerprint(final_stat)
                        or file_bytes != final_stat.st_size
                    ):
                        raise E4ParityError(
                            f"workspace file changed during snapshot: {relative_text}"
                        )
                    append_entry(
                        {
                            "path": relative_text,
                            "kind": "file",
                            "mode": stat.S_IMODE(final_stat.st_mode),
                            "bytes": file_bytes,
                            "sha256": digest.hexdigest(),
                        }
                    )
                finally:
                    os.close(file_fd)
                continue
            raise E4ParityError(f"workspace contains unsupported entry {relative_text}")
        final_names = bounded_directory_names(
            directory_fd,
            relative,
            len(initial_names),
        )
        if initial_names != final_names:
            raise E4ParityError(
                f"workspace directory changed during snapshot: "
                f"{relative.as_posix() or '.'}"
            )

    try:
        root_fd = os.open(root, directory_flags)
    except OSError as exc:
        raise E4ParityError(
            f"workspace root is not a secure directory: {root}"
        ) from exc
    try:
        root_stat = os.fstat(root_fd)
        visit(root_fd, Path(), 0)
        if fingerprint(root_stat) != fingerprint(os.fstat(root_fd)):
            raise E4ParityError("workspace root changed during snapshot")
    finally:
        os.close(root_fd)
    entries.sort(key=lambda entry: entry["path"])
    snapshot = {
        "schema_version": "bb.e4.workspace_snapshot.v1",
        "entries": entries,
    }
    validate_workspace_snapshot(snapshot)
    return snapshot


def workspace_snapshot(root: Path) -> dict[str, Any]:
    """Snapshot a quiescent workspace through two descriptor-safe observations."""
    first = _workspace_snapshot_once(root)
    second = _workspace_snapshot_once(root)
    if first != second:
        raise E4ParityError("workspace changed between snapshot observations")
    return second


def validate_workspace_snapshot(snapshot: dict[str, Any]) -> None:
    _validate_closed_json(snapshot)
    if (
        type(snapshot) is not dict
        or set(snapshot) != {"schema_version", "entries"}
        or snapshot.get("schema_version") != "bb.e4.workspace_snapshot.v1"
        or type(snapshot.get("entries")) is not list
    ):
        raise E4ParityError("workspace snapshot must contain exact schema and entries")
    entries = snapshot["entries"]
    if len(entries) > _MAX_WORKSPACE_ENTRIES:
        raise E4ParityError(f"workspace entry count exceeds {_MAX_WORKSPACE_ENTRIES}")
    paths: list[str] = []
    total_bytes = 0
    for index, entry in enumerate(entries):
        context = f"workspace entry {index}"
        if type(entry) is not dict:
            raise E4ParityError(f"{context} must be an object")
        path = entry.get("path")
        kind = entry.get("kind")
        mode = entry.get("mode")
        if (
            type(path) is not str
            or not path
            or path.startswith("/")
            or "\\" in path
            or ":" in path
        ):
            raise E4ParityError(f"{context} path must be safe and relative")
        parts = path.split("/")
        if (
            len(path.encode("utf-8")) > _MAX_WORKSPACE_PATH_BYTES
            or len(parts) > _MAX_WORKSPACE_DEPTH
            or any(
                part in {"", ".", ".."}
                or not part.isprintable()
                or len(part.encode("utf-8")) > _MAX_WORKSPACE_COMPONENT_BYTES
                for part in parts
            )
        ):
            raise E4ParityError(f"{context} path must be safe and relative")
        if type(mode) is not int or not 0 <= mode <= 0o7777:
            raise E4ParityError(f"{context} mode must be an exact permission mode")
        if kind == "directory":
            expected_keys = {"path", "kind", "mode"}
        elif kind == "file":
            expected_keys = {"path", "kind", "mode", "bytes", "sha256"}
            byte_count = entry.get("bytes")
            digest = entry.get("sha256")
            if (
                type(byte_count) is not int
                or byte_count < 0
                or byte_count > _MAX_WORKSPACE_FILE_BYTES
                or type(digest) is not str
                or _SHA256_RE.fullmatch(digest) is None
            ):
                raise E4ParityError(f"{context} file size and digest must be valid")
            total_bytes += byte_count
            if total_bytes > _MAX_WORKSPACE_BYTES:
                raise E4ParityError(f"workspace bytes exceed {_MAX_WORKSPACE_BYTES}")
        elif kind == "symlink":
            expected_keys = {"path", "kind", "mode", "target"}
            target = entry.get("target")
            if (
                type(target) is not str
                or not target
                or not target.isprintable()
                or len(target.encode("utf-8")) > _MAX_WORKSPACE_PATH_BYTES
            ):
                raise E4ParityError(f"{context} symlink target must be valid text")
        else:
            raise E4ParityError(f"{context} kind is unsupported")
        if set(entry) != expected_keys:
            raise E4ParityError(f"{context} must contain exact {kind} fields")
        paths.append(path)
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise E4ParityError("workspace entries must be sorted with unique paths")
    entry_kinds = {entry["path"]: entry["kind"] for entry in entries}
    for path in paths:
        parts = path.split("/")
        for depth in range(1, len(parts)):
            parent = "/".join(parts[:depth])
            if entry_kinds.get(parent) != "directory":
                raise E4ParityError(
                    f"workspace parent {parent!r} must exist as a directory"
                )


def validate_e4_trace(trace: dict[str, Any]) -> None:
    if type(trace) is not dict:
        raise TypeError("trace must be an exact dict")
    _validate_closed_json(trace)
    if set(trace) != _TRACE_KEYS:
        raise E4ParityError("trace must contain the exact execution-trace fields")
    if trace["schema_version"] != "bb.e4.execution_trace.v1":
        raise E4ParityError("trace schema_version must be bb.e4.execution_trace.v1")
    for field_name in ("target_id", "fixture_id"):
        value = _require_bounded_text(
            trace[field_name], f"trace {field_name}", max_bytes=256
        )
        if value != value.strip():
            raise E4ParityError(f"trace {field_name} must be normalized text")
    for field_name in ("events", "provider_requests", "provider_responses"):
        if type(trace[field_name]) is not list:
            raise E4ParityError(f"trace {field_name} must be an array")
    if not trace["events"]:
        raise E4ParityError("trace events must not be empty")
    process = trace["process"]
    if type(process) is not dict or set(process) != _PROCESS_KEYS:
        raise E4ParityError("trace process must contain the exact process fields")
    for field_name in ("stdout_base64", "stderr_base64"):
        value = process[field_name]
        if type(value) is not str:
            raise E4ParityError(f"trace process.{field_name} must be base64 text")
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise E4ParityError(
                f"trace process.{field_name} must be canonical base64"
            ) from exc
        if base64.b64encode(decoded).decode("ascii") != value:
            raise E4ParityError(f"trace process.{field_name} must be canonical base64")
    exit_code = process["exit_code"]
    signal_number = process["signal"]
    if exit_code is not None and (type(exit_code) is not int or exit_code < 0):
        raise E4ParityError(
            "trace process.exit_code must be a non-negative integer or null"
        )
    if signal_number is not None and (
        type(signal_number) is not int or signal_number < 1
    ):
        raise E4ParityError("trace process.signal must be a positive integer or null")
    if (exit_code is None) == (signal_number is None):
        raise E4ParityError("trace process must set exactly one of exit_code or signal")
    workspace = trace["workspace"]
    validate_workspace_snapshot(workspace)
    terminal = trace["terminal"]
    if type(terminal) is not dict or set(terminal) != {"reason", "result", "error"}:
        raise E4ParityError("trace terminal must contain exact terminal fields")
    reason = _require_bounded_text(
        terminal["reason"], "trace terminal.reason", max_bytes=256
    )
    if reason != reason.strip():
        raise E4ParityError("trace terminal.reason must be normalized text")
    if terminal["result"] is not None and terminal["error"] is not None:
        raise E4ParityError("trace terminal cannot contain both result and error")


def build_e4_parity_report(
    *,
    target_id: str,
    target_descriptor_sha256: str,
    target_config_sha256: str,
    upstream_identity: dict[str, Any],
    fixture_id: str,
    fixture_sha256: str,
    engine_commit: str,
    built_package_sha256: str,
    reference_trace: dict[str, Any],
    clone_trace: dict[str, Any],
    normalization_rules: Sequence[NormalizationRule] = (),
    temporary_roots: TemporaryPathRoots | None = None,
) -> dict[str, Any]:
    for field_name, value in (
        ("target_descriptor_sha256", target_descriptor_sha256),
        ("target_config_sha256", target_config_sha256),
        ("fixture_sha256", fixture_sha256),
        ("built_package_sha256", built_package_sha256),
    ):
        if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
            raise E4ParityError(f"{field_name} must be a lowercase SHA-256")
    for field_name, value in (
        ("target_id", target_id),
        ("fixture_id", fixture_id),
    ):
        normalized = _require_bounded_text(value, field_name, max_bytes=256)
        if normalized != normalized.strip():
            raise E4ParityError(f"{field_name} must be normalized text")
    if type(engine_commit) is not str or _GIT_OID_RE.fullmatch(engine_commit) is None:
        raise E4ParityError("engine_commit must be a full lowercase Git object ID")
    if type(upstream_identity) is not dict:
        raise TypeError("upstream_identity must be an exact dict")
    if type(reference_trace) is not dict or type(clone_trace) is not dict:
        raise TypeError("traces must be exact dicts")
    if type(normalization_rules) not in {list, tuple}:
        raise TypeError("normalization_rules must be an exact list or tuple")
    if len(normalization_rules) > _MAX_NORMALIZATION_RULES:
        raise E4ParityError(
            f"normalization rule count exceeds {_MAX_NORMALIZATION_RULES}"
        )
    identity_bytes = canonical_json_bytes(upstream_identity)
    reference_bytes = canonical_json_bytes(reference_trace)
    clone_bytes = canonical_json_bytes(clone_trace)
    reference_snapshot = json.loads(reference_bytes)
    clone_snapshot = json.loads(clone_bytes)
    validate_e4_trace(reference_snapshot)
    validate_e4_trace(clone_snapshot)
    if (
        reference_snapshot["target_id"] != target_id
        or clone_snapshot["target_id"] != target_id
    ):
        raise E4ParityError("trace target IDs must match the report target")
    if (
        reference_snapshot["fixture_id"] != fixture_id
        or clone_snapshot["fixture_id"] != fixture_id
    ):
        raise E4ParityError("trace fixture IDs must match the report fixture")
    rules = tuple(normalization_rules)
    comparison = compare_e4_traces(
        reference_snapshot,
        clone_snapshot,
        rules=rules,
        temporary_roots=temporary_roots,
    )
    return {
        "schema_version": "bb.e4.parity_report.v2",
        "target_id": target_id,
        "target_descriptor_sha256": target_descriptor_sha256,
        "target_config_sha256": target_config_sha256,
        "upstream_identity_sha256": hashlib.sha256(identity_bytes).hexdigest(),
        "fixture_id": fixture_id,
        "fixture_sha256": fixture_sha256,
        "engine_commit": engine_commit,
        "built_package_sha256": built_package_sha256,
        "reference_trace_sha256": hashlib.sha256(reference_bytes).hexdigest(),
        "clone_trace_sha256": hashlib.sha256(clone_bytes).hexdigest(),
        "normalization_rules": [
            {
                **_pointer_evidence(rule.pointer),
                "kind": rule.kind,
            }
            for rule in rules
        ],
        "status": "passed" if comparison.matches else "failed",
        "normalized_fields": [
            field.as_dict() for field in comparison.normalized_fields
        ],
        "mismatches": [mismatch.as_dict() for mismatch in comparison.mismatches],
    }


def _child_pointer(pointer: _JsonPointer, token: str) -> _JsonPointer:
    return pointer.child(token)


def _normalize_value(value: Any, kind: str, temporary_root: str | None) -> str:
    if kind == "timestamp":
        if type(value) is bool:
            raise E4ParityError("timestamp cannot be boolean")
        if type(value) is int:
            return "<timestamp>"
        if type(value) is float:
            if not math.isfinite(value):
                raise E4ParityError("timestamp must be finite")
            return "<timestamp>"
        if isinstance(value, str):
            candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
            try:
                parsed = datetime.fromisoformat(candidate)
            except ValueError as exc:
                raise E4ParityError(
                    "timestamp string must be timezone-aware ISO-8601"
                ) from exc
            if parsed.tzinfo is None:
                raise E4ParityError("timestamp string must be timezone-aware ISO-8601")
            return "<timestamp>"
        raise E4ParityError("timestamp must be numeric or ISO-8601 text")
    if kind == "pid":
        if type(value) is not int or value < 1:
            raise E4ParityError("pid must be a positive integer")
        return "<pid>"
    if kind == "temporary_path":
        if temporary_root is None:
            raise E4ParityError("temporary path root is missing")
        return _normalize_temporary_path(value, temporary_root)
    raise AssertionError(f"unhandled normalization kind {kind!r}")


def _normalize_temporary_path(value: Any, root: str) -> str:
    if type(value) is not str:
        raise E4ParityError("temporary path must be text")
    normalized_value = value.replace("\\", "/").rstrip("/")
    normalized_root = root.replace("\\", "/").rstrip("/")
    _validate_absolute_path(normalized_value, "temporary path")
    if normalized_value == normalized_root:
        return "<tmp>"
    prefix = normalized_root + "/"
    if not normalized_value.startswith(prefix):
        raise E4ParityError("temporary path is outside its admitted root")
    remainder = normalized_value[len(prefix) :]
    if any(
        part in {"", ".", ".."} or not part.isprintable()
        for part in remainder.split("/")
    ):
        raise E4ParityError("temporary path contains an unsafe component")
    return f"<tmp>/{remainder}"


def _validate_absolute_path(value: str, field_name: str) -> None:
    _require_bounded_text(value, field_name, max_bytes=_MAX_WORKSPACE_PATH_BYTES)
    normalized = value.replace("\\", "/")
    is_posix = normalized.startswith("/")
    is_windows = _WINDOWS_ABSOLUTE_RE.match(normalized) is not None
    if not is_posix and not is_windows:
        raise E4ParityError(f"{field_name} must be absolute")
    if normalized == "/" or re.fullmatch(r"[A-Za-z]:/?", normalized):
        raise E4ParityError(f"{field_name} cannot be a filesystem root")
    components = normalized[1:].split("/") if is_posix else normalized[3:].split("/")
    if any(
        part in {"", ".", ".."}
        or not part.isprintable()
        or len(part.encode("utf-8")) > _MAX_WORKSPACE_COMPONENT_BYTES
        for part in components
    ):
        raise E4ParityError(f"{field_name} contains an unsafe component")
