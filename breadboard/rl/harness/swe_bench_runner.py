from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import stat
import subprocess
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast, runtime_checkable

from .contracts import RuntimeClass
from .headless import HeadlessRunRequest
from .runners.base import FrozenJsonObject, freeze_json_object, thaw_json
from .swe_bench_task import (
    DATASET_REPOSITORY,
    DATASET_REVISION,
    DATASET_SHA256,
    DATASET_SIZE_BYTES,
    EVALUATOR_COMMIT,
    EVALUATOR_LICENSE,
    EVALUATOR_SOURCE_URL,
    EVALUATOR_TIMEOUT_SECONDS,
    EVALUATOR_TREE,
    EVALUATOR_VERSION,
    INSTANCE_ID,
    IMAGE_INDEX_DIGEST,
    IMAGE_LEAF_DIGEST,
    IMAGE_PLATFORM,
    IMAGE_REFERENCE,
    PINNED_SYMPY_20590,
    ROW_DIGEST,
    PinnedSweBenchTask,
    SweBenchTaskError,
    official_evaluator_command,
    prediction_jsonl,
    score_official_reports,
    verify_evaluator_installation,
)


TASK_BINDING_SCHEMA_VERSION = "bb.rl.swe-bench-task-binding.v1"
EVALUATOR_BINDING_SCHEMA_VERSION = "bb.rl.swe-bench-evaluator-binding.v1"
COMMAND_SCHEMA_VERSION = "bb.rl.swe-bench-evaluator-command.v1"
EVALUATION_RESULT_SCHEMA_VERSION = "bb.rl.swe-bench-evaluation-result.v1"
RUN_REQUEST_SCHEMA_VERSION = "bb.rl.swe-bench-run-request.v1"
REWARD_RECEIPT_SCHEMA_VERSION = "bb.rl.swe-bench-reward-receipt.v1"
HEADLESS_RESULT_SCHEMA_VERSION = "bb.rl.headless-result.v1"

E4_PROFILE_IDS: tuple[str, ...] = ("Pi", "OMP", "OpenHands", "mini-swe-agent")

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}\Z")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_ADAPTER_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}\Z")
_MAX_PATCH_BYTES = 4 * 1024 * 1024
_MAX_REPORT_BYTES = 16 * 1024 * 1024
_MAX_EVALUATOR_BYTES = 128 * 1024 * 1024
_MAX_RESULT_BYTES = 16 * 1024 * 1024
_RESULT_VALIDATION_TOKEN = object()
_CLEANUP_INVENTORY_FIELDS = (
    "active_lease_ids",
    "orphan_resource_ids",
    "leaked_artifact_ids",
    "cleanup_errors",
    "container_ids",
    "process_ids",
    "cgroup_paths",
    "mount_paths",
    "workspace_paths",
    "artifact_paths",
    "secret_lease_ids",
    "broker_descriptor_count",
)


class SweBenchRunnerError(RuntimeError):
    """Raised when an installed SWE-bench journey cannot be admitted."""


def _digest(value: str, *, field_name: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise SweBenchRunnerError(f"{field_name} must be a full sha256 digest")
    return value


def _safe_id(value: str, *, field_name: str) -> str:
    if type(value) is not str or _SAFE_ID_RE.fullmatch(value) is None:
        raise SweBenchRunnerError(f"{field_name} is not a safe identity")
    return value


def _adapter_id(value: str) -> str:
    if type(value) is not str or _ADAPTER_ID_RE.fullmatch(value) is None:
        raise SweBenchRunnerError("controller adapter_id is not a safe identity")
    return value


def _absolute_path(value: str, *, field_name: str) -> str:
    if (
        type(value) is not str
        or "\x00" in value
        or not os.path.isabs(value)
        or os.path.normpath(value) != value
    ):
        raise SweBenchRunnerError(f"{field_name} must be normalized and absolute")
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _canonical_digest(value: Any) -> str:
    return _digest_bytes(_canonical_bytes(value))


def _frozen_projection(
    value: Mapping[str, Any], *, field_name: str
) -> FrozenJsonObject:
    try:
        return freeze_json_object(
            value,
            field_name=field_name,
            max_encoded_bytes=_MAX_RESULT_BYTES,
        )
    except Exception as exc:
        if isinstance(exc, SweBenchRunnerError):
            raise
        raise SweBenchRunnerError(f"{field_name} is not bounded closed JSON") from exc


@dataclass(frozen=True, slots=True)
class E4ProfileIdentity:
    """The generic E4 controller profile selected for one episode."""

    profile_id: Literal["Pi", "OMP", "OpenHands", "mini-swe-agent"]

    def __post_init__(self) -> None:
        if type(self.profile_id) is not str or self.profile_id not in E4_PROFILE_IDS:
            raise SweBenchRunnerError("unsupported E4 controller profile")

    def identity_dict(self) -> dict[str, str]:
        return {
            "schema_version": "bb.rl.e4-profile-identity.v1",
            "profile_id": self.profile_id,
        }

    @property
    def identity_digest(self) -> str:
        return _canonical_digest(self.identity_dict())


@dataclass(frozen=True, slots=True)
class E4ControllerBinding:
    profile: E4ProfileIdentity
    target_id: str
    adapter_id: str
    implementation_digest: str

    def __post_init__(self) -> None:
        if type(self.profile) is not E4ProfileIdentity:
            raise TypeError("controller profile must be an exact E4ProfileIdentity")
        if (
            type(self.target_id) is not str
            or not self.target_id
            or len(self.target_id) > 256
        ):
            raise SweBenchRunnerError("controller target_id is not a safe identity")
        _adapter_id(self.adapter_id)
        _digest(self.implementation_digest, field_name="controller implementation")

    def identity_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "bb.rl.e4-controller-binding.v1",
            "profile": self.profile.identity_dict(),
            "target_id": self.target_id,
            "adapter_id": self.adapter_id,
            "implementation_digest": self.implementation_digest,
        }

    @property
    def identity_digest(self) -> str:
        return _canonical_digest(self.identity_dict())


