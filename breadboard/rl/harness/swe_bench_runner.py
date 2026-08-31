from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, cast

from .contracts import RuntimeClass
from .headless import (
    HeadlessProviderRouteAuthority,
    HeadlessRunRequest,
    run_headless_request,
)
from .runners.base import FrozenJsonObject, freeze_json_object, thaw_json
from .policy_provider import E4TargetPolicyProjection
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
_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_IMMUTABLE_IMAGE_REFERENCE = (
    IMAGE_REFERENCE.rsplit(":", 1)[0] + "@" + IMAGE_LEAF_DIGEST
)
_MAX_PATCH_BYTES = 4 * 1024 * 1024
_MAX_REPORT_BYTES = 16 * 1024 * 1024
_MAX_EVALUATOR_BYTES = 128 * 1024 * 1024
_MAX_RESULT_BYTES = 16 * 1024 * 1024
_MAX_COMPOSITION_REF_BYTES = 1024 * 1024
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


def _run_id(value: str) -> str:
    if type(value) is not str or _RUN_ID_RE.fullmatch(value) is None:
        raise SweBenchRunnerError("run_id is not a safe evaluator cleanup identity")
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

def _regular_file_digest(path: str, *, field_name: str, max_bytes: int) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > max_bytes
        ):
            raise SweBenchRunnerError(f"{field_name} identity is invalid")
        payload = bytearray()
        while len(payload) <= max_bytes:
            chunk = os.read(descriptor, min(64 * 1024, max_bytes + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if (
            len(payload) > max_bytes
            or len(payload) != before.st_size
            or (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
        ):
            raise SweBenchRunnerError(f"{field_name} changed during measurement")
        return _digest_bytes(bytes(payload))
    except OSError as exc:
        raise SweBenchRunnerError(f"{field_name} is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


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


def _controller_identity(
    profile: E4ProfileIdentity,
    target: E4TargetPolicyProjection,
) -> dict[str, Any]:
    target_family, separator, target_version = target.target_id.partition("@")
    expected_profile = {
        "pi": "Pi",
        "oh-my-pi": "OMP",
        "openhands": "OpenHands",
        "mini-swe-agent": "mini-swe-agent",
    }.get(target_family)
    if (
        separator != "@"
        or not target_version
        or expected_profile is None
        or profile.profile_id != expected_profile
    ):
        raise SweBenchRunnerError(
            "E4 controller profile does not match the packaged target"
        )
    target_identity = target.identity_dict()
    return {
        "schema_version": "bb.rl.e4-controller-identity.v1",
        "profile": profile.identity_dict(),
        "target": target_identity,
        "implementation_digest": _canonical_digest(target_identity),
    }


def _controller_model_name(controller_identity: Mapping[str, Any]) -> str:
    return "breadboard-e4-" + _canonical_digest(controller_identity)[7:31]


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
        _run_id(run_id)
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
        _run_id(self.run_id)
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
        instance_envelope = _frozen_projection(
            instance_report, field_name="instance report"
        )
        thawed_instance_envelope = cast(
            Mapping[str, Any], thaw_json(instance_envelope)
        )
        if set(thawed_instance_envelope) != {INSTANCE_ID}:
            raise SweBenchRunnerError(
                "official instance report envelope does not bind the pinned task"
            )
        inner_instance = thawed_instance_envelope[INSTANCE_ID]
        if not isinstance(inner_instance, Mapping):
            raise SweBenchRunnerError(
                "official instance report envelope is malformed"
            )
        try:
            reward = score_official_reports(
                aggregate_report=cast(Mapping[str, Any], thaw_json(aggregate)),
                instance_report=inner_instance,
            )
        except SweBenchTaskError as exc:
            raise SweBenchRunnerError(str(exc)) from exc
        return cls(
            command=command,
            aggregate_report_digest=_canonical_digest(thaw_json(aggregate)),
            instance_report_digest=_canonical_digest(
                thaw_json(instance_envelope)
            ),
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
            or identity.st_uid != 0
            or identity.st_nlink != 1
            or identity.st_mode & 0o022
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


OFFICIAL_EVALUATOR_ENVIRONMENT_DIGEST = "sha256:84fcca1fc6aa1cf93b44ae2e78792894470455a5f1a6cff27265623586415a9d"
OFFICIAL_EVALUATOR_PYTHON_DIGEST = "sha256:a15ccdaf07655a8667a3687b13171486c9a265d92273ba3ad820fbb64d2cfecc"
OFFICIAL_EVALUATOR_DOCKER_DIGEST = "sha256:6435ff9214bf8e0931078fb0980809728cac0a54a526d4f28a26d3e48132b58d"
_MAX_ENVIRONMENT_FILES = 50_000
_MAX_ENVIRONMENT_BYTES = 4 * 1024 * 1024 * 1024
_EVALUATOR_ENVIRONMENT_ASSERTION = (
    "root=os.path.realpath(sys.argv.pop(1));"
    "import swebench;"
    "valid=os.path.realpath(sys.prefix)==root and "
    "os.path.commonpath((os.path.realpath(swebench.__file__),root))==root;"
    "valid or sys.exit(86);"
)



def _require_root_owned_immutable_directory(path: str) -> None:
    current = os.path.abspath(path)
    while True:
        try:
            identity = os.stat(current, follow_symlinks=False)
        except OSError as exc:
            raise SweBenchRunnerError(
                "evaluator authority directory is unavailable"
            ) from exc
        if (
            not stat.S_ISDIR(identity.st_mode)
            or identity.st_uid != 0
            or identity.st_mode & 0o022
        ):
            raise SweBenchRunnerError(
                "evaluator authority directory is not root-owned and immutable: "
                + current
            )
        parent = os.path.dirname(current)
        if parent == current:
            return
        current = parent

def _require_root_private_work_directory(path: str) -> None:
    if os.geteuid() != 0:
        raise SweBenchRunnerError(
            "official evaluator requires the root-owned host custody boundary"
        )
    _require_root_owned_immutable_directory(os.path.dirname(path))
    try:
        identity = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise SweBenchRunnerError(
            "official evaluator work directory is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(identity.st_mode)
        or stat.S_IMODE(identity.st_mode) != 0o700
        or identity.st_uid != 0
    ):
        raise SweBenchRunnerError(
            "official evaluator work directory is not root-owned and private"
        )



def _measure_immutable_tree(
    tree_root: str,
    *,
    projection_root: str,
) -> tuple[list[dict[str, Any]], int]:
    _require_root_owned_immutable_directory(tree_root)
    entries: list[dict[str, Any]] = []
    total_bytes = 0
    for directory, names, files in os.walk(tree_root, followlinks=False):
        directory_identity = os.stat(directory, follow_symlinks=False)
        if (
            not stat.S_ISDIR(directory_identity.st_mode)
            or directory_identity.st_uid != 0
            or directory_identity.st_mode & 0o022
        ):
            raise SweBenchRunnerError(
                "evaluator environment contains a mutable directory"
            )
        if "__pycache__" in names:
            raise SweBenchRunnerError(
                "evaluator environment contains executable bytecode caches"
            )
        names[:] = sorted(names)
        for name in names:
            path = os.path.join(directory, name)
            identity = os.stat(path, follow_symlinks=False)
            if (
                not stat.S_ISDIR(identity.st_mode)
                or identity.st_uid != 0
                or identity.st_mode & 0o022
            ):
                raise SweBenchRunnerError(
                    "evaluator environment contains a mutable directory"
                )
        for name in sorted(files):
            if name.endswith((".pyc", ".pyo")):
                raise SweBenchRunnerError(
                    "evaluator environment contains executable bytecode"
                )
            path = os.path.join(directory, name)
            identity = os.stat(path, follow_symlinks=False)
            if (
                not stat.S_ISREG(identity.st_mode)
                or identity.st_uid != 0
                or identity.st_nlink != 1
                or identity.st_mode & 0o022
                or identity.st_size > _MAX_EVALUATOR_BYTES
            ):
                raise SweBenchRunnerError(
                    "evaluator environment contains an invalid file"
                )
            total_bytes += identity.st_size
            if (
                len(entries) >= _MAX_ENVIRONMENT_FILES
                or total_bytes > _MAX_ENVIRONMENT_BYTES
            ):
                raise SweBenchRunnerError("evaluator environment exceeds its bounds")
            entries.append(
                {
                    "path": os.path.relpath(path, projection_root),
                    "size_bytes": identity.st_size,
                    "executable": bool(identity.st_mode & 0o111),
                    "digest": _measure_file(
                        path,
                        max_bytes=_MAX_EVALUATOR_BYTES,
                    ),
                }
            )
    if not entries:
        raise SweBenchRunnerError("evaluator environment authority tree is empty")
    return entries, total_bytes


def measure_official_evaluator_environment(root: str) -> dict[str, Any]:
    """Measure the complete locked evaluator package tree and host executables."""

    _absolute_path(root, field_name="evaluator environment root")
    _require_root_owned_immutable_directory(root)
    library_root = os.path.join(root, "lib")
    _require_root_owned_immutable_directory(library_root)
    try:
        python_directories = sorted(
            name
            for name in os.listdir(library_root)
            if name == "python3.11"
            and os.path.isdir(os.path.join(library_root, name))
        )
    except OSError as exc:
        raise SweBenchRunnerError("evaluator environment library is unavailable") from exc
    if python_directories != ["python3.11"]:
        raise SweBenchRunnerError("evaluator environment Python ABI is not pinned")
    site_packages = os.path.join(
        library_root,
        python_directories[0],
        "site-packages",
    )
    runtime_library = os.path.join(root, "runtime", "lib")
    site_entries, site_bytes = _measure_immutable_tree(
        site_packages,
        projection_root=root,
    )
    runtime_entries, runtime_bytes = _measure_immutable_tree(
        runtime_library,
        projection_root=root,
    )
    entries = site_entries + runtime_entries
    total_bytes = site_bytes + runtime_bytes
    if (
        len(entries) > _MAX_ENVIRONMENT_FILES
        or total_bytes > _MAX_ENVIRONMENT_BYTES
    ):
        raise SweBenchRunnerError("evaluator environment exceeds its bounds")
    distribution_root = os.path.join(
        site_packages,
        "swebench-5.0.1.dist-info",
    )
    distribution_direct_url = _read_json_file(
        os.path.join(distribution_root, "direct_url.json")
    )
    metadata_path = os.path.join(distribution_root, "METADATA")
    _measure_file(metadata_path, max_bytes=_MAX_REPORT_BYTES)
    with open(metadata_path, "rb") as source:
        distribution_metadata = source.read(_MAX_REPORT_BYTES + 1)
    expected_direct_url = {
        "url": "https://github.com/SWE-bench/SWE-bench.git",
        "vcs_info": {
            "vcs": "git",
            "commit_id": EVALUATOR_COMMIT,
            "requested_revision": EVALUATOR_COMMIT,
        },
    }
    if distribution_direct_url != expected_direct_url:
        raise SweBenchRunnerError("evaluator source commit is not pinned")
    if (
        distribution_metadata is None
        or len(distribution_metadata) > _MAX_REPORT_BYTES
        or b"Name: swebench\n" not in distribution_metadata
        or f"Version: {EVALUATOR_VERSION}\n".encode() not in distribution_metadata
    ):
        raise SweBenchRunnerError("evaluator distribution metadata is not pinned")
    python_path = os.path.join(root, "bin", "python")
    try:
        python_launcher = os.lstat(python_path)
        python_target = os.readlink(python_path)
    except OSError as exc:
        raise SweBenchRunnerError(
            "official evaluator Python launcher is unavailable"
        ) from exc
    if (
        not stat.S_ISLNK(python_launcher.st_mode)
        or python_launcher.st_uid != 0
        or python_target != "../runtime/bin/python3.11"
    ):
        raise SweBenchRunnerError(
            "official evaluator Python launcher is not pinned"
        )
    resolved_python_path = os.path.realpath(python_path)
    expected_python_path = os.path.join(root, "runtime", "bin", "python3.11")
    if resolved_python_path != expected_python_path:
        raise SweBenchRunnerError(
            "official evaluator Python launcher target is not pinned"
        )
    pyvenv_path = os.path.join(root, "pyvenv.cfg")
    pyvenv_digest = _measure_file(
        pyvenv_path,
        max_bytes=_MAX_REPORT_BYTES,
    )
    docker_path = os.path.realpath("/usr/bin/docker")
    _require_root_owned_immutable_directory(os.path.dirname(resolved_python_path))
    _require_root_owned_immutable_directory(os.path.dirname(docker_path))
    python_digest = _measure_file(
        resolved_python_path,
        max_bytes=_MAX_EVALUATOR_BYTES,
    )
    docker_digest = _measure_file(
        docker_path,
        max_bytes=_MAX_EVALUATOR_BYTES,
    )
    environment_projection = {
        "schema_version": "bb.rl.swe-bench-evaluator-environment.v1",
        "platform": "linux/amd64",
        "python_abi": "cp311",
        "python_launcher": {
            "path": "bin/python",
            "target": python_target,
            "pyvenv_cfg_digest": pyvenv_digest,
        },
        "evaluator_commit": EVALUATOR_COMMIT,
        "evaluator_tree": EVALUATOR_TREE,
        "files": sorted(entries, key=lambda item: item["path"]),
    }
    return {
        "environment_digest": _canonical_digest(environment_projection),
        "python_path": python_path,
        "python_digest": python_digest,
        "docker_path": docker_path,
        "docker_digest": docker_digest,
        "file_count": len(entries),
        "total_bytes": total_bytes,
    }


def _process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError as exc:
        raise SweBenchRunnerError(
            "evaluator process-group authority is unavailable"
        ) from exc
    return True


def _terminate_process_group(process_group: int) -> None:
    if not _process_group_exists(process_group):
        return
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not _process_group_exists(process_group):
            return
        time.sleep(0.02)
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not _process_group_exists(process_group):
            return
        time.sleep(0.02)
    raise SweBenchRunnerError("evaluator process group survived termination")


def _run_evaluator_process(
    argv: tuple[str, ...],
    *,
    cwd: str,
    environment: Mapping[str, str],
    timeout_seconds: int,
) -> None:
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise SweBenchRunnerError("official evaluator process did not start") from exc
    timed_out = False
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        if _process_group_exists(process.pid):
            _terminate_process_group(process.pid)
        if process.poll() is None:
            process.wait(timeout=5)
    if timed_out:
        raise SweBenchRunnerError("official evaluator process timed out")
    if process.returncode != 0:
        raise SweBenchRunnerError("official evaluator process returned nonzero")


def _copy_verified_dataset(
    source_path: str,
    destination_path: str,
) -> None:
    source = -1
    destination = -1
    hasher = hashlib.sha256()
    size = 0
    try:
        source = os.open(
            source_path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        source_identity = os.fstat(source)
        if (
            not stat.S_ISREG(source_identity.st_mode)
            or source_identity.st_nlink != 1
            or source_identity.st_size != DATASET_SIZE_BYTES
        ):
            raise SweBenchRunnerError("pinned dataset identity is invalid")
        destination = os.open(
            destination_path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o400,
        )
        while size < DATASET_SIZE_BYTES:
            chunk = os.read(source, min(64 * 1024, DATASET_SIZE_BYTES - size))
            if not chunk:
                break
            hasher.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination, view)
                if written <= 0:
                    raise SweBenchRunnerError("pinned dataset copy made no progress")
                view = view[written:]
            size += len(chunk)
        if os.read(source, 1):
            raise SweBenchRunnerError("pinned dataset exceeds its byte limit")
        os.fsync(destination)
    except OSError as exc:
        raise SweBenchRunnerError("pinned dataset could not be staged") from exc
    finally:
        if destination >= 0:
            os.close(destination)
        if source >= 0:
            os.close(source)
    if size != DATASET_SIZE_BYTES or hasher.hexdigest() != DATASET_SHA256:
        try:
            os.unlink(destination_path)
        except OSError:
            pass
        raise SweBenchRunnerError("pinned dataset digest mismatch")


def _private_file_digest(path: str, *, max_bytes: int) -> str:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        identity = os.fstat(descriptor)
        if (
            not stat.S_ISREG(identity.st_mode)
            or identity.st_uid != os.geteuid()
            or identity.st_nlink != 1
            or stat.S_IMODE(identity.st_mode) != 0o400
            or identity.st_size > max_bytes
        ):
            raise SweBenchRunnerError("private evaluator input identity is invalid")
        hasher = hashlib.sha256()
        size = 0
        while size <= max_bytes:
            chunk = os.read(descriptor, min(64 * 1024, max_bytes + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            hasher.update(chunk)
        if size > max_bytes:
            raise SweBenchRunnerError("private evaluator input exceeds its bound")
        return "sha256:" + hasher.hexdigest()
    except OSError as exc:
        raise SweBenchRunnerError("private evaluator input is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_digest_bound_dataset(
    source_path: str,
    destination_path: str,
    task_binding: SweBenchTaskBinding,
) -> str:
    try:
        import pyarrow as arrow
        import pyarrow.parquet as parquet
    except ImportError as exc:
        raise SweBenchRunnerError(
            "pyarrow is required to bind the evaluator image"
        ) from exc
    verified_row = task_binding.load_verified_row(source_path)
    table = parquet.read_table(source_path)
    rows = table.to_pylist()
    indexes = [
        index
        for index, row in enumerate(rows)
        if row.get("instance_id") == task_binding.instance_id
    ]
    if len(indexes) != 1 or rows[indexes[0]] != dict(verified_row):
        raise SweBenchRunnerError("private evaluator dataset row changed after verification")
    rows[indexes[0]] = dict(rows[indexes[0]]) | {
        "image": _IMMUTABLE_IMAGE_REFERENCE
    }
    try:
        transformed = arrow.Table.from_pylist(rows, schema=table.schema)
        parquet.write_table(transformed, destination_path, compression="zstd")
        os.chmod(destination_path, 0o400, follow_symlinks=False)
        observed_rows = parquet.read_table(destination_path).to_pylist()
    except (OSError, ValueError, TypeError) as exc:
        raise SweBenchRunnerError(
            "digest-bound evaluator dataset could not be staged"
        ) from exc
    if observed_rows != rows:
        raise SweBenchRunnerError("digest-bound evaluator dataset is not canonical")
    return _private_file_digest(
        destination_path,
        max_bytes=DATASET_SIZE_BYTES * 2,
    )


@dataclass(frozen=True, slots=True)
class OfficialEvaluatorOutcome:
    evaluation: SweBenchEvaluatorResult
    cleanup_digest: str
    evaluation_dataset_digest: str
    image_observation_digest: str


@dataclass(frozen=True, slots=True)
class SubprocessOfficialEvaluator:
    environment_root: str
    work_directory: str
    binding: SweBenchEvaluatorBinding = field(
        default_factory=lambda: OFFICIAL_SWE_BENCH_EVALUATOR
    )
    environment_digest: str = field(init=False)
    python_path: str = field(init=False, repr=False)
    python_digest: str = field(init=False)
    docker_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _absolute_path(self.environment_root, field_name="evaluator environment")
        _absolute_path(self.work_directory, field_name="evaluator work directory")
        if (
            type(self.binding) is not SweBenchEvaluatorBinding
            or self.binding != OFFICIAL_SWE_BENCH_EVALUATOR
        ):
            raise SweBenchRunnerError("evaluator adapter is not the official binding")
        measurement = measure_official_evaluator_environment(self.environment_root)
        if measurement["environment_digest"] != OFFICIAL_EVALUATOR_ENVIRONMENT_DIGEST:
            raise SweBenchRunnerError("official evaluator environment digest mismatch")
        if measurement["python_digest"] != OFFICIAL_EVALUATOR_PYTHON_DIGEST:
            raise SweBenchRunnerError("official evaluator Python digest mismatch")
        if measurement["docker_digest"] != OFFICIAL_EVALUATOR_DOCKER_DIGEST:
            raise SweBenchRunnerError("official evaluator Docker client digest mismatch")
        _require_root_private_work_directory(self.work_directory)
        object.__setattr__(
            self,
            "environment_digest",
            str(measurement["environment_digest"]),
        )
        object.__setattr__(self, "python_path", str(measurement["python_path"]))
        object.__setattr__(self, "python_digest", str(measurement["python_digest"]))
        object.__setattr__(self, "docker_digest", str(measurement["docker_digest"]))

    def _verify_environment(self) -> None:
        measurement = measure_official_evaluator_environment(self.environment_root)
        if (
            measurement.get("environment_digest") != self.environment_digest
            or measurement.get("python_path") != self.python_path
            or measurement.get("python_digest") != self.python_digest
            or measurement.get("docker_digest") != self.docker_digest
        ):
            raise SweBenchRunnerError(
                "official evaluator environment changed after admission"
            )

    def identity_dict(self) -> dict[str, Any]:
        return self.binding.identity_dict() | {
            "environment_digest": self.environment_digest,
            "python_digest": self.python_digest,
            "docker_digest": self.docker_digest,
        }

    def _environment(self, run_root: str) -> dict[str, str]:
        return {
            "HOME": run_root,
            "PATH": os.pathsep.join(
                (
                    os.path.join(self.environment_root, "bin"),
                    "/usr/bin",
                    "/bin",
                )
            ),
            "PYTHONUNBUFFERED": "1",
        }

    def _observe_image(
        self,
        *,
        run_root: str,
        environment: Mapping[str, str],
    ) -> str:
        observation_path = os.path.join(run_root, "image-observation.json")
        observation_script = (
            "import json,os,sys;"
            + _EVALUATOR_ENVIRONMENT_ASSERTION
            + "from swebench.harness.run_evaluation import _docker_client;"
            "image=_docker_client().images.get(sys.argv[1]);"
            "payload={'requested_reference':sys.argv[1],'image_id':image.id,"
            "'repo_digests':sorted(image.attrs.get('RepoDigests') or [])};"
            "open(sys.argv[2],'x',encoding='utf-8').write("
            "json.dumps(payload,sort_keys=True,separators=(',',':')));"
            "os.chmod(sys.argv[2],0o400)"
        )
        _run_evaluator_process(
            (
                self.python_path,
                "-I",
                "-B",
                "-c",
                observation_script,
                self.environment_root,
                _IMMUTABLE_IMAGE_REFERENCE,
                observation_path,
            ),
            cwd=run_root,
            environment=environment,
            timeout_seconds=60,
        )
        observation = _read_json_file(observation_path)
        image_id = observation.get("image_id")
        repo_digests = observation.get("repo_digests")
        if (
            set(observation) != {
                "requested_reference",
                "image_id",
                "repo_digests",
            }
            or observation.get("requested_reference")
            != _IMMUTABLE_IMAGE_REFERENCE
            or type(image_id) is not str
            or _DIGEST_RE.fullmatch(image_id) is None
            or not isinstance(repo_digests, list)
            or any(type(value) is not str for value in repo_digests)
            or _IMMUTABLE_IMAGE_REFERENCE not in repo_digests
        ):
            raise SweBenchRunnerError(
                "local evaluator image is not the pinned platform digest"
            )
        return _canonical_digest(observation)

    def _cleanup_containers(
        self,
        *,
        run_id: str,
        run_root: str,
        environment: Mapping[str, str],
    ) -> dict[str, Any]:
        cleanup_path = os.path.join(run_root, "container-cleanup.json")
        cleanup_script = (
            "import json, os, sys\n"
            + _EVALUATOR_ENVIRONMENT_ASSERTION
            + "\nfrom swebench.harness.run_evaluation import _docker_client\n"
            "prefix = f'sweb.eval.{sys.argv[1].lower()}.{sys.argv[2]}'\n"
            "client = _docker_client()\n"
            "targets = sorted(\n"
            "    (container for container in client.containers.list(all=True)\n"
            "     if container.name == prefix or container.name.startswith(prefix + '.')),\n"
            "    key=lambda container: container.name,\n"
            ")\n"
            "removed = []\n"
            "errors = []\n"
            "for container in targets:\n"
            "    try:\n"
            "        container.remove(force=True)\n"
            "        removed.append(container.name)\n"
            "    except Exception as exc:\n"
            "        errors.append({'name': container.name, 'error': type(exc).__name__})\n"
            "with open(sys.argv[3], 'x', encoding='utf-8') as output:\n"
            "    output.write(json.dumps(\n"
            "        {'removed': removed, 'errors': errors},\n"
            "        sort_keys=True, separators=(',', ':'),\n"
            "    ))\n"
            "os.chmod(sys.argv[3], 0o400)\n"
        )
        _run_evaluator_process(
            (
                self.python_path,
                "-I",
                "-B",
                "-c",
                cleanup_script,
                self.environment_root,
                INSTANCE_ID,
                run_id,
                cleanup_path,
            ),
            cwd=run_root,
            environment=environment,
            timeout_seconds=120,
        )
        cleanup = _read_json_file(cleanup_path)
        if (
            set(cleanup) != {"removed", "errors"}
            or not isinstance(cleanup.get("removed"), list)
            or cleanup.get("errors") != []
        ):
            raise SweBenchRunnerError("official evaluator container cleanup failed")
        inventory_path = os.path.join(run_root, "container-inventory.json")
        inventory_script = (
            "import json,os,sys;"
            + _EVALUATOR_ENVIRONMENT_ASSERTION
            + "from swebench.harness.run_evaluation import _docker_client;"
            "prefix=f'sweb.eval.{sys.argv[1].lower()}.{sys.argv[2]}';"
            "names=sorted(c.name for c in _docker_client().containers.list(all=True) "
            "if c.name==prefix or c.name.startswith(prefix+'.'));"
            "open(sys.argv[3],'x',encoding='utf-8').write("
            "json.dumps({'containers':names},sort_keys=True,separators=(',',':')))"
        )
        _run_evaluator_process(
            (
                self.python_path,
                "-I",
                "-B",
                "-c",
                inventory_script,
                self.environment_root,
                INSTANCE_ID,
                run_id,
                inventory_path,
            ),
            cwd=run_root,
            environment=environment,
            timeout_seconds=60,
        )
        inventory = _read_json_file(inventory_path)
        if inventory != {"containers": []}:
            raise SweBenchRunnerError("official evaluator containers survived cleanup")
        return {
            "schema_version": "bb.rl.swe-bench-evaluator-cleanup.v1",
            "run_id": run_id,
            "containers": [],
            "removed_containers": cleanup["removed"],
            "process_group_absent": True,
        }

    def evaluate(
        self,
        *,
        task_binding: SweBenchTaskBinding,
        dataset_path: str,
        prediction: bytes,
        run_id: str,
        model_name: str,
        patch_digest: str,
    ) -> OfficialEvaluatorOutcome:
        if type(task_binding) is not SweBenchTaskBinding:
            raise TypeError("task_binding must be an exact SweBenchTaskBinding")
        _run_id(run_id)
        _safe_id(model_name, field_name="model_name")
        _digest(patch_digest, field_name="patch")
        self._verify_environment()
        run_root = os.path.join(self.work_directory, run_id)
        try:
            os.mkdir(run_root, 0o700)
        except OSError as exc:
            raise SweBenchRunnerError(
                "official evaluator run directory must be new"
            ) from exc
        dataset_copy = os.path.join(run_root, "source-dataset.parquet")
        evaluation_dataset = os.path.join(run_root, "evaluation-dataset.parquet")
        predictions_path = os.path.join(run_root, "predictions.jsonl")
        report_directory = os.path.join(run_root, "reports")
        _require_root_private_work_directory(run_root)
        environment = self._environment(run_root)
        evaluation: SweBenchEvaluatorResult | None = None
        cleanup_projection: dict[str, Any] | None = None
        evaluation_dataset_digest: str | None = None
        image_observation_digest: str | None = None
        failure: Exception | None = None
        try:
            _copy_verified_dataset(dataset_path, dataset_copy)
            task_binding.load_verified_row(dataset_copy)
            evaluation_dataset_digest = _write_digest_bound_dataset(
                dataset_copy,
                evaluation_dataset,
                task_binding,
            )
            image_observation_digest = self._observe_image(
                run_root=run_root,
                environment=environment,
            )
            _write_prediction(predictions_path, prediction)
            os.mkdir(report_directory, 0o700)
            command = TrustedEvaluatorCommand.create(
                dataset_path=evaluation_dataset,
                predictions_path=predictions_path,
                report_directory=report_directory,
                run_id=run_id,
                model_name=model_name,
                patch_digest=patch_digest,
            )
            evaluator_script = (
                "import os,sys;"
                + _EVALUATOR_ENVIRONMENT_ASSERTION
                + "from swebench.cli.cli import main;"
                "sys.exit(main())"
            )
            _run_evaluator_process(
                (
                    self.python_path,
                    "-I",
                    "-B",
                    "-c",
                    evaluator_script,
                    self.environment_root,
                    *command.argv[1:],
                ),
                cwd=run_root,
                environment=environment,
                timeout_seconds=command.timeout_seconds + 60,
            )
            aggregate_path = os.path.join(
                command.report_directory,
                f"{command.model_name}.{command.run_id}.json",
            )
            instance_path = os.path.join(
                run_root,
                "logs",
                "run_evaluation",
                command.run_id,
                command.model_name,
                INSTANCE_ID,
                "report.json",
            )
            evaluation = SweBenchEvaluatorResult.from_reports(
                command,
                aggregate_report=_read_json_file(aggregate_path),
                instance_report=_read_json_file(instance_path),
            )
        except Exception as exc:
            failure = exc
        try:
            cleanup_projection = self._cleanup_containers(
                run_id=run_id,
                run_root=run_root,
                environment=environment,
            )
        except Exception as exc:
            failure = (
                exc
                if failure is None
                else ExceptionGroup(
                    "official evaluator execution and cleanup failed",
                    [failure, exc],
                )
            )
        try:
            shutil.rmtree(run_root)
            if os.path.lexists(run_root):
                raise SweBenchRunnerError(
                    "official evaluator run directory survived cleanup"
                )
        except Exception as exc:
            failure = (
                exc
                if failure is None
                else ExceptionGroup(
                    "official evaluator and workspace cleanup failed",
                    [failure, exc],
                )
            )
        if failure is not None:
            raise SweBenchRunnerError("official SWE-bench evaluator failed") from failure
        if (
            evaluation is None
            or cleanup_projection is None
            or evaluation_dataset_digest is None
            or image_observation_digest is None
        ):
            raise SweBenchRunnerError("official evaluator outcome is incomplete")
        cleanup_projection["run_root_absent"] = True
        return OfficialEvaluatorOutcome(
            evaluation=evaluation,
            cleanup_digest=_canonical_digest(cleanup_projection),
            evaluation_dataset_digest=evaluation_dataset_digest,
            image_observation_digest=image_observation_digest,
        )


def _freeze_path_bindings(
    value: Mapping[str, str],
    *,
    field_name: str,
) -> Mapping[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise SweBenchRunnerError(f"{field_name} must contain launcher bindings")
    copied: dict[str, str] = {}
    for name, path in value.items():
        _safe_id(name, field_name=f"{field_name} handle")
        copied[name] = _absolute_path(path, field_name=f"{field_name} path")
    return MappingProxyType(copied)


@dataclass(frozen=True, slots=True)
class InstalledHeadlessInvocation:
    composition_ref_path: str
    secret_files: Mapping[str, str]
    provider_credentials: Mapping[str, str]
    provider_routes: Mapping[str, HeadlessProviderRouteAuthority]
    repository_base_commits: Mapping[str, str]
    composition_ref_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _absolute_path(self.composition_ref_path, field_name="composition_ref_path")
        object.__setattr__(
            self,
            "composition_ref_digest",
            _regular_file_digest(
                self.composition_ref_path,
                field_name="composition_ref_path",
                max_bytes=_MAX_COMPOSITION_REF_BYTES,
            ),
        )
        object.__setattr__(
            self,
            "secret_files",
            _freeze_path_bindings(
                self.secret_files,
                field_name="composition secret files",
            ),
        )
        object.__setattr__(
            self,
            "provider_credentials",
            _freeze_path_bindings(
                self.provider_credentials,
                field_name="provider credentials",
            ),
        )
        if not isinstance(self.provider_routes, Mapping) or not self.provider_routes:
            raise SweBenchRunnerError("provider routes must contain launcher bindings")
        routes: dict[str, HeadlessProviderRouteAuthority] = {}
        for handle, route in self.provider_routes.items():
            _safe_id(handle, field_name="provider route handle")
            if type(route) is not HeadlessProviderRouteAuthority:
                raise TypeError(
                    "provider route must be an exact HeadlessProviderRouteAuthority"
                )
            routes[handle] = route
        object.__setattr__(self, "provider_routes", MappingProxyType(routes))
        if (
            not isinstance(self.repository_base_commits, Mapping)
            or not self.repository_base_commits
        ):
            raise SweBenchRunnerError(
                "repository base commits must contain launcher bindings"
            )
        commits: dict[str, str] = {}
        for digest, commit in self.repository_base_commits.items():
            _digest(digest, field_name="repository snapshot")
            if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
                raise SweBenchRunnerError("repository base commit is invalid")
            commits[digest] = commit
        object.__setattr__(
            self,
            "repository_base_commits",
            MappingProxyType(commits),
        )

    def identity_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "bb.rl.installed-headless-invocation.v1",
            "composition_ref_digest": self.composition_ref_digest,
            "secret_handle_ids": sorted(self.secret_files),
            "provider_credential_handle_ids": sorted(self.provider_credentials),
            "provider_routes": {
                handle: route.identity_dict()
                for handle, route in sorted(self.provider_routes.items())
            },
            "repository_base_commits": dict(
                sorted(self.repository_base_commits.items())
            ),
        }

    async def run(self, request: HeadlessRunRequest) -> Mapping[str, Any]:
        if (
            _regular_file_digest(
                self.composition_ref_path,
                field_name="composition_ref_path",
                max_bytes=_MAX_COMPOSITION_REF_BYTES,
            )
            != self.composition_ref_digest
        ):
            raise SweBenchRunnerError(
                "composition_ref_path changed after admission"
            )
        return await run_headless_request(
            request,
            composition_ref_path=self.composition_ref_path,
            secret_files=self.secret_files,
            provider_credentials=self.provider_credentials,
            provider_routes=self.provider_routes,
            repository_base_commits=self.repository_base_commits,
        )

@dataclass(frozen=True, slots=True)
class InstalledSweBenchRequest:
    """One installed-package journey with a sealed task repository snapshot."""

    profile: E4ProfileIdentity
    headless_request: HeadlessRunRequest
    headless_invocation: InstalledHeadlessInvocation
    dataset_path: str
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
        if type(self.headless_invocation) is not InstalledHeadlessInvocation:
            raise TypeError(
                "headless_invocation must be an exact InstalledHeadlessInvocation"
            )
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
        _absolute_path(self.dataset_path, field_name="dataset_path")
        _run_id(self.run_id)
        if (
            self.headless_request.prompt
            != self.task_binding.model_visible_task()["problem_statement"]
        ):
            raise SweBenchRunnerError(
                "headless prompt is not the pinned SWE-bench problem statement"
            )
        workspace = self.headless_request.workspace
        if workspace.task_image_digest != self.task_binding.image_digest:
            raise SweBenchRunnerError(
                "headless workspace image is not the pinned SWE-bench image"
            )
        snapshot_digest = workspace.repository_snapshot_digest
        if snapshot_digest is None:
            raise SweBenchRunnerError(
                "installed SWE-bench journey requires a sealed task repository"
            )
        if (
            self.headless_invocation.repository_base_commits.get(snapshot_digest)
            != self.task_binding.task.base_commit
        ):
            raise SweBenchRunnerError(
                "task repository snapshot lacks the pinned base authority"
            )
        if workspace.base_commit != self.task_binding.task.base_commit:
            raise SweBenchRunnerError(
                "headless workspace base is not the pinned SWE-bench commit"
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
            "headless_invocation_digest": _canonical_digest(
                self.headless_invocation.identity_dict()
            ),
            "dataset_artifact_digest": "sha256:" + DATASET_SHA256,
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
    evaluation_dataset_digest: str
    image_observation_digest: str
    command_digest: str
    aggregate_report_digest: str
    instance_report_digest: str
    report_digest: str
    reward: float
    reward_digest: str
    headless_cleanup_digest: str
    evaluator_cleanup_digest: str
    cleanup_digest: str
    cleanup_inventory_digest: str
    headless_result_digest: str
    receipt_digest: str = field(init=False)
    schema_version: Literal["bb.rl.swe-bench-reward-receipt.v1"] = (
        REWARD_RECEIPT_SCHEMA_VERSION
    )

    def __post_init__(self) -> None:
        _run_id(self.run_id)
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
            (self.evaluation_dataset_digest, "evaluation dataset"),
            (self.image_observation_digest, "image observation"),
            (self.command_digest, "evaluator command"),
            (self.aggregate_report_digest, "aggregate report"),
            (self.instance_report_digest, "instance report"),
            (self.report_digest, "report"),
            (self.reward_digest, "reward"),
            (self.headless_cleanup_digest, "headless cleanup"),
            (self.evaluator_cleanup_digest, "evaluator cleanup"),
            (self.cleanup_digest, "combined cleanup"),
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
            "evaluation_dataset_digest": self.evaluation_dataset_digest,
            "image_observation_digest": self.image_observation_digest,
            "command_digest": self.command_digest,
            "aggregate_report_digest": self.aggregate_report_digest,
            "instance_report_digest": self.instance_report_digest,
            "report_digest": self.report_digest,
            "reward": self.reward,
            "reward_digest": self.reward_digest,
            "headless_cleanup_digest": self.headless_cleanup_digest,
            "evaluator_cleanup_digest": self.evaluator_cleanup_digest,
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
    config_identity = payload.get("config_identity")
    config_digest = payload.get("config_digest")
    if (
        not isinstance(config_identity, Mapping)
        or type(config_digest) is not str
        or _canonical_digest(config_identity) != config_digest
    ):
        raise SweBenchRunnerError("headless config identity is not canonical")
    target = E4TargetPolicyProjection.load(
        request.headless_request.target_id,
        request.headless_request.target_dynamic_fields,
    )
    if payload.get("target_identity") != target.identity_dict():
        raise SweBenchRunnerError("headless target identity mismatch")
    if (
        payload.get("provider_input_identity")
        != request.headless_request.provider.identity_dict()
        or payload.get("workspace_input")
        != request.headless_request.workspace.model_dump(mode="json")
    ):
        raise SweBenchRunnerError("headless launcher input identity mismatch")
    engine = payload.get("engine_identity")
    if (
        not isinstance(engine, Mapping)
        or engine.get("distribution") != "breadboard-harness-cli"
        or _DIGEST_RE.fullmatch(str(engine.get("headless_module_digest"))) is None
        or _DIGEST_RE.fullmatch(str(engine.get("policy_provider_module_digest"))) is None
    ):
        raise SweBenchRunnerError("headless engine identity is incomplete")
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
    cleanup_receipt = cleanup.get("receipt")
    if (
        not isinstance(cleanup_receipt, Mapping)
        or _canonical_digest(cleanup_receipt) != cleanup_digest
    ):
        raise SweBenchRunnerError("headless cleanup receipt digest mismatch")
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
    if (
        evidence.get("patch_base_commit") != request.task_binding.task.base_commit
        or evidence.get("patch_digest") != patch_digest
        or _DIGEST_RE.fullmatch(str(evidence.get("patch_git_executable_digest")))
        is None
        or _DIGEST_RE.fullmatch(str(evidence.get("patch_snapshot_root_digest")))
        is None
    ):
        raise SweBenchRunnerError("headless workspace evidence is incomplete")
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
    evaluator: SubprocessOfficialEvaluator,
) -> InstalledSweBenchRewardReceipt:
    """Run one row through canonical installed headless and official evaluator code."""

    if type(request) is not InstalledSweBenchRequest:
        raise TypeError("request must be an exact InstalledSweBenchRequest")
    if type(evaluator) is not SubprocessOfficialEvaluator:
        raise TypeError("evaluator must be an exact SubprocessOfficialEvaluator")
    if (
        evaluator.binding != request.evaluator_binding
        or evaluator.binding != OFFICIAL_SWE_BENCH_EVALUATOR
    ):
        raise SweBenchRunnerError(
            "evaluator adapter is not the pinned official evaluator"
        )
    target = E4TargetPolicyProjection.load(
        request.headless_request.target_id,
        request.headless_request.target_dynamic_fields,
    )
    controller_identity = _controller_identity(request.profile, target)
    model_name = _controller_model_name(controller_identity)
    try:
        headless_result = await request.headless_invocation.run(
            request.headless_request
        )
    except Exception as exc:
        raise SweBenchRunnerError("canonical headless execution failed") from exc
    if not isinstance(headless_result, Mapping):
        raise SweBenchRunnerError("canonical headless result must be a mapping")
    (
        _frozen_headless,
        headless_digest,
        headless_cleanup_digest,
        cleanup_inventory_digest,
        patch_digest,
    ) = _validate_headless_result(headless_result, request)
    patch_path = request.headless_request.patch_path
    if patch_path is None:
        raise SweBenchRunnerError("canonical workspace patch path is missing")
    patch = _read_patch(patch_path, patch_digest)
    try:
        prediction = prediction_jsonl(
            patch,
            model_name=model_name,
        )
    except (SweBenchTaskError, TypeError) as exc:
        raise SweBenchRunnerError(str(exc)) from exc
    prediction_digest = _digest_bytes(prediction)
    outcome = evaluator.evaluate(
        task_binding=request.task_binding,
        dataset_path=request.dataset_path,
        prediction=prediction,
        run_id=request.run_id,
        model_name=model_name,
        patch_digest=patch_digest,
    )
    if type(outcome) is not OfficialEvaluatorOutcome:
        raise SweBenchRunnerError("evaluator adapter returned an unbound outcome")
    evaluation = outcome.evaluation
    if (
        type(evaluation) is not SweBenchEvaluatorResult
        or evaluation.command.patch_digest != patch_digest
        or evaluation.command.run_id != request.run_id
        or evaluation.command.model_name != model_name
    ):
        raise SweBenchRunnerError("evaluator command/result binding mismatch")
    combined_cleanup_digest = _canonical_digest(
        {
            "schema_version": "bb.rl.swe-bench-combined-cleanup.v1",
            "headless_cleanup_digest": headless_cleanup_digest,
            "evaluator_cleanup_digest": outcome.cleanup_digest,
            "headless_cleanup_inventory_digest": cleanup_inventory_digest,
        }
    )
    return InstalledSweBenchRewardReceipt(
        run_id=request.run_id,
        episode_id=request.headless_request.resolve_request.episode_id,
        controller_identity=controller_identity,
        evaluator_identity=evaluator.identity_dict(),
        task_binding_digest=request.task_binding.identity_digest,
        dataset_artifact_digest="sha256:" + DATASET_SHA256,
        dataset_row_digest=request.task_binding.row_digest,
        prediction_digest=prediction_digest,
        patch_digest=patch_digest,
        image_index_digest=IMAGE_INDEX_DIGEST,
        image_leaf_digest=IMAGE_LEAF_DIGEST,
        evaluation_dataset_digest=outcome.evaluation_dataset_digest,
        image_observation_digest=outcome.image_observation_digest,
        command_digest=evaluation.command.command_digest,
        aggregate_report_digest=evaluation.aggregate_report_digest,
        instance_report_digest=evaluation.instance_report_digest,
        report_digest=evaluation.report_digest,
        reward=evaluation.reward,
        reward_digest=evaluation.reward_digest,
        headless_cleanup_digest=headless_cleanup_digest,
        evaluator_cleanup_digest=outcome.cleanup_digest,
        cleanup_digest=combined_cleanup_digest,
        cleanup_inventory_digest=cleanup_inventory_digest,
        headless_result_digest=headless_digest,
    )


__all__ = [
    "COMMAND_SCHEMA_VERSION",
    "E4ProfileIdentity",
    "E4_PROFILE_IDS",
    "EVALUATION_RESULT_SCHEMA_VERSION",
    "EVALUATOR_BINDING_SCHEMA_VERSION",
    "HEADLESS_RESULT_SCHEMA_VERSION",
    "InstalledHeadlessInvocation",
    "InstalledSweBenchRequest",
    "InstalledSweBenchRewardReceipt",
    "OFFICIAL_EVALUATOR_DOCKER_DIGEST",
    "OFFICIAL_EVALUATOR_ENVIRONMENT_DIGEST",
    "OFFICIAL_EVALUATOR_PYTHON_DIGEST",
    "OFFICIAL_SWE_BENCH_EVALUATOR",
    "OfficialEvaluatorOutcome",
    "PINNED_SWE_BENCH_TASK",
    "REWARD_RECEIPT_SCHEMA_VERSION",
    "RUN_REQUEST_SCHEMA_VERSION",
    "SubprocessOfficialEvaluator",
    "SweBenchEvaluatorBinding",
    "SweBenchEvaluatorResult",
    "SweBenchRunnerError",
    "SweBenchTaskBinding",
    "TrustedEvaluatorCommand",
    "measure_official_evaluator_environment",
    "run_installed_swe_bench",
]
