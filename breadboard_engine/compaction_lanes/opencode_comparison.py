"""opencode comparison responsibility owner."""
from __future__ import annotations
import hashlib
import json
import re
import time
from typing import Any
from .opencode_scenarios import PARITY_REFERENCE
from breadboard_engine.provider.contract_wire import canonical_json
from .request_bytes import project_guarded_values, request_json_problem

def canonical(body: Any) -> bytes:
    return canonical_json([body])[1:-1].encode("utf-8")

class CreatedTimeTieOrder:
    """Guard stock message-v2.ts:740's ORDER BY time_created DESC collision.

    Native records establish the tie, message identities and BB creation order.
    Only tied slots move. The entire resulting request must otherwise match.
    Tool assistants move as a contiguous calls-then-results slice, validated
    against genuine native call IDs, names, input arguments and output.
    """

    def __init__(self, stock: dict[str, Any], bb: dict[str, Any]):
        self.groups = {}
        for message in stock.get("messages", []):
            created = message["info"].get("time", {}).get("created")
            if type(created) is int:
                self.groups.setdefault(created, []).append(message)
        self.bb_messages = bb.get("messages", [])
        self.stock_session_id = stock.get("session", {}).get("id")
        self.bb_session_id = bb.get("session", {}).get("id")

    @staticmethod
    def wire(message):
        role = message["info"].get("role")
        if role not in {"user", "assistant"}:
            return None
        parts = message.get("parts", [])
        if any(part.get("type") not in {"text", "step-start", "step-finish"} for part in parts):
            return None
        texts = [part["text"] for part in parts
                 if part.get("type") == "text" and not part.get("ignored")]
        if not texts or any(not isinstance(text, str) for text in texts):
            return None
        return {"role": role, "content": [
            {"type": "input_text" if role == "user" else "output_text", "text": text}
            for text in texts]}

    @classmethod
    def wire_slice(cls, message, inputs):
        text = cls.wire(message)
        if text is not None:
            return [text]
        if message["info"].get("role") != "assistant":
            return None
        parts = message.get("parts", [])
        if any(part.get("type") not in {"tool", "step-start", "step-finish"} for part in parts):
            return None
        tools = [part for part in parts if part.get("type") == "tool"]
        if not tools or len({part.get("callID") for part in tools}) != len(tools):
            return None
        calls, results = [], []
        for part in tools:
            state = part.get("state", {})
            if (state.get("status") != "completed" or not isinstance(state.get("input"), dict)
                    or not isinstance(state.get("output"), str)
                    or not isinstance(part.get("callID"), str) or not isinstance(part.get("tool"), str)):
                return None
            matches = [item for item in inputs if item.get("type") == "function_call"
                       and item.get("call_id") == part["callID"]]
            outputs = [item for item in inputs if item.get("type") == "function_call_output"
                       and item.get("call_id") == part["callID"]]
            if len(matches) != 1 or len(outputs) != 1:
                return None
            call, output = matches[0], outputs[0]
            if (set(call) != {"type", "call_id", "name", "arguments"}
                    or set(output) != {"type", "call_id", "output"}
                    or call["name"] != part["tool"] or not isinstance(call["arguments"], str)
                    or output["output"] != state["output"]):
                return None
            try:
                arguments = json.loads(call["arguments"])
            except json.JSONDecodeError:
                return None
            if canonical(arguments) != canonical(state["input"]):
                return None
            calls.append(call)
            results.append(output)
        return [*calls, *results]

    @staticmethod
    def positions(inputs, wire):
        return [index for index in range(len(inputs) - len(wire) + 1)
                if canonical(inputs[index:index + len(wire)]) == canonical(wire)]

    def body(self, stock: dict[str, Any], bb: dict[str, Any], *,
             raw_stock: dict[str, Any] | None = None, raw_bb: dict[str, Any] | None = None):
        if not isinstance(stock.get("input"), list) or not isinstance(bb.get("input"), list):
            return stock, []
        raw_stock = stock if raw_stock is None else raw_stock
        raw_bb = bb if raw_bb is None else raw_bb
        if (not isinstance(self.stock_session_id, str) or not self.stock_session_id
                or not isinstance(self.bb_session_id, str) or not self.bb_session_id
                or raw_stock.get("prompt_cache_key") != self.stock_session_id
                or raw_bb.get("prompt_cache_key") != self.bb_session_id):
            return stock, []
        stock_inputs = raw_stock["input"]
        bb_inputs = raw_bb["input"]
        projected = {**stock, "input": list(stock["input"])}
        used = []
        for created, group in self.groups.items():
            if len(group) < 2:
                continue
            raw_wires = [self.wire_slice(message, stock_inputs) for message in group]
            if any(wire is None for wire in raw_wires):
                continue
            positions = [self.positions(stock_inputs, wire) for wire in raw_wires]
            if any(len(matches) != 1 for matches in positions):
                continue
            starts = [matches[0] for matches in positions]
            slots = sorted(slot for start, wire in zip(starts, raw_wires)
                           for slot in range(start, start + len(wire)))
            if slots != list(range(slots[0], slots[-1] + 1)):
                continue
            wires = [stock["input"][start:start + len(wire)]
                     for start, wire in zip(starts, raw_wires)]
            candidates = []
            for wire in wires:
                matches = []
                for message in self.bb_messages:
                    raw_wire = self.wire_slice(message, bb_inputs)
                    if raw_wire is None:
                        continue
                    positions = self.positions(bb_inputs, raw_wire)
                    if len(positions) == 1:
                        start = positions[0]
                        if canonical(bb["input"][start:start + len(raw_wire)]) == canonical(wire):
                            matches.append(message)
                candidates.append(matches)
            # A replay user can share text with its original. The uniquely
            # matched native assistant's parentID identifies its actual user.
            parents = {matches[0]["info"].get("parentID")
                       for matches in candidates if len(matches) == 1}
            candidates = [matches if len(matches) == 1 else [
                message for message in matches if message["info"]["id"] in parents]
                for matches in candidates]
            if any(len(matches) != 1 for matches in candidates):
                continue
            mapped = [matches[0] for matches in candidates]
            times = [message["info"].get("time", {}).get("created") for message in mapped]
            if any(type(time) is not int for time in times) or len(set(times)) != len(times):
                continue
            ordered = sorted(zip(times, wires), key=lambda pair: pair[0])
            replacement = [item for _, wire in ordered for item in wire]
            start, end = slots[0], slots[-1] + 1
            if canonical(bb["input"][start:end]) != canonical(replacement):
                continue
            if canonical(stock["input"][start:end]) == canonical(replacement):
                continue
            projected["input"][start:end] = replacement
            used.append({"stock_created": created, "stock_message_ids": [
                message["info"]["id"] for message in group],
                "bb_message_ids": [message["info"]["id"] for message in mapped],
                "bb_created": times, "request_slots": slots})
        if used and canonical(projected) == canonical(bb):
            return projected, used
        return stock, []

