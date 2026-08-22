from __future__ import annotations

import base64
import copy
import functools
import hashlib
import hmac
import io
import json
import tarfile
import subprocess
import tempfile
from pathlib import Path

import pytest

import breadboard.rl.phase5.f2_terminal as f2_terminal_module
from breadboard.rl.harness.composition import (
    HmacSha256ReceiptAuthenticator,
    ArtifactFileRefV1,
    CallbackJournalVerificationReceiptV1,
    EvidenceReceiptSignatureV1,
    EvidenceReceiptSigningAuthorityV1,
    OuterBridgeCleanupReceiptV1,
    OuterBridgeLeaseV1,
    PreboundServiceSocketLeaseV1,
    PolicyTlsTrustAuthorityV1,
    TlsCallbackPolicyV1,
    TlsCallbackRuntimeInputV1,
)
from breadboard.rl.phase5.f2_runtime import (
    FixedPolicyAuthorityRefs,
    F2RuntimeError,
    OperatorAuthorityInputs,
    OuterBridgePlanInputs,
    PreboundServiceSocketPlanInputs,
    TerminalPackageInputs,
    materialize_terminal_package,
    stock_docker_blocker,
    write_terminal_package,
)
from breadboard.rl.phase5.f2_terminal import (
    F1_PREREQUISITE_ID,
    F1_PREREQUISITE_REF,
    F1_PREREQUISITE_ROOT,
    MARKER_PREFIX,
    MARKER_SCHEMA,
    OUTER_ARTIFACTS,
    REPORT_SCHEMA,
    RUNNER_ARTIFACTS,
    export_f2_artifacts_from_raw,
    TARGET_ARTIFACTS,
    F2ValidationError,
    canonical_json_bytes,
    parse_artifact_markers,
    promote,
    safe_extract_archive,
    sha256_ref,
    validate_scratch,
    verify_canonical,
)


WRAPPER_IMAGE_REF = "reviewed-wrapper@sha256:" + "9" * 64

BRIDGE_ID = "c" * 64
BRIDGE_LABEL_BYTES = b"f2-network-authority"
BRIDGE_LABEL_REF = sha256_ref(BRIDGE_LABEL_BYTES)
BRIDGE_LABELS = [{"key": "bb.rl.f2.network", "value": BRIDGE_LABEL_REF}]
BRIDGE_CLEANUP_BYTES = b"f2-outer-bridge-cleanup-authority"
BRIDGE_CLEANUP_REF = sha256_ref(BRIDGE_CLEANUP_BYTES)
TEST_OPENSSL = "/opt/homebrew/opt/openssl@3/bin/openssl"

REF_BYTES: dict[str, bytes] = {}


def raw_ref(raw: bytes) -> str:
    digest = sha256_ref(raw)
    REF_BYTES[digest] = raw
    return digest


def ref(label: str) -> str:
    return raw_ref(label.encode())
def image(label: str) -> str:
    return label + "@" + ref(label)

