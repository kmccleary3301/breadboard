from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Optional

from .compiler import compile_ctree
from .evaluation_baselines import build_deterministic_reranker_plan
from .evaluation_metrics import (
    build_empty_support_bundle,
    build_evaluation_prompt_planes,
    compare_result_pair,
    evaluate_support_result,
)


HOLDOUT_GENERALIZATION_PACK_VERSION = "phase10_holdout_generalization_pack_v1"
from .policy import build_rehydration_plan
from .store import CTreeStore


_PILOT_BASE_SCENARIO_IDS = {
    "pilot_resume_distractor_v1",
    "pilot_dependency_noise_v1",
    "pilot_semantic_pivot_v1",
    "pilot_subtree_salience_v1",
}


def _scenario_definition(
    *,
    scenario_id: str,
    family: str,
    mode: str,
    nodes: List[Dict[str, Any]],
    base_order: List[str],
    order_order: List[str],
    focus_label: str,
    expected_support_labels: List[str],
    forbidden_support_labels: List[str],
    expected_artifact_refs: List[str],
    forbidden_artifact_refs: List[str],
    expected_blocker_refs: Optional[List[str]] = None,
    expected_validation_labels: Optional[List[str]] = None,
    expected_child_labels: Optional[List[str]] = None,
    wrong_neighbor_labels: Optional[List[str]] = None,
    max_support_share: float = 0.30,
    tight_budget_share: Optional[float] = None,
    tight_budget_token_budget: Optional[int] = None,
    optional_system_id: Optional[str] = None,
    optional_lane_profile: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "scenario_id": scenario_id,
        "family": family,
        "mode": mode,
        "nodes": nodes,
        "base_order": base_order,
        "order_order": order_order,
        "focus_label": focus_label,
        "expected_support_labels": list(expected_support_labels),
        "forbidden_support_labels": list(forbidden_support_labels),
        "expected_artifact_refs": list(expected_artifact_refs),
        "forbidden_artifact_refs": list(forbidden_artifact_refs),
        "expected_blocker_refs": list(expected_blocker_refs or []),
        "expected_validation_labels": list(expected_validation_labels or []),
        "expected_child_labels": list(expected_child_labels or []),
        "wrong_neighbor_labels": list(wrong_neighbor_labels or []),
        "max_support_share": max_support_share,
        "tight_budget_share": tight_budget_share if tight_budget_share is not None else max_support_share,
        "tight_budget_token_budget": tight_budget_token_budget,
        "optional_system_id": optional_system_id,
        "optional_lane_profile": optional_lane_profile,
    }


def _resolve_reference(value: Any, label_to_id: Dict[str, str]) -> str:
    if isinstance(value, dict):
        label = str(value.get("label") or "").strip()
        if label:
            return str(label_to_id.get(label) or label)
        target = str(value.get("target") or "").strip()
        if target:
            return target
        return ""
    text = str(value or "").strip()
    return str(label_to_id.get(text) or text)


def _materialize_store(node_specs: List[Dict[str, Any]], order: Iterable[str]) -> Dict[str, Any]:
    spec_by_label = {
        str(spec.get("label") or "").strip(): spec
        for spec in list(node_specs or [])
        if str(spec.get("label") or "").strip()
    }
    store = CTreeStore()
    label_to_id: Dict[str, str] = {}

    for turn, label in enumerate(list(order or []), start=1):
        spec = spec_by_label.get(str(label) or "")
        if not isinstance(spec, dict):
            continue
        node_type = str(spec.get("node_type") or "task")
        payload: Dict[str, Any] = {}
        for key in (
            "title",
            "status",
            "constraints",
            "targets",
            "workspace_scope",
            "artifact_refs",
            "path",
            "final_spec",
            "lexical_terms",
        ):
            if key in spec:
                payload[key] = copy.deepcopy(spec.get(key))

        parent_label = str(spec.get("parent_label") or "").strip()
        if parent_label:
            payload["parent_id"] = str(label_to_id[parent_label])

        blocker_values: List[str] = []
        for raw in list(spec.get("blocker_refs") or []):
            text = str(raw or "").strip()
            if text:
                blocker_values.append(text)
        for blocker_label in list(spec.get("blocker_labels") or []):
            blocker_id = str(label_to_id.get(str(blocker_label) or "") or "").strip()
            if blocker_id:
                blocker_values.append(blocker_id)
        if blocker_values:
            payload["blocker_refs"] = blocker_values

        related_links: List[Dict[str, Any]] = []
        for link in list(spec.get("related_links") or []):
            if not isinstance(link, dict):
                continue
            target_value = _resolve_reference(link.get("target_label") or link.get("target") or "", label_to_id)
            if not target_value:
                continue
            related_links.append(
                {
                    "type": str(link.get("type") or "").strip(),
                    "target": target_value,
                }
            )
        if related_links:
            payload["related_links"] = related_links

        label_to_id[str(label)] = store.record(node_type, payload, turn=turn)

    return {"store": store, "label_to_id": label_to_id}


def _labels_to_ids(values: Iterable[Any], label_to_id: Dict[str, str]) -> List[str]:
    items: List[str] = []
    for value in list(values or []):
        resolved = _resolve_reference(value, label_to_id)
        if resolved:
            items.append(resolved)
    return items


def _materialize_scenario(defn: Dict[str, Any], perturbation: str) -> Dict[str, Any]:
    use_order = "order_order" if perturbation == "order" else "base_order"
    materialized = _materialize_store(list(defn.get("nodes") or []), list(defn.get(use_order) or []))
    store = materialized["store"]
    label_to_id = materialized["label_to_id"]
    max_support_share = float(
        defn.get("tight_budget_share") if perturbation == "tight_budget" else defn.get("max_support_share") or 0.30
    )
    token_budget = None
    if perturbation == "tight_budget":
        raw_budget = defn.get("tight_budget_token_budget")
        token_budget = int(raw_budget) if raw_budget is not None else None

    return {
        "store": store,
        "scenario_id": str(defn.get("scenario_id") or "") if perturbation == "base" else f"{defn.get('scenario_id')}__{perturbation}",
        "base_scenario_id": str(defn.get("scenario_id") or ""),
        "family": str(defn.get("family") or ""),
        "mode": str(defn.get("mode") or "active_continuation"),
        "perturbation": str(perturbation),
        "focus_node_id": str(label_to_id[str(defn.get("focus_label") or "")]),
        "expected_support_node_ids": _labels_to_ids(defn.get("expected_support_labels") or [], label_to_id),
        "forbidden_support_node_ids": _labels_to_ids(defn.get("forbidden_support_labels") or [], label_to_id),
        "expected_artifact_refs": list(defn.get("expected_artifact_refs") or []),
        "forbidden_artifact_refs": list(defn.get("forbidden_artifact_refs") or []),
        "expected_blocker_refs": _labels_to_ids(defn.get("expected_blocker_refs") or [], label_to_id),
        "expected_validation_ids": _labels_to_ids(defn.get("expected_validation_labels") or [], label_to_id),
        "wrong_neighbor_node_ids": _labels_to_ids(defn.get("wrong_neighbor_labels") or [], label_to_id),
        "max_support_share": max_support_share,
        "token_budget": token_budget,
        "optional_system_id": str(defn.get("optional_system_id") or "").strip() or None,
        "optional_lane_profile": str(defn.get("optional_lane_profile") or "").strip() or None,
    }


