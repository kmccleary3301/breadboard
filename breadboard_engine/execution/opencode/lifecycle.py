"""Owned OpenCode subprocess, SSE subscription, and durable session binding."""
from __future__ import annotations
import atexit
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from typing import Callable
import urllib.error
import urllib.request
import uuid
from ..opencode_auxiliary import AuxiliaryTransport
from .wire import StockExecutionError
from ..local_http_proxy import LocalHTTPProxy
from .interception import handler_for

DEFAULT_BINARY = Path.home() / ".cache/bb-compaction-e4/pkgs/opencode-darwin-arm64@1.2.17/package/bin/opencode"


LABEL_PLUGIN = '''export const BreadboardTransportLabels = async () => ({
  "chat.headers": async (input, output) => {
    output.headers["x-bb-stock-session"] = input.sessionID;
    output.headers["x-bb-stock-agent"] = input.agent.name;
    output.headers["x-bb-stock-provider"] = input.model.providerID;
  }
});
'''


class OpenCodeLifecycle:
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
        self.lifecycle_threads: list[threading.Thread] = []
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
        from ...security.path_policy import prepare_workspace_temp_directory
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

        self.proxy = LocalHTTPProxy(handler_for(self))
        self.proxy.start()
        providers = config.setdefault("provider", {})
        # Stock autoloads free Zen models without auth (provider.ts:131-150).
        # Preserve that availability and intercept it without inventing a key.
        providers.setdefault("opencode", {})
        for pid in set(providers) | {"openai"}:
            options = providers.setdefault(pid, {}).setdefault("options", {})
            options["baseURL"] = f"http://127.0.0.1:{self.proxy.server_port}/v1"
            if pid != "opencode":
                options["apiKey"] = "bb-local-tool-proxy"
        from ...security.child_environment import build_child_environment
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
            from ...security import build_restricted_process_command
            self.worktree_root = None
            self.git_metadata_roots = ()
            if stock_worktree_read_only:
                from ...security.stock_worktree import resolve_stock_worktree
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
            for target in (self._stdout, self._stderr, self._watch):
                self._start_owned_thread(target)
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
            self._start_owned_thread(self._events)
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


    def _start_owned_thread(self, target):
        thread = threading.Thread(target=target, daemon=True)
        with self.condition:
            self.lifecycle_threads = [owned for owned in self.lifecycle_threads if owned.is_alive()]
            self.lifecycle_threads.append(thread)
            thread.start()

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
        self._start_owned_thread(run)


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
        if self.event_connection is not None:
            self.event_connection.close()
        with self.condition:
            readers = list(self.lifecycle_threads)
        for thread in readers:
            if thread is threading.current_thread():
                continue
            thread.join(timeout=5)
            if thread.is_alive():
                cleanup_error = StockExecutionError("Stock process/SSE reader did not stop")
        if process is not None:
            for pipe in (process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()
        proxy = getattr(self, "proxy", None)
        if proxy is not None:
            proxy.close()
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


