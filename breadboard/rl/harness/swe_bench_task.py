from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import stat
from typing import Any, Mapping


INSTANCE_ID = "sympy__sympy-20590"
REPOSITORY = "sympy/sympy"
VERSION = "1.7"
BASE_COMMIT = "cffd4e0f86fefd4802349a9f9b19ed70934ea354"
ENVIRONMENT_SETUP_COMMIT = BASE_COMMIT
DATASET_REPOSITORY = "SWE-bench/SWE-bench_Verified"
DATASET_REVISION = "78f471bf655a3137b2e8a75af1501690ec009ec3"
DATASET_RELATIVE_PATH = "data/test-00000-of-00001.parquet"
DATASET_SHA256 = "030cfd7f2a704c4c0226e7f104c725a3b41230b1d3517f9c915ad7ea5be3fa25"
DATASET_SIZE_BYTES = 6_304_616
PROBLEM_STATEMENT = """Symbol instances have __dict__ since 1.7?
In version 1.6.2 Symbol instances had no `__dict__` attribute
```python
>>> sympy.Symbol('s').__dict__
---------------------------------------------------------------------------
AttributeError                            Traceback (most recent call last)
<ipython-input-3-e2060d5eec73> in <module>
----> 1 sympy.Symbol('s').__dict__

AttributeError: 'Symbol' object has no attribute '__dict__'
>>> sympy.Symbol('s').__slots__
('name',)
```

This changes in 1.7 where `sympy.Symbol('s').__dict__` now exists (and returns an empty dict)
I may misinterpret this, but given the purpose of `__slots__`, I assume this is a bug, introduced because some parent class accidentally stopped defining `__slots__`."""
ROW_DIGEST = "sha256:8cfb0a8385266590d0faf4687cf52d0dab83beb2a8191eaa6030d18d85f050d8"
IMAGE_REFERENCE = "swebench/sweb.eval.x86_64.sympy_1776_sympy-20590:latest"
IMAGE_PLATFORM = "linux/amd64"
IMAGE_LEAF_DIGEST = (
    "sha256:bcdff110a35d19a6838fde5f678d1b5103610234bd034e5703df46878aad3b63"
)
IMAGE_INDEX_DIGEST = (
    "sha256:3a282752833ce34730ee0621e22033501c993f45742775ca57f04c9ff27178a0"
)
EVALUATOR_VERSION = "5.0.1"
EVALUATOR_COMMIT = "87ab1f6ced28f75ba73ca899dc759b019310944a"
EVALUATOR_TREE = "b90d30cbd774582f5a2643aa90a7c5e289309e7d"
EVALUATOR_LICENSE = "MIT"
EVALUATOR_TIMEOUT_SECONDS = 1_800
DATASET_URL = (
    "https://huggingface.co/datasets/"
    f"{DATASET_REPOSITORY}/resolve/{DATASET_REVISION}/{DATASET_RELATIVE_PATH}"
)
IMAGE_TAG_METADATA_URL = (
    "https://hub.docker.com/v2/repositories/"
    "swebench/sweb.eval.x86_64.sympy_1776_sympy-20590/tags/latest"
)
EVALUATOR_SOURCE_URL = "https://github.com/SWE-bench/SWE-bench/tree/" + EVALUATOR_COMMIT
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


