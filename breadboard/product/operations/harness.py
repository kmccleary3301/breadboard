from __future__ import annotations

import json
import os
import shlex
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from breadboard.product.harness.lock import lock_metadata_path, lock_path, sha256_json
from breadboard.product.harness.resolution import (
    compile_harness_source,
    load_harness_document,
)
from breadboard.product.harness.templates import (
    DAILY_DRIVER_MODEL_ROLES_NAME,
    DAILY_DRIVER_PROMPT_BUNDLE_PATH,
    DAILY_DRIVER_TEMPLATE_NAME,
    daily_driver_model_roles_path,
    daily_driver_prompt_path,
    daily_driver_template_path,
)
from breadboard.product.harness.validate import (
    HarnessDefinitionValidationError,
    load_harness_definition,
)
from breadboard.product.operations.model import (
    EXIT_VALIDATION_FAILURE,
    OperationContext,
    OperationResult,
    from_exception,
    portable_ref,
)


@dataclass(frozen=True, slots=True)
class CreateHarnessRequest:
    directory: str | Path = "."


@dataclass(frozen=True, slots=True)
class LockHarnessRequest:
    path: str | Path
    out: str | Path | None = None
    check: bool = False


@dataclass(frozen=True, slots=True)
class UpdateHarnessRequest:
    path: str | Path
    definition: dict[str, Any] | None = None
    source: str | Path | None = None


@dataclass(frozen=True, slots=True)
class ListHarnessesRequest:
    directory: str | Path | None = None


@dataclass(frozen=True, slots=True)
class GetHarnessRequest:
    path: str | Path


@dataclass(frozen=True, slots=True)
class ValidateHarnessRequest:
    path: str | Path


@dataclass(frozen=True, slots=True)
class ExplainHarnessRequest:
    path: str | Path


@dataclass(frozen=True, slots=True)
class GetHarnessLockRequest:
    path: str | Path


_MUTATION_LOCK = threading.RLock()


def _is_harness_path(path: Path, context: OperationContext) -> bool:
    try:
        if "harness" not in path.name and path.name != DAILY_DRIVER_TEMPLATE_NAME:
            return False
        if not path.is_file() or path.is_symlink():
            return False
        reference: str | Path = path
        if context.contained:
            reference = path.relative_to(context.workspace)
        resolved = context.resolve_path(reference)
        load_harness_definition(resolved)
        return True
    except (OSError, PermissionError, TypeError, ValueError, yaml.YAMLError):
        return False


def _validate_output_path(path: Path, context: OperationContext) -> None:
    if context.contained:
        context.resolve_path(path.relative_to(context.workspace))
    elif path.is_symlink():
        raise ValueError("mutation target cannot be a symlink")


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def _path_identity(path: Path) -> tuple[int, int]:
    stat = os.lstat(path)
    return stat.st_dev, stat.st_ino


def _remove_published(path: Path, identity: tuple[int, int]) -> None:
    try:
        if _path_identity(path) == identity:
            path.unlink()
    except FileNotFoundError:
        pass


def _rollback_published(
    published: list[tuple[Path, tuple[int, int]]],
) -> None:
    for path, identity in reversed(published):
        _remove_published(path, identity)


def _publish_seed(path: Path, content: bytes) -> tuple[int, int] | None:
    temporary = path.with_name(f".{path.name}.{os.urandom(8).hex()}.tmp")
    descriptor: int | None = None
    published: tuple[int, int] | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        identity = _path_identity(temporary)
        try:
            os.link(temporary, path)
        except FileExistsError:
            return None
        published = identity
        return identity
    except BaseException:
        if published is not None:
            _remove_published(path, published)
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except BaseException:
            if published is not None:
                _remove_published(path, published)
            raise


def _seed_mismatch(path: Path, content: bytes) -> bool:
    return path.is_symlink() or (
        path.exists() and (not path.is_file() or path.read_bytes() != content)
    )


def daily_driver_bundle_paths(directory: str | Path) -> tuple[Path, Path, Path]:
    root = Path(directory)
    return (
        root / DAILY_DRIVER_TEMPLATE_NAME,
        root / DAILY_DRIVER_PROMPT_BUNDLE_PATH,
        root / DAILY_DRIVER_MODEL_ROLES_NAME,
    )


