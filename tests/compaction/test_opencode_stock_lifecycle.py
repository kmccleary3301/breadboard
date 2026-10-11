"""Real stock primary transitions, summary metadata, and user-part projection."""
import json
from pathlib import Path

from breadboard_engine.execution.opencode_tools import OpenCodeToolSession
from breadboard_engine.provider.contract_messages import ProviderResult
from tests.compaction.test_opencode_stock_tools import MODEL, binary, plain, isolated_environment, response


def test_stock_primary_agent_can_change_on_same_root(plain):
    first = plain.user([{"type": "text", "text": "Answer as build."}], agent="build")
    plain.submit(response(plain, text="Build answer."))
    second = plain.user([{"type": "text", "text": "Answer as plan."}], agent="plan")
    assert plain.root_agent == "plan"
    assert first["model"] == second["model"]
    plain.submit(response(plain, text="Plan answer."))


def test_genuine_bb_summary_response_retains_stock_usage_and_reasoning(plain):
    plain.user([{"type": "text", "text": "Inspect then compact."}], agent="build")
    payload = response(plain, [("call_summary", "bash", {
        "command": "printf genuine-summary", "description": "Inspect before summary",
    })])
    payload["usage"] = {"input_tokens": 390000, "output_tokens": 1}
    plain.submit(payload)
    summary = response(plain, text="Genuine BB summary.\n")
    summary["usage"] = {"input_tokens": 42, "output_tokens": 7,
                        "input_tokens_details": {"cached_tokens": 0},
                        "output_tokens_details": {"reasoning_tokens": 3}}
    summary["output"].insert(0, {"type": "reasoning", "id": "rs_summary",
                                 "summary": [{"type": "summary_text", "text": "Summary reasoning."}]})
    result = ProviderResult(messages=[], raw_response=summary, usage=summary["usage"], model=MODEL)
    assert plain.set_compaction_response(result) == "Genuine BB summary.\n"
    plain.compact("Genuine BB summary.\n")
    messages = plain.api("GET", "/session/" + plain.session_id + "/message")
    native = next(message for message in messages
                  if message["info"]["role"] == "assistant" and message["info"].get("summary"))
    assert native["info"]["tokens"]["input"] == 42
    assert native["info"]["tokens"]["reasoning"] == 3
    assert any(part.get("text") == "Summary reasoning." for part in native["parts"])
    # Source processor.ts:321 trimEnd runs before the stock text-complete hook.
    assert any(part.get("text") == "Genuine BB summary." for part in native["parts"])
    plain.submit(response(plain))


def test_source_hook_added_text_preserves_authorized_media_slots(tmp_path, binary):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plugin = workspace / "user-hook.mjs"
    plugin.write_text('''export const UserHook = async () => ({
      "chat.message": async (_input, output) => {
        const first = output.parts[0];
        output.parts.unshift({...first, id: first.id + "prefix", text: "Native added prefix."});
        output.parts.push({...first, id: first.id + "suffix", text: "Native added suffix."});
      }
    });''')
    image = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/2ioAAAAASUVORK5CYII="
    session = OpenCodeToolSession(
        str(workspace), MODEL, binary=binary, timeout=60,
        stock_config={"plugin": [plugin.as_uri()], "agent": {"title": {"disable": True}}},
        environment=isolated_environment(tmp_path / "stock"),
    )
    try:
        session.user([{"type": "text", "text": "Original authorized text."},
                      {"type": "file", "mime": "image/png", "url": image}], agent="build")
        sequence = session.latest_user_sequence()
        # Stock persists/sorts by part ID, not the plugin's temporary array order.
        assert sequence == [{"type": "text", "text": "Original authorized text."},
                            {"type": "text", "text": "Native added prefix."},
                            {"type": "text", "text": "Native added suffix."},
                            {"type": "stock_media", "index": 0}]
        assert image not in json.dumps(sequence)
        session.submit(response(session))
    finally:
        session.close()


def test_root_prompt_lifetime_does_not_use_operation_deadline(plain):
    import time
    plain.timeout = 1
    plain.user([{"type": "text", "text": "Wait for BB's model response."}])
    time.sleep(1.2)
    plain.check_alive()
    plain.submit(response(plain, text="The long-lived root request survived."))


def test_headless_finish_and_operator_stop_wait_for_one_genuine_native_close(plain, tmp_path):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from types import SimpleNamespace

    from breadboard_engine.execution.stock_tools import register_stock_session, close_stock_sessions
    from tests.compaction.test_opencode_stock_interaction import runner_at

    plain.headless = True
    plain.user([{"type": "text", "text": "Complete before operator shutdown."}])
    plain.submit(response(plain, text="The real native root is idle."))
    assert plain.headless_idle_seen
    entered = threading.Event()
    release = threading.Event()
    stopped = threading.Event()
    aborts = []
    original_api = plain.api
    def paused_native_abort(method, path, *args, **kwargs):
        if method == "POST" and path == f"/session/{plain.session_id}/abort":
            aborts.append(path)
            entered.set()
            if not release.wait(10):
                raise TimeoutError("Genuine native shutdown barrier was not released")
        return original_api(method, path, *args, **kwargs)
    plain.api = paused_native_abort
    owner = SimpleNamespace()
    register_stock_session(owner, plain)
    runner = runner_at(tmp_path / "operator")
    asyncio.run(runner.registry.create(runner.session))
    def close_agent():
        stopped.set()
        close_stock_sessions(owner)
    runner._agent = SimpleNamespace(close=close_agent)
    with ThreadPoolExecutor(max_workers=2) as executor:
        worker = executor.submit(plain.finish_run)
        assert entered.wait(10)
        operator = executor.submit(lambda: asyncio.run(runner.stop()))
        try:
            assert stopped.wait(10)
            assert not operator.done(), "Operator stop must await the worker-owned whole teardown"
        finally:
            release.set()
        worker.result(timeout=15)
        operator.result(timeout=15)
    assert len(aborts) == 1
    assert plain.closed and plain.process.poll() is not None
    assert not Path(plain.scratch.name).exists()
    assert owner._stock_tool_sessions == []


def test_native_process_lookup_race_still_disposes_proxy_and_scratch(plain):
    original_terminate = plain.process.terminate
    def exited_during_terminate():
        original_terminate()
        raise ProcessLookupError("Native process exited at the termination boundary")
    plain.process.terminate = exited_during_terminate
    plain.close()
    assert plain.closed and plain.process.poll() is not None
    assert not Path(plain.scratch.name).exists()
    assert plain.proxy.socket.fileno() == -1
