"""Pinned stock Claude Code tool execution, enabled only by native_semantics.

The CLI is unmodified. The local proxy supplies main-loop tool calls and captures
stock's wire tool results. Only explicitly classified tool-model exchanges use
the configured provider. Source citations refer to @anthropic-ai/claude-code
2.1.63 cli.js; validation/tool_result wrapping is at line 3583.
"""
from __future__ import annotations

import atexit
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import threading
import uuid
from typing import Any, Callable
from .local_http_proxy import LocalHTTPProxy, send_payload
from .opencode_auxiliary import AuxiliaryCancelled, AuxiliaryTransport


STOCK_TOOLS = (
    "Agent", "TaskOutput", "Bash", "Glob", "Grep", "ExitPlanMode", "Read",
    "Edit", "Write", "NotebookEdit", "WebFetch", "TodoWrite", "WebSearch",
    "TaskStop", "AskUserQuestion", "Skill", "EnterPlanMode", "EnterWorktree",
)
def _default_package() -> Path:
    try:
        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        home = Path(os.environ.get("HOME") or os.path.expanduser("~"))
    return home / ".cache/bb-compaction-e4/pkgs/@anthropic-ai__claude-code@2.1.63/node_modules/@anthropic-ai/claude-code"


DEFAULT_PACKAGE = _default_package()
SUMMARY_PREFIX = "Your task is to create a detailed summary of the conversation so far,"


