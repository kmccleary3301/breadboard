"""OpenCode 1.2.17 execution through its unmodified server and model transport.

Only dated profiles opt in. The proxy labels requests by stock session/agent,
feeds BB's parent responses to stock, and forwards only identified auxiliaries.
Sources: opencode 715b844c src/server/routes/session.ts:484-540,692-798;
src/session/llm.ts:133-145,208-233; src/session/compaction.ts:58-98,235-294.
"""
from __future__ import annotations

import atexit
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable
import urllib.error
import urllib.request
import uuid

from .opencode_auxiliary import AuxiliaryCancelled, AuxiliaryTransport


SEMANTICS = {"opencode_1_2_17", "oh_my_opencode_3_10_0"}
DEFAULT_BINARY = Path.home() / ".cache/bb-compaction-e4/pkgs/opencode-darwin-arm64@1.2.17/package/bin/opencode"
LABEL_HEADERS = {"x-bb-stock-session", "x-bb-stock-agent", "x-bb-stock-provider"}
LABEL_PLUGIN = '''export const BreadboardTransportLabels = async () => ({
  "chat.headers": async (input, output) => {
    output.headers["x-bb-stock-session"] = input.sessionID;
    output.headers["x-bb-stock-agent"] = input.agent.name;
    output.headers["x-bb-stock-provider"] = input.model.providerID;
  }
});
'''


class StockExecutionError(RuntimeError):
    """A stock-side failure must never fall back to BB's executors."""


def upstream_headers(headers: dict) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in
            LABEL_HEADERS | {"host", "content-length", "authorization", "connection", "x-api-key", "accept-encoding"}}


def responses_payload(response: dict, stream: bool) -> bytes:
    """Emit the Responses events stock's AI SDK consumes, without changing output."""
    if not stream:
        return json.dumps(response).encode()
    events = [{"type": "response.created", "response": {**response, "status": "in_progress", "output": []}}]
    for index, completed in enumerate(response["output"]):
        item = copy.deepcopy(completed)
        if item["type"] == "function_call":
            item["arguments"] = ""
        elif item["type"] == "message":
            item["content"] = []
        elif item["type"] == "reasoning":
            item["summary"] = []
        item["status"] = "in_progress"
        events.append({"type": "response.output_item.added", "output_index": index, "item": item})
        if completed["type"] == "function_call":
            events.extend([
                {"type": "response.function_call_arguments.delta", "item_id": item["id"], "output_index": index, "delta": completed["arguments"]},
                {"type": "response.function_call_arguments.done", "item_id": item["id"], "output_index": index, "arguments": completed["arguments"]},
            ])
        elif completed["type"] == "message":
            for ci, part in enumerate(completed["content"]):
                if part["type"] not in {"output_text", "refusal"}:
                    raise StockExecutionError(f"Unsupported parent Responses content: {part['type']}")
                base = {"item_id": item["id"], "output_index": index, "content_index": ci}
                key = "text" if part["type"] == "output_text" else "refusal"
                event_type = "output_text" if key == "text" else "refusal"
                events.extend([
                    {"type": "response.content_part.added", **base, "part": {**part, key: ""}},
                    {"type": f"response.{event_type}.delta", **base, "delta": part[key]},
                    {"type": f"response.{event_type}.done", **base, key: part[key]},
                    {"type": "response.content_part.done", **base, "part": part},
                ])
        elif completed["type"] == "reasoning":
            for si, part in enumerate(completed.get("summary", ())):
                if part.get("type") != "summary_text":
                    raise StockExecutionError("Unsupported parent reasoning summary")
                base = {"item_id": item["id"], "output_index": index, "summary_index": si}
                events.extend([
                    {"type": "response.reasoning_summary_part.added", **base, "part": {**part, "text": ""}},
                    {"type": "response.reasoning_summary_text.delta", **base, "delta": part["text"]},
                    {"type": "response.reasoning_summary_text.done", **base, "text": part["text"]},
                    {"type": "response.reasoning_summary_part.done", **base, "part": part},
                ])
        else:
            raise StockExecutionError(f"Unsupported parent Responses item: {completed['type']}")
        events.append({"type": "response.output_item.done", "output_index": index, "item": completed})
    events.append({"type": "response.completed", "response": response})
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def response_from_result(result: Any, model: str) -> dict:
    raw = result.raw_response
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump(mode="json")
    if isinstance(raw, dict) and isinstance(raw.get("output"), list):
        return copy.deepcopy(raw)
    output = []
    for message in result.messages:
        if message.content:
            if not isinstance(message.content, str):
                raise StockExecutionError("Parent response requires raw Responses output for non-text content")
            output.append({"type": "message", "id": "msg_" + uuid.uuid4().hex,
                           "role": "assistant", "status": "completed",
                           "content": [{"type": "output_text", "text": message.content, "annotations": []}]})
        for call in message.tool_calls:
            output.append({"type": "function_call", "id": "fc_" + uuid.uuid4().hex,
                           "call_id": call.id, "name": call.name, "arguments": call.arguments,
                           "status": "completed"})
    usage = result.usage or {}
    return {"id": "resp_" + uuid.uuid4().hex, "object": "response", "model": model,
            "created_at": int(time.time()), "status": "completed", "output": output,
            "usage": {"input_tokens": usage.get("input_tokens", usage.get("prompt_tokens", 0)),
                      "output_tokens": usage.get("output_tokens", usage.get("completion_tokens", 0)),
                      "input_tokens_details": {"cached_tokens": usage.get("cache_read_tokens", 0)},
                      "output_tokens_details": {"reasoning_tokens": 0}}}


