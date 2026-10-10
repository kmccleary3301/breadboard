"""Reusable recording mock provider for long-session compaction lanes.

Stdlib-only HTTP server (using `http.server.ThreadingHTTPServer`) serving:
- OpenAI Responses: ``POST /v1/responses`` (SSE)
- OpenAI Chat Completions: ``POST /v1/chat/completions`` (SSE or JSON)
- Anthropic Messages: ``POST /v1/messages`` (SSE)

Script format:
==============
A script is a JSON array of response descriptors played in FIFO order:
1. Text response:
   {
       "type": "text",
       "text": "Hello world",
       "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
   }
2. Tool call response:
   {
       "type": "tool_call",
       "name": "shell",
       "call_id": "call_123",
       "arguments": "{\"command\": \"ls\"}",
       "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
   }
3. Combined response (text and/or tool calls):
   {
       "type": "response",
       "text": "Thinking...",
       "tool_calls": [
           {"call_id": "call_1", "name": "shell", "arguments": "ls"}
       ],
       "usage": {"input_tokens": 150, "output_tokens": 25, "total_tokens": 175}
   }
4. Native provider error (e.g. context length exceeded):
   {
       "type": "error",
       "error": "context_length_exceeded",
       "message": "Your input exceeds the context window of this model.",
       "status_code": 400
   }
   When `error` is "context_length_exceeded", each protocol formats its native
   error payload (OpenAI Responses SSE `response.failed` event or 400, Chat Completions 400,
   Anthropic 400 invalid_request_error).
5. Raw response (verbatim status, headers, and SSE chunks or body):
   {
       "type": "raw",
       "status_code": 200,
       "headers": {"content-type": "text/event-stream"},
       "chunks": ["event: foo\\ndata: bar\\n\\n"]
   }

Recorded requests:
==================
Every incoming request is recorded to a JSONL file with:
{
    "timestamp": 123456789.0,
    "method": "POST",
    "path": "/v1/responses",
    "headers": {"content-type": "application/json", ...},  # auth headers stripped
    "raw_body_bytes": "...",  # hex encoded
    "raw_body": "...",        # utf-8 decoded string (best effort)
    "json": {...}             # parsed JSON body or null
}

Identifiers and recorded timestamps are deterministic per provider instance.
Chat completion ``created`` defaults to the fixture epoch 1700000000 and can be
set explicitly in a response descriptor. An exhausted script raises in the
engine and returns HTTP 409 rather than inventing a successful response.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

AUTH_HEADER_NAMES = {
    "authorization",
    "x-api-key",
    "api-key",
    "openai-organization",
    "openai-project",
    "x-openai-actor-authorization",
}


def sanitize_headers(headers: Any) -> Dict[str, str]:
    """Return request headers with authentication headers stripped."""
    cleaned: Dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() not in AUTH_HEADER_NAMES:
            cleaned[key] = value
    return cleaned


class MockScriptEngine:
    """Manages an ordered queue of scripted responses and request recording."""

    def __init__(
        self,
        script: Optional[List[Dict[str, Any]]] = None,
        record_path: Optional[Union[str, Path]] = None,
    ) -> None:
        self.script: List[Dict[str, Any]] = list(script) if script else []
        self.record_path = Path(record_path) if record_path else None
        self.recorded_requests: List[Dict[str, Any]] = []
        self._id_counter = 0
        self._lock = threading.Lock()
        if self.record_path:
            self.record_path.parent.mkdir(parents=True, exist_ok=True)
            # Truncate or open file
            with open(self.record_path, "w", encoding="utf-8") as f:
                f.write("")

    def append_script(self, item: Dict[str, Any]) -> None:
        with self._lock:
            self.script.append(item)

    def set_script(self, script: List[Dict[str, Any]]) -> None:
        with self._lock:
            self.script = list(script)

    def next_id(self) -> str:
        """Generate replay-stable identifiers within this provider instance."""
        with self._lock:
            self._id_counter += 1
            return f"{self._id_counter:012d}"

    def next_response(self) -> Dict[str, Any]:
        with self._lock:
            if not self.script:
                raise RuntimeError("Mock provider script exhausted")
            return self.script.pop(0)

    def record_request(
        self,
        method: str,
        path: str,
        headers: Dict[str, str],
        raw_body: bytes,
        parsed_json: Optional[Any],
    ) -> Dict[str, Any]:
        record = {
            "timestamp": 1700000000,
            "method": method,
            "path": path,
            "headers": sanitize_headers(headers),
            "raw_body_bytes": raw_body.hex(),
            "raw_body": raw_body.decode("utf-8", errors="replace"),
            "json": parsed_json,
        }
        with self._lock:
            record["timestamp"] += len(self.recorded_requests)
            self.recorded_requests.append(record)
            if self.record_path:
                with open(self.record_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record) + "\n")
        return record


class MockRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Responses, Chat Completions, and Messages."""

    server: MockThreadingServer

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress default noisy stderr logging unless explicitly requested
        pass

    def do_GET(self) -> None:
        if self.path in ("/health", "/healthz"):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}\n')
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        # Read body
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b""
        parsed_json = None
        if raw_body:
            try:
                parsed_json = json.loads(raw_body.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

        # Record the request
        self.server.engine.record_request(
            method="POST",
            path=self.path,
            headers=dict(self.headers),
            raw_body=raw_body,
            parsed_json=parsed_json,
        )

        try:
            item = self.server.engine.next_response()
        except RuntimeError as exc:
            self.send_error(HTTPStatus.CONFLICT, str(exc))
            return

        # Route by endpoint
        normalized_path = self.path.split("?")[0]
        if normalized_path in ("/v1/responses", "/responses"):
            self._handle_openai_responses(item, parsed_json)
        elif normalized_path in ("/v1/chat/completions", "/chat/completions"):
            self._handle_chat_completions(item, parsed_json)
        elif normalized_path in ("/v1/messages", "/messages"):
            self._handle_anthropic_messages(item, parsed_json)
        elif normalized_path in ("/v1/responses/compact", "/responses/compact"):
            self._handle_openai_compact(item, parsed_json)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, f"Endpoint {self.path} not mapped")

    def _handle_openai_responses(self, item: Dict[str, Any], request_json: Optional[Any]) -> None:
        """Handle OpenAI Responses API (`POST /v1/responses`) with SSE."""
        if item.get("type") == "raw":
            self._send_raw_response(item)
            return

        resp_id = item.get("id") or f"resp_{self.server.engine.next_id()}"
        model = (request_json or {}).get("model", "gpt-4o") if isinstance(request_json, dict) else "gpt-4o"

        # Check for error
        if item.get("type") == "error":
            error_code = item.get("error", "context_length_exceeded")
            error_message = item.get(
                "message",
                "Your input exceeds the context window of this model. Please adjust your input and try again.",
            )
            status_code = item.get("status_code", 400)
            if status_code == 200:
                # Deliver as SSE response.failed event
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                event_payload = {
                    "type": "response.failed",
                    "response": {
                        "id": resp_id,
                        "status": "failed",
                        "error": {
                            "code": error_code,
                            "message": error_message,
                        },
                    },
                }
                body = f"event: response.failed\ndata: {json.dumps(event_payload)}\n\n"
                self.wfile.write(body.encode("utf-8"))
                return

            # Native HTTP 400 Bad Request
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            err_body = {
                "error": {
                    "message": error_message,
                    "type": "invalid_request_error",
                    "param": None,
                    "code": error_code,
                }
            }
            self.wfile.write(json.dumps(err_body).encode("utf-8"))
            return

        # Normal response SSE
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        created_ev = {
            "type": "response.created",
            "response": {
                "id": resp_id,
                "status": "in_progress",
                "model": model,
            },
        }
        self.wfile.write(f"event: response.created\ndata: {json.dumps(created_ev)}\n\n".encode("utf-8"))

        # Text item
        text = item.get("text")
        if text:
            msg_id = f"msg_{self.server.engine.next_id()}"
            msg_ev = {
                "type": "response.output_item.done",
                "item": {
                    "type": "message",
                    "role": "assistant",
                    "id": msg_id,
                    "content": [{"type": "output_text", "text": text}],
                },
            }
            self.wfile.write(f"event: response.output_item.done\ndata: {json.dumps(msg_ev)}\n\n".encode("utf-8"))

        # Tool calls
        tool_calls = item.get("tool_calls") or []
        if item.get("type") == "tool_call":
            tool_calls = [
                {
                    "call_id": item.get("call_id") or f"call_{self.server.engine.next_id()}",
                    "name": item.get("name", "tool"),
                    "arguments": item.get("arguments", "{}"),
                }
            ]
        for tc in tool_calls:
            args_str = tc.get("arguments", "{}")
            if isinstance(args_str, (dict, list)):
                args_str = json.dumps(args_str)
            tc_ev = {
                "type": "response.output_item.done",
                "item": {
                    "type": "function_call",
                    "call_id": tc.get("call_id") or f"call_{self.server.engine.next_id()}",
                    "name": tc.get("name", "tool"),
                    "arguments": args_str,
                },
            }
            self.wfile.write(f"event: response.output_item.done\ndata: {json.dumps(tc_ev)}\n\n".encode("utf-8"))

        # Usage and completion
        raw_usage = item.get("usage") or {}
        input_tokens = raw_usage.get("input_tokens", 50)
        output_tokens = raw_usage.get("output_tokens", 20)
        total_tokens = raw_usage.get("total_tokens", input_tokens + output_tokens)

        completed_ev = {
            "type": "response.completed",
            "response": {
                "id": resp_id,
                "status": "completed",
                "usage": {
                    "input_tokens": input_tokens,
                    "input_tokens_details": None,
                    "output_tokens": output_tokens,
                    "output_tokens_details": None,
                    "total_tokens": total_tokens,
                },
            },
        }
        self.wfile.write(f"event: response.completed\ndata: {json.dumps(completed_ev)}\n\n".encode("utf-8"))

    def _handle_openai_compact(self, item: Dict[str, Any], request_json: Optional[Any]) -> None:
        """Handle unary `/v1/responses/compact` if invoked."""
        if item.get("type") == "raw":
            self._send_raw_response(item)
            return
        if item.get("type") == "error":
            self.send_response(item.get("status_code", 400))
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            err_body = {
                "error": {
                    "message": item.get("message", "Context window exceeded"),
                    "type": "invalid_request_error",
                    "code": item.get("error", "context_length_exceeded"),
                }
            }
            self.wfile.write(json.dumps(err_body).encode("utf-8"))
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        body = {
            "output": item.get("output") or [
                {"type": "compaction", "encrypted_content": item.get("encrypted_content", "compacted_summary")}
            ]
        }
        self.wfile.write(json.dumps(body).encode("utf-8"))

    def _handle_chat_completions(self, item: Dict[str, Any], request_json: Optional[Any]) -> None:
        """Handle OpenAI Chat Completions API (`POST /v1/chat/completions`)."""
        if item.get("type") == "raw":
            self._send_raw_response(item)
            return

        stream = False
        model = "gpt-4o"
        if isinstance(request_json, dict):
            stream = bool(request_json.get("stream", False))
            model = request_json.get("model", "gpt-4o")

        # Check for error
        if item.get("type") == "error":
            error_code = item.get("error", "context_length_exceeded")
            error_message = item.get(
                "message",
                "This model's maximum context length is 16385 tokens. However, your messages resulted in 18000 tokens.",
            )
            status_code = item.get("status_code", 400)
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            err_body = {
                "error": {
                    "message": error_message,
                    "type": "invalid_request_error",
                    "param": "messages",
                    "code": error_code,
                }
            }
            self.wfile.write(json.dumps(err_body).encode("utf-8"))
            return

        resp_id = item.get("id") or f"chatcmpl-{self.server.engine.next_id()}"
        now = item.get("created", 1700000000)
        text = item.get("text", "")
        raw_usage = item.get("usage") or {}
        input_tokens = raw_usage.get("input_tokens", 50)
        output_tokens = raw_usage.get("output_tokens", 20)
        total_tokens = raw_usage.get("total_tokens", input_tokens + output_tokens)
        usage = {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": total_tokens,
        }

        tool_calls = item.get("tool_calls") or []
        if item.get("type") == "tool_call":
            tool_calls = [
                {
                    "index": 0,
                    "id": item.get("call_id") or f"call_{self.server.engine.next_id()}",
                    "type": "function",
                    "function": {
                        "name": item.get("name", "tool"),
                        "arguments": item.get("arguments", "{}")
                        if isinstance(item.get("arguments"), str)
                        else json.dumps(item.get("arguments", {})),
                    },
                }
            ]
        elif tool_calls:
            formatted = []
            for idx, tc in enumerate(tool_calls):
                args = tc.get("arguments", "{}")
                if not isinstance(args, str):
                    args = json.dumps(args)
                formatted.append(
                    {
                        "index": idx,
                        "id": tc.get("call_id") or tc.get("id") or f"call_{self.server.engine.next_id()}",
                        "type": "function",
                        "function": {
                            "name": tc.get("name", "tool"),
                            "arguments": args,
                        },
                    }
                )
            tool_calls = formatted
        if stream:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            # First chunk: role
            chunk1 = {
                "id": resp_id,
                "object": "chat.completion.chunk",
                "created": now,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": ""},
                        "finish_reason": None,
                    }
                ],
            }
            self.wfile.write(f"data: {json.dumps(chunk1)}\n\n".encode("utf-8"))

            if text:
                chunk_text = {
                    "id": resp_id,
                    "object": "chat.completion.chunk",
                    "created": now,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": text},
                            "finish_reason": None,
                        }
                    ],
                }
                self.wfile.write(f"data: {json.dumps(chunk_text)}\n\n".encode("utf-8"))

            if tool_calls:
                chunk_tc = {
                    "id": resp_id,
                    "object": "chat.completion.chunk",
                    "created": now,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"tool_calls": tool_calls},
                            "finish_reason": None,
                        }
                    ],
                }
                self.wfile.write(f"data: {json.dumps(chunk_tc)}\n\n".encode("utf-8"))

            # Final chunk
            chunk_end = {
                "id": resp_id,
                "object": "chat.completion.chunk",
                "created": now,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "tool_calls" if tool_calls else "stop",
                    }
                ],
                "usage": usage,
            }
            self.wfile.write(f"data: {json.dumps(chunk_end)}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            # JSON response
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            msg: Dict[str, Any] = {"role": "assistant", "content": text or None}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            body = {
                "id": resp_id,
                "object": "chat.completion",
                "created": now,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": msg,
                        "finish_reason": "tool_calls" if tool_calls else "stop",
                    }
                ],
                "usage": usage,
            }
            self.wfile.write(json.dumps(body).encode("utf-8"))

    def _handle_anthropic_messages(self, item: Dict[str, Any], request_json: Optional[Any]) -> None:
        """Handle Anthropic Messages API (`POST /v1/messages`) with SSE."""
        if item.get("type") == "raw":
            self._send_raw_response(item)
            return

        model = (request_json or {}).get("model", "claude-3-5-sonnet-20241022") if isinstance(request_json, dict) else "claude-3-5-sonnet"

        # Check for error
        if item.get("type") == "error":
            error_message = item.get(
                "message",
                "prompt is too long: 205000 tokens > 200000 maximum context length",
            )
            status_code = item.get("status_code", 400)
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            err_body = {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": error_message,
                },
            }
            self.wfile.write(json.dumps(err_body).encode("utf-8"))
            return

        resp_id = item.get("id") or f"msg_{self.server.engine.next_id()}"
        text = item.get("text", "")
        raw_usage = item.get("usage") or {}
        input_tokens = raw_usage.get("input_tokens", 50)
        output_tokens = raw_usage.get("output_tokens", 20)

        tool_calls = item.get("tool_calls") or []
        if item.get("type") == "tool_call":
            tool_calls = [
                {
                    "call_id": item.get("call_id") or f"toolu_{self.server.engine.next_id()}",
                    "name": item.get("name", "tool"),
                    "arguments": item.get("arguments", "{}"),
                }
            ]

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        # 1. message_start
        ev_msg_start = {
            "type": "message_start",
            "message": {
                "id": resp_id,
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": model,
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": input_tokens, "output_tokens": 1},
            },
        }
        self.wfile.write(f"event: message_start\ndata: {json.dumps(ev_msg_start)}\n\n".encode("utf-8"))

        idx = 0
        if text:
            # 2. content_block_start
            ev_block_start = {
                "type": "content_block_start",
                "index": idx,
                "content_block": {"type": "text", "text": ""},
            }
            self.wfile.write(f"event: content_block_start\ndata: {json.dumps(ev_block_start)}\n\n".encode("utf-8"))

            # 3. content_block_delta
            ev_block_delta = {
                "type": "content_block_delta",
                "index": idx,
                "delta": {"type": "text_delta", "text": text},
            }
            self.wfile.write(f"event: content_block_delta\ndata: {json.dumps(ev_block_delta)}\n\n".encode("utf-8"))

            # 4. content_block_stop
            ev_block_stop = {"type": "content_block_stop", "index": idx}
            self.wfile.write(f"event: content_block_stop\ndata: {json.dumps(ev_block_stop)}\n\n".encode("utf-8"))
            idx += 1

        for tc in tool_calls:
            call_id = tc.get("call_id") or f"toolu_{self.server.engine.next_id()}"
            name = tc.get("name", "tool")
            args = tc.get("arguments", "{}")
            if not isinstance(args, str):
                args = json.dumps(args)

            ev_tc_start = {
                "type": "content_block_start",
                "index": idx,
                "content_block": {"type": "tool_use", "id": call_id, "name": name, "input": {}},
            }
            self.wfile.write(f"event: content_block_start\ndata: {json.dumps(ev_tc_start)}\n\n".encode("utf-8"))

            ev_tc_delta = {
                "type": "content_block_delta",
                "index": idx,
                "delta": {"type": "input_json_delta", "partial_json": args},
            }
            self.wfile.write(f"event: content_block_delta\ndata: {json.dumps(ev_tc_delta)}\n\n".encode("utf-8"))

            ev_tc_stop = {"type": "content_block_stop", "index": idx}
            self.wfile.write(f"event: content_block_stop\ndata: {json.dumps(ev_tc_stop)}\n\n".encode("utf-8"))
            idx += 1

        # 5. message_delta
        ev_msg_delta = {
            "type": "message_delta",
            "delta": {
                "stop_reason": "tool_use" if tool_calls else "end_turn",
                "stop_sequence": None,
            },
            "usage": {"output_tokens": output_tokens},
        }
        self.wfile.write(f"event: message_delta\ndata: {json.dumps(ev_msg_delta)}\n\n".encode("utf-8"))

        # 6. message_stop
        ev_msg_stop = {"type": "message_stop"}
        self.wfile.write(f"event: message_stop\ndata: {json.dumps(ev_msg_stop)}\n\n".encode("utf-8"))

    def _send_raw_response(self, item: Dict[str, Any]) -> None:
        status_code = item.get("status_code", 200)
        self.send_response(status_code)
        headers = item.get("headers") or {}
        if "content-type" not in {k.lower() for k in headers}:
            self.send_header("Content-Type", "text/event-stream")
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()

        chunks = item.get("chunks") or []
        body = item.get("body")
        if body:
            if isinstance(body, str):
                self.wfile.write(body.encode("utf-8"))
            else:
                self.wfile.write(bytes(body))
        for chunk in chunks:
            if isinstance(chunk, str):
                self.wfile.write(chunk.encode("utf-8"))
            else:
                self.wfile.write(bytes(chunk))


class MockThreadingServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: Tuple[str, int],
        RequestHandlerClass: Any,
        engine: MockScriptEngine,
    ) -> None:
        self.engine = engine
        self.daemon_threads = True
        super().__init__(server_address, RequestHandlerClass)


class MockProvider:
    """Convenient manager for running MockThreadingServer in tests or standalone."""

    def __init__(
        self,
        script: Optional[List[Dict[str, Any]]] = None,
        record_path: Optional[Union[str, Path]] = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self.host = host
        self.port = port
        self.engine = MockScriptEngine(script=script, record_path=record_path)
        self.server: Optional[MockThreadingServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> str:
        self.server = MockThreadingServer(
            (self.host, self.port),
            MockRequestHandler,
            engine=self.engine,
        )
        self.port = self.server.server_port
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        return self.base_url

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def __enter__(self) -> MockProvider:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Mock Provider for LLM APIs")
    parser.add_argument("--host", default="127.0.0.1", help="Host address")
    parser.add_argument("--port", type=int, default=8000, help="Port")
    parser.add_argument("--script", type=Path, help="Path to script JSON file")
    parser.add_argument("--record", type=Path, help="Path to JSONL recording output")
    args = parser.parse_args()

    script = None
    if args.script and args.script.exists():
        script = json.loads(args.script.read_text(encoding="utf-8"))

    provider = MockProvider(script=script, record_path=args.record, host=args.host, port=args.port)
    base_url = provider.start()
    print(f"Mock Provider listening at {base_url}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        provider.stop()


if __name__ == "__main__":
    main()
