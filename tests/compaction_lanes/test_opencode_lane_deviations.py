"""OpenCode lane deviations behavioral regressions."""
from __future__ import annotations
import json
import pytest
from breadboard_engine.compaction_lanes.opencode_comparison import canonical, compare_records
from .opencode_test_support import _add_completed_notification, _completed_notification_guard_fixture, _runtime_id_api_fixture, _runtime_id_guard_fixture, _time_tie_fixture, _tool_time_tie_fixture, record

def test_runtime_id_deviation_guards_provenance_and_counts_exact_positions():
    from breadboard_engine.compaction_lanes.opencode_comparison import canonical
    guard, stock, bb = _runtime_id_guard_fixture()
    projected = []
    for side, evidence in (("stock", stock), ("bb", bb)):
        child = evidence["children"][0]["session"]["id"]
        body = {"model": "test", "prompt_cache_key": evidence["session"]["id"], "input": [
            {"role": "developer", "content": "You are a helpful AI assistant tasked with summarizing conversations."},
            {"type": "function_call_output", "call_id": "call_delegated",
             "output": evidence["messages"][0]["parts"][0]["state"]["output"]},
            {"role": "user", "content": [{"type": "input_text", "text":
                "Instruction\n### Active/Recent Delegated Sessions\n"
                f"- **explore**(running): Delegated inspection | session: `{child}`\n"}]}]}
        before = canonical(body)
        projected.append(guard.body(body, side))
        assert canonical(body) == before
        assert guard.body({"prompt_cache_key": child, "input": []}, side)["prompt_cache_key"] == "<runtime-session-0>"
    assert canonical(projected[0]) == canonical(projected[1])
    assert guard.counts["stock"] == guard.counts["bb"] == {
        "background_output.bg_meta": 1, "background_output.session_ref": 1,
        "background_output.session": 1, "background_output.bg_ref": 1,
        "background_output.bg": 1, "summary.active_recent": 1, "delegated.prompt_cache_key": 1}

def test_runtime_id_deviation_rejects_mismatched_native_api_mapping():
    from breadboard_engine.compaction_lanes.opencode_comparison import RuntimeGeneratedIDs
    _, stock, bb = _runtime_id_guard_fixture()
    bb["messages"][0]["parts"][0]["state"]["metadata"]["sessionId"] = stock["children"][0]["session"]["id"]
    with pytest.raises(ValueError, match="provenance"):
        RuntimeGeneratedIDs(stock, bb, [stock["session"]["id"]])

def test_runtime_id_deviation_rejects_ids_outside_approved_positions():
    guard, stock, _ = _runtime_id_guard_fixture()
    child = stock["children"][0]["session"]["id"]
    with pytest.raises(ValueError, match="outside an approved position"):
        guard.body({"prompt_cache_key": stock["session"]["id"],
                    "input": [{"role": "user", "content": child}]}, "stock")

def test_runtime_id_deviation_rejects_root_id_difference():
    from breadboard_engine.compaction_lanes.opencode_comparison import RuntimeGeneratedIDs
    _, stock, bb = _runtime_id_guard_fixture()
    bb["session"]["id"] = "ses_" + "c" * 12 + "D" * 14
    with pytest.raises(ValueError, match="root session IDs differ"):
        RuntimeGeneratedIDs(stock, bb, [stock["session"]["id"]])

def test_runtime_id_deviation_rejects_regex_only_identifier():
    guard, _, _ = _runtime_id_guard_fixture()
    with pytest.raises(ValueError, match="Unmapped runtime ID"):
        guard.body({"prompt_cache_key": "ses_" + "d" * 12 + "E" * 14, "input": []}, "stock")

def test_runtime_id_deviation_guards_source_continuation_hint_and_counts():
    from breadboard_engine.compaction_lanes.opencode_comparison import RuntimeGeneratedIDs
    _, stock, bb = _runtime_id_guard_fixture()
    for evidence in (stock, bb):
        child = evidence["children"][0]["session"]["id"]
        evidence["messages"][0]["parts"][0]["state"]["output"] += (
            f'\n\nto continue: task(session_id="{child}", prompt="...")')
    guard = RuntimeGeneratedIDs(stock, bb, [stock["session"]["id"]])
    projected = []
    for side, evidence in (("stock", stock), ("bb", bb)):
        projected.append(guard.body({"input": [{"type": "function_call_output", "call_id": "call_delegated",
            "output": evidence["messages"][0]["parts"][0]["state"]["output"]}]}, side))
    assert projected[0] == projected[1]
    assert guard.counts["stock"]["background_output.session_hint"] == 1
    assert guard.counts["stock"] == guard.counts["bb"]