class OpenCodeToolSession:
    """One server/root session per BB session. Child sessions stay stock-owned."""

    def __init__(self, workspace: str, model: str, *, binary: Path = DEFAULT_BINARY,
                 plugin_root: Path | None = None, timeout: float = 180,
                 auxiliary: Callable | None = None, stock_config: dict | None = None,
                 environment: dict | None = None, log_path: Path | None = None,
                 on_close: Callable | None = None,
                 interaction: Callable | None = None, protected_paths: tuple[str, ...] = (),
                 read_only_roots: list[dict] | None = None,
                 lsp_root: dict | None = None,
                 stock_worktree_read_only: bool = False,
                 data_root: Path | None = None,
                 session_imports: list[dict] | None = None, imported_session_id: str | None = None,
                 headless: bool = False, pty: bool = False):
        self._close_mutex = threading.Lock()
        binary = Path(binary).expanduser().resolve()
        plugin_root = Path(plugin_root).expanduser().resolve() if plugin_root is not None else None
        self.workspace = str(Path(workspace).resolve())
        self.model = model
        self.timeout = timeout
        self.auxiliary = auxiliary
        self.log_path = log_path
        self.interaction = interaction
        self.requires_plugin = plugin_root is not None
        self.events_ready = threading.Event()
        self.event_connection = None
        self.records: list[dict] = []
        self.condition = threading.Condition()
        self.pending: dict | None = None
        self.failure: str | None = None
        self.closed = False
        self.results: dict[str, dict] = {}
        self.compaction_record_ids: set[str] = set()
        self.pruned_call_ids: set[str] = set()
        self.stderr: list[str] = []
        self.url: str | None = None
        self.session_id: str | None = None
        self.root_agent: str | None = None
        self.compaction_summary: str | None = None
        self.compaction_response: dict | None = None
        self.compaction_pending: dict | None = None
        self.request_done = threading.Event()
        self.request_done.set()
        self.native_prompt_done = threading.Event()
        self.native_prompt_done.set()
        self.source_recovery_pending: Callable[[], bool] | None = None
        self.stock_input_handler: Callable[[dict], None] | None = None
        self.run_active = False
        self.final_response_pending = False
        self.active_user_id: str | None = None
        self._overflow_user_id: str | None = None
        self.recovery_outcome: str | None = None
        self.headless = headless
        self.headless_idle_seen = False
        self.observed_user_ids: set[str] = set()
        self.interaction_threads: dict[threading.Thread, dict] = {}
        self.interaction_context = threading.local()
        self.on_abort: Callable | None = None
        self.ready = threading.Event()
        from ..security.path_policy import prepare_workspace_temp_directory
        temp_root = prepare_workspace_temp_directory(Path(self.workspace))
        self.scratch = tempfile.TemporaryDirectory(prefix="opencode-tools-", dir=temp_root)
        scratch = Path(self.scratch.name)
        plugin = scratch / "transport-labels.mjs"
        self.binding_path = None
        self.binding = None
        if data_root is not None:
            durable = Path(os.path.abspath(data_root))
            workspace_path = Path(self.workspace)
            if not durable.is_relative_to(workspace_path) or durable == workspace_path:
                self.scratch.cleanup()
                raise StockExecutionError("Native session data must remain under its BB workspace session store")
            if any(path.is_symlink() for path in (durable, *durable.parents) if path.is_relative_to(workspace_path)):
                self.scratch.cleanup()
                raise StockExecutionError("Native session data cannot use symlinked storage")
            durable.mkdir(parents=True, exist_ok=True)
            self.binding_path = durable / "root.json"
            if self.binding_path.exists():
                self.binding = json.loads(self.binding_path.read_text())
                if self.binding.get("workspace") != self.workspace or self.binding.get("model") != model:
                    self.scratch.cleanup()
                    raise StockExecutionError("Native resumed session workspace/model differs from its BB binding")
                self.compaction_record_ids.update(self.binding.get("compaction_records", []))
                self.pruned_call_ids.update(self.binding.get("pruned_calls", []))
        else:
            durable = scratch
        plugin.write_text(LABEL_PLUGIN)
        config = copy.deepcopy(stock_config or {})
        self.on_close = on_close
        plugins = list(config.get("plugin", []))
        if plugin_root is not None:
            metadata = json.loads((plugin_root / "package.json").read_bytes())
            if metadata.get("name") != "oh-my-opencode" or metadata.get("version") != "3.10.0":
                self.scratch.cleanup()
                raise StockExecutionError("Expected oh-my-opencode@3.10.0")
            if not any(Path(entry["path"]).expanduser().resolve() == plugin_root.resolve()
                       for entry in (read_only_roots or ())):
                self.scratch.cleanup()
                raise StockExecutionError("OMO requires a profile-declared digest-pinned plugin read-only root")
            staged_sg = plugin_root / "node_modules/@ast-grep/cli/sg"
            if not staged_sg.is_file() or not os.access(staged_sg, os.X_OK):
                self.scratch.cleanup()
                raise StockExecutionError(
                    "Missing staged stock ast-grep 0.40.0; run PYTHONPATH=. python "
                    "scripts/compaction_lanes/stage_opencode_stock_dependencies.py"
                )
            with staged_sg.open("rb") as handle:
                if hashlib.file_digest(handle, "sha256").hexdigest() != (
                    "a8de4e534206a71030ae7151f049cb4513548b4e5e9e88a71ed0b73b041e5ace"
                ):
                    self.scratch.cleanup()
                    raise StockExecutionError(
                        "Staged stock ast-grep 0.40.0 digest mismatch; run PYTHONPATH=. python "
                        "scripts/compaction_lanes/stage_opencode_stock_dependencies.py"
                    )
            plugins.append((plugin_root / "dist/index.js").as_uri())
        plugins.append(plugin.as_uri())
        config.update(model=model, plugin=plugins)
        provider_id, model_id = model.split("/", 1)
        if provider_id != "openai":
            raise StockExecutionError("Pinned OpenCode parent transport requires openai Responses")
        session = self
        self.auxiliary_transport = AuxiliaryTransport()
        self.auxiliary_request = threading.local()
        self.closing = False
        staged_lsp = None
        if lsp_root is not None:
            try:
                staged_lsp = Path(lsp_root["path"]).expanduser().resolve(strict=True)
                required = ("node_modules/.bin/typescript-language-server", "node_modules/.bin/pyright-langserver",
                            "node_modules/typescript/lib/tsserver.js", "node_modules/pyright/dist/pyright-langserver.js")
                if not all((staged_lsp / path).is_file() for path in required) or not (staged_lsp / "install-cache").is_dir():
                    raise FileNotFoundError("Incomplete staged LSP packages/cache")
            except (KeyError, TypeError, OSError) as exc:
                self.scratch.cleanup()
                raise StockExecutionError(
                    "Missing staged TypeScript/Pyright LSP; run PYTHONPATH=. python scripts/compaction_lanes/stage_opencode_lsp.py"
                ) from exc
            read_only_roots = [*(read_only_roots or []), lsp_root]

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                try:
                    raw_body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    body = json.loads(raw_body)
                    labels = {k.lower(): v for k, v in self.headers.items()}
                    kind = session.classify(labels)
                    session.auxiliary_request.session_id = labels["x-bb-stock-session"]
                    session.record(kind, body, labels)
                    if kind in {"main", "compaction"}:
                        session.native_prompt_done.clear()
                        with session.condition:
                            for item in body.get("input", []):
                                if item.get("type") == "function_call_output":
                                    session.results[item["call_id"]] = copy.deepcopy(item)
                    if kind in {"child", "title", "summary"}:
                        if session.auxiliary is None:
                            raise StockExecutionError(f"No real provider configured for stock {kind}")
                        status, headers, payload = session.auxiliary(body, upstream_headers(dict(self.headers)), self.path,
                                                                    labels["x-bb-stock-provider"], raw_body=raw_body)
                    elif kind == "compaction":
                        with session.condition:
                            if session.compaction_pending is not None:
                                raise StockExecutionError("Concurrent root compaction requests")
                            pending = {"body": body, "cancelled": False, "summary": None, "response": None}
                            session.compaction_pending = pending
                            if session._overflow_user_id is not None:
                                session.recovery_outcome = "summary"
                                session._overflow_user_id = None
                            session.announce_stock_input(pending)
                            session.condition.notify_all()
                            session.condition.wait_for(lambda: pending["summary"] is not None or pending["cancelled"] or
                                                       session.closed or session.failure)
                            if pending["cancelled"] or session.closed:
                                return
                            session.check_alive()
                            summary = pending["summary"]
                            native_response = pending["response"] or session.text_response(summary)
                            session.compaction_pending = None
                            session.condition.notify_all()
                        payload = responses_payload(native_response, bool(body.get("stream")))
                        status, headers = 200, {"content-type": "text/event-stream" if body.get("stream") else "application/json"}
                    else:
                        if self.path != "/v1/responses":
                            raise StockExecutionError(f"Unknown parent endpoint {self.path}")
                        pending = {"body": body, "response": None, "cancelled": False}
                        with session.condition:
                            if session.pending is not None:
                                raise StockExecutionError("Concurrent root model requests")
                            session.pending = pending
                            recovery_from = session._overflow_user_id
                            if recovery_from is not None:
                                native_user = session._latest_user_message()
                                if native_user["info"]["id"] != recovery_from:
                                    pending["recovery_from_message_id"] = recovery_from
                            session.announce_stock_input(pending)
                            if pending.get("recovery_from_message_id") is not None:
                                session.recovery_outcome = "handled"
                                session._overflow_user_id = None
                            session.condition.notify_all()
                            session.condition.wait_for(lambda: pending["response"] is not None or pending["cancelled"] or session.closed)
                            if pending["cancelled"] or session.closed:
                                return
                            response = pending["response"]
                        if "provider_error" in pending:
                            status, headers, payload = pending["provider_error"]
                        else:
                            payload = responses_payload(response, bool(body.get("stream")))
                            status, headers = 200, {"content-type": "text/event-stream" if body.get("stream") else "application/json"}
                    self.send_response(status)
                    for key, value in headers.items():
                        if key.lower() not in {"content-length", "transfer-encoding", "connection", "content-encoding"}:
                            self.send_header(key, value)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except AuxiliaryCancelled:
                    return
                except (BrokenPipeError, ConnectionResetError):
                    if not session.closed and not session.closing and session.compaction_summary is None:
                        session.fail("Stock closed its provider connection unexpectedly")
                except Exception as exc:
                    session.record("unclassified", {"error": str(exc)}, {})
                    session.fail(str(exc))
                    payload = json.dumps({"error": {"message": str(exc), "type": "stock_execution_error"}}).encode()
                    self.send_response(500)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

        self.proxy = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.proxy.daemon_threads = True
        threading.Thread(target=self.proxy.serve_forever, daemon=True).start()
        providers = config.setdefault("provider", {})
        # Stock autoloads free Zen models without auth (provider.ts:131-150).
        # Preserve that availability and intercept it without inventing a key.
        providers.setdefault("opencode", {})
        for pid in set(providers) | {"openai"}:
            options = providers.setdefault(pid, {}).setdefault("options", {})
            options["baseURL"] = f"http://127.0.0.1:{self.proxy.server_port}/v1"
            if pid != "opencode":
                options["apiKey"] = "bb-local-tool-proxy"
        from ..security.child_environment import build_child_environment
        overrides = {**(environment or {}), "OPENCODE_CONFIG_CONTENT": json.dumps(config),
                     "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1",
                     "OPENCODE_SERVER_PASSWORD": uuid.uuid4().hex}
        overrides.update(
            HOME=str(scratch / "home"), OPENCODE_TEST_HOME=str(scratch / "home"),
            **{"XDG_" + name.upper() + "_HOME": str(scratch / name)
               for name in ("config", "data", "cache", "state")},
        )
        overrides["XDG_DATA_HOME"] = str(durable / "data")
        overrides["XDG_STATE_HOME"] = str(durable / "state")
        overrides["OPENCODE_DISABLE_LSP_DOWNLOAD"] = "1"
        if staged_lsp is not None:
            overrides["PATH"] = str(staged_lsp / "node_modules/.bin") + os.pathsep + os.environ.get("PATH", "")
            overrides["NODE_PATH"] = str(staged_lsp / "node_modules")
            overrides["BUN_INSTALL_CACHE_DIR"] = str(staged_lsp / "install-cache")
        env = build_child_environment(os.environ, overrides, allowed_override_keys=overrides)
        self.password = env["OPENCODE_SERVER_PASSWORD"]
        try:
            # Construct the credential boundary against the real host HOME.
            # The isolated stock HOME is a fresh directory, not a credential root.
            env["HOME"] = os.environ.get("HOME", str(Path.home()))
            from ..security import build_restricted_process_command
            self.worktree_root = None
            self.git_metadata_roots = ()
            if stock_worktree_read_only:
                from ..security.stock_worktree import resolve_stock_worktree
                self.worktree_root, self.git_metadata_roots = resolve_stock_worktree(
                    self.workspace, env, isolated_home=str(scratch / "home"),
                    protected_paths=protected_paths, timeout=timeout,
                )
            command, child_env = build_restricted_process_command(
                [str(binary), "serve", "--hostname", "127.0.0.1", "--port", "0"],
                workspace=self.workspace, shell=False, environment=env,
                protected_paths=protected_paths, allow_network=True,
                pty=pty,
                read_only_roots=read_only_roots or (),
                read_only_worktree_root=self.worktree_root,
                read_only_git_metadata_roots=self.git_metadata_roots,
            )
            child_env["HOME"] = str(scratch / "home")
            if session_imports is not None:
                if not session_imports or not imported_session_id:
                    raise StockExecutionError("Native fixture imports require a selected empty root session")
                roots = [item["info"]["id"] for item in session_imports
                         if isinstance(item, dict) and isinstance(item.get("info"), dict) and
                         not item["info"].get("parentID")]
                if imported_session_id not in roots:
                    raise StockExecutionError("Selected root is missing from native fixture imports")
                for index, item in enumerate(session_imports):
                    if not isinstance(item, dict) or not isinstance(item.get("info"), dict) or item.get("messages") != []:
                        raise StockExecutionError("Native fixture imports accept only pre-run empty stock sessions")
                    fixture = scratch / f"session-import-{index}.json"
                    fixture.write_text(json.dumps(item))
                    import_command, import_env = build_restricted_process_command(
                        [str(binary), "import", str(fixture)], workspace=self.workspace, shell=False,
                        environment=env, protected_paths=protected_paths, allow_network=True,
                        read_only_roots=read_only_roots or (),
                        read_only_worktree_root=self.worktree_root,
                        read_only_git_metadata_roots=self.git_metadata_roots,
                    )
                    import_env["HOME"] = str(scratch / "home")
                    imported = subprocess.run(import_command, cwd=self.workspace, env=import_env,
                                              capture_output=True, text=True, timeout=timeout)
                    if imported.returncode or f"Imported session: {item['info']['id']}" not in imported.stdout:
                        raise StockExecutionError(f"Native stock session import failed: {imported.stderr} {imported.stdout}")
            self.process = subprocess.Popen(command, cwd=self.workspace, env=child_env,
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            text=True, bufsize=1)
            threading.Thread(target=self._stdout, daemon=True).start()
            threading.Thread(target=self._stderr, daemon=True).start()
            threading.Thread(target=self._watch, daemon=True).start()
            if not self.ready.wait(timeout):
                raise StockExecutionError("Stock OpenCode server startup timed out")
            self.check_alive()
            health = self.api("GET", "/global/health")
            if health.get("version") != "1.2.17":
                raise StockExecutionError(f"Expected stock OpenCode 1.2.17, got {health.get('version')!r}")
            if self.worktree_root is not None:
                native_root = Path(self.api("GET", "/path")["worktree"]).resolve()
                if native_root != self.worktree_root.resolve():
                    raise StockExecutionError(
                        f"Stock worktree discovery mismatch: {native_root} != {self.worktree_root}"
                    )
            # Project configuration remains stock-read, but cannot bypass proxy
            # routing. Fail before any session/model turn if it adds an endpoint.
            effective = self.api("GET", "/config")
            proxy_url = f"http://127.0.0.1:{self.proxy.server_port}/v1"
            for pid, provider in effective.get("provider", {}).items():
                if provider.get("options", {}).get("baseURL") != proxy_url:
                    raise StockExecutionError(f"Stock project provider {pid} is not routed through the BB proxy")
                for mid, entry in provider.get("models", {}).items():
                    endpoint = (entry.get("provider") or {}).get("api")
                    if endpoint and endpoint != proxy_url:
                        raise StockExecutionError(f"Stock project model {pid}/{mid} has an unproxied endpoint")
            unbound = set(self.api("GET", "/provider")["connected"]) - set(providers)
            if unbound:
                raise StockExecutionError(f"Stock providers are not routed through the BB proxy: {sorted(unbound)}")
            # Stock `run` applies these rules when no interactive host is attached.
            # src/cli/cmd/run.ts:352-368. Interactive clients use the server API.
            permission = [] if interaction else [
                {"permission": name, "action": "deny", "pattern": "*"}
                for name in ("question", "plan_enter", "plan_exit")
            ]
            if self.binding is not None or session_imports is not None:
                selected_id = self.binding["session_id"] if self.binding is not None else imported_session_id
                selected = self.api("GET", "/session/" + selected_id)
                if selected.get("parentID") or selected.get("directory") != self.workspace:
                    raise StockExecutionError("Native resumed root does not belong to this BB workspace")
                self.session_id = selected["id"]
            else:
                self.session_id = self.api("POST", "/session", {"permission": permission})["id"]
            self.persist_binding()
            threading.Thread(target=self._events, daemon=True).start()
            if not self.events_ready.wait(timeout):
                raise StockExecutionError("Stock server event subscription timed out")
        except Exception:
            self.close()
            raise
        atexit.register(self.close)

    def persist_binding(self):
        if self.binding_path is None or self.session_id is None:
            return
        value = {"workspace": self.workspace, "model": self.model, "session_id": self.session_id,
                 "compaction_records": sorted(self.compaction_record_ids), "pruned_calls": sorted(self.pruned_call_ids)}
        temporary = self.binding_path.with_suffix(".tmp")
        with temporary.open("w") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.binding_path)

    def _stdout(self):
        for line in self.process.stdout:
            match = re.search(r"opencode server listening on (http://127\.0\.0\.1:\d+)", line)
            if match:
                self.url = match[1]
                self.ready.set()

    def _stderr(self):
        for line in self.process.stderr:
            self.stderr.append(line)

    def _watch(self):
        code = self.process.wait()
        if not self.closed:
            self.fail(f"Stock OpenCode exited with code {code}: {''.join(self.stderr)[-4000:]}")
        self.ready.set()

    def _events(self):
        import base64
        try:
            request = urllib.request.Request(
                self.url + "/event",
                headers={"x-opencode-directory": self.workspace,
                         "Authorization": "Basic " + base64.b64encode(("opencode:" + self.password).encode()).decode()},
            )
            with urllib.request.urlopen(request) as connection:
                self.event_connection = connection
                self.events_ready.set()
                for line in connection:
                    if self.closed:
                        break
                    if not line.startswith(b"data: "):
                        continue
                    event = json.loads(line[6:])
                    kind = event.get("type")
                    properties = event.get("properties", {})
                    info = properties.get("info", {})
                    if kind == "session.status" and properties.get("sessionID") == self.session_id:
                        with self.condition:
                            if properties["status"]["type"] == "idle":
                                self.native_prompt_done.set()
                                if self.headless and self.final_response_pending:
                                    self.headless_idle_seen = True
                            else:
                                self.native_prompt_done.clear()
                            self.condition.notify_all()
                        continue
                    if self.headless and kind == "message.updated" and info.get("sessionID") == self.session_id and info.get("role") == "user":
                        with self.condition:
                            self.observed_user_ids.add(info["id"])
                            self.condition.notify_all()
                    if kind == "message.updated" and info.get("error", {}).get("name") == "MessageAbortedError":
                        self.auxiliary_transport.cancel(info["sessionID"])
                        continue
                    if kind not in {"permission.asked", "question.asked"}:
                        continue
                    interaction_state = getattr(self, "current_state", None)
                    thread = threading.Thread(target=self._interaction, args=(kind, properties, interaction_state), daemon=True)
                    with self.condition:
                        if self.closing:
                            continue
                        self.interaction_threads[thread] = {"properties": properties, "state": interaction_state}
                        try:
                            thread.start()
                        except Exception:
                            self.interaction_threads.pop(thread, None)
                            raise
        except Exception as exc:
            if not self.closed and not self.closing:
                self.fail(f"Stock event bridge failed: {exc}")
            self.events_ready.set()

    def _interaction(self, kind: str, properties: dict, state):
        self.interaction_context.state = state
        try:
            if self.headless and properties["sessionID"] != self.session_id:
                # run.ts:539-541 handles root requests only. Background child
                # prompts remain stock-owned until the first root idle/disposal.
                return
            if self.interaction:
                self.interaction(self, kind, properties)
            elif kind == "permission.asked":
                # src/cli/cmd/run.ts:539-550, noninteractive auto-rejection.
                self.api("POST", "/permission/" + properties["id"] + "/reply", {"reply": "reject"})
            else:
                raise StockExecutionError("Stock asked a question in a noninteractive session despite stock's deny rule")
        except Exception as exc:
            if not self.closed and not self.closing:
                self.fail(f"Stock interaction failed: {exc}")
        finally:
            with self.condition:
                self.interaction_threads.pop(threading.current_thread(), None)
                self.condition.notify_all()

    def forward_auxiliary_http(self, url: str, headers: dict, body: bytes):
        session_id = getattr(self.auxiliary_request, "session_id", None)
        if session_id is None:
            raise StockExecutionError("Auxiliary HTTP forwarding requires an authenticated stock session")
        return self.auxiliary_transport.forward(session_id, url, headers, body)

    def fail(self, message: str):
        with self.condition:
            self.failure = self.failure or message
            self.condition.notify_all()

    def check_alive(self):
        if self.failure or self.closed:
            raise StockExecutionError(self.failure or "Stock OpenCode session is closed")

    def record(self, kind: str, body: dict, headers: dict):
        record = {"kind": kind, "request": body, "headers": upstream_headers(headers)}
        self.records.append(record)
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as output:
                output.write(json.dumps(record, ensure_ascii=False) + "\n")

    def classify(self, headers: dict) -> str:
        sid = headers.get("x-bb-stock-session")
        agent = headers.get("x-bb-stock-agent")
        if not sid or not agent or not headers.get("x-bb-stock-provider"):
            raise StockExecutionError("Unlabelled stock provider request")
        if sid == self.session_id:
            if agent in {"title", "summary", "compaction"}:
                return agent
            if agent != self.root_agent:
                # Root primary agents can change between user turns; the stock
                # registry, not the first observed display name, is authoritative.
                known = next((item for item in self.api("GET", "/agent") if item["name"] == agent), None)
                if known is None or known.get("mode") not in {"primary", "all"}:
                    raise StockExecutionError(f"Unknown root agent {agent}")
                self.root_agent = agent
            return "main"
        ancestor = self.api("GET", "/session/" + sid)
        seen = set()
        while ancestor.get("parentID") and ancestor["id"] not in seen:
            seen.add(ancestor["id"])
            if ancestor["parentID"] == self.session_id:
                return "child"
            ancestor = self.api("GET", "/session/" + ancestor["parentID"])
        raise StockExecutionError(f"Provider request from unrelated stock session {sid}")

    def api(self, method: str, path: str, body: dict | None = None, *, long_lived: bool = False):
        import base64
        self.check_alive()
        headers = {"Content-Type": "application/json", "x-opencode-directory": self.workspace,
                   "Authorization": "Basic " + base64.b64encode(("opencode:" + self.password).encode()).decode()}
        request = urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None,
                                         headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=None if long_lived else self.timeout) as response:
                payload = response.read()
                return json.loads(payload) if payload else None
        except urllib.error.HTTPError as exc:
            raise StockExecutionError(f"Stock API {method} {path}: {exc.code} {exc.read().decode()}") from exc

    def _start_request(self, path: str, body: dict):
        self.check_alive()
        self.request_done.clear()
        self.last_request_result = None
        self.native_prompt_done.clear()
        def run():
            try:
                self.last_request_result = self.api("POST", path, body, long_lived=True)
            except Exception as exc:
                if not self.closed:
                    self.fail(str(exc))
            finally:
                self.request_done.set()
                with self.condition:
                    self.condition.notify_all()
        threading.Thread(target=run, daemon=True).start()

    def _root_finished(self) -> bool:
        return self.request_done.is_set() and self.native_prompt_done.is_set()

    def _wait_root_finished(self, message: str):
        with self.condition:
            ready = self.condition.wait_for(lambda: self._root_finished() or self.failure or self.closed, self.timeout)
            self.check_alive()
            if not ready:
                raise StockExecutionError(message)

    def wait_pending(self, *, allow_finished_request: bool = True) -> dict:
        with self.condition:
            ready = self.condition.wait_for(lambda: self.pending is not None or self.compaction_pending is not None or
                                            self.failure or self.closed or
                                            (allow_finished_request and self._root_finished()), self.timeout)
            self.check_alive()
            if not ready or (self.pending is None and self.compaction_pending is None):
                native_result = getattr(self, "last_request_result", None)
                native_error = native_result.get("info", {}).get("error") if isinstance(native_result, dict) else None
                if native_error is None:
                    messages = self.api("GET", "/session/" + self.session_id + "/message?limit=1")
                    native_error = next((m["info"]["error"] for m in messages if m["info"].get("error")), None)
                raise StockExecutionError(
                    f"Stock did not produce a root provider continuation: native_error={native_error!r}; "
                    f"{''.join(self.stderr)[-4000:]}"
                )
            if self.compaction_pending is not None:
                return self.compaction_pending["body"]
            if self.requires_plugin:
                names = {tool.get("name", tool.get("function", {}).get("name"))
                         for tool in self.pending["body"].get("tools", ())}
                if "look_at" not in names:
                    self.fail("Pinned oh-my-opencode plugin did not register its stock tools")
                    self.check_alive()
            return self.pending["body"]

    def user(self, parts: list[dict], *, agent: str | None = None):
        self._wait_root_finished("Previous stock turn has not completed")
        self.run_active = True
        self.final_response_pending = False
        self.user_file_parts = [copy.deepcopy(part) for part in parts if part["type"] == "file"]
        self.compaction_continuation_user_id = None
        self._start_request("/session/" + self.session_id + "/message",
                            {"parts": parts, "model": dict(zip(("providerID", "modelID"), self.model.split("/", 1))),
                             **({"agent": agent} if agent else {})})
        request = self.wait_pending()
        self.active_user_id = self._latest_user_message()["info"]["id"]
        return request

    def announce_stock_input(self, pending: dict) -> None:
        """Identify a stock-created user owning a new idle/final-boundary prompt."""
        if (
            self.run_active and not self.final_response_pending
            and pending.get("recovery_from_message_id") is None
        ) or self.compaction_summary is not None:
            return
        message = self._latest_user_message()
        message_id = message["info"]["id"]
        if message_id in {self.active_user_id, getattr(self, "compaction_continuation_user_id", None)}:
            return
        if self.headless:
            # Flush the same authenticated event stream used by `opencode run`
            # before deciding whether this notification followed its first idle.
            ready = self.condition.wait_for(
                lambda: message_id in self.observed_user_ids or self.failure or self.closed,
                self.timeout,
            )
            self.check_alive()
            if not ready:
                raise StockExecutionError("Stock notification user event was not observed")
            if self.headless_idle_seen:
                pending["cancelled"] = True
                self.condition.notify_all()
                return
        if self.stock_input_handler is None:
            raise StockExecutionError(
                "Stock-initiated parent turns require a live SessionRunner input queue; "
                "use SessionService for persistent native execution"
            )
        source = {
            "kind": "stock", "source": "opencode", "session_id": self.session_id,
            "message_id": message_id, "request_id": uuid.uuid4().hex,
            "agent": message["info"]["agent"], "model": copy.deepcopy(message["info"]["model"]),
            "content": "\n".join(part["text"] for part in message["parts"]
                                 if part["type"] == "text" and not part.get("ignored")),
        }
        if pending.get("recovery_from_message_id") is not None:
            source["recovery_from_message_id"] = pending["recovery_from_message_id"]
        pending["stock_origin"] = source
        self.stock_input_handler(copy.deepcopy(source))

    def claim_stock_input(self, source: dict) -> dict:
        """Claim the genuine source request without posting another user message."""
        with self.condition:
            pending = self.pending or self.compaction_pending
            if pending is None or pending.get("stock_origin") != source:
                raise StockExecutionError("Stock-origin input does not own the queued native request")
            if pending.get("stock_claimed"):
                raise StockExecutionError("Stock-origin input has already been claimed")
            pending["stock_claimed"] = True
            self.active_user_id = source["message_id"]
            self.user_file_parts = []
            self.compaction_continuation_user_id = None
            self.run_active = True
            self.final_response_pending = False
            return pending["body"]

    def submit(self, response: dict):
        self.wait_pending()
        calls = [item for item in response["output"] if item["type"] == "function_call"]
        with self.condition:
            pending = self.pending
            if pending is None:
                raise StockExecutionError("Cannot submit a main response while stock is awaiting BB compaction")
            self.pending = None
            self.final_response_pending = not calls
            pending["response"] = response
            self.condition.notify_all()
        if calls:
            self.wait_pending()
            missing = [item["call_id"] for item in calls if item["call_id"] not in self.results]
            if missing:
                raise StockExecutionError(f"Stock omitted tool results: {missing}")
        else:
            with self.condition:
                ready = self.condition.wait_for(lambda: self._root_finished() or self.headless_idle_seen or self.compaction_pending is not None or
                                                (self.pending is not None and self.pending.get("stock_origin") is not None) or
                                                self.failure or self.closed, self.timeout)
                if not ready:
                    raise StockExecutionError("Stock did not finish the mirrored parent response")
        self.check_alive()

    def submit_overflow(self, status: int, headers: dict, payload: bytes):
        """Let stock consume the original provider failure and create replay markers."""
        if not isinstance(status, int) or not 100 <= status <= 599:
            raise StockExecutionError(f"Invalid native provider HTTP status {status}")
        self.wait_pending()
        with self.condition:
            pending = self.pending
            if pending is None:
                raise StockExecutionError("No native main request is awaiting the provider failure")
            self.pending = None
            self._overflow_user_id = self.active_user_id
            self.recovery_outcome = None
            pending["provider_error"] = (status, headers, payload)
            pending["response"] = {}
            self.condition.notify_all()
        # A plugin may start recovery after the failed /message request returns.
        # Its summarize route owns that transition; never enqueue a competing one.
        with self.condition:
            ready = self.condition.wait_for(lambda: self.compaction_pending is not None or
                                            self.recovery_outcome == "handled" or
                                            self.failure or self.closed, self.timeout)
            self.check_alive()
            if not ready:
                # No source continuation: keep the original provider error as
                # the old turn's outcome instead of starting a competing summary.
                self.recovery_outcome = "error"
                self._overflow_user_id = None
            return self.recovery_outcome

    def text_response(self, text: str) -> dict:
        from types import SimpleNamespace
        return response_from_result(SimpleNamespace(raw_response=None, usage={},
                                    messages=[SimpleNamespace(content=text, tool_calls=[])]), self.model.split("/", 1)[1])

    def result(self, call_id: str) -> dict:
        self.check_alive()
        item = self.results.get(call_id)
        if item is None:
            raise StockExecutionError(f"No stock tool result for BB call {call_id}")
        output = item["output"]
        result = {}
        if isinstance(output, str):
            result["__mvi_text_output"] = output
        elif isinstance(output, list):
            result["__mvi_content_output"] = copy.deepcopy(output)
            result["__mvi_native_responses_output"] = True
            result["__mvi_text_output"] = "\n".join(p.get("text", "") for p in output if p.get("type") in {"input_text", "output_text"})
        else:
            raise StockExecutionError(f"Unexpected stock output type {type(output).__name__}")
        return result

    def compaction_prompt(self, *, auto: bool = True) -> dict:
        """Expose stock's summary request before BB generates/commits its summary."""
        with self.condition:
            if self.compaction_pending is not None:
                return self.compaction_pending["body"]
            if self.source_recovery_pending is not None and self.source_recovery_pending():
                ready = self.condition.wait_for(lambda: self.compaction_pending is not None or
                                                self.failure or self.closed, self.timeout)
                self.check_alive()
                if not ready:
                    raise StockExecutionError("Stock plugin did not produce its deferred compaction prompt")
                return self.compaction_pending["body"]
            pending = self.pending
            self.pending = None
            if pending:
                pending["cancelled"] = True
                self.condition.notify_all()
        if pending:
            self.api("POST", "/session/" + self.session_id + "/abort", {})
            # Native compaction precedes creating a continuation assistant.
            messages = self.api("GET", "/session/" + self.session_id + "/message")
            draft = messages[-1]
            if draft["info"].get("role") == "assistant" and not any(
                part.get("type") in {"text", "tool", "reasoning"} for part in draft["parts"]
            ):
                self.api("DELETE", f"/session/{self.session_id}/message/{draft['info']['id']}")
        self._wait_root_finished("Stock turn did not stop before compaction")
        self._start_request("/session/" + self.session_id + "/summarize",
                            {"providerID": self.model.split("/", 1)[0], "modelID": self.model.split("/", 1)[1], "auto": auto})
        with self.condition:
            ready = self.condition.wait_for(lambda: self.compaction_pending is not None or self.failure or
                                            self.closed or self._root_finished(), self.timeout)
            self.check_alive()
            if not ready or self.compaction_pending is None:
                raise StockExecutionError("Stock did not produce a compaction prompt")
            return self.compaction_pending["body"]

    def _latest_user_message(self) -> dict:
        messages = self.api("GET", "/session/" + self.session_id + "/message")
        for message in reversed(messages):
            if message["info"]["role"] == "user" and not any(
                part["type"] == "compaction" for part in message["parts"]
            ):
                return message
        raise StockExecutionError("Stock has no current user message to project")

    def _latest_user_parts(self) -> list[dict]:
        return self._latest_user_message()["parts"]

    def is_compaction_continuation(self) -> bool:
        continuation = getattr(self, "compaction_continuation_user_id", None)
        return continuation is not None and self._latest_user_message()["info"]["id"] == continuation

    def latest_user_text_parts(self) -> list[dict]:
        """Return only chat.message-transformed user text, never source history/media."""
        return [{"type": "text", "text": part["text"]} for part in self._latest_user_parts()
                if part["type"] == "text" and not part.get("ignored")]

    def latest_user_sequence(self, request: dict | None = None) -> list[dict]:
        """Expose canonical text and authorized media ordinals, never media bytes."""
        files = getattr(self, "user_file_parts", [])
        remaining = list(enumerate(files))
        sequence = []
        parts = self._latest_user_parts() if request is None else []
        if request is not None:
            current = next((item for item in reversed(request["input"]) if item.get("role") == "user"), None)
            if current is None:
                raise StockExecutionError("Stock in-flight input omitted its current user")
            content = current["content"]
            wire_remaining = list(files)
            for block in ([{"type": "input_text", "text": content}] if isinstance(content, str) else content):
                if block.get("type") == "input_text" and set(block) == {"type", "text"} and isinstance(block["text"], str):
                    parts.append({"type": "text", "text": block["text"]})
                elif block.get("type") in {"input_image", "input_file"}:
                    url = block.get("image_url") if block["type"] == "input_image" else block.get("file_data")
                    original = next((part for part in wire_remaining if part["url"] == url), None)
                    if original is None:
                        raise StockExecutionError("Stock in-flight transform introduced unauthorized media")
                    wire_remaining.remove(original)
                    parts.append({"type": "file", "url": url, "mime": original["mime"]})
                else:
                    raise StockExecutionError("Stock in-flight current-user projection contains an unknown block")
        for part in parts:
            if part["type"] == "text":
                if not part.get("ignored"):
                    sequence.append({"type": "text", "text": part["text"]})
            elif part["type"] == "file":
                match = next(((index, original) for index, original in remaining
                              if original["url"] == part["url"] and original["mime"] == part["mime"]), None)
                if match is None:
                    raise StockExecutionError("Stock user hook introduced media absent from BB's authorized input")
                remaining.remove(match)
                sequence.append({"type": "stock_media", "index": match[0]})
            else:
                raise StockExecutionError(f"Unsupported stock user part projection: {part['type']}")
        if remaining and not self.is_compaction_continuation():
            raise StockExecutionError("Stock user hook removed BB's authorized media")
        return sequence

    def set_compaction_response(self, result) -> str:
        """Retain the genuine BB summary response, including reasoning and usage."""
        if self.compaction_pending is None:
            raise StockExecutionError("No queued native compaction can receive the genuine summary response")
        model = str(self.compaction_pending["body"]["model"])
        self.compaction_response = response_from_result(result, model)
        self.compaction_pending["response"] = self.compaction_response
        return "".join(part.get("text", part.get("refusal", ""))
                       for item in self.compaction_response["output"] if item["type"] == "message"
                       for part in item.get("content", ()))

    def compact(self, summary: str, *, auto: bool = True):
        """Complete the queued native transition with BB's committed actual summary."""
        previous_user_id = self._latest_user_message()["info"]["id"]
        self.compaction_prompt(auto=auto)
        with self.condition:
            self.compaction_summary = summary
            pending = self.compaction_pending
            if pending is None:
                raise StockExecutionError("Native compaction lost its pending summary operation")
            pending["summary"] = summary
            self.condition.notify_all()
        try:
            with self.condition:
                if not self.condition.wait_for(lambda: self.compaction_pending is not pending or self.failure or
                                               self.closed, self.timeout):
                    raise StockExecutionError("Stock did not consume the committed summary")
                self.check_alive()
            if auto:
                # Plugin summarize may outlive the original failed /message.
                self.wait_pending(allow_finished_request=False)
                current_user_id = self._latest_user_message()["info"]["id"]
                if current_user_id != previous_user_id:
                    self.compaction_continuation_user_id = current_user_id
            else:
                self._wait_root_finished("Stock compaction timed out")
            self.check_alive()
        finally:
            self.compaction_summary = None
            self.compaction_response = None

    def prune(self, call_ids: list[str]):
        """Apply BB's exact prune selection using stock's public part-update API."""
        missing = set(call_ids) - self.pruned_call_ids
        if not missing:
            return
        messages = self.api("GET", "/session/" + self.session_id + "/message")
        for message in messages:
            for part in message["parts"]:
                if part.get("type") == "tool" and part.get("callID") in missing:
                    if part["state"]["status"] != "completed":
                        raise StockExecutionError("Cannot prune an incomplete stock tool")
                    part["state"]["time"]["compacted"] = int(time.time() * 1000)
                    self.api("PATCH", f"/session/{self.session_id}/message/{part['messageID']}/part/{part['id']}", part)
                    self.pruned_call_ids.add(part["callID"])
                    missing.remove(part["callID"])
        if missing:
            raise StockExecutionError(f"BB pruned calls missing from stock history: {sorted(missing)}")

    def wait_for_idle_source_input(self) -> None:
        """Fence admission behind a stock user already queued, even before SSE busy."""
        with self.condition:
            if self.run_active or self.stock_input_handler is None or self.active_user_id is None or self.closed:
                return
            if any(pending is not None and pending.get("stock_origin") is not None
                   for pending in (self.pending, self.compaction_pending)):
                return
        message = self._latest_user_message()
        if message["info"]["id"] not in {
            self.active_user_id, getattr(self, "compaction_continuation_user_id", None),
        } and any(part["type"] == "text" and not part.get("synthetic") for part in message["parts"]):
            # prompt.ts creates this user before its busy status/provider frame.
            # The previous request's idle cannot terminate this successor wait.
            self.wait_pending(allow_finished_request=False)

    def finish_run(self):
        """Interrupt a still-active parent using stock's own user-stop transition."""
        with self.condition:
            self.run_active = False
            finish_headless = self.headless and self.headless_idle_seen
            # OMO manager.ts:1359 enqueues a real parent prompt, not another BB
            # model continuation. The session runner owns its next normal turn.
            if not finish_headless:
                if any(pending is not None and pending.get("stock_origin") is not None and not pending.get("stock_claimed")
                       for pending in (self.pending, self.compaction_pending)):
                    return
                if self._root_finished() and self.pending is None and self.compaction_pending is None:
                    return
            preserve_source = self.stock_input_handler is not None and self.final_response_pending
        if finish_headless:
            # run.ts:531-537 exits at root idle; bootstrap then disposes the
            # instance. Do not keep background children alive after CLI exit.
            self.close()
            return
        if preserve_source:
            # prompt.ts creates the user before starting the session loop. A
            # genuine new user can therefore be busy before its model frame
            # reaches the proxy; do not abort that source-owned successor.
            message = self._latest_user_message()
            if message["info"]["id"] not in {
                self.active_user_id, getattr(self, "compaction_continuation_user_id", None),
            } and any(part["type"] == "text" and not part.get("synthetic") for part in message["parts"]):
                self.wait_pending()
                return
        try:
            # routes/session.ts:354-381 calls prompt.ts:256-267 cancel.
            # Never fabricate a model completion to terminate a budgeted BB run.
            if self.on_abort is not None:
                self.on_abort(self)
            self.api("POST", "/session/" + self.session_id + "/abort", {})
            with self.condition:
                for pending in (self.pending, self.compaction_pending):
                    if pending is not None:
                        pending["cancelled"] = True
                self.pending = None
                self.compaction_pending = None
                self.condition.notify_all()
            self._wait_root_finished("Stock parent turn did not finish after its run-boundary abort")
            self.check_alive()
        except Exception:
            self.close()
            raise

    def close(self):
        # Headless finish_run and operator stop may own teardown concurrently.
        # The second caller waits until all native/proxy/scratch cleanup ends.
        with self._close_mutex:
            self._close_locked()

    def _close_locked(self):
        if self.closed:
            return
        self.closing = True
        cleanup_error = None
        if getattr(self, "on_close", None) is not None:
            try:
                self.on_close(self)
            except Exception as exc:
                cleanup_error = exc
        cancelled_children = set()
        transport = getattr(self, "auxiliary_transport", None)
        if transport is not None:
            try:
                cancelled_children = transport.close()
            except Exception as exc:
                cleanup_error = exc
        if self.url and self.session_id and not self.failure:
            with self.condition:
                if self.pending:
                    self.pending["cancelled"] = True
                    self.pending = None
                    self.condition.notify_all()
            try:
                for child in cancelled_children - {self.session_id}:
                    self.api("POST", "/session/" + child + "/abort", {})
                self.api("POST", "/session/" + self.session_id + "/abort", {})
                # src/server/server.ts:257-275 releases stock LSP/PTY resources.
                self.api("POST", "/instance/dispose", {})
            except Exception as exc:
                cleanup_error = exc
        self.closed = True
        with self.condition:
            self.condition.notify_all()
        process = getattr(self, "process", None)
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                process.wait()
        proxy = getattr(self, "proxy", None)
        if proxy is not None:
            proxy.shutdown()
            proxy.server_close()
        with self.condition:
            interactions = list(self.interaction_threads)
        deadline = time.monotonic() + 5
        for thread in interactions:
            thread.join(max(0, deadline - time.monotonic()))
            if thread.is_alive():
                cleanup_error = StockExecutionError("Stock interaction bridge did not stop")
        self.scratch.cleanup()
        atexit.unregister(self.close)
        if cleanup_error is not None:
            raise StockExecutionError(f"Stock resource disposal failed: {cleanup_error}") from cleanup_error


