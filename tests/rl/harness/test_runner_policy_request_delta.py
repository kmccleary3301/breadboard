"""Focused tests for PolicyRequestEvent request deltas, reconstruction, and compression."""

from __future__ import annotations

import base64
import copy
import dataclasses
import json
from typing import Any

import pytest

from breadboard.rl.harness.evidence import _json_value
from breadboard.rl.harness.runners.base import (
    PolicyRequestEvent,
    PolicyResponseEvent,
    RunnerTermination,
    RunnerTerminationEvent,
    apply_policy_request_delta,
    policy_request_delta,
    reconstruct_policy_requests,
)
from breadboard_engine.compilation.contracts import (
    canonical_json_bytes,
    canonical_sha256,
)


def _event_dict(event: PolicyRequestEvent) -> dict[str, Any]:
    return {
        "type": "PolicyRequestEvent",
        "sequence": event.sequence,
        "episode_id": event.episode_id,
        "effective_plan_digest": event.effective_plan_digest,
        "turn": event.turn,
        "request_digest": event.request_digest,
        "request_delta": event.request_delta,
    }


def test_synthetic_50_turn_growing_request_round_trips_and_compresses_linearly() -> None:
    """Requirement 5 (a) and (b):

    (a) For a synthetic 50-turn growing request (large repeated history),
        reconstruct_policy_requests returns every full request exactly and digests match.
    (b) The serialized size of the 50 PolicyRequestEvents grows linearly:
        assert total canonical JSON bytes of events is < 3x the final request size,
        whereas the old uncompressed encoding would be ~25x.
    """
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "You are a helpful coding assistant. " * 15}
    ]
    events: list[PolicyRequestEvent] = []
    requests: list[dict[str, Any]] = []
    prev_request: dict[str, Any] | None = None
    prev_digest: str | None = None

    plan_digest = "sha256:" + "a" * 64

    for turn in range(1, 51):
        messages = list(messages)
        messages.append(
            {"role": "user", "content": f"User prompt turn {turn}: " + "investigate detail " * 15}
        )
        messages.append(
            {
                "role": "assistant",
                "content": f"Assistant response turn {turn}: " + "explanation and findings " * 15,
            }
        )
        req = {
            "model": "gpt-5-future",
            "temperature": 0.2,
            "max_tokens": 4096,
            "tools": [
                {"name": f"tool_{i}", "description": f"Tool description {i} " * 5}
                for i in range(8)
            ],
            "messages": messages,
        }
        requests.append(req)
        event = PolicyRequestEvent.from_request(
            sequence=turn - 1,
            episode_id="ep-50-turns",
            effective_plan_digest=plan_digest,
            turn=turn,
            request=req,
            previous=prev_request,
            previous_digest=prev_digest,
        )
        events.append(event)
        prev_request = req
        prev_digest = event.request_digest

    # (a) Verify round-trip reconstruction
    reconstructed = reconstruct_policy_requests(events)
    assert len(reconstructed) == 50
    for idx, (turn, reconstructed_req) in enumerate(reconstructed):
        assert turn == idx + 1
        assert reconstructed_req == requests[idx]
        assert canonical_sha256(reconstructed_req) == events[idx].request_digest

    # (b) Verify compression: total event bytes < 3x final request bytes
    final_request_bytes = len(canonical_json_bytes(requests[-1]))
    total_event_bytes = sum(len(canonical_json_bytes(_event_dict(e))) for e in events)
    old_encoding_bytes = sum(len(canonical_json_bytes(r)) for r in requests)

    # Check ratio is < 3x
    assert total_event_bytes < 3 * final_request_bytes
    # Check old uncompressed encoding is indeed ~25x (> 20x)
    assert old_encoding_bytes > 20 * final_request_bytes


