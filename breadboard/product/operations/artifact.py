from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from breadboard.product.operations.model import (
    OperationContext,
    OperationResult,
    from_exception,
    portable_ref,
)
from breadboard.product.runtime.artifacts import (
    ArtifactRef,
    list_workspace_artifacts,
    read_workspace_artifact,
    workspace_artifact_ref,
)


@dataclass(frozen=True, slots=True)
class ListArtifactsRequest:
    pass


@dataclass(frozen=True, slots=True)
class GetArtifactRequest:
    reference: str
    size: int | None = None
    media_type: str | None = None


@dataclass(frozen=True, slots=True)
class VerifyArtifactRequest:
    reference: str
    size: int | None = None
    media_type: str | None = None


_DEFAULT_MEDIA_TYPE = "application/octet-stream"


def _artifact_ref(
    reference: str,
    context: OperationContext,
    *,
    size: int | None,
    media_type: str | None,
) -> ArtifactRef:
    if size is not None:
        return ArtifactRef(reference, int(size), media_type or _DEFAULT_MEDIA_TYPE)
    try:
        return workspace_artifact_ref(
            context.workspace,
            reference,
            media_type=media_type or _DEFAULT_MEDIA_TYPE,
        )
    except OSError as error:
        raise PermissionError("artifact path is unavailable") from error


def _read_artifact(context: OperationContext, reference: ArtifactRef) -> bytes:
    try:
        return read_workspace_artifact(context.workspace, reference)
    except OSError as error:
        raise PermissionError("artifact path is unavailable") from error


def list_artifacts(
    _request: ListArtifactsRequest,
    context: OperationContext,
) -> OperationResult:
    command = ("artifact", "list")
    try:
        root = context.workspace / ".breadboard" / "artifacts" / "sha256"
        rows = [ref.as_dict() for ref in list_workspace_artifacts(context.workspace)]
        return OperationResult.success(
            command,
            {"artifacts": rows, "count": len(rows)},
            refs=[portable_ref(root, context.workspace)] if rows else [],
            stage="artifact.list",
        )
    except OSError:
        return from_exception(
            command,
            PermissionError("artifact store is unavailable"),
            "artifact.list",
        )
    except Exception as error:
        return from_exception(command, error, "artifact.list")


def get_artifact(
    request: GetArtifactRequest,
    context: OperationContext,
    *,
    command_name: Literal["get", "show"] = "get",
) -> OperationResult:
    command = ("artifact", command_name)
    stage = f"artifact.{command_name}"
    try:
        reference = _artifact_ref(
            request.reference,
            context,
            size=request.size,
            media_type=request.media_type,
        )
        body = _read_artifact(context, reference)
        return OperationResult.success(
            command,
            {"artifact": reference.as_dict(), "bytes": len(body)},
            hashes={"artifact": reference.digest},
            stage=stage,
        )
    except Exception as error:
        return from_exception(command, error, stage)


def verify_artifact(
    request: VerifyArtifactRequest,
    context: OperationContext,
) -> OperationResult:
    command = ("artifact", "verify")
    try:
        reference = _artifact_ref(
            request.reference,
            context,
            size=request.size,
            media_type=request.media_type,
        )
        body = _read_artifact(context, reference)
        digest = "sha256:" + hashlib.sha256(body).hexdigest()
        return OperationResult.success(
            command,
            {"artifact": reference.as_dict(), "verified": digest == reference.digest},
            hashes={"artifact": digest},
            stage="artifact.verify",
        )
    except Exception as error:
        return from_exception(command, error, "artifact.verify")
