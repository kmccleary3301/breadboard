from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


SCHEMA_VERSION = "bb.env_package.v1alpha"


def _text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _bool(value: Any) -> bool:
    return bool(value)


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return dict(value)


def _text_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    copied: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            copied.append(text)
    return copied


@dataclass(frozen=True)
class ProvenanceSpec:
    created_at: str
    license: str
    source_usage_policy: str
    contamination_scope: str
    source_refs: list[str] = field(default_factory=list)
    source_hashes: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "ProvenanceSpec":
        return ProvenanceSpec(
            created_at=_text(data.get("created_at"), "provenance.created_at"),
            license=_text(data.get("license"), "provenance.license"),
            source_usage_policy=_text(
                data.get("source_usage_policy"),
                "provenance.source_usage_policy",
            ),
            contamination_scope=_text(
                data.get("contamination_scope"),
                "provenance.contamination_scope",
            ),
            source_refs=_text_list(data.get("source_refs"), "provenance.source_refs"),
            source_hashes=_mapping(data.get("source_hashes"), "provenance.source_hashes"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "license": self.license,
            "source_usage_policy": self.source_usage_policy,
            "contamination_scope": self.contamination_scope,
            "source_refs": list(self.source_refs),
            "source_hashes": dict(self.source_hashes),
        }


@dataclass(frozen=True)
class TasksetSpec:
    taskset_id: str
    source_kind: str
    source_hash: str
    task_id_field: str
    prompt_fields: list[str]
    allowed_splits: list[str]
    source_uri: str | None = None

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "TasksetSpec":
        source_uri = data.get("source_uri")
        return TasksetSpec(
            taskset_id=_text(data.get("taskset_id"), "taskset.taskset_id"),
            source_kind=_text(data.get("source_kind"), "taskset.source_kind"),
            source_hash=_text(data.get("source_hash"), "taskset.source_hash"),
            task_id_field=_text(data.get("task_id_field"), "taskset.task_id_field"),
            prompt_fields=_text_list(data.get("prompt_fields"), "taskset.prompt_fields"),
            allowed_splits=_text_list(data.get("allowed_splits"), "taskset.allowed_splits"),
            source_uri=str(source_uri).strip() if source_uri else None,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "taskset_id": self.taskset_id,
            "source_kind": self.source_kind,
            "source_hash": self.source_hash,
            "task_id_field": self.task_id_field,
            "prompt_fields": list(self.prompt_fields),
            "allowed_splits": list(self.allowed_splits),
        }
        if self.source_uri:
            payload["source_uri"] = self.source_uri
        return payload


@dataclass(frozen=True)
class SplitSpec:
    split_id: str
    split_type: str
    taskset_id: str
    selector: dict[str, Any]
    split_hash: str
    immutable: bool = True
    optimizer_visible: bool = False
    trainer_visible: bool = False
    protected: bool = False

    @staticmethod
    def from_dict(split_id: str, data: Mapping[str, Any]) -> "SplitSpec":
        return SplitSpec(
            split_id=_text(data.get("split_id", split_id), "split.split_id"),
            split_type=_text(data.get("split_type"), "split.split_type"),
            taskset_id=_text(data.get("taskset_id"), "split.taskset_id"),
            selector=_mapping(data.get("selector"), "split.selector"),
            split_hash=_text(data.get("split_hash"), "split.split_hash"),
            immutable=_bool(data.get("immutable", True)),
            optimizer_visible=_bool(data.get("optimizer_visible", False)),
            trainer_visible=_bool(data.get("trainer_visible", False)),
            protected=_bool(data.get("protected", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "split_id": self.split_id,
            "split_type": self.split_type,
            "taskset_id": self.taskset_id,
            "selector": dict(self.selector),
            "split_hash": self.split_hash,
            "immutable": self.immutable,
            "optimizer_visible": self.optimizer_visible,
            "trainer_visible": self.trainer_visible,
            "protected": self.protected,
        }


@dataclass(frozen=True)
class HarnessContract:
    harness_id: str
    interaction_mode: str
    observation_schema: dict[str, Any]
    action_schema: dict[str, Any]
    termination: dict[str, Any]
    tools: list[str] = field(default_factory=list)
    max_turns: int | None = None
    hidden_state_policy: str = "never_visible"

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "HarnessContract":
        max_turns = data.get("max_turns")
        return HarnessContract(
            harness_id=_text(data.get("harness_id"), "harness.harness_id"),
            interaction_mode=_text(data.get("interaction_mode"), "harness.interaction_mode"),
            observation_schema=_mapping(data.get("observation_schema"), "harness.observation_schema"),
            action_schema=_mapping(data.get("action_schema"), "harness.action_schema"),
            termination=_mapping(data.get("termination"), "harness.termination"),
            tools=_text_list(data.get("tools"), "harness.tools"),
            max_turns=int(max_turns) if max_turns is not None else None,
            hidden_state_policy=str(data.get("hidden_state_policy") or "never_visible"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "harness_id": self.harness_id,
            "interaction_mode": self.interaction_mode,
            "observation_schema": dict(self.observation_schema),
            "action_schema": dict(self.action_schema),
            "termination": dict(self.termination),
            "tools": list(self.tools),
            "hidden_state_policy": self.hidden_state_policy,
        }
        if self.max_turns is not None:
            payload["max_turns"] = self.max_turns
        return payload


@dataclass(frozen=True)
class RuntimeEnvelope:
    backend: str
    isolation_level: str
    agent_user: str
    network: str
    secrets_policy: str
    pool_key_fields: list[str]
    image_digest: str | None = None
    network_allowlist_reason: str | None = None

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "RuntimeEnvelope":
        image_digest = data.get("image_digest")
        allowlist_reason = data.get("network_allowlist_reason")
        return RuntimeEnvelope(
            backend=_text(data.get("backend"), "runtime.backend"),
            isolation_level=_text(data.get("isolation_level"), "runtime.isolation_level"),
            agent_user=str(data.get("agent_user") or "sandbox").strip(),
            network=str(data.get("network") or "none").strip(),
            secrets_policy=str(data.get("secrets_policy") or "ambient_forbidden").strip(),
            pool_key_fields=_text_list(data.get("pool_key_fields"), "runtime.pool_key_fields"),
            image_digest=str(image_digest).strip() if image_digest else None,
            network_allowlist_reason=str(allowlist_reason).strip() if allowlist_reason else None,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "backend": self.backend,
            "isolation_level": self.isolation_level,
            "agent_user": self.agent_user,
            "network": self.network,
            "secrets_policy": self.secrets_policy,
            "pool_key_fields": list(self.pool_key_fields),
        }
        if self.image_digest:
            payload["image_digest"] = self.image_digest
        if self.network_allowlist_reason:
            payload["network_allowlist_reason"] = self.network_allowlist_reason
        return payload


@dataclass(frozen=True)
class VerifierSpec:
    verifier_id: str
    kind: str
    code_hash: str
    input_contract: dict[str, Any]
    output_contract: dict[str, Any]
    isolated_from_agent: bool = True
    rerun_policy: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "VerifierSpec":
        return VerifierSpec(
            verifier_id=_text(data.get("verifier_id"), "verifier.verifier_id"),
            kind=_text(data.get("kind"), "verifier.kind"),
            code_hash=_text(data.get("code_hash"), "verifier.code_hash"),
            input_contract=_mapping(data.get("input_contract"), "verifier.input_contract"),
            output_contract=_mapping(data.get("output_contract"), "verifier.output_contract"),
            isolated_from_agent=_bool(data.get("isolated_from_agent", True)),
            rerun_policy=_mapping(data.get("rerun_policy"), "verifier.rerun_policy"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "verifier_id": self.verifier_id,
            "kind": self.kind,
            "code_hash": self.code_hash,
            "input_contract": dict(self.input_contract),
            "output_contract": dict(self.output_contract),
            "isolated_from_agent": self.isolated_from_agent,
            "rerun_policy": dict(self.rerun_policy),
        }


@dataclass(frozen=True)
class RewardSpec:
    reward_id: str
    kind: str
    terminal_reward: bool = True

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "RewardSpec":
        return RewardSpec(
            reward_id=_text(data.get("reward_id"), "reward.reward_id"),
            kind=_text(data.get("kind"), "reward.kind"),
            terminal_reward=_bool(data.get("terminal_reward", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "reward_id": self.reward_id,
            "kind": self.kind,
            "terminal_reward": self.terminal_reward,
        }


@dataclass(frozen=True)
class RendererSpec:
    renderer_id: str
    tokenizer_hash: str
    chat_template_hash: str
    bridge_to_next_turn_required: bool = True
    mask_contract: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "RendererSpec":
        return RendererSpec(
            renderer_id=_text(data.get("renderer_id"), "renderer.renderer_id"),
            tokenizer_hash=_text(data.get("tokenizer_hash"), "renderer.tokenizer_hash"),
            chat_template_hash=_text(data.get("chat_template_hash"), "renderer.chat_template_hash"),
            bridge_to_next_turn_required=_bool(data.get("bridge_to_next_turn_required", True)),
            mask_contract=_mapping(data.get("mask_contract"), "renderer.mask_contract"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "renderer_id": self.renderer_id,
            "tokenizer_hash": self.tokenizer_hash,
            "chat_template_hash": self.chat_template_hash,
            "bridge_to_next_turn_required": self.bridge_to_next_turn_required,
            "mask_contract": dict(self.mask_contract),
        }


@dataclass(frozen=True)
class HardeningPolicy:
    policy_id: str
    threat_level: str
    agent_non_root_required: bool
    verifier_isolated_required: bool
    quarantine_on_findings: list[str] = field(default_factory=list)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "HardeningPolicy":
        return HardeningPolicy(
            policy_id=_text(data.get("policy_id"), "hardening.policy_id"),
            threat_level=_text(data.get("threat_level"), "hardening.threat_level"),
            agent_non_root_required=_bool(data.get("agent_non_root_required", True)),
            verifier_isolated_required=_bool(data.get("verifier_isolated_required", True)),
            quarantine_on_findings=_text_list(
                data.get("quarantine_on_findings"),
                "hardening.quarantine_on_findings",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "threat_level": self.threat_level,
            "agent_non_root_required": self.agent_non_root_required,
            "verifier_isolated_required": self.verifier_isolated_required,
            "quarantine_on_findings": list(self.quarantine_on_findings),
        }


@dataclass(frozen=True)
class ReplayConformanceSpec:
    replay_required: bool = True
    live_replay_parity_required: bool = True

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "ReplayConformanceSpec":
        return ReplayConformanceSpec(
            replay_required=_bool(data.get("replay_required", True)),
            live_replay_parity_required=_bool(data.get("live_replay_parity_required", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "replay_required": self.replay_required,
            "live_replay_parity_required": self.live_replay_parity_required,
        }


@dataclass(frozen=True)
class ExportEligibility:
    allowed_formats: list[str]
    trainable: bool
    requires_token_records: bool
    requires_replay_pass: bool
    support_level: str
    trainability_status: str
    trainability_blockers: list[str]
    support_evidence_refs: list[str] = field(default_factory=list)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "ExportEligibility":
        return ExportEligibility(
            allowed_formats=_text_list(data.get("allowed_formats"), "exports.allowed_formats"),
            trainable=_bool(data.get("trainable", False)),
            requires_token_records=_bool(data.get("requires_token_records", True)),
            requires_replay_pass=_bool(data.get("requires_replay_pass", True)),
            support_level=str(data.get("support_level") or "experimental").strip(),
            trainability_status=str(data.get("trainability_status") or "not_trainable").strip(),
            trainability_blockers=_text_list(
                data.get("trainability_blockers"),
                "exports.trainability_blockers",
            ),
            support_evidence_refs=_text_list(
                data.get("support_evidence_refs"),
                "exports.support_evidence_refs",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_formats": list(self.allowed_formats),
            "trainable": self.trainable,
            "requires_token_records": self.requires_token_records,
            "requires_replay_pass": self.requires_replay_pass,
            "support_level": self.support_level,
            "trainability_status": self.trainability_status,
            "trainability_blockers": list(self.trainability_blockers),
            "support_evidence_refs": list(self.support_evidence_refs),
        }


@dataclass(frozen=True)
class EnvPackage:
    package_id: str
    version: str
    provenance: ProvenanceSpec
    tasksets: list[TasksetSpec]
    splits: dict[str, SplitSpec]
    harness: HarnessContract
    runtime: RuntimeEnvelope
    verifier: VerifierSpec
    reward: RewardSpec
    renderer: RendererSpec
    hardening: HardeningPolicy | None
    replay: ReplayConformanceSpec
    exports: ExportEligibility
    schema_version: str = SCHEMA_VERSION
    package_hash: str | None = None

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "EnvPackage":
        from breadboard.rl.env_package.validate import validate_env_package_mapping

        errors = validate_env_package_mapping(data)
        if errors:
            raise ValueError("; ".join(errors))

        hardening_data = data.get("hardening")
        hardening = (
            HardeningPolicy.from_dict(hardening_data)
            if isinstance(hardening_data, Mapping)
            else None
        )
        return EnvPackage(
            schema_version=_text(data.get("schema_version"), "schema_version"),
            package_id=_text(data.get("package_id"), "package_id"),
            version=_text(data.get("version"), "version"),
            package_hash=str(data.get("package_hash")).strip()
            if data.get("package_hash")
            else None,
            provenance=ProvenanceSpec.from_dict(data["provenance"]),
            tasksets=[TasksetSpec.from_dict(item) for item in data["tasksets"]],
            splits={
                str(split_id): SplitSpec.from_dict(str(split_id), split_data)
                for split_id, split_data in data["splits"].items()
            },
            harness=HarnessContract.from_dict(data["harness"]),
            runtime=RuntimeEnvelope.from_dict(data["runtime"]),
            verifier=VerifierSpec.from_dict(data["verifier"]),
            reward=RewardSpec.from_dict(data["reward"]),
            renderer=RendererSpec.from_dict(data["renderer"]),
            hardening=hardening,
            replay=ReplayConformanceSpec.from_dict(data["replay"]),
            exports=ExportEligibility.from_dict(data["exports"]),
        )

    def to_dict(self, *, include_package_hash: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "package_id": self.package_id,
            "version": self.version,
            "provenance": self.provenance.to_dict(),
            "tasksets": [taskset.to_dict() for taskset in self.tasksets],
            "splits": {split_id: split.to_dict() for split_id, split in self.splits.items()},
            "harness": self.harness.to_dict(),
            "runtime": self.runtime.to_dict(),
            "verifier": self.verifier.to_dict(),
            "reward": self.reward.to_dict(),
            "renderer": self.renderer.to_dict(),
            "hardening": self.hardening.to_dict() if self.hardening else None,
            "replay": self.replay.to_dict(),
            "exports": self.exports.to_dict(),
        }
        if include_package_hash and self.package_hash:
            payload["package_hash"] = self.package_hash
        return payload
