"""opencode stock responsibility owner."""
from __future__ import annotations
import hashlib
import http.client
import json
import os
import base64
from pathlib import Path
import subprocess
import threading
import re
import socket
import urllib.request
import urllib.parse
import time
import traceback
from contextlib import contextmanager, nullcontext
from typing import Any
import yaml
from .opencode_mcp import mcp_configuration, reset_mcp_relay
from .opencode_scenarios import PLUGIN_COMMIT, PROFILES, ROOT, SOURCE_API_PASSWORD, SOURCE_API_USERNAME, STOCK_VERSION, TEST_WINDOW, production_settings, scenario_turns

def resolve_stock() -> Path:
    cli = Path(os.environ.get("OPENCODE_TEST_BINARY", str(Path.home() / ".cache/bb-compaction-e4/pkgs/opencode-darwin-arm64@1.2.17/package/bin/opencode")))
    result = subprocess.run([str(cli), "--version"], input="", capture_output=True, text=True, timeout=20)
    if result.returncode or result.stdout.strip() != STOCK_VERSION:
        raise ValueError(f"Expected OpenCode {STOCK_VERSION}: {result.stdout!r} {result.stderr!r}")
    return cli

def plugin_root() -> Path:
    document = yaml.safe_load((ROOT / PROFILES["oh-my-opencode"]).read_bytes())
    source = Path(os.environ.get("OPENCODE_PLUGIN_ROOT", document["tools"]["stock_plugin_root"])).expanduser().resolve()
    metadata = json.loads((source / "package.json").read_bytes())
    if metadata["name"] != "oh-my-opencode" or metadata["version"] != "3.10.0":
        raise ValueError("Plugin package must be oh-my-opencode@3.10.0")
    from breadboard_engine.security.stock_install import stock_install_digest
    pin = next(entry for entry in document["tools"]["stock_read_only_roots"]
               if Path(entry["path"]).expanduser().resolve() == source)
    if stock_install_digest(source) != pin["digest"]:
        raise ValueError(f"Plugin installation does not match the profile's pinned {PLUGIN_COMMIT} artifact")
    if not (source / "dist/index.js").is_file():
        raise FileNotFoundError(source / "dist/index.js")
    return source

def stock_environment(config: dict[str, Any], scratch: Path) -> dict[str, str]:
    return {**os.environ, "HOME": str(scratch / "home"), "OPENCODE_TEST_HOME": str(scratch / "home"),
            **{"XDG_" + name.upper() + "_HOME": str(scratch / name)
               for name in ("config", "data", "cache", "state")},
            "OPENCODE_CONFIG_CONTENT": json.dumps(config),
            "OPENCODE_SERVER_USERNAME": SOURCE_API_USERNAME,
            "OPENCODE_SERVER_PASSWORD": SOURCE_API_PASSWORD,
            "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1",
            "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_PROJECT_CONFIG": "1"}

