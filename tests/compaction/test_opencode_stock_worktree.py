"""Stock snapshot discovery and scoped worktree access, never a sandbox bypass."""
from pathlib import Path
import subprocess
import json
import os
from scripts.compaction_lanes.opencode_lane import ResponsesProvider
from breadboard_engine.execution.opencode_tools import responses_payload

import pytest

from breadboard_engine.execution.opencode_tools import OpenCodeToolSession
from breadboard_engine.security.launch_policy import ProcessIsolationUnavailable
from tests.compaction.test_opencode_stock_tools import MODEL, binary, isolated_environment, response


def test_nested_git_workspace_snapshots_with_read_only_worktree(tmp_path, binary):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    workspace = root / "nested"
    workspace.mkdir()
    (workspace / "sample.txt").write_text("snapshot me\n")
    session = OpenCodeToolSession(
        str(workspace), MODEL, binary=binary, timeout=60,
        stock_worktree_read_only=True,
        stock_config={"agent": {"title": {"disable": True}}},
        environment=isolated_environment(tmp_path / "stock"),
    )
    try:
        assert session.worktree_root == root.resolve()
        assert Path(session.api("GET", "/path")["worktree"]).resolve() == root.resolve()
        session.user([{"type": "text", "text": "Answer in this nested git workspace."}], agent="build")
        session.submit(response(session, text="Snapshot complete."))
        messages = session.api("GET", "/session/" + session.session_id + "/message")
        assert any(part["type"] == "step-start" and part.get("snapshot")
                   for message in messages for part in message["parts"])
        assert not any(message["info"].get("error") for message in messages)
        session.user([{"type": "text", "text": "Do not write outside the workspace."}], agent="build")
        session.submit(response(session, [("call_write_parent", "bash", {
            "command": "printf forbidden > ../outside.txt", "description": "Exercise the read-only parent boundary",
        })]))
        assert "operation not permitted" in session.result("call_write_parent")["__mvi_text_output"].lower()
        assert not (root / "outside.txt").exists()
        session.submit(response(session))
    finally:
        session.close()


def test_protected_path_in_stock_worktree_rejects_access(tmp_path, binary):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    workspace = root / "nested"
    workspace.mkdir()
    credential = root / "broker-secret"
    credential.write_text("must-not-be-readable")
    with pytest.raises(ProcessIsolationUnavailable, match="worktree overlaps a protected"):
        OpenCodeToolSession(
            str(workspace), MODEL, binary=binary, timeout=60,
            stock_worktree_read_only=True, protected_paths=(str(credential),),
            environment=isolated_environment(tmp_path / "stock"),
        )


def direct_project_identity(binary, workspace, scratch):
    provider = ResponsesProvider()
    stopped = {"id": "resp_project", "object": "response", "model": MODEL.split("/", 1)[1],
               "created_at": 1, "status": "completed", "output": [],
               "usage": {"input_tokens": 0, "output_tokens": 0}}
    provider.engine.set_script([{"type": "raw", "headers": {"content-type": "text/event-stream"},
                                "chunks": [responses_payload(stopped, True).decode()]}])
    url = provider.start() + "/v1"
    config = {"model": MODEL, "provider": {"openai": {"options": {"baseURL": url, "apiKey": "test"}}}}
    environment = {**os.environ, **isolated_environment(scratch), "OPENCODE_CONFIG_CONTENT": json.dumps(config),
                   "OPENCODE_DISABLE_MODELS_FETCH": "1", "OPENCODE_DISABLE_AUTOUPDATE": "1"}
    try:
        result = subprocess.run([str(binary), "run", "--format", "json", "--agent", "build", "--title", "Linked stock"],
                                cwd=workspace, env=environment, input="Inspect this project.", text=True,
                                capture_output=True, check=False, timeout=60)
        assert result.returncode == 0, f"Stock run exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        session_id = next(event["sessionID"] for event in events if event.get("sessionID"))
        exported = subprocess.run([str(binary), "export", session_id], cwd=workspace, env=environment,
                                  text=True, capture_output=True, check=True, timeout=60)
        return json.loads(exported.stdout)["info"]["projectID"]
    finally:
        provider.stop()


def test_linked_git_worktree_external_common_dir_snapshots(tmp_path, binary):
    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", str(origin)], check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=Conformance", "-c", "user.email=conformance@example.invalid",
                    "commit", "--allow-empty", "-m", "Native worktree baseline"],
                   cwd=origin, check=True, capture_output=True)
    linked = tmp_path / "linked"
    subprocess.run(["git", "worktree", "add", "-b", "native-linked", str(linked)],
                   cwd=origin, check=True, capture_output=True)
    workspace = linked / "nested"
    workspace.mkdir()
    (workspace / "sample.txt").write_text("linked snapshot\n")
    session = OpenCodeToolSession(str(workspace), MODEL, binary=binary, timeout=60,
                                 stock_worktree_read_only=True,
                                 stock_config={"agent": {"title": {"disable": True}}},
                                 environment=isolated_environment(tmp_path / "stock"))
    try:
        assert session.worktree_root == linked.resolve()
        project_id = session.api("GET", "/session/" + session.session_id)["projectID"]
        assert project_id == direct_project_identity(binary, workspace, tmp_path / "oracle")
        session.user([{"type": "text", "text": "Snapshot the linked git worktree."}])
        session.submit(response(session, text="Linked snapshot complete."))
        messages = session.api("GET", "/session/" + session.session_id + "/message")
        assert any(part.get("snapshot") for message in messages for part in message["parts"])
        assert not any(message["info"].get("error") for message in messages)
    finally:
        session.close()


