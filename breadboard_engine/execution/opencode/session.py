"""Root session request coordination and stock history operations."""
from __future__ import annotations
import copy
import json
import time
import uuid
from .wire import StockExecutionError, upstream_headers, response_from_result
from .lifecycle import OpenCodeLifecycle

class OpenCodeToolSession(OpenCodeLifecycle):
    """One server/root session per BB session. Children remain stock-owned."""

    def forward_auxiliary_http(self, url: str, headers: dict, body: bytes):
        session_id = getattr(self.auxiliary_request, "session_id", None)
        if session_id is None:
            raise StockExecutionError("Auxiliary HTTP forwarding requires an authenticated stock session")
        return self.auxiliary_transport.forward(session_id, url, headers, body)


    def fail(self, message: str):
        with self.condition:
            self.failure = self.failure or message
            self.condition.notify_all()


    def check_alive(self):
        if self.failure or self.closed:
            raise StockExecutionError(self.failure or "Stock OpenCode session is closed")


    def record(self, kind: str, body: dict, headers: dict):
        record = {"kind": kind, "request": body, "headers": upstream_headers(headers)}
        self.records.append(record)
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as output:
                output.write(json.dumps(record, ensure_ascii=False) + "\n")


    def classify(self, headers: dict) -> str:
        sid = headers.get("x-bb-stock-session")
        agent = headers.get("x-bb-stock-agent")
        if not sid or not agent or not headers.get("x-bb-stock-provider"):
            raise StockExecutionError("Unlabelled stock provider request")
        if sid == self.session_id:
            if agent in {"title", "summary", "compaction"}:
                return agent
            if agent != self.root_agent:
                # Root primary agents can change between user turns; the stock
                # registry, not the first observed display name, is authoritative.
                known = next((item for item in self.api("GET", "/agent") if item["name"] == agent), None)
                if known is None or known.get("mode") not in {"primary", "all"}:
                    raise StockExecutionError(f"Unknown root agent {agent}")
                self.root_agent = agent
            return "main"
        ancestor = self.api("GET", "/session/" + sid)
        seen = set()
        while ancestor.get("parentID") and ancestor["id"] not in seen:
            seen.add(ancestor["id"])
            if ancestor["parentID"] == self.session_id:
                return "child"
            ancestor = self.api("GET", "/session/" + ancestor["parentID"])
        raise StockExecutionError(f"Provider request from unrelated stock session {sid}")


    def _root_finished(self) -> bool:
        return self.request_done.is_set() and self.native_prompt_done.is_set()


    def _wait_root_finished(self, message: str):
        with self.condition:
            ready = self.condition.wait_for(lambda: self._root_finished() or self.failure or self.closed, self.timeout)
            self.check_alive()
            if not ready:
                raise StockExecutionError(message)


    def wait_pending(self, *, allow_finished_request: bool = True) -> dict:
        with self.condition:
            ready = self.condition.wait_for(lambda: self.pending is not None or self.compaction_pending is not None or
                                            self.failure or self.closed or
                                            (allow_finished_request and self._root_finished()), self.timeout)
            self.check_alive()
            if not ready or (self.pending is None and self.compaction_pending is None):
                native_result = getattr(self, "last_request_result", None)
                native_error = native_result.get("info", {}).get("error") if isinstance(native_result, dict) else None
                if native_error is None:
                    messages = self.api("GET", "/session/" + self.session_id + "/message?limit=1")
                    native_error = next((m["info"]["error"] for m in messages if m["info"].get("error")), None)
                raise StockExecutionError(
                    f"Stock did not produce a root provider continuation: native_error={native_error!r}; "
                    f"{''.join(self.stderr)[-4000:]}"
                )
            if self.compaction_pending is not None:
                return self.compaction_pending["body"]
            if self.requires_plugin:
                names = {tool.get("name", tool.get("function", {}).get("name"))
                         for tool in self.pending["body"].get("tools", ())}
                if "look_at" not in names:
                    self.fail("Pinned oh-my-opencode plugin did not register its stock tools")
                    self.check_alive()
            return self.pending["body"]


    def user(self, parts: list[dict], *, agent: str | None = None):
        self._wait_root_finished("Previous stock turn has not completed")
        self.run_active = True
        self.final_response_pending = False
        self.user_file_parts = [copy.deepcopy(part) for part in parts if part["type"] == "file"]
        self.compaction_continuation_user_id = None
        self._start_request("/session/" + self.session_id + "/message",
                            {"parts": parts, "model": dict(zip(("providerID", "modelID"), self.model.split("/", 1))),
                             **({"agent": agent} if agent else {})})
        request = self.wait_pending()
        self.active_user_id = self._latest_user_message()["info"]["id"]
        return request


    def announce_stock_input(self, pending: dict) -> None:
        """Identify a stock-created user owning a new idle/final-boundary prompt."""
        if (
            self.run_active and not self.final_response_pending
            and pending.get("recovery_from_message_id") is None
        ) or self.compaction_summary is not None:
            return
        message = self._latest_user_message()
        message_id = message["info"]["id"]
        if message_id in {self.active_user_id, getattr(self, "compaction_continuation_user_id", None)}:
            return
        if self.headless:
            # Flush the same authenticated event stream used by `opencode run`
            # before deciding whether this notification followed its first idle.
            ready = self.condition.wait_for(
                lambda: message_id in self.observed_user_ids or self.failure or self.closed,
                self.timeout,
            )
            self.check_alive()
            if not ready:
                raise StockExecutionError("Stock notification user event was not observed")
            if self.headless_idle_seen:
                pending["cancelled"] = True
                self.condition.notify_all()
                return
        if self.stock_input_handler is None:
            raise StockExecutionError(
                "Stock-initiated parent turns require a live SessionRunner input queue; "
                "use SessionService for persistent native execution"
            )
        source = {
            "kind": "stock", "source": "opencode", "session_id": self.session_id,
            "message_id": message_id, "request_id": uuid.uuid4().hex,
            "agent": message["info"]["agent"], "model": copy.deepcopy(message["info"]["model"]),
            "content": "\n".join(part["text"] for part in message["parts"]
                                 if part["type"] == "text" and not part.get("ignored")),
        }
        if pending.get("recovery_from_message_id") is not None:
            source["recovery_from_message_id"] = pending["recovery_from_message_id"]
        pending["stock_origin"] = source
        self.stock_input_handler(copy.deepcopy(source))


    def claim_stock_input(self, source: dict) -> dict:
        """Claim the genuine source request without posting another user message."""
        with self.condition:
            pending = self.pending or self.compaction_pending
            if pending is None or pending.get("stock_origin") != source:
                raise StockExecutionError("Stock-origin input does not own the queued native request")
            if pending.get("stock_claimed"):
                raise StockExecutionError("Stock-origin input has already been claimed")
            pending["stock_claimed"] = True
            self.active_user_id = source["message_id"]
            self.user_file_parts = []
            self.compaction_continuation_user_id = None
            self.run_active = True
            self.final_response_pending = False
            return pending["body"]


    def submit(self, response: dict):
        self.wait_pending()
        calls = [item for item in response["output"] if item["type"] == "function_call"]
        with self.condition:
            pending = self.pending
            if pending is None:
                raise StockExecutionError("Cannot submit a main response while stock is awaiting BB compaction")
            self.pending = None
            self.final_response_pending = not calls
            pending["response"] = response
            self.condition.notify_all()
        if calls:
            self.wait_pending()
            missing = [item["call_id"] for item in calls if item["call_id"] not in self.results]
            if missing:
                raise StockExecutionError(f"Stock omitted tool results: {missing}")
        else:
            with self.condition:
                ready = self.condition.wait_for(lambda: self._root_finished() or self.headless_idle_seen or self.compaction_pending is not None or
                                                (self.pending is not None and self.pending.get("stock_origin") is not None) or
                                                self.failure or self.closed, self.timeout)
                if not ready:
                    raise StockExecutionError("Stock did not finish the mirrored parent response")
        self.check_alive()


    def submit_overflow(self, status: int, headers: dict, payload: bytes):
        """Let stock consume the original provider failure and create replay markers."""
        if not isinstance(status, int) or not 100 <= status <= 599:
            raise StockExecutionError(f"Invalid native provider HTTP status {status}")
        self.wait_pending()
        with self.condition:
            pending = self.pending
            if pending is None:
                raise StockExecutionError("No native main request is awaiting the provider failure")
            self.pending = None
            self._overflow_user_id = self.active_user_id
            self.recovery_outcome = None
            pending["provider_error"] = (status, headers, payload)
            pending["response"] = {}
            self.condition.notify_all()
        # A plugin may start recovery after the failed /message request returns.
        # Its summarize route owns that transition; never enqueue a competing one.
        with self.condition:
            ready = self.condition.wait_for(lambda: self.compaction_pending is not None or
                                            self.recovery_outcome == "handled" or
                                            self.failure or self.closed, self.timeout)
            self.check_alive()
            if not ready:
                # No source continuation: keep the original provider error as
                # the old turn's outcome instead of starting a competing summary.
                self.recovery_outcome = "error"
                self._overflow_user_id = None
            return self.recovery_outcome


    def text_response(self, text: str) -> dict:
        from types import SimpleNamespace
        return response_from_result(SimpleNamespace(raw_response=None, usage={},
                                    messages=[SimpleNamespace(content=text, tool_calls=[])]), self.model.split("/", 1)[1])


    def result(self, call_id: str) -> dict:
        self.check_alive()
        item = self.results.get(call_id)
        if item is None:
            raise StockExecutionError(f"No stock tool result for BB call {call_id}")
        output = item["output"]
        result = {}
        if isinstance(output, str):
            result["__mvi_text_output"] = output
        elif isinstance(output, list):
            result["__mvi_content_output"] = copy.deepcopy(output)
            result["__mvi_native_responses_output"] = True
            result["__mvi_text_output"] = "\n".join(p.get("text", "") for p in output if p.get("type") in {"input_text", "output_text"})
        else:
            raise StockExecutionError(f"Unexpected stock output type {type(output).__name__}")
        return result


    def compaction_prompt(self, *, auto: bool = True) -> dict:
        """Expose stock's summary request before BB generates/commits its summary."""
        with self.condition:
            if self.compaction_pending is not None:
                return self.compaction_pending["body"]
            if self.source_recovery_pending is not None and self.source_recovery_pending():
                ready = self.condition.wait_for(lambda: self.compaction_pending is not None or
                                                self.failure or self.closed, self.timeout)
                self.check_alive()
                if not ready:
                    raise StockExecutionError("Stock plugin did not produce its deferred compaction prompt")
                return self.compaction_pending["body"]
            pending = self.pending
            self.pending = None
            if pending:
                pending["cancelled"] = True
                self.condition.notify_all()
        if pending:
            self.api("POST", "/session/" + self.session_id + "/abort", {})
            # Native compaction precedes creating a continuation assistant.
            messages = self.api("GET", "/session/" + self.session_id + "/message")
            draft = messages[-1]
            if draft["info"].get("role") == "assistant" and not any(
                part.get("type") in {"text", "tool", "reasoning"} for part in draft["parts"]
            ):
                self.api("DELETE", f"/session/{self.session_id}/message/{draft['info']['id']}")
        self._wait_root_finished("Stock turn did not stop before compaction")
        self._start_request("/session/" + self.session_id + "/summarize",
                            {"providerID": self.model.split("/", 1)[0], "modelID": self.model.split("/", 1)[1], "auto": auto})
        with self.condition:
            ready = self.condition.wait_for(lambda: self.compaction_pending is not None or self.failure or
                                            self.closed or self._root_finished(), self.timeout)
            self.check_alive()
            if not ready or self.compaction_pending is None:
                raise StockExecutionError("Stock did not produce a compaction prompt")
            return self.compaction_pending["body"]


    def _latest_user_message(self) -> dict:
        messages = self.api("GET", "/session/" + self.session_id + "/message")
        for message in reversed(messages):
            if message["info"]["role"] == "user" and not any(
                part["type"] == "compaction" for part in message["parts"]
            ):
                return message
        raise StockExecutionError("Stock has no current user message to project")


    def _latest_user_parts(self) -> list[dict]:
        return self._latest_user_message()["parts"]


    def is_compaction_continuation(self) -> bool:
        continuation = getattr(self, "compaction_continuation_user_id", None)
        return continuation is not None and self._latest_user_message()["info"]["id"] == continuation


    def latest_user_text_parts(self) -> list[dict]:
        """Return only chat.message-transformed user text, never source history/media."""
        return [{"type": "text", "text": part["text"]} for part in self._latest_user_parts()
                if part["type"] == "text" and not part.get("ignored")]


    def latest_user_sequence(self, request: dict | None = None) -> list[dict]:
        """Expose canonical text and authorized media ordinals, never media bytes."""
        files = getattr(self, "user_file_parts", [])
        remaining = list(enumerate(files))
        sequence = []
        parts = self._latest_user_parts() if request is None else []
        if request is not None:
            current = next((item for item in reversed(request["input"]) if item.get("role") == "user"), None)
            if current is None:
                raise StockExecutionError("Stock in-flight input omitted its current user")
            content = current["content"]
            wire_remaining = list(files)
            for block in ([{"type": "input_text", "text": content}] if isinstance(content, str) else content):
                if block.get("type") == "input_text" and set(block) == {"type", "text"} and isinstance(block["text"], str):
                    parts.append({"type": "text", "text": block["text"]})
                elif block.get("type") in {"input_image", "input_file"}:
                    url = block.get("image_url") if block["type"] == "input_image" else block.get("file_data")
                    original = next((part for part in wire_remaining if part["url"] == url), None)
                    if original is None:
                        raise StockExecutionError("Stock in-flight transform introduced unauthorized media")
                    wire_remaining.remove(original)
                    parts.append({"type": "file", "url": url, "mime": original["mime"]})
                else:
                    raise StockExecutionError("Stock in-flight current-user projection contains an unknown block")
        for part in parts:
            if part["type"] == "text":
                if not part.get("ignored"):
                    sequence.append({"type": "text", "text": part["text"]})
            elif part["type"] == "file":
                match = next(((index, original) for index, original in remaining
                              if original["url"] == part["url"] and original["mime"] == part["mime"]), None)
                if match is None:
                    raise StockExecutionError("Stock user hook introduced media absent from BB's authorized input")
                remaining.remove(match)
                sequence.append({"type": "stock_media", "index": match[0]})
            else:
                raise StockExecutionError(f"Unsupported stock user part projection: {part['type']}")
        if remaining and not self.is_compaction_continuation():
            raise StockExecutionError("Stock user hook removed BB's authorized media")
        return sequence
    def source_should_continue(self) -> bool:
        """Expose queued source ownership without exporting coordination locks."""
        with self.condition:
            if self.closed or self.headless_idle_seen:
                return False
            return any(
                pending is not None and pending.get("stock_origin") is not None
                and not pending.get("stock_claimed")
                for pending in (self.pending, self.compaction_pending)
            )



    def set_compaction_response(self, result) -> str:
        """Retain the genuine BB summary response, including reasoning and usage."""
        if self.compaction_pending is None:
            raise StockExecutionError("No queued native compaction can receive the genuine summary response")
        model = str(self.compaction_pending["body"]["model"])
        self.compaction_response = response_from_result(result, model)
        self.compaction_pending["response"] = self.compaction_response
        return "".join(part.get("text", part.get("refusal", ""))
                       for item in self.compaction_response["output"] if item["type"] == "message"
                       for part in item.get("content", ()))


    def compact(self, summary: str, *, auto: bool = True):
        """Complete the queued native transition with BB's committed actual summary."""
        previous_user_id = self._latest_user_message()["info"]["id"]
        self.compaction_prompt(auto=auto)
        with self.condition:
            self.compaction_summary = summary
            pending = self.compaction_pending
            if pending is None:
                raise StockExecutionError("Native compaction lost its pending summary operation")
            pending["summary"] = summary
            self.condition.notify_all()
        try:
            with self.condition:
                if not self.condition.wait_for(lambda: self.compaction_pending is not pending or self.failure or
                                               self.closed, self.timeout):
                    raise StockExecutionError("Stock did not consume the committed summary")
                self.check_alive()
            if auto:
                # Plugin summarize may outlive the original failed /message.
                self.wait_pending(allow_finished_request=False)
                current_user_id = self._latest_user_message()["info"]["id"]
                if current_user_id != previous_user_id:
                    self.compaction_continuation_user_id = current_user_id
            else:
                self._wait_root_finished("Stock compaction timed out")
            self.check_alive()
        finally:
            self.compaction_summary = None
            self.compaction_response = None


    def prune(self, call_ids: list[str]):
        """Apply BB's exact prune selection using stock's public part-update API."""
        missing = set(call_ids) - self.pruned_call_ids
        if not missing:
            return
        messages = self.api("GET", "/session/" + self.session_id + "/message")
        for message in messages:
            for part in message["parts"]:
                if part.get("type") == "tool" and part.get("callID") in missing:
                    if part["state"]["status"] != "completed":
                        raise StockExecutionError("Cannot prune an incomplete stock tool")
                    part["state"]["time"]["compacted"] = int(time.time() * 1000)
                    self.api("PATCH", f"/session/{self.session_id}/message/{part['messageID']}/part/{part['id']}", part)
                    self.pruned_call_ids.add(part["callID"])
                    missing.remove(part["callID"])
        if missing:
            raise StockExecutionError(f"BB pruned calls missing from stock history: {sorted(missing)}")


    def wait_for_idle_source_input(self) -> None:
        """Fence admission behind a stock user already queued, even before SSE busy."""
        with self.condition:
            if self.run_active or self.stock_input_handler is None or self.active_user_id is None or self.closed:
                return
            if any(pending is not None and pending.get("stock_origin") is not None
                   for pending in (self.pending, self.compaction_pending)):
                return
        message = self._latest_user_message()
        if message["info"]["id"] not in {
            self.active_user_id, getattr(self, "compaction_continuation_user_id", None),
        } and any(part["type"] == "text" and not part.get("synthetic") for part in message["parts"]):
            # prompt.ts creates this user before its busy status/provider frame.
            # The previous request's idle cannot terminate this successor wait.
            self.wait_pending(allow_finished_request=False)


    def finish_run(self):
        """Interrupt a still-active parent using stock's own user-stop transition."""
        with self.condition:
            self.run_active = False
            finish_headless = self.headless and self.headless_idle_seen
            # OMO manager.ts:1359 enqueues a real parent prompt, not another BB
            # model continuation. The session runner owns its next normal turn.
            if not finish_headless:
                if any(pending is not None and pending.get("stock_origin") is not None and not pending.get("stock_claimed")
                       for pending in (self.pending, self.compaction_pending)):
                    return
                if self._root_finished() and self.pending is None and self.compaction_pending is None:
                    return
            preserve_source = self.stock_input_handler is not None and self.final_response_pending
        if finish_headless:
            # run.ts:531-537 exits at root idle; bootstrap then disposes the
            # instance. Do not keep background children alive after CLI exit.
            self.close()
            return
        if preserve_source:
            # prompt.ts creates the user before starting the session loop. A
            # genuine new user can therefore be busy before its model frame
            # reaches the proxy; do not abort that source-owned successor.
            message = self._latest_user_message()
            if message["info"]["id"] not in {
                self.active_user_id, getattr(self, "compaction_continuation_user_id", None),
            } and any(part["type"] == "text" and not part.get("synthetic") for part in message["parts"]):
                self.wait_pending()
                return
        try:
            # routes/session.ts:354-381 calls prompt.ts:256-267 cancel.
            # Never fabricate a model completion to terminate a budgeted BB run.
            if self.on_abort is not None:
                self.on_abort(self)
            self.api("POST", "/session/" + self.session_id + "/abort", {})
            with self.condition:
                for pending in (self.pending, self.compaction_pending):
                    if pending is not None:
                        pending["cancelled"] = True
                self.pending = None
                self.compaction_pending = None
                self.condition.notify_all()
            self._wait_root_finished("Stock parent turn did not finish after its run-boundary abort")
            self.check_alive()
        except Exception:
            self.close()
            raise