def _texts(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(p.get("text", "") for p in content if p.get("type") == "text")


def _system(body: dict) -> str:
    return _texts(body.get("system", []))


class StockExecutionError(RuntimeError):
    pass


class ClaudeToolSession:
    """One CLI per BB session preserves read guards, tasks, skills, and cwd."""

    def __init__(self, workspace: str, model: str, *, package: Path = DEFAULT_PACKAGE,
                 timeout: float = 180, auxiliary: Callable | None = None,
                 environment: dict | None = None, log_path: Path | None = None,
                 session_id: str | None = None, permission_mode: str | None = None,
                 permission: Callable | None = None, mirror_auto_compaction: bool = False,
                 cancel_permission: Callable | None = None,
                 auxiliary_transport: AuxiliaryTransport | None = None):
        manifest = json.loads((package / "package.json").read_text())
        if manifest.get("version") != "2.1.63":
            raise StockExecutionError("Claude tool executable must be version 2.1.63")
        self.timeout = timeout
        self.auxiliary = auxiliary
        self.auxiliary_transport = auxiliary_transport or AuxiliaryTransport()
        self.permission = permission
        self.cancel_permission = cancel_permission
        self.log_path = log_path
        self.records: list[dict] = []
        self.operation: dict | None = None
        self.failure: str | None = None
        self.closed = False
        self.lock = threading.Lock()
        self.close_lock = threading.Lock()
        self.handshake_lock = threading.Lock()
        self.stdin_lock = threading.Lock()
        self.completed = threading.Event()
        self.skill_fork_started = threading.Event()
        self.marker = "breadboard-stock-tool-session-" + str(uuid.uuid5(uuid.NAMESPACE_URL, session_id or str(Path(workspace).resolve())))
        self.main_system: str | None = None
        self.agent_systems: set[tuple[str, tuple]] = set()
        self.tool_calls = 0
        self.compaction_record_ids: set[str] = set()
        self.pending_compactions = 0
        self.mirror_auto_compaction = mirror_auto_compaction
        self.summary_supplied = threading.Event()
        self.last_transport: dict = {}
        self.main_response_ready = threading.Event()
        self.awaiting_main_request: dict | None = None
        self.staged_primary_response: dict | None = None
        self.pending_main_response: dict | None = None
        self.stderr: list[str] = []
        session = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                try:
                    raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    body = json.loads(raw)
                    if self.path.split("?", 1)[0] != "/v1/messages":
                        raise StockExecutionError(f"Unclassified Claude endpoint: {self.path}")
                    try:
                        kind = session.classify(body)
                    except StockExecutionError:
                        session.record("unclassified", body)
                        raise
                    session.record(kind, body)
                    session.last_transport = {"path": self.path, "headers": {
                        key.lower(): value for key, value in self.headers.items()
                        if key.lower() in {"anthropic-beta", "anthropic-version", "x-app"}
                    }}
                    if kind in {"agent", "web_fetch", "web_search"}:
                        if session.auxiliary is None:
                            raise StockExecutionError(f"No provider configured for Claude {kind} exchange")
                        status, headers, payload = session.auxiliary(body, dict(self.headers), self.path)
                        send_payload(self, status, headers, payload)
                        return
                    response = session.respond(kind, body)
                    if isinstance(response, dict):
                        payload = session.encode_native_response(response, body)
                    else:
                        usage = None
                        operation = session.operation or {}
                        if kind == "compact" and operation.get("summary_response") is not None:
                            usage = operation["summary_response"].usage
                        elif any(part["type"] == "tool_use" for part in response):
                            usage = operation.get("usage")
                        payload = session.encode_response(response, body, usage=usage)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream" if body.get("stream") else "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except AuxiliaryCancelled:
                    return
                except Exception as exc:
                    if session.closed:
                        return
                    session.failure = f"Claude stock execution proxy: {exc}"
                    session.completed.set()
                    payload = json.dumps({"type": "error", "error": {"type": "api_error", "message": session.failure}}).encode()
                    self.send_response(500)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

        self.server = LocalHTTPProxy(Handler)
        self.server_thread = self.server.listener
        self.server.start()
        env = dict(os.environ if environment is None else environment)
        env.update(ANTHROPIC_BASE_URL=f"http://127.0.0.1:{self.server.server_port}",
                   ANTHROPIC_API_KEY="breadboard-local-tool-proxy",
                   DISABLE_AUTO_COMPACT="0" if mirror_auto_compaction else "1",
                   CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
        for key in ("CLAUDECODE", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
            env.pop(key, None)
        # The existing launch policy admits Node runtime roots, then strips
        # loader variables from the child. Admit the pinned package read-only.
        env["NODE_PATH"] = os.pathsep.join(filter(None, (str(package), env.get("NODE_PATH"))))
        command = ["node", str(package / "cli.js"), "-p", "--model", model,
                   "--input-format", "stream-json", "--output-format", "stream-json",
                   "--verbose", "--strict-mcp-config", "--debug-to-stderr"]
        if session_id is not None:
            command.extend(["--session-id", session_id])
        if permission_mode is not None:
            command.extend(["--permission-mode", permission_mode])
        if permission is not None:
            command.extend(["--permission-prompt-tool", "stdio"])
        try:
            from breadboard_engine.security import build_restricted_process_command
            command, env = build_restricted_process_command(
                command, workspace=workspace, shell=False, environment=env,
                allow_network=True,
            )
            # The isolated child gets only a fixed local-proxy placeholder;
            # provider credentials remain in the parent broker exchange.
            env["ANTHROPIC_API_KEY"] = "breadboard-local-tool-proxy"
            env["CLAUDE_CODE_TMPDIR"] = env["TMPDIR"]  # cli.js:6340,1818
            self.process = subprocess.Popen(command, cwd=workspace, env=env, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            text=True, bufsize=1)
        except Exception:
            self.auxiliary_transport.close()
            self.server.close()
            raise
        self.output_thread = threading.Thread(target=self._read_output, daemon=True)
        self.error_thread = threading.Thread(target=self._read_errors, daemon=True)
        self.output_thread.start()
        self.error_thread.start()
        atexit.register(self.close)

    def record(self, kind: str, body: dict) -> None:
        record = {"kind": kind, "request": body}
        self.records.append(record)
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as output:
                output.write(json.dumps(record, ensure_ascii=False) + "\n")

    def classify(self, body: dict) -> str:
        """Fail closed. Never treat an unknown stock request as an auxiliary call."""
        messages = body.get("messages", [])
        if not messages:
            raise StockExecutionError("Unclassified Claude request without messages")
        last = _texts(messages[-1].get("content", []))
        if last.startswith(SUMMARY_PREFIX):  # cli.js:2209-2211
            return "compact"
        tools = body.get("tools", [])
        if any(t.get("type") == "web_search_20250305" for t in tools):  # cli.js:2930
            return "web_search"
        if last.startswith("\nWeb page content:\n---\n") and "\n---\n" in last[25:]:  # cli.js:399-412,2904
            return "web_fetch"
        system = _system(body)
        # cli.js:498: the billing suffix depends on the first user text and
        # changes at a compact boundary. This is classification only; forwarded
        # requests and recorded bytes are never normalized.
        system = re.sub(r"\Ax-anthropic-billing-header: cc_version=2\.1\.63\.[0-9a-f]{3}; cc_entrypoint=sdk-cli; cch=00000;\n", "", system)
        operation = self.operation or {}
        calls = operation.get("calls", [])
        skill_calls = [call for call in calls if call["name"] == "Skill"]
        loop_key = (system, tuple(t.get("name") for t in tools))
        if loop_key in self.agent_systems:
            if skill_calls:
                operation["skill_fork_classified"] = True
                self.skill_fork_started.clear()
            return "agent"
        for call in calls:
            if call["name"] != "Agent":
                continue
            arguments = operation.get("effective_inputs", {}).get(call["id"], call["input"])
            prompt = arguments.get("prompt")
            # cli.js:2730 appends the child prompt after inherited parent
            # messages. The inherited marker does not identify the current loop.
            if prompt and messages[-1].get("role") == "user" and last.endswith(prompt):
                self.agent_systems.add(loop_key)
                return "agent"
        if (operation.get("kind") == "context" or operation.get("auto_summary_replied")) and tuple(t.get("name") for t in tools) == STOCK_TOOLS:
            self.main_system = system
            return "main"
        if operation.get("sent") and tuple(t.get("name") for t in tools) == STOCK_TOOLS:
            expected = {call["id"] for call in calls}
            for index in range(len(messages) - 1, -1, -1):
                message = messages[index]
                content = message.get("content", [])
                if message.get("role") != "assistant" or not isinstance(content, list):
                    continue
                ids = {part.get("id") for part in content if part.get("type") == "tool_use"}
                if not ids:
                    continue
                observed = {
                    part.get("tool_use_id")
                    for following in messages[index + 1:] if following.get("role") == "user"
                    for part in following.get("content", [])
                    if isinstance(part, dict) and part.get("type") == "tool_result"
                }
                if expected and ids == expected and observed == expected:
                    self.main_system = system
                    return "main"
                break
        marked = any(self.marker in _texts(m.get("content", [])) for m in messages if m.get("role") == "user")
        if marked and tuple(t.get("name") for t in tools) == STOCK_TOOLS:
            # EnterWorktree changes stock's environment system block (3144).
            # The exact native catalog plus our seeded transcript marker
            # identifies main-loop traffic without guessing by model.
            self.main_system = system
            return "main"
        if (skill_calls and not operation.get("skill_fork_classified")
                and self.skill_fork_started.wait(min(self.timeout, 5))):
            # VUY emits this actual launch before jC's first request (2749).
            # Ee9 legacy commands have no baseDir, so ew4 omits the skill-root
            # prefix (1840-1842). Consume the launch once, then identify later
            # turns by the captured child system/catalog rather than prompt text.
            self.skill_fork_started.clear()
            operation["skill_fork_classified"] = True
            self.agent_systems.add(loop_key)
            return "agent"
        raise StockExecutionError("Unclassified stock Claude model request")

    def respond(self, kind: str, body: dict) -> list[dict] | dict:
        operation = self.operation
        if operation is None:
            raise StockExecutionError("Stock requested a model turn with no active execution")
        if kind == "compact":
            if self.mirror_auto_compaction and operation.get("kind") == "tool":
                captured = json.loads(json.dumps(body))
                prompt = captured["messages"][-1]["content"].pop()
                if prompt.get("type") != "text" or not prompt.get("text", "").startswith(SUMMARY_PREFIX):
                    raise StockExecutionError("Automatic compact request lost its summary prompt")
                self.capture_tool_results(captured, operation)
                operation["summary_request"] = body
                self.completed.set()
                if not self.summary_supplied.wait(self.timeout):
                    raise StockExecutionError("BB did not supply the pending automatic summary")
                operation["auto_summary_replied"] = True
                return self.native_summary_response
            if operation.get("kind") != "compact":
                raise StockExecutionError("Stock initiated compaction outside BB's compaction transition")
            return [{"type": "text", "text": operation["summary"]}]
        if kind != "main":
            raise StockExecutionError(f"No local response for request kind {kind}")
        if operation.get("auto_summary_replied"):
            operation["continuation_messages"] = body["messages"]
            operation["continuation_context"] = body
            return self._pause_main(body)
        if operation.get("kind") == "context":
            operation["request"] = body
            if self.mirror_auto_compaction:
                return self._pause_main(body)
            return [{"type": "text", "text": "Context captured."}]
        if operation.get("kind") == "compact_probe":
            captured = json.loads(json.dumps(body["messages"]))
            for message in reversed(captured):
                content = message.get("content")
                if isinstance(content, list) and content and content[-1].get("text") == self.marker:
                    content.pop()
                    if not content:
                        captured.remove(message)
                    break
            operation["messages"] = captured
            return [{"type": "text", "text": "Compaction state captured."}]
        if operation.get("sent"):
            self.capture_tool_results(body, operation)
            if self.mirror_auto_compaction:
                return self._pause_main(body)
            return [{"type": "text", "text": "Tool execution complete."}]
        operation["sent"] = True
        return [{"type": "tool_use", **call} for call in operation["calls"]]

    def capture_tool_results(self, body: dict, operation: dict):
        expected = {call["id"] for call in operation["calls"]}
        messages = body["messages"]
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            content = message.get("content", [])
            if message.get("role") != "assistant" or not isinstance(content, list):
                continue
            ids = {part.get("id") for part in content if part.get("type") == "tool_use"}
            if ids != expected:
                continue
            following = messages[index + 1:]
            if not following or any(message.get("role") != "user" for message in following):
                raise StockExecutionError("Unexpected content after stock tool batch")
            blocks = []
            for message in following:
                parts = message.get("content", [])
                if isinstance(parts, str):
                    parts = [{"type": "text", "text": parts}]
                blocks.extend({key: value for key, value in part.items() if key != "cache_control"} for part in parts)
            results = [part for part in blocks if part.get("type") == "tool_result"]
            if len(results) != len(expected) or {part.get("tool_use_id") for part in results} != expected:
                raise StockExecutionError("Stock continuation did not contain the complete correlated tool batch")
            operation["messages"] = following
            operation["blocks"] = blocks
            operation["request"] = body
            operation["tool_results_captured"] = True
            self.skill_fork_started.clear()
            return
        raise StockExecutionError("Stock continuation did not contain the correlated assistant tool turn")

    def _pause_main(self, body: dict) -> dict:
        with self.handshake_lock:
            if self.failure:
                raise StockExecutionError(self.failure)
            self.main_response_ready.clear()
            self.awaiting_main_request = body
            self.completed.set()
        if not self.main_response_ready.wait(self.timeout):
            raise StockExecutionError("BB did not answer the pending stock main request")
        if self.failure:
            raise StockExecutionError(self.failure)
        response = self.pending_main_response
        if response is None:
            raise StockExecutionError("Stock main request received no native response")
        self.awaiting_main_request = None
        return response

    def stage_primary_response(self, native_message: dict):
        with self.handshake_lock:
            if self.failure:
                raise StockExecutionError(self.failure)
            self.staged_primary_response = native_message

    def release_main_response(self, operation: dict, native_message: dict):
        with self.handshake_lock:
            if self.failure:
                raise StockExecutionError(self.failure)
            if self.awaiting_main_request is None:
                raise StockExecutionError("Stock has no pending main request")
            self.completed.clear()
            self.skill_fork_started.clear()
            self.summary_supplied.clear()
            self.operation = operation
            self.pending_main_response = native_message
            self.main_response_ready.set()

    def await_tool_result(self):
        if not self.completed.wait(self.timeout):
            raise StockExecutionError("Stock tool continuation timed out")
        if self.failure:
            raise StockExecutionError(self.failure)
        return self.operation

    @staticmethod
    def encode_native_response(message: dict, request: dict) -> bytes:
        if not request.get("stream"):
            return json.dumps(message).encode()
        events = [{"type": "message_start", "message": {**message, "content": [], "stop_reason": None}}]
        for index, block in enumerate(message["content"]):
            kind = block["type"]
            if kind == "tool_use":
                start, deltas = {**block, "input": {}}, [{"type": "input_json_delta", "partial_json": json.dumps(block["input"])}]
            elif kind == "text":
                start, deltas = {**block, "text": ""}, [{"type": "text_delta", "text": block["text"]}]
            elif kind == "thinking":
                start, deltas = {**block, "thinking": "", "signature": ""}, [
                    {"type": "thinking_delta", "thinking": block["thinking"]},
                    {"type": "signature_delta", "signature": block["signature"]},
                ]
            elif kind == "redacted_thinking":
                start, deltas = block, []
            else:
                raise StockExecutionError(f"Unsupported native model block: {kind}")
            events.append({"type": "content_block_start", "index": index, "content_block": start})
            events.extend({"type": "content_block_delta", "index": index, "delta": delta} for delta in deltas)
            events.append({"type": "content_block_stop", "index": index})
        events.extend([
            {"type": "message_delta", "delta": {
                key: message[key] for key in ("stop_reason", "stop_sequence", "stop_details", "container") if key in message
            },
             "usage": {"output_tokens": message["usage"]["output_tokens"]}},
            {"type": "message_stop"},
        ])
        return "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode()

    @staticmethod
    def encode_response(content: list[dict], body: dict, *, usage: dict | None = None) -> bytes:
        stop = "tool_use" if any(p["type"] == "tool_use" for p in content) else "end_turn"
        identity = uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([body, content], sort_keys=True))
        message = {"id": "msg_" + identity.hex, "type": "message", "role": "assistant",
                   "model": body["model"], "content": content, "stop_reason": stop,
                   "stop_sequence": None, "usage": usage if usage is not None else {"input_tokens": 1, "output_tokens": 1}}
        if not body.get("stream"):
            return json.dumps(message).encode()
        events = [{"type": "message_start", "message": {**message, "content": [], "stop_reason": None}}]
        for index, part in enumerate(content):
            events.append({"type": "content_block_start", "index": index,
                           "content_block": {**part, **({"input": {}} if part["type"] == "tool_use" else {"text": ""})}})
            delta = {"type": "input_json_delta", "partial_json": json.dumps(part["input"])} if part["type"] == "tool_use" else {"type": "text_delta", "text": part["text"]}
            events.extend(({"type": "content_block_delta", "index": index, "delta": delta},
                           {"type": "content_block_stop", "index": index}))
        events.extend(({"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": message["usage"]["output_tokens"]}},
                       {"type": "message_stop"}))
        return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()

    def _read_output(self):
        for line in self.process.stdout:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if item.get("type") == "control_request":
                try:
                    request = item.get("request", {})
                    if request.get("subtype") != "can_use_tool" or self.permission is None:
                        raise StockExecutionError(f"Unsupported stock control request: {request.get('subtype')}")
                    response = self.permission(request)
                    operation = self.operation or {}
                    call_id = request.get("tool_use_id")
                    if response.get("behavior") == "allow" and isinstance(response.get("updatedInput"), dict):
                        if any(call["id"] == call_id and call["name"] == request.get("tool_name")
                               for call in operation.get("calls", [])):
                            # cli.js:12529 passes the approved input to Rc6.
                            # Retain the genuine assistant input for replay;
                            # only auxiliary classification uses effective input.
                            operation.setdefault("effective_inputs", {})[call_id] = response["updatedInput"]
                    # cli.js:12507,12529: stock validates this SDK permission
                    # response and produces its own tool_result.
                    self._send({"type": "control_response", "response": {
                        "subtype": "success", "request_id": item["request_id"],
                        "response": response,
                    }})
                except Exception as exc:
                    self.failure = f"Claude stock permission exchange: {exc}"
                    self.completed.set()
            if item.get("type") == "result":
                if item.get("is_error"):
                    self.failure = f"Stock CLI failed: {item.get('result') or item.get('errors')}"
                self.completed.set()
        if self.operation and not self.completed.is_set():
            self.failure = f"Stock Claude exited with code {self.process.wait()}: {''.join(self.stderr)[-4000:]}"
            self.completed.set()

    def _read_errors(self):
        for line in self.process.stderr:
            self.stderr.append(line)
            operation = self.operation or {}
            skill_calls = [call for call in operation.get("calls", []) if call["name"] == "Skill"]
            if skill_calls and not operation.get("tool_results_captured"):
                # cli.js:9 y() emits a complete ISO timestamp + severity record;
                # headless je8(true) (12787) JSON-escapes a multiline body first.
                match = re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z \[DEBUG\] ([^\n]*)\n",
                    line,
                )
                if match:
                    body = match[1]
                    if body.startswith('"'):
                        try:
                            body = json.loads(body)
                        except ValueError:
                            continue
                        if not isinstance(body, str):
                            continue
                    # cli.js:2749 uses ECMAScript trim(), then substring(1)
                    # only when the trimmed name starts with one slash.
                    for call in skill_calls:
                        arguments = operation.get("effective_inputs", {}).get(call["id"], call["input"])
                        skill_name = arguments.get("skill")
                        if not isinstance(skill_name, str):
                            continue
                        skill_name = skill_name.strip(
                            "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
                            "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
                        ).removeprefix("/")
                        prefix = f"SkillTool executing forked skill {skill_name} with agent"
                        # D3Y (2012-2013) admits any nonempty string agent name.
                        # y() trims the whole record; impose no invented grammar.
                        if body == prefix or body.startswith(prefix + " "):
                            operation["skill_fork_classified"] = False
                            self.skill_fork_started.set()
                            break

    def _send(self, message: dict):
        with self.stdin_lock:
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()

    def _run(self, operation: dict, text: str):
        with self.handshake_lock:
            if self.failure or self.process.poll() is not None:
                raise StockExecutionError(self.failure or f"Stock Claude exited with code {self.process.returncode}")
            self.completed.clear()
            self.skill_fork_started.clear()
            self.operation = operation
        self._send({"type": "user", "message": {"role": "user", "content": text}})
        if not self.completed.wait(self.timeout):
            self.failure = f"Stock Claude execution timed out after {self.timeout:g} seconds"
            self.close()
        if self.failure:
            raise StockExecutionError(self.failure)
        return operation

    def execute(self, name: str, arguments: dict, *, call_id: str | None = None, usage: dict | None = None) -> dict:
        return self.execute_batch([{"id": call_id, "name": name, "input": arguments}], usage=usage)

    def execute_batch(self, calls: list[dict], *, usage: dict | None = None) -> dict:
        """Inject one assistant turn; stock owns safe parallel/unsafe serial execution.

        cli.js:2577 KpY/YpY/zpY and :3583 pl6 classify and schedule the batch.
        """
        with self.lock:
            if self.failure:
                raise StockExecutionError(self.failure)
            if not calls:
                raise StockExecutionError("Stock tool batch is empty")
            calls = [{**call, "id": call.get("id") or f"toolu_bb_{self.tool_calls + index}"}
                     for index, call in enumerate(calls)]
            if len({call["id"] for call in calls}) != len(calls):
                raise StockExecutionError("Stock tool batch contains duplicate call ids")
            if self.mirror_auto_compaction and usage is None:
                raise StockExecutionError("Automatic mirror requires actual BB provider usage")
            with self.handshake_lock:
                if self.failure:
                    raise StockExecutionError(self.failure)
                self.summary_supplied.clear()
            operation = {"kind": "tool", "calls": calls, "usage": usage}
            if self.mirror_auto_compaction:
                native = self.staged_primary_response
                if native is None:
                    raise StockExecutionError("Stock tool batch has no genuine staged provider response")
                native_calls = [{key: part[key] for key in ("id", "name", "input")}
                                for part in native.get("content", []) if part.get("type") == "tool_use"]
                if native_calls != calls:
                    raise StockExecutionError("Stock tool batch differs from the staged provider response")
                operation["sent"] = True
                self.release_main_response(operation, native)
                self.staged_primary_response = None
                operation = self.await_tool_result()
            else:
                operation = self._run(operation, self.marker)
            self.tool_calls += len(calls)
            blocks = operation.get("blocks")
            if not blocks:
                raise StockExecutionError("Stock CLI finished without producing a tool result")
            return {"__claude_tool_result_blocks": blocks,
                    "__claude_tool_result_messages": operation["messages"],
                    "__mvi_text_output": "\n".join(_texts(part["content"]) for part in blocks if part.get("type") == "tool_result")}

    def capture_context(self, prompt: str) -> dict:
        """Render current vendor context in the live stock session."""
        with self.lock:
            return self._run({"kind": "context"}, prompt)["request"]

    def complete_auto_compaction(self, response):
        """Finish SG6(auto=true) with the genuine BB summary; cli.js:2207,2209."""
        with self.lock:
            with self.handshake_lock:
                if self.failure:
                    raise StockExecutionError(self.failure)
                operation = self.operation or {}
                if "summary_request" not in operation:
                    raise StockExecutionError("Stock has no pending automatic compaction")
                self.completed.clear()
                operation["summary_response"] = response
                self.summary_supplied.set()
            if not self.completed.wait(self.timeout):
                raise StockExecutionError("Stock automatic continuation timed out")
            if self.failure:
                raise StockExecutionError(self.failure)
            messages = operation.get("continuation_messages")
            if not messages:
                raise StockExecutionError("Stock did not produce an automatic continuation")
            self.pending_compactions += 1
            return messages

    def compact(self, summary: str):
        """Drive stock's clear-and-restore transition, cli.js:2209,2211."""
        with self.lock:
            prior = sum(r["kind"] == "compact" for r in self.records)
            operation = self._run({"kind": "compact", "summary": summary}, "/compact")
            if sum(r["kind"] == "compact" for r in self.records) != prior + 1:
                raise StockExecutionError("Stock CLI did not execute its compaction transition")
            probe = self._run({"kind": "compact_probe"}, self.marker)
            return probe["messages"]

    def close(self):
        with self.close_lock:
            if self.closed:
                return
            try:
                with self.handshake_lock:
                    self.failure = self.failure or "Stock Claude session closed"
                    self.main_response_ready.set()
                    self.summary_supplied.set()
                    self.completed.set()
                if self.cancel_permission is not None:
                    self.cancel_permission()
                self.auxiliary_transport.close()
                process = getattr(self, "process", None)
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                if getattr(self, "server", None) is not None:
                    self.server.close()
                for thread in (self.server_thread, getattr(self, "output_thread", None), getattr(self, "error_thread", None)):
                    if thread is not None and thread is not threading.current_thread():
                        thread.join(timeout=5)
                        if thread.is_alive():
                            raise StockExecutionError("Stock Claude shutdown left a live pipe/server reader")
                if process is not None:
                    for pipe in (process.stdin, process.stdout, process.stderr):
                        if pipe is not None:
                            pipe.close()
                self.closed = True
            finally:
                atexit.unregister(self.close)


