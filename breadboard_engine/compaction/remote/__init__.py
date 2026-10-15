"""Provider-agnostic remote compaction dispatcher and collaborator ports.

Routes compaction to provider-native ports (OpenAI Responses, Anthropic native)
or to an optional self-hosted remote endpoint (OMP ``compaction.remoteEndpoint``).
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Mapping, Optional
import urllib.error
import urllib.request

from ..methods import (
    CompactionContext,
    CompactionError,
    CompactionRecord,
    MethodUnavailable,
)
from ..settings import MAX_SUMMARY_TOKENS
from ..serialize import escape_summary_boundary_tags, serialize_conversation_for_summary
from ..primitives.protected_windows import visible_messages
from ..transcript import find_cut_point
from ..prompts import SUMMARIZATION_PROMPT, SUMMARIZATION_SYSTEM_PROMPT, UPDATE_SUMMARIZATION_PROMPT



def _default_http_poster(
    url: str,
    payload: Mapping[str, Any],
    headers: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except Exception as exc:
        raise CompactionError(f"Remote endpoint POST to {url} failed: {exc}") from exc


class RemoteCompaction:
    """Provider-agnostic dispatcher for provider-native and endpoint compaction."""

    name: str = "remote"

    def __init__(
        self,
        *,
        http_poster: Optional[Callable[[str, Mapping[str, Any], Optional[Mapping[str, str]]], Dict[str, Any]]] = None,
    ) -> None:
        self._http_poster = http_poster or _default_http_poster

    def run(self, context: CompactionContext) -> CompactionRecord:
        # 1. Custom remote endpoint taking precedence when configured (OMP remoteEndpoint)
        if context.settings.remote_endpoint:
            return self._run_remote_endpoint(context)

        # 2. Provider-native port matching route (provider, api, supports(model))
        for port in context.remote_ports:
            if (
                port.provider == context.target.provider
                and port.api == context.target.api
                and port.supports(context.target.model)
            ):
                return port.compact(context)

        raise MethodUnavailable(
            f"No remote compaction port registered for route {context.target.provider}/{context.target.api} "
            f"supporting model {context.target.model}"
        )

    def _run_remote_endpoint(self, context: CompactionContext) -> CompactionRecord:
        endpoint = str(context.settings.remote_endpoint).strip()
        messages = visible_messages(context)
        start = context.state.kept_start(messages)
        cut = find_cut_point(
            messages,
            start,
            len(messages),
            context.settings.keep_recent_tokens,
        )
        messages_to_summarize = messages[start:cut.first_kept_index]
        if not messages_to_summarize:
            raise MethodUnavailable("No history messages eligible for remote endpoint summarization")

        conversation_text = serialize_conversation_for_summary(messages_to_summarize)
        # OMP compaction.ts summarizeConversationWindow: the endpoint replaces only the transport.
        previous = context.state.latest_readable_boundary()
        previous_summary = previous.summary if previous is not None else None
        base = UPDATE_SUMMARIZATION_PROMPT if previous_summary else SUMMARIZATION_PROMPT
        if context.custom_instructions:
            base = f"{base}\n\nAdditional focus: {context.custom_instructions}"
        prompt_text = f"<conversation>\n{conversation_text}\n</conversation>\n\n"
        if previous_summary:
            prompt_text += f"<previous-summary>\n{escape_summary_boundary_tags(previous_summary)}\n</previous-summary>\n\n"
        prompt_text += base

        is_chat_completions = "/chat/completions" in endpoint
        max_tokens = MAX_SUMMARY_TOKENS

        if is_chat_completions:
            payload: Dict[str, Any] = {
                "model": context.target.model,
                "messages": [
                    {"role": "system", "content": SUMMARIZATION_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt_text},
                ],
                "stream": False,
                "max_tokens": max_tokens,
            }
            resp_data = self._http_poster(endpoint, payload, None)
            choices = resp_data.get("choices") or []
            if not choices or not isinstance(choices[0], dict):
                raise CompactionError("Remote chat-completions endpoint returned no choices")
            choice_msg = choices[0].get("message") or {}
            summary = choice_msg.get("content")
            if not isinstance(summary, str) or not summary.strip():
                raise CompactionError("Remote chat-completions endpoint returned empty content")
            short_summary = "Remote compaction"
        else:
            payload = {
                "systemPrompt": SUMMARIZATION_SYSTEM_PROMPT,
                "prompt": prompt_text,
                "maxTokens": max_tokens,
            }
            resp_data = self._http_poster(endpoint, payload, None)
            summary = resp_data.get("summary")
            if not isinstance(summary, str) or not summary.strip():
                raise CompactionError("Remote endpoint returned missing or empty summary")
            short_summary = resp_data.get("shortSummary") or "Remote compaction"

        summary_messages = ({"role": "user", "content": f"<summary>\n{summary.strip()}\n</summary>"},)
        return context.new_record(
            method=self.name,
            first_kept_index=cut.first_kept_index,
            summary=summary.strip(),
            short_summary=short_summary,
            summary_messages=summary_messages,
            details={"remote_endpoint": endpoint},
        )
