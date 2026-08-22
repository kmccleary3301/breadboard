from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from breadboard.rl.harness.composition import (
    OuterBridgePlanV1,
    EvidenceReceiptSigningAuthorityV1,
    PreboundServiceSocketPlanV1,
    PolicyTlsTrustAuthorityV1,
    TlsCallbackRuntimeInputV1,
)

from breadboard.rl.harness.runners.terminal import (
    TERMINAL_ADAPTER_ID,
    TERMINAL_IMPLEMENTATION_DIGEST,
    TERMINAL_RUNTIME_ABI,
    TERMINAL_TOOL_DEFINITIONS,
    TerminalLoopLimits,
    TerminalRunRequest,
)

from .f2_terminal import (
    F1_PREREQUISITE_ID,
    F1_PREREQUISITE_REF,
    F1_PREREQUISITE_ROOT,
    canonical_json_bytes,
    sha256_ref,
)

TERMINAL_PACKAGE_SCHEMA = "bb.rl.f2.terminal-package.v1"
RUNTIME_BLOCKER_SCHEMA = "bb.rl.f2.runtime-blocker.v1"
FIXED_REAL_POLICY_PROVENANCE = "production-fixed-real-policy"
CANONICAL_EXECUTION_PATH = (
    "launch/generate_nemo.sh",
    "launch/eval_nemo.sh",
    "recipe.nemo_async.evals.run",
)
_SHA_REF = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE = re.compile(r"^[a-z0-9][a-z0-9./_-]*@sha256:[0-9a-f]{64}$")


class F2RuntimeError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def _ref(value: object, name: str) -> str:
    if type(value) is not str or not _SHA_REF.fullmatch(value):
        raise F2RuntimeError(f"{name} must be a canonical lowercase sha256 ref", code="authority_invalid")
    return value


OuterBridgePlanInputs = OuterBridgePlanV1
PreboundServiceSocketPlanInputs = PreboundServiceSocketPlanV1


@dataclass(frozen=True, slots=True)
class OperatorAuthorityInputs:
    image_ref: str
    task_image_ref: str
    verifier_image_ref: str
    composition_digest: str
    outer_bridge_plan: OuterBridgePlanV1
    prebound_service_socket_plans: tuple[PreboundServiceSocketPlanV1, ...]
    tls_callback_runtime_input: TlsCallbackRuntimeInputV1
    policy_tls_trust_authority: PolicyTlsTrustAuthorityV1
    evidence_receipt_signing_authority: EvidenceReceiptSigningAuthorityV1
    runtime_ref: str
    runtime_executable_ref: str
    security_ref: str
    network_ref: str
    policy_ref: str
    tool_ref: str
    verifier_ref: str
    reward_ref: str
    task_ref: str
    evidence_ref: str

    def __post_init__(self) -> None:
        images = (self.image_ref, self.task_image_ref, self.verifier_image_ref)
        if any(type(value) is not str or not _IMAGE.fullmatch(value) for value in images) or self.image_ref in {self.task_image_ref, self.verifier_image_ref}:
            raise F2RuntimeError("outer and primary/verifier immutable image roles must be separated", code="authority_invalid")
        _ref(self.composition_digest, "composition_digest")
        expected_roles = ["callback_tls", "fixed_policy", "harness"]
        if type(self.prebound_service_socket_plans) is not tuple or [plan.role for plan in self.prebound_service_socket_plans] != expected_roles or any(plan.gateway != self.outer_bridge_plan.gateway for plan in self.prebound_service_socket_plans):
            raise F2RuntimeError("exact sorted gateway prebound service socket plans required", code="authority_invalid")
        if type(self.tls_callback_runtime_input) is not TlsCallbackRuntimeInputV1:
            raise F2RuntimeError(
                "typed TLS callback runtime input required",
                code="authority_invalid",
            )
        if type(self.evidence_receipt_signing_authority) is not EvidenceReceiptSigningAuthorityV1:
            raise F2RuntimeError(
                "typed Ed25519 evidence receipt authority required",
                code="authority_invalid",
            )
        if type(self.policy_tls_trust_authority) is not PolicyTlsTrustAuthorityV1:
            raise F2RuntimeError(
                "typed policy TLS trust authority required",
                code="authority_invalid",
            )
        callback_plan = self.prebound_service_socket_plans[0]
        if (
            self.tls_callback_runtime_input.host != self.outer_bridge_plan.gateway
            or self.tls_callback_runtime_input.route_id != "f2-fixed-policy-callback"
            or self.tls_callback_runtime_input.socket_role != "callback_tls"
            or self.tls_callback_runtime_input.socket_plan_id != callback_plan.socket_plan_id
            or self.tls_callback_runtime_input.observed_port != callback_plan.observed_port
            or self.policy_tls_trust_authority.route_id
            != self.tls_callback_runtime_input.route_id
            or self.policy_tls_trust_authority.server_name
            != self.tls_callback_runtime_input.host
            or self.policy_tls_trust_authority.ca_bundle_ref.sha256
            != self.tls_callback_runtime_input.ca_certificate_sha256
        ):
            raise F2RuntimeError(
                "TLS callback must bind the exact callback gateway socket authority",
                code="authority_invalid",
            )
        for field in (
            "runtime_ref", "runtime_executable_ref", "security_ref", "network_ref",
            "policy_ref", "tool_ref", "verifier_ref", "reward_ref", "task_ref", "evidence_ref",
        ):
            _ref(getattr(self, field), field)

    def canonical(self) -> dict[str, object]:
        return {
            name: (
                self.outer_bridge_plan.model_dump(mode="json")
                if name == "outer_bridge_plan"
                else [plan.model_dump(mode="json") for plan in self.prebound_service_socket_plans]
                if name == "prebound_service_socket_plans"
                else self.tls_callback_runtime_input.model_dump(mode="json")
                if name == "tls_callback_runtime_input"
                else self.policy_tls_trust_authority.model_dump(mode="json")
                if name == "policy_tls_trust_authority"
                else self.evidence_receipt_signing_authority.model_dump(mode="json")
                if name == "evidence_receipt_signing_authority"
                else getattr(self, name)
            )
            for name in self.__dataclass_fields__
        }


