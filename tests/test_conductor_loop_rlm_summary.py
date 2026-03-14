from __future__ import annotations

from agentic_coder_prototype.conductor_loop import build_ctree_runtime_payload, build_rlm_summary
from agentic_coder_prototype.state.session_state import SessionState


def test_build_rlm_summary_from_provider_metadata() -> None:
    payload = build_rlm_summary(
        {
            "rlm_budget_state": {
                "subcalls": 3,
                "total_tokens": 1200,
                "total_cost_usd": 0.42,
            },
            "rlm_branch_ledger": {
                "schema_version": "rlm_branch_ledger_v1",
                "branches": {"root": {"status": "completed"}},
                "events": [{"status": "completed"}],
            },
            "rlm_hybrid_summary": {"total_events": 4},
            "rlm_batch_summary": {"batch_count": 2, "batch_item_count": 8, "batch_failures": 1},
            "rlm_router_summary": {"decision_count": 3},
            "rlm_ctree_projection_summary": {"event_counts": {"branch_start": 1}},
            "rlm_last_episode_delta": {"subcall_count": 1},
        }
    )
    assert payload is not None
    assert payload["subcall_count"] == 3
    assert payload["total_tokens"] == 1200
    assert payload["branch_count"] == 1
    assert payload["branch_event_count"] == 1
    assert payload["hybrid"]["total_events"] == 4
    assert payload["batch_count"] == 2
    assert payload["batch_item_count"] == 8
    assert payload["batch_failures"] == 1
    assert payload["router"]["decision_count"] == 3
    assert payload["ctree_projection"]["event_counts"]["branch_start"] == 1
    assert payload["last_episode_delta"]["subcall_count"] == 1


def test_build_rlm_summary_returns_none_when_empty() -> None:
    assert build_rlm_summary({}) is None
    assert build_rlm_summary(None) is None


def test_build_ctree_runtime_payload_collects_ctree_surfaces() -> None:
    state = SessionState("ws", "image", {}, event_emitter=None)
    root_id = state.ctree_store.record("objective", {"title": "Phase 9"}, turn=1)
    state.ctree_store.record(
        "task",
        {
            "title": "Implement store",
            "parent_id": root_id,
            "targets": ["ctrees/store.py"],
            "constraints": [{"summary": "Preserve replay determinism"}],
        },
        turn=2,
    )

    payload = build_ctree_runtime_payload(
        {"ctrees": {"runner": {"enabled": True, "branches": 3}}},
        state,
        prompt_summary={"section_count": 2},
    )

    assert payload is not None
    assert payload["snapshot"]["node_count"] == 2
    assert payload["compiler"]["kind"] == "htsg_r_preview"
    assert payload["collapse"]["kind"] == "tranche1_collapse"
    assert payload["retrieval_substrate"]["schema_version"] == "ctree_retrieval_substrate_v1"
    assert payload["rehydration_bundle"]["schema_version"] == "ctree_support_bundle_v1"
    assert payload["prompt_planes"]["schema_version"] == "ctree_prompt_planes_v2"
    assert payload["prompt_planes"]["active_path"]
    assert payload["prompt_planes"]["support_bundle"]["support_node_ids"]
    assert payload["runner"]["branches"] == 3