class RuntimeGeneratedIDs:
    """Guard only source runtime IDs, proven by each side's genuine native API.

    Sources: background-task.ts:80-91; manager.ts:156; id/id.ts:49-74;
    compaction-context-injector/hook.ts:63-67; task-history.ts:61-73.
    Completed-list notification: manager.ts:1255-1287; internal-initiator-marker.ts:1-10.
    """
    SESSION = r"ses_[0-9a-f]{12}[0-9A-Za-z]{14}"
    BACKGROUND = r"bg_[0-9a-f]{8}"
    OUTPUT = re.compile(
        rf"Background task launched\.\n\nBackground Task ID: (?P<bg>{BACKGROUND})"
        rf"\nDescription: [^\n]+\nAgent: [^\n]+\nStatus: (?:pending|running)\n\n"
        rf'System notifies on completion\. Use `background_output` with task_id="(?P<bg_ref>{BACKGROUND})" to check\.'
        rf"\n\n<task_metadata>\nsession_id: (?P<session>{SESSION})"
        rf"\ntask_id: (?P<session_ref>{SESSION})\nbackground_task_id: (?P<bg_meta>{BACKGROUND})\n</task_metadata>"
        rf'(?:\n\nto continue: task\(session_id="(?P<session_hint>{SESSION})", prompt="\.\.\."\))?'
    )
    HISTORY = re.compile(
        rf"(- \*\*[^*\n]+\*\*(?:\[[^\]\n]+\])?\((?:pending|running|completed|error|cancelled)\)"
        rf": [^\n]+ \| session: `)({SESSION})(`)"
    )
    COMPLETED_NOTIFICATION = re.compile(
        r"<system-reminder>\n\[ALL BACKGROUND TASKS COMPLETE\]\n\n\*\*Completed:\*\*\n"
        rf"(?P<tasks>(?:- `{BACKGROUND}`: [^\n]+\n)+)"
        r'\nUse `background_output\(task_id="<id>"\)` to retrieve each result\.\n'
        r"</system-reminder>\n<!-- OMO_INTERNAL_INITIATOR -->"
    )
    COMPLETED_ROW = re.compile(rf"- `(?P<bg>{BACKGROUND})`: (?P<description>[^\n]+)\n")
    TOKENS = re.compile(rf"\b(?:{SESSION}|{BACKGROUND})\b")

    def __init__(self, stock: dict[str, Any], bb: dict[str, Any], imported_ids: list[str]):
        self.root = stock["session"]["id"]
        if bb["session"]["id"] != self.root or self.root not in imported_ids:
            raise ValueError("The deterministic imported root session IDs differ")
        self.imported = set(imported_ids)
        tasks = {side: self._tasks(evidence) for side, evidence in (("stock", stock), ("bb", bb))}
        if not tasks["stock"] or tasks["stock"].keys() != tasks["bb"].keys():
            raise ValueError("Native APIs do not report the same background call identities")
        self.tasks = tasks
        self.maps = {"stock": {}, "bb": {}}
        for index, call_id in enumerate(tasks["stock"]):
            for kind, slot in (("bg", 0), ("session", 1)):
                token = f"<runtime-{kind}-{index}>"
                for side in self.maps:
                    value = tasks[side][call_id][slot]
                    if value in self.maps[side] or value in self.imported:
                        raise ValueError("Runtime identity mapping is not a bijection")
                    self.maps[side][value] = token
        self.counts = {"stock": {}, "bb": {}}
        self.completed_notifications = {
            side: self._completed_notifications(evidence, tasks[side])
            for side, evidence in (("stock", stock), ("bb", bb))}

    def _tasks(self, evidence):
        children = {child["session"]["id"]: child["session"] for child in evidence["children"]}
        tasks = {}
        for message in evidence["messages"]:
            for part in message["parts"]:
                state = part.get("state", {})
                if part.get("type") != "tool" or part.get("tool") != "task" or state.get("input", {}).get("run_in_background") is not True:
                    continue
                matched = self.OUTPUT.fullmatch(state.get("output", ""))
                if matched is None:
                    raise ValueError("Genuine background tool output does not match its source format")
                bg, child = matched["bg"], matched["session"]
                if matched["bg_ref"] != bg or matched["bg_meta"] != bg or matched["session_ref"] != child:
                    raise ValueError("Genuine background tool output IDs are inconsistent")
                if matched["session_hint"] is not None and matched["session_hint"] != child:
                    raise ValueError("Genuine background continuation hint ID is inconsistent")
                info = children.get(child)
                if info is None or info.get("parentID") != self.root or state.get("metadata", {}).get("sessionId") != child:
                    raise ValueError("Runtime child ID has no matching genuine native API provenance")
                call_id = part["callID"]
                if call_id in tasks:
                    raise ValueError("Duplicate native background call identity")
                tasks[call_id] = (bg, child, state.get("time", {}).get("compacted") is not None)
        return tasks

    def _completed_notifications(self, evidence, tasks):
        children = {child["session"]["id"]: child for child in evidence["children"]}
        calls = {part["callID"]: part for message in evidence["messages"]
                 for part in message["parts"]
                 if part.get("type") == "tool" and part.get("tool") == "task"}
        by_background = {value[0]: (call_id, value[1]) for call_id, value in tasks.items()}
        notifications = {}
        for message in evidence["messages"]:
            if message["info"]["role"] != "user":
                continue
            for part in message["parts"]:
                text = part.get("text", "")
                matched = self.COMPLETED_NOTIFICATION.fullmatch(text) if part.get("type") == "text" else None
                if matched is None:
                    continue
                if (message["info"].get("sessionID") != self.root
                        or part.get("sessionID") != self.root
                        or part.get("messageID") != message["info"]["id"]):
                    raise ValueError("Completed notification has no matching native user provenance")
                proof = []
                for row in self.COMPLETED_ROW.finditer(matched["tasks"]):
                    task = by_background.get(row["bg"])
                    if task is None:
                        raise ValueError("Completed notification has an unproven background ID")
                    call_id, child_id = task
                    if calls[call_id]["state"]["input"].get("description") != row["description"]:
                        raise ValueError("Completed notification description does not match its native task call")
                    completions = [
                        child_message["info"]["id"] for child_message in children[child_id]["messages"]
                        if child_message["info"].get("sessionID") == child_id
                        and child_message["info"]["role"] == "assistant"
                        and child_message["info"].get("finish") == "stop"
                        and child_message["info"].get("time", {}).get("completed")
                        and not child_message["info"].get("error")
                        and not child_message["info"].get("summary")]
                    if not completions:
                        raise ValueError("Completed notification has no genuine child completion evidence")
                    proof.append({"background_id": row["bg"], "call_id": call_id,
                                  "child_id": child_id, "child_completion_ids": completions})
                if len({item["background_id"] for item in proof}) != len(proof):
                    raise ValueError("Completed notification repeats a native background ID")
                notifications[text] = {"native_user_id": message["info"]["id"], "tasks": proof}
        return notifications

    def _token(self, side, value, position):
        token = self.maps[side].get(value)
        if token is None:
            raise ValueError(f"Unmapped runtime ID at {position}: {value}")
        counts = self.counts[side]
        counts[position] = counts.get(position, 0) + 1
        return token

    def body(self, body: dict[str, Any], side: str) -> dict[str, Any]:
        # Comparison copy only: the retained raw request is never altered.
        if re.search(r"<runtime-(?:bg|session)-[0-9]+>", json.dumps(body)):
            raise ValueError("Raw request contains a reserved comparison marker")
        projected = json.loads(json.dumps(body))
        # Stock injects this notification only into the parent session (manager.ts:1359-1367);
        # each side's root is its genuine native root, checked equal in __init__.
        root_owned = body.get("prompt_cache_key") == self.root
        cache = projected.get("prompt_cache_key")
        if cache is not None and cache not in self.imported:
            if re.fullmatch(self.SESSION, cache) is None:
                raise ValueError("Delegated request has an invalid native session ID")
            projected["prompt_cache_key"] = self._token(side, cache, "delegated.prompt_cache_key")
        summary = is_summary({"json": projected})
        for index, item in enumerate(projected.get("input", [])):
            if item.get("type") == "function_call_output" and isinstance(item.get("output"), str):
                matched = self.OUTPUT.fullmatch(item["output"])
                proven = self.tasks[side].get(item.get("call_id"))
                if proven is not None and matched is None:
                    if item["output"] == "[Old tool result content cleared]" and proven[2]:
                        continue
                    raise ValueError("Native background call has malformed wire output")
                if matched is not None:
                    if proven is None or proven[:2] != (matched["bg"], matched["session"]):
                        raise ValueError("Background wire call has no matching native task provenance")
                    if matched["bg_ref"] != matched["bg"] or matched["bg_meta"] != matched["bg"] or matched["session_ref"] != matched["session"]:
                        raise ValueError("Background wire IDs do not match their source provenance")
                    if matched["session_hint"] is not None and matched["session_hint"] != matched["session"]:
                        raise ValueError("Background continuation hint ID does not match source provenance")
                    text = item["output"]
                    names = [name for name in ("bg", "bg_ref", "session", "session_ref", "bg_meta", "session_hint")
                             if matched[name] is not None]
                    for name in sorted(names, key=lambda name: matched.start(name), reverse=True):
                        text = text[:matched.start(name)] + self._token(side, matched[name], "background_output." + name) + text[matched.end(name):]
                    item["output"] = text
            if root_owned and item.get("role") == "user":
                content = item.get("content")
                blocks = [{"type": "input_text", "text": content}] if isinstance(content, str) else content
                if isinstance(blocks, list) and len(blocks) == 1 and blocks[0].get("type") == "input_text":
                    text = blocks[0].get("text", "")
                    matched = self.COMPLETED_NOTIFICATION.fullmatch(text)
                    if matched is not None:
                        if text not in self.completed_notifications[side]:
                            raise ValueError("Completed notification wire text has no native user provenance")
                        rows = list(self.COMPLETED_ROW.finditer(matched["tasks"]))
                        offset = matched.start("tasks")
                        for row in reversed(rows):
                            start, end = row.span("bg")
                            text = (text[:offset + start]
                                    + self._token(side, row["bg"], "notification.completed_list")
                                    + text[offset + end:])
                        blocks[0]["text"] = text
                        if isinstance(content, str):
                            item["content"] = text
            if summary and index == len(projected["input"]) - 1 and item.get("role") == "user":
                content = item.get("content")
                blocks = [{"text": content}] if isinstance(content, str) else content
                for block in blocks:
                    text = block.get("text", "")
                    marker = "\n### Active/Recent Delegated Sessions\n"
                    if marker not in text:
                        continue
                    head, history = text.split(marker, 1)
                    lines = []
                    for line in history.splitlines(keepends=True):
                        if not line.strip():
                            lines.append(line)
                            continue
                        matched = self.HISTORY.fullmatch(line.removesuffix("\n"))
                        if matched is None:
                            raise ValueError("Delegated history does not match its exact source format")
                        lines.append(matched[1] + self._token(side, matched[2], "summary.active_recent") + matched[3]
                                     + ("\n" if line.endswith("\n") else ""))
                    block["text"] = head + marker + "".join(lines)
                if isinstance(content, str):
                    item["content"] = blocks[0]["text"]
        for token in self.TOKENS.findall(json.dumps(projected, ensure_ascii=False)):
            if token not in self.imported:
                raise ValueError(f"Runtime ID outside an approved position or without native provenance: {token}")
        return projected