def _phase10_base_definitions() -> List[Dict[str, Any]]:
    return [
        _scenario_definition(
            scenario_id="pilot_resume_distractor_v1",
            family="resume_distractors",
            mode="resume",
            optional_system_id="helper_rehydration",
            optional_lane_profile="helper_rehydration",
            max_support_share=0.30,
            tight_budget_share=0.20,
            tight_budget_token_budget=180,
            focus_label="focus",
            expected_support_labels=["focus", "relevant"],
            forbidden_support_labels=["stale_archive"],
            expected_artifact_refs=[],
            forbidden_artifact_refs=[],
            wrong_neighbor_labels=["stale_archive"],
            base_order=["root", "relevant", "stale_archive", "focus"],
            order_order=["root", "relevant", "focus", "stale_archive"],
            nodes=[
                {"label": "root", "node_type": "objective", "title": "Phase 10 resume distractor pack"},
                {
                    "label": "relevant",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Current resume metrics contract",
                    "targets": ["agentic_coder_prototype/ctrees/evaluation_metrics.py"],
                    "artifact_refs": ["resume_metrics_contract.md"],
                    "workspace_scope": ["agentic_coder_prototype/ctrees"],
                },
                {
                    "label": "stale_archive",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Resume archive cleanup",
                    "status": "superseded",
                    "targets": ["docs/archive/resume_old.md"],
                    "artifact_refs": ["resume_archive.md"],
                    "workspace_scope": ["docs/archive"],
                },
                {
                    "label": "focus",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Continue resume metrics instrumentation",
                    "constraints": [{"summary": "Prefer the current resume metrics path"}],
                    "targets": ["agentic_coder_prototype/ctrees/evaluation_metrics.py"],
                    "artifact_refs": ["resume_followup.md"],
                    "workspace_scope": ["agentic_coder_prototype/ctrees"],
                },
            ],
        ),
        _scenario_definition(
            scenario_id="resume_blocker_context_v1",
            family="resume_distractors",
            mode="resume",
            optional_system_id="helper_rehydration",
            optional_lane_profile="helper_rehydration",
            max_support_share=0.30,
            tight_budget_share=0.20,
            tight_budget_token_budget=180,
            focus_label="focus",
            expected_support_labels=["focus", "current_patch"],
            forbidden_support_labels=["archive_patch"],
            expected_artifact_refs=[],
            forbidden_artifact_refs=[],
            expected_blocker_refs=[],
            wrong_neighbor_labels=["archive_patch"],
            base_order=["root", "prereq", "current_patch", "archive_patch", "focus"],
            order_order=["root", "prereq", "current_patch", "focus", "archive_patch"],
            nodes=[
                {"label": "root", "node_type": "objective", "title": "Phase 10 resume blocker context"},
                {
                    "label": "prereq",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Resume prerequisite note",
                    "status": "done",
                    "targets": ["agentic_coder_prototype/ctrees/policy.py"],
                    "artifact_refs": ["resume_prereq.md"],
                    "workspace_scope": ["agentic_coder_prototype/ctrees"],
                },
                {
                    "label": "current_patch",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Current resume compiler patch",
                    "blocker_labels": ["prereq"],
                    "targets": ["agentic_coder_prototype/ctrees/compiler.py"],
                    "artifact_refs": ["current_resume_patch.md"],
                    "workspace_scope": ["agentic_coder_prototype/ctrees"],
                },
                {
                    "label": "archive_patch",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Legacy resume compiler patch",
                    "status": "superseded",
                    "targets": ["docs/archive/compiler_resume.md"],
                    "artifact_refs": ["legacy_resume_patch.md"],
                    "workspace_scope": ["docs/archive"],
                },
                {
                    "label": "focus",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Resume compiler patch after prerequisite",
                    "blocker_labels": ["current_patch"],
                    "constraints": [{"summary": "Use the current compiler patch chain"}],
                    "targets": ["agentic_coder_prototype/ctrees/compiler.py"],
                    "artifact_refs": ["resume_compiler_followup.md"],
                    "workspace_scope": ["agentic_coder_prototype/ctrees"],
                },
            ],
        ),
        _scenario_definition(
            scenario_id="continuation_workspace_scope_v1",
            family="resume_distractors",
            mode="active_continuation",
            optional_system_id="helper_rehydration",
            optional_lane_profile="helper_rehydration",
            max_support_share=0.30,
            tight_budget_share=0.20,
            tight_budget_token_budget=160,
            focus_label="focus",
            expected_support_labels=["focus", "relevant"],
            forbidden_support_labels=["distractor", "stale"],
            expected_artifact_refs=[],
            forbidden_artifact_refs=[],
            wrong_neighbor_labels=["distractor", "stale"],
            base_order=["root", "relevant", "distractor", "stale", "focus"],
            order_order=["root", "relevant", "stale", "focus", "distractor"],
            nodes=[
                {"label": "root", "node_type": "objective", "title": "Phase 10 continuation workspace scope"},
                {
                    "label": "relevant",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Compiler continuity note",
                    "targets": ["agentic_coder_prototype/ctrees/compiler.py"],
                    "artifact_refs": ["compiler_continuity.md"],
                    "workspace_scope": ["agentic_coder_prototype/ctrees"],
                },
                {
                    "label": "distractor",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Continuity planning bulletin",
                    "targets": ["docs_tmp/c_trees/phase_10/outline.md"],
                    "artifact_refs": ["continuity_bulletin.md"],
                    "workspace_scope": ["docs_tmp/c_trees/phase_10"],
                },
                {
                    "label": "stale",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Old compiler continuity note",
                    "status": "superseded",
                    "targets": ["agentic_coder_prototype/ctrees/compiler.py"],
                    "artifact_refs": ["old_compiler_continuity.md"],
                    "workspace_scope": ["agentic_coder_prototype/ctrees"],
                },
                {
                    "label": "focus",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Continue compiler continuity repair",
                    "constraints": [{"summary": "Resume the current compiler continuity path"}],
                    "targets": ["agentic_coder_prototype/ctrees/compiler.py"],
                    "artifact_refs": ["compiler_continuity_followup.md"],
                    "workspace_scope": ["agentic_coder_prototype/ctrees"],
                },
            ],
        ),
        _scenario_definition(
            scenario_id="resume_focus_shift_v1",
            family="resume_distractors",
            mode="resume",
            optional_system_id="helper_rehydration",
            optional_lane_profile="helper_rehydration",
            max_support_share=0.30,
            tight_budget_share=0.20,
            tight_budget_token_budget=180,
            focus_label="focus",
            expected_support_labels=["focus", "relevant"],
            forbidden_support_labels=["noise", "tail_noise"],
            expected_artifact_refs=[],
            forbidden_artifact_refs=[],
            wrong_neighbor_labels=["noise", "tail_noise"],
            base_order=["root", "relevant", "noise", "tail_noise", "focus"],
            order_order=["root", "relevant", "focus", "noise", "tail_noise"],
            nodes=[
                {"label": "root", "node_type": "objective", "title": "Phase 10 resume focus shift"},
                {
                    "label": "relevant",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Resume evaluation contract note",
                    "targets": ["agentic_coder_prototype/ctrees/evaluation_metrics.py"],
                    "artifact_refs": ["evaluation_resume_contract.md"],
                    "workspace_scope": ["agentic_coder_prototype/ctrees"],
                },
                {
                    "label": "noise",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Release resume bulletin",
                    "targets": ["docs/releases/phase10.md"],
                    "artifact_refs": ["release_resume_bulletin.md"],
                    "workspace_scope": ["docs/releases"],
                },
                {
                    "label": "tail_noise",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Router patch checklist",
                    "targets": ["agentic_coder_prototype/router/audit.py"],
                    "artifact_refs": ["router_patch_checklist.md"],
                    "workspace_scope": ["agentic_coder_prototype/router"],
                },
                {
                    "label": "focus",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Resume evaluation contract patch",
                    "constraints": [{"summary": "Use the evaluation contract note"}],
                    "targets": ["agentic_coder_prototype/ctrees/evaluation_metrics.py"],
                    "artifact_refs": ["evaluation_resume_followup.md"],
                    "workspace_scope": ["agentic_coder_prototype/ctrees"],
                },
            ],
        ),
        _scenario_definition(
            scenario_id="pilot_dependency_noise_v1",
            family="dependency_noise",
            mode="dependency_lookup",
            optional_system_id="graph_neighborhood",
            optional_lane_profile="graph_neighborhood",
            max_support_share=0.30,
            tight_budget_share=0.24,
            tight_budget_token_budget=220,
            focus_label="focus",
            expected_support_labels=["focus", "blocker", "validation"],
            forbidden_support_labels=["noise"],
            expected_artifact_refs=["compiler_schema_validation.md", "schema_blocker.md"],
            forbidden_artifact_refs=["noise_neighbor.md"],
            expected_blocker_refs=["blocker"],
            expected_validation_labels=["validation"],
            wrong_neighbor_labels=["noise"],
            base_order=["root", "validation", "prerequisite", "noise", "blocker", "focus"],
            order_order=["root", "validation", "prerequisite", "blocker", "focus", "noise"],
            nodes=[
                {"label": "root", "node_type": "objective", "title": "Phase 10 dependency noise pack"},
                {
                    "label": "validation",
                    "node_type": "evidence_bundle",
                    "parent_label": "root",
                    "title": "Compiler schema validation packet",
                    "status": "done",
                    "artifact_refs": ["compiler_schema_validation.md"],
                },
                {
                    "label": "prerequisite",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Policy prerequisite for compiler patch",
                    "status": "done",
                    "artifact_refs": ["policy_prereq.md"],
                    "targets": ["agentic_coder_prototype/ctrees/policy.py"],
                },
                {
                    "label": "noise",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Unrelated graph neighbor noise",
                    "status": "done",
                    "artifact_refs": ["noise_neighbor.md"],
                    "targets": ["agentic_coder_prototype/router/graph.py"],
                },
                {
                    "label": "blocker",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Schema blocker for compiler patch",
                    "artifact_refs": ["schema_blocker.md"],
                    "blocker_labels": ["prerequisite"],
                    "related_links": [
                        {"type": "validates", "target_label": "validation"},
                        {"type": "waits_for", "target_label": "noise"},
                    ],
                    "targets": ["agentic_coder_prototype/ctrees/schema.py"],
                },
                {
                    "label": "focus",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Resolve compiler dependency lookup",
                    "blocker_labels": ["blocker"],
                    "targets": ["agentic_coder_prototype/ctrees/compiler.py"],
                },
            ],
        ),
        _scenario_definition(
            scenario_id="dependency_prerequisite_neighbor_v1",
            family="dependency_noise",
            mode="dependency_lookup",
            optional_system_id="graph_neighborhood",
            optional_lane_profile="graph_neighborhood",
            max_support_share=0.30,
            tight_budget_share=0.24,
            tight_budget_token_budget=220,
            focus_label="focus",
            expected_support_labels=["focus", "blocker", "prerequisite"],
            forbidden_support_labels=["noise"],
            expected_artifact_refs=["dependency_prereq.md", "schema_blocker_neighbor.md"],
            forbidden_artifact_refs=["batch_router_noise.md"],
            expected_blocker_refs=["blocker"],
            wrong_neighbor_labels=["noise"],
            base_order=["root", "prerequisite", "noise", "blocker", "focus"],
            order_order=["root", "prerequisite", "blocker", "focus", "noise"],
            nodes=[
                {"label": "root", "node_type": "objective", "title": "Phase 10 prerequisite neighbor pack"},
                {
                    "label": "prerequisite",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Dependency prerequisite note",
                    "status": "done",
                    "artifact_refs": ["dependency_prereq.md"],
                    "targets": ["agentic_coder_prototype/ctrees/policy.py"],
                },
                {
                    "label": "noise",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Batch router noise neighbor",
                    "status": "done",
                    "artifact_refs": ["batch_router_noise.md"],
                    "targets": ["agentic_coder_prototype/router/audit.py"],
                },
                {
                    "label": "blocker",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Schema blocker waiting on prerequisite",
                    "artifact_refs": ["schema_blocker_neighbor.md"],
                    "blocker_labels": ["prerequisite"],
                    "related_links": [{"type": "waits_for", "target_label": "noise"}],
                    "targets": ["agentic_coder_prototype/ctrees/schema.py"],
                },
                {
                    "label": "focus",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Resolve compiler prerequisite chain",
                    "blocker_labels": ["blocker"],
                    "targets": ["agentic_coder_prototype/ctrees/compiler.py"],
                },
            ],
        ),
        _scenario_definition(
            scenario_id="dependency_supersession_neighbor_v1",
            family="dependency_noise",
            mode="dependency_lookup",
            optional_system_id="graph_neighborhood",
            optional_lane_profile="graph_neighborhood",
            max_support_share=0.30,
            tight_budget_share=0.24,
            tight_budget_token_budget=220,
            focus_label="focus",
            expected_support_labels=["focus", "legacy", "replacement"],
            forbidden_support_labels=["noise"],
            expected_artifact_refs=["replacement_schema.md"],
            forbidden_artifact_refs=["legacy_schema.md", "archive_schema_note.md"],
            expected_blocker_refs=["legacy"],
            expected_validation_labels=["validation"],
            wrong_neighbor_labels=["noise"],
            base_order=["root", "replacement", "validation", "noise", "legacy", "focus"],
            order_order=["root", "replacement", "validation", "legacy", "focus", "noise"],
            nodes=[
                {"label": "root", "node_type": "objective", "title": "Phase 10 supersession neighbor pack"},
                {
                    "label": "replacement",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Replacement schema contract",
                    "status": "done",
                    "artifact_refs": ["replacement_schema.md"],
                    "targets": ["agentic_coder_prototype/ctrees/schema.py"],
                },
                {
                    "label": "validation",
                    "node_type": "evidence_bundle",
                    "parent_label": "root",
                    "title": "Replacement schema validation packet",
                    "status": "done",
                    "artifact_refs": ["replacement_validation.md"],
                },
                {
                    "label": "noise",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Archive schema note",
                    "status": "done",
                    "artifact_refs": ["archive_schema_note.md"],
                    "targets": ["docs/archive/schema.md"],
                },
                {
                    "label": "legacy",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Legacy schema prerequisite",
                    "artifact_refs": ["legacy_schema.md"],
                    "targets": ["agentic_coder_prototype/ctrees/schema.py"],
                    "related_links": [
                        {"type": "supersedes", "target_label": "replacement"},
                        {"type": "validates", "target_label": "validation"},
                    ],
                },
                {
                    "label": "focus",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Resolve compiler schema after supersession",
                    "blocker_labels": ["legacy"],
                    "targets": ["agentic_coder_prototype/ctrees/compiler.py"],
                },
            ],
        ),
        _scenario_definition(
            scenario_id="dependency_validation_external_gate_v1",
            family="dependency_noise",
            mode="dependency_lookup",
            optional_system_id="graph_neighborhood",
            optional_lane_profile="graph_neighborhood",
            max_support_share=0.30,
            tight_budget_share=0.24,
            tight_budget_token_budget=220,
            focus_label="focus",
            expected_support_labels=["focus", "blocker", "validation"],
            forbidden_support_labels=["noise"],
            expected_artifact_refs=["compiler_validation_gate.md", "external_gate_proxy.md"],
            forbidden_artifact_refs=["neighbor_noise.md"],
            expected_blocker_refs=["blocker"],
            expected_validation_labels=["validation"],
            wrong_neighbor_labels=["noise"],
            base_order=["root", "validation", "noise", "blocker", "focus"],
            order_order=["root", "validation", "blocker", "focus", "noise"],
            nodes=[
                {"label": "root", "node_type": "objective", "title": "Phase 10 external gate dependency pack"},
                {
                    "label": "validation",
                    "node_type": "evidence_bundle",
                    "parent_label": "root",
                    "title": "Compiler validation gate",
                    "status": "done",
                    "artifact_refs": ["compiler_validation_gate.md"],
                },
                {
                    "label": "noise",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Neighbor noise packet",
                    "status": "done",
                    "artifact_refs": ["neighbor_noise.md"],
                    "targets": ["agentic_coder_prototype/router/graph.py"],
                },
                {
                    "label": "blocker",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "External gate proxy",
                    "artifact_refs": ["external_gate_proxy.md"],
                    "blocker_refs": ["ext-ci-gate"],
                    "related_links": [
                        {"type": "validates", "target_label": "validation"},
                        {"type": "waits_for", "target_label": "noise"},
                    ],
                    "targets": ["agentic_coder_prototype/ctrees/policy.py"],
                },
                {
                    "label": "focus",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Resolve external compiler gate",
                    "blocker_labels": ["blocker"],
                    "targets": ["agentic_coder_prototype/ctrees/compiler.py"],
                },
            ],
        ),
        _scenario_definition(
            scenario_id="pilot_semantic_pivot_v1",
            family="semantic_pivot",
            mode="pivot",
            optional_system_id="dense_retrieval",
            optional_lane_profile="dense_retrieval",
            max_support_share=0.30,
            tight_budget_share=0.20,
            tight_budget_token_budget=170,
            focus_label="focus",
            expected_support_labels=["focus", "semantic"],
            forbidden_support_labels=["near_miss"],
            expected_artifact_refs=[],
            forbidden_artifact_refs=[],
            wrong_neighbor_labels=["near_miss"],
            base_order=["root", "semantic", "near_miss", "focus"],
            order_order=["root", "semantic", "focus", "near_miss"],
            nodes=[
                {"label": "root", "node_type": "objective", "title": "Phase 10 semantic pivot pack"},
                {
                    "label": "semantic",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Validation restoration compilation note",
                    "artifact_refs": ["semantic_validation_restore.md"],
                    "targets": ["agentic_coder_prototype/ctrees/compiler.py"],
                },
                {
                    "label": "near_miss",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Resume planning bulletin",
                    "artifact_refs": ["resume_bulletin.md"],
                    "targets": ["docs_tmp/c_trees/phase_10/outline.md"],
                },
                {
                    "label": "focus",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Pivot to verification resume compiler work",
                    "constraints": [{"summary": "Recover semantic support for the compiler pivot"}],
                    "targets": ["agentic_coder_prototype/ctrees/compiler.py"],
                    "artifact_refs": ["pivot_resume_compiler.md"],
                },
            ],
        ),
        _scenario_definition(
            scenario_id="pivot_compile_build_v1",
            family="semantic_pivot",
            mode="pivot",
            optional_system_id="dense_retrieval",
            optional_lane_profile="dense_retrieval",
            max_support_share=0.30,
            tight_budget_share=0.20,
            tight_budget_token_budget=170,
            focus_label="focus",
            expected_support_labels=["focus", "semantic"],
            forbidden_support_labels=["near_miss"],
            expected_artifact_refs=[],
            forbidden_artifact_refs=[],
            wrong_neighbor_labels=["near_miss"],
            base_order=["root", "semantic", "near_miss", "focus"],
            order_order=["root", "semantic", "focus", "near_miss"],
            nodes=[
                {"label": "root", "node_type": "objective", "title": "Phase 10 compile/build semantic pack"},
                {
                    "label": "semantic",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Build verification packet",
                    "artifact_refs": ["build_verification_packet.md"],
                    "targets": ["agentic_coder_prototype/ctrees/compiler.py"],
                },
                {
                    "label": "near_miss",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Build farm bulletin",
                    "artifact_refs": ["build_farm_bulletin.md"],
                    "targets": ["infra/buildfarm/controller.py"],
                },
                {
                    "label": "focus",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Pivot to compile contract verification",
                    "constraints": [{"summary": "Recover compile/build validation support"}],
                    "targets": ["agentic_coder_prototype/ctrees/compiler.py"],
                    "artifact_refs": ["compile_contract_pivot.md"],
                },
            ],
        ),
        _scenario_definition(
            scenario_id="pivot_restore_resume_v1",
            family="semantic_pivot",
            mode="pivot",
            optional_system_id="dense_retrieval",
            optional_lane_profile="dense_retrieval",
            max_support_share=0.30,
            tight_budget_share=0.20,
            tight_budget_token_budget=170,
            focus_label="focus",
            expected_support_labels=["focus", "semantic"],
            forbidden_support_labels=["near_miss"],
            expected_artifact_refs=[],
            forbidden_artifact_refs=[],
            wrong_neighbor_labels=["near_miss"],
            base_order=["root", "semantic", "near_miss", "focus"],
            order_order=["root", "semantic", "focus", "near_miss"],
            nodes=[
                {"label": "root", "node_type": "objective", "title": "Phase 10 restore/resume semantic pack"},
                {
                    "label": "semantic",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Resume restoration memo",
                    "artifact_refs": ["resume_restoration_memo.md"],
                    "targets": ["agentic_coder_prototype/ctrees/compiler.py"],
                },
                {
                    "label": "near_miss",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Restore archive memo",
                    "artifact_refs": ["restore_archive_memo.md"],
                    "targets": ["docs/archive/resume.md"],
                },
                {
                    "label": "focus",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Pivot to rehydrate compiler session",
                    "constraints": [{"summary": "Recover resume/restore support for the compiler session"}],
                    "targets": ["agentic_coder_prototype/ctrees/compiler.py"],
                    "artifact_refs": ["rehydrate_compiler_session.md"],
                },
            ],
        ),
        _scenario_definition(
            scenario_id="pivot_validation_check_v1",
            family="semantic_pivot",
            mode="pivot",
            optional_system_id="dense_retrieval",
            optional_lane_profile="dense_retrieval",
            max_support_share=0.30,
            tight_budget_share=0.20,
            tight_budget_token_budget=170,
            focus_label="focus",
            expected_support_labels=["focus", "semantic"],
            forbidden_support_labels=["near_miss"],
            expected_artifact_refs=[],
            forbidden_artifact_refs=[],
            wrong_neighbor_labels=["near_miss"],
            base_order=["root", "semantic", "near_miss", "focus"],
            order_order=["root", "semantic", "focus", "near_miss"],
            nodes=[
                {"label": "root", "node_type": "objective", "title": "Phase 10 validation/check semantic pack"},
                {
                    "label": "semantic",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Validation check packet",
                    "artifact_refs": ["validation_check_packet.md"],
                    "targets": ["tests/test_ctrees_policy.py"],
                },
                {
                    "label": "near_miss",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Check-in packet",
                    "artifact_refs": ["checkin_packet.md"],
                    "targets": ["docs/checkin.md"],
                },
                {
                    "label": "focus",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Pivot to verify policy contract",
                    "constraints": [{"summary": "Use validation evidence rather than check-in noise"}],
                    "targets": ["tests/test_ctrees_policy.py"],
                    "artifact_refs": ["verify_policy_contract.md"],
                },
            ],
        ),
        _scenario_definition(
            scenario_id="pilot_subtree_salience_v1",
            family="subtree_salience",
            mode="active_continuation",
            optional_system_id="summary_coupling",
            optional_lane_profile="summary_coupling",
            max_support_share=0.30,
            tight_budget_share=0.24,
            tight_budget_token_budget=180,
            focus_label="active",
            expected_support_labels=["active", "blocked"],
            forbidden_support_labels=["stale"],
            expected_artifact_refs=[],
            forbidden_artifact_refs=[],
            expected_blocker_refs=[],
            wrong_neighbor_labels=["stale"],
            base_order=["root", "parent", "blocked", "active", "stale"],
            order_order=["root", "parent", "blocked", "stale", "active"],
            nodes=[
                {"label": "root", "node_type": "objective", "title": "Phase 10 subtree salience pack"},
                {
                    "label": "parent",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Summarize subtree for rehydration",
                    "targets": ["agentic_coder_prototype/ctrees/compiler.py"],
                },
                {
                    "label": "blocked",
                    "node_type": "task",
                    "parent_label": "parent",
                    "title": "Blocked child",
                    "status": "blocked",
                    "blocker_refs": ["dep-phase10-pack"],
                    "artifact_refs": ["blocked_child_summary.md"],
                    "targets": ["agentic_coder_prototype/ctrees/schema.py"],
                },
                {
                    "label": "active",
                    "node_type": "task",
                    "parent_label": "parent",
                    "title": "Active child",
                    "targets": ["agentic_coder_prototype/ctrees/holdout_generalization_pack.py"],
                    "artifact_refs": ["active_child_summary.md"],
                },
                {
                    "label": "stale",
                    "node_type": "task",
                    "parent_label": "parent",
                    "title": "Superseded child",
                    "status": "superseded",
                    "artifact_refs": ["stale_child_summary.md"],
                },
            ],
        ),
        _scenario_definition(
            scenario_id="subtree_validation_salience_v1",
            family="subtree_salience",
            mode="active_continuation",
            optional_system_id="summary_coupling",
            optional_lane_profile="summary_coupling",
            max_support_share=0.30,
            tight_budget_share=0.24,
            tight_budget_token_budget=180,
            focus_label="focus",
            expected_support_labels=["focus", "blocked", "validation"],
            forbidden_support_labels=["stale"],
            expected_artifact_refs=[],
            forbidden_artifact_refs=[],
            expected_blocker_refs=[],
            wrong_neighbor_labels=["stale"],
            base_order=["root", "parent", "blocked", "validation", "stale", "focus"],
            order_order=["root", "parent", "blocked", "validation", "focus", "stale"],
            nodes=[
                {"label": "root", "node_type": "objective", "title": "Phase 10 subtree validation salience"},
                {
                    "label": "parent",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Summarize validation subtree",
                    "targets": ["agentic_coder_prototype/ctrees/compiler.py"],
                },
                {
                    "label": "blocked",
                    "node_type": "task",
                    "parent_label": "parent",
                    "title": "Blocked validator child",
                    "status": "blocked",
                    "blocker_refs": ["dep-validator"],
                    "artifact_refs": ["blocked_validator_child.md"],
                    "targets": ["agentic_coder_prototype/ctrees/schema.py"],
                },
                {
                    "label": "validation",
                    "node_type": "task",
                    "parent_label": "parent",
                    "title": "Validator packet",
                    "status": "done",
                    "artifact_refs": ["validator_packet.md"],
                    "targets": ["tests/test_ctrees_policy.py"],
                },
                {
                    "label": "stale",
                    "node_type": "task",
                    "parent_label": "parent",
                    "title": "Legacy validator packet",
                    "status": "superseded",
                    "artifact_refs": ["legacy_validator_packet.md"],
                },
                {
                    "label": "focus",
                    "node_type": "task",
                    "parent_label": "parent",
                    "title": "Continue validator subtree repair",
                    "artifact_refs": ["validator_subtree_followup.md"],
                    "targets": ["agentic_coder_prototype/ctrees/compiler.py"],
                },
            ],
        ),
        _scenario_definition(
            scenario_id="subtree_done_vs_stale_v1",
            family="subtree_salience",
            mode="active_continuation",
            optional_system_id="summary_coupling",
            optional_lane_profile="summary_coupling",
            max_support_share=0.30,
            tight_budget_share=0.24,
            tight_budget_token_budget=180,
            focus_label="focus",
            expected_support_labels=["focus", "done_child"],
            forbidden_support_labels=["stale"],
            expected_artifact_refs=[],
            forbidden_artifact_refs=[],
            wrong_neighbor_labels=["stale"],
            base_order=["root", "parent", "done_child", "stale", "focus"],
            order_order=["root", "parent", "done_child", "focus", "stale"],
            nodes=[
                {"label": "root", "node_type": "objective", "title": "Phase 10 subtree done vs stale"},
                {
                    "label": "parent",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Summarize active subtree",
                    "targets": ["agentic_coder_prototype/ctrees/compiler.py"],
                },
                {
                    "label": "done_child",
                    "node_type": "task",
                    "parent_label": "parent",
                    "title": "Done child",
                    "status": "done",
                    "artifact_refs": ["active_summary.md"],
                    "targets": ["agentic_coder_prototype/ctrees/policy.py"],
                },
                {
                    "label": "stale",
                    "node_type": "task",
                    "parent_label": "parent",
                    "title": "Archived child",
                    "status": "superseded",
                    "artifact_refs": ["stale_summary.md"],
                },
                {
                    "label": "focus",
                    "node_type": "task",
                    "parent_label": "parent",
                    "title": "Continue subtree synthesis",
                    "artifact_refs": ["focus_summary.md"],
                    "targets": ["agentic_coder_prototype/ctrees/compiler.py"],
                },
            ],
        ),
        _scenario_definition(
            scenario_id="subtree_blocker_active_mix_v1",
            family="subtree_salience",
            mode="active_continuation",
            optional_system_id="summary_coupling",
            optional_lane_profile="summary_coupling",
            max_support_share=0.30,
            tight_budget_share=0.24,
            tight_budget_token_budget=180,
            focus_label="focus",
            expected_support_labels=["focus", "blocked_sibling", "active_sibling"],
            forbidden_support_labels=["archive_sibling"],
            expected_artifact_refs=[],
            forbidden_artifact_refs=[],
            expected_blocker_refs=[],
            wrong_neighbor_labels=["archive_sibling"],
            base_order=["root", "parent", "blocked_sibling", "active_sibling", "archive_sibling", "focus"],
            order_order=["root", "parent", "blocked_sibling", "active_sibling", "focus", "archive_sibling"],
            nodes=[
                {"label": "root", "node_type": "objective", "title": "Phase 10 subtree blocker/active mix"},
                {
                    "label": "parent",
                    "node_type": "task",
                    "parent_label": "root",
                    "title": "Summarize blocker and active sibling state",
                    "targets": ["agentic_coder_prototype/ctrees/compiler.py"],
                },
                {
                    "label": "blocked_sibling",
                    "node_type": "task",
                    "parent_label": "parent",
                    "title": "Blocked sibling",
                    "status": "blocked",
                    "blocker_refs": ["dep-subtree"],
                    "artifact_refs": ["blocker_child_summary.md"],
                    "targets": ["agentic_coder_prototype/ctrees/schema.py"],
                },
                {
                    "label": "active_sibling",
                    "node_type": "task",
                    "parent_label": "parent",
                    "title": "Active sibling",
                    "artifact_refs": ["active_sibling_summary.md"],
                    "targets": ["agentic_coder_prototype/ctrees/policy.py"],
                },
                {
                    "label": "archive_sibling",
                    "node_type": "task",
                    "parent_label": "parent",
                    "title": "Archive sibling",
                    "status": "superseded",
                    "artifact_refs": ["archive_sibling_summary.md"],
                },
                {
                    "label": "focus",
                    "node_type": "task",
                    "parent_label": "parent",
                    "title": "Continue subtree blocker resolution",
                    "artifact_refs": ["focus_blocker_followup.md"],
                    "targets": ["agentic_coder_prototype/ctrees/compiler.py"],
                },
            ],
        ),
    ]


