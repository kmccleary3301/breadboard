from __future__ import annotations

import json
from typing import Any

from scripts.e4_parity.validators import hash_utils as _hash_utils

RENDER_PROFILE = "breadboard_p3_helper_runtime"
HOOK_DURATION_MS = 12
SESSION_TTL_SECONDS = 3600
SESSION_EXPIRES_AT = "2026-07-03T08:30:00Z"
MEMORY_OBSERVED_TOKENS = 64000
MEMORY_THRESHOLD_TOKENS = 120000
MEMORY_CONTEXT_WINDOW = 128000
MEMORY_MAX_INPUT_TOKENS = 120000
MEMORY_RESERVED_OUTPUT_TOKENS = 8000
MEMORY_TARGET_AFTER_TOKENS = 32000
MEMORY_ELIDED_TOKEN_ESTIMATE = 12000
MEMORY_INSERTION_TOKEN_ESTIMATE = 512
POLICY_BUDGETS = {"token_budget": 200000, "cost_budget_usd": 0.0, "wall_time_seconds": 3600}
POLICY_LIMITS = {"concurrency": 2, "file_bytes": 10485760, "network_bytes": 0, "tool_calls": 50}


def _sha256_text(value: str) -> str:
    return _hash_utils.sha256_text(value)


def _hash_no_prefix(value: str) -> str:
    return _hash_utils.sha256_hex(value.encode("utf-8"))


def _digest_json(value: Any) -> dict[str, str]:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"algorithm": "sha256", "value": _hash_no_prefix(payload)}


def _actor(kind: str, actor_id: str) -> dict[str, str]:
    return {"actor_kind": kind, "actor_id": actor_id}


def context_sources(*, workspace_root: str) -> list[dict[str, Any]]:
    return [
        {"source_id": "system_prompt", "source_kind": "system-prompt", "uri": "kernel://prompts/system", "content": "BreadBoard helper runtime source pack", "order": 0, "model_visible": True, "host_visible": True},
        {"source_id": "project_context", "source_kind": "project-file", "uri": "workspace://AGENTS.md", "content": "project instructions", "order": 10, "model_visible": True, "host_visible": True},
        {"source_id": "generated_cwd", "source_kind": "workspace-state", "uri": "generated://cwd", "content": workspace_root, "order": 20, "model_visible": True, "host_visible": True},
        {"source_id": "redacted_env", "source_kind": "config", "uri": "env://OPENAI_API_KEY", "content": "redacted", "order": 30, "model_visible": False, "host_visible": True},
    ]


def capability_declarations() -> list[dict[str, Any]]:
    return [
        {"capability_id": "tool.read", "capability_type": "tool", "name": "read", "scopes": ["workspace:read"], "metadata": {"payload_type": "resource_ref"}},
        {"capability_id": "tool.read", "capability_type": "tool", "name": "read duplicate", "scopes": ["shadowed"]},
        {"capability_id": "tool.disabled", "capability_type": "tool", "name": "disabled fixture declaration", "disabled": True, "scopes": ["disabled"]},
        {"capability_id": "resolver.workspace", "capability_type": "resource_resolver", "name": "workspace resolver", "model_visible": False, "scopes": ["workspace:read"]},
    ]


def hook_effects(*, execution_id: str) -> list[dict[str, Any]]:
    return [
        {"effect_id": f"{execution_id}_effect_0", "effect_type": "log", "effect_ref": "log://observe-only", "status": "applied"},
        {"effect_id": f"{execution_id}_effect_1", "effect_type": "tool_surface_patch", "effect_ref": "bb.capability_registry.v1:tool_surface_patch", "status": "applied"},
        {"effect_id": f"{execution_id}_effect_2", "effect_type": "capability_exposure", "effect_ref": "bb.capability_registry.v1:registered_hook_tool", "status": "applied"},
        {"effect_id": f"{execution_id}_effect_3", "effect_type": "signal", "effect_ref": "bb.signal.v1:block-cancel", "status": "suppressed"},
    ]


def hook_visibility(*, execution_id: str) -> dict[str, Any]:
    return {
        "model_visible": True,
        "provider_visible": False,
        "model_render_ref": f"bb.tool_model_render.v1:{execution_id}",
        "provider_exchange_ref": None,
    }