@pytest.mark.parametrize("boundary", ["overlap", "hardlink"])
def test_linked_git_metadata_rejects_protected_boundaries(tmp_path, binary, boundary):
    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", str(origin)], check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=Conformance", "-c", "user.email=conformance@example.invalid",
                    "commit", "--allow-empty", "-m", "Protected metadata baseline"],
                   cwd=origin, check=True, capture_output=True)
    linked = tmp_path / "linked"
    subprocess.run(["git", "worktree", "add", "-b", "protected-linked", str(linked)],
                   cwd=origin, check=True, capture_output=True)
    credential = origin / ".git" / "broker-secret" if boundary == "overlap" else tmp_path / "broker-secret"
    credential.write_text("must-not-be-readable")
    if boundary == "hardlink":
        os.link(credential, origin / ".git" / "linked-secret")
    with pytest.raises(ProcessIsolationUnavailable, match="protected|hardlink"):
        OpenCodeToolSession(
            str(linked), MODEL, binary=binary, timeout=60,
            stock_worktree_read_only=True, protected_paths=(str(credential),),
            environment=isolated_environment(tmp_path / "stock"),
        )


@pytest.mark.parametrize("kind", ["bare", "symlink", "foreign_worktree"])
def test_fabricated_git_pointers_never_grant_external_metadata_reads(tmp_path, binary, kind):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    origin = tmp_path / "foreign"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    target = origin
    if kind == "foreign_worktree":
        repo = tmp_path / "repo"
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.name=Conformance", "-c", "user.email=conformance@example.invalid",
                        "commit", "--allow-empty", "-m", "Provenance"], cwd=repo, check=True, capture_output=True)
        linked = tmp_path / "legitimate"
        subprocess.run(["git", "worktree", "add", "-b", "legitimate", str(linked)],
                       cwd=repo, check=True, capture_output=True)
        target = Path((linked / ".git").read_text().removeprefix("gitdir: ").strip())
    if kind == "symlink":
        (workspace / ".git").symlink_to(target, target_is_directory=True)
    else:
        (workspace / ".git").write_text(f"gitdir: {target}\n")
    with pytest.raises(ProcessIsolationUnavailable, match="provenance|backlink"):
        OpenCodeToolSession(str(workspace), MODEL, binary=binary, timeout=60, stock_worktree_read_only=True,
                            environment=isolated_environment(tmp_path / "stock"))


@pytest.mark.parametrize("mismatch", [False, True])
def test_submodule_requires_superproject_metadata_and_matching_core_worktree(tmp_path, binary, mismatch):
    child = tmp_path / "child"
    subprocess.run(["git", "init", str(child)], check=True, capture_output=True)
    (child / "sample.txt").write_text("submodule snapshot\n")
    subprocess.run(["git", "add", "sample.txt"], cwd=child, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=Conformance", "-c", "user.email=conformance@example.invalid",
                    "commit", "-m", "Submodule source"], cwd=child, check=True, capture_output=True)
    superproject = tmp_path / "superproject"
    subprocess.run(["git", "init", str(superproject)], check=True, capture_output=True)
    subprocess.run(["git", "-c", "protocol.file.allow=always", "submodule", "add", str(child), "module"],
                   cwd=superproject, check=True, capture_output=True)
    workspace = superproject / "module"
    git_directory = (workspace / (workspace / ".git").read_text().removeprefix("gitdir: ").strip()).resolve()
    if mismatch:
        subprocess.run(["git", "--git-dir=" + str(git_directory), "config", "core.worktree", str(child)],
                       check=True, capture_output=True)
        with pytest.raises(ProcessIsolationUnavailable, match="core.worktree"):
            OpenCodeToolSession(str(workspace), MODEL, binary=binary, timeout=60, stock_worktree_read_only=True,
                                environment=isolated_environment(tmp_path / "stock"))
        return
    session = OpenCodeToolSession(str(workspace), MODEL, binary=binary, timeout=60, stock_worktree_read_only=True,
                                 stock_config={"agent": {"title": {"disable": True}}},
                                 environment=isolated_environment(tmp_path / "stock"))
    try:
        assert session.worktree_root == workspace.resolve()
        project_id = session.api("GET", "/session/" + session.session_id)["projectID"]
        assert project_id == direct_project_identity(binary, workspace, tmp_path / "oracle")
        session.user([{"type": "text", "text": "Snapshot this submodule."}])
        session.submit(response(session, text="Submodule snapshot complete."))
        messages = session.api("GET", "/session/" + session.session_id + "/message")
        assert any(part.get("snapshot") for message in messages for part in message["parts"])
        assert not any(message["info"].get("error") for message in messages)
    finally:
        session.close()