def is_summary(row: dict[str, Any]) -> bool:
    body = row["json"]
    return isinstance(body, dict) and "You are a helpful AI assistant tasked with summarizing conversations." in json.dumps(body.get("input", []))

def content_text(content: Any) -> str:
    """Read text for diagnostic classification; never rewrite compared bodies."""
    if isinstance(content, str):
        return content
    return "".join(part["text"] for part in content or [] if isinstance(part, dict) and isinstance(part.get("text"), str))

def compaction_input_findings(stock: dict[str, Any], bb: dict[str, Any], *, summary: bool, baseline_users: tuple[tuple[str, str], ...] = (), baseline_outputs: set[tuple[bytes, bytes]] | None = None) -> list[str]:
    left, right = stock.get("input", []), bb.get("input", [])
    findings = []
    if summary:
        systems = lambda items: [(item["role"], content_text(item.get("content"))) for item in items if item.get("role") in {"system", "developer"}]
        if systems(left) != systems(right):
            findings.append("summary system text differs")
        # The ordinary BB ledger interleaves calls and outputs, unlike stock's
        # grouped calls. Keep that array-order baseline in the strict diff;
        # still guard every retained call/output value and per-kind order.
        for kind in ("function_call", "function_call_output"):
            parts = lambda items: [item for item in items if item.get("type") == kind]
            stock_parts, bb_parts = parts(left), parts(right)
            if len(stock_parts) != len(bb_parts) or any(
                canonical(a) != canonical(b) and not (kind == "function_call_output" and baseline_outputs is not None and (canonical(a), canonical(b)) in baseline_outputs)
                for a, b in zip(stock_parts, bb_parts)
            ):
                findings.append(f"summary {kind} history differs")
        users = lambda items: [content_text(item.get("content")) for item in items if item.get("role") == "user"]
        stock_users, bb_users = users(left), users(right)
        if len(stock_users) != len(bb_users) or any(a != b and (a, b) not in baseline_users for a, b in zip(stock_users, bb_users)):
            findings.append("summary user history or prompt differs")
        # model_output.py:160-161 already mutates zero-tool answers before
        # compaction. Diagnose that inherited baseline rendering without ever
        # changing its strict body diff, digest, or compared bytes.
        assistants = lambda items: [content_text(item.get("content")) for item in items if item.get("role") == "assistant"]
        stock_assistants, bb_assistants = assistants(left), assistants(right)
        if len(stock_assistants) != len(bb_assistants) or any(
            candidate != text and candidate != text.rstrip() + "\n\n>>>>>> END RESPONSE"
            for text, candidate in zip(stock_assistants, bb_assistants)
        ):
            findings.append("summary assistant history differs in count, content, or order")
        masked = lambda items: sum(item.get("type") == "function_call_output" and item.get("output") == "[Old tool result content cleared]" for item in items)
        if masked(left) != masked(right):
            findings.append("summary pruned tool-output count differs")
    else:
        marker = "What did we do so far?"
        def bridge(items):
            for index, item in enumerate(items):
                if item.get("role") == "user" and content_text(item.get("content")) == marker:
                    return [(part.get("role"), content_text(part.get("content"))) for part in items[index:index + 3]]
            return None
        expected = bridge(left)
        if expected is not None and bridge(right) != expected:
            findings.append("post-compaction summary bridge differs")
    return findings

