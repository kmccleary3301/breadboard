from __future__ import annotations

from pathlib import Path

from breadboard.product.operations.artifact import (
    GetArtifactRequest,
    ListArtifactsRequest,
    VerifyArtifactRequest,
    get_artifact as run_get_artifact,
    list_artifacts as run_list_artifacts,
    verify_artifact as run_verify_artifact,
)
from breadboard.product.operations.model import (
    OperationContext,
    OperationResult,
    from_exception,
    portable_ref,
)
from breadboard.product.runtime.artifacts import (
    ArtifactRef as LegacyArtifactRef,
    discard_workspace_artifact as legacy_discard_workspace_artifact,
    put_workspace_artifact as legacy_put_workspace_artifact,
    workspace_artifact_ref as legacy_workspace_artifact_ref,
)


def _workspace(args) -> Path:
    return Path(getattr(args, "workspace", None) or Path.cwd()).expanduser().resolve()


def _operation_context(args) -> OperationContext:
    return OperationContext(
        workspace=_workspace(args),
        path_policy="explicit-local",
        reference_root=Path.cwd().resolve(),
    )


def _legacy_ref(value, workspace: Path, size=None, media_type=None):
    if size is not None:
        return LegacyArtifactRef(
            value,
            int(size),
            media_type or "application/octet-stream",
        )
    try:
        return legacy_workspace_artifact_ref(
            workspace,
            value,
            media_type=media_type or "application/octet-stream",
        )
    except OSError as error:
        raise PermissionError("artifact path is unavailable") from error


def put(args):
    workspace = _workspace(args)
    source = Path(args.SOURCE).expanduser()
    try:
        body = source.read_bytes()
    except OSError:
        return from_exception(
            ["artifact", "put"],
            PermissionError("artifact source is unavailable"),
            "artifact.put",
        )
    try:
        reference = legacy_put_workspace_artifact(
            workspace,
            body,
            media_type=args.media_type,
        )
        return OperationResult.success(
            ["artifact", "put"],
            {
                "artifact": reference.as_dict(),
                "source": portable_ref(source, workspace),
            },
            refs=[portable_ref(source, workspace)],
            hashes={"artifact": reference.digest},
            stage="artifact.put",
        )
    except OSError:
        return from_exception(
            ["artifact", "put"],
            PermissionError("artifact store is unavailable"),
            "artifact.put",
        )
    except Exception as error:
        return from_exception(["artifact", "put"], error, "artifact.put")


def delete(args):
    workspace = _workspace(args)
    try:
        reference = _legacy_ref(
            args.REF,
            workspace,
            getattr(args, "size", None),
            getattr(args, "media_type", None),
        )
        legacy_discard_workspace_artifact(workspace, reference)
        return OperationResult.success(
            ["artifact", "delete"],
            {"artifact": reference.as_dict(), "deleted": True},
            hashes={"artifact": reference.digest},
            stage="artifact.delete",
        )
    except OSError:
        return from_exception(
            ["artifact", "delete"],
            PermissionError("artifact path is unavailable"),
            "artifact.delete",
        )
    except Exception as error:
        return from_exception(["artifact", "delete"], error, "artifact.delete")


def list_artifacts(args):
    return run_list_artifacts(
        ListArtifactsRequest(),
        _operation_context(args),
    )


def get(args, command_name="get"):
    return run_get_artifact(
        GetArtifactRequest(
            reference=args.REF,
            size=getattr(args, "size", None),
            media_type=getattr(args, "media_type", None),
        ),
        _operation_context(args),
        command_name=command_name,
    )


def verify(args):
    return run_verify_artifact(
        VerifyArtifactRequest(
            reference=args.REF,
            size=getattr(args, "size", None),
            media_type=getattr(args, "media_type", None),
        ),
        _operation_context(args),
    )
