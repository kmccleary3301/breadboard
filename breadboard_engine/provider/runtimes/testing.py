"""Deterministic provider runtimes used for tests and smoke checks."""

from __future__ import annotations

import json
import textwrap
from typing import Any, Dict, List, Optional

from ..contracts import ProviderMessage, ProviderResult, ProviderRuntime, ProviderRuntimeContext, ProviderToolCall


class MockRuntime(ProviderRuntime):
    """A simple mock provider runtime for offline validation.

    Heuristics:
      - If no prior tool calls, request list_dir(path=".", depth=1)
      - Else if only one prior tool call, request apply_unified_patch with a minimal project skeleton
      - Else, return a short assistant message
    """

    def create_client(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        default_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        return {"mock": True}

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
        model_hint = (model or "").strip().lower()
        if model_hint in {"no_tools", "no_tool", "zero_tool", "no_tool_activity"}:
            out_messages = [
                ProviderMessage(
                    role="assistant",
                    content="...",
                    tool_calls=[],
                    finish_reason="stop",
                    index=0,
                )
            ]
            return ProviderResult(
                messages=out_messages,
                raw_response={"mock": True, "mode": model_hint},
                usage=None,
                encrypted_reasoning=None,
                reasoning_summaries=None,
                model="mock",
            )
        # Count prior tool calls in assistant messages
        prior_calls = 0
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            tool_calls = msg.get("tool_calls")
            if isinstance(tool_calls, list):
                prior_calls += len(tool_calls)

        def _seen_tool(name: str) -> bool:
            for msg in messages:
                if msg.get("role") != "assistant":
                    continue
                for call in msg.get("tool_calls") or []:
                    fn = getattr(getattr(call, "function", None), "name", "") or call.get("function")
                    if fn == name:
                        return True
            return False

        has_todo = _seen_tool("todo.write_board")
        has_write = _seen_tool("write")
        has_shell = _seen_tool("run_shell")

        def _mk_tool_call(name: str, args: Dict[str, Any]) -> ProviderToolCall:
            try:
                arg_str = json.dumps(args)
            except Exception:
                arg_str = "{}"
            ptc = ProviderToolCall(
                id=f"mock-call-{prior_calls + 1}-{name}",
                name=name,
                arguments=arg_str,
                type="function",
            )
            try:
                from types import SimpleNamespace as _SNS
                setattr(ptc, "function", _SNS(name=name, arguments=arg_str))
            except Exception:
                pass
            return ptc

        out_messages: List[ProviderMessage] = []

        if prior_calls == 0:
            # Explore workspace
            tc = _mk_tool_call("list_dir", {"path": ".", "depth": 1})
            out_messages.append(ProviderMessage(role="assistant", content=None, tool_calls=[tc], finish_reason="stop", index=0))
        elif prior_calls == 1:
            # Emit a minimal unified diff patch adding Makefile and skeleton files
            unified = textwrap.dedent(
                """
                diff --git a/Makefile b/Makefile
                new file mode 100644
                index 0000000..c3f9c3b
                --- /dev/null
                +++ b/Makefile
                @@ -0,0 +1,7 @@
                +CC=gcc
                +CFLAGS=-Wall -Wextra -Werror
                +all: test
                +test: protofilesystem.o test_filesystem.o
                +\t$(CC) $(CFLAGS) -o test_fs protofilesystem.o test_filesystem.o
                +clean:
                +\trm -f *.o test_fs

                diff --git a/protofilesystem.h b/protofilesystem.h
                new file mode 100644
                index 0000000..1f1264a
                --- /dev/null
                +++ b/protofilesystem.h
                @@ -0,0 +1,6 @@
                +#ifndef PROTOFILESYSTEM_H
                +#define PROTOFILESYSTEM_H
                +
                +int fs_init(void);
                +
                +#endif

                diff --git a/protofilesystem.c b/protofilesystem.c
                new file mode 100644
                index 0000000..4d6c0be
                --- /dev/null
                +++ b/protofilesystem.c
                @@ -0,0 +1,5 @@
                +#include \"protofilesystem.h\"
                +
                +int fs_init(void) {
                +    return 0;
                +}

                diff --git a/test_filesystem.c b/test_filesystem.c
                new file mode 100644
                index 0000000..a3bb0bc
                --- /dev/null
                +++ b/test_filesystem.c
                @@ -0,0 +1,11 @@
                +#include <stdio.h>
                +#include \"protofilesystem.h\"
                +
                +int main(void) {
                +    if (fs_init() != 0) {
                +        fprintf(stderr, \"fs_init failed\\n\");
                +        return 1;
                +    }
                +    printf(\"OK\\n\");
                +    return 0;
                +}
                """
            ).lstrip("\n")
            if not unified.endswith("\n"):
                unified += "\n"
            tc = _mk_tool_call("apply_unified_patch", {"patch": unified})
            out_messages.append(ProviderMessage(role="assistant", content=None, tool_calls=[tc], finish_reason="stop", index=0))
        else:
            out_messages.append(ProviderMessage(role="assistant", content="Proceed to build and test.", tool_calls=[], finish_reason="stop", index=0))

        return ProviderResult(messages=out_messages, raw_response={"mock": True}, usage=None, encrypted_reasoning=None, reasoning_summaries=None, model="mock")


class SmokeRuntime(ProviderRuntime):
    """Deterministic single-turn runtime for CI/smoke checks.

    Emits a completion sentinel immediately (no tool calls) so headless runs
    can validate the full request/stream/shutdown path without spending tokens.
    """

    def create_client(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        default_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        return {"smoke": True}

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
        content = "Hi! All systems nominal.\n\nTASK COMPLETE\n\n>>>>>> END RESPONSE"
        if stream:
            event = {"content_index": 0, "message_id": "smoke-message-1"}
            context.record_provider_event("response_start")
            context.record_provider_event("text_start", event)
            context.record_provider_event("text_delta", {**event, "delta": content})
            context.record_provider_event("text_end", event)
        return ProviderResult(
            messages=[
                ProviderMessage(
                    role="assistant",
                    content=content,
                    tool_calls=[],
                    finish_reason="stop",
                    index=0,
                )
            ],
            raw_response={"smoke": True},
            usage=None,
            encrypted_reasoning=None,
            reasoning_summaries=None,
            model="smoke",
        )


class CliMockRuntime(ProviderRuntime):
    """Deterministic runtime for CLI guardrail fixtures."""

    def create_client(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        default_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        return {"cli_mock": True}

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
        prior_calls = 0
        has_todo = False
        has_write = False
        has_shell = False
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            tool_calls = msg.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            prior_calls += len(tool_calls)
            for call in tool_calls:
                name = None
                if isinstance(call, dict):
                    fn_block = call.get("function")
                    if isinstance(fn_block, dict):
                        name = fn_block.get("name")
                    name = name or call.get("name")
                if not name:
                    continue
                if name.startswith("todo."):
                    has_todo = True
                elif name.startswith("write") or name == "create_file_from_block":
                    has_write = True
                elif name.startswith("run_shell") or name == "bash.run":
                    has_shell = True

        def _mk_tool_call(name: str, args: Dict[str, Any]) -> ProviderToolCall:
            payload = json.dumps(args)
            call = ProviderToolCall(id=None, name=name, arguments=payload, type="function")
            try:
                from types import SimpleNamespace as _SNS
                setattr(call, "function", _SNS(name=name, arguments=payload))
            except Exception:
                pass
            return call

        out_messages: List[ProviderMessage] = []

        if not has_todo:
            board = {
                "todos": [
                    {"content": "Plan work", "status": "completed"},
                    {"content": "Implement feature", "status": "in_progress"},
                    {"content": "Validate output", "status": "pending"},
                ]
            }
            call = _mk_tool_call("todo.write_board", board)
            out_messages.append(
                ProviderMessage(role="assistant", content=None, tool_calls=[call], finish_reason="stop", index=0)
            )
        elif not has_write:
            call = _mk_tool_call(
                "write",
                {
                    "path": "bubble_sort.py",
                    "content": "def bubble_sort(nums):\n    n = len(nums)\n    for i in range(n):\n        for j in range(0, n - i - 1):\n            if nums[j] > nums[j + 1]:\n                nums[j], nums[j + 1] = nums[j + 1], nums[j]\n    return nums\n\nif __name__ == '__main__':\n    data = [5, 3, 1, 4, 2]\n    print(bubble_sort(data))\n",
                },
            )
            out_messages.append(
                ProviderMessage(role="assistant", content=None, tool_calls=[call], finish_reason="stop", index=0)
            )
        elif not has_shell:
            call = _mk_tool_call("run_shell", {"command": "python bubble_sort.py", "timeout": 30})
            out_messages.append(
                ProviderMessage(role="assistant", content=None, tool_calls=[call], finish_reason="stop", index=0)
            )
        else:
            out_messages.append(
                ProviderMessage(
                    role="assistant",
                    content="TASK COMPLETE",
                    tool_calls=[],
                    finish_reason="stop",
                    index=0,
                )
            )

        raw_response = {
            "mock": True,
            "prior_tool_calls": prior_calls,
        }
        return ProviderResult(messages=out_messages, raw_response=raw_response, metadata={"status_code": 200})


__all__ = ["MockRuntime", "SmokeRuntime", "CliMockRuntime"]