def resource_sidecars(*, access_id: str, uri: str, content_hash: str, content_digest: str) -> list[dict[str, Any]]:
    return [
        {
            "sidecar_id": f"sidecar_{access_id}_manifest",
            "kind": "hash_manifest",
            "digest": _digest_json({"uri": uri, "hash": content_hash}),
            "media_type": "application/json",
            "size_bytes": 128,
            "uri": f"bb+blob://sha256/{content_digest}/sidecars/hash_manifest.json",
        }
    ]


def resource_access_inputs(*, access_id: str, uri: str, content: str, operation: str = "read", approval_required: bool = False, returned_size_bytes: int | None = None, redacted: bool = False) -> dict[str, Any]:
    digest = _hash_no_prefix(content)
    return {
        "access_id": access_id,
        "uri": uri,
        "content": content,
        "operation": operation,
        "boundary": "/workspace",
        "returned_size_bytes": returned_size_bytes,
        "redacted": redacted,
        "approval_required": approval_required,
        "resolver_id": "resolver_workspace",
        "scope_id": "workspace_default",
        "root_uri": "file:///workspace",
        "media_type": "text/plain",
        "storage_uri": f"bb+blob://sha256/{digest}",
        "storage_resolver_id": "resolver_blob_store",
        "encrypted_at_rest": True,
        "retention_policy": "project",
        "sidecars": resource_sidecars(access_id=access_id, uri=uri, content_hash=f"sha256:{digest}", content_digest=digest),
    }