def test_tampering_delta_makes_reconstruct_raise() -> None:
    """Requirement 5 (c): tampering (changing an item in a delta) makes reconstruct raise."""
    plan_digest = "sha256:" + "b" * 64
    req1 = {"model": "m1", "messages": ["hello"]}
    req2 = {"model": "m1", "messages": ["hello", "world"]}

    event1 = PolicyRequestEvent.from_request(
        sequence=0,
        episode_id="ep-tamper",
        effective_plan_digest=plan_digest,
        turn=1,
        request=req1,
        previous=None,
        previous_digest=None,
    )
    event2 = PolicyRequestEvent.from_request(
        sequence=1,
        episode_id="ep-tamper",
        effective_plan_digest=plan_digest,
        turn=2,
        request=req2,
        previous=req1,
        previous_digest=event1.request_digest,
    )

    # Tamper with event2's delta items
    tampered_delta = {
        "base_request_digest": event2.request_delta["base_request_digest"],
        "set": dict(event2.request_delta["set"]),
        "extend": {
            "messages": {
                "keep": event2.request_delta["extend"]["messages"]["keep"],
                "items": ["tampered_world"],
            }
        },
        "splice": dict(event2.request_delta["splice"]),
        "nested": dict(event2.request_delta["nested"]),
        "remove": list(event2.request_delta["remove"]),
    }
    tampered_event2 = PolicyRequestEvent(
        sequence=event2.sequence,
        episode_id=event2.episode_id,
        effective_plan_digest=event2.effective_plan_digest,
        turn=event2.turn,
        request_digest=event2.request_digest,
        request_delta=tampered_delta,
    )

    with pytest.raises(ValueError, match="reconstructed policy request digest mismatch"):
        reconstruct_policy_requests([event1, tampered_event2])
def test_key_change_in_non_list_field_and_key_removal_round_trip() -> None:
    """Requirement 5 (d): a key change in a non-list field and a key removal round-trip correctly."""
    plan_digest = "sha256:" + "c" * 64
    req1 = {
        "model": "v1",
        "temperature": 0.5,
        "seed": 42,
        "extra_info": {"nested": "value", "count": 1},
        "history": [1, 2, 3],
    }
    # In req2:
    # - temperature changes (float, goes to set)
    # - model changes (string, goes to splice)
    # - extra_info count changes (int, goes to nested set), nested string changes (splice)
    # - key removal: "seed" removed
    # - list extends
    req2 = {
        "model": "v2",
        "temperature": 0.9,
        "extra_info": {"nested": "new_value", "count": 2},
        "history": [1, 2, 3, 4],
    }
    # In req3:
    # - another key removal: "extra_info" removed
    # - key addition: "top_p" added
    req3 = {
        "model": "v2",
        "temperature": 0.9,
        "top_p": 0.95,
        "history": [1, 2, 3, 4],
    }

    requests = [req1, req2, req3]
    events = []
    prev_req = None
    prev_dig = None
    for turn, req in enumerate(requests, start=1):
        evt = PolicyRequestEvent.from_request(
            sequence=turn - 1,
            episode_id="ep-change",
            effective_plan_digest=plan_digest,
            turn=turn,
            request=req,
            previous=prev_req,
            previous_digest=prev_dig,
        )
        events.append(evt)
        prev_req = req
        prev_dig = evt.request_digest

    # Verify event2's delta specifically
    delta2 = events[1].request_delta
    assert "seed" in delta2["remove"]
    assert delta2["splice"]["model"]["keep"] == 1
    assert delta2["splice"]["model"]["text"] == "2"
    assert delta2["set"]["temperature"] == 0.9
    assert delta2["nested"]["extra_info"]["splice"]["nested"]["keep"] == 0
    assert delta2["nested"]["extra_info"]["splice"]["nested"]["text"] == "new_value"
    assert delta2["nested"]["extra_info"]["set"]["count"] == 2
    assert delta2["extend"]["history"]["keep"] == 3
    assert delta2["extend"]["history"]["items"] == (4,)
    # Verify event3's delta specifically
    delta3 = events[2].request_delta
    assert "extra_info" in delta3["remove"]
    assert delta3["set"]["top_p"] == 0.95
    assert "history" not in delta3["extend"]  # history unchanged => omitted

    # Round trip
    reconstructed = reconstruct_policy_requests(events)
    for (turn, r), orig in zip(reconstructed, requests):
        assert r == orig


