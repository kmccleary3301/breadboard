"""Pinned Hermes phase actor with parent-owned HTTP and history acknowledgements.

The carrier binds supplier descriptors but has no conversation entrypoint. Each
request and tool round is driven through the pinned supplier's phase helpers.
"""

from __future__ import annotations

import base64
import concurrent.futures
import contextvars
import dataclasses
import hashlib
import importlib
import inspect
import json
import math
import os
import queue
import signal
import site
import sys
import threading
import time
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "bb.hermes-native.v1"
MAX_REQUESTS = 8
MAX_OUTPUT_TOKENS = 2048
EPISODE_SECONDS = 120.0
PROVIDER_SECONDS = 45.0
TERMINAL_SECONDS = 30
NATIVE_TOOL_SECONDS = 35.0
ACTION_SECONDS = 40.0
MAX_RAW_COMMAND_BYTES = 1 << 20
MAX_OBSERVATION_BYTES = 16 << 20
MAX_PROVIDER_BYTES = 4 << 20
MAX_TRANSCRIPT_BYTES = 32 << 20
FRAME_BYTES = 16 << 20
_SOURCE_COMMIT = "939e45c91d751fadd94dcd1b873ac3cb44846213"
_SOURCE_ID = "hermes-agent-" + _SOURCE_COMMIT
_TOOL_NAMES = (
    "patch",
    "read_file",
    "search_files",
    "skill_view",
    "skills_list",
    "terminal",
    "write_file",
)
_EXPECTED_FIXTURES = {
    "memories/MEMORY.md": "Project tests use unittest.\n",
    "memories/USER.md": "This is a synthetic evaluation user.\n",
    "skills/fixture-code-style/SKILL.md": "---\nname: fixture-code-style\ndescription: Use unittest and preserve public signatures.\n---\n# Fixture code style\n\nUse the repository's existing unittest tests. Preserve public function signatures.\nRead references/assertions.md for assertion guidance.\n",
    "skills/fixture-code-style/references/assertions.md": "# Assertions\n\nAssert observable results and error cases. Run python -m unittest -q after code changes.\n",
}
# TurnFacadeMixin contains the opaque conversation entrypoints and is excluded.
_NATIVE_MIXINS = (
    "ClientLifecycleMixin",
    "StreamDeliveryMixin",
    "StatusOutputMixin",
    "ApiRequestHooksMixin",
    "ApiErrorSummaryMixin",
    "InterruptControlMixin",
    "TurnExplainersMixin",
    "ActivityTrackingMixin",
    "RateLimitCreditsMixin",
    "SessionPersistenceMixin",
    "CompressionFacadeMixin",
    "VisionMessagePrepMixin",
    "ReasoningParamsMixin",
)
_NATIVE_DESCRIPTORS = (
    "base_url",
    "_TOOL_CALL_ARGUMENTS_CORRUPTION_MARKER",
    "_VALID_API_ROLES",
    "_get_session_db_for_recall",
    "_session_row_model_config",
    "_ensure_db_session",
    "_transition_context_engine_session",
    "reset_session_state",
    "_effective_lmstudio_context_length",
    "_lmstudio_load_was_unverified",
    "_ensure_lmstudio_runtime_loaded",
    "_disable_codex_reasoning_replay",
    "_is_provider_stream_parse_error",
    "_emit_auxiliary_failure",
    "_current_main_runtime",
    "_hostname_for",
    "_is_direct_openai_url",
    "_is_azure_openai_url",
    "_is_github_copilot_url",
    "_resolved_api_call_timeout",
    "_resolved_api_call_stale_timeout_base",
    "_compute_non_stream_stale_timeout",
    "_stale_timeout_is_explicit",
    "_codex_silent_hang_hint",
    "_is_openrouter_url",
    "_is_copilot_url",
    "_is_copilot_provider",
    "_is_codex_backend",
    "_model_requires_responses_api",
    "_provider_model_requires_responses_api",
    "_max_tokens_param",
    "_requested_output_cap_from_api_kwargs",
    "_has_content_after_think_block",
    "_has_natural_response_ending",
    "_is_ollama_glm_backend",
    "_should_treat_stop_as_truncated",
    "_stream_diag_init",
    "_stream_diag_capture_response",
    "_flatten_exception_chain",
    "_log_stream_retry",
    "_emit_stream_drop",
    "_check_compression_model_feasibility",
    "_replay_compression_warning",
    "_anthropic_prompt_cache_policy",
    "_direct_native_anthropic_tool_cache_capability",
    "_strip_think_blocks",
    "_looks_like_codex_intermediate_ack",
    "_extract_reasoning",
    "_spawn_background_review",
    "_spawn_background_review_now",
    "_maybe_requeue_preempted_review",
    "_summarize_background_review_actions",
    "_REVIEW_REQUEUE_MAX_ATTEMPTS",
    "_build_memory_write_metadata",
    "get_activity_summary",
    "shutdown_memory_provider",
    "commit_memory_session",
    "_sync_external_memory_for_turn",
    "release_clients",
    "close",
    "_close_active_children",
    "_drop_shared_client",
    "_close_request_clients",
    "_close_codex_session",
    "_trim_process_memory",
    "_finalize_owned_session_row",
    "_hydrate_todo_store",
    "_latest_todo_response",
    "_tool_response_matches_todo_call",
    "_assistant_has_todo_tool_call",
    "is_interrupted",
    "_get_tool_call_name_static",
    "_get_tool_call_id_static",
    "_is_thinking_only_assistant",
    "_content_has_real_payload",
    "_cap_delegate_task_calls",
    "_deduplicate_tool_calls",
    "_has_pending_fallback",
    "_set_tool_guardrail_halt",
    "_toolguard_controlled_halt_response",
    "_append_guardrail_observation",
    "_stall_guards_enabled",
    "_guardrail_block_result",
    "_apply_pending_steer_to_tool_results",
    "_build_system_prompt",
    "_sanitize_api_messages",
    "_drop_thinking_only_and_merge_users",
    "_uniquify_tool_call_ids",
    "_repair_tool_call",
    "_invalidate_system_prompt",
    "_deterministic_call_id",
    "_split_responses_tool_id",
    "_derive_responses_function_call_id",
    "_interruptible_api_call",
    "_interruptible_streaming_api_call",
    "_restore_primary_runtime",
    "_build_api_kwargs",
    "_invoke_tool",
    "_execute_tool_calls_concurrent",
    "_execute_tool_calls_sequential",
    "_wrap_verbose",
    "_conversation_root_id",
)