def enabled(conductor) -> bool:
    return (conductor.config.get("tools") or {}).get("native_semantics") in SEMANTICS


def get_session(conductor, state) -> OpenCodeToolSession:
    sessions = getattr(conductor, "_opencode_tool_sessions", None)
    if sessions is None:
        sessions = conductor._opencode_tool_sessions = {}
    key = str(state.get_provider_metadata("session_id") or state.session_id)
    if key not in sessions:
        driver = conductor.config.get("workspace", {}).get("sandbox", {}).get("driver", "process")
        if driver != "process":
            raise StockExecutionError("Stock OpenCode requires the same local process workspace as BB; no native executor fallback")
        tools = conductor.config["tools"]
        model = conductor.config["providers"]["default_model"]

        def auxiliary(body, headers, path, provider, *, raw_body):
            from breadboard_engine.provider_broker import get_provider_broker
            from contextlib import nullcontext
            from breadboard_engine.provider.routing import provider_router
            route = provider + "/" + str(body.get("model", ""))
            declared = {entry["id"] for entry in conductor.config["providers"]["models"]}
            if route not in declared:
                raise StockExecutionError(f"Unroutable stock auxiliary model {route}")
            routing, _, _ = provider_router.get_declared_provider_config(route, declared)
            model_params = next(entry.get("params", {}) for entry in conductor.config["providers"]["models"]
                                if entry["id"] == route)
            material_context = (nullcontext({}) if routing.auth_owner == "none" else
                                get_provider_broker().execution_material(provider, session_id=key))
            with material_context as material:
                credential = material.get("api_key")
                if not credential and routing.credential_required:
                    raise StockExecutionError(f"Stock auxiliary requires the session's {provider} credential")
                base_url = model_params.get("base_url") or material.get("base_url") or routing.base_url
                if not base_url:
                    raise StockExecutionError(f"No upstream endpoint for stock auxiliary provider {provider}")
                outgoing = upstream_headers(headers)
                outgoing.update(routing.default_headers)
                if credential:
                    outgoing["authorization"] = "Bearer " + credential
                if provider == "anthropic":
                    outgoing.pop("authorization")
                    outgoing["x-api-key"] = credential
                suffix = path.removeprefix("/v1") if str(base_url).rstrip("/").endswith("/v1") else path
                return sessions[key].forward_auxiliary_http(
                    str(base_url).rstrip("/") + suffix, outgoing, raw_body
                )

        plugin = None
        if tools["native_semantics"] == "oh_my_opencode_3_10_0":
            plugin_path = tools.get("stock_plugin_root") or os.environ.get("OPENCODE_PLUGIN_ROOT")
            if not plugin_path:
                raise StockExecutionError("OPENCODE_PLUGIN_ROOT must identify the pinned oh-my-opencode build")
            plugin = Path(plugin_path).expanduser().resolve()
        log_dir = Path(getattr(getattr(conductor, "logger_v2", None), "run_dir", None) or conductor.workspace)
        model_config = next(m for m in conductor.config["providers"]["models"] if m["id"] == model)
        config = copy.deepcopy(tools.get("stock_config", {}))
        for entry in conductor.config["providers"]["models"]:
            pid = entry["id"].split("/", 1)[0]
            # Let stock's bundled catalog own limits/capabilities; preserve only
            # explicit stock_config overrides, including bounded lane fixtures.
            config.setdefault("provider", {}).setdefault(pid, {})
        config.setdefault("agent", {}).setdefault("build", {}).update(model_config.get("params", {}))
        lsp_pin = tools.get("stock_lsp_root")
        if lsp_pin is None:
            raise StockExecutionError("Dated native profiles require the pinned stock_lsp_root declaration")

        def interaction(session, kind, properties):
            interaction_state = session.interaction_context.state
            bridge_stock_interaction(conductor, interaction_state, session, kind, properties)
        from breadboard.product.runtime.session_store import session_directory
        from breadboard.product.runtime.artifacts import is_portable_basename
        if not is_portable_basename(key):
            raise StockExecutionError("BB session ID is not a portable session-store key")
        headless = getattr(conductor, "_opencode_stock_headless", False)
        sessions[key] = OpenCodeToolSession(str(conductor.workspace), model,
                                            binary=Path(tools.get("stock_binary", DEFAULT_BINARY)),
                                            plugin_root=plugin, timeout=float(tools.get("stock_timeout", 180)),
                                            auxiliary=auxiliary, stock_config=config,
                                            environment=tools.get("stock_environment"),
                                            interaction=interaction if not headless and state.get_provider_metadata("permission_queue") is not None else None,
                                            protected_paths=tuple(getattr(conductor, "_protected_credential_paths", ())),
                                            read_only_roots=tools.get("stock_read_only_roots"),
                                            data_root=session_directory(Path(conductor.workspace)) / key / "stock-opencode",
                                            lsp_root=lsp_pin,
                                            stock_worktree_read_only=tools.get("stock_worktree_read_only", False),
                                            session_imports=tools.get("stock_session_imports"),
                                            imported_session_id=key if tools.get("stock_session_imports") is not None else None,
                                            headless=headless,
                                            pty=tools.get("pty") is True,
                                            log_path=log_dir / ("opencode-tool-exchanges-" + uuid.uuid4().hex + ".jsonl"))
        sessions[key].current_state = state
        def cancel_interactions(session, *, root_only):
            with session.condition:
                frames = list(session.interaction_threads.values())
            if root_only:
                for frame in frames:
                    if frame["properties"]["sessionID"] == session.session_id:
                        conductor.permission_broker.cancel_stock_requests(frame["state"], [frame["properties"]["id"]])
            else:
                states = {id(session.current_state): session.current_state}
                states.update((id(frame["state"]), frame["state"]) for frame in frames if frame["state"] is not None)
                for active_state in states.values():
                    conductor.permission_broker.cancel_pending_requests(active_state)
        sessions[key].on_abort = lambda session: cancel_interactions(session, root_only=True)
        sessions[key].on_close = lambda session: cancel_interactions(session, root_only=False)
        def source_recovery_pending():
            recipe = getattr(getattr(conductor, "compaction_controller", None), "recipe", None)
            return any(getattr(trigger, "kind", None) == "pending_error" and trigger.pending is not None
                       for trigger in getattr(recipe, "triggers", ()))
        sessions[key].source_recovery_pending = source_recovery_pending
        from .stock_tools import register_stock_session
        register_stock_session(conductor, sessions[key])
    state._opencode_tool_session = sessions[key]
    state._opencode_native_semantics = conductor.config["tools"]["native_semantics"]
    sessions[key].current_state = state
    handler = getattr(conductor, "_opencode_stock_input_handler", None)
    if handler is not None:
        sessions[key].stock_input_handler = handler
    sessions[key].headless = getattr(conductor, "_opencode_stock_headless", False)
    return sessions[key]


