"""Durable publication and admission lifecycle for immutable harness generations.

The lifecycle is deliberately the only owner of target publication state.  It
stores complete Harness Locks rather than references to author files, and uses a
small transaction around an atomic JSON snapshot. External preparation runs
outside that transaction; disposal holds the lock from its final ownership
check through its durable cleanup result.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from threading import RLock
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import uuid4

from breadboard.artifacts.cas import FilesystemCAS
from breadboard.product.harness.lock import (
    EffectiveHarnessLock,
    materialize_lock,
    sha256_json,
)
from .events import ProcessLock


PreparationStatus = Literal["preparing", "ready", "failed"]
CleanupStatus = Literal["not_required", "owned", "confirmed_absent", "unknown"]
AdmissionStatus = Literal["reserved", "materialized", "released"]


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        from types import MappingProxyType

        return MappingProxyType(
            {str(key): _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


def _required_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _required_target(value: object) -> str:
    target = _required_string(value, "target")
    path = PurePosixPath(target)
    if (
        path.is_absolute()
        or "\\" in target
        or not path.parts
        or str(path) != target
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise ValueError("target must be a safe relative publication name")
    return target


def _record_digest(
    *,
    target: str,
    lock_record: Mapping[str, Any],
    source_ref: str,
    expected_revision: int | None,
    request_id: str,
) -> str:
    return sha256_json(
        {
            "target": target,
            "lock_record": _thaw(lock_record),
            "source_ref": source_ref,
            "expected_revision": expected_revision,
            "request_id": request_id,
        }
    )


class GenerationLifecycleError(RuntimeError):
    """A stable lifecycle failure that can be safely reported to a caller."""

    def __init__(
        self,
        code: str,
        message: str,
        failed_stage: str,
        observed_revision: int | None = None,
    ) -> None:
        self.code = code
        self.failed_stage = failed_stage
        self.observed_revision = observed_revision
        super().__init__(message)

    @property
    def message(self) -> str:
        return str(self)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "failed_stage": self.failed_stage,
            "observed_revision": self.observed_revision,
        }


@dataclass(frozen=True, slots=True)
class GenerationPreparation:
    preparation_id: str
    target: str
    generation_id: str
    source_ref: str
    lock_record: Mapping[str, Any]
    status: PreparationStatus
    resource_ref: str | None
    cleanup: CleanupStatus
    request_id: str
    request_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "lock_record", _freeze(self.lock_record))

    def as_dict(self) -> dict[str, Any]:
        return {
            "preparation_id": self.preparation_id,
            "target": self.target,
            "generation_id": self.generation_id,
            "source_ref": self.source_ref,
            "lock_record": _thaw(self.lock_record),
            "status": self.status,
            "resource_ref": self.resource_ref,
            "cleanup": self.cleanup,
            "request_id": self.request_id,
            "request_digest": self.request_digest,
        }


@dataclass(frozen=True, slots=True)
class GenerationPublication:
    target: str
    revision: int
    generation_id: str
    preparation_id: str
    request_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "revision": self.revision,
            "generation_id": self.generation_id,
            "preparation_id": self.preparation_id,
            "request_id": self.request_id,
        }


@dataclass(frozen=True, slots=True)
class GenerationAdmission:
    admission_id: str
    session_id: str
    target: str | None
    publication_revision: int | None
    generation_id: str
    source_ref: str
    lock_record: Mapping[str, Any]
    controller_epoch: int
    work_id: str
    attempt_id: str
    grant_epoch: int
    status: AdmissionStatus
    input_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "lock_record", _freeze(self.lock_record))

    def as_dict(self) -> dict[str, Any]:
        return {
            "admission_id": self.admission_id,
            "session_id": self.session_id,
            "target": self.target,
            "publication_revision": self.publication_revision,
            "generation_id": self.generation_id,
            "source_ref": self.source_ref,
            "lock_record": _thaw(self.lock_record),
            "controller_epoch": self.controller_epoch,
            "work_id": self.work_id,
            "attempt_id": self.attempt_id,
            "grant_epoch": self.grant_epoch,
            "status": self.status,
            "input_digest": self.input_digest,
        }


@runtime_checkable
class GenerationPreparer(Protocol):
    def prepare(
        self,
        preparation: GenerationPreparation,
        record_resource: Callable[[str], None],
    ) -> str | None:
        """Acquire world resources and record identity before readiness checks."""

    def observe(self, resource_ref: str) -> Literal["ready", "absent", "unknown"]:
        """Observe a previously recorded resource without mutating it."""

    def dispose(self, resource_ref: str) -> Literal["confirmed_absent", "unknown"]:
        """Dispose one resource and report whether absence was confirmed."""


class _DefaultPreparer:
    """Verify every exact Lock artifact without retaining a health resource."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def prepare(
        self,
        preparation: GenerationPreparation,
        record_resource: Callable[[str], None],
    ) -> None:
        lock = EffectiveHarnessLock._from_record(preparation.lock_record)
        cas = FilesystemCAS(self.workspace / ".breadboard" / "module-artifacts")
        try:
            materialize_lock(lock, cas=cas)
        finally:
            cas.close()

    def observe(self, resource_ref: str) -> Literal["ready", "absent", "unknown"]:
        return "unknown"

    def dispose(self, resource_ref: str) -> Literal["confirmed_absent", "unknown"]:
        return "confirmed_absent"