@runtime_checkable
class E4ControllerPort(Protocol):
    @property
    def binding(self) -> E4ControllerBinding: ...


def select_e4_controller(
    profile: E4ProfileIdentity,
    controllers: Mapping[str, E4ControllerPort],
) -> E4ControllerPort:
    """Resolve a controller by its declared profile without profile-specific branches."""

    if type(profile) is not E4ProfileIdentity:
        raise TypeError("profile must be an exact E4ProfileIdentity")
    if not isinstance(controllers, Mapping):
        raise TypeError("controllers must be a mapping of profile identities")
    controller = controllers.get(profile.profile_id)
    if controller is None:
        raise SweBenchRunnerError("selected E4 controller profile is not installed")
    try:
        binding = controller.binding
    except Exception as exc:
        raise SweBenchRunnerError(
            "installed E4 controller binding is unavailable"
        ) from exc
    if type(binding) is not E4ControllerBinding or binding.profile != profile:
        raise SweBenchRunnerError("installed E4 controller profile identity mismatch")
    return controller


@dataclass(frozen=True, slots=True)
class SweBenchTaskBinding:
    """Immutable one-row SWE-bench authority used by the installed runner."""

    task: PinnedSweBenchTask = field(default_factory=lambda: PINNED_SYMPY_20590)
    image_digest: str = IMAGE_LEAF_DIGEST

    def __post_init__(self) -> None:
        if type(self.task) is not PinnedSweBenchTask or self.task != PINNED_SYMPY_20590:
            raise SweBenchRunnerError(
                "SWE-bench task authority is not the pinned one-row task"
            )
        if self.image_digest != IMAGE_LEAF_DIGEST:
            raise SweBenchRunnerError(
                "SWE-bench image must use the pinned platform digest"
            )
        _digest(self.image_digest, field_name="SWE-bench image")

    @property
    def instance_id(self) -> str:
        return self.task.instance_id

    @property
    def row_digest(self) -> str:
        return self.task.row_digest

    def identity_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TASK_BINDING_SCHEMA_VERSION,
            "instance_id": self.instance_id,
            "repository": self.task.repository,
            "base_commit": self.task.base_commit,
            "dataset_repository": DATASET_REPOSITORY,
            "dataset_revision": DATASET_REVISION,
            "dataset_relative_path": self.task.dataset_relative_path,
            "dataset_sha256": DATASET_SHA256,
            "dataset_size_bytes": DATASET_SIZE_BYTES,
            "row_digest": self.row_digest,
            "image_reference": IMAGE_REFERENCE,
            "image_platform": IMAGE_PLATFORM,
            "image_index_digest": IMAGE_INDEX_DIGEST,
            "image_leaf_digest": self.image_digest,
            "evaluator_version": EVALUATOR_VERSION,
            "evaluator_commit": EVALUATOR_COMMIT,
        }

    @property
    def identity_digest(self) -> str:
        return _canonical_digest(self.identity_dict())

    def load_verified_row(self, dataset_path: str) -> Mapping[str, Any]:
        try:
            return self.task.load_verified_row(dataset_path)
        except SweBenchTaskError as exc:
            raise SweBenchRunnerError(str(exc)) from exc

    def model_visible_task(self) -> dict[str, str]:
        return self.task.model_visible_task()


PINNED_SWE_BENCH_TASK = SweBenchTaskBinding()


@dataclass(frozen=True, slots=True)
class SweBenchEvaluatorBinding:
    """Exact official SWE-bench evaluator authority."""

    repository: str = "SWE-bench/SWE-bench"
    version: str = EVALUATOR_VERSION
    commit: str = EVALUATOR_COMMIT
    tree: str = EVALUATOR_TREE
    license: str = EVALUATOR_LICENSE

    def __post_init__(self) -> None:
        expected = (
            "SWE-bench/SWE-bench",
            EVALUATOR_VERSION,
            EVALUATOR_COMMIT,
            EVALUATOR_TREE,
            EVALUATOR_LICENSE,
        )
        if (
            self.repository,
            self.version,
            self.commit,
            self.tree,
            self.license,
        ) != expected:
            raise SweBenchRunnerError(
                "official SWE-bench evaluator identity is not pinned"
            )

    def identity_dict(self) -> dict[str, str]:
        return {
            "schema_version": EVALUATOR_BINDING_SCHEMA_VERSION,
            "repository": self.repository,
            "version": self.version,
            "commit": self.commit,
            "tree": self.tree,
            "license": self.license,
            "source_url": EVALUATOR_SOURCE_URL,
        }

    @property
    def identity_digest(self) -> str:
        return _canonical_digest(self.identity_dict())