def bridge_stock_interaction(conductor, state, session, kind: str, properties: dict):
    broker = conductor.permission_broker
    if kind == "question.asked":
        answers = broker.request_stock_question(state, properties)
        if answers is None:
            session.api("POST", "/question/" + properties["id"] + "/reject", {})
        else:
            session.api("POST", "/question/" + properties["id"] + "/reply", {"answers": answers})
        return
    if kind != "permission.asked":
        raise StockExecutionError(f"Unknown stock interaction {kind}")
    from ..permissions.broker import PermissionRequest
    requests = [
        PermissionRequest(properties["permission"], pattern,
                          {"stock_kind": "opencode_permission", "stock_request": properties})
        for pattern in properties["patterns"]
    ]
    decisions = [decision for _, decision in broker._request_permissions(state, requests, concurrent=True)]
    reply = "reject" if "reject" in decisions else "always" if decisions and all(d == "always" for d in decisions) else "once"
    session.api("POST", "/permission/" + properties["id"] + "/reply", {"reply": reply})


def stock_system_messages(request: dict) -> list[dict]:
    messages = []
    for item in request["input"]:
        if item.get("role") not in {"developer", "system"}:
            continue
        content = item.get("content")
        if not isinstance(content, str):
            raise StockExecutionError("Stock developer prompt must be a string")
        messages.append({"role": "system", "content": content})
    if not messages:
        raise StockExecutionError("Stock omitted the generated developer prompt")
    return messages


