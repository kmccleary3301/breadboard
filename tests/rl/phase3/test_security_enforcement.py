from __future__ import annotations

import pytest

from breadboard.rl.phase2.hardening import ArtifactEgressRequest, EgressPolicy, redact_mapping
from breadboard.rl.phase3.security_enforcement import enforce_artifact_egress, enforce_command_request, enforce_workspace_path


def policy() -> EgressPolicy:
    return EgressPolicy(allowed_prefixes=("ws/replay",), max_artifact_bytes=100)


def test_denied_absolute_path() -> None:
    with pytest.raises(PermissionError):
        enforce_workspace_path("/tmp/x", tenant_id="t", workspace_id="ws")


def test_denied_parent_escape() -> None:
    with pytest.raises(PermissionError):
        enforce_workspace_path("ws/../secret", tenant_id="t", workspace_id="ws")


def test_denied_egress_classification() -> None:
    with pytest.raises(PermissionError, match="artifact egress denied"):
        enforce_artifact_egress(ArtifactEgressRequest("ws/replay/a.json", 10, "secret"), policy())


def test_denied_oversize_artifact() -> None:
    with pytest.raises(PermissionError):
        enforce_artifact_egress(ArtifactEgressRequest("ws/replay/a.json", 101, "tenant_internal"), policy())


def test_denied_destructive_command() -> None:
    with pytest.raises(PermissionError):
        enforce_command_request(["rm", "-rf", "/"], workspace_relative_path="ws", workspace_id="ws")


def test_redaction_secret_and_path_values() -> None:
    result = redact_mapping({"api_token": "abc", "path": "/tmp/secret", "ok": "value"})
    assert result["api_token"] == "<redacted>"
    assert result["path"] == "<redacted>"
    assert result["ok"] == "value"


def test_valid_artifact_replay_path() -> None:
    assert enforce_workspace_path("ws/replay/a.json", tenant_id="t", workspace_id="ws").as_posix() == "ws/replay/a.json"
    enforce_artifact_egress(ArtifactEgressRequest("ws/replay/a.json", 10, "tenant_internal"), policy())