def build_phase10_base_scenarios() -> List[Dict[str, Any]]:
    return [_materialize_scenario(defn, "base") for defn in _phase10_base_definitions()]


def build_phase10_full_holdout_scenarios() -> List[Dict[str, Any]]:
    scenarios: List[Dict[str, Any]] = []
    for defn in _phase10_base_definitions():
        scenarios.append(_materialize_scenario(defn, "base"))
        scenarios.append(_materialize_scenario(defn, "order"))
        scenarios.append(_materialize_scenario(defn, "tight_budget"))
    return scenarios


def build_phase10_pilot_scenarios() -> List[Dict[str, Any]]:
    return [item for item in build_phase10_base_scenarios() if str(item.get("base_scenario_id") or "") in _PILOT_BASE_SCENARIO_IDS]


def _system_ids_for_scenario(scenario: Dict[str, Any]) -> List[str]:
    systems = ["stripped_support", "frozen_core", "deterministic_reranker"]
    optional_system_id = str(scenario.get("optional_system_id") or "").strip()
    if optional_system_id:
        systems.append(optional_system_id)
    return systems


def _lane_profile_for_system(system_id: str, scenario: Dict[str, Any]) -> Optional[str]:
    if system_id == "frozen_core":
        return "frozen_core"
    if system_id == str(scenario.get("optional_system_id") or "").strip():
        return str(scenario.get("optional_lane_profile") or "").strip() or None
    return None