def stock_tool_schema(request: dict) -> list[dict]:
    tools = request.get("tools", [])
    if not all(t.get("type") == "function" for t in tools):
        raise StockExecutionError("Pinned stock catalog contains a non-function tool")
    return copy.deepcopy(tools)


def synchronize_record(state, record):
    session = getattr(state, "_opencode_tool_session", None)
    if session is None or record.record_id in session.compaction_record_ids:
        return
    if record.is_boundary:
        session.compact(record.summary or "", auto=record.reason != "manual")
    else:
        session.prune([edit.message["tool_call_id"] for edit in record.edits
                       if edit.message.get("pruned_at") and edit.message.get("tool_call_id")])
    session.compaction_record_ids.add(record.record_id)
    session.persist_binding()


def stock_user_parts(content: Any, *, context=None) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    parts = []
    for block in content:
        kind = block.get("type")
        if kind in {"text", "input_text"}:
            parts.append({"type": "text", "text": block["text"]})
            continue
        if kind == "file" and block.get("url") and block.get("mime"):
            parts.append(copy.deepcopy(block))
            continue
        source = block.get("source") or {}
        if kind == "media":
            from ..provider.input_media import resolve_input_media
            media = resolve_input_media(block, context)
            mime, url = media.mime, media.data_url
        elif kind in {"image", "document"} and source.get("type") == "base64":
            mime = source["media_type"]
            url = f"data:{mime};base64,{source['data']}"
        elif kind in {"image_url", "input_image"}:
            image = block.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            mime = block.get("mime_type") or block.get("media_type")
            if url and url.startswith("data:"):
                mime = url[5:].split(";", 1)[0]
        elif kind == "input_file" and block.get("file_data"):
            url = block["file_data"]
            mime = url[5:].split(";", 1)[0] if url.startswith("data:") else block.get("mime_type")
        else:
            raise StockExecutionError(f"Unsupported stock input media {kind}")
        if not url or not mime:
            raise StockExecutionError("Stock file input requires a URL and MIME type")
        parts.append({"type": "file", "url": url, "mime": mime,
                      "filename": block.get("filename", "attachment")})
    return parts


