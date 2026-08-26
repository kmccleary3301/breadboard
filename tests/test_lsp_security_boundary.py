from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from breadboard.lsp_manager import LSPManagerV2, LSPServer
from breadboard_engine.integration.lsp_manager import LSPManager
from breadboard_engine.security import WorkspaceFilesystem, WorkspacePathError


class _NotificationClient:
    def __init__(self) -> None:
        self.notifications: list[tuple[str, dict]] = []

    def _send_notification(self, method: str, payload: dict) -> None:
        self.notifications.append((method, payload))


def _local_lsp_server(workspace: Path) -> tuple[object, _NotificationClient]:
    server_type = LSPServer.__ray_metadata__.modified_class
    server = object.__new__(server_type)
    server.initialized = True
    client = _NotificationClient()
    server.client = client
    server._workspace_files = WorkspaceFilesystem(workspace)
    server.workspace_root = str(server._workspace_files.root)
    return server, client


def test_canonical_lsp_reads_only_descriptor_admitted_workspace_files(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    safe = workspace / "safe.py"
    safe.write_text("print('safe')\n", encoding="utf-8")
    protected = tmp_path / "credentials.sqlite3"
    canary = "lsp-descriptor-canary-e7"
    protected.write_text(canary, encoding="utf-8")
    linked = workspace / "linked.py"
    linked.symlink_to(protected)
    hardlinked = workspace / "hardlinked.py"
    os.link(protected, hardlinked)
    server, client = _local_lsp_server(workspace)

    try:
        opened = server.open_document(str(safe))
        linked_result = server.open_document(str(linked))
        hardlink_result = server.open_document(str(hardlinked))
        outside_result = server.open_document(str(protected))
    finally:
        server._workspace_files.close()

    assert opened["status"] == "opened"
    assert "print('safe')" in json.dumps(client.notifications)
    assert "error" in linked_result
    assert "error" in hardlink_result
    assert "error" in outside_result
    assert canary not in json.dumps(
        (client.notifications, linked_result, hardlink_result, outside_result)
    )


def test_canonical_lsp_manager_admission_rejects_linked_or_external_files(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    safe = workspace / "safe.py"
    safe.write_text("pass\n", encoding="utf-8")
    protected = tmp_path / "credential"
    protected.write_text("credential", encoding="utf-8")
    (workspace / "linked.py").symlink_to(protected)

    manager_type = LSPManagerV2.__ray_metadata__.modified_class
    manager = object.__new__(manager_type)
    filesystem = WorkspaceFilesystem(workspace)
    manager.roots = {str(filesystem.root)}
    manager._root_filesystems = {str(filesystem.root): filesystem}
    try:
        assert manager._admit_file(str(safe)) == str(safe)
        with pytest.raises(WorkspacePathError):
            manager._admit_file(str(workspace / "linked.py"))
        with pytest.raises(WorkspacePathError):
            manager._admit_file(str(protected))
    finally:
        filesystem.close()


def test_integration_lsp_reads_absolute_workspace_file_through_descriptor(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    safe = workspace / "safe.py"
    safe.write_text("value = 1\n", encoding="utf-8")
    protected = tmp_path / "credential.py"
    canary = "integration-lsp-canary-e7"
    protected.write_text(canary, encoding="utf-8")
    linked = workspace / "linked.py"
    linked.symlink_to(protected)
    manager = LSPManager(str(workspace))
    server = manager.get_language_server("safe.py")
    assert server is not None

    try:
        safe_logical = manager._logical_file_path(str(safe))
        diagnostics = asyncio.run(manager._static_validate(safe_logical, None, server))
        linked_logical = manager._logical_file_path(str(linked))
        with pytest.raises(WorkspacePathError):
            asyncio.run(manager._static_validate(linked_logical, None, server))
        with pytest.raises(WorkspacePathError):
            manager._logical_file_path(str(protected))
    finally:
        asyncio.run(manager.shutdown())

    assert canary not in json.dumps([item.message for item in diagnostics])


def test_workspace_filesystem_rejects_untrusted_root_symlink(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    alias = tmp_path / "workspace-alias"
    alias.symlink_to(workspace, target_is_directory=True)

    with pytest.raises(
        WorkspacePathError,
        match="workspace_root_ancestor_not_directory",
    ):
        WorkspaceFilesystem(alias)


def test_workspace_filesystem_accepts_root_owned_darwin_var_alias(
    tmp_path: Path,
) -> None:
    import sys

    if sys.platform != "darwin":
        pytest.skip("Darwin root alias contract")
    canonical_var = Path("/private/var")
    try:
        relative = tmp_path.relative_to(canonical_var)
    except ValueError:
        pytest.skip("temporary directory is not under /private/var")
    alias = Path("/var") / relative

    with WorkspaceFilesystem(alias) as workspace:
        workspace.write_text("proof.txt", "alias-safe")
        assert workspace.root == tmp_path

    assert (tmp_path / "proof.txt").read_text(encoding="utf-8") == "alias-safe"