@dataclass(frozen=True, slots=True)
class FixedPolicyAuthorityRefs:
    code_digest: str
    script_digest: str
    model_label_digest: str
    instance_digest: str

    def __post_init__(self) -> None:
        for field in self.__dataclass_fields__:
            _ref(getattr(self, field), field)

    def canonical(self) -> dict[str, str]:
        return {field: getattr(self, field) for field in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class TerminalPackageInputs:
    config_ref: str
    config_bundle_ref: str
    dependency_closure_ref: str
    compiled_config_ref: str
    admission_receipt_ref: str
    direct_selector_ref: str
    selection_ref: str
    effective_plan_ref: str
    model_ref: str
    checkpoint_ref: str
    tokenizer_ref: str
    bridge_ref: str
    operator: OperatorAuthorityInputs
    policy_authority: FixedPolicyAuthorityRefs

    def __post_init__(self) -> None:
        for field in self.__dataclass_fields__:
            if field not in {"operator", "policy_authority"}:
                _ref(getattr(self, field), field)
        if type(self.operator) is not OperatorAuthorityInputs:
            raise F2RuntimeError("operator authority must be explicit", code="authority_invalid")
        if type(self.policy_authority) is not FixedPolicyAuthorityRefs:
            raise F2RuntimeError("fixed policy authority refs must be explicit", code="authority_invalid")


@dataclass(frozen=True, slots=True)
class MaterializedTerminalPackage:
    package_ref: str
    package_bytes: bytes
    members: Mapping[str, bytes]
    run_request: TerminalRunRequest


def _member(schema: str, ref: str, dependencies: Mapping[str, str]) -> bytes:
    return canonical_json_bytes({"schema_version": schema, "ref": ref, "dependencies": dict(dependencies)})


def materialize_terminal_package(inputs: TerminalPackageInputs, *, prompt: str, model: str) -> MaterializedTerminalPackage:
    """Materialize a closed F2 input package without consulting env/config for authority.

    Config and task documents are only content identities. Runtime, policy, tool,
    verifier, reward, and evidence capabilities come exclusively from the typed
    operator authority supplied by the caller.
    """
    if type(inputs) is not TerminalPackageInputs:
        raise F2RuntimeError("typed terminal package inputs required", code="authority_invalid")
    if type(prompt) is not str or not prompt or type(model) is not str or not model:
        raise F2RuntimeError("non-empty prompt and model required", code="request_invalid")
    authority = inputs.operator
    members = {
        "config-bundle.json": _member("bb.rl.f2.config-bundle-ref.v1", inputs.config_bundle_ref, {}),
        "dependency-closure.json": _member("bb.rl.f2.dependency-closure-ref.v1", inputs.dependency_closure_ref, {"config_bundle_ref": inputs.config_bundle_ref}),
        "compiled-config.json": _member("bb.rl.f2.compiled-config-ref.v1", inputs.compiled_config_ref, {"dependency_closure_ref": inputs.dependency_closure_ref}),
        "admission-receipt.json": _member("bb.rl.f2.admission-receipt-ref.v1", inputs.admission_receipt_ref, {"compiled_config_ref": inputs.compiled_config_ref}),
        "direct-selector.json": _member("bb.rl.f2.direct-selector-ref.v1", inputs.direct_selector_ref, {"admission_receipt_ref": inputs.admission_receipt_ref}),
        "selection.json": _member("bb.rl.f2.selection-ref.v1", inputs.selection_ref, {"direct_selector_ref": inputs.direct_selector_ref}),
        "effective-plan.json": _member("bb.rl.f2.effective-plan-ref.v1", inputs.effective_plan_ref, {"selection_ref": inputs.selection_ref, "config_ref": inputs.config_ref}),
        "operator-authority.json": canonical_json_bytes({"schema_version": "bb.rl.f2.operator-authority.v1", **authority.canonical()}),
    }
    authority_ref = sha256_ref(members["operator-authority.json"])
    package = {
        "schema_version": TERMINAL_PACKAGE_SCHEMA,
        "selector_kind": "direct",
        "overlay_refs": [],
        "adapter_id": TERMINAL_ADAPTER_ID,
        "task_image_ref": authority.task_image_ref,
        "verifier_image_ref": authority.verifier_image_ref,
        "evidence_receipt_signing_authority": authority.evidence_receipt_signing_authority.model_dump(mode="json"),
        "policy_tls_trust_authority": authority.policy_tls_trust_authority.model_dump(mode="json"),
        "runtime_abi": TERMINAL_RUNTIME_ABI,
        "implementation_digest": TERMINAL_IMPLEMENTATION_DIGEST,
        "runtime_class": "hardened_docker",
        "image_ref": authority.image_ref,
        "composition_digest": authority.composition_digest,
        "outer_bridge_plan": authority.outer_bridge_plan.model_dump(mode="json"),
        "prebound_service_socket_plans": [plan.model_dump(mode="json") for plan in authority.prebound_service_socket_plans],
        "tls_callback_runtime_input": authority.tls_callback_runtime_input.model_dump(mode="json"),
        "f1_prerequisite": {
            "schema_version": "bb.rl.f2.f1-prerequisite.v1",
            "canonical_id": F1_PREREQUISITE_ID,
            "report_schema": "bb.rl.f1.ibm-exact-container-preflight-report.v3",
            "report_ref": F1_PREREQUISITE_REF,
            "canonical_root": F1_PREREQUISITE_ROOT,
        },
        "runtime_ref": authority.runtime_ref,
        "runtime_executable_ref": authority.runtime_executable_ref,
        "security_ref": authority.security_ref,
        "network_ref": authority.network_ref,
        "config_ref": inputs.config_ref,
        "policy_ref": authority.policy_ref,
        "policy_provenance": FIXED_REAL_POLICY_PROVENANCE,
        "policy_authority": inputs.policy_authority.canonical(),
        "tool_ref": authority.tool_ref,
        "verifier_ref": authority.verifier_ref,
        "reward_ref": authority.reward_ref,
        "task_ref": authority.task_ref,
        "evidence_ref": authority.evidence_ref,
        "operator_authority_ref": authority_ref,
        "execution_path": list(CANONICAL_EXECUTION_PATH),
        "config_chain": {
            "bundle": inputs.config_bundle_ref,
            "closure": inputs.dependency_closure_ref,
            "compiled": inputs.compiled_config_ref,
            "admission": inputs.admission_receipt_ref,
            "selector": inputs.direct_selector_ref,
            "selection": inputs.selection_ref,
            "effective_plan": inputs.effective_plan_ref,
        },
        "model_chain": {"model": inputs.model_ref, "checkpoint": inputs.checkpoint_ref, "tokenizer": inputs.tokenizer_ref, "bridge": inputs.bridge_ref},
    }
    provisional = canonical_json_bytes(package)
    package["package_ref"] = sha256_ref(provisional)
    package_bytes = canonical_json_bytes(package)
    members = {**members, "terminal-package.json": package_bytes}
    request = TerminalRunRequest(
        responses_create_params={"model": model, "input": prompt},
        tools=TERMINAL_TOOL_DEFINITIONS,
        limits=TerminalLoopLimits(max_turns=2, action_timeout_seconds=60, max_observation_chars=100_000),
    )
    return MaterializedTerminalPackage(package["package_ref"], package_bytes, members, request)


def write_terminal_package(package: MaterializedTerminalPackage, destination: Path) -> Path:
    destination = Path(destination)
    if destination.exists():
        raise F2RuntimeError("package destination already exists", code="destination_exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".f2-package-", dir=destination.parent))
    try:
        for name, raw in package.members.items():
            path = staging / name
            path.write_bytes(raw)
            with path.open("rb") as stream: os.fsync(stream.fileno())
        os.replace(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination


def stock_docker_blocker(*, runtime_observation_ref: str, runtime_executable_ref: str) -> dict[str, Any]:
    """Return the truthful pre-create disposition for an unattested stock daemon."""
    _ref(runtime_observation_ref, "runtime_observation_ref")
    _ref(runtime_executable_ref, "runtime_executable_ref")
    return {
        "schema_version": RUNTIME_BLOCKER_SCHEMA,
        "passed": False,
        "code": "runtime_unsupported",
        "reason": "oci_runtime_exact_execution_unavailable",
        "stage": "pre_create",
        "runtime_observation_ref": runtime_observation_ref,
        "runtime_executable_ref": runtime_executable_ref,
        "lease_count": 0,
        "container_count": 0,
        "reward_count": 0,
        "promotion_allowed": False,
    }