def get_claude_tool_session(conductor, *, session_state=None):
    """Create the session once, shared by context rendering and tool execution."""
    if session_state is None:
        session_state = getattr(conductor, "_active_session_state", None)
    session = getattr(session_state, "_claude_tool_session", None) if session_state else getattr(conductor, "_claude_tool_session", None)
    if session is None:
        tools = conductor.config.get("tools", {})
        driver = conductor.config.get("workspace", {}).get("sandbox", {}).get("driver", "process")
        if driver != "process":
            raise StockExecutionError("The Claude stock tool profile requires a local process workspace; it is daily-driving only")
        providers = conductor.config["providers"]
        model = providers["default_model"].removeprefix("anthropic/")
        session_id = session_state.get_provider_metadata("session_id") if session_state is not None else None
        # BB standalone IDs are opaque run-<hex> strings (agent.py:27).
        # The native CLI requires UUIDs. Preserve explicit UUID admissions;
        # derive other native identities from the existing BB admission.
        native_session_id = session_id
        if session_id is not None and not re.fullmatch(
            r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", session_id
        ):
            native_session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "breadboard/claude-code/session/" + session_id))

        auxiliary_transport = AuxiliaryTransport()
        def auxiliary(body, headers, path):
            from breadboard_engine.provider_broker import get_provider_broker
            with get_provider_broker().execution_material(
                "anthropic", session_id=str(session_id or ""),
                environment_key="ANTHROPIC_API_KEY",
            ) as material:
                if material is None:
                    raise StockExecutionError("Claude auxiliary exchange requires the session's Anthropic credential")
                key = material.get("api_key")
                token = material.get("access_token")
                if not key and not token:
                    raise StockExecutionError("Claude auxiliary exchange requires the session's Anthropic credential")
                upstream = str(material.get("base_url") or "https://api.anthropic.com").rstrip("/")
                outgoing = {k: v for k, v in headers.items() if k.lower() not in {"host", "content-length", "authorization", "x-api-key"}}
                outgoing.update({"Authorization": f"Bearer {token}"} if token else {"x-api-key": key})
                return auxiliary_transport.forward(
                    str(session_id or ""), upstream + path, outgoing,
                    json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(),
                )

        log_dir = Path(getattr(getattr(conductor, "logger_v2", None), "run_dir", conductor.workspace))
        permission = None
        cancel_permission = None
        if session_state is not None and session_state.get_provider_metadata("permission_queue") is not None:
            from breadboard_engine.permissions.broker import PermissionBroker
            broker = getattr(conductor, "permission_broker", None) or PermissionBroker(conductor.config)
            from breadboard_engine.permissions.claude_permission import request_stock_tool_permission
            permission = lambda request: request_stock_tool_permission(broker, session_state, request)
            cancel_permission = lambda: broker.cancel_pending_requests(session_state)
        session = ClaudeToolSession(str(conductor.workspace), model, auxiliary=auxiliary,
                                   auxiliary_transport=auxiliary_transport,
                                   package=Path(tools.get("stock_package", DEFAULT_PACKAGE)),
                                   session_id=native_session_id,
                                   log_path=log_dir / "claude-tool-exchanges.jsonl",
                                   permission_mode=tools.get("stock_permission_mode"),
                                   permission=permission,
                                   cancel_permission=cancel_permission,
                                   mirror_auto_compaction=bool((conductor.config.get("prompts") or {}).get("summary_projection")))
        setattr(session_state if session_state else conductor, "_claude_tool_session", session)
        from .stock_tools import register_stock_session
        register_stock_session(conductor, session)
    return session


