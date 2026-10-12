"""OpenCode-labelled HTTP interception; parent and auxiliary protocols stay explicit."""
from __future__ import annotations
import copy
import json
from http.server import BaseHTTPRequestHandler
from ..opencode_auxiliary import AuxiliaryCancelled
from ..local_http_proxy import send_payload
from .wire import StockExecutionError, upstream_headers, responses_payload

def handler_for(session):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
    
        def do_POST(self):
            try:
                raw_body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                body = json.loads(raw_body)
                labels = {k.lower(): v for k, v in self.headers.items()}
                kind = session.classify(labels)
                session.auxiliary_request.session_id = labels["x-bb-stock-session"]
                session.record(kind, body, labels)
                if kind in {"main", "compaction"}:
                    session.native_prompt_done.clear()
                    with session.condition:
                        for item in body.get("input", []):
                            if item.get("type") == "function_call_output":
                                session.results[item["call_id"]] = copy.deepcopy(item)
                if kind in {"child", "title", "summary"}:
                    if session.auxiliary is None:
                        raise StockExecutionError(f"No real provider configured for stock {kind}")
                    status, headers, payload = session.auxiliary(body, upstream_headers(dict(self.headers)), self.path,
                                                                labels["x-bb-stock-provider"], raw_body=raw_body)
                elif kind == "compaction":
                    with session.condition:
                        if session.compaction_pending is not None:
                            raise StockExecutionError("Concurrent root compaction requests")
                        pending = {"body": body, "cancelled": False, "summary": None, "response": None}
                        session.compaction_pending = pending
                        if session._overflow_user_id is not None:
                            session.recovery_outcome = "summary"
                            session._overflow_user_id = None
                        session.announce_stock_input(pending)
                        session.condition.notify_all()
                        session.condition.wait_for(lambda: pending["summary"] is not None or pending["cancelled"] or
                                                   session.closed or session.failure)
                        if pending["cancelled"] or session.closed:
                            return
                        session.check_alive()
                        summary = pending["summary"]
                        native_response = pending["response"] or session.text_response(summary)
                        session.compaction_pending = None
                        session.condition.notify_all()
                    payload = responses_payload(native_response, bool(body.get("stream")))
                    status, headers = 200, {"content-type": "text/event-stream" if body.get("stream") else "application/json"}
                else:
                    if self.path != "/v1/responses":
                        raise StockExecutionError(f"Unknown parent endpoint {self.path}")
                    pending = {"body": body, "response": None, "cancelled": False}
                    with session.condition:
                        if session.pending is not None:
                            raise StockExecutionError("Concurrent root model requests")
                        session.pending = pending
                        recovery_from = session._overflow_user_id
                        if recovery_from is not None:
                            native_user = session._latest_user_message()
                            if native_user["info"]["id"] != recovery_from:
                                pending["recovery_from_message_id"] = recovery_from
                        session.announce_stock_input(pending)
                        if pending.get("recovery_from_message_id") is not None:
                            session.recovery_outcome = "handled"
                            session._overflow_user_id = None
                        session.condition.notify_all()
                        session.condition.wait_for(lambda: pending["response"] is not None or pending["cancelled"] or session.closed)
                        if pending["cancelled"] or session.closed:
                            return
                        response = pending["response"]
                    if "provider_error" in pending:
                        status, headers, payload = pending["provider_error"]
                    else:
                        payload = responses_payload(response, bool(body.get("stream")))
                        status, headers = 200, {"content-type": "text/event-stream" if body.get("stream") else "application/json"}
                send_payload(self, status, headers, payload)
            except AuxiliaryCancelled:
                return
            except (BrokenPipeError, ConnectionResetError):
                if not session.closed and not session.closing and session.compaction_summary is None:
                    session.fail("Stock closed its provider connection unexpectedly")
            except Exception as exc:
                session.record("unclassified", {"error": str(exc)}, {})
                session.fail(str(exc))
                payload = json.dumps({"error": {"message": str(exc), "type": "stock_execution_error"}}).encode()
                self.send_response(500)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
    return Handler