@dataclass(frozen=True, slots=True)
class PinnedSweBenchTask:
    schema_version: str = "bb.rl.swe-bench-task.v1"
    instance_id: str = INSTANCE_ID
    repository: str = REPOSITORY
    version: str = VERSION
    base_commit: str = BASE_COMMIT
    environment_setup_commit: str = ENVIRONMENT_SETUP_COMMIT
    dataset_repository: str = DATASET_REPOSITORY
    dataset_revision: str = DATASET_REVISION
    dataset_relative_path: str = DATASET_RELATIVE_PATH
    dataset_sha256: str = DATASET_SHA256
    dataset_size_bytes: int = DATASET_SIZE_BYTES
    row_digest: str = ROW_DIGEST
    image_reference: str = IMAGE_REFERENCE
    image_platform: str = IMAGE_PLATFORM
    image_leaf_digest: str = IMAGE_LEAF_DIGEST
    image_index_digest: str = IMAGE_INDEX_DIGEST
    evaluator_version: str = EVALUATOR_VERSION
    evaluator_commit: str = EVALUATOR_COMMIT
    evaluator_tree: str = EVALUATOR_TREE
    evaluator_timeout_seconds: int = EVALUATOR_TIMEOUT_SECONDS

    def identity_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @property
    def identity_digest(self) -> str:
        return _canonical_digest(self.identity_dict())

    def verify_dataset(self, path: str) -> None:
        identity = _measure_regular_file(path, max_bytes=self.dataset_size_bytes)
        if (
            identity["size_bytes"] != self.dataset_size_bytes
            or identity["sha256"] != self.dataset_sha256
        ):
            raise SweBenchTaskError("pinned SWE-bench dataset identity mismatch")

    def load_verified_row(self, path: str) -> Mapping[str, Any]:
        self.verify_dataset(path)
        try:
            import pyarrow.parquet as parquet
        except ImportError:
            raise SweBenchTaskError(
                "pyarrow is required to verify the pinned SWE-bench row"
            ) from None
        table = parquet.read_table(path)
        matching = [
            row
            for row in table.to_pylist()
            if row.get("instance_id") == self.instance_id
        ]
        if len(matching) != 1:
            raise SweBenchTaskError(
                "pinned SWE-bench dataset does not contain exactly one task row"
            )
        row = matching[0]
        if _canonical_digest(row) != self.row_digest:
            raise SweBenchTaskError("pinned SWE-bench row digest mismatch")
        expected = {
            "repo": self.repository,
            "version": self.version,
            "base_commit": self.base_commit,
            "environment_setup_commit": self.environment_setup_commit,
            "image": self.image_reference,
        }
        if any(row.get(name) != value for name, value in expected.items()):
            raise SweBenchTaskError("pinned SWE-bench row authority mismatch")
        return row

    def model_visible_task(self, path: str | None = None) -> dict[str, str]:
        problem_statement = PROBLEM_STATEMENT
        if path is not None:
            row = self.load_verified_row(path)
            if row.get("problem_statement") != problem_statement:
                raise SweBenchTaskError(
                    "pinned SWE-bench problem statement authority mismatch"
                )
        return {
            "instance_id": self.instance_id,
            "repository": self.repository,
            "base_commit": self.base_commit,
            "problem_statement": problem_statement,
        }


class SweBenchTaskError(RuntimeError):
    pass


def prediction_jsonl(model_patch: str, *, model_name: str) -> bytes:
    if type(model_patch) is not str:
        raise TypeError("model_patch must be text")
    if _SAFE_ID.fullmatch(model_name) is None:
        raise SweBenchTaskError("model_name is not a safe evaluator identity")
    return (
        _canonical_bytes(
            {
                "instance_id": INSTANCE_ID,
                "model_name_or_path": model_name,
                "model_patch": model_patch,
            }
        )
        + b"\n"
    )


def official_evaluator_command(
    *,
    dataset_path: str,
    predictions_path: str,
    report_directory: str,
    run_id: str,
    timeout_seconds: int = EVALUATOR_TIMEOUT_SECONDS,
) -> tuple[str, ...]:
    for path in (dataset_path, predictions_path, report_directory):
        _require_absolute_path(path)
    if _SAFE_ID.fullmatch(run_id) is None:
        raise SweBenchTaskError("run_id is not a safe evaluator identity")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3_600:
        raise SweBenchTaskError("evaluator timeout is outside its supported range")
    return (
        "swebench",
        "eval",
        dataset_path,
        "--predictions",
        predictions_path,
        "--run-id",
        run_id,
        "--instance",
        INSTANCE_ID,
        "--split",
        "test",
        "--workers",
        "1",
        "--timeout",
        str(timeout_seconds),
        "--report-dir",
        report_directory,
    )


def verify_evaluator_installation(
    *,
    installed_version: str,
    source_commit: str,
) -> None:
    if installed_version != EVALUATOR_VERSION or source_commit != EVALUATOR_COMMIT:
        raise SweBenchTaskError("official SWE-bench evaluator identity mismatch")


def verify_image_tag_metadata(payload: Mapping[str, Any]) -> None:
    if payload.get("digest") != IMAGE_INDEX_DIGEST:
        raise SweBenchTaskError("SWE-bench image index digest mismatch")
    images = payload.get("images")
    if not isinstance(images, list):
        raise SweBenchTaskError("SWE-bench image metadata is malformed")
    leaves = [
        item
        for item in images
        if isinstance(item, Mapping)
        and item.get("os") == "linux"
        and item.get("architecture") == "amd64"
    ]
    if len(leaves) != 1 or leaves[0].get("digest") != IMAGE_LEAF_DIGEST:
        raise SweBenchTaskError("SWE-bench image leaf digest mismatch")


