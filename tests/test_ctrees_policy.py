from __future__ import annotations

from agentic_coder_prototype.ctrees.compiler import compile_ctree
from agentic_coder_prototype.ctrees.collapse import collapse_ctree
from agentic_coder_prototype.ctrees.policy import (
    build_rehydration_plan,
    build_retrieval_substrate,
    collapse_policy,
    resolve_retrieval_policy,
)
from agentic_coder_prototype.ctrees.store import CTreeStore


def test_ctree_compiler_hashes_deterministic() -> None:
    store_a = CTreeStore()
    store_a.record("message", {"text": "hello", "seq": 1, "timestamp": 1}, turn=1)

    store_b = CTreeStore()
    store_b.record("message", {"text": "hello", "seq": 99, "timestamp": 999}, turn=1)

    compiled_a = compile_ctree(store_a)
    compiled_b = compile_ctree(store_b)

    assert compiled_a["hashes"]["z1"] == compiled_b["hashes"]["z1"]
    assert compiled_a["hashes"]["z2"] == compiled_b["hashes"]["z2"]
    assert compiled_a["hashes"]["z3"] == compiled_b["hashes"]["z3"]


def test_ctree_replay_and_recompute_match() -> None:
    store = CTreeStore()
    root_id = store.record("objective", {"title": "Phase 9"}, turn=1)
    store.record("task", {"title": "Reducer", "parent_id": root_id, "targets": ["ctrees/compiler.py"]}, turn=2)
    store.record(
        "task",
        {
            "title": "Blocked sibling",
            "parent_id": root_id,
            "blocker_refs": ["dep-1"],
        },
        turn=3,
    )

    replayed = CTreeStore.from_events(list(store.events))

    compiled_original = compile_ctree(store)
    compiled_replayed = compile_ctree(replayed)

    assert compiled_original["stages"]["SPEC"] == compiled_replayed["stages"]["SPEC"]
    assert compiled_original["prompt_planes"] == compiled_replayed["prompt_planes"]


def test_ctree_collapse_policy_ordering() -> None:
    store = CTreeStore()
    store.nodes.append({"id": "ctn_000010"})
    store.nodes.append({"id": "ctn_000002"})
    store.nodes.append({"id": "ctn_000001"})

    policy_target_2 = collapse_policy(store, target=2)
    policy_target_1 = collapse_policy(store, target=1)

    assert policy_target_2["drop"] == ["ctn_000001"]
    assert policy_target_1["drop"] == ["ctn_000001", "ctn_000002"]


def test_ctree_parent_reduction_is_sibling_order_invariant() -> None:
    store_a = CTreeStore()
    parent_a = store_a.record("objective", {"title": "Parent"}, turn=1)
    store_a.record("task", {"title": "First", "parent_id": parent_a, "targets": ["a.py"]}, turn=2)
    store_a.record("task", {"title": "Second", "parent_id": parent_a, "blocker_refs": ["dep-1"]}, turn=3)

    store_b = CTreeStore()
    parent_b = store_b.record("objective", {"title": "Parent"}, turn=1)
    store_b.record("task", {"title": "Second", "parent_id": parent_b, "blocker_refs": ["dep-1"]}, turn=2)
    store_b.record("task", {"title": "First", "parent_id": parent_b, "targets": ["a.py"]}, turn=3)

    reductions_a = compile_ctree(store_a)["stages"]["SPEC"]["parent_reductions"]
    reductions_b = compile_ctree(store_b)["stages"]["SPEC"]["parent_reductions"]

    comparable_a = dict(reductions_a[0])
    comparable_b = dict(reductions_b[0])
    comparable_a.pop("node_id", None)
    comparable_b.pop("node_id", None)
    assert comparable_a == comparable_b


