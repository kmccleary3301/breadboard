"""Live 2.1.63 SDK context; BB retains its own conversation and summary model.

Requires the pinned CLI. Context comes from this session's live stock process,
never recordings. Each tool continuation and compact continuation re-renders
stock context (cli.js:6110); no extra probe turn consumes its skill reminders.
Auto restoration is SG6/ne (cli.js:2207,2209,2211), not the /compact command.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re
from urllib.parse import parse_qsl, urlsplit

from ...execution.claude_tools import get_claude_tool_session, StockExecutionError

_USER_CONTEXT = "<system-reminder>\nAs you answer the user's questions, you can use the following context:\n"
_BILLING = re.compile(r"\Ax-anthropic-billing-header: cc_version=(2\.1\.63)\.[0-9a-f]{3}; cc_entrypoint=sdk-cli; cch=00000;\Z")


def _portable_messages(messages):
    result = deepcopy(messages)
    for message in result:
        message["content"] = [
            {key: value for key, value in block.items() if key != "cache_control"}
            for block in message["content"]
            if not (block.get("type") == "text" and block.get("text", "").startswith(_USER_CONTEXT))
        ]
    return result


def build_context(owner, state, run_context, *, fresh, user_message):
    session = get_claude_tool_session(owner, session_state=state)
    content = user_message["content"]
    if not isinstance(content, str):
        raise StockExecutionError("Claude startup requires a canonical user text prompt")
    source = session.capture_context(content)
    if fresh:
        with state.context_mutation():
            system = [{key: value for key, value in block.items() if key != "cache_control"}
                      for block in source["system"]]
            state.messages[0]["content"] = system
            state.provider_messages[0]["content"] = deepcopy(system)
    portable = _portable_messages([source["messages"][-1]])
    if portable[0]["role"] != "user":
        raise StockExecutionError("Stock startup did not produce the user prompt")
    # The conductor appends this original message after the context hook.
    user_message["content"] = portable[0]["content"]
    return []


def _current_context(session, *, summary):
    operation = session.operation or {}
    if summary:
        source = operation.get("summary_request")
    else:
        source = operation.get("continuation_context") or operation.get("request")
    if not isinstance(source, dict):
        raise StockExecutionError("Stock has not rendered the current request context")
    return source


def _billing(first_user, version):
    # cli.js:498 IF5/Y28/ff7: first user text, UTF-16 indices 4/7/20, salt.
    text = next((block["text"] for block in first_user["content"] if block.get("type") == "text"), "")
    units = text.encode("utf-16-le", errors="surrogatepass")
    selected = "".join(units[index * 2:index * 2 + 2].decode("utf-16-le", errors="surrogatepass")
                       if index * 2 < len(units) else "0" for index in (4, 7, 20))
    selected = selected.encode("utf-16-le", errors="surrogatepass").decode("utf-16-le", errors="replace")
    return hashlib.sha256(("59cf53e54c78" + selected + version).encode()).hexdigest()[:3]


def build_request(request, context):
    session = getattr(context.session_state, "_claude_tool_session", None)
    if session is None:
        raise StockExecutionError("Claude live context session is unavailable")
    summary = bool((context.extra or {}).get("compaction_summary"))
    source = _current_context(session, summary=summary)
    request = deepcopy(request)
    messages = request["messages"]
    # TD4 DV/PD at cli.js:2211 joins adjacent user messages for the summary.
    if summary:
        joined = []
        for message in messages:
            if joined and message["role"] == joined[-1]["role"] == "user":
                joined[-1]["content"].extend(message["content"])
            else:
                joined.append(message)
        messages = request["messages"] = joined
    else:
        user_context = [block for message in source["messages"] for block in message["content"]
                        if block.get("type") == "text" and block.get("text", "").startswith(_USER_CONTEXT)]
        if user_context:
            positions = context.extra["request_layout_user_indices"]
            if not positions:
                raise StockExecutionError("Claude user context has no canonical user message")
            blocks = messages[positions[-1]]["content"]
            blocks.insert(len(blocks) - 1, deepcopy(user_context[-1]))
    system = deepcopy(source["system"])
    header = system[0]["text"]
    match = _BILLING.fullmatch(header)
    if match is None:
        raise StockExecutionError("Unsupported stock attribution header")
    first_user = next(message for message in messages if message["role"] == "user")
    suffix = _billing(first_user, match.group(1))
    system[0]["text"] = f"x-anthropic-billing-header: cc_version={match.group(1)}.{suffix}; cc_entrypoint=sdk-cli; cch=00000;"
    request["system"] = system
    request["metadata"] = deepcopy(source["metadata"])
    # Exact source schemas, only for tools the production conductor admitted.
    native_tools = {tool["name"]: tool for tool in source.get("tools", [])}
    declared = request.get("tools", [])
    if any(tool["name"] not in native_tools for tool in declared):
        raise StockExecutionError("Declared tool is absent from the live stock catalog")
    if declared:
        request["tools"] = [deepcopy(native_tools[tool["name"]]) for tool in declared]
    # cli.js:6110 Vjz/Pjz/Wjz: the final block of the last two messages.
    cache = deepcopy(system[-1]["cache_control"])
    for message in messages:
        for block in message["content"]:
            block.pop("cache_control", None)
    for message in messages[-2:]:
        if message["content"] and message["content"][-1]["type"] not in {"thinking", "redacted_thinking"}:
            message["content"][-1]["cache_control"] = deepcopy(cache)
    transport = session.last_transport
    parsed = urlsplit(transport["path"])
    if parsed.path != "/v1/messages":
        raise StockExecutionError("Unsupported stock Messages endpoint")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if query:
        request["extra_query"] = query
    request["extra_headers"] = {**request.get("extra_headers", {}), **transport["headers"]}
    return request


def accept_response(response, context):
    session = getattr(context.session_state, "_claude_tool_session", None)
    if session is None:
        raise StockExecutionError("Claude live context session is unavailable")
    native = response.model_dump(mode="json", exclude_unset=True)
    if (context.extra or {}).get("compaction_summary"):
        session.native_summary_response = native
        return
    session.stage_primary_response(native)
    if not any(block["type"] == "tool_use" for block in native["content"]):
        with session.lock:
            session.release_main_response({"kind": "answer"}, native)
            session.await_tool_result()


class _SummaryProjection:
    def __init__(self, state):
        self.state = state

    def prepare(self, request):
        return request

    def complete(self, response):
        session = getattr(self.state, "_claude_tool_session", None)
        if session is None:
            raise StockExecutionError("Claude automatic mirror is unavailable")
        return _portable_messages(session.complete_auto_compaction(response))


def build_summary_projection(state, config):
    return _SummaryProjection(state)
