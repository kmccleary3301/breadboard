"""PTY capability does not let a stock tmux pane read protected credentials."""
from pathlib import Path
import shlex
import shutil
import subprocess
import uuid

import pytest

from breadboard_engine.execution.opencode_tools import OpenCodeToolSession
from tests.compaction.test_opencode_stock_omo_groups import PLUGIN, PLUGIN_DIGEST, LSP_PIN
from tests.compaction.test_opencode_stock_tools import MODEL, binary, isolated_environment, response


def test_stock_tmux_pane_cannot_read_protected_path_with_pty_enabled(tmp_path, binary):
    tmux = shutil.which("tmux")
    if tmux is None:
        pytest.skip("tmux is absent: real PTY credential isolation requires tmux")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
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
        timeout=30, lsp_root=LSP_PIN, pty=True, protected_paths=(str(secret),),
        stock_config={"agent": {"title": {"disable": True}}},
        read_only_roots=[{"path": str(PLUGIN), "digest": PLUGIN_DIGEST}],
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