OFFICIAL_SWE_BENCH_EVALUATOR = SweBenchEvaluatorBinding()


@dataclass(frozen=True, slots=True)
class TrustedEvaluatorCommand:
    """A command whose argv is derived solely from the pinned official evaluator."""

    evaluator: SweBenchEvaluatorBinding
    argv: tuple[str, ...]
    dataset_path: str
    predictions_path: str
    report_directory: str
    run_id: str
    patch_digest: str
    model_name: str
    timeout_seconds: int = EVALUATOR_TIMEOUT_SECONDS

    @classmethod
    def create(
        cls,
        *,
        dataset_path: str,
        predictions_path: str,
        report_directory: str,
        run_id: str,
        model_name: str,
        patch_digest: str,
        timeout_seconds: int = EVALUATOR_TIMEOUT_SECONDS,
    ) -> TrustedEvaluatorCommand:
        _absolute_path(dataset_path, field_name="dataset_path")
        _absolute_path(predictions_path, field_name="predictions_path")
        _absolute_path(report_directory, field_name="report_directory")
        _safe_id(run_id, field_name="run_id")
        _safe_id(model_name, field_name="model_name")
        _digest(patch_digest, field_name="patch")
        argv = official_evaluator_command(
            dataset_path=dataset_path,
            predictions_path=predictions_path,
            report_directory=report_directory,
            run_id=run_id,
            timeout_seconds=timeout_seconds,
        )
        return cls(
            evaluator=OFFICIAL_SWE_BENCH_EVALUATOR,
            argv=argv,
            dataset_path=dataset_path,
            predictions_path=predictions_path,
            report_directory=report_directory,
            run_id=run_id,
            model_name=model_name,
            patch_digest=patch_digest,
            timeout_seconds=timeout_seconds,
        )

    def __post_init__(self) -> None:
        if (
            type(self.evaluator) is not SweBenchEvaluatorBinding
            or self.evaluator != OFFICIAL_SWE_BENCH_EVALUATOR
        ):
            raise SweBenchRunnerError(
                "evaluator command is not bound to the official pinned evaluator"
            )
        object.__setattr__(self, "argv", tuple(self.argv))
        _absolute_path(self.dataset_path, field_name="dataset_path")
        _absolute_path(self.predictions_path, field_name="predictions_path")
        _absolute_path(self.report_directory, field_name="report_directory")
        _safe_id(self.run_id, field_name="run_id")
        _safe_id(self.model_name, field_name="model_name")
        _digest(self.patch_digest, field_name="patch")
        if (
            type(self.timeout_seconds) is not int
            or not 1 <= self.timeout_seconds <= 3_600
        ):
            raise SweBenchRunnerError(
                "evaluator timeout is outside its supported range"
            )
        expected = official_evaluator_command(
            dataset_path=self.dataset_path,
            predictions_path=self.predictions_path,
            report_directory=self.report_directory,
            run_id=self.run_id,
            timeout_seconds=self.timeout_seconds,
        )
        if self.argv != expected:
            raise SweBenchRunnerError(
                "evaluator command is not the pinned official command"
            )

    def identity_dict(self) -> dict[str, Any]:
        return {
            "schema_version": COMMAND_SCHEMA_VERSION,
            "evaluator": self.evaluator.identity_dict(),
            "argv": list(self.argv),
            "dataset_path": self.dataset_path,
            "predictions_path": self.predictions_path,
            "report_directory": self.report_directory,
            "run_id": self.run_id,
            "model_name": self.model_name,
            "patch_digest": self.patch_digest,
            "timeout_seconds": self.timeout_seconds,
        }

    @property
    def command_digest(self) -> str:
        return _canonical_digest(self.identity_dict())