def create_harness(
    request: CreateHarnessRequest,
    context: OperationContext,
) -> OperationResult:
    command = ["harness", "init"]
    stage = "harness.init"
    try:
        directory = context.resolve_path(request.directory)
        paths = daily_driver_bundle_paths(directory)
        for path in paths:
            _validate_output_path(path, context)
        seeds = (
            (paths[0], daily_driver_template_path().read_bytes()),
            (paths[1], daily_driver_prompt_path().read_bytes()),
            (paths[2], daily_driver_model_roles_path().read_bytes()),
        )
        directory.mkdir(parents=True, exist_ok=True)
        with _MUTATION_LOCK:
            if any(_seed_mismatch(path, content) for path, content in seeds):
                return OperationResult.failure(
                    command,
                    2,
                    "path_exists",
                    "refusing to overwrite existing harness bundle",
                    stage,
                )
            published: list[tuple[Path, tuple[int, int]]] = []
            try:
                for path, content in seeds:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if not path.exists():
                        if identity := _publish_seed(path, content):
                            published.append((path, identity))
                if any(_seed_mismatch(path, content) for path, content in seeds):
                    _rollback_published(published)
                    return OperationResult.failure(
                        command,
                        2,
                        "path_exists",
                        "refusing to overwrite existing harness bundle",
                        stage,
                    )
            except BaseException:
                _rollback_published(published)
                raise
        refs = [portable_ref(path, context.workspace) for path, _ in seeds]
        return OperationResult.success(
            command,
            {
                "path": refs[0],
                "prompt_path": refs[1],
                "model_roles_path": refs[2],
            },
            refs,
            stage=stage,
        )
    except Exception as error:
        return from_exception(command, error, stage)


def _stage_bytes(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.urandom(8).hex()}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def _write_lock_pair(
    lock: Path,
    metadata: Path,
    lock_content: bytes,
    metadata_content: bytes,
) -> None:
    paths = (lock, metadata)
    snapshots: dict[Path, bytes | None] = {}
    for path in paths:
        if path.is_symlink():
            raise ValueError("mutation target cannot be a symlink")
        if path.exists() and not path.is_file():
            raise IsADirectoryError(path)
        snapshots[path] = path.read_bytes() if path.exists() else None

    staged: list[tuple[Path, Path]] = []
    committed: list[Path] = []
    try:
        for path, content in zip(paths, (lock_content, metadata_content)):
            staged.append((path, _stage_bytes(path, content)))
        try:
            for path, temporary in staged:
                os.replace(temporary, path)
                committed.append(path)
        except BaseException:
            for path in reversed(committed):
                previous = snapshots[path]
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(previous)
            raise
    finally:
        for _, temporary in staged:
            temporary.unlink(missing_ok=True)


def _lock_target(
    request: LockHarnessRequest,
    source: Path,
    context: OperationContext,
) -> Path:
    output = None if request.out is None else context.resolve_path(request.out)
    target = lock_path(source, output)
    _validate_output_path(target, context)
    _validate_output_path(lock_metadata_path(target), context)
    return target


def lock_harness(
    request: LockHarnessRequest,
    context: OperationContext,
) -> OperationResult:
    command = ["harness", "lock"]
    stage = "harness.lock"
    try:
        source = context.resolve_path(request.path)
        with _MUTATION_LOCK:
            target = _lock_target(request, source, context)
            compilation = compile_harness_source(
                source, context.workspace, context.contained
            )
            metadata = {
                "schema_version": "bb.harness_lock_metadata.v1",
                "source_ref": portable_ref(source, context.workspace),
                "source_sha256": sha256_json(compilation.resolved_author_dict()),
                "graph_hash": compilation.lock["graph_hash"],
            }
            metadata_path = lock_metadata_path(target)
            if request.check:
                if not target.exists() or not metadata_path.exists():
                    return OperationResult.failure(
                        command,
                        5,
                        "lock_missing",
                        "lock is missing",
                        stage,
                    )
                if (
                    json.loads(target.read_text()) != compilation.lock.as_dict()
                    or json.loads(metadata_path.read_text()) != metadata
                ):
                    return OperationResult.failure(
                        command,
                        5,
                        "lock_drift",
                        "harness definition changed after lock",
                        stage,
                        next_actions=[
                            f"breadboard harness lock {shlex.quote(portable_ref(source, context.workspace))}"
                        ],
                    )
                graph_hash = str(metadata["graph_hash"])
                return OperationResult.success(
                    command,
                    {
                        "path": portable_ref(target, context.workspace),
                        "graph_hash": graph_hash,
                        "checked": True,
                    },
                    [portable_ref(target, context.workspace)],
                    {"graph": graph_hash},
                    stage=stage,
                )

            _write_lock_pair(
                target,
                metadata_path,
                _json_bytes(compilation.lock.as_dict()),
                _json_bytes(metadata),
            )
            graph_hash = str(metadata["graph_hash"])
            next_action = f"breadboard harness run {shlex.quote(str(source))} --local"
            if target.resolve() != lock_path(source).resolve():
                next_action += f" --lock {shlex.quote(str(target.resolve()))}"
            return OperationResult.success(
                command,
                {
                    "path": portable_ref(target, context.workspace),
                    "graph_hash": graph_hash,
                },
                [portable_ref(target, context.workspace)],
                {
                    "graph": graph_hash,
                    "source": str(metadata["source_sha256"]),
                },
                [next_action],
                stage,
            )
    except Exception as error:
        return from_exception(command, error, stage)


