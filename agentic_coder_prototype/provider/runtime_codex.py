"""Codex app-server backed provider runtime."""

from __future__ import annotations

import base64
import json
import os
import select
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from .runtime import (
    ProviderMessage,
    ProviderResult,
    ProviderRuntime,
    ProviderRuntimeContext,
    ProviderRuntimeError,
    provider_registry,
)


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
    return (os.getenv(_CODEX_POOL_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


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

    def __init__(self, *, codex_bin: str, cwd: str, env: Dict[str, str]) -> None:
        self.codex_bin = codex_bin
        self.cwd = cwd
        self.env = env
        self._proc: Optional[subprocess.Popen[str]] = None
        self._lock = threading.Lock()
        self._stderr_lines: Deque[str] = deque(maxlen=400)
        self._stderr_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._proc is not None:
            return
        self._proc = subprocess.Popen(
            [self.codex_bin, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=self.cwd,
            env=self.env,
            bufsize=1,
        )
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

    def turn_start(self, thread_id: str, input_items: List[Dict[str, Any]] | Dict[str, Any] | str) -> Dict[str, Any]:
        if isinstance(input_items, str):
            normalized_input: List[Dict[str, Any]] = [{"type": "text", "text": input_items}]
        elif isinstance(input_items, dict):
            normalized_input = [input_items]
        else:
            normalized_input = list(input_items)
        return self.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": normalized_input,
            },
        )

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
                raise ProviderRuntimeError(
                    f"Codex app-server request failed: {err}",
                    details={"method": method, "error": err},
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
        if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            return {"decision": "accept"}
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
                details={"stderr_tail": self._stderr_tail()},
            )
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProviderRuntimeError(
                "Codex app-server emitted invalid JSON",
                details={"line": line[:2000]},
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderRuntimeError(
                "Codex app-server emitted a non-object JSON-RPC payload",
                details={"payload": payload},
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

    def _stderr_tail(self, limit: int = 40) -> str:
        return "\n".join(list(self._stderr_lines)[-limit:])


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
            "cwd": normalized_cwd,
            "model": normalized_model,
        }
    warm_env = dict(env or os.environ.copy())
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
            "start_seconds": round(started_client_at - acquired_at, 6) if not cache_hit else 0.0,
            "initialize_seconds": round(initialized_at - started_client_at, 6) if not cache_hit else 0.0,
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
        session_state = context.session_state
        cwd = self._resolve_cwd(context)
        invoke_started_at = time.monotonic()
        healthy_client = False
        try:
            app_client = self._ensure_client(model=model, cwd=cwd)
            invoke_after_client_at = time.monotonic()
            user_text = self._extract_latest_user_text(messages)
            if not user_text:
                raise ProviderRuntimeError("Codex runtime requires a latest user message to execute")

            start_result = app_client.turn_start(self._thread_id or "", user_text)
            invoke_after_turn_start_at = time.monotonic()
            turn = start_result.get("turn") if isinstance(start_result, dict) else {}
            turn_id = str((turn or {}).get("id") or "")
            if not turn_id:
                raise ProviderRuntimeError("Codex app-server turn/start did not return a turn id")

            turn_index = None
            if session_state is not None:
                try:
                    turn_index = session_state.get_provider_metadata("current_turn_index")
                except Exception:
                    turn_index = None

            self._message_phase_by_item_id.clear()
            self._final_message_chunks.clear()
            self._tool_output_buffers.clear()
            completed_turn: Optional[Dict[str, Any]] = None
            final_answer_completed_at: Optional[float] = None
            first_notification_at: Optional[float] = None
            first_final_answer_delta_at: Optional[float] = None
            notification_count = 0

            while True:
                timeout_s = 1.5 if final_answer_completed_at is not None else None
                try:
                    notification = app_client.next_notification(timeout_s=timeout_s)
                except TypeError:
                    notification = app_client.next_notification()
                if notification is None:
                    if final_answer_completed_at is not None and self._has_final_answer_text():
                        completed_turn = {"items": self._synthesized_completed_items(), "completion_method": "final_answer_timeout"}
                        break
                    continue
                notification_count += 1
                if first_notification_at is None:
                    first_notification_at = time.monotonic()
                method = str(notification.get("method") or "")
                params = notification.get("params")
                payload = params if isinstance(params, dict) else {}

                if (
                    first_final_answer_delta_at is None
                    and method == "item/agentMessage/delta"
                    and isinstance(payload, dict)
                ):
                    item_id = str(payload.get("item_id") or payload.get("itemId") or "")
                    phase = self._message_phase_by_item_id.get(item_id, "final_answer")
                    delta = payload.get("delta")
                    if phase == "final_answer" and isinstance(delta, str) and delta:
                        first_final_answer_delta_at = time.monotonic()

                if method == "turn/completed":
                    turn_payload = payload.get("turn")
                    completed_turn = turn_payload if isinstance(turn_payload, dict) else payload
                    break

                if self._handle_notification(
                    method=method,
                    payload=payload,
                    turn_index=turn_index,
                    stream=stream,
                    session_state=session_state,
                ):
                    final_answer_completed_at = time.monotonic()

            final_texts = [
                "".join(chunks).strip()
                for item_id, chunks in self._final_message_chunks.items()
                if self._message_phase_by_item_id.get(item_id) == "final_answer"
            ]
            final_texts = [text for text in final_texts if text]
            if not final_texts:
                final_texts = self._extract_completed_final_messages(completed_turn)
            if not final_texts and completed_turn and completed_turn.get("error"):
                raise ProviderRuntimeError(
                    "Codex app-server turn failed",
                    details={"turn": completed_turn},
                )

            out_messages = [
                ProviderMessage(
                    role="assistant",
                    content="\n\n".join(final_texts).strip() or None,
                    tool_calls=[],
                    finish_reason="stop",
                    index=0,
                )
            ] if final_texts else []

            healthy_client = True
            return ProviderResult(
                messages=out_messages,
                raw_response={
                    "provider": "codex",
                    "thread_id": self._thread_id,
                    "turn": completed_turn,
                },
                usage=None,
                encrypted_reasoning=None,
                reasoning_summaries=None,
                model=model,
                metadata={
                    "provider_turn_completed": True,
                    "provider_turn_completion_method": "codex_app_server",
                    "provider_turn_completion_reason": str((completed_turn or {}).get("completion_method") or "codex_turn_completed"),
                    "provider_runtime_timing": {
                        "invoke_total_seconds": round(time.monotonic() - invoke_started_at, 6),
                        "client_ready_seconds": round(invoke_after_client_at - invoke_started_at, 6),
                        "turn_start_seconds": round(invoke_after_turn_start_at - invoke_after_client_at, 6),
                        "post_turn_wait_seconds": round(time.monotonic() - invoke_after_turn_start_at, 6),
                        "first_notification_seconds": round(first_notification_at - invoke_started_at, 6)
                        if first_notification_at is not None
                        else None,
                        "first_final_answer_delta_seconds": round(first_final_answer_delta_at - invoke_started_at, 6)
                        if first_final_answer_delta_at is not None
                        else None,
                        "notification_count": notification_count,
                        **self._last_client_setup_timing,
                    },
                },
            )
        finally:
            self._release_leased_client(healthy=healthy_client)

    def _ensure_client(self, *, model: str, cwd: str) -> _CodexJsonRpcClient:
        codex_bin = self._resolve_codex_bin()
        env = os.environ.copy()
        if self._client is not None and self._thread_id and self._session_model == model and self._session_cwd == cwd:
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
        thread_result = client.thread_start({"model": model, "cwd": cwd})
        after_thread_start_at = time.monotonic()
        thread = thread_result.get("thread") if isinstance(thread_result, dict) else {}
        thread_id = str((thread or {}).get("id") or "")
        if not thread_id:
            client.close()
            raise ProviderRuntimeError("Codex app-server thread/start did not return a thread id")
        self._client = client
        self._thread_id = thread_id
        self._session_model = model
        self._session_cwd = cwd
        self._leased_client_key = (codex_bin, cwd, model)
        self._leased_client_env = dict(env)
        self._last_client_setup_timing = {
            "client_cache_hit": cache_hit,
            "client_spawn_seconds": round((after_start_at - spawn_started_at), 6) if not cache_hit else 0.0,
            "client_initialize_seconds": round(after_initialize_at - after_start_at, 6) if not cache_hit else 0.0,
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

    def _extract_latest_user_text(self, messages: List[Dict[str, Any]]) -> str:
        for message in reversed(messages or []):
            if str(message.get("role") or "") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                parts: List[str] = []
                for block in content:
                    if isinstance(block, dict):
                        text = block.get("text")
                        if isinstance(text, str) and text.strip():
                            parts.append(text.strip())
                if parts:
                    return "\n\n".join(parts).strip()
        return ""

    def _handle_notification(
        self,
        *,
        method: str,
        payload: Dict[str, Any],
        turn_index: Optional[int],
        stream: bool,
        session_state: Any,
    ) -> bool:
        if session_state is None:
            return False

        if method == "item/started":
            item = payload.get("item")
            if not isinstance(item, dict):
                return False
            item_id = str(item.get("id") or "")
            item_type = str(item.get("type") or "")
            if item_type == "agentMessage" and item_id:
                phase = str(item.get("phase") or "final_answer")
                self._message_phase_by_item_id[item_id] = phase
                self._final_message_chunks.setdefault(item_id, [])
            elif item_type == "commandExecution":
                self._emit_tool_exec_start(session_state, item, turn_index=turn_index)
            return False

        if method == "item/completed":
            item = payload.get("item")
            if not isinstance(item, dict):
                return False
            item_id = str(item.get("id") or "")
            item_type = str(item.get("type") or "")
            if item_type == "agentMessage" and item_id:
                phase = str(item.get("phase") or self._message_phase_by_item_id.get(item_id) or "final_answer")
                self._message_phase_by_item_id[item_id] = phase
                text = item.get("text")
                if isinstance(text, str) and text:
                    self._final_message_chunks[item_id] = [text]
                return phase == "final_answer"
            elif item_type == "commandExecution":
                self._emit_tool_exec_end(session_state, item, turn_index=turn_index)
            return False

        if method == "item/agentMessage/delta":
            item_id = str(payload.get("item_id") or payload.get("itemId") or "")
            delta = payload.get("delta")
            if not item_id or not isinstance(delta, str) or not delta:
                return False
            phase = self._message_phase_by_item_id.get(item_id, "final_answer")
            self._final_message_chunks.setdefault(item_id, []).append(delta)
            if not stream:
                return False
            event_type = "assistant.message.delta" if phase == "final_answer" else "assistant.thought_summary.delta"
            session_state._emit_event(event_type, {"delta": delta, "item_id": item_id}, turn=turn_index)
            return False

        if method in {"item/reasoning/textDelta", "item/reasoningSummary/textDelta"}:
            delta = payload.get("delta")
            if not isinstance(delta, str) or not delta or not stream:
                return False
            event_type = "assistant.reasoning.delta" if method == "item/reasoning/textDelta" else "assistant.thought_summary.delta"
            session_state._emit_event(event_type, {"delta": delta}, turn=turn_index)
            return False

        if method in {"command/exec/outputDelta", "item/commandExecution/outputDelta"}:
            self._emit_tool_exec_output(session_state, payload, turn_index=turn_index)
            return False

        return False

    def _has_final_answer_text(self) -> bool:
        for item_id, chunks in self._final_message_chunks.items():
            if self._message_phase_by_item_id.get(item_id) != "final_answer":
                continue
            if "".join(chunks).strip():
                return True
        return False

    def _synthesized_completed_items(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for item_id, chunks in self._final_message_chunks.items():
            phase = self._message_phase_by_item_id.get(item_id) or "final_answer"
            text = "".join(chunks).strip()
            if not text:
                continue
            items.append({"id": item_id, "type": "agentMessage", "phase": phase, "text": text})
        return items

    def _emit_tool_exec_start(self, session_state: Any, item: Dict[str, Any], *, turn_index: Optional[int]) -> None:
        call_id = str(item.get("id") or "")
        command = str(item.get("command") or "running")
        process_id = item.get("process_id") or item.get("processId")
        payload = {
            "call_id": call_id,
            "exec_id": str(process_id) if process_id else call_id,
            "tool": "shell_command",
            "tool_name": "shell_command",
            "command": command,
        }
        self._tool_output_buffers[str(payload["exec_id"])] = {"stdout": "", "stderr": ""}
        session_state._emit_event("tool.exec.start", payload, turn=turn_index)

    def _emit_tool_exec_output(self, session_state: Any, payload: Dict[str, Any], *, turn_index: Optional[int]) -> None:
        stream_name = str(payload.get("stream") or "")
        encoded = payload.get("deltaBase64") or payload.get("delta_base64")
        if not isinstance(encoded, str) or not encoded:
            return
        try:
            chunk = base64.b64decode(encoded).decode("utf-8", "ignore")
        except Exception:
            return
        exec_id = str(payload.get("processId") or payload.get("process_id") or payload.get("call_id") or "")
        if not exec_id or not chunk:
            return
        if exec_id not in self._tool_output_buffers:
            self._tool_output_buffers[exec_id] = {"stdout": "", "stderr": ""}
        if stream_name == "stderr":
            self._tool_output_buffers[exec_id]["stderr"] += chunk
            session_state._emit_event("tool.exec.stderr.delta", {"exec_id": exec_id, "delta": chunk}, turn=turn_index)
        else:
            self._tool_output_buffers[exec_id]["stdout"] += chunk
            session_state._emit_event("tool.exec.stdout.delta", {"exec_id": exec_id, "delta": chunk}, turn=turn_index)

    def _emit_tool_exec_end(self, session_state: Any, item: Dict[str, Any], *, turn_index: Optional[int]) -> None:
        call_id = str(item.get("id") or "")
        process_id = item.get("process_id") or item.get("processId")
        exec_id = str(process_id) if process_id else call_id
        aggregated = item.get("aggregated_output") or item.get("aggregatedOutput")
        if isinstance(aggregated, str) and aggregated:
            existing = self._tool_output_buffers.setdefault(exec_id, {"stdout": "", "stderr": ""})
            if not existing["stdout"] and not existing["stderr"]:
                existing["stdout"] = aggregated
                session_state._emit_event(
                    "tool.exec.stdout.delta",
                    {"call_id": call_id, "exec_id": exec_id, "delta": aggregated},
                    turn=turn_index,
                )
        exit_code = item.get("exit_code") if item.get("exit_code") is not None else item.get("exitCode")
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
                success=(exit_code == 0 or exit_code is None),
                metadata={
                    "is_run_shell": True,
                    "exit_code": exit_code,
                    "call_id": call_id,
                },
            )
        except Exception:
            pass
        self._tool_output_buffers.pop(exec_id, None)

    def _extract_completed_final_messages(self, completed_turn: Optional[Dict[str, Any]]) -> List[str]:
        items = completed_turn.get("items") if isinstance(completed_turn, dict) else None
        if not isinstance(items, list):
            return []
        texts: List[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "") != "agentMessage":
                continue
            if str(item.get("phase") or "") != "final_answer":
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
        return texts


provider_registry.register_runtime("codex_app_server", CodexAppServerRuntime)
