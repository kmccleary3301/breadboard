from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from scripts.compaction_lanes import omp16_lane as lane


def _summary_body(*, stock: bool, short: bool = False) -> dict:
    from breadboard.rl.harness.native_stream_profiles import NATIVE_STREAM_PROFILES
    system = NATIVE_STREAM_PROFILES["breadboard.oh-my-pi.v16.2.13"].compaction_summary_system_prompt
    prompt = lane.ROOT / "breadboard_engine/compaction/presets/prompts/omp@16.2.13"
    text = (prompt / ("compaction-short-summary.md" if short else "compaction-summary.md")).read_text().strip()
    body = {"model": lane.MODEL, "messages": [{"role": "system", "content": system},
            {"role": "user", "content": [{"type": "text", "text": text}]}],
            "max_completion_tokens": 512 if stock and short else 2048}
    if not stock:
        body["tools"] = [{"type": "function", "function": {"name": "read", "parameters": {"additionalProperties": False}}}]
    return body


def _normalize(body: dict, stock: bool) -> bytes:
    _, harness = lane.target_documents()
    return lane.normalize(json.dumps(body).encode(), stock=stock,
        tools=_summary_body(stock=False)["tools"], cap=2048, settings={"reserveTokens": 16384},
        deviations=[d["id"] for d in harness["policy"]["deviations"]])


@pytest.mark.parametrize("short", [False, True])
def test_admitted_summary_fields_are_value_guarded(short: bool) -> None:
    assert _normalize(_summary_body(stock=True, short=short), True) == _normalize(_summary_body(stock=False, short=short), False)


@pytest.mark.parametrize("stock,field,value", [
    (True, "tools", []),
    (False, "tools", []),
    (True, "max_completion_tokens", 100),
    (False, "max_completion_tokens", 512),
    (True, "max_completion_tokens", 2048.0),
    (False, "max_completion_tokens", 2048.0),
    (False, "max_completion_tokens", True),
])
def test_unadmitted_summary_values_are_rejected(stock: bool, field: str, value: object) -> None:
    body = _summary_body(stock=stock)
    body[field] = value
    with pytest.raises(ValueError):
        _normalize(body, stock)


def test_summary_tool_guard_rejects_boolean_as_number() -> None:
    body = _summary_body(stock=False)
    body["tools"][0]["function"]["parameters"]["additionalProperties"] = 0
    with pytest.raises(ValueError, match="BB summary tools differ from episode tools"):
        _normalize(body, False)


def test_only_object_member_order_is_canonicalized() -> None:
    body = {"messages": [{"content": "Whitespace  stays\n", "role": "user"}], "tools": ["read", "write"]}
    reversed_members = {key: body[key] for key in reversed(body)}
    assert _normalize(body, True) == _normalize(reversed_members, False)
    changed = deepcopy(body)
    changed["tools"].reverse()
    assert _normalize(body, True) != _normalize(changed, False)
    changed = deepcopy(body)
    changed["messages"][0]["content"] = "Whitespace stays\n"
    assert _normalize(body, True) != _normalize(changed, False)


@pytest.mark.skipif(not os.environ.get("OMP16213_CODING_AGENT_NODE_MODULES"), reason="Pinned OMP16 package required")
@pytest.mark.parametrize("scenario", lane.SCENARIOS)
def test_stock_sdk_matches_production_conductor(tmp_path: Path, scenario: str) -> None:
    report = lane.run_lane(tmp_path / scenario, scenario)
    assert report["diffs"] == []
    assert report["stock_requests"] == report["bb_requests"]
    assert report["remaining_stock_responses"] == report["remaining_bb_responses"] == 0
    expected = 2 if scenario == "before_request" else 1
    assert report["stock_compactions"] == report["bb_compactions"] == expected
    assert report["stock_settings"]["strategy"] == "snapcompact"
    phases = json.loads((tmp_path / scenario / "bb-result.json").read_text())["phases"]
    checkpoints = [p["payload"].get("checkpoint") for p in phases
                   if p["operation"] == "prepare_compaction" and p["result"]["kind"] == "compaction_prepared"]
    assert checkpoints.count(scenario) == expected
