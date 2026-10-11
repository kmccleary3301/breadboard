"""The dated headless sidecar exits at the same root idle as stock `run`."""
import copy
import json
import os
import subprocess
import threading

from breadboard_engine.execution.opencode_tools import OpenCodeToolSession
from scripts.compaction_lanes.opencode_lane import ResponsesProvider
from tests.compaction.test_opencode_stock_delegation import connect_auxiliary, raw_descriptor
from tests.compaction.test_opencode_stock_omo_groups import PLUGIN, PLUGIN_DIGEST, LSP_PIN
from tests.compaction.test_opencode_stock_tools import MODEL, binary, isolated_environment, response


def test_headless_first_root_idle_disposes_pending_child_like_stock_run(tmp_path, binary):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sample.txt").write_text("real child input\n")
    config = {"permission": {"read": "ask"}, "lsp": False,
              "agent": {"title": {"disable": True},
                        "explore": {"model": MODEL, "permission": {"read": "ask"}}}}
    # Keep remote discovery out of this lifecycle proof on both real stock
    # transports (OMO mcp-config-handler.ts:8-25,48-51). No hook is disabled.
    config["mcp"] = {name: {"enabled": False} for name in ("websearch", "context7", "grep_app")}
    session = OpenCodeToolSession(str(workspace), MODEL, binary=binary, plugin_root=PLUGIN,
        timeout=30, lsp_root=LSP_PIN, headless=True, stock_config=config,
        read_only_roots=[{"path": str(PLUGIN), "digest": PLUGIN_DIGEST}],
        environment=isolated_environment(tmp_path / "sidecar"))
    empty = {"info": session.api("GET", f"/session/{session.session_id}"), "messages": []}
    prompt = "Start a background reader and finish the parent."
    task = ("call_headless_bg", "task", {"subagent_type": "explore", "load_skills": [],
        "description": "Read sample", "prompt": "Read sample.txt.", "run_in_background": True})
    child = raw_descriptor(response(session, [("call_headless_child", "read", {
        "filePath": str(workspace / "sample.txt")})]))
    notices = []
    child_held = threading.Event()
    original_interaction = session._interaction
    def observe_interaction(kind, properties, state):
        original_interaction(kind, properties, state)
        if properties["sessionID"] != session.session_id:
            child_held.set()
    session._interaction = observe_interaction
    session.stock_input_handler = notices.append
    try:
        with ResponsesProvider() as provider:
            provider.engine.set_child_script(session.session_id, [child])
            connect_auxiliary(session, provider)
            initial = session.user([{"type": "text", "text": prompt + "\n"}])
            session.submit(response(session, [task]))
            assert child_held.wait(15)
            assert session.api("GET", "/permission"), "Stock child must really be waiting, not mocked"
            finish = response(session, text="The headless parent is complete.")
            session.submit(finish)
            assert session.headless_idle_seen
            session.finish_run()
            assert session.closed and session.process.poll() is not None
            assert notices == []
            assert len([row for row in session.records if row["kind"] == "main"]) == 2

        # Stock run.ts:531-537 stops its event loop at root idle, while 640-646
        # awaits that root prompt. bootstrap disposes the instance, not a daemon
        # that waits for the background child. Exercise the actual pinned CLI.
        oracle = tmp_path / "oracle"
        oracle.mkdir()
        fixture = oracle / "empty.json"
        fixture.write_text(json.dumps(empty))
        with ResponsesProvider() as provider:
            provider.engine.set_script([raw_descriptor(response(session, [task])), raw_descriptor(finish)])
            provider.engine.set_child_script(session.session_id, [child])
            stock_config = copy.deepcopy(config)
            stock_config.update(model=MODEL, plugin=[(PLUGIN / "dist/index.js").as_uri()])
            stock_config["provider"] = {"opencode": {}, "openai": {"options": {
                "baseURL": provider.base_url + "/v1", "apiKey": "stock-test"}}}
            env = {**os.environ, **isolated_environment(oracle),
                   "OPENCODE_CONFIG_CONTENT": json.dumps(stock_config),
                   "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1",
                   "OPENCODE_DISABLE_LSP_DOWNLOAD": "1"}
            imported = subprocess.run([str(binary), "import", str(fixture)], cwd=workspace,
                env=env, capture_output=True, text=True, timeout=30)
            assert imported.returncode == 0, imported.stderr
            completed = subprocess.run([str(binary), "run", "--format", "json", "--model", MODEL,
                "--session", session.session_id, *prompt.split()], cwd=workspace, env=env, input="",
                capture_output=True, text=True, timeout=30)
            assert completed.returncode == 0, completed.stderr
            root = [row["json"] for row in provider.engine.recorded_requests
                    if row["json"].get("prompt_cache_key") == session.session_id]
            assert len(root) == 2
            assert root[0] == initial, "Headless root request must match actual stock CLI bytes"
            assert len([row for row in provider.engine.recorded_requests
                        if row["json"].get("prompt_cache_key") != session.session_id]) == 1
    finally:
        session.close()