def test_ctree_compiler_reduces_parent_state() -> None:
    store = CTreeStore()
    parent_id = store.record("objective", {"title": "Phase 9"}, turn=1)
    store.record(
        "task",
        {
            "title": "Define schema",
            "parent_id": parent_id,
            "targets": ["ctrees/schema.py"],
            "artifact_refs": ["schema_spec.md"],
        },
        turn=2,
    )
    store.record(
        "task",
        {
            "title": "Blocked child",
            "parent_id": parent_id,
            "blocker_refs": ["bd-123"],
        },
        turn=3,
    )

    compiled = compile_ctree(store)
    reductions = compiled["stages"]["SPEC"]["parent_reductions"]

    assert reductions
    reduced_parent = reductions[0]
    assert reduced_parent["node_id"] == parent_id
    assert reduced_parent["child_count"] == 2
    assert reduced_parent["child_status_counts"]["active"] == 1
    assert reduced_parent["child_status_counts"]["blocked"] == 1
    assert "ctrees/schema.py" in reduced_parent["active_target_set"]
    assert "bd-123" in reduced_parent["unresolved_blocker_set"]


def test_ctree_policy_exposes_tranche1_retrieval_and_rehydration() -> None:
    store = CTreeStore()
    root_id = store.record("objective", {"title": "Phase 9"}, turn=1)
    store.record(
        "task",
        {
            "title": "Draft policy",
            "parent_id": root_id,
            "constraints": [{"summary": "Keep it deterministic"}],
            "targets": ["ctrees/policy.py"],
            "artifact_refs": ["policy_spec.md"],
        },
        turn=2,
    )

    retrieval = resolve_retrieval_policy(store, mode="resume")
    substrate = build_retrieval_substrate(store, mode="resume")
    rehydration = build_rehydration_plan(store, mode="resume")
    collapse = collapse_ctree(store)

    assert retrieval["enabled_lanes"] == ["structural", "lexical"]
    assert substrate["active_path_node_ids"]
    assert substrate["candidate_support"]["structural"]
    assert rehydration["restore_bundle"]["constraints"]
    assert rehydration["rehydration_bundle"]["support_node_ids"]
    assert collapse["retrieval_policy"]["mode"] == "resume"
    assert collapse["rehydration_plan"]["mode"] == "resume"
    assert collapse["retrieval_substrate"]["candidate_support"]["structural"]


def test_ctree_prompt_planes_use_support_bundle_contract() -> None:
    store = CTreeStore()
    root_id = store.record("objective", {"title": "Prompt plane test"}, turn=1)
    store.record(
        "task",
        {
            "title": "Plane contract",
            "parent_id": root_id,
            "constraints": [{"summary": "Use explicit support bundles"}],
            "targets": ["ctrees/compiler.py"],
        },
        turn=2,
    )

    compiled = compile_ctree(store, prompt_summary={"turn_count": 2})
    planes = compiled["prompt_planes"]

    assert planes["schema_version"] == "ctree_prompt_planes_v2"
    assert "stable_rules" in planes
    assert "support_bundle" in planes
    assert "dynamic_prompt_context" not in planes
    assert "retrieved_support" not in planes
    assert "rehydration" not in planes
    assert planes["support_bundle"]["support_node_ids"]
    assert planes["live_session_delta"]["prompt_summary"]["turn_count"] == 2


def test_ctree_explicit_focus_override_changes_active_path_and_support() -> None:
    store = CTreeStore()
    root_id = store.record("objective", {"title": "Focus root"}, turn=1)
    focus_id = store.record(
        "task",
        {
            "title": "Focus candidate",
            "parent_id": root_id,
            "targets": ["ctrees/compiler.py"],
            "artifact_refs": ["focus_artifact.md"],
        },
        turn=2,
    )
    tail_id = store.record(
        "task",
        {
            "title": "Later unrelated task",
            "parent_id": root_id,
            "targets": ["router/audit.py"],
            "artifact_refs": ["tail_artifact.md"],
        },
        turn=3,
    )

    baseline = build_rehydration_plan(store, mode="active_continuation")
    focused = build_rehydration_plan(store, mode="active_continuation", focus_node_id=focus_id)

    assert baseline["focus_node_id"] == tail_id
    assert focused["focus_node_id"] == focus_id
    assert baseline["retrieval_policy"]["focus_selection"]["strategy"] == "last_node_fallback"
    assert focused["retrieval_policy"]["focus_selection"]["strategy"] == "explicit"
    assert focused["retrieval_substrate"]["active_path_node_ids"] == [root_id, focus_id]
    assert "ctrees/compiler.py" in focused["rehydration_bundle"]["targets"]
    assert "router/audit.py" not in focused["rehydration_bundle"]["targets"]