@contextmanager
def stock_server(workspace: Path, scratch: Path, config: dict[str, Any]):
    """Keep one unmodified source instance alive for the entire session."""
    ready = threading.Event()
    endpoints: list[str] = []
    logs: list[str] = []
    process = subprocess.Popen(
        [str(resolve_stock()), "serve", "--hostname", "127.0.0.1", "--port", "0"],
        cwd=workspace, env=stock_environment(config, scratch),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    def collect() -> None:
        for line in process.stdout:
            logs.append(line)
            match = re.search(r"opencode server listening on (http://127\.0\.0\.1:\d+)", line)
            if match:
                endpoints.append(match.group(1))
                ready.set()
        ready.set()
    threading.Thread(target=collect, daemon=True).start()
    try:
        if not ready.wait(30) or not endpoints:
            raise RuntimeError(f"Stock server failed: {''.join(logs)}")
        def api(method: str, path: str, body: dict[str, Any] | None = None):
            request = urllib.request.Request(
                endpoints[0] + path, data=json.dumps(body).encode() if body is not None else None,
                headers={"content-type": "application/json",
                         "Authorization": "Basic " + base64.b64encode(
                             f"{SOURCE_API_USERNAME}:{SOURCE_API_PASSWORD}".encode()).decode()},
                method=method)
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.loads(response.read())
        # The source SSE endpoint uses the same declared fixture credentials.
        # Expose its genuine listener address, never a manufactured event feed.
        api.url = endpoints[0]
        yield api
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        if process.stdout:
            process.stdout.close()

def create_empty_stock_sessions(workspace: Path, scratch: Path, *, delegated: bool) -> list[dict[str, Any]]:
    """Create real IDs before either run; import.ts:134-169 preserves them."""
    with stock_server(workspace, scratch, {"permission": "allow", "lsp": False}) as api:
        root = {"info": api("POST", "/session", {"title": "Compaction lane"}), "messages": []}
        return [root, {"info": api("POST", "/session", {
            "parentID": root["info"]["id"], "title": "Delegated fixture"}), "messages": []}] if delegated else [root]

def native_session_evidence(api, session_id: str) -> dict[str, Any]:
    """Readonly genuine source API evidence; never used as model history."""
    children = api("GET", "/session/" + session_id + "/children")
    return {"session": api("GET", "/session/" + session_id),
            "messages": api("GET", "/session/" + session_id + "/message"),
            "children": [{"session": child,
                          "messages": api("GET", "/session/" + child["id"] + "/message")}
                         for child in children]}

class NativePruneFence:
    """Observe actual owned part-update events before the next real human input."""

    def __init__(self, api, url: str, workspace: Path, session_id: str,
                 call_ids: list[str], password: str):
        self.session_id = session_id
        self.call_ids = call_ids
        self.expected = {}
        self.observed = {}
        self.ready = threading.Event()
        self.complete = threading.Event()
        self.stop = threading.Event()
        self.failure = None
        messages = api("GET", f"/session/{session_id}/message")
        for message in messages:
            for part in message["parts"]:
                if part.get("type") == "tool" and part.get("callID") in call_ids:
                    if part["sessionID"] != session_id or part["callID"] in self.expected:
                        raise ValueError("Prune fence requires unique owned native tool parts")
                    if part["state"]["status"] != "completed" or part["state"]["time"].get("compacted") is not None:
                        raise ValueError("Prune fence must subscribe before native pruning")
                    self.expected[part["callID"]] = {
                        "id": part["id"], "messageID": message["info"]["id"],
                        "tool": part["tool"],
                        "output_sha256": hashlib.sha256(part["state"]["output"].encode()).hexdigest(),
                    }
        if set(self.expected) != set(call_ids):
            raise ValueError("Prune fence omitted a genuine native tool part")
        endpoint = urllib.parse.urlsplit(url)
        self.connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=180)
        self.headers = {
            "x-opencode-directory": str(workspace.resolve()),
            "Authorization": "Basic " + base64.b64encode(
                f"{SOURCE_API_USERNAME}:{password}".encode()).decode(),
        }
        self.thread = threading.Thread(target=self._observe, daemon=True)

    def _observe(self) -> None:
        try:
            self.connection.request("GET", "/event", headers=self.headers)
            with self.connection.getresponse() as response:
                if response.status != 200:
                    raise RuntimeError(f"Native event subscription failed: {response.status}")
                self.ready.set()
                for line in response:
                    if self.stop.is_set():
                        break
                    if not line.startswith(b"data: "):
                        continue
                    event = json.loads(line[6:])
                    if event["type"] != "message.part.updated":
                        continue
                    part = event["properties"]["part"]
                    call_id = part.get("callID")
                    if part.get("sessionID") != self.session_id or call_id not in self.expected:
                        continue
                    expected = self.expected[call_id]
                    if part["id"] != expected["id"] or part["messageID"] != expected["messageID"]:
                        raise ValueError("Prune event changed its native part identity")
                    if (part.get("type") != "tool" or part.get("tool") != expected["tool"]
                            or part["state"]["status"] != "completed"):
                        raise ValueError("Prune event changed its native completed tool")
                    compacted = part["state"]["time"].get("compacted")
                    if type(compacted) is not int:
                        continue
                    digest = hashlib.sha256(part["state"]["output"].encode()).hexdigest()
                    if digest != expected["output_sha256"]:
                        raise ValueError("Prune fence observed changed output, not native prune")
                    self.observed[call_id] = {**expected, "compacted": compacted}
                    if set(self.observed) == set(self.expected):
                        self.complete.set()
                        break
                if not self.complete.is_set() and not self.stop.is_set():
                    raise RuntimeError("Native event subscription ended before prune completed")
        except Exception as error:
            if not self.stop.is_set():
                self.failure = error
                self.complete.set()
        finally:
            self.ready.set()

    def __enter__(self):
        self.thread.start()
        if not self.ready.wait(30):
            self.__exit__(None, None, None)
            raise TimeoutError("Native prune event subscription did not start")
        if self.failure is not None:
            self.__exit__(None, None, None)
            raise self.failure
        return self

    def wait(self) -> dict[str, Any]:
        if not self.complete.wait(180):
            raise TimeoutError("Native prune-complete events did not arrive")
        if self.failure is not None:
            raise self.failure
        return {"session_id": self.session_id, "event_type": "message.part.updated",
                "call_ids": self.call_ids, "native_parts": self.observed,
                "next_error_delivery": "Only after these genuine native prune events",
                "source": "packages/opencode/src/session/prompt.ts:716"}

    def __exit__(self, *_):
        self.stop.set()
        connection_socket = self.connection.sock
        if connection_socket is not None:
            try:
                connection_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.connection.close()
        self.thread.join(10)
        if self.thread.is_alive():
            raise RuntimeError("Native prune event observer did not stop")