def score_official_reports(
    *,
    aggregate_report: Mapping[str, Any],
    instance_report: Mapping[str, Any],
) -> float:
    if (
        type(aggregate_report.get("schema_version")) is not int
        or aggregate_report["schema_version"] != 2
    ):
        raise SweBenchTaskError("official SWE-bench aggregate report schema mismatch")
    for name in ("total_instances", "submitted_instances", "completed_instances"):
        if type(aggregate_report.get(name)) is not int or aggregate_report[name] != 1:
            raise SweBenchTaskError("official SWE-bench report is incomplete")
    if aggregate_report.get("submitted_ids") != [INSTANCE_ID] or aggregate_report.get(
        "completed_ids"
    ) != [INSTANCE_ID]:
        raise SweBenchTaskError("official SWE-bench report instance identity mismatch")
    for name in (
        "infra_failure_instances",
        "ambiguous_failure_instances",
        "empty_patch_instances",
        "error_instances",
    ):
        if type(aggregate_report.get(name)) is not int or aggregate_report[name] != 0:
            raise SweBenchTaskError(
                "official SWE-bench evaluator reported a non-evaluation outcome"
            )
    for name in (
        "incomplete_ids",
        "infra_failure_ids",
        "ambiguous_failure_ids",
        "empty_patch_ids",
        "error_ids",
    ):
        if aggregate_report.get(name) != []:
            raise SweBenchTaskError(
                "official SWE-bench evaluator reported a non-evaluation outcome"
            )
    if aggregate_report.get("failure_reasons") != {}:
        raise SweBenchTaskError(
            "official SWE-bench evaluator reported a non-evaluation outcome"
        )
    if instance_report.get("instance_id") != INSTANCE_ID:
        raise SweBenchTaskError(
            "official SWE-bench instance report does not match the pinned task"
        )
    if instance_report.get("infra_failure") is not False:
        raise SweBenchTaskError(
            "official SWE-bench instance report is an infrastructure failure"
        )
    if (
        instance_report.get("patch_is_None") is not False
        or instance_report.get("patch_exists") is not True
        or instance_report.get("patch_successfully_applied") is not True
    ):
        raise SweBenchTaskError("official SWE-bench patch was not validly evaluated")
    resolved = instance_report.get("resolved")
    if type(resolved) is not bool:
        raise SweBenchTaskError("official SWE-bench resolution is not boolean")
    for name, expected in (
        ("resolved_instances", int(resolved)),
        ("unresolved_instances", int(not resolved)),
    ):
        if (
            type(aggregate_report.get(name)) is not int
            or aggregate_report[name] != expected
        ):
            raise SweBenchTaskError("official SWE-bench reports disagree on resolution")
    resolved_ids = aggregate_report.get("resolved_ids")
    unresolved_ids = aggregate_report.get("unresolved_ids")
    expected_resolved = [INSTANCE_ID] if resolved else []
    expected_unresolved = [] if resolved else [INSTANCE_ID]
    if resolved_ids != expected_resolved or unresolved_ids != expected_unresolved:
        raise SweBenchTaskError("official SWE-bench reports disagree on resolution")
    return 1.0 if resolved else 0.0


def _require_absolute_path(path: str) -> None:
    if (
        type(path) is not str
        or not os.path.isabs(path)
        or os.path.normpath(path) != path
    ):
        raise SweBenchTaskError("evaluator paths must be normalized and absolute")


def _measure_regular_file(path: str, *, max_bytes: int) -> dict[str, Any]:
    _require_absolute_path(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise SweBenchTaskError("task artifact must be a private regular file")
        if before.st_size > max_bytes:
            raise SweBenchTaskError("task artifact exceeds its byte limit")
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise SweBenchTaskError("task artifact changed while reading")
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise SweBenchTaskError("task artifact changed while reading")
        after = os.fstat(descriptor)
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) != (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ):
            raise SweBenchTaskError("task artifact changed while reading")
        return {
            "sha256": digest.hexdigest(),
            "size_bytes": after.st_size,
        }
    finally:
        os.close(descriptor)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _canonical_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


PINNED_SYMPY_20590 = PinnedSweBenchTask()


__all__ = [
    "BASE_COMMIT",
    "DATASET_REVISION",
    "DATASET_SHA256",
    "DATASET_SIZE_BYTES",
    "DATASET_URL",
    "ENVIRONMENT_SETUP_COMMIT",
    "EVALUATOR_COMMIT",
    "EVALUATOR_LICENSE",
    "EVALUATOR_SOURCE_URL",
    "EVALUATOR_TIMEOUT_SECONDS",
    "EVALUATOR_TREE",
    "PROBLEM_STATEMENT",
    "EVALUATOR_VERSION",
    "IMAGE_INDEX_DIGEST",
    "IMAGE_LEAF_DIGEST",
    "IMAGE_PLATFORM",
    "IMAGE_REFERENCE",
    "IMAGE_TAG_METADATA_URL",
    "INSTANCE_ID",
    "PINNED_SYMPY_20590",
    "PinnedSweBenchTask",
    "REPOSITORY",
    "ROW_DIGEST",
    "SweBenchTaskError",
    "official_evaluator_command",
    "prediction_jsonl",
    "score_official_reports",
    "verify_evaluator_installation",
    "verify_image_tag_metadata",
]