def _build_system_run(scenario: Dict[str, Any], system_id: str) -> Dict[str, Any]:
    store = scenario["store"]
    mode = str(scenario.get("mode") or "active_continuation")
    focus_node_id = str(scenario.get("focus_node_id") or "").strip() or None
    token_budget = scenario.get("token_budget")
    lane_profile = _lane_profile_for_system(system_id, scenario)
    helper_summary_enabled = system_id == "summary_coupling"
    compiled = compile_ctree(store, helper_summary_enabled=helper_summary_enabled, focus_node_id=focus_node_id)
    compiled_prompt_planes = dict(compiled.get("prompt_planes") or {})

    if system_id == "stripped_support":
        core_plan = build_rehydration_plan(
            store,
            mode=mode,
            focus_node_id=focus_node_id,
            lane_profile="frozen_core",
            token_budget=token_budget,
        )
        stripped_bundle = build_empty_support_bundle(
            mode=mode,
            focus_node_id=core_plan.get("focus_node_id"),
            active_path_node_ids=(core_plan.get("retrieval_policy") or {}).get("active_path_node_ids") or [],
        )
        prompt_planes = build_evaluation_prompt_planes(compiled_prompt_planes, support_bundle=stripped_bundle)
        return {
            "system_id": system_id,
            "lane_profile": None,
            "retrieval_substrate": dict(core_plan.get("retrieval_substrate") or {}),
            "support_bundle": stripped_bundle,
            "prompt_planes": prompt_planes,
        }

    if system_id == "deterministic_reranker":
        reranker_plan = build_deterministic_reranker_plan(
            store,
            mode=mode,
            token_budget=token_budget,
            focus_node_id=focus_node_id,
            graph_enabled=True,
            graph_neighborhood_enabled=False,
            dense_enabled=False,
        )
        prompt_planes = build_evaluation_prompt_planes(
            compiled_prompt_planes,
            support_bundle=dict(reranker_plan.get("rehydration_bundle") or {}),
        )
        return {
            "system_id": system_id,
            "lane_profile": None,
            "retrieval_substrate": dict(reranker_plan.get("retrieval_substrate") or {}),
            "support_bundle": dict(reranker_plan.get("rehydration_bundle") or {}),
            "prompt_planes": prompt_planes,
        }

    rehydration_plan = build_rehydration_plan(
        store,
        mode=mode,
        focus_node_id=focus_node_id,
        lane_profile=lane_profile,
        token_budget=token_budget,
    )
    prompt_planes = build_evaluation_prompt_planes(
        compiled_prompt_planes,
        support_bundle=dict(rehydration_plan.get("rehydration_bundle") or {}),
        include_summary_proposals=helper_summary_enabled,
    )
    return {
        "system_id": system_id,
        "lane_profile": lane_profile,
        "retrieval_substrate": dict(rehydration_plan.get("retrieval_substrate") or {}),
        "support_bundle": dict(rehydration_plan.get("rehydration_bundle") or {}),
        "prompt_planes": prompt_planes,
    }