def test_ctree_compile_focus_override_propagates_to_prompt_planes() -> None:
    store = CTreeStore()
    root_id = store.record("objective", {"title": "Compile focus"}, turn=1)
    focus_id = store.record(
        "task",
        {
            "title": "Desired focus",
            "parent_id": root_id,
            "targets": ["ctrees/policy.py"],
        },
        turn=2,
    )
    store.record(
        "task",
        {
            "title": "Trailing sibling",
            "parent_id": root_id,
            "targets": ["router/audit.py"],
        },
        turn=3,
    )

    compiled = compile_ctree(store, focus_node_id=focus_id)

    assert compiled["prompt_planes"]["reduced_task_state"]["focus_node_id"] == focus_id
    assert compiled["prompt_planes"]["support_bundle"]["focus_node_id"] == focus_id
    assert compiled["stages"]["HEADER"]["focus_node_id"] == focus_id
    assert compiled["stages"]["HEADER"]["active_path_node_ids"] == [root_id, focus_id]


def test_ctree_helper_subtree_summaries_are_opt_in() -> None:
    store = CTreeStore()
    root_id = store.record("objective", {"title": "Helper subtree"}, turn=1)
    store.record(
        "task",
        {
            "title": "Blocked child",
            "parent_id": root_id,
            "blocker_refs": ["dep-schema"],
            "artifact_refs": ["schema_spec.md"],
        },
        turn=2,
    )
    store.record(
        "task",
        {
            "title": "Active child",
            "parent_id": root_id,
            "artifact_refs": ["replacement_schema.md"],
        },
        turn=3,
    )

    baseline = compile_ctree(store)
    helper = compile_ctree(store, helper_summary_enabled=True)

    assert "subtree_summary_proposals" not in baseline["prompt_planes"]
    assert helper["stages"]["SPEC"]["helper_subtree_summaries"]
    assert helper["prompt_planes"]["subtree_summary_proposals"]
    assert helper["stages"]["FROZEN"]["helper_summary_digest"]


def test_ctree_dependency_lookup_surfaces_direct_blocker_graph_support() -> None:
    store = CTreeStore()
    root_id = store.record("objective", {"title": "Dependency lookup"}, turn=1)
    blocker_id = store.record(
        "task",
        {
            "title": "Schema prerequisite",
            "parent_id": root_id,
            "targets": ["ctrees/schema.py"],
            "artifact_refs": ["schema_spec.md"],
        },
        turn=2,
    )
    store.record(
        "task",
        {
            "title": "Blocked compiler follow-up",
            "parent_id": root_id,
            "blocker_refs": [blocker_id],
            "related_links": [{"type": "validates", "target": blocker_id}],
            "targets": ["ctrees/compiler.py"],
        },
        turn=3,
    )

    substrate = build_retrieval_substrate(store, mode="dependency_lookup")
    bundle = build_rehydration_plan(store, mode="dependency_lookup")["rehydration_bundle"]

    assert substrate["retrieval_policy"]["enabled_lanes"] == ["structural", "graph_link"]
    assert substrate["candidate_support"]["graph_link"]
    assert blocker_id in [item["node_id"] for item in substrate["candidate_support"]["graph_link"]]
    assert blocker_id in bundle["support_node_ids"]
    assert blocker_id in bundle["blocker_refs"]
    assert blocker_id in bundle["validations"]
    assert "schema_spec.md" in bundle["artifact_refs"]