@dataclass(frozen=True, slots=True)
class SweBenchEvaluatorResult:
    """Bound result projection; raw official reports are deliberately not retained."""

    command: TrustedEvaluatorCommand
    aggregate_report_digest: str
    instance_report_digest: str
    reward: float
    _validation_token: object = field(default=None, repr=False, compare=False)
    report_digest: str = field(init=False)
    reward_digest: str = field(init=False)

    @classmethod
    def from_reports(
        cls,
        command: TrustedEvaluatorCommand,
        *,
        aggregate_report: Mapping[str, Any],
        instance_report: Mapping[str, Any],
    ) -> SweBenchEvaluatorResult:
        if type(command) is not TrustedEvaluatorCommand:
            raise TypeError("command must be an exact TrustedEvaluatorCommand")
        aggregate = _frozen_projection(aggregate_report, field_name="aggregate report")
        instance = _frozen_projection(instance_report, field_name="instance report")
        try:
            reward = score_official_reports(
                aggregate_report=cast(Mapping[str, Any], thaw_json(aggregate)),
                instance_report=cast(Mapping[str, Any], thaw_json(instance)),
            )
        except SweBenchTaskError as exc:
            raise SweBenchRunnerError(str(exc)) from exc
        return cls(
            command=command,
            aggregate_report_digest=_canonical_digest(thaw_json(aggregate)),
            instance_report_digest=_canonical_digest(thaw_json(instance)),
            reward=reward,
            _validation_token=_RESULT_VALIDATION_TOKEN,
        )

    def __post_init__(self) -> None:
        if type(self.command) is not TrustedEvaluatorCommand:
            raise TypeError("command must be an exact TrustedEvaluatorCommand")
        if self._validation_token is not _RESULT_VALIDATION_TOKEN:
            raise SweBenchRunnerError(
                "evaluator result lacks an official report validation"
            )
        _digest(self.aggregate_report_digest, field_name="aggregate report")
        _digest(self.instance_report_digest, field_name="instance report")
        report_digest = _canonical_digest(
            {
                "schema_version": EVALUATION_RESULT_SCHEMA_VERSION,
                "aggregate_report_digest": self.aggregate_report_digest,
                "instance_report_digest": self.instance_report_digest,
            }
        )
        reward_digest = _canonical_digest(
            {
                "schema_version": EVALUATION_RESULT_SCHEMA_VERSION,
                "patch_digest": self.command.patch_digest,
                "report_digest": report_digest,
                "reward": self.reward,
            }
        )
        object.__setattr__(self, "report_digest", report_digest)
        object.__setattr__(self, "reward_digest", reward_digest)

    def public_projection(self) -> dict[str, Any]:
        return {
            "schema_version": EVALUATION_RESULT_SCHEMA_VERSION,
            "command_digest": self.command.command_digest,
            "aggregate_report_digest": self.aggregate_report_digest,
            "instance_report_digest": self.instance_report_digest,
            "report_digest": self.report_digest,
            "reward": self.reward,
            "reward_digest": self.reward_digest,
        }


