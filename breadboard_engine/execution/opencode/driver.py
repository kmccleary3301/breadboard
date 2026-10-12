"""Conductor admission and provider routing for pinned OpenCode sessions."""
from __future__ import annotations
import copy
import os
from pathlib import Path
import uuid
from typing import Any
from .session import OpenCodeToolSession
from .lifecycle import DEFAULT_BINARY
from .wire import StockExecutionError, upstream_headers, response_from_result

SEMANTICS = {"opencode_1_2_17", "oh_my_opencode_3_10_0"}

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
        from ..stock_tools import register_stock_session
        register_stock_session(conductor, sessions[key])
    state._opencode_tool_session = sessions[key]
    sessions[key].current_state = state
    handler = getattr(conductor, "_opencode_stock_input_handler", None)
    if handler is not None:
        sessions[key].stock_input_handler = handler
    sessions[key].headless = getattr(conductor, "_opencode_stock_headless", False)
    return sessions[key]


def bridge_stock_interaction(conductor, state, session, kind: str, properties: dict):
    broker = conductor.permission_broker
    if kind == "question.asked":
        from ...permissions.opencode_questions import request_stock_question
        answers = request_stock_question(broker, state, properties)
        if answers is None:
            session.api("POST", "/question/" + properties["id"] + "/reject", {})
        else:
            session.api("POST", "/question/" + properties["id"] + "/reply", {"answers": answers})
        return
    if kind != "permission.asked":
        raise StockExecutionError(f"Unknown stock interaction {kind}")
    from ...permissions.broker import PermissionRequest
    requests = [
        PermissionRequest(properties["permission"], pattern,
                          {"stock_kind": "opencode_permission", "stock_request": properties})
        for pattern in properties["patterns"]
    ]
    decisions = [decision for _, decision in broker._request_permissions(state, requests, concurrent=True)]
    reply = "reject" if "reject" in decisions else "always" if decisions and all(d == "always" for d in decisions) else "once"
    session.api("POST", "/permission/" + properties["id"] + "/reply", {"reply": reply})


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
            from ...provider.input_media import resolve_input_media
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
        from ...provider.contracts import ProviderRuntimeContext
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
    from ...compaction.overflow import is_context_overflow
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