def prepare_stock_initial_user(conductor, state):
    if not enabled(conductor):
        return None
    session = get_session(conductor, state)
    stock_origin = state.get_provider_metadata("opencode_stock_origin")
    if stock_origin is not None:
        request = session.claim_stock_input(stock_origin)
        state.set_provider_metadata("opencode_stock_origin", None)
        state.set_provider_metadata("opencode_stock_raw_user", None)
        return request
    raw_user = state.get_provider_metadata("opencode_stock_raw_user")
    if raw_user is not None:
        from ..provider.contracts import ProviderRuntimeContext
        context = ProviderRuntimeContext(session_state=state, agent_config=conductor.config)
        request = session.user(stock_user_parts(raw_user["content"], context=context))
        state.set_provider_metadata("opencode_stock_raw_user", None)
        return request
    return session.wait_pending()


def prepare_stock_turn(conductor, state):
    if not enabled(conductor):
        return None
    session = get_session(conductor, state)
    for record in state.compaction_state.records:
        synchronize_record(state, record)
    if session.compaction_pending is not None:
        raise StockExecutionError("Native root compaction requires a committed BB boundary before continuation")
    return prepare_stock_initial_user(conductor, state)


def submit_stock_response(conductor, state, result):
    if enabled(conductor):
        session = get_session(conductor, state)
        session.submit(response_from_result(result, session.model.split("/", 1)[1]))