def test_list_whose_first_item_changes_keep_zero_round_trips() -> None:
    """Requirement 5 (e): a list whose first item changes (keep=0) round-trips."""
    plan_digest = "sha256:" + "d" * 64
    req1 = {"items": ["item1", "item2", "item3"], "constant": 100}
    # Completely change the first item
    req2 = {"items": ["modified_item1", "item2", "item3"], "constant": 100}
    # Also test completely replacing with a shorter list
    req3 = {"items": ["brand_new"], "constant": 100}

    requests = [req1, req2, req3]
    events = []
    prev_req = None
    prev_dig = None
    for turn, req in enumerate(requests, start=1):
        evt = PolicyRequestEvent.from_request(
            sequence=turn - 1,
            episode_id="ep-keep-zero",
            effective_plan_digest=plan_digest,
            turn=turn,
            request=req,
            previous=prev_req,
            previous_digest=prev_dig,
        )
        events.append(evt)
        prev_req = req
        prev_dig = evt.request_digest

    # Check keep == 0 for both subsequent turns
    assert events[1].request_delta["extend"]["items"]["keep"] == 0
    assert events[1].request_delta["extend"]["items"]["items"] == (
        "modified_item1",
        "item2",
        "item3",
    )
    assert events[2].request_delta["extend"]["items"]["keep"] == 0
    assert events[2].request_delta["extend"]["items"]["items"] == ("brand_new",)

    # Reconstruct and verify exact round-trip
    reconstructed = reconstruct_policy_requests(events)
    for (turn, r), orig in zip(reconstructed, requests):
        assert r == orig


def test_checkpointed_native_50_turn_growing_request_round_trips_and_compresses_linearly() -> None:
    """Test (f): 50-turn synthetic checkpointed native request with base64 body.

    native_http_request: {method, url, body_b64: base64(json.dumps({messages, tools}))}
    round-trips and total serialized event bytes < 3x final request bytes.
    """
    plan_digest = "sha256:" + "0" * 64
    tools = [{"name": "tool_1", "description": "fixed tool"}]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "Assistant system instructions " * 25}
    ]
    events: list[PolicyRequestEvent] = []
    requests: list[dict[str, Any]] = []
    prev_req: dict[str, Any] | None = None
    prev_dig: str | None = None

    for turn in range(1, 51):
        messages = list(messages)
        messages.append(
            {"role": "user", "content": f"Turn {turn} " + "query information " * 25}
        )
        messages.append(
            {
                "role": "assistant",
                "content": f"Turn {turn} " + "response details and explanation " * 25,
            }
        )
        body_str = json.dumps({"messages": messages, "tools": tools})
        b64 = base64.b64encode(body_str.encode("utf-8")).decode("ascii")
        req = {
            "model": "native-checkpointed-v1",
            "native_http_request": {
                "method": "POST",
                "url": "http://native.local/v1/chat/completions",
                "body_b64": b64,
            },
        }
        requests.append(req)
        evt = PolicyRequestEvent.from_request(
            sequence=turn - 1,
            episode_id="ep-checkpointed-native",
            effective_plan_digest=plan_digest,
            turn=turn,
            request=req,
            previous=prev_req,
            previous_digest=prev_dig,
        )
        events.append(evt)
        prev_req = req
        prev_dig = evt.request_digest

    # Round-trip verification
    reconstructed = reconstruct_policy_requests(events)
    assert len(reconstructed) == 50
    for idx, (turn, reconstructed_req) in enumerate(reconstructed):
        assert turn == idx + 1
        assert reconstructed_req == requests[idx]
        assert canonical_sha256(reconstructed_req) == events[idx].request_digest

    # Verify splice is used in subsequent turns
    assert (
        events[1].request_delta["nested"]["native_http_request"]["splice"]["body_b64"]["keep"]
        > 0
    )

    # Compression verification (< 3x final request size)
    final_request_bytes = len(canonical_json_bytes(requests[-1]))
    total_event_bytes = sum(len(canonical_json_bytes(_event_dict(e))) for e in events)
    old_encoding_bytes = sum(len(canonical_json_bytes(r)) for r in requests)

    assert total_event_bytes < 3 * final_request_bytes
    assert old_encoding_bytes > 20 * final_request_bytes