@functools.cache
def ed25519_material(attempt_id: str) -> tuple[bytes, bytes, bytes]:
    with tempfile.TemporaryDirectory(prefix=f"{attempt_id}-ed25519-") as temporary:
        root = Path(temporary)
        private_path = root / "private.pem"
        public_path = root / "public.pem"
        der_path = root / "public.der"
        subprocess.run(
            [TEST_OPENSSL, "genpkey", "-algorithm", "ED25519", "-out", str(private_path)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [TEST_OPENSSL, "pkey", "-in", str(private_path), "-pubout", "-out", str(public_path)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [TEST_OPENSSL, "pkey", "-pubin", "-in", str(public_path), "-outform", "DER", "-out", str(der_path)],
            check=True,
            capture_output=True,
        )
        return private_path.read_bytes(), public_path.read_bytes(), der_path.read_bytes()


def sign_ed25519(private_key: bytes, payload: bytes) -> bytes:
    with tempfile.TemporaryDirectory(prefix="f2-sign-") as temporary:
        root = Path(temporary)
        private_path = root / "private.pem"
        payload_path = root / "payload"
        signature_path = root / "signature"
        private_path.write_bytes(private_key)
        payload_path.write_bytes(payload)
        subprocess.run(
            [TEST_OPENSSL, "pkeyutl", "-sign", "-rawin", "-inkey", str(private_path), "-in", str(payload_path), "-out", str(signature_path)],
            check=True,
            capture_output=True,
        )
        return signature_path.read_bytes()


RECEIPT_AUTHENTICATOR = HmacSha256ReceiptAuthenticator(
    key_id="signer-1",
    key=b"f2-receipt-authenticator-key-material",
)


def signed_receipt(model_type: type, **payload: object) -> object:
    auth_digest = sha256_ref(canonical_json_bytes(payload))
    unsigned = {
        **payload,
        "signer_key_id": RECEIPT_AUTHENTICATOR.key_id,
        "signature_algorithm": RECEIPT_AUTHENTICATOR.algorithm,
        "auth_digest": auth_digest,
    }
    signature = RECEIPT_AUTHENTICATOR.sign(canonical_json_bytes(unsigned)).hex()
    return model_type(**unsigned, signature=signature)




def write_json(path: Path, value: object) -> None:
    path.write_bytes(canonical_json_bytes(value))


def fixed_policy(episode: str) -> tuple[dict[str, object], str, str]:
    authority = {"code_digest": ref("code"), "script_digest": ref("script"), "model_label_digest": ref("model-label"), "instance_digest": ref("instance"), "model_label": "fixed-model", "shell_command": "cat answer.txt", "completion": "done"}
    binding_digest = ref("binding")
    def response(turn: int, request_digest: str, name: str, arguments: dict[str, str], *, prior_request: str | None, prior_tool: str | None, observation: str | None) -> tuple[dict[str, object], str]:
        arguments_raw = canonical_json_bytes(arguments).decode()
        seed = {"schema_version": "bb.wrapper.fixed-policy-call.v1", "episode_id": episode, "binding_digest": binding_digest, "request_digest": request_digest}
        if turn == 1: seed.update({"script_digest": authority["script_digest"], "name": name, "arguments": arguments_raw})
        else: seed.update({"prior_tool_call_digest": prior_tool, "observation_digest": observation, "name": name, "arguments": arguments_raw})
        tool_digest = raw_ref(canonical_json_bytes(seed))
        call = {"type": "function_call", "call_id": "call_" + tool_digest[7:31], "name": name, "arguments": arguments_raw}
        metadata = {"schema_version": "bb.wrapper.fixed-policy-response-binding.v1", "intelligence_claim": None, "turn": turn, **{key: authority[key] for key in ("code_digest", "script_digest", "model_label_digest", "instance_digest")}, "request_digest": request_digest, "tool_call_digest": tool_digest, "observation_digest": observation, "prior_request_digest": prior_request, "prior_tool_call_digest": prior_tool}
        metadata["response_content_digest"] = raw_ref(canonical_json_bytes({"output": [call], "binding": metadata}))
        payload = {"id": "resp_" + metadata["response_content_digest"][7:31], "object": "response", "model": authority["model_label"], "output": [call], "metadata": {"breadboard_fixed_policy": metadata}}
        return payload, tool_digest
    payload1 = {"model": "fixed-model", "input": "solve"}
    request1 = raw_ref(canonical_json_bytes(payload1))
    response1, tool_digest = response(1, request1, "shell", {"command": authority["shell_command"]}, prior_request=None, prior_tool=None, observation=None)
    call = response1["output"][0]
    tool_output = {"type": "function_call_output", "call_id": call["call_id"], "output": "ok"}
    observation_ref = raw_ref(canonical_json_bytes(tool_output))
    payload2 = {"model": "fixed-model", "input": [call, tool_output]}
    request2 = raw_ref(canonical_json_bytes(payload2))
    response2, _ = response(2, request2, "submit", {"result": authority["completion"]}, prior_request=request1, prior_tool=tool_digest, observation=observation_ref)
    turns = []
    observations = []
    routes = []
    for number, payload, request_digest, response_payload in ((1, payload1, request1, response1), (2, payload2, request2, response2)):
        response_digest = raw_ref(canonical_json_bytes(response_payload))
        turn = {"schema_version": "bb.rl.policy-http-request.v1", "episode_id": episode, "effective_plan_digest": ref("plan"), "binding_digest": binding_digest, "policy_slot_id": "slot-1", "request_digest": request_digest, "request_payload": payload, "turn": number, "attempt": 1, "response_digest": response_digest, "response_payload": response_payload}
        turns.append(turn)
        request_body = {key: turn[key] for key in ("schema_version", "episode_id", "effective_plan_digest", "binding_digest", "policy_slot_id", "request_digest", "request_payload", "turn", "attempt")}
        response_body = {"response_digest": response_digest, "response_payload": response_payload}
        observations.append({"path": "/v1/responses", "request_body_sha256": raw_ref(canonical_json_bytes(request_body)), "request_digest": request_digest, "response_body_sha256": raw_ref(canonical_json_bytes(response_body)), "response_digest": response_digest})
        routes.append({"connected_peer_ip": "10.42.0.7", "tls_version": "TLSv1.3", "cipher": "TLS_AES_256_GCM_SHA384", "leaf_der_digest": ref("leaf-der"), "ca_authority_digest": ref("ca-cert"), "server_name": "10.88.0.1", "server_certificate_verified": True, "hostname_verified": True, "bearer_authenticated": True, "mutual_tls": False, "network_grant_ref": ref("network-grant"), "route_digest": ref("route-revision"), "request_digest": request_digest, "response_digest": response_digest})
    return {"authority": authority, "turns": turns, "callback_observations": observations, "tls_route_observations": routes}, call["call_id"], observation_ref


def prebound_socket_plan(role: str, port: int, inode: int) -> PreboundServiceSocketPlanInputs:
    value = {"schema_version": "bb.rl.harness-prebound-service-socket-plan.v1", "role": role, "gateway": "10.88.0.1", "observed_port": port, "family": "AF_INET", "socket_type": "SOCK_STREAM", "protocol": "IPPROTO_TCP", "socket_device": 7, "socket_inode": inode, "socket_mode": 0o140600, "socket_owner_uid": 1000, "getsockname_host": "10.88.0.1", "getsockname_port": port, "ip_freebind": True}
    value["socket_plan_id"] = sha256_ref(canonical_json_bytes(value))
    return PreboundServiceSocketPlanInputs.model_validate(value)


def tls_callback_input(
    callback_plan: PreboundServiceSocketPlanInputs,
    *,
    leaf_pem: bytes = b"leaf-cert",
) -> TlsCallbackRuntimeInputV1:
    ca_ref = ArtifactFileRefV1(
        path="/run/f2/callback-ca.pem",
        sha256=ref("ca-cert"),
        size_bytes=7,
        media_type="application/x-pem-file",
    )
    leaf_ref = ArtifactFileRefV1(
        path="/run/f2/callback-leaf.pem",
        sha256=raw_ref(leaf_pem),
        size_bytes=len(leaf_pem),
        media_type="application/x-pem-file",
    )
    return TlsCallbackRuntimeInputV1(
        schema_version="bb.rl.harness-tls-callback-runtime-input.v1",
        route_id="f2-fixed-policy-callback",
        host=callback_plan.gateway,
        observed_port=callback_plan.observed_port,
        socket_role="callback_tls",
        socket_plan_id=callback_plan.socket_plan_id,
        ca_certificate_ref=ca_ref,
        leaf_certificate_ref=leaf_ref,
        ca_certificate_sha256=ca_ref.sha256,
        leaf_certificate_sha256=leaf_ref.sha256,
        leaf_public_key_sha256=ref("leaf-public-key"),
        private_key_secret_handle_id="callback-tls-private-key",
        tls_policy=TlsCallbackPolicyV1(
            minimum_tls_version="TLSv1.3",
            maximum_tls_version="TLSv1.3",
            server_certificate_verification_required=True,
            hostname_verification_required=True,
            bearer_authentication_required=True,
            mutual_tls_required=False,
        ),
    )


def valid_documents(
    attempt_id: str = "f2-real-one",
    *,
    leaf_pem: bytes = b"leaf-cert",
) -> dict[str, object]:
    REF_BYTES.clear()
    REF_BYTES[BRIDGE_LABEL_REF] = BRIDGE_LABEL_BYTES
    REF_BYTES[BRIDGE_CLEANUP_REF] = BRIDGE_CLEANUP_BYTES
    episode = "episode-real-1"
    names = {name: ref(name) for name in ("package", "config", "policy", "tool", "sandbox", "verifier", "reward", "task", "operator", "selection", "plan", "completed", "closed", "cleanup")}
    evidence_bytes = canonical_json_bytes({"schema_version": "bb.rl.execution-evidence.v1", "episode_id": episode})
    names["evidence"] = raw_ref(evidence_bytes)
    policy_contract, shell_call_id, observation_ref = fixed_policy(episode)
    receipts = {"container_id": "container-1", "lease_id": "lease-1", "workspace_id": "workspace-1"}
    task_image = verifier_image = image("private-task-verifier")
    policy_authority = {field: policy_contract["authority"][field] for field in ("code_digest", "script_digest", "model_label_digest", "instance_digest")}
    bridge_plan_model = OuterBridgePlanInputs(schema_version="bb.rl.harness-outer-bridge-plan.v1", network_name="bb-f2-bridge", driver="bridge", subnet="10.88.0.0/24", gateway="10.88.0.1", internal=True, labels=tuple(BRIDGE_LABELS), cleanup_owner="f2_outer_orchestrator", cleanup_ref=BRIDGE_CLEANUP_REF)
    bridge_plan = bridge_plan_model.model_dump(mode="json")
    socket_plan_models = tuple(prebound_socket_plan(role, port, inode) for role, port, inode in (("callback_tls", 18443, 103), ("fixed_policy", 18081, 101), ("harness", 18080, 102)))
    composition_digest = ref("composition")
    planned_inspect = canonical_json_bytes({"Id": BRIDGE_ID, "Name": bridge_plan_model.network_name, "Driver": "bridge", "Internal": True, "Subnet": bridge_plan_model.subnet, "Gateway": bridge_plan_model.gateway, "Labels": {label.key: label.value for label in bridge_plan_model.labels}})
    planned_lease = signed_receipt(
        OuterBridgeLeaseV1,
        schema_version="bb.rl.harness-outer-bridge-lease.v1",
        composition_digest=composition_digest,
        plan_digest=bridge_plan_model.canonical_digest(),
        broker_pid=200,
        broker_starttime="1000",
        broker_mount_namespace="mnt:[1]",
        daemon_instance_id="daemon-1",
        daemon_pid=201,
        daemon_starttime="1001",
        daemon_pid_namespace="pid:[1]",
        network_id=BRIDGE_ID,
        network_name=bridge_plan_model.network_name,
        inspect_media_type="application/vnd.breadboard.docker-network-inspect+json;version=1",
        inspect_bytes_base64=base64.b64encode(planned_inspect).decode(),
        inspect_sha256=sha256_ref(planned_inspect),
        created_at="2026-07-11T00:00:00Z",
        lease_expires_at="2026-07-11T01:00:00Z",
        lease_id="sha256:" + "6" * 64,
    )
    REF_BYTES[planned_lease.canonical_digest()] = planned_lease.canonical_bytes()
    for route in policy_contract["tls_route_observations"]:
        route["network_grant_ref"] = planned_lease.canonical_digest()
    socket_plans = [model.model_dump(mode="json") for model in socket_plan_models]
    private_key, public_key, public_spki = ed25519_material(attempt_id)
    public_key_ref = ArtifactFileRefV1(
        path="/run/f2/evidence-receipt-public.pem",
        sha256=raw_ref(public_key),
        size_bytes=len(public_key),
        media_type="application/x-pem-file",
    )
    evidence_receipt_authority = EvidenceReceiptSigningAuthorityV1(
        schema_version="bb.rl.harness-evidence-receipt-signing-authority.v1",
        attempt_id=attempt_id,
        composition_digest=composition_digest,
        evidence_policy_digest=ref("evidence-policy"),
        algorithm="Ed25519",
        public_key_ref=public_key_ref,
        public_key_sha256=public_key_ref.sha256,
        public_key_spki_sha256=raw_ref(public_spki),
        private_key_secret_handle_id="evidence-receipt-signing-key",
        openssl_authority_digest=ref("openssl-authority"),
    )
    prerequisite = {"schema_version": "bb.rl.f2.f1-prerequisite.v1", "canonical_id": F1_PREREQUISITE_ID, "report_schema": "bb.rl.f1.ibm-exact-container-preflight-report.v3", "report_ref": F1_PREREQUISITE_REF, "canonical_root": F1_PREREQUISITE_ROOT}
    docs: dict[str, object] = {
        "source": {"schema_version": "bb.rl.f2.source.v1", "breadboard_head": ref("bb-head"), "wrapper_head": ref("wrapper-head"), "tree_ref": ref("source-tree"), "payload_ref": ref("payload")},
        "prerequisite": prerequisite,
        "terminal_package": {"schema_version": "bb.rl.f2.terminal-package.v1", "package_ref": names["package"], "selector_kind": "direct", "overlay_refs": [], "adapter_id": "breadboard.terminal-responses.v1", "runtime_class": "hardened_docker", "image_ref": WRAPPER_IMAGE_REF, "task_image_ref": task_image, "verifier_image_ref": verifier_image, "composition_digest": composition_digest, "outer_bridge_plan": bridge_plan, "prebound_service_socket_plans": socket_plans, "f1_prerequisite": prerequisite, "config_ref": names["config"], "policy_ref": names["policy"], "policy_authority": policy_authority, "tool_ref": names["tool"], "verifier_ref": names["verifier"], "reward_ref": names["reward"], "task_ref": names["task"], "evidence_ref": names["evidence"], "operator_authority_ref": names["operator"], "execution_path": ["launch/generate_nemo.sh", "launch/eval_nemo.sh", "recipe.nemo_async.evals.run"]},
        "selection": {"schema_version": "bb.rl.f2.selection.v1", "ref": names["selection"], "episode_id": episode, "selector_kind": "direct", "overlay_refs": [], "config_ref": names["config"], "effective_plan_ref": names["plan"]},
        "effective_plan": {"schema_version": "bb.rl.f2.effective-plan.v1", "ref": names["plan"], "episode_id": episode, "selection_ref": names["selection"], "config_ref": names["config"], "policy_ref": names["policy"], "tool_ref": names["tool"], "sandbox_ref": names["sandbox"], "verifier_ref": names["verifier"], "reward_ref": names["reward"], "artifact_ref": names["evidence"]},
        "policy": {"schema_version": "bb.rl.f2.policy-observation.v1", "ref": names["policy"], "episode_id": episode, "provenance": "production-fixed-real-policy", "request_ref": policy_contract["turns"][0]["request_digest"], "response_ref": policy_contract["turns"][1]["response_digest"], "order": 0, "tool_call_id": shell_call_id, **policy_contract},
        "tool": {"schema_version": "bb.rl.f2.tool-observation.v1", "ref": names["tool"], "episode_id": episode, "tool_id": "shell", "tool_call_id": shell_call_id, "command_ref": ref("command"), "observation_ref": observation_ref, "order": 1},
        "sandbox": {"schema_version": "bb.rl.f2.sandbox-attestation.v1", "ref": names["sandbox"], "episode_id": episode, "runtime_class": "hardened_docker", "image_ref": task_image, "runtime_ref": ref("runtime"), "security_ref": ref("security"), "network_ref": ref("network"), "task_ref": names["task"], **receipts, "started_at": "2026-07-11T00:00:00Z"},
        "verifier": {"schema_version": "bb.rl.f2.verifier-attestation.v1", "ref": names["verifier"], "episode_id": episode, "provenance": "production", "image_ref": verifier_image, "verifier_ref": names["verifier"], "tool_observation_ref": names["tool"], "artifact_ref": names["evidence"], "reward_ref": names["reward"], "container_id": "verifier-container-1", "lease_id": "verifier-lease-1", "snapshot_ref": ref("sealed-snapshot"), "credential_refs": [], "finished_at": "2026-07-11T00:00:01Z"},
        "reward": {"schema_version": "bb.rl.f2.reward.v1", "ref": names["reward"], "episode_id": episode, "value": 1.0, "components": {"verified": 1.0}},
        "completed": {"schema_version": "bb.rl.f2.completed-envelope.v1", "ref": names["completed"], "episode_id": episode, "status": "completed", "observed_at": "2026-07-11T00:00:02Z", "artifact_ref": names["evidence"], "reward_ref": names["reward"], "resource_receipts": receipts, "cleanup": "pending"},
        "closed": {"schema_version": "bb.rl.f2.closed-envelope.v1", "ref": names["closed"], "episode_id": episode, "status": "closed", "observed_at": "2026-07-11T00:00:03Z", "completed_ref": names["completed"], "resource_receipts": receipts, "cleanup_ref": names["cleanup"]},
        "cleanup": {"schema_version": "bb.rl.f2.cleanup.v1", "ref": names["cleanup"], "episode_id": episode, "released": True, "processes": [], "containers": [], "leases": [], "workspaces": [], "caches": [], "secrets": []},
    }
    docs["terminal_package"]["tls_callback_runtime_input"] = tls_callback_input(socket_plan_models[0], leaf_pem=leaf_pem).model_dump(mode="json")  # type: ignore[index]
    tls_runtime = TlsCallbackRuntimeInputV1.model_validate(
        docs["terminal_package"]["tls_callback_runtime_input"]
    )
    docs["terminal_package"]["policy_tls_trust_authority"] = PolicyTlsTrustAuthorityV1(
        schema_version="bb.rl.policy-tls-trust-authority.v1",
        route_id=tls_runtime.route_id,
        server_name=tls_runtime.host,
        ca_bundle_ref=tls_runtime.ca_certificate_ref,
        expected_leaf_certificate_sha256=ref("leaf-der"),
        minimum_tls_version="TLSv1.3",
        cipher_suite="TLS_AES_256_GCM_SHA384",
        dedicated_single_leaf_ca=True,
    ).model_dump(mode="json")
    docs["terminal_package"]["evidence_receipt_signing_authority"] = evidence_receipt_authority.model_dump(mode="json")  # type: ignore[index]
    docs["eval_row"] = {"schema_version": "bb.rl.f2.eval-row.v1", "row_id": "row-1", "rollout_id": "rollout-1", "episode_id": episode, "status": "closed", "package_ref": names["package"], "selection_ref": names["selection"], "effective_plan_ref": names["plan"], "policy_ref": names["policy"], "tool_ref": names["tool"], "sandbox_ref": names["sandbox"], "verifier_ref": names["verifier"], "reward_ref": names["reward"], "artifact_roots": [names["evidence"]], "completed_envelope_ref": names["completed"], "closed_envelope_ref": names["closed"]}
    docs["eval_summary"] = {"schema_version": "bb.rl.f2.eval-summary.v1", "row_count": 1, "rollout_count": 1, "episode_count": 1, "row_ids": ["row-1"], "episode_ids": [episode], "status": "closed"}
    graph_objects = [{"ref": digest, "size": len(raw), "media_type": "application/octet-stream", "bytes_b64": base64.b64encode(raw).decode(), "parents": [] if digest != names["evidence"] else sorted(set(REF_BYTES) - {digest}), "producer": "breadboard"} for digest, raw in REF_BYTES.items()]
    docs["artifact_graph"] = {"schema_version": "bb.rl.f2.artifact-graph.v1", "roots": [names["evidence"]], "objects": graph_objects}
    return docs


def archive_bytes(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for filename, raw in sorted(entries.items()):
            info = tarfile.TarInfo(filename)
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    return output.getvalue()

def callback_packet(
    documents: dict[str, object],
    attempt_id: str,
) -> dict[str, bytes]:
    package = documents["terminal_package"]
    tls = package["tls_callback_runtime_input"]
    authority = EvidenceReceiptSigningAuthorityV1.model_validate(
        package["evidence_receipt_signing_authority"]
    )
    policy = documents["policy"]
    records: list[dict[str, object]] = [{
        "schema_version": "bb.wrapper.callback-tls-route-observation.v1",
        "route_id": tls["route_id"],
        "route_revision_digest": policy["tls_route_observations"][0]["route_digest"],
        "dns_policy_digest": ref("dns-policy"),
        "ip_policy_digest": ref("ip-policy"),
        "bind_address": tls["host"],
        "bind_port": tls["observed_port"],
        "server_hostname": tls["host"],
        "minimum_tls_version": "TLSv1.3",
        "cipher_suite": "TLS_AES_256_GCM_SHA384",
        "ca_bundle_sha256": tls["ca_certificate_sha256"],
        "ca_certificate_pem": "ca-cert",
        "leaf_certificate_sha256": tls["leaf_certificate_sha256"],
        "leaf_certificate_pem": REF_BYTES[tls["leaf_certificate_sha256"]].decode(),
    }]
    for index, (turn, observation, transport) in enumerate(
        zip(
            policy["turns"],
            policy["callback_observations"],
            policy["tls_route_observations"],
            strict=True,
        ),
        start=1,
    ):
        records.append({
            "schema_version": "bb.wrapper.callback-turn-observation.v1",
            "episode_id": turn["episode_id"],
            "effective_plan_digest": turn["effective_plan_digest"],
            "binding_digest": turn["binding_digest"],
            "policy_slot_id": turn["policy_slot_id"],
            "turn": index,
            "attempt": 1,
            **observation,
            "response_payload": turn["response_payload"],
            "transport": transport,
        })
    key = hashlib.sha256(("journal-" + attempt_id).encode()).digest()
    previous = "hmac-sha256:" + "0" * 64
    journal_lines: list[bytes] = []
    for sequence, record in enumerate(records, start=1):
        unsigned = {
            "schema_version": "bb.wrapper.observation-journal-entry.v1",
            "sequence": sequence,
            "idempotency_key": sha256_ref(canonical_json_bytes({"sequence": sequence, "record": record})),
            "previous_entry_mac": previous,
            "record_digest": sha256_ref(canonical_json_bytes(record)),
            "record": record,
            "committed": True,
        }
        previous = "hmac-sha256:" + hmac.new(
            key, canonical_json_bytes(unsigned), hashlib.sha256
        ).hexdigest()
        journal_lines.append(canonical_json_bytes({**unsigned, "entry_mac": previous}))
    journal = b"\n".join(journal_lines) + b"\n"
    snapshot = canonical_json_bytes({
        "schema_version": "bb.wrapper.callback-observation-snapshot.v1",
        "entry_count": 3,
        "head_entry_mac": previous,
        "records": records,
    })
    receipt = CallbackJournalVerificationReceiptV1(
        schema_version="bb.rl.callback-journal-verification-receipt.v1",
        attempt_id=attempt_id,
        composition_digest=package["composition_digest"],
        route_id=tls["route_id"],
        journal_ref=ArtifactFileRefV1(
            path="/run/f2/callback-observations/journal.jsonl",
            sha256=sha256_ref(journal),
            size_bytes=len(journal),
            media_type="application/x-ndjson",
        ),
        snapshot_ref=ArtifactFileRefV1(
            path="/run/f2/callback-observations/snapshot.json",
            sha256=sha256_ref(snapshot),
            size_bytes=len(snapshot),
            media_type="application/json",
        ),
        head_mac=previous.removeprefix("hmac-sha256:"),
        event_count=3,
        chain_verified=True,
        snapshot_verified=True,
        evidence_policy_digest=authority.evidence_policy_digest,
        signer_public_key_spki_sha256=authority.public_key_spki_sha256,
        signer_authority_digest=authority.canonical_digest(),
    )
    receipt_raw = receipt.canonical_bytes()
    private_key, public_key, _ = ed25519_material(attempt_id)
    signature = EvidenceReceiptSignatureV1(
        schema_version="bb.rl.evidence-receipt-signature.v1",
        algorithm="Ed25519",
        signer_authority_digest=authority.canonical_digest(),
        receipt_digest=receipt.canonical_digest(),
        signature_base64=base64.b64encode(sign_ed25519(private_key, receipt_raw)).decode(),
    )
    return {
        "callback_observation_journal": journal,
        "callback_observation_snapshot": snapshot,
        "callback_verification_authority": authority.canonical_bytes(),
        "callback_verification_public_key": public_key,
        "callback_verification_receipt": receipt_raw,
        "callback_verification_signature": signature.canonical_bytes(),
    }


def make_attempt(
    root: Path,
    docs: dict[str, object] | None = None,
    name: str = "f2-real-one",
    *,
    bridge_auth_mutation: str | None = None,
    callback_mutation: str | None = None,
    scheduler_mutation: str | None = None,
) -> Path:
    attempt = root / name
    artifacts = attempt / "artifacts"
    runner = attempt / "runner"
    outer = attempt / "outer"
    artifacts.mkdir(parents=True)
    runner.mkdir()
    outer.mkdir()
    documents = docs if docs is not None else valid_documents(name)
    target_raw: dict[str, bytes] = {}
    markers: list[bytes] = []
    for key, filename in TARGET_ARTIFACTS.items():
        raw = canonical_json_bytes(documents[key])
        (artifacts / filename).write_bytes(raw)
        target_raw[filename] = raw
        markers.append(MARKER_PREFIX.encode() + canonical_json_bytes({"schema_version": MARKER_SCHEMA, "attempt_id": name, "name": key, "path": "artifacts/" + filename, "sha256": sha256_ref(raw), "size": len(raw)}))
    stdout = b"\n".join(markers) + b"\n"
    stderr = b""
    target_run_id = "20260711T000000Z-slurm-123"
    command_id = "command-1"
    payload_ref = ref("payload")
    invocation = {"schema_version": "bb.rl.f2.runner-invocation.v1", "attempt_id": name, "target_run_id": target_run_id, "job_id": "123", "node": "ibm-node-1", "payload_ref": payload_ref}
    callback_raw = callback_packet(documents, name)
    if callback_mutation == "journal":
        callback_raw["callback_observation_journal"] += b"x"
    elif callback_mutation == "count":
        snapshot = json.loads(callback_raw["callback_observation_snapshot"])
        snapshot["entry_count"] = 2
        callback_raw["callback_observation_snapshot"] = canonical_json_bytes(snapshot)
    elif callback_mutation == "signer":
        receipt = json.loads(callback_raw["callback_verification_receipt"])
        receipt["signer_public_key_spki_sha256"] = ref("wrong-signer")
        callback_raw["callback_verification_receipt"] = canonical_json_bytes(receipt)
    elif callback_mutation == "signature":
        signature = json.loads(callback_raw["callback_verification_signature"])
        signature["signature_base64"] = base64.b64encode(b"x" * 64).decode()
        callback_raw["callback_verification_signature"] = canonical_json_bytes(signature)
    elif callback_mutation == "public-key":
        callback_raw["callback_verification_public_key"] += b"x"
    write_json(runner / RUNNER_ARTIFACTS["invocation"], invocation)
    (runner / RUNNER_ARTIFACTS["stdout"]).write_bytes(stdout)
    (runner / RUNNER_ARTIFACTS["stderr"]).write_bytes(stderr)
    write_json(runner / RUNNER_ARTIFACTS["exit"], {"schema_version": "bb.rl.f2.runner-exit.v1", "returncode": 0})
    for key, raw in callback_raw.items():
        (runner / RUNNER_ARTIFACTS[key]).write_bytes(raw)
    result_archive = archive_bytes(target_raw)
    (attempt / "result.tar.gz").write_bytes(result_archive)
    scheduler = {"schema_version": "bb.rl.f2.scheduler-observation.v1", "target_alias": "ZYPHRA_IBM_AMD_1", "requested": {"partition": "gpu", "nodes": 1, "tasks": 1, "gpus": 1}, "observed": {"job_id": "123", "partition": "gpu", "node_list": "ibm-node-1", "node_count": 1, "task_count": 1, "gpus_on_node": "1", "hostname": "ibm-node-1"}, "os": {"system": "Linux", "release": "x", "machine": "x86_64"}, "started_utc": "2026-07-11T00:00:00Z", "scontrol": {"argv": ["scontrol", "show", "job", "-o", "123"], "exit_code": 0, "stdout": "JobId=123 TresPerNode=gres:gpu:1\n", "stderr": ""}}
    if scheduler_mutation == "gpu-empty":
        scheduler["observed"]["gpus_on_node"] = ""
    elif scheduler_mutation == "gpu-zero":
        scheduler["observed"]["gpus_on_node"] = "0"
    elif scheduler_mutation == "gpu-multiple":
        scheduler["observed"]["gpus_on_node"] = "2"
        scheduler["scontrol"]["stdout"] = "JobId=123 TresPerNode=gres:gpu:2\n"
    elif scheduler_mutation == "scontrol-missing":
        scheduler["scontrol"]["stdout"] = "JobId=123 TresPerNode=N/A\n"
    component = {"schema_version": "bb.rl.f2.phase3-component.v1", "report_id": name, "target_run_id": target_run_id, "passed": True, "blocked_reason": "", "promotion_allowed": False, "point_award_allowed": False, "bead_closure_allowed": False}
    package_doc = documents["terminal_package"]
    bridge_plan_model = OuterBridgePlanInputs.model_validate(package_doc["outer_bridge_plan"])
    inspect_bytes = canonical_json_bytes({"Id": BRIDGE_ID, "Name": bridge_plan_model.network_name, "Driver": "bridge", "Internal": True, "Subnet": bridge_plan_model.subnet, "Gateway": bridge_plan_model.gateway, "Labels": {label.key: label.value for label in bridge_plan_model.labels}})
    bridge_lease_model = signed_receipt(
        OuterBridgeLeaseV1,
        schema_version="bb.rl.harness-outer-bridge-lease.v1",
        composition_digest=package_doc["composition_digest"],
        plan_digest=bridge_plan_model.canonical_digest(),
        broker_pid=200,
        broker_starttime="1000",
        broker_mount_namespace="mnt:[1]",
        daemon_instance_id="daemon-1",
        daemon_pid=201,
        daemon_starttime="1001",
        daemon_pid_namespace="pid:[1]",
        network_id=BRIDGE_ID,
        network_name=bridge_plan_model.network_name,
        inspect_media_type="application/vnd.breadboard.docker-network-inspect+json;version=1",
        inspect_bytes_base64=base64.b64encode(inspect_bytes).decode(),
        inspect_sha256=sha256_ref(inspect_bytes),
        created_at="2026-07-11T00:00:00Z",
        lease_expires_at="2026-07-11T01:00:00Z",
        lease_id="sha256:" + "6" * 64,
    )
    bridge_lease = bridge_lease_model.model_dump(mode="json")
    bridge_lease_digest = bridge_lease_model.canonical_digest()
    socket_leases = []
    for plan in (PreboundServiceSocketPlanInputs.model_validate(value) for value in package_doc["prebound_service_socket_plans"]):
        observation = canonical_json_bytes({"role": plan.role, "host": plan.gateway, "port": plan.observed_port, "device": plan.socket_device, "inode": plan.socket_inode, "mode": plan.socket_mode, "uid": plan.socket_owner_uid, "ip_freebind": True})
        socket_leases.append(PreboundServiceSocketLeaseV1(schema_version="bb.rl.harness-prebound-service-socket-lease.v1", role=plan.role, socket_plan_digest=plan.canonical_digest(), socket_plan_id=plan.socket_plan_id, bridge_lease_id=bridge_lease["lease_id"], bridge_lease_digest=bridge_lease_digest, pre_create_observation_bytes_base64=base64.b64encode(observation).decode(), pre_create_observation_digest=sha256_ref(observation), post_create_observation_bytes_base64=base64.b64encode(observation).decode(), post_create_observation_digest=sha256_ref(observation), server_handoff_receipt="sha256:" + ("9" if plan.role == "fixed_policy" else "a") * 64).model_dump(mode="json"))
    attachment_bytes = canonical_json_bytes({"network_id": BRIDGE_ID, "network_name": bridge_plan_model.network_name, "containers": ["outer-container-1", "container-1", "verifier-container-1"]})
    delete_stdout = b"deleted"
    delete_stderr = b""
    post_list = b"[]"
    post_inspect_stdout = b"[]"
    post_inspect_stderr = b"network absent"
    cleanup_receipt = signed_receipt(
        OuterBridgeCleanupReceiptV1,
        schema_version="bb.rl.harness-outer-bridge-cleanup-receipt.v1",
        lease_id=bridge_lease["lease_id"],
        lease_digest=bridge_lease_digest,
        network_id=BRIDGE_ID,
        network_name=bridge_plan_model.network_name,
        delete_returncode=0,
        delete_stdout_base64=base64.b64encode(delete_stdout).decode(),
        delete_stderr_base64=base64.b64encode(delete_stderr).decode(),
        delete_result_sha256=sha256_ref(canonical_json_bytes({"returncode": 0, "stdout_base64": base64.b64encode(delete_stdout).decode(), "stderr_base64": base64.b64encode(delete_stderr).decode()})),
        post_list_bytes_base64=base64.b64encode(post_list).decode(),
        post_list_sha256=sha256_ref(post_list),
        post_inspect_stdout_base64=base64.b64encode(post_inspect_stdout).decode(),
        post_inspect_stderr_base64=base64.b64encode(post_inspect_stderr).decode(),
        post_inspect_sha256=sha256_ref(canonical_json_bytes({"stdout_base64": base64.b64encode(post_inspect_stdout).decode(), "stderr_base64": base64.b64encode(post_inspect_stderr).decode()})),
        id_absent=True,
        name_absent=True,
        broker_pid=200,
        broker_starttime="1000",
    ).model_dump(mode="json")
    bridge_authentication_verification = {
        "schema_version": "bb.rl.f2.bridge-authentication-verification.v1",
        "lease_id": bridge_lease["lease_id"],
        "lease_digest": bridge_lease_digest,
        "cleanup_digest": sha256_ref(canonical_json_bytes(cleanup_receipt)),
        "signer_key_id": bridge_lease["signer_key_id"],
        "lease_verified": bridge_lease_model.verify_authenticator(RECEIPT_AUTHENTICATOR),
        "cleanup_verified": OuterBridgeCleanupReceiptV1.model_validate(cleanup_receipt).verify_authenticator(RECEIPT_AUTHENTICATOR),
        "collector_ref": ref("private-daemon-collector"),
        "verified_at": "2026-07-11T00:00:04Z",
    }
    if bridge_auth_mutation is not None:
        bridge_authentication_verification[bridge_auth_mutation] = False
    raw_runner = {
        "target.stdout": stdout,

        "target.stderr": stderr,
        "runner/scheduler.json": canonical_json_bytes(scheduler),
        "runner/docker-identity.json": canonical_json_bytes({"schema_version": "bb.rl.f2.docker-identity.v1", "version": {"exit_code": 0}, "info": {"exit_code": 0}}),
        "runner/image-inspect.json": canonical_json_bytes({"schema_version": "bb.rl.f2.image-observation.v1", "requested_ref": WRAPPER_IMAGE_REF, "measured_image_id": "sha256:" + "a" * 64, "admission": "composition_private_daemon_offline_authority", "authority": {"binding": "composition-owned", "immutable_reference": WRAPPER_IMAGE_REF, "image_id": "sha256:" + "a" * 64, "composition_digest": package_doc["composition_digest"], "outer_bridge_plan": package_doc["outer_bridge_plan"]}}),
        "runner/container-inspect.json": canonical_json_bytes({"schema_version": "bb.rl.f2.container-observation.v1", "container_id": "container-1", "create_exit_code": 0, "inspect_exit_code": 0, "runtime_authority": {"name": "breadboard-runc"}, "outer_bridge_lease": bridge_lease, "prebound_service_socket_leases": socket_leases, "callback_tls_host": bridge_plan_model.gateway, "outer_wrapper": {"container_id": "outer-container-1", "network_id": BRIDGE_ID, "network_name": bridge_plan_model.network_name, "lease_id": bridge_lease["lease_id"]}, "attachment_inspect_bytes_base64": base64.b64encode(attachment_bytes).decode(), "attachment_inspect_sha256": sha256_ref(attachment_bytes)}),
        "runner/component-report.json": canonical_json_bytes(component),
        "runner/post-cleanup.json": canonical_json_bytes({"schema_version": "bb.rl.f2.cleanup-observation.v1", "remove": {"exit_code": 0}, "name_matches": [], "label_matches": [], "container_create_attempted": True, "outer_bridge_cleanup_receipt": cleanup_receipt, "bridge_authentication_verification": bridge_authentication_verification}),
    }
    raw_runner.update({
        "runner/callback-observation-journal.jsonl": callback_raw["callback_observation_journal"],
        "runner/callback-observation-snapshot.json": callback_raw["callback_observation_snapshot"],
        "runner/callback-verification-authority.json": callback_raw["callback_verification_authority"],
        "runner/callback-verification-public-key.pem": callback_raw["callback_verification_public_key"],
        "runner/callback-verification-receipt.json": callback_raw["callback_verification_receipt"],
        "runner/callback-verification-signature.json": callback_raw["callback_verification_signature"],
    })
    runner_archive = archive_bytes(raw_runner)
    (attempt / "runner-result.tar.gz").write_bytes(runner_archive)
    write_json(attempt / "attempt.json", {"schema_version": "bb.rl.f2.attempt.v1", "attempt_id": name, "target_run_id": target_run_id, "command_id": command_id, "payload_ref": payload_ref})
    (attempt / "target.stdout").write_bytes(stdout)
    (attempt / "target.stderr").write_bytes(stderr)
    (attempt / "exit_code").write_bytes(b"0\n")
    precheck_raw = b"ssh-config\nknown-host\nprobe\n"
    precheck = {"schema_version": "bb.rl.f2.target-precheck.v1", "target_record_ref": ref("target-record"), "ssh_config_ref": ref("ssh-config"), "known_hosts_match_ref": ref("known-hosts"), "probe_ref": ref("probe"), "raw_ref": sha256_ref(precheck_raw), "ssh_alias": "ZYPHRA_IBM_AMD_1", "hostname": "ibm-node-1", "f1_prerequisite_id": F1_PREREQUISITE_ID, "f1_prerequisite_ref": F1_PREREQUISITE_REF, "f1_prerequisite_root": F1_PREREQUISITE_ROOT, "passed": True}
    phase3 = {"schema_version": "bb.rl.f2.phase3-invocation.v1", "argv": ["python3", "scripts/rl_phase3/run_phase3_target_command.py", "--gres", "gpu:1"], "target_alias": "ZYPHRA_IBM_AMD_1", "partition": "gpu", "command_id": command_id, "target_run_id": target_run_id, "job_id": "123", "node": "ibm-node-1", "payload_ref": payload_ref, "target_precheck": precheck, "target_precheck_raw_b64": base64.b64encode(precheck_raw).decode()}
    write_json(outer / OUTER_ARTIFACTS["phase3_invocation"], phase3)
    phase3_log = b"authenticated IBM Phase3 target command observed\n"
    (outer / OUTER_ARTIFACTS["phase3_log"]).write_bytes(phase3_log)
    manifest = canonical_json_bytes({"schema_version": "bb.rl.f2.phase3-command-log-manifest.v1", "command_id": command_id})
    (outer / OUTER_ARTIFACTS["phase3_manifest"]).write_bytes(manifest)
    write_json(outer / OUTER_ARTIFACTS["transport"], {"schema_version": "bb.rl.f2.phase3-transport.v1", "raw_log_ref": sha256_ref(phase3_log), "manifest_ref": sha256_ref(manifest), "runner_archive_ref": sha256_ref(runner_archive), "precheck_raw_ref": sha256_ref(precheck_raw), "precheck_report_ref": sha256_ref(canonical_json_bytes(precheck)), "component_failed_count": 0})
    write_json(outer / OUTER_ARTIFACTS["result_archive"], {"schema_version": "bb.rl.f2.result-archive.v1", "attempt_id": name, "sha256": sha256_ref(result_archive), "size_bytes": len(result_archive)})
    return attempt


def test_raw_exporter_emits_exact_content_addressed_inventory() -> None:
    docs = valid_documents()
    records = {name: canonical_json_bytes(docs[name]) for name in TARGET_ARTIFACTS if name != "artifact_graph"}
    graph = docs["artifact_graph"]
    raw_objects = {item["ref"]: base64.b64decode(item["bytes_b64"]) for item in graph["objects"]}
    parents = {item["ref"]: tuple(item["parents"]) for item in graph["objects"]}
    producers = {item["ref"]: item["producer"] for item in graph["objects"]}
    exported = export_f2_artifacts_from_raw(records=records, raw_objects=raw_objects, parents=parents, roots=tuple(graph["roots"]), producers=producers)
    assert set(exported) == set(TARGET_ARTIFACTS.values())
    assert json.loads(exported[TARGET_ARTIFACTS["artifact_graph"]])["roots"] == graph["roots"]
    missing = next(ref_ for ref_ in raw_objects if ref_ != graph["roots"][0])
    with pytest.raises(F2ValidationError):
        export_f2_artifacts_from_raw(records=records, raw_objects={key: value for key, value in raw_objects.items() if key != missing}, parents=parents, roots=tuple(graph["roots"]), producers=producers)


@pytest.mark.parametrize(
    "mutation",
    ("gpu-empty", "gpu-zero", "gpu-multiple", "scontrol-missing"),
)
def test_rejects_nonexact_scheduler_gpu_evidence(
    tmp_path: Path,
    mutation: str,
) -> None:
    with pytest.raises(F2ValidationError, match="one-node/task/GPU"):
        validate_scratch(make_attempt(tmp_path, scheduler_mutation=mutation))


@pytest.mark.parametrize(
    "mutation",
    ("journal", "count", "signer", "signature", "public-key"),
)
def test_rejects_callback_receipt_packet_mutations(
    tmp_path: Path,
    mutation: str,
) -> None:
    with pytest.raises(F2ValidationError, match="callback"):
        validate_scratch(make_attempt(tmp_path, callback_mutation=mutation))


def test_validates_promotes_and_reverifies_raw_single_episode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    attempt = make_attempt(tmp_path)
    report = validate_scratch(attempt)
    assert report["schema_version"] == REPORT_SCHEMA
    assert report["status"] == "passed" and len(report["raw_artifacts"]) == len(TARGET_ARTIFACTS)
    destination = promote(attempt, tmp_path / "docs_tmp" / "ZYPHRA" / "RL_PHASE_5" / "evidence" / "target" / "F2")
    assert verify_canonical(destination) == report
    with pytest.raises(F2ValidationError, match="already exists"): promote(attempt, destination.parent)


@pytest.mark.parametrize("field", ("lease_verified", "cleanup_verified"))
def test_rejects_unverified_bridge_authenticator_receipts(
    tmp_path: Path,
    field: str,
) -> None:
    with pytest.raises(F2ValidationError, match="authenticator verification"):
        validate_scratch(make_attempt(tmp_path, bridge_auth_mutation=field))


@pytest.mark.parametrize("mutation", ("outer-job", "raw-log", "marker", "result-archive", "runner-archive", "local"))
def test_rejects_fabricated_outer_runner_and_local_evidence(tmp_path: Path, mutation: str) -> None:
    attempt = make_attempt(tmp_path)
    if mutation == "outer-job":
        path = attempt / "outer" / OUTER_ARTIFACTS["phase3_invocation"]
        value = json.loads(path.read_bytes()); value["job_id"] = "999"; write_json(path, value)
    elif mutation == "raw-log":
        (attempt / "outer" / OUTER_ARTIFACTS["phase3_log"]).write_bytes(b"locally fabricated\n")
    elif mutation == "marker":
        changed = (attempt / "target.stdout").read_bytes().replace(b"\"size\":", b"\"size\":999999,\"ignored\":")
        (attempt / "target.stdout").write_bytes(changed)
        (attempt / "runner" / RUNNER_ARTIFACTS["stdout"]).write_bytes(changed)
    elif mutation == "result-archive":
        (attempt / "result.tar.gz").write_bytes((attempt / "result.tar.gz").read_bytes() + b"x")
    elif mutation == "runner-archive":
        (attempt / "runner-result.tar.gz").write_bytes((attempt / "runner-result.tar.gz").read_bytes() + b"x")
    else:
        path = attempt / "outer" / OUTER_ARTIFACTS["transport"]
        value = json.loads(path.read_bytes()); value["local_validation"] = True; write_json(path, value)
    with pytest.raises(F2ValidationError):
        validate_scratch(attempt)


@pytest.mark.parametrize(
    ("document", "field", "value"),
    [
        ("selection", "config_ref", ref("wrong")), ("effective_plan", "policy_ref", ref("wrong")),
        ("policy", "tool_call_id", "wrong"), ("tool", "order", 2),
        ("sandbox", "runtime_class", "trusted_process"), ("verifier", "provenance", "test-verifier"),
        ("reward", "value", 2.0), ("completed", "cleanup", "released"),
        ("closed", "completed_ref", ref("wrong")), ("cleanup", "containers", ["orphan"]),
        ("verifier", "container_id", "container-1"),
        ("prerequisite", "canonical_id", "f1-20260711t203833z-ibm-preflight"),
    ],
)
def test_rejects_every_join_class(tmp_path: Path, document: str, field: str, value: object) -> None:
    docs = valid_documents(); docs[document][field] = value  # type: ignore[index]
    with pytest.raises(F2ValidationError): validate_scratch(make_attempt(tmp_path, docs))


def test_rejects_lineage_attempt_as_target_precheck_prerequisite(tmp_path: Path) -> None:
    attempt = make_attempt(tmp_path)
    path = attempt / "outer" / OUTER_ARTIFACTS["phase3_invocation"]
    value = json.loads(path.read_bytes())
    value["target_precheck"]["f1_prerequisite_id"] = "f1-20260711t203833z-ibm-preflight"
    write_json(path, value)
    with pytest.raises(F2ValidationError, match="target precheck canonical F1 prerequisite mismatch"):
        validate_scratch(attempt)


def test_rejects_internal_bridge_authority_mismatch(tmp_path: Path) -> None:
    docs = valid_documents()
    docs["terminal_package"]["outer_bridge_plan"]["gateway"] = "10.88.0.2"  # type: ignore[index]
    with pytest.raises(F2ValidationError):
        validate_scratch(make_attempt(tmp_path, docs))


@pytest.mark.parametrize("mutation", ("authority", "callback", "prior", "tls-loopback"))
def test_rejects_fixed_policy_callback_join_mutations(tmp_path: Path, mutation: str) -> None:
    docs = valid_documents()
    policy = docs["policy"]
    if mutation == "authority":
        policy["turns"][0]["response_payload"]["metadata"]["breadboard_fixed_policy"]["code_digest"] = ref("foreign-code")
    elif mutation == "callback":
        policy["callback_observations"][1]["response_digest"] = ref("foreign-response")
    elif mutation == "prior":
        policy["turns"][1]["response_payload"]["metadata"]["breadboard_fixed_policy"]["prior_request_digest"] = ref("foreign-request")
    else:
        policy["tls_route_observations"][0]["connected_peer_ip"] = "127.0.0.1"
    with pytest.raises(F2ValidationError):
        validate_scratch(make_attempt(tmp_path, docs))


@pytest.mark.parametrize(
    ("field", "bad_value"),
    (
        ("server_certificate_verified", False),
        ("hostname_verified", False),
        ("bearer_authenticated", False),
        ("mutual_tls", True),
    ),
)
def test_rejects_tls_authentication_semantic_mutations(
    tmp_path: Path,
    field: str,
    bad_value: bool,
) -> None:
    docs = valid_documents()
    docs["policy"]["tls_route_observations"][0][field] = bad_value  # type: ignore[index]
    with pytest.raises(F2ValidationError, match="TLS"):
        validate_scratch(make_attempt(tmp_path, docs))


@pytest.mark.parametrize(
    ("field", "bad_value"),
    (
        ("server_certificate_verification_required", False),
        ("hostname_verification_required", False),
        ("bearer_authentication_required", False),
        ("mutual_tls_required", True),
    ),
)
def test_rejects_tls_package_policy_mutations(
    tmp_path: Path,
    field: str,
    bad_value: bool,
) -> None:
    docs = valid_documents()
    docs["terminal_package"]["tls_callback_runtime_input"]["tls_policy"][field] = bad_value  # type: ignore[index]
    with pytest.raises(F2ValidationError, match="TLS"):
        validate_scratch(make_attempt(tmp_path, docs))


def test_pem_reformat_preserves_der_identity_semantics(tmp_path: Path) -> None:
    original = valid_documents("f2-real-original")
    original_tls = original["terminal_package"]["tls_callback_runtime_input"]
    original_trust = original["terminal_package"]["policy_tls_trust_authority"]
    reformatted = valid_documents("f2-real-pem", leaf_pem=b"leaf-cert\n")
    reformatted_tls = reformatted["terminal_package"]["tls_callback_runtime_input"]
    reformatted_trust = reformatted["terminal_package"]["policy_tls_trust_authority"]
    assert original_tls["leaf_certificate_sha256"] != reformatted_tls["leaf_certificate_sha256"]
    assert (
        original_trust["expected_leaf_certificate_sha256"]
        == reformatted_trust["expected_leaf_certificate_sha256"]
    )
    report = validate_scratch(
        make_attempt(tmp_path, reformatted, name="f2-real-pem")
    )
    assert report["status"] == "passed"


def test_rejects_pem_digest_masquerading_as_leaf_der(tmp_path: Path) -> None:
    docs = valid_documents()
    pem_digest = docs["terminal_package"]["tls_callback_runtime_input"]["leaf_certificate_sha256"]
    docs["policy"]["tls_route_observations"][0]["leaf_der_digest"] = pem_digest
    with pytest.raises(F2ValidationError, match="TLS"):
        validate_scratch(make_attempt(tmp_path, docs))


def test_rejects_renamed_canonical_and_leaves_no_partial_promotion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    attempt = make_attempt(tmp_path / "source")
    destination = promote(attempt, tmp_path / "docs_tmp" / "ZYPHRA" / "RL_PHASE_5" / "evidence" / "target" / "F2")
    renamed_parent = tmp_path / "canonical" / "renamed"
    renamed_parent.mkdir(parents=True)
    renamed = renamed_parent / destination.name
    destination.rename(renamed)
    with pytest.raises(F2ValidationError, match="renamed"):
        verify_canonical(renamed)
    second = make_attempt(tmp_path / "second", name="f2-real-two")
    def fail_replace(source: Path, target: Path) -> None:
        target.mkdir()
        (target / "concurrent-owner").write_bytes(b"preserve")
        raise F2ValidationError("canonical destination already exists")
    monkeypatch.setattr("breadboard.rl.phase5.f2_terminal._rename_noreplace", fail_replace)
    with pytest.raises(F2ValidationError, match="already exists"):
        promote(second, tmp_path / "docs_tmp" / "ZYPHRA" / "RL_PHASE_5" / "evidence" / "target" / "F2")
    second_root = tmp_path / "docs_tmp" / "ZYPHRA" / "RL_PHASE_5" / "evidence" / "target" / "F2"
    assert (second_root / "20260711T000000Z-slurm-123" / "concurrent-owner").read_bytes() == b"preserve"
    assert not (second_root / ".20260711T000000Z-slurm-123.staging").exists()
def test_bounded_gzip_secret_scan_rejects_high_ratio_before_expansion() -> None:
    import gzip
    bomb = gzip.compress(b"x" * (2 * 1024 * 1024), mtime=0)
    with pytest.raises(F2ValidationError, match="compressed evidence budget"):
        f2_terminal_module._scan_secret_bytes(bomb, ())
    secret = gzip.compress(b"authorization: bearer forbidden", mtime=0)
    with pytest.raises(F2ValidationError, match="secret-like"):
        f2_terminal_module._scan_secret_bytes(secret, ())




def test_rejects_fixture_f1_and_secret_evidence(tmp_path: Path) -> None:
    for mutate in ("fixture", "f1", "secret"):
        root = tmp_path / mutate; docs = valid_documents()
        if mutate == "fixture": docs["policy"]["provenance"] = "production-fixture-policy"  # type: ignore[index]
        elif mutate == "f1": docs["policy"]["foreign"] = "bb.rl.f1.artifact.v1"  # type: ignore[index]
        attempt = make_attempt(root, docs)
        if mutate == "secret": (attempt / "runner" / "stdout.bin").write_bytes(b"Authorization: Bearer raw-secret-value")
        with pytest.raises(F2ValidationError): validate_scratch(attempt)


def test_valid_policy_schema_remains_closed_and_accepted(tmp_path: Path) -> None:
    assert validate_scratch(make_attempt(tmp_path))["status"] == "passed"


def test_rejects_hyphenated_trusted_process_identity(tmp_path: Path) -> None:
    docs = valid_documents()
    docs["policy"]["provenance"] = "trusted-process"
    with pytest.raises(F2ValidationError, match="fixture or trusted-process"):
        validate_scratch(make_attempt(tmp_path, docs))


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("foreign_provenance", "trusted-process"),
        ("callback_metadata", {}),
    ),
)
def test_rejects_unknown_policy_top_level_fields(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    docs = valid_documents()
    docs["policy"][field] = value
    with pytest.raises(F2ValidationError, match="policy: keys mismatch"):
        validate_scratch(make_attempt(tmp_path, docs))


def test_seeded_secret_named_encodings_and_nearby_controls() -> None:
    seed = b"\xfb\xefseed-value\x00"
    standard = base64.b64encode(seed)
    urlsafe = base64.urlsafe_b64encode(seed)
    base32 = base64.b32encode(seed)
    encoded = {
        "standard-base64-padded": standard,
        "standard-base64-unpadded": standard.rstrip(b"="),
        "urlsafe-base64-padded": urlsafe,
        "urlsafe-base64-unpadded": urlsafe.rstrip(b"="),
        "hex-lower": seed.hex().encode(),
        "hex-upper": seed.hex().upper().encode(),
        "base32-upper-padded": base32,
        "base32-upper-unpadded": base32.rstrip(b"="),
        "base32-lower-padded": base32.lower(),
        "base32-lower-unpadded": base32.lower().rstrip(b"="),
    }
    for name, value in encoded.items():
        with pytest.raises(F2ValidationError, match="seeded secret"):
            f2_terminal_module._scan_secret_bytes(
                b"prefix:" + value + b":suffix",
                (seed,),
            )
        index = len(value) // 2
        replacement = b"A" if value[index:index + 1] != b"A" else b"B"
        nearby = value[:index] + replacement + value[index + 1:]
        f2_terminal_module._scan_secret_bytes(
            b"prefix:" + nearby + b":suffix",
            (seed,),
        )


def test_archive_marker_path_and_symlink_safety(tmp_path: Path) -> None:
    attempt_id = "f2-real-one"
    markers = []
    for name, filename in TARGET_ARTIFACTS.items():
        markers.append(MARKER_PREFIX.encode() + canonical_json_bytes({"schema_version": MARKER_SCHEMA, "attempt_id": attempt_id, "name": name, "path": "artifacts/" + filename, "sha256": ref(name), "size": 1}))
    assert len(parse_artifact_markers(b"\n".join(markers), attempt_id)) == len(TARGET_ARTIFACTS)
    with pytest.raises(F2ValidationError): parse_artifact_markers(b"\n".join(markers[:-1]), attempt_id)
    archive = tmp_path / "unsafe.tar"
    with tarfile.open(archive, "w") as stream:
        info = tarfile.TarInfo("../escape"); raw = b"x"; info.size = 1; stream.addfile(info, io.BytesIO(raw))
    with pytest.raises(F2ValidationError): safe_extract_archive(archive, tmp_path / "out")
    attempt = make_attempt(tmp_path / "link")
    victim = attempt / "artifacts" / TARGET_ARTIFACTS["source"]
    victim.unlink(); victim.symlink_to(attempt / "artifacts" / TARGET_ARTIFACTS["policy"])
    with pytest.raises(F2ValidationError): validate_scratch(attempt)


def test_seeded_secret_and_renamed_paths_are_rejected(tmp_path: Path) -> None:
    attempt = make_attempt(tmp_path / "seed")
    (attempt / "runner" / "stdout.bin").write_bytes(b"prefix " + base64.b64encode(b"seed-value"))
    with pytest.raises(F2ValidationError, match="seeded secret"): validate_scratch(attempt, secret_material=[b"seed-value"])
    renamed = make_attempt(tmp_path / "rename")
    (renamed / "artifacts" / TARGET_ARTIFACTS["tool"]).rename(renamed / "artifacts" / "renamed.json")
    with pytest.raises(F2ValidationError, match="inventory"): validate_scratch(renamed)

def test_runtime_materializer_is_closed_and_stock_docker_fails_precreate(tmp_path: Path) -> None:
    bridge_plan = OuterBridgePlanInputs(schema_version="bb.rl.harness-outer-bridge-plan.v1", network_name="bb-f2-bridge", driver="bridge", subnet="10.88.0.0/24", gateway="10.88.0.1", internal=True, labels=tuple(BRIDGE_LABELS), cleanup_owner="f2_outer_orchestrator", cleanup_ref=BRIDGE_CLEANUP_REF)
    socket_plans = tuple(prebound_socket_plan(role, port, inode) for role, port, inode in (("callback_tls", 18443, 103), ("fixed_policy", 18081, 101), ("harness", 18080, 102)))
    authority_docs = valid_documents()
    receipt_authority = EvidenceReceiptSigningAuthorityV1.model_validate(
        authority_docs["terminal_package"]["evidence_receipt_signing_authority"]
    )
    tls_trust = PolicyTlsTrustAuthorityV1.model_validate(
        authority_docs["terminal_package"]["policy_tls_trust_authority"]
    )
    operator = OperatorAuthorityInputs(WRAPPER_IMAGE_REF, image("runtime-task"), image("runtime-verifier"), ref("composition"), bridge_plan, socket_plans, tls_callback_input(socket_plans[0]), tls_trust, receipt_authority, ref("runtime"), ref("oci"), ref("security"), ref("network"), ref("policy"), ref("tool"), ref("verifier"), ref("reward"), ref("task"), ref("evidence"))
    values = [ref(str(index)) for index in range(12)]
    inputs = TerminalPackageInputs(*values, operator, FixedPolicyAuthorityRefs(ref("code"), ref("script"), ref("model-label"), ref("instance")))
    package = materialize_terminal_package(inputs, prompt="solve", model="fixed-real-model")
    decoded = json.loads(package.package_bytes)
    assert decoded["selector_kind"] == "direct" and decoded["overlay_refs"] == []
    assert decoded["runtime_class"] == "hardened_docker"
    assert decoded["outer_bridge_plan"] == bridge_plan.model_dump(mode="json")
    assert decoded["prebound_service_socket_plans"] == [plan.model_dump(mode="json") for plan in socket_plans]
    assert decoded["tls_callback_runtime_input"]["tls_policy"] == {
        "minimum_tls_version": "TLSv1.3",
        "maximum_tls_version": "TLSv1.3",
        "server_certificate_verification_required": True,
        "hostname_verification_required": True,
        "bearer_authentication_required": True,
        "mutual_tls_required": False,
    }
    assert b"BEGIN PRIVATE KEY" not in package.package_bytes
    assert "private_key_path" not in decoded["tls_callback_runtime_input"]
    with pytest.raises(ValueError, match="RFC1918"):
        OuterBridgePlanInputs(schema_version="bb.rl.harness-outer-bridge-plan.v1", network_name="name", driver="bridge", subnet="127.0.0.0/24", gateway="127.0.0.1", internal=True, labels=tuple(BRIDGE_LABELS), cleanup_owner="f2_outer_orchestrator", cleanup_ref=BRIDGE_CLEANUP_REF)
    assert [tool.tool_id for tool in package.run_request.tools].count("shell") == 1
    destination = write_terminal_package(package, tmp_path / "package")
    assert (destination / "operator-authority.json").is_file()
    with pytest.raises(F2RuntimeError, match="already exists"): write_terminal_package(package, destination)
    blocker = stock_docker_blocker(runtime_observation_ref=ref("stock"), runtime_executable_ref=ref("oci"))
    assert blocker == {"schema_version": "bb.rl.f2.runtime-blocker.v1", "passed": False, "code": "runtime_unsupported", "reason": "oci_runtime_exact_execution_unavailable", "stage": "pre_create", "runtime_observation_ref": ref("stock"), "runtime_executable_ref": ref("oci"), "lease_count": 0, "container_count": 0, "reward_count": 0, "promotion_allowed": False}