def wait_stock_idle_checkpoint(api, session_id: str, base_url: str,
                               remaining_responses: int, answer: str) -> dict[str, Any]:
    """Fence scripted phases on genuine source idle and completed-answer evidence."""
    deadline = time.monotonic() + 180
    while True:
        with urllib.request.urlopen(base_url + "/lane/script-progress", timeout=10) as response:
            remaining = json.load(response)["remaining"]
        evidence = native_session_evidence(api, session_id)
        status = api("GET", "/session/status").get(session_id, {"type": "idle"})
        completed = any(
            message["info"]["role"] == "assistant"
            and message["info"].get("finish") == "stop"
            and message["info"].get("time", {}).get("completed")
            and not message["info"].get("summary")
            and not message["info"].get("error")
            and any(part.get("type") == "text" and part.get("text") == answer
                    for part in message["parts"])
            for message in evidence["messages"])
        checkpoint = {"native_evidence": evidence, "native_status": status,
                      "remaining_responses": remaining, "completed_answer": answer}
        if remaining == remaining_responses and status["type"] == "idle" and completed:
            return checkpoint
        if time.monotonic() >= deadline or remaining < remaining_responses:
            return {**checkpoint, "error": "Source idle/completed checkpoint did not match the scripted phase"}
        time.sleep(0.05)

