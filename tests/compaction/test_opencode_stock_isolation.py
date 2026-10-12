from tests.compaction.test_opencode_stock_tools import omo_dependencies
from pathlib import Path
import json
import pytest
from breadboard_engine.execution.opencode.session import OpenCodeToolSession
from breadboard_engine.execution.opencode.wire import StockExecutionError
from breadboard_engine.provider.routing import ProviderRouter
from breadboard_engine.provider_broker import get_provider_broker
from breadboard_engine.security.isolation_errors import ProcessIsolationUnavailable
from breadboard_engine.security.stock_install import stock_install_digest, validate_stock_read_only_roots
from tests.compaction.test_opencode_stock_tools import MODEL
from tests.compaction.test_opencode_stock_tools import binary
from tests.compaction.test_opencode_stock_tools import isolated_environment
from tests.compaction.test_opencode_stock_tools import response
from tests.compaction.test_opencode_stock_tools import hermetic_stock_config
from tests.compaction.test_opencode_stock_tools import short_stock_workspace
from breadboard_engine.security.stock_install import stock_install_digest
import shlex
import shutil
import subprocess
import uuid
from tests.compaction.test_opencode_stock_omo_groups import PLUGIN
from tests.compaction.test_opencode_stock_omo_groups import PLUGIN_DIGEST
from tests.compaction.test_opencode_stock_omo_groups import LSP_PIN
import os
from breadboard_engine.compaction_lanes.opencode_recording import ResponsesProvider
from breadboard_engine.execution.opencode.wire import responses_payload
from breadboard_engine.security.launch_policy import ProcessIsolationUnavailable

"""Pinned stock process isolation and opt-in auxiliary provider routing."""




def test_install_digest_rejects_mutation_and_credential_overlap(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    install = tmp_path / "install"
    install.mkdir()
    bundle = install / "index.js"
    bundle.write_text("export default function plugin() {}")
    pin = {"path": str(install), "digest": stock_install_digest(install)}
    assert validate_stock_read_only_roots([pin], workspace=workspace, protected_paths=()) == (install,)
    with pytest.raises(ProcessIsolationUnavailable, match="protected credential"):
        validate_stock_read_only_roots([pin], workspace=workspace, protected_paths=(install,))
    bundle.write_text("mutated")
    with pytest.raises(ProcessIsolationUnavailable, match="digest mismatch"):
        validate_stock_read_only_roots([pin], workspace=workspace, protected_paths=())


def test_stock_cannot_read_protected_path_or_write_install_root(tmp_path, binary):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    protected = tmp_path / "protected"
    protected.mkdir()
    secret = protected / "secret.txt"
    secret.write_text("fixture-secret-never-return")
    install = tmp_path / "install"
    install.mkdir()
    data = install / "asset.txt"
    data.write_text("unchanged")
    pin = {"path": str(install), "digest": stock_install_digest(install)}
    session = OpenCodeToolSession(str(workspace), MODEL, binary=binary, timeout=60,
                                 protected_paths=(str(protected),), read_only_roots=[pin], stock_config=hermetic_stock_config({"permission": "allow", "agent": {"title": {"disable": True}}}), environment=isolated_environment(tmp_path / "ignored-home"))
    try:
        session.user([{"type": "text", "text": "Exercise fixture isolation."}])
        session.submit(response(session, [("read_protected", "read", {"filePath": str(secret)}),
                         ("write_install", "bash", {"command": f"printf changed > {data}",
                                                    "description": "Check read-only fixture"})]))
        denied = session.result("read_protected")["__mvi_text_output"]
        assert "fixture-secret-never-return" not in denied
        assert "permission denied" in denied.lower() or "operation not permitted" in denied.lower()
        assert "operation not permitted" in session.result("write_install")["__mvi_text_output"].lower()
        assert data.read_text() == "unchanged"
    finally:
        session.close()


def test_home_auth_and_global_config_are_ignored(tmp_path, binary):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fake_home = tmp_path / "host-home"
    (fake_home / ".config/opencode").mkdir(parents=True)
    (fake_home / ".config/opencode/opencode.json").write_text(json.dumps({
        "provider": {"host-only": {"npm": "@ai-sdk/openai", "options": {"apiKey": "not-a-real-secret"}}}}))
    (fake_home / ".local/share/opencode").mkdir(parents=True)
    (fake_home / ".local/share/opencode/auth.json").write_text(json.dumps({
        "host-only": {"type": "api", "key": "not-a-real-secret"}}))
    session = OpenCodeToolSession(str(workspace), MODEL, binary=binary, timeout=60,
                                 environment={"HOME": str(fake_home), "XDG_CONFIG_HOME": str(fake_home / ".config"),
                                              "XDG_DATA_HOME": str(fake_home / ".local/share"),
                                              "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1"})
    try:
        config = session.api("GET", "/config")
        assert "host-only" not in config.get("provider", {})
        assert "host-only" not in session.api("GET", "/provider")["connected"]
        assert config.get("permission") != "allow"
    finally:
        session.close()


def test_project_provider_fails_loudly_before_model_call(tmp_path, binary):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "opencode.json").write_text(json.dumps({"provider": {"project-unrouted": {
        "npm": "@ai-sdk/openai", "options": {"baseURL": "http://127.0.0.1:1/v1", "apiKey": "fixture"},
        "models": {"fixture": {"name": "Fixture"}}}}}))
    with pytest.raises(StockExecutionError, match="project-unrouted"):
        OpenCodeToolSession(str(workspace), MODEL, binary=binary, timeout=60,
                            environment={"OPENCODE_DISABLE_DEFAULT_PLUGINS": "1"})


