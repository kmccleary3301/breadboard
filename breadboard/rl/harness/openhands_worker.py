"""Pinned OpenHands SDK 1.47.0 native phase actor.

This module deliberately has no OpenHands imports at module import time.  The
launcher imports it before entering the worker namespace; :func:`factory` is
called by ``native_worker.serve`` after the namespace and ownership bootstrap.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "bb.openhands-native.v1"
MODEL_RESPONSE_LIMIT = 4 * 1024 * 1024
TRANSCRIPT_LIMIT = 32 * 1024 * 1024
FRAME_LIMIT = 16 * 1024 * 1024
RAW_TERMINAL_LIMIT = 1 * 1024 * 1024
NATIVE_IDLE_TIMEOUT = 30


def load_native_config(source: str | Path | Mapping[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(source, Mapping):
        import copy
        return copy.deepcopy(dict(source))
    if source is not None:
        return json.loads(Path(source).read_text(encoding="utf-8"))
    env_path = os.environ.get("OPENHANDS_NATIVE_CONFIG_PATH")
    if env_path and Path(env_path).is_file():
        return json.loads(Path(env_path).read_text(encoding="utf-8"))
    fallback = Path(__file__).resolve().parents[3] / "config/e4_targets/openhands_sdk/1.47.0/native-config.json"
    if fallback.is_file():
        return json.loads(fallback.read_text(encoding="utf-8"))
    raise FileNotFoundError("Could not find openhands native-config.json")

class NativeWorkerError(RuntimeError):
    """An invalid phase request or a native source failure."""


class _IPCTransport:
    """httpx transport which turns one SDK request into one parent exchange."""

    def __init__(self, channel: Any, credential: str, request_result: Callable[[Any], Mapping[str, Any]]):
        import httpx

        self._channel = channel
        self._credential = credential
        self._request_result = request_result
        self._httpx = httpx

    def handle_request(self, request: Any) -> Any:
        method = request.method.decode() if isinstance(request.method, bytes) else str(request.method)
        if method.upper() != "POST":
            raise self._httpx.UnsupportedProtocol("native OpenHands transport accepts POST only")
        auth = request.headers.get("authorization")
        if auth != f"Bearer {self._credential}":
            raise self._httpx.LocalProtocolError("native transport credential rejected")
        body = request.content
        if len(body) > FRAME_LIMIT:
            raise self._httpx.RequestError("native OpenHands request exceeds frame limit", request=request)
        headers: list[list[str]] = []
        content_length_seen = False
        for name, value in request.headers.raw:
            decoded_name = name.decode("latin-1")
            decoded_value = value.decode("latin-1")
            header_kind = decoded_name.casefold()
            if header_kind == "transfer-encoding":
                continue
            if header_kind == "content-length":
                if content_length_seen:
                    continue
                decoded_value = str(len(body))
                content_length_seen = True
            headers.append([decoded_name, decoded_value])
        if not content_length_seen:
            headers.append(["Content-Length", str(len(body))])
        result = dict(self._request_result(request))
        result["http_request"] = {
            "method": method,
            "url": str(request.url),
            # Authorization is intentionally omitted from public evidence.
            "headers": [[name, value] for name, value in headers if name.lower() != "authorization"],
            "body_b64": base64.b64encode(body).decode("ascii"),
        }
        self._channel.respond(result)
        command = self._channel.receive()
        if command is None:
            raise self._httpx.ReadError("provider response channel closed", request=request)
        if command.get("operation") != "provider_response":
            raise self._httpx.LocalProtocolError("expected provider_response command")
        payload = command.get("payload")
        if not isinstance(payload, Mapping):
            raise self._httpx.LocalProtocolError("provider response payload is not an object")
        error = payload.get("error")
        if isinstance(error, Mapping):
            kind = str(error.get("type", "RequestError"))
            message = str(error.get("message", "provider transport failed"))[:4096]
            exc_type = getattr(self._httpx, kind, self._httpx.RequestError)
            if not isinstance(exc_type, type) or not issubclass(exc_type, self._httpx.RequestError):
                exc_type = self._httpx.RequestError
            raise exc_type(message, request=request)
        try:
            status_code = payload["status_code"]
            response_headers = payload.get("headers", [])
            response_body = base64.b64decode(payload["body_b64"], validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise self._httpx.LocalProtocolError("provider response is malformed") from exc
        if type(status_code) is not int or not 100 <= status_code <= 599:
            raise self._httpx.LocalProtocolError("provider response status is invalid")
        if len(response_body) > MODEL_RESPONSE_LIMIT:
            raise self._httpx.LocalProtocolError("provider response exceeds 4 MiB")
        try:
            pairs = [(str(item[0]), str(item[1])) for item in response_headers]
        except (IndexError, TypeError, ValueError) as exc:
            raise self._httpx.LocalProtocolError("provider response headers are malformed") from exc
        return self._httpx.Response(status_code, headers=pairs, content=response_body, request=request)

    def close(self) -> None:
        return None


class OpenHandsActor:
    """One lease-owned five-tool OpenHands conversation."""

    def __init__(self, channel: Any):
        self._channel = channel
        self._sdk: dict[str, Any] = {}
        self._conversation: Any = None
        self._agent: Any = None
        self._llm: Any = None
        self._client: Any = None
        self._transport: Any = None
        self._credential: str | None = None
        self._events: list[Any] = []
        self._transcript_bytes = 0
        self._iteration = 0
        self._prepared: list[Any] = []
        self._prepared_cursor = 0
        self._observations: dict[int, list[Any]] = {}
        self._sample_error: dict[str, Any] | None = None
        self._native_response: Any = None
        self._native_error: BaseException | None = None
        self._closed = False

    def _capture_event(self, event: Any) -> None:
        self._events.append(event)

    def _begin(self) -> None:
        self._events = []
        self._sample_error = None
    def _error_dump(self, error: BaseException) -> dict[str, Any]:
        value: dict[str, Any] = {
            "type": type(error).__name__,
            "message": self._bounded_message(error),
        }
        response = getattr(error, "response", None)
        if response is not None:
            try:
                body = bytes(response.content)
                if len(body) <= MODEL_RESPONSE_LIMIT:
                    value["response"] = {
                        "status_code": response.status_code,
                        "body_b64": base64.b64encode(body).decode("ascii"),
                    }
            except Exception:
                pass
        return value


    @staticmethod
    def _bounded_message(value: Any, limit: int = 4096) -> str:
        return str(value)[:limit]

    def _event_dump(self, event: Any) -> dict[str, Any]:
        value = event.model_dump(mode="json")
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > FRAME_LIMIT:
            raise NativeWorkerError("native event exceeds frame limit")
        self._transcript_bytes += len(encoded)
        if self._transcript_bytes > TRANSCRIPT_LIMIT:
            raise NativeWorkerError("native transcript exceeds 32 MiB")
        return value

    def _event_delta(self) -> list[dict[str, Any]]:
        return [self._event_dump(event) for event in self._events]

    def _status(self) -> str:
        if self._conversation is None:
            raise NativeWorkerError("worker is not initialized")
        return self._conversation.state.execution_status.name


    def _request_result(self, _request: Any) -> Mapping[str, Any]:
        events = self._event_delta()
        self._events = []
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "provider_request",
            "event_delta": events,
        }

    def _response_dump(self, response: Any) -> dict[str, Any]:
        value = response.model_dump(mode="json")
        if len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MODEL_RESPONSE_LIMIT:
            raise NativeWorkerError("native model response exceeds 4 MiB")
        return value

    def _take_events(self) -> list[dict[str, Any]]:
        events = self._event_delta()
        self._events = []
        return events

    @staticmethod
    def _require_str(payload: Mapping[str, Any], key: str) -> str:
        value = payload.get(key)
        if type(value) is not str or not value:
            raise NativeWorkerError(f"{key} must be a non-empty string")
        return value

    def _initialize(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if self._conversation is not None:
            raise NativeWorkerError("worker is already initialized")
        task = self._require_str(payload, "task")
        model_config = payload.get("model_config")
        if not isinstance(model_config, Mapping) or set(model_config) != {
            "model_name", "model_canonical_name", "max_input_tokens", "base_url",
        }:
            raise NativeWorkerError("model_config requires the admitted source-model fields")
        if model_config["model_canonical_name"] is not None:
            raise NativeWorkerError("the source profile does not admit a canonical-model override")
        max_input_tokens = model_config["max_input_tokens"]
        if type(max_input_tokens) is not int or max_input_tokens < 1:
            raise NativeWorkerError("max_input_tokens must be a positive integer")
        max_iteration_per_run = payload.get("max_iteration_per_run")
        if type(max_iteration_per_run) is not int or max_iteration_per_run < 1:
            raise NativeWorkerError("max_iteration_per_run must be a positive integer")
        self._max_iteration_per_run = max_iteration_per_run
        workspace = self._require_str(payload, "workspace")
        scratch = self._require_str(payload, "scratch")
        if not os.path.isabs(workspace) or not os.path.isabs(scratch):
            raise NativeWorkerError("workspace and scratch must be absolute")
        if any(key in payload for key in ("workspace_path", "scratch_path", "workspace_root", "tmpdir")):
            raise NativeWorkerError("caller-supplied path authority is forbidden")
        Path(workspace).mkdir(parents=True, exist_ok=True)
        Path(scratch).mkdir(parents=True, exist_ok=True)
        self._sdk["workspace"] = workspace
        home = Path(scratch) / "home"
        config = Path(scratch) / "config"
        home.mkdir(parents=True, exist_ok=True)
        config.mkdir(parents=True, exist_ok=True)
        os.environ["HOME"] = str(home)
        os.environ["XDG_CONFIG_HOME"] = str(config)
        os.environ["OH_PERSISTENCE_DIR"] = str(Path(scratch) / "openhands")
        os.environ["TMPDIR"] = str(Path(scratch) / "tmp")
        Path(os.environ["OH_PERSISTENCE_DIR"]).mkdir(parents=True, exist_ok=True)
        Path(os.environ["TMPDIR"]).mkdir(parents=True, exist_ok=True)
        os.environ.pop("OH_RUNTIME_IDLE_TIMEOUT_SECONDS", None)

        # These imports intentionally occur after the namespace-owned roots and
        # environment are established.
        os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
        import httpx
        from openai import OpenAI
        from openhands.sdk import Agent, LLM, LocalConversation, Message, TextContent, Tool
        from openhands.sdk.agent.utils import prepare_llm_messages
        from openhands.sdk.conversation import ConversationExecutionStatus
        from openhands.sdk.event import MessageEvent
        from openhands.sdk.event.conversation_error import ConversationErrorEvent
        from openhands.sdk.llm.utils.runtime_metadata import detect_provider
        from openhands.tools import FileEditorTool, TaskTrackerTool, TerminalTool
        from openhands.sdk.tool import register_tool
        import openhands.tools.terminal.terminal.subprocess_terminal as subprocess_module
        import openhands.tools.terminal.terminal.factory as terminal_factory
        terminal_factory._is_tmux_available = lambda: False

        source_subprocess_terminal = subprocess_module.SubprocessTerminal

        class CappedSubprocessTerminal(source_subprocess_terminal):
            """Source-derived PTY reader with a fatal per-action raw-byte cap."""

            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                self._bb_raw_bytes = 0

            def execute(self, action: Any) -> Any:
                self._bb_raw_bytes = 0
                return super().execute(action)

            def _read_output_continuously_pty(self) -> None:
                fd = self._pty_master_fd
                if fd is None:
                    return
                try:
                    while True:
                        if self.process and self.process.poll() is not None:
                            break
                        ready, _, _ = subprocess_module.select.select([fd], [], [], 0.1)
                        if not ready:
                            continue
                        try:
                            chunk = os.read(fd, 4096)
                            if not chunk:
                                break
                            self._bb_raw_bytes += len(chunk)
                            if self._bb_raw_bytes > RAW_TERMINAL_LIMIT:
                                try:
                                    os.write(2, b"bb-openhands: terminal raw output cap exceeded\n")
                                finally:
                                    os._exit(74)
                            text = chunk.decode("utf-8", errors="replace")
                            with self.output_lock:
                                self._add_text_to_buffer(text)
                        except OSError:
                            continue
                        except Exception:
                            break
                except Exception:
                    return

        # The factory imports this symbol lazily. Replacing only this symbol
        # leaves the genuine tmux probe and reset-selection logic untouched.
        subprocess_module.SubprocessTerminal = CappedSubprocessTerminal

        model = model_config["model_name"]
        base_url = model_config["base_url"]
        if type(model) is not str or not model.startswith("openai/") or type(base_url) is not str or not base_url:
            raise NativeWorkerError("model_config requires the admitted OpenAI-compatible route")
        shell_path = "/bin/bash"
        self._sdk.update(locals())
        self._credential = "bb-native-" + secrets.token_urlsafe(24)
        transport_type = type(
            "_OpenHandsIPCBaseTransport",
            (httpx.BaseTransport,),
            {
                "__init__": _IPCTransport.__init__,
                "handle_request": _IPCTransport.handle_request,
                "close": _IPCTransport.close,
            },
        )
        import copy
        native_config_source = (
            payload.get("native_config")
            or payload.get("native_config_path")
        )
        native_config = load_native_config(native_config_source)
        model_profile = copy.deepcopy(native_config.get("model", {}))
        timeout = model_profile.get("timeout", 45)
        num_retries = model_profile.get("num_retries", 0)
        self._transport = transport_type(self._channel, self._credential, self._request_result)
        http_client = httpx.Client(transport=self._transport, timeout=timeout)
        self._client = OpenAI(
            api_key=self._credential,
            base_url=base_url,
            max_retries=num_retries,
            timeout=timeout,
            http_client=http_client,
        )
        llm_kwargs: dict[str, Any] = {
            **model_profile,
            "model": model,
            "api_key": self._credential,
            "base_url": base_url,
            "max_input_tokens": max_input_tokens,
        }
        self._llm = LLM(**llm_kwargs)
        if detect_provider(self._llm) is not None:
            raise NativeWorkerError("provider metadata discovery is outside the local-only profile")
        register_tool("TerminalTool", TerminalTool)
        register_tool("FileEditorTool", FileEditorTool)
        register_tool("TaskTrackerTool", TaskTrackerTool)
        tools = [
            Tool(name="TerminalTool", params={"terminal_type": "subprocess", "shell_path": shell_path, "no_change_timeout_seconds": NATIVE_IDLE_TIMEOUT}),
            Tool(name="FileEditorTool", params={}),
            Tool(name="TaskTrackerTool", params={}),
        ]
        self._agent = Agent(
            llm=self._llm,
            tools=tools,
            include_default_tools=["FinishTool", "ThinkTool"],
            system_prompt_kwargs={"cli_mode": True},
            agent_context=None,
            condenser=None,
            critic=None,
            mcp_config={},
            tool_concurrency_limit=1,
        )
        self._conversation = LocalConversation(
            agent=self._agent,
            workspace=workspace,
            persistence_dir=None,
            max_iteration_per_run=self._max_iteration_per_run,
            stuck_detection=True,
            max_budget_per_run=None,
            visualizer=None,
            plugins=None,
            callbacks=[self._capture_event],
        )
        self._conversation._ensure_agent_ready()
        self._conversation.send_message(task)
        schemas = [tool.to_openai_tool(add_security_risk_prediction=True) for tool in self._agent.tools_map.values()]
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "initialized",
            "event_delta": self._take_events(),
            "status": self._status(),
            "iteration": self._iteration,
            "tool_schemas": [schema.model_dump(mode="json") if hasattr(schema, "model_dump") else schema for schema in schemas],
            "source_runtime": {
                "sdk": "openhands-sdk@1.47.0",
                "tools": "1.47.0",
                "terminal_type": "subprocess",
                "native_idle_timeout_seconds": NATIVE_IDLE_TIMEOUT,
                "raw_terminal_output_limit": RAW_TERMINAL_LIMIT,
                "raw_terminal_cap_exit_code": 74,
            },
        }

    def _sample(self, _payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if self._conversation is None:
            raise NativeWorkerError("worker is not initialized")
        if self._prepared:
            raise NativeWorkerError("previous sample has uncommitted actions")
        self._begin()
        state = self._conversation.state
        if state.last_user_message_id is not None:
            blocked_reason = state.pop_blocked_message(state.last_user_message_id)
            if blocked_reason is not None:
                state.execution_status = self._sdk["ConversationExecutionStatus"].FINISHED
                return {"schema_version": SCHEMA_VERSION, "kind": "sample_ready", "event_delta": self._take_events(), "status": self._status(), "iteration": self._iteration}
        if self._status() in {"FINISHED", "STUCK", "ERROR"} or self._iteration >= self._max_iteration_per_run:
            return {"schema_version": SCHEMA_VERSION, "kind": "sample_ready", "event_delta": [], "status": self._status(), "iteration": self._iteration}
        if self._status() in {"IDLE", "PAUSED"}:
            state.execution_status = self._sdk["ConversationExecutionStatus"].RUNNING
        if self._conversation._check_stuck_or_nudge():
            return {"schema_version": SCHEMA_VERSION, "kind": "sample_ready", "event_delta": self._take_events(), "status": self._status(), "iteration": self._iteration}
        try:
            call_context = self._conversation.get_llm_call_context()
            self._llm.resolve_runtime_metadata()
            messages = self._sdk["prepare_llm_messages"](state.view, condenser=None, llm=self._llm)
            self._native_response = self._llm.completion(
                messages,
                tools=list(self._agent.tools_map.values()),
                add_security_risk_prediction=True,
                client=self._client,
                call_context=call_context,
                stream=False,
            )
        except Exception as exc:
            self._native_response = None
            self._native_error = exc
            self._iteration += 1
            self._sample_error = self._error_dump(exc)
            return {
                "schema_version": SCHEMA_VERSION,
                "kind": "sample_ready",
                "event_delta": self._take_events(),
                "status": self._status(),
                "iteration": self._iteration,
                "source_error": self._sample_error,
            }
        self._native_error = None
        self._iteration += 1
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "sample_ready",
            "event_delta": self._take_events(),
            "status": self._status(),
            "iteration": self._iteration,
            "source_response": self._response_dump(self._native_response),
        }

    def _prepare(self, _payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if self._conversation is None:
            raise NativeWorkerError("worker is not initialized")
        state = self._conversation.state
        if self._native_error is not None:
            error = self._native_error
            self._native_error = None
            error_type = type(error).__name__
            if error_type == "FunctionCallValidationError":
                self._conversation._on_event(
                    self._sdk["MessageEvent"](
                        source="user",
                        llm_message=self._sdk["Message"](
                            role="user",
                            content=[self._sdk["TextContent"](text=str(error))],
                        ),
                    )
                )
            elif error_type == "LLMContentPolicyViolationError":
                self._conversation._on_event(
                    self._sdk["MessageEvent"](
                        source="environment",
                        llm_message=self._sdk["Message"](
                            role="user",
                            content=[
                                self._sdk["TextContent"](
                                    text="Your previous response was blocked by the model's content filter. Please continue, rephrasing to avoid the flagged content."
                                )
                            ],
                        ),
                    )
                )
            else:
                state.execution_status = self._sdk["ConversationExecutionStatus"].ERROR
                self._conversation._on_event(self._sdk["ConversationErrorEvent"](
                    source="environment", code=error_type, detail=str(error),
                ))
                return {
                    "schema_version": SCHEMA_VERSION,
                    "kind": "prepared",
                    "event_delta": self._take_events(),
                    "status": self._status(),
                    "iteration": self._iteration,
                    "actions": [],
                    "source_error": self._sample_error,
                }
        response = self._native_response
        if response is None:
            return {"schema_version": SCHEMA_VERSION, "kind": "prepared", "event_delta": [], "status": self._status(), "iteration": self._iteration, "actions": []}
        self._native_response = None
        message = response.message
        from openhands.sdk.agent.response_dispatch import classify_response, LLMResponseType
        kind = classify_response(message)
        if kind is LLMResponseType.CONTENT:
            self._agent._emit_message_event(message, response, self._conversation, self._conversation._on_event)
            state.execution_status = self._sdk["ConversationExecutionStatus"].FINISHED
            return {"schema_version": SCHEMA_VERSION, "kind": "prepared", "event_delta": self._take_events(), "status": self._status(), "iteration": self._iteration, "actions": []}
        if kind is not LLMResponseType.TOOL_CALLS:
            self._agent._handle_no_content_response(message, response, self._conversation, state, self._conversation._on_event, response_type=kind)
            return {"schema_version": SCHEMA_VERSION, "kind": "prepared", "event_delta": self._take_events(), "status": self._status(), "iteration": self._iteration, "actions": []}
        actions: list[Any] = []
        for index, call in enumerate(message.tool_calls):
            action = self._agent._get_action_event(
                call,
                self._conversation,
                response.id,
                self._conversation._on_event,
                security_analyzer=state.security_analyzer,
                thought=[c for c in message.content if isinstance(c, self._sdk["TextContent"])] if index == 0 else [],
                reasoning_content=message.reasoning_content if index == 0 else None,
                thinking_blocks=list(message.thinking_blocks) if index == 0 else [],
                responses_reasoning_item=message.responses_reasoning_item if index == 0 else None,
            )
            if action is not None:
                actions.append(action)
        finish_index = next((i for i, action in enumerate(actions) if action.tool_name == "finish"), None)
        self._prepared = actions[: finish_index + 1] if finish_index is not None else actions
        self._prepared_cursor = 0
        def action_dump(action: Any, index: int) -> dict[str, Any]:
            risk = getattr(action, "security_risk", None)
            if hasattr(risk, "value"):
                risk = risk.value
            return {
                "index": index,
                "tool_id": action.tool_name,
                "call_id": action.tool_call_id,
                "arguments": json.loads(action.tool_call.arguments),
                "security_risk": risk if isinstance(risk, str) else "UNKNOWN",
            }
        all_output = [action_dump(action, index) for index, action in enumerate(actions)]
        prepared_output = all_output[: len(self._prepared)]
        return {"schema_version": SCHEMA_VERSION, "kind": "prepared", "event_delta": self._take_events(), "status": self._status(), "iteration": self._iteration, "actions": prepared_output, "prepared_actions": all_output}


    def _execute(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if not self._prepared:
            raise NativeWorkerError("no prepared action")
        index = payload.get("index")
        tool_id = payload.get("tool_id")
        if type(index) is not int or index != self._prepared_cursor or index < 0 or index >= len(self._prepared):
            raise NativeWorkerError("action index is not the next prepared action")
        action = self._prepared[index]
        if tool_id != action.tool_name:
            raise NativeWorkerError("tool identity does not match prepared action")
        events = self._agent._execute_action_event(self._conversation, action)
        self._observations[index] = events
        self._prepared_cursor += 1
        return {"schema_version": SCHEMA_VERSION, "kind": "executed", "event_delta": [], "status": self._status(), "iteration": self._iteration, "index": index, "tool_id": action.tool_name, "observations": [event.model_dump(mode="json") for event in events]}

    def _commit(self, _payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if self._prepared_cursor != len(self._prepared):
            raise NativeWorkerError("cannot commit before every prepared action executes")
        self._begin()
        for index in range(len(self._prepared)):
            for event in self._observations.get(index, []):
                self._conversation._on_event(event)
        has_finish = bool(self._prepared and self._prepared[-1].tool_name == "finish")
        if has_finish:
            should_continue, followup = self._agent._check_iterative_refinement(self._conversation, self._prepared[-1])
            if should_continue and followup:
                self._conversation._on_event(self._sdk["MessageEvent"](source="user", llm_message=self._sdk["Message"](role="user", content=[self._sdk["TextContent"](text=followup)])))
            else:
                self._conversation.state.execution_status = self._sdk["ConversationExecutionStatus"].FINISHED
        if self._iteration >= self._max_iteration_per_run and self._status() not in {"FINISHED", "ERROR", "STUCK"}:
            self._conversation.state.execution_status = self._sdk["ConversationExecutionStatus"].ERROR
            self._conversation._on_event(self._sdk["ConversationErrorEvent"](
                source="environment", code="MaxIterationsReached",
                detail=f"Agent reached maximum iterations limit ({self._max_iteration_per_run}).",
            ))
        result = {
            "schema_version": SCHEMA_VERSION,
            "kind": "committed",
            "event_delta": self._take_events(),
            "status": self._status(),
            "iteration": self._iteration,
        }
        self._prepared = []
        self._prepared_cursor = 0
        self._observations.clear()
        return result

    def dispatch(self, operation: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if self._closed:
            raise NativeWorkerError("worker is closed")
        if type(operation) is not str or not isinstance(payload, Mapping):
            raise NativeWorkerError("operation payload is invalid")
        if operation == "initialize":
            return self._initialize(payload)
        if operation == "sample":
            return self._sample(payload)
        if operation == "provider_response":
            raise NativeWorkerError("provider_response is consumed by the HTTP transport")
        if operation == "prepare":
            return self._prepare(payload)
        if operation == "execute":
            return self._execute(payload)
        if operation == "commit":
            return self._commit(payload)
        raise NativeWorkerError(f"unsupported native operation: {operation}")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._conversation is not None:
            try:
                self._conversation.close()
            except Exception:
                pass
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
        if self._transport is not None:
            self._transport.close()


def factory(channel: Any) -> OpenHandsActor:
    """Factory passed to ``native_worker.serve`` after namespace bootstrap."""
    return OpenHandsActor(channel)


__all__ = ["OpenHandsActor", "NativeWorkerError", "factory"]


if __name__ == "__main__":
    # Interpreter bootstrap paths must not contaminate source tool subprocesses.
    for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONNOUSERSITE", "LD_LIBRARY_PATH"):
        os.environ.pop(name, None)
    from native_worker import serve

    serve(factory)
