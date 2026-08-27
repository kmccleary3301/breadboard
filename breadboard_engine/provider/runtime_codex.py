"""Codex app-server backed provider runtime."""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple
from breadboard_engine.security import (
    ProcessIsolationUnavailable,
    build_child_environment,
    protected_credential_paths,
    validate_workspace_credential_boundary,
)

from .contracts import (
    ProviderMessage,
    ProviderToolCall,
    ProviderResult,
    ProviderRuntime,
    ProviderRuntimeContext,
    ProviderRuntimeError,
)
from .input_media import resolve_input_media
from .model_role_options import codex_role_options
from .registry import provider_registry


_CODEX_BIN_ENV = "BREADBOARD_CODEX_BIN"
_CODEX_POOL_ENV = "BREADBOARD_CODEX_APP_SERVER_POOL"
_POOL_MAX_IDLE_PER_KEY = 1




@dataclass
class _PooledCodexClientEntry:
    client: "_CodexJsonRpcClient"
    key: Tuple[str, str, str]
    env: Dict[str, str]


_POOL_LOCK = threading.Lock()
_CLIENT_POOL: Dict[Tuple[str, str, str], List[_PooledCodexClientEntry]] = {}


def _codex_pool_enabled() -> bool:
    return (os.getenv(_CODEX_POOL_ENV) or "").strip().lower() in {"1", "true", "yes", "on",
    }


def _resolve_codex_bin_path() -> str:
    explicit = (os.getenv(_CODEX_BIN_ENV) or "").strip()
    if explicit:
        return explicit
    found = shutil.which("codex")
    if found:
        return found
    raise ProviderRuntimeError(
        "Codex binary not found in PATH",
        details={"env_var": _CODEX_BIN_ENV},
    )