def test_string_splice_round_trip() -> None:
    """Test (g): string splice round-trip incl. keep=0 and empty text."""
    plan_digest = "sha256:" + "0" * 64

    # Turn 1: base string
    req1 = {"text_field": "common_prefix_shared_tail", "constant": 1}
    # Turn 2: keep=0 (completely distinct string)
    req2 = {"text_field": "entirely_different_string", "constant": 1}
    # Turn 3: keep > 0, empty text (shortened to a prefix of previous)
    req3 = {"text_field": "entirely_", "constant": 1}
    # Turn 4: keep > 0, appended text
    req4 = {"text_field": "entirely_appended_text", "constant": 1}

    requests = [req1, req2, req3, req4]
    events = []
    prev_req = None
    prev_dig = None
    for turn, req in enumerate(requests, start=1):
        evt = PolicyRequestEvent.from_request(
            sequence=turn - 1,
            episode_id="ep-splice",
            effective_plan_digest=plan_digest,
            turn=turn,
            request=req,
            previous=prev_req,
            previous_digest=prev_dig,
        )
        events.append(evt)
        prev_req = req
        prev_dig = evt.request_digest

    # Turn 2: keep=0
    assert events[1].request_delta["splice"]["text_field"]["keep"] == 0
    assert events[1].request_delta["splice"]["text_field"]["text"] == "entirely_different_string"

    # Turn 3: keep > 0 and empty text
    assert events[2].request_delta["splice"]["text_field"]["keep"] == len("entirely_")
    assert events[2].request_delta["splice"]["text_field"]["text"] == ""

    # Reconstruct and verify
    reconstructed = reconstruct_policy_requests(events)
    for (turn, r), orig in zip(reconstructed, requests):
        assert r == orig


def test_nested_mapping_key_removal() -> None:
    """Test (h): nested mapping key removal inside a nested delta."""
    plan_digest = "sha256:" + "1" * 64

    req1 = {
        "level1": {
            "level2": {
                "keep_key": "stays",
                "remove_me": "will_be_removed",
                "modify_me": 10,
            }
        }
    }
    req2 = {
        "level1": {
            "level2": {
                "keep_key": "stays",
                "modify_me": 20,
            }
        }
    }
    req3 = {
        "level1": {
            "level2": {
                "keep_key": "stays",
                "added_key": True,
            }
        }
    }

    requests = [req1, req2, req3]
    events = []
    prev_req = None
    prev_dig = None
    for turn, req in enumerate(requests, start=1):
        evt = PolicyRequestEvent.from_request(
            sequence=turn - 1,
            episode_id="ep-nested-remove",
            effective_plan_digest=plan_digest,
            turn=turn,
            request=req,
            previous=prev_req,
            previous_digest=prev_dig,
        )
        events.append(evt)
        prev_req = req
        prev_dig = evt.request_digest

    # Verify nested key removal in turn 2
    delta2 = events[1].request_delta["nested"]["level1"]["nested"]["level2"]
    assert list(delta2["remove"]) == ["remove_me"]
    assert dict(delta2["set"]) == {"modify_me": 20}

    # Verify nested key removal in turn 3
    delta3 = events[2].request_delta["nested"]["level1"]["nested"]["level2"]
    assert list(delta3["remove"]) == ["modify_me"]
    assert dict(delta3["set"]) == {"added_key": True}

    reconstructed = reconstruct_policy_requests(events)
    for (turn, r), orig in zip(reconstructed, requests):
        assert r == orig