class HermesWorkerError(RuntimeError):
    """An invalid native protocol, authority, or installed closure."""


class _ProfileStop(BaseException):
    """Unwind supplier exception/retry handlers before an excluded operation."""


class _HistoryAbort(BaseException):
    """A failed durable acknowledgement must not become a native tool error."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value)).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise HermesWorkerError(message)


def _history_revision(
    old: list[bytes], messages: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, list[bytes]]:
    rows = [_canonical_json(row) for row in messages]
    prefix = 0
    while prefix < min(len(old), len(rows)) and old[prefix] == rows[prefix]:
        prefix += 1
    if prefix == len(old) == len(rows):
        return None, rows
    return {
        "kind": "history_revision",
        "start": prefix,
        "delete_count": len(old) - prefix,
        "insert": [json.loads(row) for row in rows[prefix:]],
        "before_digest": _digest([json.loads(row) for row in old]),
        "after_digest": _digest(messages),
    }, rows


@dataclasses.dataclass
class _ParentExchange:
    operation: str
    value: Any
    result: concurrent.futures.Future = dataclasses.field(
        default_factory=concurrent.futures.Future
    )


def _carrier_type(actor: "HermesActor", source: Any) -> type:
    descriptors = {
        name: inspect.getattr_static(source.AIAgent, name)
        for name in _NATIVE_DESCRIPTORS
    }
    native_create = source.ClientLifecycleMixin._create_openai_client
    native_flush = source.SessionPersistenceMixin._flush_messages_to_session_db
    native_cleanup = source.AIAgent._cleanup_task_resources

    def http_client(self: Any, base_url: str = "", *, verify: Any = True) -> Any:
        httpx = actor._source_import("httpx")

        class ParentTransport(httpx.BaseTransport):
            def handle_request(self, request: Any) -> Any:
                return actor._exchange("http", request)

        return httpx.Client(
            transport=ParentTransport(),
            timeout=PROVIDER_SECONDS,
            follow_redirects=False,
            trust_env=False,
        )

    def create_client(self: Any, *args: Any, **kwargs: Any) -> Any:
        client = native_create(self, *args, **kwargs)
        _require(client.max_retries == 0, "supplier SDK client enabled retries")
        actor._sdk_clients.append(
            {
                "class": type(client).__module__ + "." + type(client).__qualname__,
                "max_retries": client.max_retries,
                "reason": kwargs.get("reason"),
                "shared_argument": kwargs.get("shared"),
                "base_url": str(client.base_url),
            }
        )
        return client

    def flush(
        self: Any, messages: list[dict[str, Any]], *args: Any, **kwargs: Any
    ) -> Any:
        result = native_flush(self, messages, *args, **kwargs)
        actor._source_flushes += 1
        if result is not False:
            actor._exchange("history", messages)
        return result

    def cleanup(self: Any, task_id: Any) -> Any:
        try:
            actor._capture_resources()
        finally:
            return_value = native_cleanup(self, task_id)
            actor._native_cleanup_returned = True
        return return_value

    def execute(
        self: Any,
        assistant_message: Any,
        messages: list,
        effective_task_id: str,
        api_call_count: int = 0,
    ) -> None:
        actor._execute_native_batch(
            assistant_message, messages, effective_task_id, api_call_count
        )

    def excluded(name: str) -> Any:
        def stop(self: Any, *args: Any, **kwargs: Any) -> Any:
            raise _ProfileStop(name)

        return stop

    descriptors.update(
        {
            "_build_keepalive_http_client": http_client,
            "_create_openai_client": create_client,
            "_flush_messages_to_session_db": flush,
            "_cleanup_task_resources": cleanup,
            "_execute_tool_calls": execute,
            "_handle_max_iterations": excluded("summary_or_grace_request"),
            "_try_activate_fallback": excluded("provider_fallback"),
            "_try_recover_primary_transport": excluded("transport_recovery"),
            "_recover_with_credential_pool": excluded("credential_rotation"),
            "_compress_context": excluded("context_compression"),
        }
    )
    carrier = type(
        "HermesSourceCarrier",
        tuple(getattr(source, name) for name in _NATIVE_MIXINS),
        descriptors,
    )
    _require(
        not issubclass(carrier, source.AIAgent)
        and not hasattr(carrier, "run_conversation"),
        "opaque supplier loop entered carrier",
    )
    return carrier


class HermesActor:
    def __init__(self, channel: Any) -> None:
        self._channel = channel
        self._control_thread = threading.get_ident()
        self._exchanges: queue.Queue[_ParentExchange] = queue.Queue()
        self._closed = False
        self._initialized = False
        self._state: Any = None
        self._loop: Any = None
        self._loop_state: Any = None
        self._runtime: Any = None
        self._native_result: dict[str, Any] | None = None
        self._bootstrap_messages: list[dict[str, Any]] = []
        self._history_rows: list[bytes] = []
        self._history_digest = _digest([])
        self._source_modules: dict[str, Any] = {}
        self._source_runtime: dict[str, Any] = {}
        self._sdk_clients: list[dict[str, Any]] = []
        self._source_flushes = 0
        self._deadline = time.monotonic() + EPISODE_SECONDS
        self._status = "RUNNING"
        self._stop_reason: str | None = None
        self._last_source_phase: str | None = None
        self._phase = "new"
        self._requests = 0
        self._raw_provider_response = b""
        self._tool_schemas: list[dict[str, Any]] = []
        self._bridge_completed = False
        self._segment_index: int | None = None
        self._segment_expected: list[dict[str, Any]] = []
        self._segment_flushed = 0
        self._resource_facts: dict[str, Any] | None = None
        self._native_cleanup_returned = False
        self._workspace_before: dict[str, str] = {}
        self._source_error: dict[str, str] | None = None

    @property
    def _messages(self) -> list[dict[str, Any]]:
        return (
            self._loop_state.messages
            if self._loop_state is not None
            else self._bootstrap_messages
        )

    def _remaining(self) -> float:
        return max(0.0, self._deadline - time.monotonic())
    def _workspace_snapshot(self) -> dict[str, str]:
        snapshot: dict[str, str] = {}
        for path in self._workspace.rglob("*"):
            if path.is_file() and not path.is_symlink():
                try:
                    relative = path.relative_to(self._workspace).as_posix()
                    snapshot[relative] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
                except (OSError, ValueError):
                    continue
        return snapshot

    def _workspace_effects(self) -> dict[str, str | None]:
        current = self._workspace_snapshot()
        effects: dict[str, str | None] = {}
        for path in sorted(set(self._workspace_before) | set(current)):
            before, after = self._workspace_before.get(path), current.get(path)
            if before != after:
                effects[path] = after
        return effects

    def _admit_deadline(self, payload: Mapping[str, Any]) -> None:
        remaining = payload["remaining_seconds"]
        _require(
            type(remaining) in (int, float)
            and math.isfinite(remaining)
            and 0 < remaining <= EPISODE_SECONDS,
            "invalid remaining episode time",
        )
        self._deadline = min(self._deadline, time.monotonic() + remaining)

    @staticmethod
    def _keys(payload: Mapping[str, Any], keys: set[str]) -> None:
        _require(set(payload) == keys, "native payload fields differ")

    def _source_import(self, name: str) -> Any:
        if name not in self._source_modules:
            module = importlib.import_module(name)
            origin = Path(module.__file__).resolve(strict=True)
            _require(
                origin.is_relative_to(self._source_root)
                or origin.is_relative_to(self._site_packages),
                f"module outside pinned source/SDK: {name}: {origin}",
            )
            self._source_modules[name] = module
        return self._source_modules[name]

    def _materialize_environment(
        self, workspace: str, scratch: str, model: Mapping[str, Any]
    ) -> None:
        native_root = Path(__file__).resolve().parent
        config = json.loads((native_root / "hermes-native-config.json").read_bytes())
        _require(
            config["schema_version"] == "bb.hermes.native-runtime.v1"
            and config["source_commit"] == _SOURCE_COMMIT,
            "native runtime identity differs",
        )
        _require(Path(config["native_root"]) == native_root, "native root differs")
        self._source_root = Path(config["source_root"]).resolve(strict=True)
        self._site_packages = Path(config["site_packages"]).resolve(strict=True)
        fixture_root = Path(config["fixtures_root"]).resolve(strict=True)
        _require(
            self._source_root == native_root / "source" / _SOURCE_ID,
            "source path differs",
        )
        _require(
            self._site_packages.is_relative_to(native_root / "sdk")
            and fixture_root == native_root / "hermes-fixtures",
            "SDK/fixture root differs",
        )
        _require(
            Path(sys.executable).resolve()
            == Path(config["python_executable"]).resolve(),
            "noncanonical interpreter",
        )
        _require(
            type(workspace) is str
            and type(scratch) is str
            and Path(workspace).is_absolute()
            and Path(scratch).is_absolute(),
            "workspace/scratch must be absolute",
        )
        self._workspace = Path(workspace).resolve(strict=True)
        self._workspace_before = self._workspace_snapshot()
        self._scratch = Path(scratch).resolve(strict=True)
        _require(
            self._workspace != self._scratch
            and self._workspace.parent == self._scratch.parent,
            "workspace/scratch must be separate siblings",
        )
        self._home = self._scratch / "hermes-home"
        self._home.mkdir(mode=0o700)
        for directory in ("tmp", "cache", "config"):
            (self._scratch / directory).mkdir(mode=0o700, exist_ok=True)
        fixture_digests = {}
        for relative, expected in _EXPECTED_FIXTURES.items():
            raw = (fixture_root / relative).read_bytes()
            _require(raw == expected.encode("utf-8"), f"fixture differs: {relative}")
            target = self._home / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(raw)
            fixture_digests[relative] = hashlib.sha256(raw).hexdigest()
        profile_config = {
            "model": {"streaming": False, "context_length": model["max_input_tokens"]},
            "agent": {
                "api_max_retries": 1,
                "environment_probe": False,
                "bot_mode_protocol": False,
                "verify_on_stop": False,
                "task_completion_guidance": True,
                "parallel_tool_call_guidance": True,
                "stall_guards": True,
            },
            "terminal": {
                "backend": "local",
                "cwd": str(self._workspace),
                "timeout": TERMINAL_SECONDS,
            },
            "memory": {
                "memory_enabled": True,
                "user_profile_enabled": True,
                "memory_char_limit": 2200,
                "user_char_limit": 1375,
                "provider": "",
            },
            "compression": {"enabled": False, "micro_compact": False},
            "approvals": {
                "mode": "manual",
                "single_query_mode": "deny",
                "unattended_mode": "deny",
            },
            "timeouts": {
                "api": {"call": PROVIDER_SECONDS},
                "tools": {
                    "call": NATIVE_TOOL_SECONDS,
                    "concurrent_batch": NATIVE_TOOL_SECONDS,
                },
            },
            "lsp": {"enabled": False},
        }
        with (self._home / "config.yaml").open("xb") as stream:
            stream.write(_canonical_json(profile_config))
        os.environ.update(
            {
                "HOME": str(self._home),
                "HERMES_HOME": str(self._home),
                "TMPDIR": str(self._scratch / "tmp"),
                "XDG_CONFIG_HOME": str(self._scratch / "config"),
                "TERMINAL_CWD": str(self._workspace),
            }
        )
        boundary = importlib.import_module("hermes_tool_exec")
        facts = boundary.configure_shell_boundary(
            self._workspace, self._scratch, self._home
        )
        os.environ.update(
            {
                "HERMES_HOME": str(self._home),
                "HERMES_SKIP_MCP": "1",
                "HERMES_SINGLE_QUERY_SESSION": "1",
                "HERMES_API_TIMEOUT": "45",
                "HERMES_TOOL_TIMEOUT": "35",
                "HERMES_CONCURRENT_TOOL_TIMEOUT_S": "35",
                "TERMINAL_ENV": "local",
                "TERMINAL_CWD": str(self._workspace),
                "TERMINAL_TIMEOUT": "30",
                "TZ": "UTC",
                "HERMES_TIMEZONE": "UTC",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        time.tzset()
        os.chdir(self._workspace)
        # The canonical interpreter's own stdlib remains available. No task or ambient site directory is admitted.
        python_root = Path(config["python_executable"]).parent.parent.resolve()
        sys.path[:] = [
            str(native_root),
            *[
                entry
                for entry in sys.path
                if entry
                and Path(entry).resolve().is_relative_to(python_root)
                and "site-packages" not in Path(entry).parts
            ],
        ]
        site.addsitedir(str(self._site_packages))
        sys.path.insert(0, str(self._source_root))
        self._source_runtime.update(
            {
                "workspace": str(self._workspace),
                "scratch": str(self._scratch),
                "home": str(self._home),
                "fixture_root": str(fixture_root),
                "fixture_sha256": fixture_digests,
                "shell_boundary": facts,
                "source_root": str(self._source_root),
                "site_packages": str(self._site_packages),
                "sdk_clients": self._sdk_clients,
            }
        )

    def _initialize(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        _require(self._phase == "new", "worker already initialized")
        self._keys(
            payload,
            {"task", "model_config", "workspace", "scratch", "schema_overlay", "remaining_seconds"},
        )
        self._admit_deadline(payload)
        overlay = payload["schema_overlay"]
        _require(
            isinstance(overlay, Mapping) and set(overlay) == {"read_file", "terminal"},
            "sealed schema overlay is missing or incomplete",
        )
        self._schema_overlay = overlay
        task, model = payload["task"], payload["model_config"]
        _require(type(task) is str and bool(task), "task must be nonempty text")
        self._task = task
        _require(
            isinstance(model, Mapping)
            and set(model)
            == {"model_name", "model_canonical_name", "max_input_tokens", "base_url"},
            "model configuration fields differ",
        )
        _require(
            model["model_canonical_name"] is None
            and type(model["model_name"]) is str
            and bool(model["model_name"])
            and type(model["max_input_tokens"]) is int
            and model["max_input_tokens"] > 0
            and type(model["base_url"]) is str
            and bool(model["base_url"]),
            "model configuration is outside the profile",
        )
        self._materialize_environment(payload["workspace"], payload["scratch"], model)
        source = self._source_import("run_agent")
        self._source_import("toolsets").create_custom_toolset(
            "bb-hermes-native",
            "Bounded Hermes seven-tool profile",
            tools=list(_TOOL_NAMES),
            includes=[],
        )
        state = _carrier_type(self, source)()
        self._state = state
        self._source_import("agent.agent_init").init_agent(
            state,
            base_url=model["base_url"],
            api_key="bb-hermes-native",
            provider="custom",
            api_mode="chat_completions",
            model=model["model_name"],
            max_iterations=MAX_REQUESTS,
            enabled_toolsets=["bb-hermes-native"],
            disabled_toolsets=[],
            save_trajectories=False,
            verbose_logging=False,
            quiet_mode=True,
            max_tokens=MAX_OUTPUT_TOKENS,
            reasoning_config=None,
            request_overrides={},
            platform="api_server",
            skip_context_files=False,
            load_soul_identity=False,
            skip_memory=False,
            skip_background_review=True,
            session_db=None,
            pass_session_id=False,
            checkpoints_enabled=False,
            fallback_model=None,
            credential_pool=None,
            capabilities={"streaming": False},
        )
        _require(
            state.api_mode == "chat_completions" and state.provider == "custom",
            "source selected a different API route",
        )
        _require(
            state._resolved_api_call_timeout() == PROVIDER_SECONDS,
            "native provider timeout differs",
        )
        state.context_compressor.context_length = model["max_input_tokens"]
        runtime_module = importlib.import_module("hermes_tools")
        _require(
            Path(runtime_module.__file__).resolve().parent
            == Path(__file__).resolve().parent,
            "tool runtime outside native root",
        )
        self._tool_error_type = runtime_module.HermesToolRuntimeError
        self._runtime = runtime_module.HermesToolRuntime(
            state,
            workspace=self._workspace,
            scratch=self._scratch,
            hermes_home=self._home,
            schema_overlay=self._schema_overlay,
            remaining=self._remaining,
        )
        runtime_facts = self._runtime.initialize()
        self._tool_schemas = list(state.tools)
        _require(
            tuple(item["function"]["name"] for item in self._tool_schemas)
            == _TOOL_NAMES,
            "native tool order differs",
        )
        loop = self._source_import("agent.conversation_loop")
        self._loop = loop
        user_message, moa_config, persist_user_message = loop._decode_inline_moa_turn(
            task, None
        )
        _require(moa_config is None, "inline MoA is outside the admitted profile")
        state._last_compaction_in_place = state._last_compression_attempt_recorded = (
            False
        )
        state._last_compression_attempt_in_place = None
        loop.begin_fast_mode_turn(state, None)
        context = loop.build_turn_context(
            state,
            user_message,
            None,
            None,
            state.session_id,
            None,
            persist_user_message,
            None,
            persist_user_display_kind=None,
            persist_user_display_metadata=None,
            persist_user_platform_id=None,
            turn_author=None,
            restore_or_build_system_prompt=loop._restore_or_build_system_prompt,
            install_safe_stdio=loop._install_safe_stdio,
            sanitize_surrogates=loop._sanitize_surrogates,
            summarize_user_message_for_log=loop._summarize_user_message_for_log,
            set_session_context=loop.set_session_context,
            set_current_write_origin=loop.set_current_write_origin,
            ra=loop._ra,
            moa_active=False,
        )
        # These are the pinned turn prelude's seven per-turn resets, not carrier defaults.
        state._delivered_interim_texts = set()
        state._incremental_persistence_failed = False
        state._last_persistence_error_cause = None
        state._compression_adoption_failed = False
        state._ephemeral_reasoning_off = False
        state._auth_pool_refresh_counts = {}
        state._last_turn_usage = None
        self._loop_state = loop._LoopState(
            system_message=None,
            moa_config=None,
            max_compression_attempts=getattr(state, "max_compression_attempts", 3),
            **{
                field.name: getattr(context, field.name.lstrip("_"))
                for field in dataclasses.fields(loop._LoopState)
                if field.name in loop._CTX_FIELDS
            },
        )
        self._initialized, self._phase = True, "ready"
        self._source_runtime.update(
            {
                "source": _SOURCE_ID,
                "tool_order": list(_TOOL_NAMES),
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "max_iterations": MAX_REQUESTS,
                "provider_timeout_seconds": PROVIDER_SECONDS,
                "native_tool_timeout_seconds": NATIVE_TOOL_SECONDS,
                "terminal_timeout_seconds": TERMINAL_SECONDS,
                "raw_command_output_bytes": MAX_RAW_COMMAND_BYTES,
                "observation_bytes": MAX_OBSERVATION_BYTES,
                "response_bytes": MAX_PROVIDER_BYTES,
                "transcript_bytes": MAX_TRANSCRIPT_BYTES,
                "module_origins": {
                    name: str(Path(module.__file__).resolve())
                    for name, module in self._source_modules.items()
                },
                "native_runtime": runtime_facts,
            }
        )
        return self._result(
            "initialized",
            tool_schemas=self._tool_schemas,
            source_runtime=self._source_runtime,
        )

    def _result(
        self, kind: str, *, advance: bool = True, **extra: Any
    ) -> dict[str, Any]:
        if self._status != "RUNNING":
            self._capture_resources()
        revision, rows = _history_revision(self._history_rows, self._messages)
        digest = revision["after_digest"] if revision else self._history_digest
        if advance:
            self._history_rows, self._history_digest = rows, digest
        state = self._loop_state
        result = {
            "schema_version": SCHEMA_VERSION,
            "kind": kind,
            "event_delta": [revision] if revision else [],
            "history_digest": digest,
            "status": self._status,
            "iteration": state.api_call_count if state else 0,
            "public_stop": self._stop_reason,
            "source_exit": state._turn_exit_reason if state else None,
            "native_counters": {
                "actual_requests": self._requests,
                "source_flushes": self._source_flushes,
                "sdk_clients": list(self._sdk_clients),
            },
        }
        if hasattr(self, "_workspace"):
            result["file_effects"] = self._workspace_effects()
        if state is not None:
            result["native_counters"].update(
                {
                    "source_iteration": state.api_call_count,
                    "iteration_budget_used": self._state.iteration_budget.used,
                    "iteration_budget_remaining": self._state.iteration_budget.remaining,
                }
            )
            result["proposal"] = {
                "phase": self._last_source_phase,
                "retry_count": state.retry_count,
                "restart_count": state.restart_count,
                "length_continue_retries": state.length_continue_retries,
                "truncated_tool_call_retries": state.truncated_tool_call_retries,
                "truncated_response_parts": list(state.truncated_response_parts),
                **{
                    name: vars(self._state)[name]
                    for name in (
                        "_ephemeral_max_output_tokens",
                        "_ephemeral_reasoning_off",
                        "_budget_grace_call",
                    )
                    if name in vars(self._state)
                },
            }
        if self._source_error is not None:
            result["source_error"] = self._source_error
        if self._native_result is not None:
            result["source_result_metadata"] = {
                key: value
                for key, value in self._native_result.items()
                if key != "messages"
            }
        if self._resource_facts is not None:
            result["resource_facts"] = self._resource_facts
        result.update(extra)
        _require(
            len(_canonical_json(result)) <= FRAME_BYTES,
            "native response exceeds frame limit",
        )
        return result

    @contextmanager
    def _parent_deadline(self, seconds: float):
        # httpx does not enforce timeouts around a custom synchronous transport.
        # Interrupt both a stalled frame header and a stalled frame body.
        _require(
            threading.current_thread() is threading.main_thread(),
            "parent I/O must run on the control thread",
        )
        _require(
            signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0),
            "source owns an unexpected active real-time timer",
        )
        seconds = min(seconds, self._remaining())
        _require(seconds > 0, "native parent exchange exhausted the episode deadline")
        previous = signal.getsignal(signal.SIGALRM)

        def expired(signum: int, frame: Any) -> None:
            raise HermesWorkerError("native parent exchange deadline expired")

        signal.signal(signal.SIGALRM, expired)
        try:
            signal.setitimer(signal.ITIMER_REAL, seconds)
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)

    def _receive(self, operation: str, keys: set[str]) -> Mapping[str, Any]:
        with self._parent_deadline(ACTION_SECONDS):
            command = self._channel.receive()
        _require(
            isinstance(command, Mapping)
            and command.get("operation") == operation
            and isinstance(command.get("payload"), Mapping),
            f"expected {operation}",
        )
        payload = command["payload"]
        self._keys(payload, keys)
        if "remaining_seconds" in payload:
            self._admit_deadline(payload)
        return payload

    def _exchange(self, operation: str, value: Any) -> Any:
        if threading.get_ident() == self._control_thread:
            _require(operation == "history", "HTTP escaped the source sampling phase")
            return self._after_native_flush(value)
        exchange = _ParentExchange(operation, value)
        self._exchanges.put(exchange)
        timeout = (
            min(PROVIDER_SECONDS, self._remaining())
            if operation == "http"
            else self._remaining()
        )
        return exchange.result.result(timeout=max(0.001, timeout))

    def _handle_http(self, request: Any) -> Any:
        _require(
            self._phase == "sampling" and self._requests < MAX_REQUESTS,
            "provider request outside admitted sample",
        )
        body = request.read()
        _require(len(body) <= FRAME_BYTES, "provider request exceeds frame limit")
        _require(request.method == "POST", "unexpected SDK HTTP method")
        self._requests += 1
        with self._parent_deadline(PROVIDER_SECONDS):
            self._channel.respond(
                self._result(
                    "provider_request",
                    http_request={
                        "method": request.method,
                        "url": str(request.url),
                        "headers": [
                            [key.decode("latin-1"), value.decode("latin-1")]
                            for key, value in request.headers.raw
                        ],
                        "body_b64": base64.b64encode(body).decode("ascii"),
                    },
                )
            )
            command = self._channel.receive()
        _require(
            isinstance(command, Mapping)
            and command.get("operation") == "provider_response"
            and isinstance(command.get("payload"), Mapping),
            "expected provider response",
        )
        payload = command["payload"]
        self._admit_deadline(payload)
        raw = base64.b64decode(payload["body_b64"], validate=True)
        _require(len(raw) <= MAX_PROVIDER_BYTES, "provider response exceeds 4 MiB")
        status = payload["status_code"]
        _require(type(status) is int and 100 <= status <= 599, "invalid HTTP status")
        headers = payload["headers"]
        _require(
            isinstance(headers, list)
            and all(
                isinstance(pair, (list, tuple))
                and len(pair) == 2
                and all(type(item) is str for item in pair)
                for pair in headers
            ),
            "invalid response headers",
        )
        self._raw_provider_response = raw
        return self._source_import("httpx").Response(
            status, headers=headers, content=raw, request=request
        )

    def _source_phase(self, name: str, **extra: Any) -> Any:
        self._last_source_phase = name
        return self._loop._run_phase(
            getattr(self._loop, name), self._state, self._loop_state, **extra
        )

    def _stop(self, reason: str) -> None:
        self._status, self._stop_reason = "STOPPED", reason

    def _accept_native_result(self, result: dict[str, Any]) -> None:
        _require(isinstance(result, dict), "native phase returned no result")
        result = self._source_import("agent.turn_context").export_current_turn_boundary(
            self._state,
            result,
            self._task,
        )
        self._native_result = result
        if "messages" in result:
            _require(
                isinstance(result["messages"], list), "native result history is invalid"
            )
            self._loop_state.messages = result["messages"]
        if result.get("completed") is True and not result.get("cleanup_errors"):
            self._status = "FINISHED"
        elif result.get("failed") is True or result.get("cleanup_errors"):
            self._status = "ERROR"
        else:
            self._stop("native_partial")

    def _finish_native(self) -> None:
        self._last_source_phase = "finalize_turn"
        finalizer = self._loop.finalize_turn
        values = {
            name: getattr(self._loop_state, name)
            for name in inspect.signature(finalizer).parameters
            if name != "agent"
        }
        self._accept_native_result(finalizer(self._state, **values))

    def _sample_source(self) -> None:
        for name in (
            "begin_iteration",
            "prepare_iteration",
            "assemble_api_request",
            "run_preflight_gate",
            "announce_api_call",
            "nous_rate_limit_guard",
            "build_api_request",
            "perform_api_call",
            "check_api_response",
            "apply_retry_restarts",
        ):
            verdict = self._source_phase(name)
            if name == "announce_api_call":
                state = self._loop_state
                state.api_start_time, state.retry_count, state.max_retries = (
                    time.time(),
                    0,
                    self._state._api_max_retries,
                )
                state._retry, state.finish_reason, state.response, state.api_kwargs = (
                    self._loop.TurnRetryState(),
                    "stop",
                    None,
                    None,
                )
                state.api_request_id = self._state._current_api_request_id = (
                    f"{state.turn_id}:api:{state.api_call_count}"
                )
            if name in {
                "prepare_iteration",
                "assemble_api_request",
                "announce_api_call",
                "build_api_request",
            }:
                continue
            if verdict.action == "return":
                self._accept_native_result(verdict.result)
                return
            if verdict.action == "continue":
                self._stop("excluded_recovery:" + name)
                return
            if verdict.action == "break" and name != "check_api_response":
                self._finish_native()
                return

    def _sample(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._keys(payload, {"remaining_seconds"})
        self._admit_deadline(payload)
        _require(
            self._phase == "ready" and self._status == "RUNNING", "sample out of phase"
        )
        if (
            self._requests >= MAX_REQUESTS
            or self._loop_state.api_call_count >= self._state.max_iterations
            or self._state.iteration_budget.remaining <= 0
        ):
            self._stop(
                "request_cap"
                if self._requests >= MAX_REQUESTS
                else "native_iteration_cap"
            )
            return self._result("sample_ready")
        self._phase, self._raw_provider_response = "sampling", b""
        completion: concurrent.futures.Future = concurrent.futures.Future()

        def run() -> None:
            try:
                self._sample_source()
            except BaseException as exc:
                completion.set_exception(exc)
            else:
                completion.set_result(None)

        context = contextvars.copy_context()
        thread = threading.Thread(
            target=context.run, args=(run,), name="hermes-source-sample", daemon=True
        )
        thread.start()
        try:
            while not completion.done():
                _require(self._remaining() > 0, "episode deadline exhausted")
                try:
                    exchange = self._exchanges.get(timeout=min(0.05, self._remaining()))
                except queue.Empty:
                    continue
                try:
                    if exchange.operation == "http":
                        response = self._handle_http(exchange.value)
                    elif exchange.operation == "history":
                        response = self._after_native_flush(exchange.value)
                    else:
                        raise HermesWorkerError("unknown parent exchange")
                except BaseException as exc:
                    exchange.result.set_exception(exc)
                    raise
                else:
                    exchange.result.set_result(response)
            try:
                completion.result()
            except _ProfileStop as exc:
                self._stop(str(exc))
            except _HistoryAbort:
                raise
            except Exception as exc:
                self._source_error = {
                    "type": type(exc).__name__,
                    "message": str(exc)[:4096],
                }
                if isinstance(exc, self._source_import("openai").APIError):
                    self._stop("excluded_api_retry")
                else:
                    self._status, self._stop_reason = "ERROR", "source_phase_error"
        finally:
            self._phase = "sampled"
        return self._result(
            "sample_ready",
            raw_response_b64=base64.b64encode(self._raw_provider_response).decode(
                "ascii"
            ),
        )

    def _prepare(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self._keys(payload, {"remaining_seconds"})
        self._admit_deadline(payload)
        _require(self._phase == "sampled", "prepare out of phase")
        if self._status != "RUNNING":
            self._phase = "ready"
            return self._result("prepared", actions=[], segments=[])
        self._bridge_completed = False
        try:
            intake = self._source_phase("normalize_model_response")
            if intake.action == "return":
                self._accept_native_result(intake.result)
            elif intake.action == "continue":
                self._stop("excluded_recovery:normalize_model_response")
            else:
                assistant = self._loop_state.assistant_message
                name = (
                    "run_tool_round" if assistant.tool_calls else "finish_text_response"
                )
                before = _digest(self._messages)
                verdict = self._source_phase(name)
                if verdict.action == "return":
                    self._accept_native_result(verdict.result)
                elif verdict.action == "break":
                    self._finish_native()
                elif (
                    verdict.action == "continue"
                    and not self._bridge_completed
                    and _digest(self._messages) == before
                ):
                    self._stop("excluded_silent_continuation:" + name)
                elif verdict.action not in {"continue", "fallthrough"}:
                    raise HermesWorkerError("unknown native phase verdict")
        except _ProfileStop as exc:
            self._stop(str(exc))
        except Exception as exc:
            if isinstance(exc, (HermesWorkerError, self._tool_error_type)):
                raise
            self._source_error = {
                "type": type(exc).__name__,
                "message": str(exc)[:4096],
            }
            verdict = self._source_phase("handle_outer_loop_error", e=exc)
            if verdict.action == "break":
                self._finish_native()
                self._status = "ERROR"
            else:
                self._stop("excluded_outer_error_retry")
        if self._status == "RUNNING" and self._requests >= MAX_REQUESTS:
            self._stop("request_cap")
        self._phase = "ready"
        if self._bridge_completed:
            return self._result(
                "committed", journal_delta={"source_flushes": self._source_flushes}
            )
        return self._result("prepared", actions=[], segments=[])

    def _call_fields(self, call: Any, index: int) -> dict[str, Any]:
        executor = self._source_import("agent.tool_executor")
        call_id = executor._pairing_tool_call_id(call)
        _require(
            type(call_id) is str
            and bool(call_id)
            and type(call.function.arguments) is str,
            "native call identity/arguments are invalid",
        )
        return {
            "index": index,
            "tool_id": call.function.name,
            "call_id": call_id,
            "arguments_json": call.function.arguments,
        }

    def _execute_native_batch(
        self, assistant: Any, messages: list, task_id: str, api_count: int
    ) -> None:
        calls = assistant.tool_calls
        _require(
            bool(calls) and messages is self._messages,
            "native batch lost source history",
        )
        actions = [self._call_fields(call, index) for index, call in enumerate(calls)]
        positions = {id(call): index for index, call in enumerate(calls)}
        if len(calls) == 1:
            segments = [("sequential", calls)]
        else:
            segments = self._source_import(
                "agent.tool_dispatch_helpers"
            )._plan_tool_batch_segments(calls, execution_cwd=self._runtime.cwd)
        segment_rows = [
            {
                "index": index,
                "kind": kind,
                "action_indices": [positions[id(call)] for call in segment],
            }
            for index, (kind, segment) in enumerate(segments)
        ]
        self._channel.respond(
            self._result("prepared", actions=actions, segments=segment_rows)
        )
        self._state._executing_tools = True
        try:
            for index, (kind, segment) in enumerate(segments):
                payload = self._receive(
                    "execute_segment", {"index", "remaining_seconds"}
                )
                _require(
                    type(payload["index"]) is int and payload["index"] == index,
                    "segment order differs",
                )
                self._phase = "executing"
                self._segment_index = index
                self._segment_expected = [
                    actions[positions[id(call)]] for call in segment
                ]
                self._segment_flushed = 0
                rows = self._runtime.execute_segment(
                    kind, segment, messages, task_id, api_count
                )
                _require(
                    self._segment_flushed == len(segment),
                    "native segment did not durably append every result",
                )
                _require(
                    isinstance(rows, list), "native segment returned invalid results"
                )
                by_id = {
                    row["tool_call_id"]: row
                    for row in rows
                    if row.get("role") == "tool"
                }
                observations = []
                for action in self._segment_expected:
                    _require(
                        action["call_id"] in by_id,
                        "native segment omitted an action result",
                    )
                    row = by_id[action["call_id"]]
                    observation = {
                        "action_index": action["index"],
                        "tool_id": action["tool_id"],
                        "call_id": action["call_id"],
                        "started": None,
                        "result": row["content"],
                        "effect_disposition": row.get("effect_disposition"),
                        "native_result": row,
                    }
                    _require(
                        len(_canonical_json(observation)) <= MAX_OBSERVATION_BYTES,
                        "tool observation exceeds 16 MiB",
                    )
                    observations.append(observation)
                self._segment_index, self._segment_expected = None, []
                self._channel.respond(
                    self._result(
                        "segment_executed", index=index, observations=observations
                    )
                )
            self._receive("commit", {"remaining_seconds"})
            self._phase = "committing"
            executor = self._source_import("agent.tool_executor")
            executor._finalize_tool_batch(
                self._state,
                messages,
                task_id,
                len(calls),
                executor._budget_for_agent(self._state),
            )
            self._bridge_completed = True
        finally:
            self._segment_index, self._segment_expected = None, []
            self._state._executing_tools = False

    def _after_native_flush(self, messages: list[dict[str, Any]]) -> None:
        if self._loop_state is None:
            self._bootstrap_messages = messages
        else:
            _require(
                messages is self._messages,
                "native flush replaced history without phase state",
            )
        revision, rows = _history_revision(self._history_rows, messages)
        if revision is None:
            return
        coordinates = {}
        if (
            self._segment_index is not None
            and messages
            and messages[-1].get("role") == "tool"
        ):
            _require(
                self._segment_flushed < len(self._segment_expected),
                "native segment appended extra results",
            )
            action = self._segment_expected[self._segment_flushed]
            _require(
                messages[-1].get("tool_call_id") == action["call_id"],
                "native result order differs",
            )
            coordinates = {
                "segment_index": self._segment_index,
                "action_index": action["index"],
            }
        digest = revision["after_digest"]
        try:
            with self._parent_deadline(ACTION_SECONDS):
                self._channel.respond(
                    self._result(
                        "history_checkpoint",
                        advance=False,
                        phase="native_flush",
                        **coordinates,
                    )
                )
                command = self._channel.receive()
        except Exception as exc:
            self._status, self._stop_reason = "ERROR", "history_ack_failed"
            raise _HistoryAbort("canonical history acknowledgement failed") from exc
        payload = command.get("payload") if isinstance(command, Mapping) else None
        if (
            not isinstance(command, Mapping)
            or command.get("operation") != "history_ack"
            or not isinstance(payload, Mapping)
            or payload.get("history_digest") != digest
            or set(payload) - {"history_digest", "remaining_seconds"}
        ):
            self._status, self._stop_reason = "ERROR", "history_ack_failed"
            raise _HistoryAbort("canonical history was not acknowledged")
        if "remaining_seconds" in payload:
            self._admit_deadline(payload)
        self._history_rows, self._history_digest = rows, digest
        if coordinates:
            self._segment_flushed += 1

    def _capture_resources(self) -> None:
        if self._runtime is not None and self._resource_facts is None:
            self._resource_facts = self._runtime.capture_resources()

    def dispatch(self, operation: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        _require(
            not self._closed and isinstance(payload, Mapping),
            "closed worker or invalid payload",
        )
        try:
            if operation == "initialize":
                return self._initialize(payload)
            _require(self._initialized, "worker is not initialized")
            if operation == "sample":
                return self._sample(payload)
            if operation == "prepare":
                return self._prepare(payload)
            raise HermesWorkerError(f"out-of-phase native operation: {operation}")
        except _HistoryAbort as exc:
            self._status, self._stop_reason = "ERROR", "history_ack_failed"
            raise HermesWorkerError(str(exc)) from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._capture_resources()
        finally:
            try:
                try:
                    if (
                        self._loop_state is not None
                        and not self._native_cleanup_returned
                    ):
                        self._state._cleanup_task_resources(
                            self._loop_state.effective_task_id
                        )
                finally:
                    if self._state is not None:
                        self._state.close()
            finally:
                if self._runtime is not None:
                    self._runtime.close()


def factory(channel: Any) -> HermesActor:
    return HermesActor(channel)


__all__ = ["HermesActor", "HermesWorkerError", "factory"]

if __name__ == "__main__":
    from native_worker import serve

    serve(factory)