def update_harness(
    request: UpdateHarnessRequest,
    context: OperationContext,
) -> OperationResult:
    command = ["harness", "update"]
    stage = "harness.update"
    temporary: Path | None = None
    try:
        path = context.resolve_path(request.path)
        _validate_output_path(path, context)
        document: Any = request.definition
        if document is None:
            if request.source is None:
                return OperationResult.failure(
                    command,
                    2,
                    "update_input_required",
                    "harness update requires --from or a definition",
                    stage,
                )
            source = context.resolve_path(request.source)
            document = yaml.safe_load(source.read_text())
        if not isinstance(document, dict):
            raise ValueError("harness definition must be a mapping")
        with _MUTATION_LOCK:
            if not path.is_file():
                raise FileNotFoundError(
                    f"harness definition not found: {portable_ref(path, context.workspace)}"
                )
            temporary = path.with_name(f".{path.name}.{os.urandom(8).hex()}.tmp")
            temporary.write_text(yaml.safe_dump(document, sort_keys=False))
            compile_harness_source(temporary, context.workspace, context.contained)
            os.replace(temporary, path)
            return validate_harness(
                ValidateHarnessRequest(request.path),
                context,
                command_name="update",
            )
    except Exception as error:
        return from_exception(command, error, stage)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def list_harnesses(
    request: ListHarnessesRequest,
    context: OperationContext,
) -> OperationResult:
    command = ["harness", "list"]
    stage = "harness.list"
    try:
        root = (
            context.workspace
            if request.directory is None
            else context.resolve_path(request.directory)
        )
        paths = (
            path
            for path in sorted(root.rglob("*.yaml"))
            if _is_harness_path(path, context)
        )
        refs = [portable_ref(path, context.workspace) for path in paths]
        return OperationResult.success(
            command,
            {"harnesses": refs, "count": len(refs)},
            refs,
            stage=stage,
        )
    except Exception as error:
        return from_exception(command, error, stage)


def get_harness(
    request: GetHarnessRequest,
    context: OperationContext,
    *,
    command_name: str = "get",
) -> OperationResult:
    command = ["harness", command_name]
    stage = f"harness.{command_name}"
    try:
        path = context.resolve_path(request.path)
        reference = portable_ref(path, context.workspace)
        return OperationResult.success(
            command,
            {"path": reference, "definition": load_harness_document(path)},
            [reference],
            stage=stage,
        )
    except Exception as error:
        return from_exception(command, error, stage)


def validate_harness(
    request: ValidateHarnessRequest,
    context: OperationContext,
    *,
    command_name: str = "validate",
) -> OperationResult:
    command = ["harness", command_name]
    stage = f"harness.{command_name}"
    path: Path | None = None
    try:
        path = context.resolve_path(request.path)
        reference = portable_ref(path, context.workspace)
        definition = load_harness_definition(path)
        return OperationResult.success(
            command,
            {"path": reference, "schema_version": definition["schema_version"]},
            [reference],
            stage=stage,
        )
    except HarnessDefinitionValidationError as error:
        refs = () if path is None else (portable_ref(path, context.workspace),)
        return OperationResult.failure(
            command,
            EXIT_VALIDATION_FAILURE,
            "invalid_harness",
            str(error),
            stage,
            refs=refs,
        )
    except Exception as error:
        return from_exception(command, error, stage)


def explain_harness(
    request: ExplainHarnessRequest,
    context: OperationContext,
) -> OperationResult:
    command = ["harness", "explain"]
    stage = "harness.explain"
    try:
        path = context.resolve_path(request.path)
        reference = portable_ref(path, context.workspace)
        explanation = compile_harness_source(
            path,
            context.workspace,
            context.contained,
        ).explanation.as_dict()
        explanation["config_path"] = reference
        return OperationResult.success(
            command,
            explanation,
            [reference],
            {"config": str(explanation.get("config_sha256", ""))},
            stage=stage,
        )
    except Exception as error:
        return from_exception(command, error, stage)


def get_harness_lock(
    request: GetHarnessLockRequest,
    context: OperationContext,
) -> OperationResult:
    command = ["harness-lock", "get"]
    stage = "harness-lock.get"
    try:
        path = context.resolve_path(request.path)
        target = path if path.name.endswith(".lock.json") else lock_path(path)
        lock = json.loads(target.read_text())
        reference = portable_ref(target, context.workspace)
        return OperationResult.success(
            command,
            {"path": reference, "lock": lock},
            [reference],
            {"graph": str(lock.get("graph_hash", ""))},
            stage=stage,
        )
    except Exception as error:
        return from_exception(command, error, stage)