def run_stock(harness: str, scenario: str, workspace: Path, scratch: Path, base_url: str,
              session_imports: list[dict[str, Any]] | None = None, mcp_relay: str | None = None) -> dict[str, Any]:
    settings = production_settings(harness)
    tools = {"*": False, **{name: True for name in settings["tools"]}}
    agent = {"tools": tools, **settings["params"]}
    config: dict[str, Any] = {"model": settings["model_id"], "provider": {"openai": {"options": {"baseURL": base_url, "apiKey": "lane-mock"}, "models": {settings["model"]: {"limit": {"context": TEST_WINDOW, "input": TEST_WINDOW, "output": settings["max_output_tokens"]}}}}}, "agent": {"build": agent}, "permission": "allow", "compaction": {"auto": True, "prune": True}}
    config["lsp"] = False
    if harness == "oh-my-opencode":
        config["plugin"] = [(plugin_root() / "dist/index.js").as_uri()]
        config.update(mcp_configuration(mcp_relay))
    env = stock_environment(config, scratch)
    cli = resolve_stock()
    session_id = None
    for index, fixture in enumerate(session_imports or ()):
        path = scratch / f"empty-session-{index}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(fixture))
        imported = subprocess.run([str(cli), "import", str(path)], cwd=workspace, env=env,
                                  capture_output=True, text=True, timeout=30)
        if imported.returncode or f"Imported session: {fixture['info']['id']}" not in imported.stdout:
            raise RuntimeError(f"Stock fixture import failed: {imported.stderr} {imported.stdout}")
        if not fixture["info"].get("parentID"):
            session_id = fixture["info"]["id"]
    if not session_id:
        raise ValueError("Stock requires its shared empty root session fixture")
    turns = []
    reset_mcp_relay(mcp_relay)
    with stock_server(workspace, scratch, config) as api:
        scripted_turns = scenario_turns(scenario, settings)
        idle_checkpoints = []
        prune_fences = []
        for index, turn in enumerate(scripted_turns):
            fence_context = (
                NativePruneFence(api, api.url, workspace, session_id,
                                 turn["native_prune_call_ids"], SOURCE_API_PASSWORD)
                if "native_prune_call_ids" in turn else nullcontext(None)
            )
            with fence_context as fence:
                try:
                    result = api("POST", "/session/" + session_id + "/message", {
                        "model": {"providerID": "openai", "modelID": settings["model"]},
                        "parts": [{"type": "text", "text": "\n" + turn["prompt"]}]})
                except TimeoutError:
                    failure = {"error": traceback.format_exc(), "failure_kind": "foreground_post_timeout",
                               "turns": turns, "session_id": session_id, "stock_config": config,
                               "idle_checkpoints": idle_checkpoints, "native_prune_fences": prune_fences}
                    # Read the same still-live source before its existing cleanup.
                    # Only native control-boundary ties can reject this oracle.
                    try:
                        failure["native_evidence"] = native_session_evidence(api, session_id)
                        failure["native_status"] = api("GET", "/session/status").get(session_id)
                    except Exception as observation_error:
                        failure["native_evidence_error"] = (
                            f"{type(observation_error).__name__}: {observation_error}")
                    return failure
                if fence is not None:
                    prune_fences.append(fence.wait())
            turns.append(result)
            error = result.get("info", {}).get("error")
            if error:
                # This scenario proves the source API error rather than
                # converting it into native overflow. Its plugin continues
                # asynchronously, and the normal completion barrier below
                # must still observe every scripted request and final answer.
                if not (scenario == "plugin_error" and error.get("name") == "APIError"
                        and error.get("data", {}).get("message") == "too many tokens: 74000 tokens > 64000 maximum"):
                    raise RuntimeError(f"Stock assistant failed: {error}")
            if "autonomous_responses" in turn:
                following = sum(len(next_turn["responses"]) + len(next_turn.get("autonomous_responses", []))
                                for next_turn in scripted_turns[index + 1:])
                checkpoint = wait_stock_idle_checkpoint(
                    api, session_id, base_url, following + len(turn["autonomous_responses"]),
                    turn["responses"][-1]["text"])
                idle_checkpoints.append(checkpoint)
                if "error" not in checkpoint:
                    with urllib.request.urlopen(base_url + "/lane/release-child", timeout=10) as response:
                        response.read()
                    checkpoint = wait_stock_idle_checkpoint(
                        api, session_id, base_url, following, turn["autonomous_responses"][-1]["text"])
                    idle_checkpoints.append(checkpoint)
                if "error" in checkpoint:
                    return {**checkpoint, "turns": turns, "session_id": session_id,
                            "stock_config": config, "idle_checkpoints": idle_checkpoints}
        # Plugin recovery forks summarize/prompt after the foreground POST
        # returns. Keep the real source process alive through the identical
        # scripted session, then observe its own root idle/completed boundary.
        deadline = time.monotonic() + 180
        while True:
            with urllib.request.urlopen(base_url + "/lane/script-progress", timeout=10) as response:
                remaining = json.load(response)["remaining"]
            evidence = native_session_evidence(api, session_id)
            assistants = [message["info"] for message in evidence["messages"]
                          if message["info"]["role"] == "assistant"]
            last = max(assistants, key=lambda info: info["time"]["created"]) if assistants else {}
            status = api("GET", "/session/status").get(session_id, {"type": "idle"})
            if remaining == 0 and status["type"] == "retry" and status.get("message") == "Mock provider script exhausted":
                return {"error": "Stock exhausted the identical scripted provider responses", "turns": turns, "session_id": session_id, "stock_config": config, "native_evidence": evidence, "native_status": status, "native_prune_fences": prune_fences}
            if remaining == 0 and status["type"] == "idle" and last.get("time", {}).get("completed"):
                if last.get("error") or last.get("summary"):
                    raise RuntimeError("Stock ended without a completed normal assistant answer")
                break
            if time.monotonic() >= deadline:
                return {"error": f"Stock scripted session did not finish: remaining={remaining}, status={status}, last={last}", "turns": turns, "session_id": session_id, "stock_config": config, "native_evidence": evidence, "native_status": status, "native_prune_fences": prune_fences}
            time.sleep(0.05)
    return {"turns": turns, "session_id": session_id, "stock_config": config,
            "native_evidence": evidence, "idle_checkpoints": idle_checkpoints,
            "native_prune_fences": prune_fences}
