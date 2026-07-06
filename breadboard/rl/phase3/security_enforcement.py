from __future__ import annotations

from collections.abc import Sequence
from pathlib import PurePosixPath

from breadboard.rl.phase2.hardening import (
    ArtifactEgressRequest,
    DestructiveActionRequest,
    EgressPolicy,
    evaluate_artifact_egress,
    guard_destructive_action,
    redact_mapping,
    workspace_isolated,
)

__all__ = [
    "ArtifactEgressRequest",
    "EgressPolicy",
    "enforce_artifact_egress",
    "enforce_workspace_path",
    "enforce_command_request",
    "redact_mapping",
    "workspace_isolated",
]


def enforce_artifact_egress(request: ArtifactEgressRequest, policy: EgressPolicy) -> None:
    result = evaluate_artifact_egress(request, policy)
    if not result["allowed"]:
        reason = "; ".join(result["reasons"])
        raise PermissionError(f"artifact egress denied: {reason}")


def enforce_workspace_path(path: str, *, tenant_id: str, workspace_id: str) -> PurePosixPath:
    del tenant_id
    normalized = path.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if not normalized or normalized.startswith("~") or pure.is_absolute() or ".." in pure.parts:
        raise PermissionError("workspace path denied: path must be workspace-relative")
    if not pure.parts or pure.parts[0] != workspace_id:
        raise PermissionError("workspace path denied: path must start with workspace_id")
    return pure


def enforce_command_request(command: Sequence[str], *, workspace_relative_path: str, workspace_id: str) -> None:
    command_text = " ".join(str(part) for part in command)
    result = guard_destructive_action(
        DestructiveActionRequest(action_id="phase3_command_request", command=command_text, workspace_relative_path=workspace_relative_path),
        workspace_id=workspace_id,
    )
    if not result["allowed"]:
        raise PermissionError("command request denied: " + "; ".join(result["reasons"]))