def protocol_provider_policy_records(*, run_id: str, route_id: str, policy_id: str, generated_at: str) -> dict[str, dict[str, Any]]:
    endpoint_hash = _sha256_text("mcp://local-static")
    protocol = {
        "session_id": f"{run_id}_mcp_static_session",
        "protocol": "mcp",
        "endpoint_hash": endpoint_hash,
        "auth_scope": {
            "scope_id": "scope_mcp_local_read",
            "method_ref": "auth-method://mcp/no-secret-local",
            "credential_ref": "secret://none/local-protocol",
            "scopes": ["tools.read", "resources.read"],
            "provider_visible": False,
        },
        "session_window": {"window_id": f"{run_id}_mcp_window", "started_at": generated_at, "expires_at": SESSION_EXPIRES_AT, "ttl_seconds": SESSION_TTL_SECONDS, "renewable": True},
        "bindings": [{"binding_id": "binding_mcp_read_resource", "binding_kind": "tool", "target_ref": "tool://mcp/read-resource", "model_visible": True, "provider_visible": False}],
        "visibility": {
            "model_visible": True,
            "provider_visible": False,
            "model_visible_refs": ["binding_mcp_read_resource"],
            "provider_visible_refs": [],
            "host_only_refs": ["auth_scope.credential_ref", "endpoint_hash"],
            "redacted_refs": ["credential_material"],
        },
        "transcript_refs": [f"transcript://{run_id}/mcp-static"],
        "link_refs": [f"link://{run_id}/mcp-read-resource"],
        "status": "active",
        "errors": [],
        "evidence_refs": ["evidence://p3/protocol/static-session"],
    }
    route = {
        "route_id": route_id,
        "selector": {"selector_id": "selector_no_provider", "model_selector": "no-provider", "provider_selector": "breadboard.local", "source": "config", "model_visible": True, "provider_visible": False},
        "role": "primary",
        "thinking": {"enabled": False, "budget_ref": None, "effort": "none", "model_visible": False, "provider_visible": False},
        "route_suffix": "/local/replay",
        "resolved": {"provider_id": "breadboard.local", "provider_family": "local", "runtime_id": "replay", "model": "no-provider", "model_revision": None},
        "base_url_hash": _sha256_text("local-replay"),
        "auth_method_ref": "auth-method://local/no-secret",
        "fallback_chain": [
            {"fallback_index": 0, "route_ref": route_id, "provider_id": "breadboard.local", "model": "no-provider", "eligible": True, "reason": "selected"},
            {"fallback_index": 1, "route_ref": "route_disabled_provider", "provider_id": "disabled", "model": "none", "eligible": False, "reason": "no credentials"},
        ],
        "selected_fallback_index": 0,
        "service_tier": "free",
        "cost_usage_refs": {"estimate_ref": "usage-estimate://local-zero", "usage_ref": "usage://none", "billing_scope_ref": "billing-scope://local"},
        "provider_exchange_refs": [],
        "visibility": {
            "model_visible_fields": ["selector.model_selector", "role", "service_tier"],
            "provider_visible_fields": [],
            "host_only_fields": ["base_url_hash", "auth_method_ref"],
            "redacted_fields": ["auth_material"],
        },
        "evidence_refs": ["evidence://p3/provider/route-before-exchange"],
    }
    policy = {
        "policy_id": policy_id,
        "applies_to": {"operation_id": f"operation_{run_id}", "run_id": run_id, "principal_ref": "principal://user/local", "provider_route_ref": f"provider_route:{route_id}"},
        "command_policy": {"surface": "command", "default_decision": "ask", "rules": [{"rule_id": "command_safe_readonly", "match": {"kind": "argv_prefix", "pattern": "python --version", "scope_ref": "workspace://default"}, "decision": "allow", "approval_required": False, "redaction_ref": None, "evidence_refs": ["evidence://policy/readonly-command"]}], "evidence_refs": ["evidence://policy/command"]},
        "file_policy": {"surface": "file", "default_decision": "ask", "rules": [{"rule_id": "file_workspace_read", "match": {"kind": "path_prefix", "pattern": "/workspace", "scope_ref": "workspace://default"}, "decision": "allow", "approval_required": False, "redaction_ref": None, "evidence_refs": ["evidence://policy/file-read"]}], "evidence_refs": ["evidence://policy/file"]},
        "network_policy": {"surface": "network", "default_decision": "deny", "rules": [{"rule_id": "network_local_mcp", "match": {"kind": "endpoint_hash", "pattern": endpoint_hash, "scope_ref": "protocol://mcp/local"}, "decision": "allow", "approval_required": False, "redaction_ref": "redaction://network/endpoint-hash", "evidence_refs": ["evidence://policy/network-local"]}], "evidence_refs": ["evidence://policy/network"]},
        "tool_policy": {"surface": "tool", "default_decision": "ask", "rules": [{"rule_id": "tool_mcp_read", "match": {"kind": "tool_id", "pattern": "tool.mcp.read-resource", "scope_ref": "tool-surface://default"}, "decision": "allow", "approval_required": False, "redaction_ref": None, "evidence_refs": ["evidence://policy/tool-mcp-read"]}], "evidence_refs": ["evidence://policy/tool"]},
        "resource_policy": {"surface": "resource", "default_decision": "ask", "rules": [{"rule_id": "resource_workspace", "match": {"kind": "resource_scheme", "pattern": "file", "scope_ref": "workspace://default"}, "decision": "allow", "approval_required": False, "redaction_ref": None, "evidence_refs": ["evidence://policy/resource-file"]}], "evidence_refs": ["evidence://policy/resource"]},
        "side_effect_policy": {"surface": "side_effect", "default_decision": "ask", "rules": [{"rule_id": "side_effect_no_external_write", "match": {"kind": "effect_class", "pattern": "external_write", "scope_ref": None}, "decision": "deny", "approval_required": True, "redaction_ref": None, "evidence_refs": ["evidence://policy/no-external-write"]}], "evidence_refs": ["evidence://policy/side-effect"]},
        "approvals": {"mode": "on_demand", "approval_refs": ["approval://workspace/default"], "approver_scope": "workspace-owner", "evidence_refs": ["evidence://policy/approvals"]},
        "budgets": dict(POLICY_BUDGETS),
        "limits": dict(POLICY_LIMITS),
        "sandbox_refs": ["sandbox://local/read-only"],
        "placement_refs": ["placement://local-process/default"],
        "conditional_rules": [{"rule_id": "require_approval_for_write", "condition_ref": "condition://operation-writes-workspace", "when_true": "ask", "when_false": "allow", "evidence_refs": ["evidence://policy/write-approval"]}],
        "redaction_visibility": {"model_visible_paths": ["command_policy.default_decision", "tool_policy.default_decision"], "provider_visible_paths": [], "host_only_paths": ["approvals.approval_refs", "sandbox_refs"], "redacted_paths": ["auth_material", "network_policy.rules[0].match.pattern"], "redaction_policy_refs": ["redaction://default-secrets"]},
        "enforcement_evidence_refs": ["evidence://policy/enforcement/p3"],
    }
    return {"protocol_session": protocol, "provider_route": route, "operation_policy": policy}