def _evaluate_scenario(scenario: Dict[str, Any]) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    result_by_system: Dict[str, Dict[str, Any]] = {}

    for system_id in _system_ids_for_scenario(scenario):
        system_run = _build_system_run(scenario, system_id)
        result = evaluate_support_result(
            store=scenario["store"],
            scenario_id=str(scenario.get("scenario_id") or ""),
            family=str(scenario.get("family") or ""),
            perturbation=str(scenario.get("perturbation") or "base"),
            system_id=system_id,
            baseline_id="stripped_support" if system_id != "stripped_support" else None,
            mode=str(scenario.get("mode") or "active_continuation"),
            focus_node_id=scenario.get("focus_node_id"),
            retrieval_substrate=dict(system_run.get("retrieval_substrate") or {}),
            support_bundle=dict(system_run.get("support_bundle") or {}),
            prompt_planes=dict(system_run.get("prompt_planes") or {}),
            expected_support_node_ids=scenario.get("expected_support_node_ids"),
            forbidden_support_node_ids=scenario.get("forbidden_support_node_ids"),
            expected_artifact_refs=scenario.get("expected_artifact_refs"),
            forbidden_artifact_refs=scenario.get("forbidden_artifact_refs"),
            expected_blocker_refs=scenario.get("expected_blocker_refs"),
            expected_validation_ids=scenario.get("expected_validation_ids"),
            wrong_neighbor_node_ids=scenario.get("wrong_neighbor_node_ids"),
            max_support_share=float(scenario.get("max_support_share") or 0.30),
        )
        result["lane_profile"] = system_run.get("lane_profile")
        results.append(result)
        result_by_system[system_id] = result

    comparisons: List[Dict[str, Any]] = []
    stripped = result_by_system.get("stripped_support")
    frozen_core = result_by_system.get("frozen_core")
    deterministic = result_by_system.get("deterministic_reranker")
    optional_system_id = str(scenario.get("optional_system_id") or "").strip()
    optional_result = result_by_system.get(optional_system_id) if optional_system_id else None
    if stripped and frozen_core:
        comparisons.append(compare_result_pair(frozen_core, stripped))
    if frozen_core and deterministic:
        comparisons.append(compare_result_pair(deterministic, frozen_core))
    if deterministic and optional_result:
        comparisons.append(compare_result_pair(optional_result, deterministic))
    elif frozen_core and optional_result:
        comparisons.append(compare_result_pair(optional_result, frozen_core))

    return {
        "scenario_id": scenario["scenario_id"],
        "base_scenario_id": scenario["base_scenario_id"],
        "family": scenario["family"],
        "perturbation": scenario["perturbation"],
        "mode": scenario["mode"],
        "focus_node_id": scenario["focus_node_id"],
        "token_budget": scenario.get("token_budget"),
        "max_support_share": scenario.get("max_support_share"),
        "systems": results,
        "comparisons": comparisons,
        "optional_system_id": optional_system_id or None,
    }