def execute_claude_tool(conductor, name: str, arguments: dict, *, tool_call: dict) -> dict:
    """Exact-name dispatch before the generic alias registry; no native fallback."""
    return execute_claude_tool_batch(conductor, [{
        "id": tool_call.get("id") or tool_call.get("call_id"), "name": name, "input": arguments,
    }])


def execute_claude_tool_batch(conductor, calls: list[dict], *, session_state=None) -> dict:
    """Execute all native calls from the admitted provider assistant turn."""
    for call in calls:
        if call["name"] not in STOCK_TOOLS:
            raise StockExecutionError(f"Tool name is not advertised by the Claude profile: {call['name']}")
    if session_state is None:
        session_state = getattr(conductor, "_active_session_state", None)
    session = get_claude_tool_session(conductor, session_state=session_state)
    state = getattr(session_state, "compaction_state", None)
    for record in getattr(state, "records", ()):
        if record.is_boundary and record.record_id not in session.compaction_record_ids:
            if session.pending_compactions:
                session.pending_compactions -= 1
            elif session.tool_calls:
                session.compact(record.summary or "")
            session.compaction_record_ids.add(record.record_id)
    usage = session_state.get_provider_metadata("usage") if session_state is not None else None
    return session.execute_batch(calls, usage=usage)