def memory_work_records(*, run_id: str, plan_id: str, work_item_id: str, generated_at: str) -> dict[str, dict[str, Any]]:
    plan = {
        "plan_id": plan_id,
        "transcript_refs": [{"transcript_id": f"{run_id}_transcript", "ref": f"bb.session_transcript.v1:{run_id}#turns:0-4", "start_seq": 0, "end_seq": 4, "hash": _sha256_text(f"{run_id}:transcript:0-4"), "visibility": {"model_visible": True, "provider_visible": True, "host_visible": True}}],
        "trigger": {"kind": "checkpoint", "source_ref": f"bb.kernel_event.v1:{run_id}_checkpoint", "reason": "P3 helper runtime compiler fixture records checkpoint-backed transcript continuation.", "observed_tokens": MEMORY_OBSERVED_TOKENS, "threshold_tokens": MEMORY_THRESHOLD_TOKENS},
        "token_budget": {"model_context_window": MEMORY_CONTEXT_WINDOW, "max_input_tokens": MEMORY_MAX_INPUT_TOKENS, "reserved_output_tokens": MEMORY_RESERVED_OUTPUT_TOKENS, "before_tokens": MEMORY_OBSERVED_TOKENS, "target_after_tokens": MEMORY_TARGET_AFTER_TOKENS},
        "preserved_refs": [{"ref_id": "preserve_system", "ref_kind": "policy", "ref": f"bb.session_transcript.v1:{run_id}#turn:0", "hash": _sha256_text(f"{run_id}:system"), "reason": "System directives stay authoritative.", "visibility": {"model_visible": True, "provider_visible": True, "host_visible": True}}],
        "elided_refs": [{"ref_id": "elide_terminal", "ref": f"bb.session_transcript.v1:{run_id}#turns:2-3", "reason": "Terminal output is represented by a summary artifact.", "token_estimate": MEMORY_ELIDED_TOKEN_ESTIMATE, "replacement_ref": f"bb.resource_ref.v1:{run_id}_terminal_summary", "visibility": {"model_visible": False, "provider_visible": False, "host_visible": True}}],
        "generated_refs": [{"ref_id": "generated_summary", "ref_kind": "summary", "artifact_ref": f"bb.resource_ref.v1:{run_id}_terminal_summary", "hash": _sha256_text(f"{run_id}:summary"), "produced_by": "breadboard.helper_runtime"}],
        "model_visible_insertions": [{"insertion_id": "insert_summary", "position": "system_context", "content_ref": f"bb.resource_ref.v1:{run_id}_terminal_summary", "content_hash": _sha256_text(f"{run_id}:summary:content"), "token_estimate": MEMORY_INSERTION_TOKEN_ESTIMATE}],
        "backend_contributions": [{"contribution_id": "backend_compactor", "contributor_kind": "compactor", "contributor_id": "breadboard.helper_runtime", "input_refs": [f"bb.session_transcript.v1:{run_id}#turns:2-3"], "output_refs": [f"bb.resource_ref.v1:{run_id}_terminal_summary"], "visibility": {"model_visible": False, "provider_visible": False, "host_visible": True}, "hash": _sha256_text(f"{run_id}:backend")}],
        "hashes": {"algorithm": "sha256", "source_hash": _sha256_text(f"{run_id}:source"), "plan_hash": _sha256_text(f"{run_id}:plan"), "compacted_hash": _sha256_text(f"{run_id}:compacted")},
        "status": "applied",
    }
    work_item = {
        "work_item_id": work_item_id,
        "identity": {"task_id": f"{run_id}_task", "task_kind": "subagent", "subagent_id": f"{run_id}_worker", "distributed_task_id": f"{run_id}_distributed", "correlation_id": f"{run_id}_correlation"},
        "delegation": {"parent_work_item_id": f"{run_id}_parent", "parent_task_id": f"{run_id}_parent_task", "delegated_by": _actor("agent", "main"), "delegation_ref": f"bb.distributed_task_descriptor.v1:{run_id}_distributed"},
        "state": {"status": "completed", "entered_at": generated_at, "reason": "Worker completed helper-runtime compiler validation.", "checkpoint_ref": f"bb.memory_compaction_plan.v1:{plan_id}"},
        "owner": _actor("agent", "main"),
        "assignee": _actor("subagent", f"{run_id}_worker"),
        "input_artifact_refs": [{"ref": f"bb.resource_ref.v1:{run_id}_assignment", "artifact_kind": "prompt", "visibility": {"model_visible": True, "provider_visible": True, "host_visible": True}}],
        "output_artifact_refs": [{"ref": f"bb.resource_ref.v1:{run_id}_result", "artifact_kind": "report", "visibility": {"model_visible": True, "provider_visible": False, "host_visible": True}}],
        "cancellation_policy": {"mode": "cooperative", "cancellable_by": [_actor("agent", "main")], "propagate_to_children": True, "on_cancel": "checkpoint_then_stop"},
        "resume_policy": {"mode": "checkpoint", "resume_from_ref": f"bb.memory_compaction_plan.v1:{plan_id}", "requires_approval": False, "wake_refs": [f"bb.wake_subscription.v1:{run_id}_wake"]},
        "visibility": {"model_visible": True, "provider_visible": False, "host_visible": True},
        "provider_route_ref": f"bb.provider_route.v1:{run_id}_route",
        "operation_policy_ref": f"bb.effective_operation_policy.v1:{run_id}_policy",
        "budget": {"token_budget": 4096, "cost_budget_usd": 1.25, "wall_time_seconds": 900},
        "isolation": "sandbox",
        "metadata": {"example": "p3-helper-runtime"},
    }
    return {"memory_plan": plan, "work_item": work_item}