def test_runtime_id_deviation_rejects_wrong_source_continuation_hint():
    from breadboard_engine.compaction_lanes.opencode_comparison import RuntimeGeneratedIDs
    _, stock, bb = _runtime_id_guard_fixture()
    wrong = stock["children"][0]["session"]["id"]
    bb["messages"][0]["parts"][0]["state"]["output"] += (
        f'\n\nto continue: task(session_id="{wrong}", prompt="...")')
    with pytest.raises(ValueError, match="continuation hint"):
        RuntimeGeneratedIDs(stock, bb, [stock["session"]["id"]])

def test_created_time_tie_order_uses_native_tie_and_distinct_bb_times():
    from breadboard_engine.compaction_lanes.opencode_comparison import CreatedTimeTieOrder
    stock, bb, left, right = _time_tie_fixture()
    projected, groups = CreatedTimeTieOrder(stock, bb).body(left, right)
    assert projected == right
    assert left["input"][1]["role"] == "assistant"
    assert groups == [{"stock_created": 10, "stock_message_ids": ["sa", "su"],
                       "bb_message_ids": ["ba", "bu"], "bb_created": [21, 20],
                       "request_slots": [1, 2]}]

@pytest.mark.parametrize("failure", ["distinct_stock_times", "no_evidence", "content",
                                    "other_field", "tied_bb_times", "wrong_bb_order"])
def test_created_time_tie_order_rejects_unproven_swaps_or_any_other_difference(failure):
    from breadboard_engine.compaction_lanes.opencode_comparison import CreatedTimeTieOrder
    stock, bb, left, right = _time_tie_fixture()
    if failure == "distinct_stock_times":
        stock["messages"][1]["info"]["time"]["created"] = 9
    elif failure == "no_evidence":
        stock["messages"] = []
    elif failure == "content":
        right["input"][1]["content"][0]["text"] += " changed"
    elif failure == "other_field":
        right["store"] = True
    elif failure == "tied_bb_times":
        bb["messages"][1]["info"]["time"]["created"] = 20
    else:
        bb["messages"][0]["info"]["time"]["created"] = 22
    projected, groups = CreatedTimeTieOrder(stock, bb).body(left, right)
    assert projected is left
    assert groups == []
    assert compare_records([record(projected)], [record(right)])

def test_created_time_tie_order_identifies_replay_user_by_genuine_assistant_parent():
    import copy
    from breadboard_engine.compaction_lanes.opencode_comparison import CreatedTimeTieOrder
    stock, bb, left, right = _time_tie_fixture()
    original = copy.deepcopy(bb["messages"][0])
    original["info"].update(id="original-user", time={"created": 1})
    bb["messages"].insert(0, original)
    projected, groups = CreatedTimeTieOrder(stock, bb).body(left, right)
    assert projected == right
    assert groups[0]["bb_message_ids"] == ["ba", "bu"]

@pytest.mark.parametrize("tool_count", [1, 2])
def test_created_time_tie_order_moves_genuine_contiguous_tool_slice_as_one_unit(tool_count):
    from breadboard_engine.compaction_lanes.opencode_comparison import CreatedTimeTieOrder
    stock, bb, left, right = _tool_time_tie_fixture(tool_count)
    projected, groups = CreatedTimeTieOrder(stock, bb).body(left, right)
    assert projected == right
    assert left["input"][1]["type"] == "function_call"
    assert groups == [{"stock_created": 10, "stock_message_ids": ["sa", "su"],
                       "bb_message_ids": ["ba", "bu"], "bb_created": [21, 20],
                       "request_slots": list(range(1, 2 * tool_count + 2))}]

