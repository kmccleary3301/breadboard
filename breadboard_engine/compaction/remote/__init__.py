"""Provider-agnostic remote compaction dispatcher and collaborator ports.

Routes compaction to provider-native ports (OpenAI Responses, Anthropic native)
or to an optional self-hosted remote endpoint (OMP ``compaction.remoteEndpoint``).
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Mapping, Optional, Sequence
import urllib.error
import urllib.request

from ..methods import (
    CompactionContext,
    CompactionError,
    CompactionRecord,
    MethodUnavailable,
    RemoteCompactionPort,
)
from ..settings import MAX_SUMMARY_TOKENS
from ..transcript import find_cut_point, leading_system_count
from ..prompts import SUMMARIZATION_PROMPT, SUMMARIZATION_SYSTEM_PROMPT



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


def _serialize_messages_for_prompt(messages: Sequence[Mapping[str, Any]]) -> str:
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    parts.append(str(part["text"]))
                elif isinstance(part, str):
                    parts.append(part)
            content = "\n".join(parts)
        lines.append(f"<{role}>\n{content}\n</{role}>")
    return "\n\n".join(lines)


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
        head = leading_system_count(context.messages)
        cut = find_cut_point(
            context.messages,
            head,
            len(context.messages),
            context.settings.keep_recent_tokens,
        )
        messages_to_summarize = context.messages[head:cut.first_kept_index]
        if not messages_to_summarize:
            raise MethodUnavailable("No history messages eligible for remote endpoint summarization")

        conversation_text = _serialize_messages_for_prompt(messages_to_summarize)
        prompt_parts = [f"<conversation>\n{conversation_text}\n</conversation>\n"]
        latest_boundary = context.state.latest_readable_boundary()
        if latest_boundary and latest_boundary.summary:
            prompt_parts.append(f"<previous-summary>\n{latest_boundary.summary}\n</previous-summary>\n")
        if context.custom_instructions:
            prompt_parts.append(f"Additional focus: {context.custom_instructions}\n")
        prompt_parts.append(SUMMARIZATION_PROMPT)
        prompt_text = "\n".join(prompt_parts)

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
