from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from breadboard.rl.phase5.runtime_capability_payload import (
    COMPONENT,
    FIXED_NONCE_SHA256,
    MANIFEST_MEMBER,
    REPORT_ID,
    REPORT_SCHEMA,
    canonical_json_bytes,
    construct_runtime_capability_payload,
    sha256_bytes,
)
from scripts.rl_phase5.build_transport_smoke_payload import _publish_exclusive

PAYLOAD_NAME = "transport-smoke-payload.zip"
RECEIPT_NAME = "transport-smoke-payload-build.json"
RECEIPT_SCHEMA = "bb.rl.phase5.runtime-preflight-capability-payload-build.v1"
CLAIM_BOUNDARY = (
    "local_deterministic_capability_build_and_cooperative_atomic_visibility_only"
)


def build(
    *,
    destination: Path,
    command_id: str,
    requested_target_run_id: str,
    runner_source_sha256: str,
    runner_test_sha256: str,
    runtime_source_sha256: str,
    runtime_test_sha256: str,
) -> dict[str, Any]:
    component_input = {
        "command_id": command_id,
        "fixed_nonce_sha256": FIXED_NONCE_SHA256,
        "requested_target_run_id": requested_target_run_id,
        "runner_source_sha256": runner_source_sha256,
        "runner_test_sha256": runner_test_sha256,
        "runtime_source_sha256": runtime_source_sha256,
        "runtime_test_sha256": runtime_test_sha256,
    }
    first_payload, first_manifest = construct_runtime_capability_payload(component_input)
    second_payload, second_manifest = construct_runtime_capability_payload(component_input)
    if first_payload != second_payload or first_manifest != second_manifest:
        raise RuntimeError("runtime capability payload build is nondeterministic")

    receipt: dict[str, Any] = {
        "admission_binding": (
            "authority_admission_sha256_equals_canonical_receipt_sha256"
        ),
        "admission_revalidation_required": True,
        "campaign_admission": False,
        "claim_boundary": CLAIM_BOUNDARY,
        "command_id": command_id,
        "component_identity": {
            "component": COMPONENT,
            "report_id": REPORT_ID,
            "schema_version": REPORT_SCHEMA,
        },
        "component_input": component_input,
        "component_input_sha256": sha256_bytes(canonical_json_bytes(component_input)),
        "deterministic_double_build": True,
        "fixed_nonce_sha256": FIXED_NONCE_SHA256,
        "incomplete_without_receipt": True,
        "passed": True,
        "payload_manifest_member": MANIFEST_MEMBER,
        "payload_manifest_sha256": sha256_bytes(first_manifest),
        "payload_manifest_size_bytes": len(first_manifest),
        "payload_path": PAYLOAD_NAME,
        "payload_sha256": sha256_bytes(first_payload),
        "payload_size_bytes": len(first_payload),
        "publication_guarantee": "atomic_visibility_only",
        "publication_state": "complete",
        "requested_target_run_id": requested_target_run_id,
        "runner_source_sha256": runner_source_sha256,
        "runner_test_sha256": runner_test_sha256,
        "same_uid_mutation_exclusion": False,
        "schema_version": RECEIPT_SCHEMA,
        "target_execution": False,
        "transport_authority": False,
    }
    receipt_raw = canonical_json_bytes(receipt)
    parent = destination.parent
    if (
        not destination.is_absolute()
        or destination.name in {"", ".", ".."}
        or "/" in destination.name
        or parent.resolve(strict=True) != parent
    ):
        raise ValueError("destination must be an absolute child of a canonical directory")
    _publish_exclusive(
        parent=parent,
        leaf=destination.name,
        payload_raw=first_payload,
        receipt_raw=receipt_raw,
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a typed deterministic RC4 Linux capability packet."
    )
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--command-id", required=True)
    parser.add_argument("--requested-target-run-id", required=True)
    parser.add_argument("--runner-source-sha256", required=True)
    parser.add_argument("--runner-test-sha256", required=True)
    parser.add_argument("--runtime-source-sha256", required=True)
    parser.add_argument("--runtime-test-sha256", required=True)
    args = parser.parse_args()

    destination_argument = args.destination
    if destination_argument.name in {"", ".", ".."}:
        raise ValueError("destination must name a child directory")
    destination = destination_argument.parent.resolve(strict=True) / destination_argument.name
    receipt = build(
        destination=destination,
        command_id=args.command_id,
        requested_target_run_id=args.requested_target_run_id,
        runner_source_sha256=args.runner_source_sha256,
        runner_test_sha256=args.runner_test_sha256,
        runtime_source_sha256=args.runtime_source_sha256,
        runtime_test_sha256=args.runtime_test_sha256,
    )
    print(canonical_json_bytes(receipt).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