class _CodexJsonRpcClient:
    """Small stdio JSON-RPC client for `codex app-server`."""

    def __init__(
        self,
        *,
        codex_bin: str,
        cwd: str,
        env: Dict[str, str],
        protected_paths: Optional[Sequence[str]] = None,
    ) -> None:
        self.codex_bin = codex_bin
        self.cwd = cwd
        self.env = env
        self.protected_paths = tuple(
            str(path)
            for path in (
                protected_paths
                if protected_paths is not None
                else protected_credential_paths()
            )
        )
        self._proc: Optional[subprocess.Popen[str]] = None
        self._lock = threading.Lock()
        self._stderr_lines: Deque[str] = deque(maxlen=400)
        self._stderr_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._proc is not None:
            return
        try:
            validate_workspace_credential_boundary(
                self.cwd,
                protected_paths=self.protected_paths,
            )
            self._proc = subprocess.Popen(
                [self.codex_bin, "app-server", "--listen", "stdio://"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=self.cwd,
                env=self.env,
                bufsize=1,
                shell=False,
            )
        except (ProcessIsolationUnavailable, OSError) as exc:
            self._proc = None
            raise ProviderRuntimeError(
                "Codex app-server isolation is unavailable",
                details={"error_type": exc.__class__.__name__},
            ) from None
        self._start_stderr_drain_thread()

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        if self._stderr_thread and self._stderr_thread.is_alive():
            self._stderr_thread.join(timeout=0.5)
        self._stderr_lines.clear()

    def initialize(self) -> Dict[str, Any]:
        result = self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "breadboard",
                    "title": "BreadBoard",
                    "version": "0.2.0",
                },
                "capabilities": {
                    "experimentalApi": True,
                },
            },
        )
        self.notify("initialized", {})
        return result

    def thread_start(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self.request("thread/start", params)

    def turn_start(
        self,
        thread_id: str,
        input_items: List[Dict[str, Any]] | Dict[str, Any] | str,
        *,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if isinstance(input_items, str):
            normalized_input: List[Dict[str, Any]] = [{"type": "text", "text": input_items}]
        elif isinstance(input_items, dict):
            normalized_input = [input_items]
        else:
            normalized_input = list(input_items)
        params = {
            "threadId": thread_id,
            "input": normalized_input,
        }
        params.update(overrides or {})
        return self.request("turn/start", params)

    def notify(self, method: str, params: Optional[Dict[str, Any]] | None) -> None:
        self._write_message({"method": method, "params": params})

    def request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        request_id = f"bb-{os.getpid()}-{threading.get_ident()}-{id(self)}"
        self._write_message({"id": request_id, "method": method, "params": params or {}})
        while True:
            msg = self._read_message()
            if "method" in msg and "id" in msg:
                response = self._handle_server_request(msg)
                self._write_message({"id": msg["id"], "result": response})
                continue
            if msg.get("id") != request_id:
                continue
            if "error" in msg:
                err = msg["error"]
                details: Dict[str, Any] = {
                    "method": method,
                    "error_type": type(err).__name__,
                }
                if isinstance(err, dict) and isinstance(err.get("code"), int):
                    details["code"] = err["code"]
                raise ProviderRuntimeError(
                    "Codex app-server request failed",
                    details=details,
                )
            result = msg.get("result")
            return result if isinstance(result, dict) else {"result": result}

    def next_notification(self, timeout_s: Optional[float] = None) -> Optional[Dict[str, Any]]:
        while True:
            msg = self._read_message(timeout_s=timeout_s)
            if msg is None:
                return None
            if "method" in msg and "id" in msg:
                response = self._handle_server_request(msg)
                self._write_message({"id": msg["id"], "result": response})
                continue
            if "method" in msg and "id" not in msg:
                return msg

    def _handle_server_request(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        method = str(msg.get("method") or "")
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            return {"decision": "cancel"}
        return {}

    def _write_message(self, payload: Dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise ProviderRuntimeError("Codex app-server is not running")
        with self._lock:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()

    def _read_message(self, timeout_s: Optional[float] = None) -> Optional[Dict[str, Any]]:
        proc = self._proc
        if proc is None or proc.stdout is None:
            raise ProviderRuntimeError("Codex app-server is not running")
        if timeout_s is not None:
            try:
                ready, _, _ = select.select([proc.stdout], [], [], max(0.0, timeout_s))
            except Exception:
                ready = [proc.stdout]
            if not ready:
                return None
        line = proc.stdout.readline()
        if not line:
            raise ProviderRuntimeError(
                "Codex app-server closed stdout unexpectedly",
                details={"stderr_line_count": len(self._stderr_lines)},
            )
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            raise ProviderRuntimeError(
                "Codex app-server emitted invalid JSON",
                details={"line_bytes": len(line.encode("utf-8", errors="replace"))},
            ) from None
        if not isinstance(payload, dict):
            raise ProviderRuntimeError(
                "Codex app-server emitted a non-object JSON-RPC payload",
                details={"payload_type": type(payload).__name__},
            )
        return payload

    def _start_stderr_drain_thread(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return

        def _drain() -> None:
            stderr = proc.stderr
            if stderr is None:
                return
            for line in stderr:
                self._stderr_lines.append(line.rstrip("\n"))

        self._stderr_thread = threading.Thread(target=_drain, daemon=True)
        self._stderr_thread.start()


def _reset_codex_client_pool_for_tests() -> None:
    with _POOL_LOCK:
        keys = list(_CLIENT_POOL.keys())
        for key in keys:
            entries = _CLIENT_POOL.pop(key, [])
            for entry in entries:
                try:
                    entry.client.close()
                except Exception:
                    pass


def _acquire_pooled_client(*, codex_bin: str, cwd: str, model: str, env: Dict[str, str]) -> tuple[_CodexJsonRpcClient, bool]:
    if not _codex_pool_enabled():
        client = _CodexJsonRpcClient(codex_bin=codex_bin, cwd=cwd, env=env)
        return client, False
    key = (codex_bin, cwd, model)
    with _POOL_LOCK:
        bucket = _CLIENT_POOL.get(key)
        if bucket:
            entry = bucket.pop()
            if not bucket:
                _CLIENT_POOL.pop(key, None)
            return entry.client, True
    client = _CodexJsonRpcClient(codex_bin=codex_bin, cwd=cwd, env=env)
    return client, False


def _release_pooled_client(
    *,
    codex_bin: str,
    cwd: str,
    model: str,
    env: Dict[str, str],
    client: _CodexJsonRpcClient,
    healthy: bool,
) -> None:
    if not healthy or not _codex_pool_enabled():
        try:
            client.close()
        except Exception:
            pass
        return
    key = (codex_bin, cwd, model)
    entry = _PooledCodexClientEntry(client=client, key=key, env=dict(env))
    extras: List[_PooledCodexClientEntry] = []
    with _POOL_LOCK:
        bucket = _CLIENT_POOL.setdefault(key, [])
        bucket.append(entry)
        while len(bucket) > _POOL_MAX_IDLE_PER_KEY:
            extras.append(bucket.pop(0))
    for extra in extras:
        try:
            extra.client.close()
        except Exception:
            pass


def prewarm_codex_app_server(*, model: str, cwd: str, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Best-effort process warmup for the pooled Codex app-server client."""

    normalized_cwd = str(cwd or "").strip() or os.getcwd()
    normalized_model = str(model or "").strip()
    if not normalized_model:
        raise ProviderRuntimeError("Codex prewarm requires a non-empty model")
    if not _codex_pool_enabled():
        return {
            "disabled": True,
            "reason": f"{_CODEX_POOL_ENV} is not enabled",
            "model": normalized_model,
        }
    warm_env = build_child_environment(source=env)
    codex_bin = _resolve_codex_bin_path()
    started_at = time.monotonic()
    client, cache_hit = _acquire_pooled_client(
        codex_bin=codex_bin,
        cwd=normalized_cwd,
        model=normalized_model,
        env=warm_env,
    )
    acquired_at = time.monotonic()
    healthy = False
    try:
        if not cache_hit:
            client.start()
            started_client_at = time.monotonic()
            client.initialize()
            initialized_at = time.monotonic()
        else:
            started_client_at = acquired_at
            initialized_at = acquired_at
        healthy = True
        return {
            "cache_hit": cache_hit,
            "cwd": normalized_cwd,
            "model": normalized_model,
            "acquire_seconds": round(acquired_at - started_at, 6),
            "start_seconds": (
                round(started_client_at - acquired_at, 6) if not cache_hit else 0.0
            ),
            "initialize_seconds": (
                round(initialized_at - started_client_at, 6) if not cache_hit else 0.0
            ),
            "total_seconds": round(time.monotonic() - started_at, 6),
        }
    finally:
        _release_pooled_client(
            codex_bin=codex_bin,
            cwd=normalized_cwd,
            model=normalized_model,
            env=warm_env,
            client=client,
            healthy=healthy,
        )


class CodexAppServerRuntime(ProviderRuntime):
    """Provider runtime that delegates execution to a local Codex app-server."""

    def __init__(self, descriptor) -> None:
        super().__init__(descriptor)
        self._client: Optional[_CodexJsonRpcClient] = None
        self._thread_id: Optional[str] = None
        self._session_model: Optional[str] = None
        self._session_cwd: Optional[str] = None
        self._leased_client_key: Optional[Tuple[str, str, str]] = None
        self._leased_client_env: Optional[Dict[str, str]] = None
        self._message_phase_by_item_id: Dict[str, str] = {}
        self._final_message_chunks: Dict[str, List[str]] = {}
        self._tool_output_buffers: Dict[str, Dict[str, str]] = {}
        self._reasoning_chunks: Dict[str, List[str]] = {}
        self._reasoning_summary_chunks: Dict[str, List[str]] = {}
        self._command_items: Dict[str, Dict[str, Any]] = {}
        self._completed_item_ids: set[str] = set()
        self._last_client_setup_timing: Dict[str, float] = {}

    def __del__(self) -> None:  # pragma: no cover - best effort cleanup
        self._release_leased_client(healthy=False)

    def create_client(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        default_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        return {
            "api_key": api_key,
            "base_url": base_url,
            "default_headers": dict(default_headers or {}),
        }

    def invoke(
        self,
        *,
        client: Any,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        stream: bool,
        context: ProviderRuntimeContext,
    ) -> ProviderResult:
        del client, tools
        context.raise_if_cancelled()
        session_state = context.session_state
        cwd = self._resolve_cwd(context)
        invoke_started_at = time.monotonic()
        healthy_client = False
        try:
            app_client = self._ensure_client(model=model, cwd=cwd)
            invoke_after_client_at = time.monotonic()
            user_input = self._extract_latest_user_input(
                messages, context=context
            )
            if not user_input:
                raise ProviderRuntimeError(
                    "Codex runtime requires a latest user message to execute"
                )

            role_options = codex_role_options(context)
            if role_options:
                start_result = app_client.turn_start(
                    self._thread_id or "",
                    user_input,
                    overrides=role_options,
                )
            else:
                start_result = app_client.turn_start(
                    self._thread_id or "",
                    user_input,
                )
            invoke_after_turn_start_at = time.monotonic()
            turn = start_result.get("turn") if isinstance(start_result, dict) else None
            turn_id = turn.get("id") if isinstance(turn, dict) else None
            if not isinstance(turn_id, str) or not turn_id:
                raise ProviderRuntimeError(
                    "Codex app-server turn/start did not return a valid turn id",
                    kind="protocol",
                    details={"code": "invalid_codex_turn_start"},
                )

            turn_index = None
            if session_state is not None:
                try:
                    turn_index = session_state.get_provider_metadata("current_turn_index")
                except Exception:
                    turn_index = None

            self._message_phase_by_item_id.clear()
            self._final_message_chunks.clear()
            self._tool_output_buffers.clear()
            self._reasoning_chunks.clear()
            self._reasoning_summary_chunks.clear()
            self._command_items.clear()
            self._completed_item_ids.clear()
            completed_turn: Optional[Dict[str, Any]] = None
            final_answer_completed_at: Optional[float] = None
            first_notification_at: Optional[float] = None
            first_final_answer_delta_at: Optional[float] = None
            notification_count = 0
            usage: Optional[Dict[str, Any]] = None

            while True:
                context.raise_if_cancelled()
                timeout_s = 1.5 if final_answer_completed_at is not None else None
                try:
                    notification = app_client.next_notification(timeout_s=timeout_s)
                except TypeError:
                    notification = app_client.next_notification()
                context.raise_if_cancelled()
                if notification is None:
                    if final_answer_completed_at is not None:
                        raise ProviderRuntimeError(
                            "Codex app-server omitted turn completion",
                            kind="protocol",
                            details={"code": "missing_codex_terminal"},
                        )
                    continue
                notification_count += 1
                if first_notification_at is None:
                    first_notification_at = time.monotonic()
                if not isinstance(notification, dict):
                    raise self._protocol_error(
                        "Malformed Codex notification", context
                    )
                method = notification.get("method")
                params = notification.get("params")
                if (
                    not isinstance(method, str)
                    or not method
                    or not isinstance(params, dict)
                ):
                    raise self._protocol_error(
                        "Malformed Codex notification envelope", context
                    )
                payload = params

                if (
                    first_final_answer_delta_at is None
                    and method == "item/agentMessage/delta"
                    and isinstance(payload, dict)
                ):
                    item_id = payload.get("itemId", payload.get("item_id"))
                    phase = self._message_phase_by_item_id.get(item_id)
                    delta = payload.get("delta")
                    if phase == "final_answer" and isinstance(delta, str) and delta:
                        first_final_answer_delta_at = time.monotonic()

                if method == "thread/tokenUsage/updated":
                    usage = self._normalize_token_usage_notification(
                        payload,
                        expected_turn_id=turn_id,
                        context=context,
                    )
                    continue

                if method == "turn/completed":
                    completed_turn = self._validate_completed_turn(
                        payload,
                        expected_turn_id=turn_id,
                        turn_index=turn_index,
                        stream=stream,
                        session_state=session_state,
                        context=context,
                    )
                    break

                if self._handle_notification(
                    method=method,
                    payload=payload,
                    turn_index=turn_index,
                    stream=stream,
                    session_state=session_state,
                    context=context,
                    expected_turn_id=turn_id,
                ):
                    final_answer_completed_at = time.monotonic()

            final_texts = self._completed_agent_texts(phase="final_answer")
            reasoning_blocks = self._completed_reasoning_blocks()
            text_blocks = [
                {"type": "text", "text": text}
                for text in self._completed_agent_texts()
            ]
            tool_calls = [
                item["tool_call"]
                for item in self._command_items.values()
                if isinstance(item.get("tool_call"), ProviderToolCall)
            ]
            tool_results = [
                item["tool_result"]
                for item in self._command_items.values()
                if isinstance(item.get("tool_result"), dict)
            ]
            if not final_texts and completed_turn and completed_turn.get("error"):
                raise ProviderRuntimeError(
                    "Codex app-server turn failed",
                    details={
                        "turn_id": completed_turn.get("id"),
                        "status": completed_turn.get("status"),
                        "error_type": type(completed_turn.get("error")).__name__,
                    },
                )

            content_blocks = [*reasoning_blocks, *text_blocks]
            out_messages = (
                [
                    ProviderMessage(
                        role="assistant",
                        content=content_blocks,
                        tool_calls=tool_calls,
                        tool_results=tool_results,
                        finish_reason="stop" if final_texts else "toolUse",
                        index=0,
                    )
                ]
                if content_blocks or tool_calls or tool_results
                else []
            )

            healthy_client = True
            return ProviderResult(
                messages=out_messages,
                raw_response={
                    "provider": "codex",
                    "thread_id": self._thread_id,
                    "turn": {
                        "id": completed_turn.get("id") if completed_turn else None,
                        "status": (
                            completed_turn.get("status") if completed_turn else None
                        ),
                    },
                },
                usage=usage,
                encrypted_reasoning=None,
                reasoning_summaries=self._completed_reasoning_summaries() or None,
                reasoning_blocks=None,
                model=model,
                metadata={
                    "provider_turn_completed": True,
                    "provider_turn_completion_method": "codex_app_server",
                    "provider_turn_completion_reason": "codex_turn_completed",
                    "provider_runtime_timing": {
                        "invoke_total_seconds": round(time.monotonic() - invoke_started_at, 6),
                        "client_ready_seconds": round(invoke_after_client_at - invoke_started_at, 6),
                        "turn_start_seconds": round(invoke_after_turn_start_at - invoke_after_client_at, 6),
                        "post_turn_wait_seconds": round(time.monotonic() - invoke_after_turn_start_at, 6),
                        "first_notification_seconds": (
                            round(first_notification_at - invoke_started_at, 6)
                        if first_notification_at is not None
                        else None
                        ),
                        "first_final_answer_delta_seconds": (
                            round(first_final_answer_delta_at - invoke_started_at, 6)
                        if first_final_answer_delta_at is not None
                        else None
                        ),
                        "notification_count": notification_count,
                        **self._last_client_setup_timing,
                    },
                },
            )
        finally:
            self._release_leased_client(healthy=healthy_client)
    def _ensure_client(self, *, model: str, cwd: str) -> _CodexJsonRpcClient:
        codex_bin = self._resolve_codex_bin()
        env = build_child_environment()
        if (
            self._client is not None and self._thread_id and self._session_model == model and self._session_cwd == cwd
        ):
            self._last_client_setup_timing = {
                "client_cache_hit": True,
                "client_spawn_seconds": 0.0,
                "client_initialize_seconds": 0.0,
                "client_thread_start_seconds": 0.0,
            }
            return self._client

        self._release_leased_client(healthy=True)
        spawn_started_at = time.monotonic()
        client, cache_hit = _acquire_pooled_client(codex_bin=codex_bin, cwd=cwd, model=model, env=env)
        after_acquire_at = time.monotonic()
        after_initialize_at = after_acquire_at
        if not cache_hit:
            client.start()
            after_start_at = time.monotonic()
            client.initialize()
            after_initialize_at = time.monotonic()
        else:
            after_start_at = spawn_started_at
        thread_result = client.thread_start(
            {
                "model": model,
                "cwd": cwd,
                "sandbox": "read-only",
                "approvalPolicy": "never",
                "ephemeral": True,
                "dynamicTools": [],
                "environments": [],
            }
        )
        after_thread_start_at = time.monotonic()
        thread = (
            thread_result.get("thread") if isinstance(thread_result, dict) else None
        )
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            client.close()
            raise ProviderRuntimeError(
                "Codex app-server thread/start did not return a valid thread id",
                kind="protocol",
                details={"code": "invalid_codex_thread_start"},
            )
        self._client = client
        self._thread_id = thread_id
        self._session_model = model
        self._session_cwd = cwd
        self._leased_client_key = (codex_bin, cwd, model)
        self._leased_client_env = dict(env)
        self._last_client_setup_timing = {
            "client_cache_hit": cache_hit,
            "client_spawn_seconds": (
                round((after_start_at - spawn_started_at), 6) if not cache_hit else 0.0
            ),
            "client_initialize_seconds": (
                round(after_initialize_at - after_start_at, 6) if not cache_hit else 0.0
            ),
            "client_thread_start_seconds": round(after_thread_start_at - after_initialize_at, 6),
        }
        return client

    def _release_leased_client(self, *, healthy: bool) -> None:
        client = self._client
        key = self._leased_client_key
        env = self._leased_client_env
        self._client = None
        self._thread_id = None
        self._session_model = None
        self._session_cwd = None
        self._leased_client_key = None
        self._leased_client_env = None
        if client is None or key is None or env is None:
            return
        _release_pooled_client(
            codex_bin=key[0],
            cwd=key[1],
            model=key[2],
            env=env,
            client=client,
            healthy=healthy,
        )

    def _resolve_codex_bin(self) -> str:
        return _resolve_codex_bin_path()

    def _resolve_cwd(self, context: ProviderRuntimeContext) -> str:
        session_state = context.session_state
        workspace = getattr(session_state, "workspace", None)
        if isinstance(workspace, str) and workspace.strip():
            return workspace.strip()
        return os.getcwd()

    def _extract_latest_user_input(
        self,
        messages: List[Dict[str, Any]],
        *,
        context: ProviderRuntimeContext | None = None,
    ) -> Any:
        for message in reversed(messages or []):
            if str(message.get("role") or "") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                text_parts: List[str] = []
                media_parts: List[Dict[str, str]] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "media":
                        media = resolve_input_media(block, context)
                        media_parts.append(
                            {"type": "image", "url": media.data_url}
                        )
                        continue
                    text = block.get("text")
                    if isinstance(text, str) and text.strip():
                        text_parts.append(text.strip())
                text = "\n\n".join(text_parts).strip()
                if media_parts:
                    return (
                        ([{"type": "text", "text": text}] if text else [])
                        + media_parts
                    )
                if text:
                    return text
        return ""

    def _protocol_error(
        self,
        message: str,
        context: ProviderRuntimeContext,
        *,
        code: str = "invalid_codex_event",
    ) -> ProviderRuntimeError:
        return ProviderRuntimeError(
            message,
            kind="protocol",
            output_emitted=bool(
                context.exchange_recorder
                and context.exchange_recorder.output_emitted
            ),
            details={"code": code},
        )

    def _validate_codex_item(
        self,
        item: Any,
        *,
        completed: bool,
        context: ProviderRuntimeContext,
    ) -> Tuple[str, str]:
        if not isinstance(item, dict):
            raise self._protocol_error("Malformed Codex item", context)
        item_id = item.get("id")
        item_type = item.get("type")
        if (
            not isinstance(item_id, str)
            or not item_id
            or len(item_id) > 256
            or not isinstance(item_type, str)
            or not item_type
        ):
            raise self._protocol_error(
                "Codex item is missing its identifier or type", context
            )
        if item_type == "userMessage":
            content = item.get("content")
            if not isinstance(content, list):
                raise self._protocol_error("Malformed Codex user item", context)
            required_fields = {
                "text": ("text",),
                "image": ("url",),
                "localImage": ("path",),
                "skill": ("name", "path"),
                "mention": ("name", "path"),
            }
            for block in content:
                block_type = block.get("type") if isinstance(block, dict) else None
                fields = required_fields.get(block_type)
                if fields is None or any(
                    not isinstance(block.get(field), str) for field in fields
                ):
                    raise self._protocol_error(
                        "Malformed Codex user content", context
                    )
        elif item_type == "hookPrompt":
            fragments = item.get("fragments")
            if not isinstance(fragments, list) or any(
                not isinstance(fragment, dict)
                or not isinstance(fragment.get("hookRunId"), str)
                or not isinstance(fragment.get("text"), str)
                for fragment in fragments
            ):
                raise self._protocol_error("Malformed Codex hook item", context)
        elif item_type == "agentMessage":
            phase = item.get("phase")
            if phase not in {"commentary", "final_answer"}:
                raise self._protocol_error(
                    "Codex agent item has an invalid phase", context
                )
            if not isinstance(item.get("text"), str):
                raise self._protocol_error(
                    "Codex agent item has invalid text", context
                )
        elif item_type == "reasoning":
            for field in ("content", "summary"):
                value = item.get(field, [])
                if not isinstance(value, list) or any(
                    not isinstance(part, str) for part in value
                ):
                    raise self._protocol_error(
                        "Codex reasoning item has invalid content", context
                    )
        elif item_type == "plan":
            if not isinstance(item.get("text"), str):
                raise self._protocol_error("Codex plan item has invalid text", context)
        elif item_type == "commandExecution":
            if (
                not isinstance(item.get("command"), str)
                or not isinstance(item.get("commandActions"), list)
                or not isinstance(item.get("cwd"), str)
                or item.get("status")
                not in {"inProgress", "completed", "failed", "declined"}
            ):
                raise self._protocol_error(
                    "Codex command item is missing required fields", context
                )
            command_actions = item["commandActions"]
            if any(not isinstance(action, dict) for action in command_actions):
                raise self._protocol_error(
                    "Codex command actions are malformed", context
                )
            process_id = item.get("processId")
            if process_id is not None and not isinstance(process_id, str):
                raise self._protocol_error(
                    "Codex command process identifier is malformed", context
                )
            duration_ms = item.get("durationMs")
            if duration_ms is not None and (
                not isinstance(duration_ms, int)
                or isinstance(duration_ms, bool)
                or duration_ms < 0
            ):
                raise self._protocol_error(
                    "Codex command duration is malformed", context
                )
            source = item.get("source", "agent")
            if source not in {
                "agent",
                "userShell",
                "unifiedExecStartup",
                "unifiedExecInteraction",
            }:
                raise self._protocol_error(
                    "Codex command source is malformed", context
                )
            if completed and item.get("status") == "inProgress":
                raise self._protocol_error(
                    "Completed Codex command is still in progress", context
                )
            aggregated = item.get("aggregatedOutput")
            if aggregated is not None and not isinstance(aggregated, str):
                raise self._protocol_error(
                    "Codex command output is malformed", context
                )
            exit_code = item.get("exitCode")
            if exit_code is not None and (
                not isinstance(exit_code, int) or isinstance(exit_code, bool)
            ):
                raise self._protocol_error(
                    "Codex command exit code is malformed", context
                )
        else:
            raise self._protocol_error(
                "Unsupported Codex item type",
                context,
                code="unknown_codex_item",
            )
        return item_id, item_type

    def _handle_notification(
        self,
        *,
        method: str,
        payload: Dict[str, Any],
        turn_index: Optional[int],
        stream: bool,
        session_state: Any,
        context: ProviderRuntimeContext,
        expected_turn_id: str,
    ) -> bool:
        turn_scoped = method == "turn/started" or method.startswith("item/")
        if turn_scoped and (
            payload.get("threadId") != self._thread_id
            or payload.get("turnId") != expected_turn_id
        ):
            raise self._protocol_error(
                "Codex notification correlation mismatch", context
            )

        if method == "turn/started":
            turn = payload.get("turn")
            if (
                not isinstance(turn, dict)
                or not isinstance(turn.get("id"), str)
                or not turn.get("id")
                or turn.get("status") != "inProgress"
                or turn.get("id") != expected_turn_id
                or not isinstance(turn.get("items"), list)
                or not isinstance(payload.get("threadId"), str)
            ):
                raise self._protocol_error(
                    "Malformed Codex turn start", context
                )
            context.record_provider_event("response_start")
            return False

        if method == "item/started":
            started_at = payload.get("startedAtMs")
            if (
                not isinstance(started_at, int)
                or isinstance(started_at, bool)
                or started_at < 0
            ):
                raise self._protocol_error(
                    "Malformed Codex item start timestamp", context
                )
            item = payload.get("item")
            item_id, item_type = self._validate_codex_item(
                item, completed=False, context=context
            )
            if (
                item_id in self._message_phase_by_item_id
                or item_id in self._reasoning_chunks
                or item_id in self._reasoning_summary_chunks
                or item_id in self._command_items
                or item_id in self._completed_item_ids
            ):
                raise self._protocol_error("Duplicate Codex item start", context)
            if item_type in {"userMessage", "hookPrompt"}:
                return False
            if item_type == "agentMessage":
                phase = item["phase"]
                self._message_phase_by_item_id[item_id] = phase
                text = item["text"]
                self._final_message_chunks[item_id] = [text] if text else []
                context.record_provider_event(
                    "text_start" if phase == "final_answer" else "thinking_start",
                    {"content_index": 0, "message_id": item_id},
                )
            elif item_type == "reasoning":
                self._reasoning_chunks[item_id] = list(item.get("content", []))
                self._reasoning_summary_chunks[item_id] = list(
                    item.get("summary", [])
                )
                context.record_provider_event(
                    "thinking_start",
                    {"content_index": 0, "message_id": item_id},
                )
            elif item_type == "plan":
                text = item["text"]
                self._reasoning_summary_chunks[item_id] = [text] if text else []
                context.record_provider_event(
                    "thinking_start",
                    {"content_index": 0, "message_id": item_id},
                )
            else:
                tool_call = ProviderToolCall(
                    id=item_id,
                    name="shell_command",
                    arguments={
                        "command": item["command"],
                        "command_actions": item["commandActions"],
                        "cwd": item["cwd"],
                        "source": item.get("source", "agent"),
                    },
                )
                self._command_items[item_id] = {
                    "tool_call": tool_call,
                    "tool_result": None,
                }
                context.record_provider_event(
                    "tool_call_start",
                    {
                        "content_index": 0,
                        "message_id": item_id,
                        "call_id": item_id,
                        "name": "shell_command",
                    },
                )
                self._emit_tool_exec_start(
                    session_state, item, turn_index=turn_index
                )
            return False

        if method == "item/completed":
            completed_at = payload.get("completedAtMs")
            if (
                not isinstance(completed_at, int)
                or isinstance(completed_at, bool)
                or completed_at < 0
            ):
                raise self._protocol_error(
                    "Malformed Codex item completion timestamp", context
                )
            item = payload.get("item")
            item_id, item_type = self._validate_codex_item(
                item, completed=True, context=context
            )
            if item_id in self._completed_item_ids:
                raise self._protocol_error("Duplicate Codex item completion", context)
            if item_type in {"userMessage", "hookPrompt"}:
                self._completed_item_ids.add(item_id)
                return False
            final_answer = False
            if item_type == "agentMessage":
                phase = item["phase"]
                prior_phase = self._message_phase_by_item_id.get(item_id)
                if prior_phase is not None and prior_phase != phase:
                    raise self._protocol_error(
                        "Codex agent phase changed during streaming", context
                    )
                self._message_phase_by_item_id[item_id] = phase
                self._final_message_chunks[item_id] = [item["text"]]
                context.record_provider_event(
                    "text_end" if phase == "final_answer" else "thinking_end",
                    {"content_index": 0, "message_id": item_id},
                )
                final_answer = phase == "final_answer"
            elif item_type == "reasoning":
                self._reasoning_chunks[item_id] = list(item.get("content", []))
                self._reasoning_summary_chunks[item_id] = list(
                    item.get("summary", [])
                )
                context.record_provider_event(
                    "thinking_end",
                    {"content_index": 0, "message_id": item_id},
                )
            elif item_type == "plan":
                self._reasoning_summary_chunks[item_id] = [item["text"]]
                context.record_provider_event(
                    "thinking_end",
                    {"content_index": 0, "message_id": item_id},
                )
            else:
                tool_call = ProviderToolCall(
                    id=item_id,
                    name="shell_command",
                    arguments={
                        "command": item["command"],
                        "command_actions": item["commandActions"],
                        "cwd": item["cwd"],
                        "source": item.get("source", "agent"),
                    },
                )
                existing = self._command_items.get(item_id)
                if (
                    existing is not None
                    and isinstance(existing.get("tool_call"), ProviderToolCall)
                    and existing["tool_call"].arguments_json
                    != tool_call.arguments_json
                ):
                    raise self._protocol_error(
                        "Codex command changed during execution", context
                    )
                aggregated = item.get("aggregatedOutput")
                if aggregated is None:
                    buffers = self._tool_output_buffers.get(
                        item_id, {"stdout": "", "stderr": ""}
                    )
                    aggregated = f"{buffers.get('stdout', '')}{buffers.get('stderr', '')}"
                failed = (
                    item["status"] in {"failed", "declined"}
                    or item.get("exitCode") not in {None, 0}
                )
                tool_result = {
                    "call_id": item_id,
                    "error" if failed else "result": aggregated,
                }
                self._command_items[item_id] = {
                    "tool_call": tool_call,
                    "tool_result": tool_result,
                }
                context.record_provider_event(
                    "tool_call_end",
                    {
                        "content_index": 0,
                        "message_id": item_id,
                        "call_id": item_id,
                        "arguments_json": tool_call.arguments_json,
                        "arguments": tool_call.parsed_arguments,
                    },
                )
                self._emit_tool_exec_end(
                    session_state, item, turn_index=turn_index
                )
            self._completed_item_ids.add(item_id)
            return final_answer

        if method == "item/agentMessage/delta":
            item_id = payload.get("itemId", payload.get("item_id"))
            delta = payload.get("delta")
            if (
                not isinstance(item_id, str)
                or not item_id
                or not isinstance(delta, str)
                or not delta
                or item_id not in self._message_phase_by_item_id
            ):
                raise self._protocol_error(
                    "Malformed Codex agent-message delta", context
                )
            phase = self._message_phase_by_item_id[item_id]
            self._final_message_chunks.setdefault(item_id, []).append(delta)
            context.record_provider_event(
                "text_delta" if phase == "final_answer" else "thinking_delta",
                {"content_index": 0, "message_id": item_id, "delta": delta},
            )
            if stream and session_state is not None:
                event_type = (
                    "assistant.message.delta"
                    if phase == "final_answer"
                    else "assistant.thought_summary.delta"
                )
                session_state._emit_event(
                    event_type,
                    {"delta": delta, "item_id": item_id},
                    turn=turn_index,
                )
            return False

        if method in {
            "item/reasoning/textDelta",
            "item/reasoning/summaryTextDelta",
            "item/reasoningSummary/textDelta",
        }:
            item_id = payload.get("itemId", payload.get("item_id"))
            delta = payload.get("delta")
            summary_delta = method != "item/reasoning/textDelta"
            index_key = "summaryIndex" if summary_delta else "contentIndex"
            index = payload.get(index_key, 0)
            if (
                not isinstance(item_id, str)
                or not item_id
                or not isinstance(delta, str)
                or not delta
                or not isinstance(index, int)
                or isinstance(index, bool)
                or index < 0
                or item_id not in self._reasoning_chunks
            ):
                raise self._protocol_error("Malformed Codex reasoning delta", context)
            target = (
                self._reasoning_summary_chunks
                if summary_delta
                else self._reasoning_chunks
            )
            target.setdefault(item_id, []).append(delta)
            context.record_provider_event(
                "thinking_delta",
                {
                    "content_index": index,
                    "message_id": item_id,
                    "delta": delta,
                },
            )
            if stream and session_state is not None:
                event_type = (
                    "assistant.thought_summary.delta"
                    if summary_delta
                    else "assistant.reasoning.delta"
                )
                session_state._emit_event(
                    event_type,
                    {"delta": delta, "item_id": item_id},
                    turn=turn_index,
                )
            return False

        if method == "item/reasoning/summaryPartAdded":
            item_id = payload.get("itemId")
            summary_index = payload.get("summaryIndex")
            if (
                not isinstance(item_id, str)
                or item_id not in self._reasoning_chunks
                or not isinstance(summary_index, int)
                or isinstance(summary_index, bool)
                or summary_index < 0
            ):
                raise self._protocol_error(
                    "Malformed Codex reasoning summary boundary", context
                )
            return False

        if method == "item/plan/delta":
            item_id = payload.get("itemId")
            delta = payload.get("delta")
            if (
                not isinstance(item_id, str)
                or item_id not in self._reasoning_summary_chunks
                or not isinstance(delta, str)
                or not delta
            ):
                raise self._protocol_error("Malformed Codex plan delta", context)
            self._reasoning_summary_chunks[item_id].append(delta)
            context.record_provider_event(
                "thinking_delta",
                {"content_index": 0, "message_id": item_id, "delta": delta},
            )
            return False

        if method == "item/commandExecution/outputDelta":
            item_id = payload.get("itemId")
            delta = payload.get("delta")
            if (
                not isinstance(item_id, str)
                or item_id not in self._command_items
                or not isinstance(delta, str)
            ):
                raise self._protocol_error(
                    "Malformed Codex command output delta", context
                )
            buffers = self._tool_output_buffers.setdefault(
                item_id, {"stdout": "", "stderr": ""}
            )
            buffers["stdout"] += delta
            if stream and session_state is not None and delta:
                session_state._emit_event(
                    "tool.exec.stdout.delta",
                    {"exec_id": item_id, "delta": delta},
                    turn=turn_index,
                )
            return False

        if method == "thread/started":
            thread = payload.get("thread")
            if (
                not isinstance(thread, dict)
                or thread.get("id") != self._thread_id
            ):
                raise self._protocol_error(
                    "Malformed Codex thread lifecycle event", context
                )
            return False

        if method == "thread/status/changed":
            status = payload.get("status")
            if (
                payload.get("threadId") != self._thread_id
                or not isinstance(status, dict)
                or status.get("type") not in {"active", "idle"}
            ):
                raise self._protocol_error(
                    "Malformed Codex thread status event", context
                )
            if status["type"] == "active" and not isinstance(
                status.get("activeFlags"), list
            ):
                raise self._protocol_error(
                    "Malformed Codex active thread status", context
                )
            return False

        raise self._protocol_error(
            "Unknown Codex provider event",
            context,
            code="unknown_codex_event",
        )

    def _normalize_token_usage_notification(
        self,
        payload: Dict[str, Any],
        *,
        expected_turn_id: str,
        context: ProviderRuntimeContext,
    ) -> Dict[str, Any]:
        if (
            payload.get("threadId") != self._thread_id
            or payload.get("turnId") != expected_turn_id
        ):
            raise self._protocol_error(
                "Codex usage correlation mismatch", context
            )
        token_usage = payload.get("tokenUsage")
        if not isinstance(token_usage, dict):
            raise self._protocol_error("Malformed Codex token usage", context)

        def breakdown(field: str) -> Dict[str, int]:
            value = token_usage.get(field)
            required = {
                "cachedInputTokens",
                "inputTokens",
                "outputTokens",
                "reasoningOutputTokens",
                "totalTokens",
            }
            if (
                not isinstance(value, dict)
                or not required.issubset(value)
                or any(
                    not isinstance(value[key], int)
                    or isinstance(value[key], bool)
                    or value[key] < 0
                    for key in required
                )
            ):
                raise self._protocol_error(
                    "Malformed Codex token usage breakdown", context
                )
            return {key: value[key] for key in required}

        last = breakdown("last")
        total = breakdown("total")
        context_window = token_usage.get("modelContextWindow")
        if context_window is not None and (
            not isinstance(context_window, int)
            or isinstance(context_window, bool)
            or context_window < 0
        ):
            raise self._protocol_error(
                "Malformed Codex model context window", context
            )
        extensions: Dict[str, Any] = {
            "codex_total": {
                "cache_read_tokens": total["cachedInputTokens"],
                "input_tokens": total["inputTokens"],
                "output_tokens": total["outputTokens"],
                "reasoning_tokens": total["reasoningOutputTokens"],
                "total_tokens": total["totalTokens"],
            }
        }
        if context_window is not None:
            extensions["model_context_window"] = context_window
        return {
            "cache_read_tokens": last["cachedInputTokens"],
            "input_tokens": last["inputTokens"],
            "output_tokens": last["outputTokens"],
            "reasoning_tokens": last["reasoningOutputTokens"],
            "total_tokens": last["totalTokens"],
            "extensions": extensions,
        }

    def _validate_completed_turn(
        self,
        payload: Dict[str, Any],
        *,
        expected_turn_id: str,
        turn_index: Optional[int],
        stream: bool,
        session_state: Any,
        context: ProviderRuntimeContext,
    ) -> Dict[str, Any]:
        turn = payload.get("turn")
        if not isinstance(turn, dict):
            raise self._protocol_error("Malformed Codex turn completion", context)
        if payload.get("threadId") != self._thread_id:
            raise self._protocol_error(
                "Codex turn completion thread mismatch", context
            )
        if turn.get("id") != expected_turn_id:
            raise self._protocol_error(
                "Codex turn completion identifier mismatch", context
            )
        status = turn.get("status")
        if status not in {"completed", "interrupted", "failed", "inProgress"}:
            raise self._protocol_error("Invalid Codex turn status", context)
        if status != "completed":
            raise ProviderRuntimeError(
                "Codex app-server turn did not complete",
                kind="provider",
                output_emitted=bool(
                    context.exchange_recorder
                    and context.exchange_recorder.output_emitted
                ),
                details={"code": f"codex_turn_{status}"},
            )
        items = turn.get("items")
        if not isinstance(items, list) or turn.get("itemsView", "full") != "full":
            raise self._protocol_error(
                "Codex turn completion omitted authoritative items", context
            )
        seen: set[str] = set()
        response_types = {"agentMessage", "reasoning", "plan", "commandExecution"}
        for item in items:
            item_id, item_type = self._validate_codex_item(
                item, completed=True, context=context
            )
            if item_id in seen:
                raise self._protocol_error(
                    "Codex turn completion contains duplicate items", context
                )
            seen.add(item_id)
            if item_type in response_types and item_id not in self._completed_item_ids:
                item_started = (
                    item_id in self._message_phase_by_item_id
                    or item_id in self._reasoning_chunks
                    or item_id in self._reasoning_summary_chunks
                    or item_id in self._command_items
                )
                if not item_started:
                    self._handle_notification(
                        method="item/started",
                        payload={
                            "item": item,
                            "startedAtMs": 0,
                            "threadId": self._thread_id,
                            "turnId": expected_turn_id,
                        },
                        turn_index=turn_index,
                        stream=stream,
                        session_state=session_state,
                        context=context,
                        expected_turn_id=expected_turn_id,
                    )
                self._handle_notification(
                    method="item/completed",
                    payload={
                        "item": item,
                        "completedAtMs": 0,
                        "threadId": self._thread_id,
                        "turnId": expected_turn_id,
                    },
                    turn_index=turn_index,
                    stream=stream,
                    session_state=session_state,
                    context=context,
                    expected_turn_id=expected_turn_id,
                )
        if not self._completed_item_ids.issubset(seen):
            raise self._protocol_error(
                "Codex turn completion dropped emitted items", context
            )
        return dict(turn)

    def _completed_agent_texts(
        self, *, phase: str = "final_answer"
    ) -> List[str]:
        texts: List[str] = []
        for item_id, chunks in self._final_message_chunks.items():
            if self._message_phase_by_item_id.get(item_id) != phase:
                continue
            text = "".join(chunks)
            if text:
                texts.append(text)
        return texts

    def _completed_reasoning_summaries(self) -> List[str]:
        return [
            text
            for chunks in self._reasoning_summary_chunks.values()
            for text in chunks
            if text
        ]

    def _completed_reasoning_blocks(self) -> List[Dict[str, str]]:
        blocks: List[Dict[str, str]] = []
        for text in self._completed_agent_texts(phase="commentary"):
            blocks.append({"type": "thinking", "text": text})
        for chunks in self._reasoning_chunks.values():
            blocks.extend(
                {"type": "thinking", "text": text} for text in chunks if text
            )
        for text in self._completed_reasoning_summaries():
            blocks.append({"type": "thinking", "text": text})
        return blocks

    def _emit_tool_exec_start(
        self,
        session_state: Any,
        item: Dict[str, Any],
        *,
        turn_index: Optional[int],
    ) -> None:
        call_id = item["id"]
        process_id = item.get("processId")
        exec_id = process_id if isinstance(process_id, str) and process_id else call_id
        self._tool_output_buffers[call_id] = {"stdout": "", "stderr": ""}
        if session_state is None:
            return
        session_state._emit_event(
            "tool.exec.start",
            {
                "call_id": call_id,
                "exec_id": exec_id,
                "tool": "shell_command",
                "tool_name": "shell_command",
                "command": item["command"],
            },
            turn=turn_index,
        )

    def _emit_tool_exec_end(
        self,
        session_state: Any,
        item: Dict[str, Any],
        *,
        turn_index: Optional[int],
    ) -> None:
        call_id = item["id"]
        process_id = item.get("processId")
        exec_id = process_id if isinstance(process_id, str) and process_id else call_id
        aggregated = item.get("aggregatedOutput")
        existing = self._tool_output_buffers.setdefault(
            call_id, {"stdout": "", "stderr": ""}
        )
        if (
            session_state is not None
            and isinstance(aggregated, str)
            and aggregated
            and not existing["stdout"]
            and not existing["stderr"]
        ):
            existing["stdout"] = aggregated
            session_state._emit_event(
                "tool.exec.stdout.delta",
                {"call_id": call_id, "exec_id": exec_id, "delta": aggregated},
                turn=turn_index,
            )
        exit_code = item.get("exitCode")
        if session_state is not None:
            session_state._emit_event(
                "tool.exec.end",
                {
                    "call_id": call_id,
                    "exec_id": exec_id,
                    "exit_code": exit_code,
                },
                turn=turn_index,
            )
            try:
                session_state.record_tool_event(
                    turn_index,
                    "shell_command",
                    success=(
                        item["status"] == "completed"
                        and (exit_code == 0 or exit_code is None)
                    ),
                    metadata={
                        "is_run_shell": True,
                        "exit_code": exit_code,
                        "call_id": call_id,
                    },
                )
            except Exception:
                pass
        self._tool_output_buffers.pop(call_id, None)


provider_registry.register_runtime("codex_app_server", CodexAppServerRuntime)