_GUARDS: dict[Path, RLock] = {}
_GUARDS_LOCK = RLock()
_STATE_SCHEMA = "bb.generation_lifecycle.v1"


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


def _default_state() -> dict[str, Any]:
    return {
        "schema_version": _STATE_SCHEMA,
        "next_epoch": 1,
        "targets": {},
        "preparations": {},
        "admissions": {},
        "requests": {},
    }


def _atomic_write(path: Path, state: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(_thaw(state), sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_state(state: object) -> dict[str, Any]:
    if not isinstance(state, Mapping) or state.get("schema_version") != _STATE_SCHEMA:
        raise GenerationLifecycleError(
            "state_corrupt", "generation state has an unsupported schema", "reconcile"
        )
    required = ("targets", "preparations", "admissions", "requests")
    if (
        any(not isinstance(state.get(name), Mapping) for name in required)
        or type(state.get("next_epoch")) is not int
        or state["next_epoch"] < 1
    ):
        raise GenerationLifecycleError(
            "state_corrupt", "generation state collections are invalid", "reconcile"
        )
    return {str(key): _thaw(value) for key, value in state.items()}


class GenerationLifecycle:
    """Durable target publication, generation preparation, and admission owner."""

    def __init__(
        self, workspace: str | Path, preparer: GenerationPreparer | None = None
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self._state_path = self.workspace / ".breadboard" / "generations" / "state.json"
        self._preparer: GenerationPreparer = preparer or _DefaultPreparer(
            self.workspace
        )
        with _GUARDS_LOCK:
            self._guard = _GUARDS.setdefault(self._state_path, RLock())

    @contextmanager
    def _locked(self):
        with self._guard:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            with ProcessLock(self._state_path):
                if self._state_path.exists():
                    try:
                        state = _validate_state(
                            json.loads(self._state_path.read_text(encoding="utf-8"))
                        )
                    except GenerationLifecycleError:
                        raise
                    except Exception as exc:
                        raise GenerationLifecycleError(
                            "state_corrupt",
                            "generation state is not valid JSON",
                            "load",
                        ) from exc
                else:
                    state = _default_state()
                original = _thaw(state)
                yield state
                if state != original:
                    _atomic_write(self._state_path, state)

    def _lock_record(
        self, lock: EffectiveHarnessLock | Mapping[str, Any]
    ) -> EffectiveHarnessLock:
        if isinstance(lock, EffectiveHarnessLock):
            return EffectiveHarnessLock._from_record(lock.as_dict())
        try:
            return EffectiveHarnessLock._from_record(lock)
        except (TypeError, ValueError) as exc:
            raise GenerationLifecycleError(
                "invalid_lock", str(exc), "validate"
            ) from exc

    @staticmethod
    def _preparation(record: Mapping[str, Any]) -> GenerationPreparation:
        return GenerationPreparation(
            preparation_id=record["preparation_id"],
            target=record["target"],
            generation_id=record["generation_id"],
            source_ref=record["source_ref"],
            lock_record=record["lock_record"],
            status=record["status"],
            resource_ref=record.get("resource_ref"),
            cleanup=record["cleanup"],
            request_id=record["request_id"],
            request_digest=record["request_digest"],
        )

    @staticmethod
    def _publication(record: Mapping[str, Any]) -> GenerationPublication:
        return GenerationPublication(
            target=record["target"],
            revision=int(record["revision"]),
            generation_id=record["generation_id"],
            preparation_id=record["preparation_id"],
            request_id=record["request_id"],
        )

    @staticmethod
    def _admission(record: Mapping[str, Any]) -> GenerationAdmission:
        return GenerationAdmission(
            admission_id=record["admission_id"],
            session_id=record["session_id"],
            target=record.get("target"),
            publication_revision=record.get("publication_revision"),
            generation_id=record["generation_id"],
            source_ref=record["source_ref"],
            lock_record=record["lock_record"],
            controller_epoch=record["controller_epoch"],
            work_id=record["work_id"],
            attempt_id=record["attempt_id"],
            grant_epoch=record["grant_epoch"],
            status=record["status"],
            input_digest=record["input_digest"],
        )

    @staticmethod
    def _active_resident_generations(state: Mapping[str, Any], target: str) -> set[str]:
        generations: set[str] = set()
        pointer = state["targets"].get(target)
        if isinstance(pointer, Mapping):
            generations.add(str(pointer["generation_id"]))
        for record in state["preparations"].values():
            if (
                isinstance(record, Mapping)
                and record.get("target") == target
                and record.get("resource_ref") is not None
                and record.get("cleanup") != "confirmed_absent"
            ):
                generations.add(str(record["generation_id"]))
        for record in state["admissions"].values():
            if (
                isinstance(record, Mapping)
                and record.get("target") == target
                and record.get("status") in ("reserved", "materialized")
            ):
                generations.add(str(record["generation_id"]))
        return generations

    @staticmethod
    def _staging_count(state: Mapping[str, Any], target: str) -> int:
        return sum(
            1
            for record in state["preparations"].values()
            if isinstance(record, Mapping)
            and record.get("target") == target
            and record.get("status") == "preparing"
        )

    def _resource_recorded(self, preparation_id: str, resource_ref: str) -> None:
        _required_string(resource_ref, "resource_ref")
        with self._locked() as state:
            record = state["preparations"].get(preparation_id)
            if not isinstance(record, dict):
                raise GenerationLifecycleError(
                    "unknown_preparation",
                    "preparation does not exist",
                    "record_resource",
                )
            previous = record.get("resource_ref")
            if previous is not None and previous != resource_ref:
                raise GenerationLifecycleError(
                    "resource_rebound",
                    "preparation resource identity changed",
                    "record_resource",
                )
            record["resource_ref"] = resource_ref
            record["cleanup"] = "owned"

    def _mark_preparation(
        self,
        preparation_id: str,
        *,
        status: PreparationStatus,
        cleanup: CleanupStatus | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> GenerationPreparation:
        with self._locked() as state:
            record = state["preparations"].get(preparation_id)
            if not isinstance(record, dict):
                raise GenerationLifecycleError(
                    "unknown_preparation", "preparation does not exist", "prepare"
                )
            record["status"] = status
            if cleanup is not None:
                record["cleanup"] = cleanup
            if error is not None:
                record["error"] = dict(error)
            return self._preparation(record)

    @staticmethod
    def _protected_resource_refs(state: Mapping[str, Any]) -> set[str]:
        current_preparation_ids = {
            value.get("preparation_id")
            for value in state["targets"].values()
            if isinstance(value, Mapping)
        }
        active_generation_targets = {
            (value.get("target"), value.get("generation_id"))
            for value in state["admissions"].values()
            if isinstance(value, Mapping)
            and value.get("status") in ("reserved", "materialized")
        }
        return {
            resource_ref
            for preparation_id, value in state["preparations"].items()
            if isinstance(value, Mapping)
            and (
                preparation_id in current_preparation_ids
                or (value.get("target"), value.get("generation_id"))
                in active_generation_targets
            )
            and isinstance((resource_ref := value.get("resource_ref")), str)
        }

    def _dispose_and_mark(
        self, preparation_id: str, resource_ref: str
    ) -> CleanupStatus:
        with self._locked() as state:
            record = state["preparations"].get(preparation_id)
            if (
                not isinstance(record, Mapping)
                or record.get("resource_ref") != resource_ref
            ):
                return "unknown"
            if resource_ref in self._protected_resource_refs(state):
                cleanup = record.get("cleanup")
                return cleanup if cleanup in ("owned", "not_required") else "unknown"
            try:
                result = self._preparer.dispose(resource_ref)
                cleanup: CleanupStatus = (
                    "confirmed_absent"
                    if result == "confirmed_absent"
                    else "unknown"
                )
                if (
                    cleanup == "confirmed_absent"
                    and self._observe(resource_ref) != "absent"
                ):
                    cleanup = "unknown"
            except Exception:
                cleanup = "unknown"
            for candidate in state["preparations"].values():
                if (
                    isinstance(candidate, dict)
                    and candidate.get("resource_ref") == resource_ref
                ):
                    candidate["cleanup"] = cleanup
            return cleanup

    def _observe(self, resource_ref: str) -> Literal["ready", "absent", "unknown"]:
        try:
            result = self._preparer.observe(resource_ref)
            return result if result in ("ready", "absent", "unknown") else "unknown"
        except Exception:
            return "unknown"

    def _request_error(
        self, record: Mapping[str, Any], *, default_stage: str
    ) -> GenerationLifecycleError:
        error = record.get("error")
        if isinstance(error, Mapping):
            return GenerationLifecycleError(
                str(error.get("code", "request_failed")),
                str(error.get("message", "generation request failed")),
                str(error.get("failed_stage", default_stage)),
                error.get("observed_revision"),
            )
        return GenerationLifecycleError(
            "request_failed", "generation request failed", default_stage
        )

    @staticmethod
    def _fail_request_for_preparation(
        state: dict[str, Any],
        preparation: Mapping[str, Any],
    ) -> None:
        request = state["requests"].get(preparation.get("request_id"))
        error = preparation.get("error")
        if isinstance(request, dict) and isinstance(error, Mapping):
            request.update({"status": "failed", "error": dict(error)})

    def _publish_preparation(
        self,
        preparation_id: str,
        *,
        target: str,
        expected_revision: int | None,
        request_id: str,
        request_digest: str,
    ) -> GenerationPublication:
        loser_resource: tuple[str, str] | None = None
        failure: GenerationLifecycleError | None = None
        publication: dict[str, Any] | None = None
        with self._locked() as state:
            request = state["requests"].get(request_id)
            if not isinstance(request, dict) or request.get("digest") != request_digest:
                raise GenerationLifecycleError(
                    "request_conflict",
                    "request_id was reused with different input",
                    "publish",
                )
            if request.get("status") == "published":
                return self._publication(request["publication"])
            preparation_record = state["preparations"].get(preparation_id)
            if (
                not isinstance(preparation_record, dict)
                or preparation_record.get("status") != "ready"
            ):
                raise self._request_error(request, default_stage="publish")
            pointer = state["targets"].get(target)
            observed_revision = (
                int(pointer["revision"]) if isinstance(pointer, Mapping) else 0
            )
            if expected_revision is not None and observed_revision != expected_revision:
                failure = GenerationLifecycleError(
                    "cas_conflict",
                    "target publication revision changed",
                    "publish",
                    observed_revision,
                )
                request.update({"status": "failed", "error": failure.as_dict()})
                resource_ref = preparation_record.get("resource_ref")
                if (
                    request.get("owns_preparation", True)
                    and isinstance(resource_ref, str)
                ):
                    loser_resource = (preparation_id, resource_ref)
            else:
                revision = observed_revision + 1
                publication = {
                    "target": target,
                    "revision": revision,
                    "generation_id": preparation_record["generation_id"],
                    "preparation_id": preparation_id,
                    "request_id": request_id,
                }
                state["targets"][target] = publication
                request.update({"status": "published", "publication": publication})
        if failure is not None:
            if loser_resource is not None:
                self._dispose_and_mark(*loser_resource)
            raise failure
        if publication is None:
            raise GenerationLifecycleError(
                "state_corrupt", "publication transaction produced no result", "publish"
            )
        return self._publication(publication)

    def prepare_and_publish(
        self,
        target: str,
        lock: EffectiveHarnessLock | Mapping[str, Any],
        source_ref: str,
        expected_revision: int | None,
        request_id: str,
    ) -> GenerationPublication:
        target = _required_target(target)
        source_ref = _required_string(source_ref, "source_ref")
        request_id = _required_string(request_id, "request_id")
        if expected_revision is not None and (
            type(expected_revision) is not int or expected_revision < 0
        ):
            raise ValueError("expected_revision must be a non-negative integer or None")
        effective = self._lock_record(lock)
        lock_record = effective.as_dict()
        request_digest = _record_digest(
            target=target,
            lock_record=lock_record,
            source_ref=source_ref,
            expected_revision=expected_revision,
            request_id=request_id,
        )
        preparation_id: str
        preparation: GenerationPreparation | None = None
        existing_resource: str | None = None
        preparation_ready = False
        with self._locked() as state:
            request = state["requests"].get(request_id)
            if isinstance(request, Mapping):
                if request.get("digest") != request_digest:
                    raise GenerationLifecycleError(
                        "request_conflict",
                        "request_id was reused with different input",
                        "prepare",
                    )
                if request.get("status") == "published":
                    return self._publication(request["publication"])
                if request.get("status") == "failed":
                    raise self._request_error(request, default_stage="prepare")
                preparation_id = str(request["preparation_id"])
                previous = state["preparations"].get(preparation_id)
                if not isinstance(previous, Mapping):
                    raise GenerationLifecycleError(
                        "state_corrupt", "request preparation is missing", "prepare"
                    )
                preparation_ready = previous.get("status") == "ready"
                if preparation_ready:
                    preparation = self._preparation(previous)
                    resource = previous.get("resource_ref")
                    existing_resource = resource if isinstance(resource, str) else None
            else:
                pointer = state["targets"].get(target)
                current = (
                    state["preparations"].get(pointer.get("preparation_id"))
                    if isinstance(pointer, Mapping)
                    else None
                )
                reusable = (
                    current
                    if isinstance(current, Mapping)
                    and current.get("generation_id") == effective.generation_id
                    and current.get("status") == "ready"
                    and current.get("cleanup") in ("owned", "not_required")
                    else None
                )
                if reusable is None:
                    for candidate in reversed(state["preparations"].values()):
                        if (
                            isinstance(candidate, Mapping)
                            and candidate.get("target") == target
                            and candidate.get("generation_id")
                            == effective.generation_id
                            and candidate.get("status") == "ready"
                            and candidate.get("cleanup") in ("owned", "not_required")
                        ):
                            reusable = candidate
                            break
                if reusable is not None:
                    preparation_id = str(reusable["preparation_id"])
                    preparation = self._preparation(reusable)
                    preparation_ready = True
                    resource = reusable.get("resource_ref")
                    existing_resource = (
                        resource if isinstance(resource, str) else None
                    )
                    state["requests"][request_id] = {
                        "digest": request_digest,
                        "status": "preparing",
                        "preparation_id": preparation_id,
                        "owns_preparation": False,
                    }
                else:
                    if self._staging_count(state, target):
                        raise GenerationLifecycleError(
                            "capacity_pressure",
                            "target already has a staging candidate",
                            "prepare",
                        )
                    residents = self._active_resident_generations(state, target)
                    if len(residents) >= 2:
                        raise GenerationLifecycleError(
                            "capacity_pressure",
                            "target resident generation capacity is full",
                            "prepare",
                        )
                    preparation_id = _new_id("preparation")
                    preparation_record = {
                        "preparation_id": preparation_id,
                        "target": target,
                        "generation_id": effective.generation_id,
                        "source_ref": source_ref,
                        "lock_record": lock_record,
                        "status": "preparing",
                        "resource_ref": None,
                        "cleanup": "not_required",
                        "request_id": request_id,
                        "request_digest": request_digest,
                    }
                    state["preparations"][preparation_id] = preparation_record
                    preparation = self._preparation(preparation_record)
                    state["requests"][request_id] = {
                        "digest": request_digest,
                        "status": "preparing",
                        "preparation_id": preparation_id,
                        "owns_preparation": True,
                    }
        if preparation_ready and existing_resource is None:
            publication = self._publish_preparation(
                preparation_id,
                target=target,
                expected_revision=expected_revision,
                request_id=request_id,
                request_digest=request_digest,
            )
            self._retire_target_resources(target)
            return publication
        if preparation is None:
            raise GenerationLifecycleError(
                "request_in_progress",
                "generation request preparation is already in progress",
                "prepare",
            )
        if existing_resource is not None:
            observed = self._observe(existing_resource)
            if observed == "ready":
                publication = self._publish_preparation(
                    preparation_id,
                    target=target,
                    expected_revision=expected_revision,
                    request_id=request_id,
                    request_digest=request_digest,
                )
                self._retire_target_resources(target)
                return publication
            self._mark_preparation(
                preparation_id,
                status="failed",
                cleanup="unknown" if observed == "unknown" else "confirmed_absent",
                error={
                    "code": "resource_missing",
                    "message": "recorded preparation resource is not ready",
                    "failed_stage": "observe",
                },
            )
            with self._locked() as state:
                state["requests"][request_id].update(
                    {
                        "status": "failed",
                        "error": state["preparations"][preparation_id].get("error"),
                    }
                )
            raise GenerationLifecycleError(
                "resource_missing",
                "recorded preparation resource is not ready",
                "observe",
            )
        try:
            resource_ref = self._preparer.prepare(
                preparation,
                lambda value: self._resource_recorded(preparation_id, value),
            )
            if resource_ref is not None:
                self._resource_recorded(preparation_id, resource_ref)
            with self._locked() as state:
                current = state["preparations"].get(preparation_id)
                resource_ref = (
                    current.get("resource_ref")
                    if isinstance(current, Mapping)
                    else resource_ref
                )
            if resource_ref is not None:
                observed = self._observe(resource_ref)
                if observed != "ready":
                    code = (
                        "resource_missing"
                        if observed == "absent"
                        else "resource_unknown"
                    )
                    error = {
                        "code": code,
                        "message": "prepared resource did not become ready",
                        "failed_stage": "observe",
                    }
                    self._mark_preparation(
                        preparation_id, status="failed", cleanup="owned", error=error
                    )
                    self._dispose_and_mark(preparation_id, resource_ref)
                    with self._locked() as state:
                        request = state["requests"].get(request_id)
                        if isinstance(request, dict):
                            request.update({"status": "failed", "error": error})
                    raise GenerationLifecycleError(code, error["message"], "observe")
            self._mark_preparation(
                preparation_id,
                status="ready",
                cleanup="owned" if resource_ref else "not_required",
            )
        except GenerationLifecycleError as exc:
            if exc.code in {"resource_missing", "resource_unknown"}:
                raise
            with self._locked() as state:
                current = state["preparations"].get(preparation_id)
                recorded_resource = (
                    current.get("resource_ref")
                    if isinstance(current, Mapping)
                    else None
                )
            error = exc.as_dict()
            self._mark_preparation(
                preparation_id,
                status="failed",
                cleanup="owned"
                if isinstance(recorded_resource, str)
                else "not_required",
                error=error,
            )
            if isinstance(recorded_resource, str):
                self._dispose_and_mark(preparation_id, recorded_resource)
            with self._locked() as state:
                request = state["requests"].get(request_id)
                if isinstance(request, dict):
                    request.update({"status": "failed", "error": error})
            raise
        except Exception as exc:
            with self._locked() as state:
                current = state["preparations"].get(preparation_id)
                recorded_resource = (
                    current.get("resource_ref")
                    if isinstance(current, Mapping)
                    else resource_ref
                )
            error = {
                "code": "prepare_failed",
                "message": str(exc),
                "failed_stage": "prepare",
            }
            self._mark_preparation(
                preparation_id,
                status="failed",
                cleanup="owned"
                if isinstance(recorded_resource, str)
                else "not_required",
                error=error,
            )
            if isinstance(recorded_resource, str):
                self._dispose_and_mark(preparation_id, recorded_resource)
            with self._locked() as state:
                request = state["requests"].get(request_id)
                if isinstance(request, dict):
                    request.update({"status": "failed", "error": error})
            raise GenerationLifecycleError(
                "prepare_failed", str(exc), "prepare"
            ) from exc
        publication = self._publish_preparation(
            preparation_id,
            target=target,
            expected_revision=expected_revision,
            request_id=request_id,
            request_digest=request_digest,
        )
        self._retire_target_resources(target)
        return publication

    def current(self, target: str) -> GenerationPublication | None:
        target = _required_target(target)
        with self._locked() as state:
            pointer = state["targets"].get(target)
            return self._publication(pointer) if isinstance(pointer, Mapping) else None

    def inspect_generation(self, generation_id: str) -> dict[str, Any]:
        """Return the secret-free durable publication, admission, and cleanup projection."""
        generation_id = _required_string(generation_id, "generation_id")
        with self._locked() as state:
            publications = [
                self._publication(record).as_dict()
                for record in state["targets"].values()
                if isinstance(record, Mapping)
                and record.get("generation_id") == generation_id
            ]
            preparations = [
                {
                    "preparation_id": record["preparation_id"],
                    "target": record["target"],
                    "status": record["status"],
                    "cleanup": record["cleanup"],
                    "request_id": record["request_id"],
                    "resource_recorded": isinstance(record.get("resource_ref"), str),
                }
                for record in state["preparations"].values()
                if isinstance(record, Mapping)
                and record.get("generation_id") == generation_id
            ]
            admissions = [
                {
                    "admission_id": record["admission_id"],
                    "session_id": record["session_id"],
                    "target": record.get("target"),
                    "publication_revision": record.get("publication_revision"),
                    "status": record["status"],
                    "work_id": record["work_id"],
                    "attempt_id": record["attempt_id"],
                    "controller_epoch": record["controller_epoch"],
                    "grant_epoch": record["grant_epoch"],
                }
                for record in state["admissions"].values()
                if isinstance(record, Mapping)
                and record.get("generation_id") == generation_id
            ]
        publications.sort(key=lambda row: (row["target"], row["revision"]))
        preparations.sort(key=lambda row: row["preparation_id"])
        admissions.sort(key=lambda row: row["admission_id"])
        pinned = sum(
            row["status"] in ("reserved", "materialized") for row in admissions
        )
        known = bool(publications or preparations or admissions)
        return {
            "generation_id": generation_id,
            "publications": publications,
            "preparations": preparations,
            "admissions": admissions,
            "retirement": {
                "known": known,
                "pinned_session_count": pinned,
                "cleanup_states": sorted({row["cleanup"] for row in preparations}),
                "retired": known
                and not publications
                and pinned == 0
                and all(
                    row["cleanup"] in ("confirmed_absent", "not_required")
                    for row in preparations
                ),
            },
        }

    def _find_admission_by_session(
        self,
        state: Mapping[str, Any],
        session_id: str,
        input_digest: str,
    ) -> GenerationAdmission | None:
        for record in state["admissions"].values():
            if not isinstance(record, Mapping):
                continue
            if record.get("session_id") != session_id:
                continue
            if record.get("status") == "released":
                continue
            if record.get("input_digest") != input_digest:
                raise GenerationLifecycleError(
                    "session_conflict",
                    "session_id was reused with different input",
                    "admission",
                )
            return self._admission(record)
        return None

    def _new_admission_record(
        self,
        *,
        session_id: str,
        target: str | None,
        publication_revision: int | None,
        generation_id: str,
        source_ref: str,
        lock_record: Mapping[str, Any],
        input_digest: str,
        controller_epoch: int,
        grant_epoch: int,
    ) -> dict[str, Any]:
        return {
            "admission_id": _new_id("admission"),
            "session_id": session_id,
            "target": target,
            "publication_revision": publication_revision,
            "generation_id": generation_id,
            "source_ref": source_ref,
            "lock_record": _thaw(lock_record),
            "controller_epoch": controller_epoch,
            "work_id": _new_id("work"),
            "attempt_id": _new_id("attempt"),
            "grant_epoch": grant_epoch,
            "status": "reserved",
            "input_digest": input_digest,
        }

    def reserve_target_admission(
        self,
        target: str,
        session_id: str,
        input_digest: str,
    ) -> GenerationAdmission:
        target = _required_target(target)
        session_id = _required_string(session_id, "session_id")
        input_digest = _required_string(input_digest, "input_digest")
        with self._locked() as state:
            replay = self._find_admission_by_session(
                state,
                session_id,
                input_digest,
            )
            if replay is not None:
                if replay.target != target:
                    raise GenerationLifecycleError(
                        "session_conflict",
                        "session already targets another publication",
                        "admission",
                    )
                return replay
            pointer = state["targets"].get(target)
            if not isinstance(pointer, Mapping):
                raise GenerationLifecycleError(
                    "publication_missing",
                    "target has no publication",
                    "admission",
                )
            preparation = state["preparations"].get(pointer["preparation_id"])
            if (
                not isinstance(preparation, Mapping)
                or preparation.get("status") != "ready"
            ):
                raise GenerationLifecycleError(
                    "publication_unavailable",
                    "published preparation is not ready",
                    "admission",
                )
            controller_epoch = state["next_epoch"]
            grant_epoch = controller_epoch + 1
            state["next_epoch"] = grant_epoch + 1
            record = self._new_admission_record(
                session_id=session_id,
                target=target,
                publication_revision=int(pointer["revision"]),
                generation_id=preparation["generation_id"],
                source_ref=preparation["source_ref"],
                lock_record=preparation["lock_record"],
                input_digest=input_digest,
                controller_epoch=controller_epoch,
                grant_epoch=grant_epoch,
            )
            state["admissions"][record["admission_id"]] = record
            return self._admission(record)

    def reserve_explicit_admission(
        self,
        lock: EffectiveHarnessLock | Mapping[str, Any],
        source_ref: str,
        session_id: str,
        input_digest: str,
    ) -> GenerationAdmission:
        source_ref = _required_string(source_ref, "source_ref")
        session_id = _required_string(session_id, "session_id")
        input_digest = _required_string(input_digest, "input_digest")
        effective = self._lock_record(lock)
        with self._locked() as state:
            replay = self._find_admission_by_session(
                state,
                session_id,
                input_digest,
            )
            if replay is not None:
                if (
                    replay.target is not None
                    or replay.generation_id != effective.generation_id
                ):
                    raise GenerationLifecycleError(
                        "session_conflict",
                        "session already targets another Lock",
                        "admission",
                    )
                return replay
            controller_epoch = state["next_epoch"]
            grant_epoch = controller_epoch + 1
            state["next_epoch"] = grant_epoch + 1
            record = self._new_admission_record(
                session_id=session_id,
                target=None,
                publication_revision=None,
                generation_id=effective.generation_id,
                source_ref=source_ref,
                lock_record=effective.as_dict(),
                input_digest=input_digest,
                controller_epoch=controller_epoch,
                grant_epoch=grant_epoch,
            )
            state["admissions"][record["admission_id"]] = record
            return self._admission(record)

    def reserve_adoption_admission(
        self,
        lock: EffectiveHarnessLock | Mapping[str, Any],
        source_ref: str,
        session_id: str,
        input_digest: str,
    ) -> GenerationAdmission:
        """Reserve an exact replacement while the Session's old admission stays live."""
        source_ref = _required_string(source_ref, "source_ref")
        session_id = _required_string(session_id, "session_id")
        input_digest = _required_string(input_digest, "input_digest")
        effective = self._lock_record(lock)
        with self._locked() as state:
            for value in state["admissions"].values():
                if not isinstance(value, Mapping):
                    continue
                if (
                    value.get("session_id") == session_id
                    and value.get("status") != "released"
                    and value.get("input_digest") == input_digest
                ):
                    replay = self._admission(value)
                    if (
                        replay.target is not None
                        or replay.generation_id != effective.generation_id
                        or replay.source_ref != source_ref
                    ):
                        raise GenerationLifecycleError(
                            "session_conflict",
                            "adoption request was reused with another Lock",
                            "admission",
                        )
                    return replay
            controller_epoch = state["next_epoch"]
            grant_epoch = controller_epoch + 1
            state["next_epoch"] = grant_epoch + 1
            record = self._new_admission_record(
                session_id=session_id,
                target=None,
                publication_revision=None,
                generation_id=effective.generation_id,
                source_ref=source_ref,
                lock_record=effective.as_dict(),
                input_digest=input_digest,
                controller_epoch=controller_epoch,
                grant_epoch=grant_epoch,
            )
            state["admissions"][record["admission_id"]] = record
            return self._admission(record)

    def mark_materialized(self, admission_id: str) -> GenerationAdmission:
        admission_id = _required_string(admission_id, "admission_id")
        with self._locked() as state:
            record = state["admissions"].get(admission_id)
            if not isinstance(record, dict):
                raise GenerationLifecycleError(
                    "unknown_admission", "admission does not exist", "materialize"
                )
            if record["status"] == "released":
                raise GenerationLifecycleError(
                    "admission_released",
                    "released admission cannot be materialized",
                    "materialize",
                )
            record["status"] = "materialized"
            return self._admission(record)

    def require_dispatch(
        self,
        admission_id: str,
        generation_id: str,
        work_id: str,
        attempt_id: str,
        controller_epoch: int,
        grant_epoch: int,
    ) -> GenerationAdmission:
        admission_id = _required_string(admission_id, "admission_id")
        generation_id = _required_string(generation_id, "generation_id")
        work_id = _required_string(work_id, "work_id")
        attempt_id = _required_string(attempt_id, "attempt_id")
        for name, value in (
            ("controller_epoch", controller_epoch),
            ("grant_epoch", grant_epoch),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        with self._locked() as state:
            record = state["admissions"].get(admission_id)
            if not isinstance(record, Mapping):
                raise GenerationLifecycleError(
                    "unknown_admission",
                    "admission does not exist",
                    "dispatch",
                )
            checks = (
                record.get("status") == "materialized",
                record.get("generation_id") == generation_id,
                record.get("work_id") == work_id,
                record.get("attempt_id") == attempt_id,
                record.get("controller_epoch") == controller_epoch,
                record.get("grant_epoch") == grant_epoch,
            )
            if not all(checks):
                raise GenerationLifecycleError(
                    "stale_dispatch",
                    "dispatch fence does not match the retained admission",
                    "dispatch",
                )
            return self._admission(record)

    def release(
        self, admission_id: str, cleanup_confirmed: bool
    ) -> GenerationAdmission:
        admission_id = _required_string(admission_id, "admission_id")
        if type(cleanup_confirmed) is not bool:
            raise TypeError("cleanup_confirmed must be a bool")
        with self._locked() as state:
            record = state["admissions"].get(admission_id)
            if not isinstance(record, dict):
                raise GenerationLifecycleError(
                    "unknown_admission", "admission does not exist", "release"
                )
            if record["status"] == "released":
                return self._admission(record)
            if not cleanup_confirmed:
                return self._admission(record)
            record["status"] = "released"
            result = self._admission(record)
            target = record.get("target")
        if isinstance(target, str):
            self._retire_target_resources(target)
        return result

    def _retire_target_resources(self, target: str) -> None:
        candidates: list[tuple[str, str]] = []
        with self._locked() as state:
            pointer = state["targets"].get(target)
            current_preparation = (
                state["preparations"].get(pointer.get("preparation_id"))
                if isinstance(pointer, Mapping)
                else None
            )
            current_generation = (
                pointer.get("generation_id")
                if isinstance(pointer, Mapping)
                and isinstance(current_preparation, Mapping)
                and current_preparation.get("status") == "ready"
                and current_preparation.get("cleanup") in ("owned", "not_required")
                else None
            )
            pinned_admissions = [
                record
                for record in state["admissions"].values()
                if isinstance(record, Mapping)
                and record.get("target") == target
                and record.get("status") in ("reserved", "materialized")
            ]
            pinned = {record.get("generation_id") for record in pinned_admissions}
            protected_resources = self._protected_resource_refs(state)
            for preparation_id, record in state["preparations"].items():
                if not isinstance(record, dict) or record.get("target") != target:
                    continue
                request = state["requests"].get(record.get("request_id"))
                if (
                    isinstance(request, Mapping)
                    and request.get("status") == "preparing"
                ):
                    continue
                resource_ref = record.get("resource_ref")
                if not isinstance(resource_ref, str) or record.get("cleanup") in (
                    "confirmed_absent",
                    "unknown",
                ):
                    continue
                if (
                    resource_ref in protected_resources
                    or record.get("generation_id") == current_generation
                    or record.get("generation_id") in pinned
                ):
                    continue
                candidates.append((preparation_id, resource_ref))
        for preparation_id, resource_ref in candidates:
            self._dispose_and_mark(preparation_id, resource_ref)

    def reconcile(self) -> tuple[GenerationPreparation, ...]:
        resources: list[tuple[str, str]] = []
        with self._locked() as state:
            for preparation_id, record in state["preparations"].items():
                if not isinstance(record, Mapping):
                    continue
                resource_ref = record.get("resource_ref")
                if not isinstance(resource_ref, str):
                    if record.get("status") == "preparing":
                        mutable = state["preparations"][preparation_id]
                        mutable["status"] = "failed"
                        mutable["cleanup"] = "not_required"
                        mutable["error"] = {
                            "code": "prepare_interrupted",
                            "message": "preparation was interrupted before resource identity was recorded",
                            "failed_stage": "reconcile",
                        }
                        self._fail_request_for_preparation(state, mutable)
                    continue
                resources.append((preparation_id, resource_ref))
        for preparation_id, resource_ref in resources:
            observation = self._observe(resource_ref)
            with self._locked() as state:
                record = state["preparations"].get(preparation_id)
                if not isinstance(record, dict):
                    continue
                if observation == "ready" and record.get("status") == "preparing":
                    record["status"] = "ready"
                    record["cleanup"] = "owned"
                elif observation == "absent":
                    record["status"] = "failed"
                    record["cleanup"] = "confirmed_absent"
                    record["error"] = {
                        "code": "resource_missing",
                        "message": "recorded resource is absent after restart",
                        "failed_stage": "reconcile",
                    }
                elif observation == "unknown":
                    record["status"] = "failed"
                    record["cleanup"] = "unknown"
                    record["error"] = {
                        "code": "resource_unknown",
                        "message": "recorded resource could not be observed after restart",
                        "failed_stage": "reconcile",
                    }
                if record.get("status") == "failed":
                    self._fail_request_for_preparation(state, record)
        targets: set[str] = set()
        with self._locked() as state:
            targets.update(str(target) for target in state["targets"])
            targets.update(
                str(record["target"])
                for record in state["preparations"].values()
                if isinstance(record, Mapping) and isinstance(record.get("target"), str)
            )
        for target in targets:
            self._retire_target_resources(target)
        with self._locked() as state:
            return tuple(
                self._preparation(record)
                for record in state["preparations"].values()
                if isinstance(record, Mapping)
            )


__all__ = [
    "GenerationAdmission",
    "GenerationLifecycle",
    "GenerationLifecycleError",
    "GenerationPreparer",
    "GenerationPreparation",
    "GenerationPublication",
]