def compare_records(stock: list[dict[str, Any]], bb: list[dict[str, Any]]) -> list[dict[str, Any]]:
    diffs = []
    baseline_users = ()
    if stock and bb and not is_summary(stock[0]) and not is_summary(bb[0]):
        texts = lambda row: [content_text(item.get("content")) for item in row["json"].get("input", []) if item.get("role") == "user"]
        # Exact inherited differences already present before compaction, e.g.
        # the stock plugin's keyword-detector analysis prefix. Classify them;
        # retain the complete differing bodies and bytes without normalization.
        baseline_users = tuple((a, b) for a, b in zip(texts(stock[0]), texts(bb[0])) if a != b)
    baseline_outputs = set()
    for left, right in zip(stock, bb):
        if is_summary(left) or is_summary(right):
            break
        outputs = lambda row: {item["call_id"]: item for item in row["json"].get("input", []) if item.get("type") == "function_call_output"}
        a, b = outputs(left), outputs(right)
        # The first tool-bearing request precedes user-turn pruning in these
        # scenarios. Capture only that exact inherited output difference
        # (e.g. the plugin's category-skill-reminder), never later pruning.
        if a or b:
            baseline_outputs.update((canonical(a[key]), canonical(b[key])) for key in a.keys() & b.keys() if canonical(a[key]) != canonical(b[key]))
            break
    if len(stock) != len(bb):
        diffs.append({"kind": "request_count", "classification": "compaction-path", "stock": len(stock), "bb": len(bb)})
    for index, (left, right) in enumerate(zip(stock, bb)):
        problem = request_json_problem(left, right, index)
        if problem is not None:
            diffs.append(problem)
            continue
        raw_left = bytes.fromhex(left["raw_body_bytes"])
        raw_right = bytes.fromhex(right["raw_body_bytes"])
        if project_guarded_values(raw_left, left["json"]) == project_guarded_values(raw_right, right["json"]) and left["path"] == right["path"] and left.get("method") == right.get("method"):
            continue
        summary = is_summary(left) or is_summary(right)
        findings = compaction_input_findings(left["json"], right["json"], summary=summary, baseline_users=baseline_users, baseline_outputs=baseline_outputs)
        keys = sorted(set(left["json"]) | set(right["json"]))
        fields = []
        for key in keys:
            if key not in left["json"] or key not in right["json"] or canonical(left["json"][key]) != canonical(right["json"][key]):
                classification = "compaction-path" if summary and key in {"tools", "tool_choice", "max_output_tokens", "stream"} else "pre-existing baseline"
                if key == "input" and findings:
                    classification = "compaction-path"
                fields.append({"field": key, "classification": classification, "stock_present": key in left["json"], "bb_present": key in right["json"], "baseline_reference": PARITY_REFERENCE if classification == "pre-existing baseline" else None})
        diffs.append({"kind": "request_body", "index": index, "stock_summary": is_summary(left), "bb_summary": is_summary(right), "fields": fields, "compaction_input_findings": findings, "inherited_user_baseline_pairs": baseline_users if summary else (),
                      "stock_raw_sha256": hashlib.sha256(bytes.fromhex(left["raw_body_bytes"])).hexdigest(), "bb_raw_sha256": hashlib.sha256(bytes.fromhex(right["raw_body_bytes"])).hexdigest()})
    return diffs

def stock_control_flow_ties(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Select the oracle from native records, never request bytes or counts.

    A tie involving a native summary or compaction part can alter boundary
    reconstruction/control flow (message-v2.ts:740). Ordinary text replay
    ties use only the separately guarded request-order deviation.
    """
    ties = []
    sessions = [(evidence.get("session", {}).get("id"), evidence.get("messages", []))]
    sessions.extend((child["session"]["id"], child.get("messages", []))
                    for child in evidence.get("children", []))
    for session_id, messages in sessions:
        groups = {}
        for message in messages:
            created = message["info"].get("time", {}).get("created")
            if type(created) is int:
                groups.setdefault(created, []).append(message)
        for created, group in groups.items():
            if len(group) > 1 and any(
                message["info"].get("role") == "assistant" and message["info"].get("summary") is True
                or any(part.get("type") == "compaction" for part in message.get("parts", []))
                for message in group
            ):
                ties.append({"session_id": session_id, "time_created": created,
                             "message_ids": [message["info"]["id"] for message in group]})
    return ties