def test_reconstruct_policy_requests_from_recovered_evidence_mappings() -> None:
    """Finding 1: reconstruct_policy_requests accepts plain persisted evidence mappings."""
    plan_digest = "sha256:" + "1" * 64
    req1 = {"turn": 1, "query": "hello", "extra": {"nested": "value1"}}
    req2 = {"turn": 2, "query": "hello world", "extra": {"nested": "value2"}}

    evt1 = PolicyRequestEvent.from_request(
        sequence=0, episode_id="ep-rec", effective_plan_digest=plan_digest, turn=1, request=req1
    )
    resp1 = PolicyResponseEvent(
        sequence=1,
        episode_id="ep-rec",
        effective_plan_digest=plan_digest,
        turn=1,
        response_payload={},
        normalized_output=(),
    )
    evt2 = PolicyRequestEvent.from_request(
        sequence=2,
        episode_id="ep-rec",
        effective_plan_digest=plan_digest,
        turn=2,
        request=req2,
        previous=req1,
        previous_digest=evt1.request_digest,
    )
    term = RunnerTerminationEvent(
        sequence=3,
        episode_id="ep-rec",
        effective_plan_digest=plan_digest,
        reason=RunnerTermination.ASSISTANT_COMPLETE,
        turns=2,
    )

    mixed_events = [evt1, resp1, evt2, term]

    # Serialize using evidence._json_value and json roundtrip (no "type" field)
    persisted_mappings = [json.loads(json.dumps(_json_value(e))) for e in mixed_events]

    # Ensure none of the mappings have "type"
    for m in persisted_mappings:
        assert "type" not in m

    # Reconstruct
    reconstructed = reconstruct_policy_requests(persisted_mappings)
    assert len(reconstructed) == 2
    assert reconstructed[0] == (1, req1)
    assert reconstructed[1] == (2, req2)


@pytest.mark.parametrize(
    ("bad_delta", "match"),
    [
        (
            {
                "base_request_digest": None,
                "set": {},
                "extend": {},
                "splice": {},
                "nested": {},
                "remove": [],
                "unknown": 1,
            },
            "unknown top-level delta keys",
        ),
        (
            {"set": {}, "extend": {}, "splice": {}, "nested": {}, "remove": []},
            "missing top-level delta keys",
        ),
        (
            {
                "base_request_digest": "not-sha",
                "set": {},
                "extend": {},
                "splice": {},
                "nested": {},
                "remove": [],
            },
            "implementation_digest",
        ),
        (
            {
                "base_request_digest": None,
                "set": {"a": 1},
                "extend": {"a": {"keep": 0, "items": []}},
                "splice": {},
                "nested": {},
                "remove": [],
            },
            "overlapping keys across set/extend/splice/nested/remove",
        ),
        (
            {
                "base_request_digest": None,
                "set": {"a": 1},
                "extend": {},
                "splice": {},
                "nested": {},
                "remove": ["a"],
            },
            "overlapping keys across set/extend/splice/nested/remove",
        ),
        (
            {
                "base_request_digest": None,
                "set": {},
                "extend": {},
                "splice": {"a": {"keep": 0, "text": "x"}},
                "nested": {
                    "a": {
                        "set": {},
                        "extend": {},
                        "splice": {},
                        "nested": {},
                        "remove": [],
                    }
                },
                "remove": [],
            },
            "overlapping keys across set/extend/splice/nested/remove",
        ),
        (
            {
                "base_request_digest": None,
                "set": {},
                "extend": {"a": {"keep": -1, "items": []}},
                "splice": {},
                "nested": {},
                "remove": [],
            },
            "keep must be a non-negative integer",
        ),
        (
            {
                "base_request_digest": None,
                "set": {},
                "extend": {"a": {"keep": "0", "items": []}},
                "splice": {},
                "nested": {},
                "remove": [],
            },
            "keep must be a non-negative integer",
        ),
        (
            {
                "base_request_digest": None,
                "set": {},
                "extend": {"a": {"keep": 0}},
                "splice": {},
                "nested": {},
                "remove": [],
            },
            "must contain only 'keep' and 'items'",
        ),
        (
            {
                "base_request_digest": None,
                "set": {},
                "extend": {"a": "not-a-mapping"},
                "splice": {},
                "nested": {},
                "remove": [],
            },
            "must be an object",
        ),
        (
            {
                "base_request_digest": None,
                "set": {},
                "extend": {},
                "splice": {"a": {"keep": -1, "text": ""}},
                "nested": {},
                "remove": [],
            },
            "keep must be a non-negative integer",
        ),
        (
            {
                "base_request_digest": None,
                "set": {},
                "extend": {},
                "splice": {"a": {"keep": 0, "text": 123}},
                "nested": {},
                "remove": [],
            },
            "text must be a string",
        ),
    ],
)
def test_policy_request_event_validates_delta_shape(bad_delta: dict[str, Any], match: str) -> None:
    digest = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match=match):
        PolicyRequestEvent(
            sequence=0,
            episode_id="ep-1",
            effective_plan_digest=digest,
            turn=1,
            request_digest=digest,
            request_delta=bad_delta,
        )


