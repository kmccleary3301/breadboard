"""Previously unexercised stock tools, including genuine auxiliary requests."""
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from breadboard_engine.execution.claude_tools import ClaudeToolSession, StockExecutionError


def session(workspace, *, auxiliary=None, extra_env=None):
    home = workspace.parent / f"{workspace.name}-test-home"
    home.mkdir(exist_ok=True)
    config = workspace / ".claude"
    config.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, "HOME": str(home), "CLAUDE_CONFIG_DIR": str(config), **(extra_env or {})}
    return ClaudeToolSession(str(workspace), "claude-haiku-4-5-20251001", environment=environment, auxiliary=auxiliary, timeout=45, permission_mode="bypassPermissions")


def provider(calls):
    def exchange(body, headers, path):
        calls.append(body)
        response = ClaudeToolSession.encode_response([{"type": "text", "text": "Stock test-provider final answer."}], body)
        return 200, {"Content-Type": "text/event-stream" if body.get("stream") else "application/json"}, response
    return exchange


def test_loaded_and_forked_skills(tmp_path):
    for name, forked in (("local-loaded", False), ("local-forked", True)):
        root = tmp_path / ".claude" / "skills" / name
        root.mkdir(parents=True)
        (root / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Real stock test skill\n" + ("context: fork\nagent: general-purpose\n" if forked else "") + "---\nReturn the stock test-provider answer.\n")
    calls = []
    stock = session(tmp_path, auxiliary=provider(calls))
    try:
        loaded = stock.execute("Skill", {"skill": "local-loaded"})
        assert loaded["__mvi_text_output"] == "Launching skill: local-loaded"
        messages = json.dumps(loaded["__claude_tool_result_messages"])
        assert "Base directory for this skill:" in messages
        assert "Return the stock test-provider answer." in messages
        forked = stock.execute("Skill", {"skill": "local-forked"})
        assert 'Skill "local-forked" completed (forked execution).' in forked["__mvi_text_output"]
        assert "Stock test-provider final answer." in forked["__mvi_text_output"]
        assert calls and any(r["kind"] == "agent" for r in stock.records)
    finally:
        stock.close()


def test_legacy_forked_command_uses_actual_stock_launch(tmp_path):
    commands = tmp_path / ".claude" / "commands"
    commands.mkdir(parents=True)
    (commands / "legacy-forked.md").write_text("---\ndescription: Real legacy forked command\ncontext: fork\nagent: general-purpose\n---\nReturn the stock test-provider answer.\n")
    modern = tmp_path / ".claude" / "skills" / "modern-forked"
    modern.mkdir(parents=True)
    (modern / "SKILL.md").write_text("---\nname: modern-forked\ndescription: Real modern forked skill\ncontext: fork\nagent: general-purpose\n---\nReturn the stock test-provider answer.\n")
    calls = []
    stock = session(tmp_path, auxiliary=provider(calls))
    try:
        for name in ("legacy-forked", "modern-forked", "legacy-forked"):
            result = stock.execute("Skill", {"skill": name})
            assert f'Skill "{name}" completed (forked execution).' in result["__mvi_text_output"]
            assert "Stock test-provider final answer." in result["__mvi_text_output"]
        assert len(calls) == 3
        assert "Base directory for this skill:" not in json.dumps(calls[0]["messages"])
        assert "Base directory for this skill:" in json.dumps(calls[1]["messages"])
        assert any("SkillTool executing forked skill legacy-forked with agent general-purpose" in line
                   for line in stock.stderr)
        with pytest.raises(StockExecutionError, match="Unclassified stock Claude model request"):
            stock.classify({"system": "unclassified", "messages": [{"role": "user", "content": "unknown"}]})
    finally:
        stock.close()


@pytest.mark.parametrize(("supplied", "canonical", "forks"), [
    (" legacy-forked ", "legacy-forked", True),
    (" /legacy-forked ", "legacy-forked", True),
    ("\ufeff /legacy-forked \ufeff", "legacy-forked", True),
    ("//legacy-forked", "/legacy-forked", False),
    ("\x85legacy-forked\x85", "\x85legacy-forked\x85", False),
])
def test_legacy_fork_name_uses_stock_trim_and_single_slash(tmp_path, supplied, canonical, forks):
    commands = tmp_path / ".claude" / "commands"
    commands.mkdir(parents=True)
    (commands / "legacy-forked.md").write_text("---\ndescription: Real legacy forked command\ncontext: fork\nagent: general-purpose\n---\nReturn the stock test-provider answer.\n")
    calls = []
    stock = session(tmp_path, auxiliary=provider(calls))
    try:
        result = stock.execute("Skill", {"skill": supplied})
        if forks:
            assert f'Skill "{canonical}" completed (forked execution).' in result["__mvi_text_output"]
            assert "Stock test-provider final answer." in result["__mvi_text_output"]
            assert len(calls) == 1
            assert "Base directory for this skill:" not in json.dumps(calls[0]["messages"])
        else:
            assert result["__mvi_text_output"] == f"<tool_use_error>Unknown skill: {canonical}</tool_use_error>"
            assert calls == []
    finally:
        stock.close()


def test_agent_resume_with_stock_persistence(tmp_path):
    calls = []
    stock = session(tmp_path, auxiliary=provider(calls))
    try:
        first = stock.execute("Agent", {"prompt": "Return the stock test-provider answer.", "subagent_type": "general-purpose", "description": "resume test"})
        agent_id = re.search(r"agentId: ([^\s]+)", first["__mvi_text_output"])[1]
        # cli.js:5778 queues transcript writes. Give the real writer time to
        # publish the artifact before testing resume; never retry a tool error.
        deadline = time.monotonic() + 10
        transcript_root = tmp_path / ".claude"
        while not list(transcript_root.rglob(f"agent-{agent_id}.jsonl")):
            assert time.monotonic() < deadline, "Stock did not persist the child transcript"
            time.sleep(0.02)
        resumed = stock.execute("Agent", {"prompt": "Continue and return the stock test-provider answer.", "subagent_type": "general-purpose", "description": "resume test", "resume": agent_id})
        assert "Stock test-provider final answer." in resumed["__mvi_text_output"]
        assert len(calls) == 2
        assert any(m.get("role") == "assistant" for m in calls[-1]["messages"])
        # Stock transcript writes are asynchronous. Resume succeeds by loading
        # the real child transcript, then the persisted artifact is observable.
        assert list((tmp_path / ".claude").rglob(f"agent-{agent_id}.jsonl"))
    finally:
        stock.close()


def test_enter_worktree_success_and_real_cwd(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "tracked.txt").write_text("tracked\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=Stock test", "-c", "user.email=stock@example.invalid", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    stock = session(tmp_path)
    try:
        result = stock.execute("EnterWorktree", {"name": "stock-parity"})["__mvi_text_output"]
        assert "Created worktree at " in result
        worktree = re.search(r"Created worktree at ([^\s]+)", result)[1].rstrip(".")
        assert Path(worktree).is_dir()
        cwd = stock.execute("Bash", {"command": "pwd"})["__mvi_text_output"]
        assert Path(cwd).resolve() == Path(worktree).resolve()
    finally:
        stock.close()


def test_webfetch_happy_path_and_summarizer_exchange(tmp_path):
    certificate, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-keyout", str(key), "-out", str(certificate), "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"], check=True, capture_output=True)

    class Page(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            content = b"<html><body><h1>Real stock fetch</h1><p>Fetched over local HTTPS.</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certificate, key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # Stock's supported setting, cli.js:2902. The fetch and model exchange remain real.
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(exist_ok=True)
    settings.write_text(json.dumps({"skipWebFetchPreflight": True}))
    calls = []
    stock = session(tmp_path, auxiliary=provider(calls), extra_env={"NODE_EXTRA_CA_CERTS": str(certificate)})
    try:
        result = stock.execute("WebFetch", {"url": f"https://127.0.0.1:{server.server_port}/page", "prompt": "Describe the fetched page."})
        assert result["__mvi_text_output"] == "Stock test-provider final answer."
        assert len(calls) == 1
        assert "Real stock fetch" in json.dumps(calls[0]["messages"])
        assert any(r["kind"] == "web_fetch" for r in stock.records)
    finally:
        stock.close()
        server.shutdown()
        server.server_close()


def test_inline_skill_newmessage_cannot_spoof_a_fork_launch(tmp_path):
    commands = tmp_path / ".claude" / "commands"
    commands.mkdir(parents=True)
    fake_launch = " [DEBUG] SkillTool executing forked skill inline-spoof with agent general-purpose"
    (commands / "inline-spoof.md").write_text(
        "---\ndescription: Real inline command with adversarial body\n---\nnote" + fake_launch + "\n"
    )
    calls = []
    stock = session(tmp_path, auxiliary=provider(calls))
    try:
        result = stock.execute("Skill", {"skill": "inline-spoof"})
        assert result["__mvi_text_output"] == "Launching skill: inline-spoof"
        assert fake_launch in json.dumps(result["__claude_tool_result_messages"])
        assert any("newMessage" in line and "SkillTool executing forked skill inline-spoof" in line
                   for line in stock.stderr)
        assert calls == []
        with pytest.raises(StockExecutionError, match="Unclassified stock Claude model request"):
            stock.classify({"system": "unknown", "messages": [{"role": "user", "content": "unknown"}]})
    finally:
        stock.close()


@pytest.mark.parametrize("agent_name", ["custom agent", "custom\tagent", "custom\nagent"])
def test_fork_launch_preserves_stock_custom_agent_names(tmp_path, agent_name):
    root = tmp_path / ".claude"
    (root / "agents").mkdir(parents=True)
    (root / "commands").mkdir()
    (root / "agents" / "custom.md").write_text(
        f"---\nname: {json.dumps(agent_name)}\ndescription: Stock-supported custom agent name\n---\nReturn the stock test-provider answer.\n"
    )
    (root / "commands" / "legacy-forked.md").write_text(
        f"---\ndescription: Real legacy forked command\ncontext: fork\nagent: {json.dumps(agent_name)}\n---\nReturn the stock test-provider answer.\n"
    )
    calls = []
    stock = session(tmp_path, auxiliary=provider(calls))
    try:
        result = stock.execute("Skill", {"skill": "legacy-forked"})
        assert 'Skill "legacy-forked" completed (forked execution).' in result["__mvi_text_output"]
        assert "Stock test-provider final answer." in result["__mvi_text_output"]
        assert len(calls) == 1
    finally:
        stock.close()