def _result_by_system_id(item: Dict[str, Any], system_id: str) -> Optional[Dict[str, Any]]:
    for result in list(item.get("systems") or []):
        if str(result.get("system_id") or "") == system_id:
            return result
    return None


def _comparison_for(item: Dict[str, Any], candidate_system_id: str, baseline_system_id: str) -> Optional[Dict[str, Any]]:
    for comparison in list(item.get("comparisons") or []):
        if (
            str(comparison.get("candidate_system_id") or "") == candidate_system_id
            and str(comparison.get("baseline_system_id") or "") == baseline_system_id
        ):
            return comparison
    return None


def _pass_rate(items: List[Dict[str, Any]], system_id: str) -> float:
    relevant = []
    for item in items:
        result = _result_by_system_id(item, system_id)
        if isinstance(result, dict):
            relevant.append(bool((result.get("outcome") or {}).get("pass")))
    if not relevant:
        return 0.0
    return sum(1 for value in relevant if value) / len(relevant)


def _aggregate_pack_summary(scenario_results: List[Dict[str, Any]], *, prompt_centric_status: str, schema_version: str) -> Dict[str, Any]:
    base_items = [item for item in scenario_results if str(item.get("perturbation") or "") == "base"]
    order_items = [item for item in scenario_results if str(item.get("perturbation") or "") == "order"]
    budget_items = [item for item in scenario_results if str(item.get("perturbation") or "") == "tight_budget"]
    family_names = sorted({str(item.get("family") or "") for item in scenario_results if str(item.get("family") or "")})
    optional_lane_ids = sorted(
        {
            str(item.get("optional_system_id") or "")
            for item in scenario_results
            if str(item.get("optional_system_id") or "").strip()
        }
    )

    family_summaries: Dict[str, Dict[str, Any]] = {}
    for family in family_names:
        family_base_items = [item for item in base_items if str(item.get("family") or "") == family]
        family_optional_ids = sorted(
            {
                str(item.get("optional_system_id") or "")
                for item in family_base_items
                if str(item.get("optional_system_id") or "").strip()
            }
        )
        family_summaries[family] = {
            "base_scenario_count": len(family_base_items),
            "frozen_core_pass_count": sum(
                1
                for item in family_base_items
                if bool((_result_by_system_id(item, "frozen_core") or {}).get("outcome", {}).get("pass"))
            ),
            "deterministic_pass_count": sum(
                1
                for item in family_base_items
                if bool((_result_by_system_id(item, "deterministic_reranker") or {}).get("outcome", {}).get("pass"))
            ),
            "frozen_core_beats_stripped_count": sum(
                1
                for item in family_base_items
                if bool((_comparison_for(item, "frozen_core", "stripped_support") or {}).get("candidate_better"))
            ),
            "optional_lane_ids": family_optional_ids,
            "optional_beats_deterministic_count": sum(
                1
                for item in family_base_items
                if bool(
                    (
                        _comparison_for(
                            item,
                            str(item.get("optional_system_id") or ""),
                            "deterministic_reranker",
                        )
                        or {}
                    ).get("candidate_better")
                )
            ),
            "frozen_core_vs_stripped_score_delta_sum": sum(
                float((_comparison_for(item, "frozen_core", "stripped_support") or {}).get("delta", {}).get("scenario_score") or 0.0)
                for item in family_base_items
            ),
        }

    optional_lane_summaries: Dict[str, Dict[str, Any]] = {}
    for lane_id in optional_lane_ids:
        lane_items = [item for item in base_items if str(item.get("optional_system_id") or "") == lane_id]
        optional_lane_summaries[lane_id] = {
            "base_scenario_count": len(lane_items),
            "pass_count": sum(
                1
                for item in lane_items
                if bool((_result_by_system_id(item, lane_id) or {}).get("outcome", {}).get("pass"))
            ),
            "beats_deterministic_count": sum(
                1
                for item in lane_items
                if bool((_comparison_for(item, lane_id, "deterministic_reranker") or {}).get("candidate_better"))
            ),
        }

    summary = {
        "scenario_count": len(scenario_results),
        "base_scenario_count": len(base_items),
        "family_count": len(family_names),
        "order_perturbation_count": len(order_items),
        "tight_budget_perturbation_count": len(budget_items),
        "system_ids": sorted({result["system_id"] for item in scenario_results for result in item["systems"]}),
        "frozen_core_beats_stripped_count": sum(
            1
            for item in base_items
            if bool((_comparison_for(item, "frozen_core", "stripped_support") or {}).get("candidate_better"))
        ),
        "frozen_core_pass_count": sum(
            1
            for item in base_items
            if bool((_result_by_system_id(item, "frozen_core") or {}).get("outcome", {}).get("pass"))
        ),
        "deterministic_pass_count": sum(
            1
            for item in base_items
            if bool((_result_by_system_id(item, "deterministic_reranker") or {}).get("outcome", {}).get("pass"))
        ),
        "optional_lane_beats_deterministic_count": sum(
            1
            for item in base_items
            if bool(
                (
                    _comparison_for(
                        item,
                        str(item.get("optional_system_id") or ""),
                        "deterministic_reranker",
                    )
                    or {}
                ).get("candidate_better")
            )
        ),
        "order_perturbation_pass_rate": _pass_rate(order_items, "frozen_core"),
        "tight_budget_perturbation_pass_rate": _pass_rate(budget_items, "frozen_core"),
        "deterministic_order_pass_rate": _pass_rate(order_items, "deterministic_reranker"),
        "deterministic_tight_budget_pass_rate": _pass_rate(budget_items, "deterministic_reranker"),
        "family_summaries": family_summaries,
        "optional_lane_summaries": optional_lane_summaries,
    }

    return {
        "schema_version": schema_version,
        "prompt_centric_baseline_status": prompt_centric_status,
        "scenarios": scenario_results,
        "summary": summary,
    }


def run_phase10_pilot_holdout_pack() -> Dict[str, Any]:
    scenario_results = [_evaluate_scenario(scenario) for scenario in build_phase10_pilot_scenarios()]
    payload = _aggregate_pack_summary(
        scenario_results,
        prompt_centric_status="unresolved_not_run",
        schema_version="phase10_holdout_generalization_pack_pilot_v1",
    )
    payload["summary"]["scenario_count"] = len(scenario_results)
    payload["summary"]["family_count"] = len({item["family"] for item in scenario_results})
    return payload


def run_phase10_full_holdout_pack() -> Dict[str, Any]:
    scenario_results = [_evaluate_scenario(scenario) for scenario in build_phase10_full_holdout_scenarios()]
    return _aggregate_pack_summary(
        scenario_results,
        prompt_centric_status="resolved_external_bridge_checkpoint",
        schema_version="phase10_holdout_generalization_pack_v1",
    )