@pytest.mark.parametrize("failure", [
    "split_stock_slice", "split_bb_slice", "call_id", "output", "arguments",
    "malformed_stock_arguments", "malformed_bb_arguments",
    "tool_name", "no_evidence", "non_tied_stock", "non_tied_bb_order", "other_field",
])
def test_created_time_tie_order_rejects_unproven_tool_slices(failure):
    from breadboard_engine.compaction_lanes.opencode_comparison import CreatedTimeTieOrder
    stock, bb, left, right = _tool_time_tie_fixture()
    if failure == "split_stock_slice":
        left["input"][2], left["input"][3] = left["input"][3], left["input"][2]
    elif failure == "split_bb_slice":
        right["input"][1], right["input"][2] = right["input"][2], right["input"][1]
    elif failure == "call_id":
        right["input"][3]["call_id"] = "unproven-call"
    elif failure == "output":
        right["input"][3]["output"] = "different"
    elif failure == "arguments":
        right["input"][2]["arguments"] = '{"command":"different"}'
    elif failure == "malformed_stock_arguments":
        left["input"][1]["arguments"] = '{"command":'
    elif failure == "malformed_bb_arguments":
        right["input"][2]["arguments"] = '{"command":'
    elif failure == "tool_name":
        right["input"][2]["name"] = "unproven-tool"
    elif failure == "no_evidence":
        stock["messages"] = []
    elif failure == "non_tied_stock":
        stock["messages"][1]["info"]["time"]["created"] = 9
    elif failure == "non_tied_bb_order":
        bb["messages"][0]["info"]["time"]["created"] = 22
    else:
        right["store"] = True
    projected, groups = CreatedTimeTieOrder(stock, bb).body(left, right)
    assert projected is left
    assert groups == []
    assert compare_records([record(projected)], [record(right)])

def test_created_time_tie_order_cannot_cross_a_non_tied_native_message():
    from breadboard_engine.compaction_lanes.opencode_comparison import CreatedTimeTieOrder
    stock, bb, left, right = _time_tie_fixture()
    untied = {"role": "user", "content": [{"type": "input_text", "text": "Not in the tie"}]}
    left["input"].insert(2, untied)
    right["input"].insert(2, untied)
    stock["messages"].append({"info": {"id": "other", "role": "user", "time": {"created": 11}},
                              "parts": [{"type": "text", "text": "Not in the tie"}]})
    projected, groups = CreatedTimeTieOrder(stock, bb).body(left, right)
    assert projected is left
    assert groups == []

def test_created_time_tie_order_validates_raw_native_slices_before_admitted_id_projection():
    import copy
    from breadboard_engine.compaction_lanes.opencode_comparison import CreatedTimeTieOrder
    stock, bb, raw_left, raw_right = _tool_time_tie_fixture()
    raw_left["input"][2]["output"] = "stock runtime ID"
    stock["messages"][0]["parts"][0]["state"]["output"] = "stock runtime ID"
    raw_right["input"][3]["output"] = "BB runtime ID"
    bb["messages"][1]["parts"][0]["state"]["output"] = "BB runtime ID"
    left, right = copy.deepcopy(raw_left), copy.deepcopy(raw_right)
    left["input"][2]["output"] = right["input"][3]["output"] = "<already-admitted-runtime-ID>"
    projected, groups = CreatedTimeTieOrder(stock, bb).body(
        left, right, raw_stock=raw_left, raw_bb=raw_right)
    assert projected == right
    assert len(groups) == 1