def test_apply_policy_request_delta_error_conditions() -> None:
    base_digest = "sha256:" + "1" * 64

    # Mismatched base_request_digest
    delta = {
        "base_request_digest": base_digest,
        "set": {},
        "extend": {},
        "splice": {},
        "nested": {},
        "remove": [],
    }
    with pytest.raises(ValueError, match="base_request_digest mismatch"):
        apply_policy_request_delta({"k": "v"}, delta)

    # Missing previous when base_request_digest is provided
    with pytest.raises(ValueError, match="previous request is None"):
        apply_policy_request_delta(None, delta)

    # Keep exceeds previous list length
    prev_req = {"list_key": [1, 2]}
    prev_digest = canonical_sha256(prev_req)
    delta_keep_overflow = {
        "base_request_digest": prev_digest,
        "set": {},
        "extend": {"list_key": {"keep": 5, "items": [1]}},
        "splice": {},
        "nested": {},
        "remove": [],
    }
    with pytest.raises(ValueError, match="keep .* exceeds previous list length"):
        apply_policy_request_delta(prev_req, delta_keep_overflow)

    # Extending a non-list
    prev_req_non_list = {"num_key": 42}
    prev_digest_non_list = canonical_sha256(prev_req_non_list)
    delta_extend_non_list = {
        "base_request_digest": prev_digest_non_list,
        "set": {},
        "extend": {"num_key": {"keep": 0, "items": [1]}},
        "splice": {},
        "nested": {},
        "remove": [],
    }
    with pytest.raises(ValueError, match="cannot extend non-list field"):
        apply_policy_request_delta(prev_req_non_list, delta_extend_non_list)

    # Splicing a non-string
    delta_splice_non_string = {
        "base_request_digest": prev_digest_non_list,
        "set": {},
        "extend": {},
        "splice": {"num_key": {"keep": 0, "text": "abc"}},
        "nested": {},
        "remove": [],
    }
    with pytest.raises(ValueError, match="cannot splice non-string field"):
        apply_policy_request_delta(prev_req_non_list, delta_splice_non_string)

    # Splicing keep exceeds string length
    prev_str = {"str_key": "short"}
    prev_str_digest = canonical_sha256(prev_str)
    delta_splice_overflow = {
        "base_request_digest": prev_str_digest,
        "set": {},
        "extend": {},
        "splice": {"str_key": {"keep": 10, "text": "abc"}},
        "nested": {},
        "remove": [],
    }
    with pytest.raises(ValueError, match="keep .* exceeds previous string length"):
        apply_policy_request_delta(prev_str, delta_splice_overflow)

    # Nested on a non-mapping
    delta_nested_non_mapping = {
        "base_request_digest": prev_digest_non_list,
        "set": {},
        "extend": {},
        "splice": {},
        "nested": {
            "num_key": {
                "set": {},
                "extend": {},
                "splice": {},
                "nested": {},
                "remove": [],
            }
        },
        "remove": [],
    }
    with pytest.raises(ValueError, match="cannot apply nested delta to non-mapping field"):
        apply_policy_request_delta(prev_req_non_list, delta_nested_non_mapping)


def test_reconstruct_policy_requests_raises_on_digest_mismatch() -> None:
    plan_digest = "sha256:" + "e" * 64
    req1 = {"k": 1}
    evt1 = PolicyRequestEvent.from_request(
        sequence=0,
        episode_id="ep",
        effective_plan_digest=plan_digest,
        turn=1,
        request=req1,
        previous=None,
        previous_digest=None,
    )
    # Event with corrupted request_digest
    corrupted_evt = PolicyRequestEvent(
        sequence=evt1.sequence,
        episode_id=evt1.episode_id,
        effective_plan_digest=evt1.effective_plan_digest,
        turn=evt1.turn,
        request_digest="sha256:" + "f" * 64,
        request_delta=evt1.request_delta,
    )
    with pytest.raises(ValueError, match="reconstructed policy request digest mismatch"):
        reconstruct_policy_requests([corrupted_evt])