def test_zen_route_is_declared_only_and_keeps_stock_public_auth():
    router = ProviderRouter(get_provider_broker())
    model = "opencode/minimax-m2.5-free"
    assert "opencode" not in router.providers
    with pytest.raises(ValueError, match=model):
        router.get_declared_provider_config(model, set())
    config, actual, native = router.get_declared_provider_config(model, {model})
    assert actual == "minimax-m2.5-free" and native
    assert config.base_url == "https://opencode.ai/zen/v1"
    assert config.default_headers == {"authorization": "Bearer public"}
    assert not config.credential_required
    assert "opencode" not in router.providers


def test_unroutable_child_model_is_not_substituted():
    router = ProviderRouter(get_provider_broker())
    with pytest.raises(ValueError, match="openai/undeclared-child"):
        router.get_declared_provider_config("openai/undeclared-child", {MODEL})






@pytest.mark.usefixtures("omo_dependencies")
@pytest.mark.parametrize("altered", [False, True], ids=["missing", "altered"])
def test_unstaged_or_altered_sg_rejects_before_startup(tmp_path, altered):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "package.json").write_text(json.dumps({"name": "oh-my-opencode", "version": "3.10.0"}))
    if altered:
        binary = plugin / "node_modules/@ast-grep/cli/sg"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"not the recorded stock ast-grep binary")
        binary.chmod(0o755)
    with pytest.raises(StockExecutionError, match="ast-grep 0.40.0"):
        OpenCodeToolSession(str(workspace), "openai/gpt-5.2", binary=Path("not-launched"),
                            plugin_root=plugin, read_only_roots=[{
                                "path": str(plugin), "digest": stock_install_digest(plugin),
                            }])
    assert list((workspace / ".breadboard/tmp").iterdir()) == []


"""PTY capability does not let a stock tmux pane read protected credentials."""




@pytest.mark.usefixtures("omo_dependencies")
def test_stock_tmux_pane_cannot_read_protected_path_with_pty_enabled(tmp_path, binary, short_stock_workspace):
    tmux = shutil.which("tmux")
    if tmux is None:
        pytest.skip("tmux is absent: real PTY credential isolation requires tmux")
    workspace = short_stock_workspace
    secret = tmp_path / "protected-token"
    marker = "BB_PROTECTED_SECRET_" + uuid.uuid4().hex
    secret.write_text(marker)
    socket_path = str(workspace / "tmux.sock")
    prefix = "-S " + shlex.quote(socket_path)
    target = "bb-secret-" + uuid.uuid4().hex
    channel = "bb-secret-ready"
    sent = (f"cat {shlex.quote(str(secret))} 2>/dev/null; status=$?; "
            f"printf pty_secret_status=$status; tmux {prefix} wait-for -S {channel}")
    session = OpenCodeToolSession(str(workspace), MODEL, binary=binary, plugin_root=PLUGIN,
        timeout=30, lsp_root=LSP_PIN, pty=True, protected_paths=(str(secret),), stock_config=hermetic_stock_config({"agent": {"title": {"disable": True}}}), read_only_roots=[{"path": str(PLUGIN), "digest": PLUGIN_DIGEST}],
    environment=isolated_environment(tmp_path / "stock"))
    try:
        session.user([{"type": "text", "text": "Check the protected path from a real tmux pane."}])
        calls = [
            ("call_secret_create", "interactive_bash", {"tmux_command":
                f"{prefix} new-session -d -s {target} -x 80 -y 24 'env PS1= bash --noprofile --norc'"}),
            ("call_secret_send", "interactive_bash", {"tmux_command":
                f"{prefix} send-keys -t {target} {shlex.quote(sent)} Enter"}),
            ("call_secret_capture", "bash", {"command":
                f"tmux {prefix} wait-for {channel}; tmux {prefix} capture-pane -p -t {target}",
                "description": "Observe the real pane's protected-read exit status"}),
            ("call_secret_kill", "interactive_bash", {"tmux_command": f"{prefix} kill-session -t {target}"}),
        ]
        results = {}
        for call in calls:
            session.submit(response(session, [call]))
            results[call[0]] = session.result(call[0])["__mvi_text_output"]
        assert results["call_secret_create"] == results["call_secret_send"] == results["call_secret_kill"] == "(no output)"
        captured = results["call_secret_capture"]
        assert "pty_secret_status=1" in captured.splitlines()
        assert marker not in captured
        session.submit(response(session))
    finally:
        subprocess.run([tmux, "-S", socket_path, "kill-server"], capture_output=True, timeout=10)
        session.close()


"""Stock snapshot discovery and scoped worktree access, never a sandbox bypass."""




def test_nested_git_workspace_snapshots_with_read_only_worktree(tmp_path, binary):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    workspace = root / "nested"
    workspace.mkdir()
    (workspace / "sample.txt").write_text("snapshot me\n")
    session = OpenCodeToolSession(str(workspace), MODEL, binary=binary, timeout=60,
    stock_worktree_read_only=True, stock_config=hermetic_stock_config({"agent": {"title": {"disable": True}}}), environment=isolated_environment(tmp_path / "stock"),)
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
                                 stock_worktree_read_only=True, stock_config=hermetic_stock_config({"agent": {"title": {"disable": True}}}), environment=isolated_environment(tmp_path / "stock"))
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
    session = OpenCodeToolSession(str(workspace), MODEL, binary=binary, timeout=60, stock_worktree_read_only=True, stock_config=hermetic_stock_config({"agent": {"title": {"disable": True}}}), environment=isolated_environment(tmp_path / "stock"))
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