@pytest.mark.parametrize("failure", [
    "colliding_child", "missing_stock_owner", "missing_bb_owner",
    "wrong_stock_owner", "wrong_bb_owner", "missing_stock_session", "missing_bb_session",
])
def test_created_time_tie_order_cannot_borrow_another_sessions_native_tie(failure):
    import copy
    from breadboard_engine.compaction_lanes.opencode_comparison import CreatedTimeTieOrder
    stock, bb, raw_left, raw_right = _tool_time_tie_fixture()
    if failure == "colliding_child":
        for evidence, body, child_id in (
            (stock, raw_left, "stock-child"), (bb, raw_right, "bb-child"),
        ):
            messages = copy.deepcopy(evidence["messages"])
            for index, message in enumerate(messages):
                message["info"]["id"] = f"{child_id}-{index}"
                message["info"]["sessionID"] = child_id
                message["info"]["time"]["created"] = 100 + index
            child_user = next(message["info"]["id"] for message in messages
                              if message["info"]["role"] == "user")
            for message in messages:
                if message["info"]["role"] == "assistant":
                    message["info"]["parentID"] = child_user
            evidence["children"] = [{"session": {"id": child_id, "parentID": "root-session"},
                                     "messages": messages}]
            body["prompt_cache_key"] = child_id
        left, right = copy.deepcopy(raw_left), copy.deepcopy(raw_right)
        left["prompt_cache_key"] = right["prompt_cache_key"] = "<already-admitted-child-id>"
    else:
        evidence = stock if "stock" in failure else bb
        body = raw_left if "stock" in failure else raw_right
        if "missing" in failure:
            (evidence if "session" in failure else body).pop(
                "session" if "session" in failure else "prompt_cache_key")
        else:
            body["prompt_cache_key"] = "unproven-owner"
        left, right = copy.deepcopy(raw_left), copy.deepcopy(raw_right)
    projected, groups = CreatedTimeTieOrder(stock, bb).body(
        left, right, raw_stock=raw_left, raw_bb=raw_right)
    assert projected is left
    assert groups == []
    assert compare_records([record(projected)], [record(right)])

@pytest.mark.parametrize("call_id", ["call_bash", None])
def test_runtime_id_deviation_rejects_launch_text_from_unproven_wire_call(call_id):
    guard, stock, _ = _runtime_id_guard_fixture()
    body = {"input": [{"type": "function_call_output", "call_id": call_id,
                       "output": stock["messages"][0]["parts"][0]["state"]["output"]}]}
    with pytest.raises(ValueError, match="native task provenance"):
        guard.body(body, "stock")

def test_runtime_id_deviation_rejects_other_proven_tasks_id_pair():
    from breadboard_engine.compaction_lanes.opencode_comparison import RuntimeGeneratedIDs
    _, stock, bb = _runtime_id_guard_fixture()
    for evidence, background, child in (
        (stock, "bg_22222222", "ses_" + "2" * 12 + "D" * 14),
        (bb, "bg_33333333", "ses_" + "3" * 12 + "E" * 14),
    ):
        other = _runtime_id_api_fixture(background, child)
        other["messages"][0]["parts"][0]["callID"] = "call_other"
        evidence["messages"] += other["messages"]
        evidence["children"] += other["children"]
    guard = RuntimeGeneratedIDs(stock, bb, [stock["session"]["id"]])
    body = {"input": [{"type": "function_call_output", "call_id": "call_delegated",
                       "output": stock["messages"][1]["parts"][0]["state"]["output"]}]}
    with pytest.raises(ValueError, match="native task provenance"):
        guard.body(body, "stock")

@pytest.mark.parametrize("output", ["malformed launch output", "<runtime-bg-0>"])
def test_runtime_id_deviation_rejects_malformed_task_output_and_raw_sentinels(output):
    guard, _, _ = _runtime_id_guard_fixture()
    body = {"input": [{"type": "function_call_output", "call_id": "call_delegated", "output": output}]}
    with pytest.raises(ValueError, match="malformed wire output|reserved comparison marker"):
        guard.body(body, "stock")

def test_runtime_id_deviation_allows_only_genuinely_pruned_task_placeholder():
    from breadboard_engine.compaction_lanes.opencode_comparison import RuntimeGeneratedIDs
    guard, stock, bb = _runtime_id_guard_fixture()
    body = {"input": [{"type": "function_call_output", "call_id": "call_delegated",
                       "output": "[Old tool result content cleared]"}]}
    with pytest.raises(ValueError, match="malformed wire output"):
        guard.body(body, "stock")
    stock["messages"][0]["parts"][0]["state"]["time"] = {"compacted": 10}
    guard = RuntimeGeneratedIDs(stock, bb, [stock["session"]["id"]])
    assert guard.body(body, "stock") == body
    assert guard.counts["stock"] == {}