def test_ctree_dependency_lookup_graph_neighborhood_is_opt_in_and_bounded() -> None:
    store = CTreeStore()
    root_id = store.record("objective", {"title": "Dependency neighborhood"}, turn=1)
    validation_id = store.record(
        "evidence_bundle",
        {
            "title": "Schema validation packet",
            "parent_id": root_id,
            "status": "done",
            "artifact_refs": ["schema_validation.md"],
        },
        turn=2,
    )
    blocker_id = store.record(
        "task",
        {
            "title": "Schema prerequisite",
            "parent_id": root_id,
            "artifact_refs": ["schema_spec.md"],
            "related_links": [{"type": "validates", "target": validation_id}],
            "targets": ["ctrees/schema.py"],
        },
        turn=3,
    )
    store.record(
        "task",
        {
            "title": "Blocked compiler follow-up",
            "parent_id": root_id,
            "blocker_refs": [blocker_id],
            "targets": ["ctrees/compiler.py"],
        },
        turn=4,
    )

    baseline = build_rehydration_plan(store, mode="dependency_lookup")
    widened = build_rehydration_plan(store, mode="dependency_lookup", graph_neighborhood_enabled=True)

    baseline_lanes = baseline["retrieval_substrate"]["retrieval_policy"]["enabled_lanes"]
    widened_lanes = widened["retrieval_substrate"]["retrieval_policy"]["enabled_lanes"]
    widened_neighbors = widened["retrieval_substrate"]["candidate_support"]["graph_neighborhood"]
    widened_bundle = widened["rehydration_bundle"]

    assert baseline_lanes == ["structural", "graph_link"]
    assert widened_lanes == ["structural", "graph_link", "graph_neighborhood"]
    assert len(widened_neighbors) <= 4
    assert any(item["reason"] == "neighbor_validates_link" for item in widened_neighbors)
    assert validation_id in widened_bundle["graph_neighbor_ids"]
    assert "schema_validation.md" in widened_bundle["artifact_refs"]


def test_ctree_helper_rehydration_is_opt_in_and_prunes_structural_spillover() -> None:
    store = CTreeStore()
    root_id = store.record("objective", {"title": "Helper pruning"}, turn=1)
    store.record(
        "task",
        {
            "title": "Relevant retrieval contract work",
            "parent_id": root_id,
            "targets": ["ctrees/policy.py"],
            "artifact_refs": ["retrieval_contract.md"],
            "workspace_scope": ["agentic_coder_prototype/ctrees"],
        },
        turn=2,
    )
    store.record(
        "task",
        {
            "title": "Irrelevant batch router audit",
            "parent_id": root_id,
            "targets": ["router/audit.py"],
            "artifact_refs": ["batch_router.md"],
            "workspace_scope": ["agentic_coder_prototype/router"],
        },
        turn=3,
    )
    store.record(
        "task",
        {
            "title": "Continue retrieval contract work",
            "parent_id": root_id,
            "targets": ["ctrees/policy.py"],
            "artifact_refs": ["retrieval_contract.md"],
            "constraints": [{"summary": "Suppress false lexical neighbors"}],
        },
        turn=4,
    )

    baseline = build_rehydration_plan(store, mode="active_continuation")
    helper = build_rehydration_plan(store, mode="active_continuation", helper_enabled=True)

    baseline_ids = (baseline.get("rehydration_bundle") or {}).get("support_node_ids") or []
    helper_ids = (helper.get("rehydration_bundle") or {}).get("support_node_ids") or []
    proposal = helper.get("helper_proposal") or {}

    assert helper.get("helper_proposal") is not None
    assert len(helper_ids) < len(baseline_ids)
    assert str(root_id) in baseline_ids
    assert str(root_id) not in helper_ids
    assert "retrieval_contract.md" in (helper.get("rehydration_bundle") or {}).get("artifact_refs") or []
    assert proposal["selected_support_node_ids"] == helper_ids