def _read_json_file(path: str) -> Mapping[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        identity = os.fstat(descriptor)
        if (
            not stat.S_ISREG(identity.st_mode)
            or identity.st_nlink != 1
            or identity.st_size > _MAX_REPORT_BYTES
        ):
            raise SweBenchRunnerError("official evaluator report identity is invalid")
        payload = bytearray()
        while len(payload) <= _MAX_REPORT_BYTES:
            chunk = os.read(
                descriptor,
                min(64 * 1024, _MAX_REPORT_BYTES + 1 - len(payload)),
            )
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > _MAX_REPORT_BYTES:
            raise SweBenchRunnerError(
                "official evaluator report exceeds its byte limit"
            )
    except OSError as exc:
        raise SweBenchRunnerError("official evaluator report is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in items:
            if name in result:
                raise SweBenchRunnerError(
                    "official evaluator report has duplicate keys"
                )
            result[name] = value
        return result

    try:
        value = json.loads(bytes(payload), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SweBenchRunnerError("official evaluator report is malformed") from exc
    if not isinstance(value, Mapping):
        raise SweBenchRunnerError("official evaluator report must be an object")
    return value


def _measure_file(path: str, *, max_bytes: int) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        identity = os.fstat(descriptor)
        if (
            not stat.S_ISREG(identity.st_mode)
            or identity.st_nlink != 1
            or identity.st_size > max_bytes
        ):
            raise SweBenchRunnerError(
                "official evaluator executable identity is invalid"
            )
        hasher = hashlib.sha256()
        size = 0
        while size <= max_bytes:
            chunk = os.read(descriptor, min(64 * 1024, max_bytes + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            hasher.update(chunk)
        if size > max_bytes:
            raise SweBenchRunnerError("official evaluator executable is too large")
        return "sha256:" + hasher.hexdigest()
    except OSError as exc:
        raise SweBenchRunnerError(
            "official evaluator executable is unavailable"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


@dataclass(frozen=True, slots=True)
class SubprocessOfficialEvaluator:
    executable_path: str
    executable_digest: str
    installed_version: str
    source_commit: str
    work_directory: str
    binding: SweBenchEvaluatorBinding = field(
        default_factory=lambda: OFFICIAL_SWE_BENCH_EVALUATOR
    )

    def __post_init__(self) -> None:
        _absolute_path(self.executable_path, field_name="evaluator executable")
        _absolute_path(self.work_directory, field_name="evaluator work directory")
        _digest(self.executable_digest, field_name="evaluator executable")
        verify_evaluator_installation(
            installed_version=self.installed_version,
            source_commit=self.source_commit,
        )
        if (
            type(self.binding) is not SweBenchEvaluatorBinding
            or self.binding != OFFICIAL_SWE_BENCH_EVALUATOR
        ):
            raise SweBenchRunnerError("evaluator adapter is not the official binding")
        if (
            _measure_file(
                self.executable_path,
                max_bytes=_MAX_EVALUATOR_BYTES,
            )
            != self.executable_digest
        ):
            raise SweBenchRunnerError("official evaluator executable digest mismatch")
        try:
            work = os.stat(self.work_directory, follow_symlinks=False)
        except OSError as exc:
            raise SweBenchRunnerError(
                "official evaluator work directory is unavailable"
            ) from exc
        if not stat.S_ISDIR(work.st_mode):
            raise SweBenchRunnerError(
                "official evaluator work directory is not a directory"
            )

    def identity_dict(self) -> dict[str, Any]:
        return self.binding.identity_dict() | {
            "executable_digest": self.executable_digest,
        }

    def evaluate(self, command: TrustedEvaluatorCommand) -> SweBenchEvaluatorResult:
        if type(command) is not TrustedEvaluatorCommand:
            raise TypeError("command must be an exact TrustedEvaluatorCommand")
        if (
            _measure_file(
                self.executable_path,
                max_bytes=_MAX_EVALUATOR_BYTES,
            )
            != self.executable_digest
        ):
            raise SweBenchRunnerError("official evaluator executable changed")
        run_work_directory = os.path.join(self.work_directory, command.run_id)
        try:
            os.mkdir(run_work_directory, 0o700)
        except OSError as exc:
            raise SweBenchRunnerError(
                "official evaluator run directory must be new"
            ) from exc
        try:
            os.mkdir(command.report_directory, 0o700)
        except OSError as exc:
            raise SweBenchRunnerError(
                "official evaluator report directory must be new"
            ) from exc
        environment = {
            "HOME": run_work_directory,
            "PATH": os.pathsep.join(
                (
                    os.path.dirname(self.executable_path),
                    "/usr/local/bin",
                    "/usr/bin",
                    "/bin",
                )
            ),
            "PYTHONUNBUFFERED": "1",
        }
        try:
            completed = subprocess.run(
                (self.executable_path, *command.argv[1:]),
                cwd=run_work_directory,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=command.timeout_seconds + 60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SweBenchRunnerError("official SWE-bench evaluator failed") from exc
        if completed.returncode != 0:
            raise SweBenchRunnerError("official SWE-bench evaluator returned nonzero")
        aggregate_path = os.path.join(
            command.report_directory,
            f"{command.model_name}.{command.run_id}.json",
        )
        instance_path = os.path.join(
            run_work_directory,
            "logs",
            "run_evaluation",
            command.run_id,
            command.model_name,
            INSTANCE_ID,
            "report.json",
        )
        return SweBenchEvaluatorResult.from_reports(
            command,
            aggregate_report=_read_json_file(aggregate_path),
            instance_report=_read_json_file(instance_path),
        )


@runtime_checkable
class InstalledHeadlessRunner(Protocol):
    def run(
        self,
        request: HeadlessRunRequest,
    ) -> Mapping[str, Any] | Awaitable[Mapping[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class InstalledSweBenchRequest:
    """One installed-package journey; repository source checkout is intentionally absent."""

    profile: E4ProfileIdentity
    headless_request: HeadlessRunRequest
    dataset_path: str
    predictions_path: str
    report_directory: str
    run_id: str
    task_binding: SweBenchTaskBinding = field(
        default_factory=lambda: PINNED_SWE_BENCH_TASK
    )
    evaluator_binding: SweBenchEvaluatorBinding = field(
        default_factory=lambda: OFFICIAL_SWE_BENCH_EVALUATOR
    )

    def __post_init__(self) -> None:
        if type(self.profile) is not E4ProfileIdentity:
            raise TypeError("profile must be an exact E4ProfileIdentity")
        if type(self.headless_request) is not HeadlessRunRequest:
            raise TypeError("headless_request must be an exact HeadlessRunRequest")
        if type(self.task_binding) is not SweBenchTaskBinding:
            raise TypeError("task_binding must be an exact SweBenchTaskBinding")
        if type(self.evaluator_binding) is not SweBenchEvaluatorBinding:
            raise TypeError(
                "evaluator_binding must be an exact SweBenchEvaluatorBinding"
            )
        if self.evaluator_binding != OFFICIAL_SWE_BENCH_EVALUATOR:
            raise SweBenchRunnerError(
                "request evaluator is not the pinned official evaluator"
            )
        for value, name in (
            (self.dataset_path, "dataset_path"),
            (self.predictions_path, "predictions_path"),
            (self.report_directory, "report_directory"),
        ):
            _absolute_path(value, field_name=name)
        _safe_id(self.run_id, field_name="run_id")
        if len({self.dataset_path, self.predictions_path}) != 2:
            raise SweBenchRunnerError("dataset and prediction paths must differ")
        workspace = self.headless_request.workspace
        if workspace.task_image_digest != self.task_binding.image_digest:
            raise SweBenchRunnerError(
                "headless workspace image is not the pinned SWE-bench image"
            )
        if (
            workspace.repository_snapshot_digest is not None
            or workspace.base_commit is not None
        ):
            raise SweBenchRunnerError(
                "installed SWE-bench journey must not use a source checkout"
            )
        if self.headless_request.resolve_request.episode_id != self.run_id:
            raise SweBenchRunnerError(
                "run_id must equal the canonical headless episode identity"
            )
        if self.headless_request.patch_path is None:
            raise SweBenchRunnerError(
                "installed SWE-bench journey requires canonical patch export"
            )

    def public_projection(self) -> dict[str, Any]:
        return {
            "schema_version": RUN_REQUEST_SCHEMA_VERSION,
            "profile": self.profile.identity_dict(),
            "episode_id": self.headless_request.resolve_request.episode_id,
            "target_id": self.headless_request.target_id,
            "target_overlay_id": self.headless_request.target_overlay_id,
            "dataset_path": self.dataset_path,
            "predictions_path": self.predictions_path,
            "report_directory": self.report_directory,
            "run_id": self.run_id,
            "task_binding_digest": self.task_binding.identity_digest,
            "evaluator_binding_digest": self.evaluator_binding.identity_digest,
        }

    @property
    def request_digest(self) -> str:
        return _canonical_digest(self.public_projection())


@dataclass(frozen=True, slots=True)
class InstalledSweBenchRewardReceipt:
    """Versioned public receipt with only digest-bound, non-secret projections."""

    run_id: str
    episode_id: str
    controller_identity: Mapping[str, Any]
    evaluator_identity: Mapping[str, Any]
    task_binding_digest: str
    dataset_artifact_digest: str
    dataset_row_digest: str
    prediction_digest: str
    patch_digest: str
    image_index_digest: str
    image_leaf_digest: str
    command_digest: str
    aggregate_report_digest: str
    instance_report_digest: str
    report_digest: str
    reward: float
    reward_digest: str
    cleanup_digest: str
    cleanup_inventory_digest: str
    headless_result_digest: str
    receipt_digest: str = field(init=False)
    schema_version: Literal["bb.rl.swe-bench-reward-receipt.v1"] = (
        REWARD_RECEIPT_SCHEMA_VERSION
    )

    def __post_init__(self) -> None:
        _safe_id(self.run_id, field_name="run_id")
        _safe_id(self.episode_id, field_name="episode_id")
        if not isinstance(self.controller_identity, Mapping) or not isinstance(
            self.evaluator_identity, Mapping
        ):
            raise SweBenchRunnerError("receipt identities must be mappings")
        object.__setattr__(
            self,
            "controller_identity",
            _frozen_projection(
                self.controller_identity, field_name="controller identity"
            ),
        )
        object.__setattr__(
            self,
            "evaluator_identity",
            _frozen_projection(
                self.evaluator_identity, field_name="evaluator identity"
            ),
        )
        for value, name in (
            (self.task_binding_digest, "task binding"),
            (self.dataset_artifact_digest, "dataset artifact"),
            (self.dataset_row_digest, "dataset row"),
            (self.prediction_digest, "prediction"),
            (self.patch_digest, "patch"),
            (self.image_index_digest, "image index"),
            (self.image_leaf_digest, "image leaf"),
            (self.command_digest, "evaluator command"),
            (self.aggregate_report_digest, "aggregate report"),
            (self.instance_report_digest, "instance report"),
            (self.report_digest, "report"),
            (self.reward_digest, "reward"),
            (self.cleanup_digest, "cleanup"),
            (self.cleanup_inventory_digest, "cleanup inventory"),
            (self.headless_result_digest, "headless result"),
        ):
            _digest(value, field_name=name)
        if self.dataset_artifact_digest != "sha256:" + DATASET_SHA256:
            raise SweBenchRunnerError(
                "receipt dataset artifact is not the pinned dataset"
            )
        if self.dataset_row_digest != ROW_DIGEST:
            raise SweBenchRunnerError("receipt dataset row is not the pinned row")
        if (
            self.image_index_digest != IMAGE_INDEX_DIGEST
            or self.image_leaf_digest != IMAGE_LEAF_DIGEST
        ):
            raise SweBenchRunnerError("receipt image authority is not pinned")
        if type(self.reward) is not float or self.reward not in {0.0, 1.0}:
            raise SweBenchRunnerError("receipt reward must be exactly 0.0 or 1.0")
        object.__setattr__(
            self, "receipt_digest", _canonical_digest(self.public_projection())
        )

    def public_projection(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "episode_id": self.episode_id,
            "controller_identity": thaw_json(self.controller_identity),
            "evaluator_identity": thaw_json(self.evaluator_identity),
            "task_binding_digest": self.task_binding_digest,
            "dataset_artifact_digest": self.dataset_artifact_digest,
            "dataset_row_digest": self.dataset_row_digest,
            "prediction_digest": self.prediction_digest,
            "patch_digest": self.patch_digest,
            "image_index_digest": self.image_index_digest,
            "image_leaf_digest": self.image_leaf_digest,
            "command_digest": self.command_digest,
            "aggregate_report_digest": self.aggregate_report_digest,
            "instance_report_digest": self.instance_report_digest,
            "report_digest": self.report_digest,
            "reward": self.reward,
            "reward_digest": self.reward_digest,
            "cleanup_digest": self.cleanup_digest,
            "cleanup_inventory_digest": self.cleanup_inventory_digest,
            "headless_result_digest": self.headless_result_digest,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.public_projection())

    def canonical_digest(self) -> str:
        return self.receipt_digest

    def to_public_dict(self) -> dict[str, Any]:
        return self.public_projection() | {"receipt_digest": self.receipt_digest}


def _validate_headless_result(
    result: Mapping[str, Any],
    request: InstalledSweBenchRequest,
) -> tuple[FrozenJsonObject, str, str, str, str]:
    frozen = _frozen_projection(result, field_name="headless result")
    payload = cast(Mapping[str, Any], thaw_json(frozen))
    if payload.get("schema_version") != HEADLESS_RESULT_SCHEMA_VERSION:
        raise SweBenchRunnerError("headless result schema is not canonical")
    if payload.get("episode_id") != request.headless_request.resolve_request.episode_id:
        raise SweBenchRunnerError("headless result episode identity mismatch")
    terminal = payload.get("terminal")
    if not isinstance(terminal, Mapping) or terminal.get("status") != "succeeded":
        raise SweBenchRunnerError(
            "headless result did not produce a successful terminal outcome"
        )
    sandbox = payload.get("sandbox_identity")
    if not isinstance(sandbox, Mapping):
        raise SweBenchRunnerError(
            "headless result is missing canonical sandbox identity"
        )
    if sandbox.get("image_digest") != request.task_binding.image_digest:
        raise SweBenchRunnerError(
            "headless result image is not the pinned SWE-bench image"
        )
    if sandbox.get("runtime_class") not in {
        RuntimeClass.HARDENED_DOCKER.value,
        RuntimeClass.HARDENED_GVISOR.value,
    }:
        raise SweBenchRunnerError("headless result does not prove an isolated runtime")
    cleanup = payload.get("cleanup")
    if not isinstance(cleanup, Mapping) or cleanup.get("disposition") != "released":
        raise SweBenchRunnerError("headless cleanup was not authoritatively released")
    cleanup_digest = cleanup.get("receipt_digest")
    if type(cleanup_digest) is not str or _DIGEST_RE.fullmatch(cleanup_digest) is None:
        raise SweBenchRunnerError("headless cleanup receipt digest is missing")
    if not isinstance(cleanup.get("receipt"), Mapping):
        raise SweBenchRunnerError("headless cleanup receipt is missing")
    inventory = payload.get("cleanup_inventory")
    inventory_digest = payload.get("cleanup_inventory_digest")
    if not isinstance(inventory, Mapping) or type(inventory_digest) is not str:
        raise SweBenchRunnerError("headless cleanup inventory is missing")
    _digest(inventory_digest, field_name="cleanup inventory")
    expected_inventory_keys = set(_CLEANUP_INVENTORY_FIELDS) | {
        "broker_close_receipt_ref"
    }
    if set(inventory) != expected_inventory_keys:
        raise SweBenchRunnerError("headless cleanup inventory schema is incomplete")
    if _canonical_digest(inventory) != inventory_digest:
        raise SweBenchRunnerError("headless cleanup inventory digest mismatch")
    if any(inventory[name] not in ([], 0) for name in _CLEANUP_INVENTORY_FIELDS):
        raise SweBenchRunnerError("headless cleanup inventory is not empty")
    patch = payload.get("patch")
    evidence = payload.get("workspace_evidence")
    requested_path = request.headless_request.patch_path
    if (
        not isinstance(patch, Mapping)
        or not isinstance(evidence, Mapping)
        or requested_path is None
        or patch.get("requested") is not True
        or patch.get("available") is not True
        or patch.get("destination") != requested_path
        or patch.get("digest") != evidence.get("patch_digest")
    ):
        raise SweBenchRunnerError(
            "headless result does not bind the canonical workspace patch"
        )
    patch_digest = patch.get("digest")
    if type(patch_digest) is not str:
        raise SweBenchRunnerError("headless workspace patch digest is missing")
    _digest(patch_digest, field_name="workspace patch")
    return (
        frozen,
        _canonical_digest(payload),
        cleanup_digest,
        inventory_digest,
        patch_digest,
    )


def _write_prediction(path: str, payload: bytes) -> None:
    parent = os.path.dirname(path)
    try:
        parent_stat = os.stat(parent, follow_symlinks=False)
    except OSError as exc:
        raise SweBenchRunnerError("prediction parent directory is unavailable") from exc
    if not stat.S_ISDIR(parent_stat.st_mode):
        raise SweBenchRunnerError("prediction parent is not a directory")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(path, flags, 0o600)
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise SweBenchRunnerError("prediction artifact write made no progress")
            written += count
        os.fsync(descriptor)
        identity = os.fstat(descriptor)
        if (
            not stat.S_ISREG(identity.st_mode)
            or identity.st_nlink != 1
            or identity.st_size != len(payload)
        ):
            raise SweBenchRunnerError("prediction artifact identity is not private")
    except FileExistsError as exc:
        raise SweBenchRunnerError("prediction artifact already exists") from exc
    except OSError as exc:
        raise SweBenchRunnerError("prediction artifact could not be written") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_patch(path: str, expected_digest: str) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        identity = os.fstat(descriptor)
        if (
            not stat.S_ISREG(identity.st_mode)
            or identity.st_nlink != 1
            or identity.st_size > _MAX_PATCH_BYTES
        ):
            raise SweBenchRunnerError("canonical workspace patch identity is invalid")
        payload = bytearray()
        while len(payload) <= _MAX_PATCH_BYTES:
            chunk = os.read(
                descriptor, min(64 * 1024, _MAX_PATCH_BYTES + 1 - len(payload))
            )
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > _MAX_PATCH_BYTES:
            raise SweBenchRunnerError(
                "canonical workspace patch exceeds its byte limit"
            )
    except OSError as exc:
        raise SweBenchRunnerError("canonical workspace patch is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    patch_bytes = bytes(payload)
    if _digest_bytes(patch_bytes) != expected_digest:
        raise SweBenchRunnerError("canonical workspace patch digest mismatch")
    try:
        return patch_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SweBenchRunnerError("canonical workspace patch is not UTF-8") from exc


async def run_installed_swe_bench(
    request: InstalledSweBenchRequest,
    *,
    controllers: Mapping[str, E4ControllerPort],
    headless: InstalledHeadlessRunner,
    evaluator: SubprocessOfficialEvaluator,
) -> InstalledSweBenchRewardReceipt:
    """Run one row through canonical headless execution and the pinned evaluator only."""

    if type(request) is not InstalledSweBenchRequest:
        raise TypeError("request must be an exact InstalledSweBenchRequest")
    controller = select_e4_controller(request.profile, controllers)
    if type(evaluator) is not SubprocessOfficialEvaluator:
        raise TypeError("evaluator must be an exact SubprocessOfficialEvaluator")
    if (
        evaluator.binding != request.evaluator_binding
        or evaluator.binding != OFFICIAL_SWE_BENCH_EVALUATOR
    ):
        raise SweBenchRunnerError(
            "evaluator adapter is not the pinned official evaluator"
        )
    if not isinstance(headless, InstalledHeadlessRunner):
        raise TypeError("headless must implement InstalledHeadlessRunner")

    try:
        raw_headless = headless.run(request.headless_request)
        headless_result = (
            await raw_headless if inspect.isawaitable(raw_headless) else raw_headless
        )
    except Exception as exc:
        raise SweBenchRunnerError("canonical headless execution failed") from exc
    if not isinstance(headless_result, Mapping):
        raise SweBenchRunnerError("canonical headless result must be a mapping")
    (
        frozen_headless,
        headless_digest,
        cleanup_digest,
        cleanup_inventory_digest,
        patch_digest,
    ) = _validate_headless_result(headless_result, request)
    if controller.binding.target_id != request.headless_request.target_id:
        raise SweBenchRunnerError(
            "selected E4 controller target does not match the headless request"
        )

    request.task_binding.load_verified_row(request.dataset_path)
    patch_path = request.headless_request.patch_path
    if patch_path is None:
        raise SweBenchRunnerError("canonical workspace patch path is missing")
    patch = _read_patch(patch_path, patch_digest)
    try:
        prediction = prediction_jsonl(
            patch,
            model_name=controller.binding.adapter_id,
        )
    except (SweBenchTaskError, TypeError) as exc:
        raise SweBenchRunnerError(str(exc)) from exc
    prediction_digest = _digest_bytes(prediction)
    _write_prediction(request.predictions_path, prediction)

    command = TrustedEvaluatorCommand.create(
        dataset_path=request.dataset_path,
        predictions_path=request.predictions_path,
        report_directory=request.report_directory,
        run_id=request.run_id,
        model_name=controller.binding.adapter_id,
        patch_digest=patch_digest,
    )
    try:
        evaluation = evaluator.evaluate(command)
        evaluation = await evaluation if inspect.isawaitable(evaluation) else evaluation
    except Exception as exc:
        raise SweBenchRunnerError("pinned official SWE-bench evaluator failed") from exc
    if type(evaluation) is not SweBenchEvaluatorResult:
        raise SweBenchRunnerError("evaluator adapter returned an unbound result")
    if evaluation.command != command or evaluation.command.patch_digest != patch_digest:
        raise SweBenchRunnerError("evaluator command/result binding mismatch")

    return InstalledSweBenchRewardReceipt(
        run_id=request.run_id,
        episode_id=request.headless_request.resolve_request.episode_id,
        controller_identity=controller.binding.identity_dict(),
        evaluator_identity=evaluator.identity_dict(),
        task_binding_digest=request.task_binding.identity_digest,
        dataset_artifact_digest="sha256:" + DATASET_SHA256,
        dataset_row_digest=request.task_binding.row_digest,
        prediction_digest=prediction_digest,
        patch_digest=patch_digest,
        image_index_digest=IMAGE_INDEX_DIGEST,
        image_leaf_digest=IMAGE_LEAF_DIGEST,
        command_digest=command.command_digest,
        aggregate_report_digest=evaluation.aggregate_report_digest,
        instance_report_digest=evaluation.instance_report_digest,
        report_digest=evaluation.report_digest,
        reward=evaluation.reward,
        reward_digest=evaluation.reward_digest,
        cleanup_digest=cleanup_digest,
        cleanup_inventory_digest=cleanup_inventory_digest,
        headless_result_digest=headless_digest,
    )


__all__ = [
    "COMMAND_SCHEMA_VERSION",
    "E4ControllerBinding",
    "E4ControllerPort",
    "E4ProfileIdentity",
    "E4_PROFILE_IDS",
    "EVALUATION_RESULT_SCHEMA_VERSION",
    "EVALUATOR_BINDING_SCHEMA_VERSION",
    "HEADLESS_RESULT_SCHEMA_VERSION",
    "InstalledHeadlessRunner",
    "InstalledSweBenchRequest",
    "InstalledSweBenchRewardReceipt",
    "OFFICIAL_SWE_BENCH_EVALUATOR",
    "PINNED_SWE_BENCH_TASK",
    "REWARD_RECEIPT_SCHEMA_VERSION",
    "RUN_REQUEST_SCHEMA_VERSION",
    "SweBenchEvaluatorBinding",
    "SweBenchEvaluatorResult",
    "SweBenchRunnerError",
    "SweBenchTaskBinding",
    "SubprocessOfficialEvaluator",
    "TrustedEvaluatorCommand",
    "select_e4_controller",
    "run_installed_swe_bench",
]