def test_completed_notification_ids_require_native_user_task_and_child_completion():
    guard, stock, bb, bodies = _completed_notification_guard_fixture()
    original = canonical(bodies)
    assert canonical(guard.body(bodies["stock"], "stock")) == canonical(guard.body(bodies["bb"], "bb"))
    assert canonical(bodies) == original
    assert guard.counts == {"stock": {"notification.completed_list": 1},
                            "bb": {"notification.completed_list": 1}}
    proof = next(iter(guard.completed_notifications["stock"].values()))
    assert proof["tasks"][0]["call_id"] == "call_delegated"
    assert proof["tasks"][0]["child_completion_ids"] == ["completed-bg_012345ab"]

def test_completed_notification_rejects_unproven_id():
    guard, stock, bb, bodies = _completed_notification_guard_fixture()
    body = json.loads(json.dumps(bodies["stock"]))
    body["input"][0]["content"][0]["text"] = body["input"][0]["content"][0]["text"].replace(
        "bg_012345ab", "bg_99999999")
    with pytest.raises(ValueError, match="native user provenance"):
        guard.body(body, "stock")

@pytest.mark.parametrize("owner,error", [
    ("child", "outside an approved position"),
    ("missing", "outside an approved position"),
    ("wrong", "Unmapped runtime ID"),
])
def test_completed_notification_on_non_root_request_is_not_substituted(owner, error):
    # Stock injects the notification only into the parent session (manager.ts:1359-1367).
    guard, stock, bb, bodies = _completed_notification_guard_fixture()
    other = {"stock": bb, "bb": stock}
    owned = {}
    for side, evidence in (("stock", stock), ("bb", bb)):
        owned[side] = {key: value for key, value in bodies[side].items() if key != "prompt_cache_key"}
        if owner == "child":
            owned[side]["prompt_cache_key"] = evidence["children"][0]["session"]["id"]
        elif owner == "wrong":
            owned[side]["prompt_cache_key"] = other[side]["children"][0]["session"]["id"]
        with pytest.raises(ValueError, match=error):
            guard.body(owned[side], side)
    assert "notification.completed_list" not in guard.counts["stock"]
    assert "notification.completed_list" not in guard.counts["bb"]
    # The lane falls back to the raw records on a guard error, so the difference stays strict.
    assert compare_records([record(owned["stock"])], [record(owned["bb"])])

@pytest.mark.parametrize("position", ["assistant", "developer", "instructions", "tool_output"])
def test_completed_notification_rejects_wrong_wire_position(position):
    guard, stock, bb, bodies = _completed_notification_guard_fixture()
    body = json.loads(json.dumps(bodies["stock"]))
    if position in {"assistant", "developer"}:
        body["input"][0]["role"] = position
    elif position == "instructions":
        body = {"instructions": body["input"][0]["content"][0]["text"], "input": []}
    else:
        body = {"input": [{"type": "function_call_output", "call_id": "other-call",
                           "output": body["input"][0]["content"][0]["text"]}]}
    with pytest.raises(ValueError, match="outside an approved position"):
        guard.body(body, "stock")

def test_completed_notification_requires_actual_child_completion():
    from breadboard_engine.compaction_lanes.opencode_comparison import RuntimeGeneratedIDs
    guard, stock, bb, bodies = _completed_notification_guard_fixture()
    bb["children"][0]["messages"][0]["info"].pop("finish")
    with pytest.raises(ValueError, match="child completion evidence"):
        RuntimeGeneratedIDs(stock, bb, [stock["session"]["id"]])

def test_completed_notification_mapping_mismatch_remains_a_request_difference():
    from breadboard_engine.compaction_lanes.opencode_comparison import RuntimeGeneratedIDs
    guard, stock, bb, bodies = _completed_notification_guard_fixture()
    for evidence, background, child_id in (
        (stock, "bg_22222222", "ses_" + "c" * 12 + "D" * 14),
        (bb, "bg_33333333", "ses_" + "d" * 12 + "E" * 14),
    ):
        second = _runtime_id_api_fixture(background, child_id)
        part = second["messages"][0]["parts"][0]
        part["callID"] = "call_second"
        evidence["messages"][0]["parts"].append(part)
        evidence["children"].extend(second["children"])
        second_body = _add_completed_notification(evidence, background)
        if evidence is bb:
            bodies["bb"] = second_body
    guard = RuntimeGeneratedIDs(stock, bb, [stock["session"]["id"]])
    assert canonical(guard.body(bodies["stock"], "stock")) != canonical(guard.body(bodies["bb"], "bb"))