def execute_opencode_tool(conductor, name: str, arguments: dict, *, tool_call: dict) -> dict:
    state = conductor._active_session_state
    if state is None:
        raise StockExecutionError("OpenCode execution requires an active BB session")
    session = get_session(conductor, state)
    call_id = tool_call.get("id") or tool_call.get("call_id")
    if not call_id:
        raise StockExecutionError("OpenCode execution requires the original provider call ID")
    return session.result(call_id)




def submit_stock_overflow(conductor, state, error: BaseException):
    from ..compaction.overflow import is_context_overflow
    if not enabled(conductor):
        return
    controller = getattr(conductor, "compaction_controller", None)
    recipe = getattr(controller, "recipe", None)
    classifier = getattr(recipe, "error_classifier", None) or is_context_overflow
    plugin_recovery = any(
        callable(getattr(trigger, "error_parser", None)) and trigger.error_parser(error) is not None
        for trigger in getattr(recipe, "triggers", ())
    )
    if not classifier(error) and not plugin_recovery:
        return
    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        response = getattr(current, "response", None)
        if response is not None:
            status = getattr(response, "status_code", None)
            payload = getattr(response, "content", None)
            if isinstance(status, int) and 100 <= status <= 599 and isinstance(payload, bytes):
                session = get_session(conductor, state)
                outcome = session.submit_overflow(status, dict(response.headers), payload)
                state.set_provider_metadata("opencode_stock_recovery_outcome", outcome)
                return
        pending.extend(value for value in (getattr(current, "__cause__", None),
                                            getattr(current, "__context__", None)) if value is not None)
    raise StockExecutionError("Cannot mirror provider overflow without its original HTTP response") from error
