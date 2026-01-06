from __future__ import annotations

import json
import re
import shlex
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class PermissionRequest:
    """Normalized permission request for a tool invocation."""

    category: str
    pattern: str
    metadata: Dict[str, Any]


class PermissionDeniedError(RuntimeError):
    """Raised when a tool call violates a hard permission policy."""


class PermissionRequestTimeoutError(RuntimeError):
    """Raised when an interactive permission request times out."""


class PermissionBroker:
    """Permission broker that mirrors OpenCode's ask/allow batching semantics."""

    STATE_KEY = "permission_broker_state"

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        raw = config or {}
        self._options = self._extract_options(raw)
        self._ask_mode = str(self._options.get("mode") or self._options.get("ask_mode") or "auto_allow").strip().lower()
        self._auto_allow = self._ask_mode in {"auto", "auto_allow", "allow", "yes", "true", "1"}
        self._decision_timeout_s = self._coerce_timeout(self._options.get("timeout_s") or self._options.get("timeout"))
        self._default_response = self._coerce_response(self._options.get("default_response") or self._options.get("default"))
        self._rules = self._normalize_config(raw)

    @staticmethod
    def _extract_options(raw: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        for key in ("options", "_options", "__options"):
            val = raw.get(key)
            if isinstance(val, dict):
                return dict(val)
        return {}

    @staticmethod
    def _coerce_timeout(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            timeout = float(value)
        except Exception:
            return None
        if timeout <= 0:
            return None
        return timeout

    @staticmethod
    def _coerce_response(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text in {"allow", "approve", "approved", "ok", "okay", "yes", "y"}:
            return "once"
        if text in {"deny", "denied", "no", "n"}:
            return "reject"
        if text in {"once", "always", "reject"}:
            return text
        return "reject"

    @staticmethod
    def _wildcard_match(value: str, pattern: str) -> bool:
        """
        OpenCode-style wildcard match:
        - only `*` and `?` are special
        - everything else is treated literally
        - DOTALL so `*` can span newlines
        """
        pat = str(pattern or "")
        text = str(value or "")
        try:
            escaped = re.escape(pat)
            escaped = escaped.replace(r"\*", ".*").replace(r"\?", ".")
            return re.match("^" + escaped + "$", text, flags=re.S) is not None
        except Exception:
            return False

    @classmethod
    def _wildcard_all(cls, value: str, patterns: Dict[str, Any]) -> Optional[Any]:
        """Return the value for the most-specific matching wildcard pattern."""
        ordered = sorted(((k, v) for k, v in (patterns or {}).items()), key=lambda item: (len(item[0]), item[0]))
        result = None
        for key, action in ordered:
            if cls._wildcard_match(value, key):
                result = action
        return result

    @classmethod
    def _wildcard_all_structured(cls, *, head: str, tail: List[str], patterns: Dict[str, Any]) -> Optional[Any]:
        ordered = sorted(((k, v) for k, v in (patterns or {}).items()), key=lambda item: (len(item[0]), item[0]))
        result = None
        for key, action in ordered:
            parts = str(key).split()
            if not parts:
                continue
            if not cls._wildcard_match(head, parts[0]):
                continue
            if len(parts) == 1 or cls._wildcard_match_sequence(tail, parts[1:]):
                result = action
        return result

    @classmethod
    def _wildcard_match_sequence(cls, items: List[str], patterns: List[str]) -> bool:
        if not patterns:
            return True
        pattern, *rest = patterns
        if pattern == "*":
            return cls._wildcard_match_sequence(items, rest)
        for idx, item in enumerate(items):
            if cls._wildcard_match(item, pattern) and cls._wildcard_match_sequence(items[idx + 1 :], rest):
                return True
        return False

    def _pattern_map(self, category: str) -> Dict[str, str]:
        rules = self._rules.get(str(category).lower(), {}) or {}
        default = str(rules.get("default") or "allow").lower()
        patterns: Dict[str, str] = {"*": default}
        for pat in rules.get("allow", []) or []:
            text = str(pat).strip()
            if text:
                patterns[text] = "allow"
        for pat in rules.get("ask", []) or []:
            text = str(pat).strip()
            if text:
                patterns[text] = "ask"
        for pat in rules.get("deny", []) or []:
            text = str(pat).strip()
            if text:
                patterns[text] = "deny"
        return patterns

    def disabled_tool_names(self) -> set[str]:
        """
        Compute a permission-derived tool visibility mask.

        Mirrors OpenCode's `ToolRegistry.enabled` behavior at the coarse level:
        - if edit is fully denied -> hide mutating file tools
        - if shell is fully denied -> hide bash/shell tools
        - if webfetch is fully denied -> hide webfetch
        """

        def _fully_denied(category: str) -> bool:
            patterns = self._pattern_map(category)
            return set(patterns) == {"*"} and patterns.get("*") == "deny"

        disabled: set[str] = set()
        if _fully_denied("edit"):
            disabled.update(
                {
                    "edit",
                    "write",
                    "patch",
                    "apply_patch",
                    "apply_unified_patch",
                    "apply_search_replace",
                    "apply_unified_diff",
                    "create_file",
                    "create_file_from_block",
                    "multiedit",
                    "edit_replace",
                    "write_text",
                }
            )
        if _fully_denied("shell"):
            disabled.update({"bash", "run_shell", "shell_command"})
        if _fully_denied("webfetch"):
            disabled.add("webfetch")
        return disabled

    def auto_allow(self) -> bool:
        return bool(self._auto_allow)

    def decide(self, call: Any) -> Optional[str]:
        request = self._build_request(call)
        if not request:
            return None
        return self._resolve_action(request)

    def ensure_allowed(self, session_state, parsed_calls: Iterable[Any]) -> None:
        if not parsed_calls:
            return
        pending_prompts: List[PermissionRequest] = []
        for call in parsed_calls:
            request = self._build_request(call)
            if not request:
                continue
            action = self._resolve_action(request)
            if action == "deny":
                self._log_event(session_state, request, status="denied", note="config_deny")
                message = (
                    "<VALIDATION_ERROR>\n"
                    f"{request.category.title()} pattern '{request.pattern}' is denied by the permission policy.\n"
                    "</VALIDATION_ERROR>"
                )
                raise PermissionDeniedError(message)
            if self._already_approved(session_state, request):
                continue
            if action == "allow":
                self._record_approval(session_state, request, mode="config_allow")
                continue
            if action == "ask":
                if self._auto_allow:
                    self._record_approval(session_state, request, mode="auto_allow")
                    self._log_event(session_state, request, status="ask", note="auto_allow")
                    continue
                pending_prompts.append(request)
                continue
            if action not in {"allow", "deny"} and self._auto_allow:
                self._record_approval(session_state, request, mode="auto_allow")
                self._log_event(session_state, request, status="ask", note="auto_allow")
                continue
            # Default fallthrough: record as informational without blocking
            self._record_approval(session_state, request, mode="implicit")

        if pending_prompts:
            for req, decision in self._request_permissions(session_state, pending_prompts):
                if decision == "always":
                    self._record_approval(session_state, req, mode="user_allow_always")
                elif decision == "once":
                    self._log_event(session_state, req, status="approved", note="user_allow_once")
                else:
                    self._log_event(session_state, req, status="denied", note="user_reject")
                    raise PermissionDeniedError(
                        "The user rejected permission to use this specific tool call. You may try again with different parameters."
                    )

    def _permission_state(self, session_state) -> Dict[str, Any]:
        state = session_state.get_provider_metadata(self.STATE_KEY, {})
        if not isinstance(state, dict):
            state = {}
        if not isinstance(state.get("approved"), dict):
            state["approved"] = {}
        if not isinstance(state.get("pending"), dict):
            state["pending"] = {}
        try:
            state["counter"] = int(state.get("counter") or 0)
        except Exception:
            state["counter"] = 0
        return state

    def _next_permission_id(self, session_state) -> str:
        state = self._permission_state(session_state)
        counter = int(state.get("counter") or 0) + 1
        state["counter"] = counter
        session_state.set_provider_metadata(self.STATE_KEY, state)
        return f"permission_{counter}"

    def _queue_get(self, queue: Any, *, timeout_s: Optional[float]) -> Any:
        if queue is None:
            raise RuntimeError("permission queue missing")
        if timeout_s is None:
            return queue.get()
        try:
            return queue.get(timeout=timeout_s)
        except TypeError:
            try:
                return queue.get(True, timeout_s)
            except TypeError:
                return queue.get()

    def _emit_permission_event(self, session_state, event_type: str, payload: Dict[str, Any]) -> None:
        try:
            emit_any = getattr(session_state, "emit_runtime_event", None)
            if callable(emit_any):
                emit_any(event_type, payload)
                return
            emit = getattr(session_state, "emit_permission_event", None)
            if callable(emit):
                emit(event_type, payload)
                return
        except Exception:
            pass
        try:
            raw_emit = getattr(session_state, "_emit_event", None)
            if callable(raw_emit):
                turn = getattr(session_state, "_active_turn_index", None)
                raw_emit(str(event_type), dict(payload or {}), turn=turn if isinstance(turn, int) else None)
        except Exception:
            pass

    def _derive_shell_approval_pattern(self, request: PermissionRequest) -> Optional[str]:
        if request.category != "shell":
            return None
        meta = request.metadata if isinstance(request.metadata, dict) else {}
        argv = meta.get("argv")
        if not isinstance(argv, list) or not argv:
            return None
        head = str(argv[0] or "").strip()
        if not head:
            return None
        sub = None
        for arg in argv[1:]:
            text = str(arg or "")
            if text and not text.startswith("-"):
                sub = text
                break
        if sub:
            return f"{head} {sub} *"
        return f"{head} *"

    def _request_permissions(self, session_state, requests: List[PermissionRequest]) -> List[tuple[PermissionRequest, str]]:
        request_id = self._next_permission_id(session_state)

        items: List[Dict[str, Any]] = []
        item_ids: List[str] = []
        for idx, req in enumerate(requests, start=1):
            approval_pattern = req.pattern
            derived = self._derive_shell_approval_pattern(req)
            metadata = dict(req.metadata or {})
            if derived:
                approval_pattern = derived
                metadata["approval_pattern"] = derived
            req.metadata = metadata
            item_id = f"{request_id}_item_{idx}"
            item_ids.append(item_id)
            items.append(
                {
                    "item_id": item_id,
                    "category": req.category,
                    "pattern": approval_pattern,
                    "metadata": dict(metadata or {}),
                }
            )

        payload: Dict[str, Any] = {
            "event_version": 1,
            "request_id": request_id,
            # Legacy key used by older clients that only understand a single id.
            "id": request_id,
            "items": items,
        }
        if items:
            payload.setdefault("category", items[0].get("category"))
            payload.setdefault("pattern", items[0].get("pattern"))
            base_meta = dict(items[0].get("metadata") or {})
            base_meta.setdefault("batch_size", len(items))
            payload.setdefault("metadata", base_meta)

        state = self._permission_state(session_state)
        pending = state.get("pending") if isinstance(state.get("pending"), dict) else {}
        pending[request_id] = payload
        state["pending"] = pending
        session_state.set_provider_metadata(self.STATE_KEY, state)

        self._emit_permission_event(session_state, "permission_request", payload)
        for req in requests:
            self._log_event(session_state, req, status="ask", note="prompt")

        queue = session_state.get_provider_metadata("permission_queue")
        response_map: Dict[str, str] = {}

        try:
            if queue is None:
                response_map = {item_id: self._default_response for item_id in item_ids}
            else:
                deadline = self._decision_timeout_s
                end_at = time.time() + float(deadline) if deadline is not None else None
                while True:
                    remaining = None
                    if end_at is not None:
                        remaining = max(0.0, end_at - time.time())
                        if remaining <= 0:
                            raise PermissionRequestTimeoutError("permission request timed out")
                    item = self._queue_get(queue, timeout_s=remaining)
                    parsed = self._parse_permission_response_item(item, request_id=request_id, item_ids=item_ids)
                    if parsed is None:
                        continue
                    response_map = parsed
                    break
        finally:
            # Always clear pending requests so reconnecting UIs don't get stuck.
            state = self._permission_state(session_state)
            pending = state.get("pending") if isinstance(state.get("pending"), dict) else {}
            pending.pop(request_id, None)
            state["pending"] = pending
            session_state.set_provider_metadata(self.STATE_KEY, state)

        response_payload = {
            "event_version": 1,
            "request_id": request_id,
            "id": request_id,
            "responses": {"items": dict(response_map)},
        }
        uniform = set(response_map.values())
        if len(uniform) == 1:
            decision = next(iter(uniform))
            response_payload["response"] = decision
        if items:
            response_payload.setdefault("category", items[0].get("category"))
            response_payload.setdefault("pattern", items[0].get("pattern"))
        self._emit_permission_event(session_state, "permission_response", response_payload)

        results: List[tuple[PermissionRequest, str]] = []
        for idx, req in enumerate(requests, start=1):
            item_id = f"{request_id}_item_{idx}"
            results.append((req, response_map.get(item_id, self._default_response)))
        return results

    def _parse_permission_response_item(
        self,
        item: Any,
        *,
        request_id: str,
        item_ids: List[str],
    ) -> Optional[Dict[str, str]]:
        if isinstance(item, str):
            decision = self._coerce_response(item)
            return {iid: decision for iid in item_ids}
        if not isinstance(item, dict):
            return None

        raw_id = (
            item.get("request_id")
            or item.get("requestId")
            or item.get("permission_id")
            or item.get("permissionId")
            or item.get("id")
        )
        if raw_id and str(raw_id) != request_id:
            return None

        responses = item.get("responses")
        if isinstance(responses, dict):
            if "default" in responses:
                decision = self._coerce_response(responses.get("default"))
                return {iid: decision for iid in item_ids}
            items = responses.get("items")
            if isinstance(items, dict):
                fallback = responses.get("fallback") or responses.get("default_response")
                fallback_decision = self._coerce_response(fallback) if fallback is not None else self._default_response
                resolved: Dict[str, str] = {}
                for iid in item_ids:
                    if iid in items:
                        resolved[iid] = self._coerce_response(items.get(iid))
                    else:
                        resolved[iid] = fallback_decision
                return resolved
            # Treat as direct mapping of item_id -> response
            if any(str(k) in item_ids for k in responses.keys()):
                resolved = {iid: self._default_response for iid in item_ids}
                for iid in item_ids:
                    if iid in responses:
                        resolved[iid] = self._coerce_response(responses.get(iid))
                return resolved

        decision = item.get("response") or item.get("decision") or item.get("default")
        if decision is not None:
            response = self._coerce_response(decision)
            return {iid: response for iid in item_ids}
        return None

    def _normalize_config(self, raw: Dict[str, Any]) -> Dict[str, Dict[str, List[str]]]:
        rules: Dict[str, Dict[str, List[str]]] = {}
        for category, cfg in (raw or {}).items():
            if str(category).strip().lower() in {"options", "_options", "__options"}:
                continue
            if not isinstance(cfg, dict):
                continue
            name = str(category).strip().lower()
            if not name:
                continue
            rules[name] = {
                "default": str(cfg.get("default") or "allow").lower(),
                "allow": self._coerce_list(cfg.get("allow") or cfg.get("allowlist")),
                "ask": self._coerce_list(cfg.get("ask") or cfg.get("asklist")),
                "deny": self._coerce_list(cfg.get("deny") or cfg.get("denylist")),
            }
        return rules

    def _coerce_list(self, values: Any) -> List[str]:
        if not values:
            return []
        if isinstance(values, str):
            return [values.strip()]
        result: List[str] = []
        for item in values:
            text = str(item).strip()
            if text:
                result.append(text)
        return result

    def _resolve_action(self, request: PermissionRequest) -> str:
        category = str(request.category or "").lower()
        patterns = self._pattern_map(category)
        if category == "shell":
            argv = request.metadata.get("argv") if isinstance(request.metadata, dict) else None
            if isinstance(argv, list) and argv:
                head = str(argv[0])
                tail = [str(item) for item in argv[1:]]
                action = self._wildcard_all_structured(head=head, tail=tail, patterns=patterns)
                return str(action or patterns.get("*") or "allow").lower()
        key = str(request.pattern or request.metadata.get("function") or "")
        action = self._wildcard_all(key, patterns)
        return str(action or patterns.get("*") or "allow").lower()

    def _build_request(self, call: Any) -> Optional[PermissionRequest]:
        function = self._extract_function_name(call)
        if not function:
            return None
        args = self._parse_arguments(getattr(call, "arguments", None))
        if function in {"run_shell", "bash", "shell_command"}:
            command = str(args.get("command") or "").strip()
            argv: List[str] = []
            if command:
                try:
                    argv = shlex.split(command, posix=True)
                except Exception:
                    argv = command.split()
            pattern = command or function
            metadata = {"command": command, "argv": argv, "function": function}
            return PermissionRequest(category="shell", pattern=pattern, metadata=metadata)
        if function in {"webfetch"}:
            url = str(args.get("url") or "").strip()
            metadata = {"url": url, "function": function}
            return PermissionRequest(category="webfetch", pattern=url or function, metadata=metadata)
        if function in {
            "write",
            "write_file",
            "create_file",
            "create_file_from_block",
            "apply_unified_patch",
            "apply_search_replace",
            "apply_unified_diff",
        }:
            path = str(
                args.get("path")
                or args.get("filename")
                or args.get("file_name")
                or args.get("file_path")
                or ""
            ).strip()
            metadata = {"path": path, "function": function}
            pattern = path or function
            return PermissionRequest(category="edit", pattern=pattern, metadata=metadata)
        if function.startswith("mcp.") or function.startswith("mcp__"):
            metadata = {"function": function}
            return PermissionRequest(category="mcp", pattern=function, metadata=metadata)
        if function.startswith("plugin.") or function.startswith("plugin__"):
            metadata = {"function": function}
            return PermissionRequest(category="plugin", pattern=function, metadata=metadata)
        return None

    def _extract_function_name(self, call: Any) -> str:
        if isinstance(call, dict):
            return str(call.get("function") or call.get("name") or "").lower()
        raw = getattr(call, "function", None)
        if isinstance(raw, dict):
            return str(raw.get("name") or "").lower()
        if isinstance(raw, str):
            return str(raw).lower()
        if hasattr(raw, "name"):
            return str(getattr(raw, "name")).lower()
        name = getattr(call, "name", "") or getattr(call, "type", "")
        return str(name).lower()

    def _parse_arguments(self, args: Any) -> Dict[str, Any]:
        if isinstance(args, dict):
            return dict(args)
        if isinstance(args, str):
            text = args.strip()
            if not text:
                return {}
            try:
                return json.loads(text)
            except Exception:
                return {"__raw": text}
        return {}

    def _already_approved(self, session_state, request: PermissionRequest) -> bool:
        state = session_state.get_provider_metadata(self.STATE_KEY, {})
        if not isinstance(state, dict):
            return False
        approved = state.get("approved", {})
        if not isinstance(approved, dict):
            return False
        patterns = approved.get(request.category, [])
        if not isinstance(patterns, list):
            return False
        candidate = str(request.pattern or "")
        if not candidate:
            return False
        for pat in patterns:
            text = str(pat or "")
            if not text:
                continue
            if self._wildcard_match(candidate, text):
                return True
        return False

    def _record_approval(self, session_state, request: PermissionRequest, mode: str) -> None:
        state = self._permission_state(session_state)
        approved = state.get("approved") if isinstance(state.get("approved"), dict) else {}
        patterns = approved.setdefault(request.category, [])
        if not isinstance(patterns, list):
            patterns = []
            approved[request.category] = patterns
        candidate = request.pattern
        meta = request.metadata if isinstance(request.metadata, dict) else {}
        stored = meta.get("approval_pattern")
        if isinstance(stored, str) and stored.strip():
            candidate = stored.strip()
        if candidate and candidate not in patterns:
            patterns.append(candidate)
        state["approved"] = approved
        session_state.set_provider_metadata(self.STATE_KEY, state)
        self._log_event(session_state, request, status="approved", note=mode)

    def _log_event(self, session_state, request: PermissionRequest, *, status: str, note: str) -> None:
        try:
            session_state.add_transcript_entry(
                {
                    "permission_broker": {
                        "category": request.category,
                        "pattern": request.pattern,
                        "metadata": request.metadata,
                        "status": status,
                        "note": note,
                    }
                }
            )
        except Exception:
            pass