def test_ctree_helper_summary_coupling_is_opt_in() -> None:
    store = CTreeStore()
    root_id = store.record("objective", {"title": "Summary coupling"}, turn=1)
    store.record(
        "task",
        {
            "title": "Blocked child",
            "parent_id": root_id,
            "blocker_refs": ["dep-schema"],
            "artifact_refs": ["schema_spec.md"],
        },
        turn=2,
    )
    store.record(
        "task",
        {
            "title": "Active child",
            "parent_id": root_id,
            "artifact_refs": ["replacement_schema.md"],
        },
        turn=3,
    )

    helper = build_rehydration_plan(store, mode="active_continuation", helper_enabled=True)
    coupled = build_rehydration_plan(
        store,
        mode="active_continuation",
        helper_enabled=True,
        helper_summary_coupling_enabled=True,
    )

    assert not (helper.get("rehydration_bundle") or {}).get("summary_support")
    assert (coupled.get("rehydration_bundle") or {}).get("summary_support")
    assert (coupled.get("helper_proposal") or {}).get("summary_coupling_parent_ids")


def test_ctree_dense_retrieval_is_opt_in() -> None:
    store = CTreeStore()
    root_id = store.record("objective", {"title": "Dense retrieval"}, turn=1)
    store.record(
        "task",
        {
            "title": "Compilation packet",
            "parent_id": root_id,
            "status": "active",
            "artifact_refs": ["compile_validation.md"],
            "constraints": [{"summary": "Validated build interface", "scope": "task"}],
        },
        turn=2,
    )
    store.record(
        "task",
        {
            "title": "Verify compiler contract",
            "parent_id": root_id,
            "artifact_refs": ["rewrite_plan.md"],
            "targets": ["ctrees/compiler.py"],
        },
        turn=3,
    )

    baseline = build_rehydration_plan(store, mode="active_continuation", helper_enabled=True)
    dense = build_rehydration_plan(
        store,
        mode="active_continuation",
        dense_enabled=True,
        helper_enabled=True,
    )

    assert not ((baseline.get("retrieval_substrate") or {}).get("candidate_support") or {}).get("dense")
    assert ((dense.get("retrieval_substrate") or {}).get("candidate_support") or {}).get("dense")
    assert (dense.get("helper_proposal") or {}).get("selected_support_node_ids") == ["ctn_000003", "ctn_000002"]


def test_ctree_lane_profiles_freeze_core_and_optional_surfaces() -> None:
    store = CTreeStore()
    root_id = store.record("objective", {"title": "Lane profile"}, turn=1)
    store.record(
        "task",
        {
            "title": "Compilation packet",
            "parent_id": root_id,
            "status": "active",
            "artifact_refs": ["compile_validation.md"],
            "constraints": [{"summary": "Validated build interface", "scope": "task"}],
        },
        turn=2,
    )
    store.record(
        "task",
        {
            "title": "Verify compiler contract",
            "parent_id": root_id,
            "artifact_refs": ["rewrite_plan.md"],
            "targets": ["ctrees/compiler.py"],
        },
        turn=3,
    )

    frozen_core = build_rehydration_plan(store, mode="active_continuation", lane_profile="frozen_core")
    dense = build_rehydration_plan(store, mode="active_continuation", lane_profile="dense_retrieval")

    assert frozen_core["lane_profile"] == "frozen_core"
    assert dense["lane_profile"] == "dense_retrieval"
    assert not ((frozen_core.get("retrieval_substrate") or {}).get("candidate_support") or {}).get("dense")
    assert ((dense.get("retrieval_substrate") or {}).get("candidate_support") or {}).get("dense")
    assert frozen_core["lane_config"]["helper_enabled"] is False
    assert dense["lane_config"]["helper_enabled"] is True
