from __future__ import annotations

from typing import Any, Dict


_MAP: dict[str, dict[str, str]] = {
    "unsupported_mode": {
        "classification": "request_shape",
        "severity": "error",
        "title": "Unsupported ATP mode",
        "action": "Use command mode requests for ATP REPL.",
    },
    "state_ref_not_found": {
        "classification": "state_ref",
        "severity": "error",
        "title": "Missing state reference",
        "action": "Regenerate state_ref by calling ATP with want_state=true.",
    },
    "state_ref_incompatible": {
        "classification": "state_ref",
        "severity": "error",
        "title": "Incompatible state reference",
        "action": "Regenerate state_ref using matching header/toolchain/mathlib snapshot.",
    },
    "state_ref_corrupt": {
        "classification": "state_ref",
        "severity": "error",
        "title": "Corrupt state reference",
        "action": "Discard the reference and regenerate from a fresh successful run.",
    },
    "state_ref_tenant_mismatch": {
        "classification": "state_ref_tenancy",
        "severity": "error",
        "title": "Cross-tenant state_ref reuse blocked",
        "action": "Use the original tenant_id or regenerate state for the current tenant.",
    },
    "invalid_timeout": {
        "classification": "limits",
        "severity": "error",
        "title": "Invalid timeout value",
        "action": "Set timeout_s to a positive value within service policy limits.",
    },
    "invalid_memory": {
        "classification": "limits",
        "severity": "error",
        "title": "Invalid memory limit",
        "action": "Set memory_mb to a positive value within service policy limits.",
    },
    "invalid_heartbeats": {
        "classification": "limits",
        "severity": "error",
        "title": "Invalid heartbeat limit",
        "action": "Set max_heartbeats to a positive value within service policy limits.",
    },
    "limit_timeout_exceeded": {
        "classification": "limits",
        "severity": "error",
        "title": "Timeout limit exceeded",
        "action": "Reduce timeout_s or increase service max_timeout_s in ATP config.",
    },
    "limit_memory_exceeded": {
        "classification": "limits",
        "severity": "error",
        "title": "Memory limit exceeded",
        "action": "Reduce memory_mb or increase service max_memory_mb in ATP config.",
    },
    "limit_heartbeats_exceeded": {
        "classification": "limits",
        "severity": "error",
        "title": "Heartbeat limit exceeded",
        "action": "Reduce max_heartbeats or increase service cap in ATP config.",
    },
    "protocol_mismatch": {
        "classification": "protocol",
        "severity": "error",
        "title": "Protocol mismatch",
        "action": "Rebuild or rotate snapshot to a compatible protocol envelope.",
    },
    "protocol_response_invalid": {
        "classification": "protocol",
        "severity": "error",
        "title": "Malformed protocol response",
        "action": "Inspect guest logs/serial output and validate REPL agent response shape.",
    },
    "protocol_transport_error": {
        "classification": "transport",
        "severity": "error",
        "title": "Transport execution failure",
        "action": "Check vsock/socket availability and Firecracker process health.",
    },
    "repl_port_conflict": {
        "classification": "transport",
        "severity": "error",
        "title": "REPL port conflict",
        "action": "Use a unique FIRECRACKER_REPL_VSOCK_PORT per worker.",
    },
    "protocol_batch_mismatch": {
        "classification": "protocol",
        "severity": "error",
        "title": "Batch cardinality mismatch",
        "action": "Treat as service/runtime fault and retry after service health checks.",
    },
}


def build_atp_harness_diagnostic(error_code: str | None, error_detail: Dict[str, Any] | None) -> Dict[str, Any] | None:
    code = str(error_code or "").strip()
    if not code:
        return None
    template = _MAP.get(code)
    if template is None:
        return {
            "code": code,
            "classification": "unknown",
            "severity": "error",
            "title": "Unknown ATP runtime error",
            "action": "Inspect error_detail and ATP logs, then update diagnostic mapping if needed.",
            "docs_ref": "docs/ATP_REPL_ERRORS.md",
            "detail_keys": sorted((error_detail or {}).keys()),
        }
    return {
        "code": code,
        "classification": template["classification"],
        "severity": template["severity"],
        "title": template["title"],
        "action": template["action"],
        "docs_ref": "docs/ATP_REPL_ERRORS.md",
        "detail_keys": sorted((error_detail or {}).keys()),
    }