def projection_broker_records(*, run_id: str, event_id: str, broker_id: str, generated_at: str) -> dict[str, dict[str, Any]]:
    projection = {
        "projection_event_id": event_id,
        "source": {"source_kernel_event_ref": f"bb.kernel_event.v1:{run_id}_event", "source_refs": [f"bb.tool_execution_outcome.v1:{run_id}_tool"], "source_hash": _sha256_text(f"{run_id}:kernel-event")},
        "projection_surface": {"surface_id": "surface_model_context", "surface_kind": "model_context", "audience": "model", "path": "/messages/3/content"},
        "projection_payload_ref": {"ref": f"bb.resource_ref.v1:{run_id}_projection_payload", "media_type": "application/json", "hash": _sha256_text(f"{run_id}:projection-payload"), "redaction_state": "summarized"},
        "status_frames": [{"frame_id": f"{event_id}_frame_0", "seq": 0, "status": "projected", "message_ref": f"bb.tool_model_render.v1:{run_id}_render", "emitted_at": generated_at, "visible_to_model": True, "visible_to_host": True}],
        "visibility": {"model_visible": True, "host_visible": True, "provider_visible": True},
        "kernel_truth": False,
    }
    broker = {
        "broker_id": broker_id,
        "request": {"request_id": f"{run_id}_side_effect_request", "effect_type": "resource_write", "operation": "append", "requested_by": _actor("agent", "main"), "reason": "Persist approved helper-runtime output artifact.", "idempotency_key": f"{run_id}_append"},
        "policy_refs": ["policy:workspace_write"],
        "permission_approval_refs": [f"bb.permission.v1:{run_id}_approval"],
        "target_refs": [{"target_kind": "resource", "ref": f"bb.resource_ref.v1:{run_id}_output", "scope": "workspace"}],
        "execution_placement": {"placement_id": "placement_local_sandbox", "environment_id": "env_workspace", "runner_ref": "bb.execution_placement.v1:placement_local_sandbox", "isolation": "sandbox"},
        "before_refs": [{"ref": f"bb.resource_ref.v1:{run_id}_output@before", "hash": _sha256_text(f"{run_id}:before"), "captured_at": generated_at}],
        "after_refs": [{"ref": f"bb.resource_ref.v1:{run_id}_output@after", "hash": _sha256_text(f"{run_id}:after"), "captured_at": generated_at}],
        "status": "applied",
        "errors": [],
        "visibility_impact": {"model_visible": True, "provider_visible": False, "host_visible": True, "model_visible_ref": f"bb.tool_model_render.v1:{run_id}_write", "provider_visible_ref": None},
    }
    return {"projection_event": projection, "side_effect_broker": broker}
