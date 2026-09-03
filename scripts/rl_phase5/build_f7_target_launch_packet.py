from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Literal

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard_engine.compilation.contracts import canonical_json_bytes
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from breadboard.rl.phase5.f4_campaign import VARIANT_IDS
from scripts.rl_phase5.run_f4_target_canaries import F4TargetCanaryInput
from scripts.rl_phase5.run_f7_topology_gate import (
    F7ImmutableJSONRef,
    F7PinnedIdentity,
    MIN_CONFIG_ATTEMPTS,
    MIN_SOAK_ATTEMPTS,
    MIN_SOAK_SECONDS,
    MIN_SWE_ATTEMPTS,
    REQUIRED_LOAD_LEVELS,
    SOAK_MEASURED_SECONDS,
    SOAK_SAMPLE_INTERVAL_SECONDS,
    SOAK_WARMUP_SECONDS,
)

_SOURCE_ROLES: dict[str, str] = {
    "breadboard_engine/compilation/contracts.py": "canonical_json_contract",
    "scripts/rl_phase5/run_f4_target_canaries.py": "f4_lifecycle_authority",
    "scripts/rl_phase5/build_f7_target_launch_packet.py": "f7_packet_contract",
    "scripts/rl_phase5/run_f7_target_workload.py": "f7_target_workload",
    "scripts/rl_phase5/run_f7_topology_gate.py": "f7_gate_contract",
}


class F7LaunchPacketError(RuntimeError):
    pass


class _ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _digest(value: str) -> str:
    if (
        type(value) is not str
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError("lowercase sha256 digest required")
    return value


def _absolute(value: str) -> str:
    if type(value) is not str or not value.startswith("/") or os.path.normpath(value) != value:
        raise ValueError("absolute normalized path required")
    return value


def _identifier(value: str) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 256
        or value.strip() != value
        or any(not (character.isalnum() or character in "._:-") for character in value)
    ):
        raise ValueError("bounded identifier required")
    return value


class F7ImmutableFileRef(_ExactModel):
    path: str
    digest: str
    media_type: str = Field(min_length=1, max_length=128)

    _path = field_validator("path")(_absolute)
    _digest = field_validator("digest")(_digest)


class F7AuthorityRef(_ExactModel):
    reference: str = Field(min_length=1, max_length=4096)
    digest: str

    _digest = field_validator("digest")(_digest)

    @model_validator(mode="after")
    def digest_bound_reference(self) -> "F7AuthorityRef":
        if not self.reference.endswith("@" + self.digest):
            raise ValueError("authority reference is not bound to its digest")
        return self


class F7ConfigAuthority(_ExactModel):
    config_id: Literal[
        "codex-like",
        "claude-like",
        "pi-like",
        "opencode",
        "oh-my-opencode",
        "unknown-name",
    ]
    config_bundle_ref: F7AuthorityRef
    declared_row_timeout_ms: float = Field(gt=0, allow_inf_nan=False)


class F7AuthorityClosure(_ExactModel):
    runtime: F7AuthorityRef
    configs: tuple[
        F7ConfigAuthority,
        F7ConfigAuthority,
        F7ConfigAuthority,
        F7ConfigAuthority,
        F7ConfigAuthority,
        F7ConfigAuthority,
    ]
    task: F7AuthorityRef
    model: F7AuthorityRef
    tokenizer: F7AuthorityRef
    checkpoint: F7AuthorityRef
    image: F7AuthorityRef
    verifier: F7AuthorityRef
    authority: F7AuthorityRef

    @model_validator(mode="after")
    def frozen_configs(self) -> "F7AuthorityClosure":
        if tuple(row.config_id for row in self.configs) != VARIANT_IDS:
            raise ValueError("F7 requires the ordered current F4 six-config authority")
        if len({row.config_bundle_ref.digest for row in self.configs}) != len(VARIANT_IDS):
            raise ValueError("F4 config authority contains a duplicate bundle")
        return self

    def pinned_identity(self) -> F7PinnedIdentity:
        config_digest = _sha256(
            canonical_json_bytes(
                [
                    {
                        "config_id": row.config_id,
                        "digest": row.config_bundle_ref.digest,
                    }
                    for row in self.configs
                ]
            )
        )
        return F7PinnedIdentity(
            runtime_digest=self.runtime.digest,
            config_digest=config_digest,
            task_digest=self.task.digest,
            model_digest=self.model.digest,
            tokenizer_digest=self.tokenizer.digest,
            checkpoint_digest=self.checkpoint.digest,
            image_digest=self.image.digest,
            verifier_digest=self.verifier.digest,
            authority_digest=self.authority.digest,
        )


class F7BaselineObservation(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f7-baseline-observation.v1"]
    identity: F7PinnedIdentity
    elapsed_seconds: float = Field(gt=0, allow_inf_nan=False)
    completed_episodes: int = Field(ge=1)
    control_plane_p95_ms: float = Field(gt=0, allow_inf_nan=False)
    throughput_eps: float = Field(gt=0, allow_inf_nan=False)
    episode_ids: tuple[str, ...] = Field(min_length=1)
    evidence_digests: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def real_joined_baseline(self) -> "F7BaselineObservation":
        if self.completed_episodes != len(self.episode_ids):
            raise ValueError("baseline completion count lacks exact episode joins")
        if len(self.evidence_digests) != len(self.episode_ids):
            raise ValueError("baseline evidence count lacks exact episode joins")
        if len(set(self.episode_ids)) != len(self.episode_ids):
            raise ValueError("baseline contains a duplicate episode")
        for episode_id in self.episode_ids:
            _identifier(episode_id)
        for digest in self.evidence_digests:
            _digest(digest)
        return self


class F7TopologyAuthoring(_ExactModel):
    topology_id: Literal["two-node", "four-node"]
    node_count: Literal[2, 4]
    requested_target_run_id: str
    command_id: str
    job_name: str

    _ids = field_validator("requested_target_run_id", "command_id", "job_name")(_identifier)

    @model_validator(mode="after")
    def exact_topology(self) -> "F7TopologyAuthoring":
        expected = "two-node" if self.node_count == 2 else "four-node"
        if self.topology_id != expected:
            raise ValueError("topology name and node count disagree")
        if not self.requested_target_run_id.endswith("-pending"):
            raise ValueError("requested target run identity must end in -pending")
        return self


class F7TargetLaunchAuthoringInput(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f7-target-launch-authoring-input.v1"]
    packet_id: str
    gate_id: str
    f4_target_input: F7ImmutableFileRef
    tokenizer_ref: F7AuthorityRef
    baseline_observation: F7ImmutableJSONRef
    scontrol: F7ImmutableFileRef
    config_timeout_ms: dict[str, float]
    two_node: F7TopologyAuthoring
    four_node: F7TopologyAuthoring

    _ids = field_validator("packet_id", "gate_id")(_identifier)

    @model_validator(mode="after")
    def exact_campaign(self) -> "F7TargetLaunchAuthoringInput":
        if (self.two_node.topology_id, self.two_node.node_count) != ("two-node", 2):
            raise ValueError("two-node authoring row is not exact")
        if (self.four_node.topology_id, self.four_node.node_count) != ("four-node", 4):
            raise ValueError("four-node authoring row is not exact")
        if set(self.config_timeout_ms) != set(VARIANT_IDS):
            raise ValueError("one declared timeout is required for each frozen F4 config")
        if any(
            type(value) not in (int, float) or not value > 0
            for value in self.config_timeout_ms.values()
        ):
            raise ValueError("config row timeouts must be positive")
        return self


class F7SourceEntry(_ExactModel):
    relative_path: str
    digest: str
    size_bytes: int = Field(ge=1)
    mode: int = Field(ge=0, le=0o777)
    role: Literal[
        "canonical_json_contract",
        "f4_lifecycle_authority",
        "f7_packet_contract",
        "f7_target_workload",
        "f7_gate_contract",
    ]

    _digest = field_validator("digest")(_digest)


class F7FrozenWorkload(_ExactModel):
    load_levels: tuple[Literal[1], Literal[2], Literal[4], Literal[8], Literal[16], Literal[32]]
    soak_total_seconds: Literal[7200]
    soak_warmup_seconds: Literal[900]
    soak_measured_seconds: Literal[6300]
    sample_interval_seconds: Literal[15]
    minimum_terminal_attempts: Literal[256]
    minimum_attempts_per_config: Literal[32]
    minimum_r_swe_attempts: Literal[64]


class F7TargetWorkloadPayload(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f7-target-workload-payload.v1"]
    packet_id: str
    gate_id: str
    topology: F7TopologyAuthoring
    predecessor: Literal["none", "two-node:passed"]
    predecessor_receipt_relative_path: str | None
    f4_target_input: F7ImmutableFileRef
    baseline_observation: F7ImmutableJSONRef
    scontrol: F7ImmutableFileRef
    authority: F7AuthorityClosure
    expected_identity: F7PinnedIdentity
    source_entries: tuple[F7SourceEntry, ...]
    gate_source_digest: str
    workload: F7FrozenWorkload
    driver_rank: Literal[0]
    tasks_per_node: Literal[1]
    permanent_non_authority: Literal[True]
    promotion_authority: Literal[False]
    scorecard_update_allowed: Literal[False]

    _ids = field_validator("packet_id", "gate_id")(_identifier)
    _gate_digest = field_validator("gate_source_digest")(_digest)

    @model_validator(mode="after")
    def exact_payload(self) -> "F7TargetWorkloadPayload":
        if self.expected_identity != self.authority.pinned_identity():
            raise ValueError("payload pinned identity is not derived from exact authority refs")
        expected_predecessor = "none" if self.topology.node_count == 2 else "two-node:passed"
        if self.predecessor != expected_predecessor:
            raise ValueError("topology predecessor is not ordered")
        if self.topology.node_count == 2 and self.predecessor_receipt_relative_path is not None:
            raise ValueError("two-node payload cannot claim a predecessor")
        if self.topology.node_count == 4 and self.predecessor_receipt_relative_path != "two-node/topology-complete.json":
            raise ValueError("four-node payload must consume the exact two-node receipt")
        if tuple(entry.relative_path for entry in self.source_entries) != tuple(sorted(_SOURCE_ROLES)):
            raise ValueError("F7 source closure is incomplete or reordered")
        gate_entries = tuple(
            entry
            for entry in self.source_entries
            if entry.role == "f7_gate_contract"
        )
        if (
            len(gate_entries) != 1
            or gate_entries[0].digest != self.gate_source_digest
        ):
            raise ValueError("F7 gate source digest is not pinned to the closure")
        return self


class F7FinalizerTemplate(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f7-finalizer-template.v1"]
    packet_id: str
    gate_id: str
    expected_identity: F7PinnedIdentity
    gate_source_digest: str
    topology_order: tuple[Literal["two-node"], Literal["four-node"]]
    completion_receipts: tuple[str, str]
    phase3_manifest_placeholders: tuple[str, str]
    promotion_authority: Literal[False]
    scorecard_update_allowed: Literal[False]
    _gate_digest = field_validator("gate_source_digest")(_digest)


class F7TargetLaunchPacket(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f7-target-launch-packet.v1"]
    packet_id: str
    packet_root: str
    authority: F7AuthorityClosure
    expected_identity: F7PinnedIdentity
    two_node_payload: F7ImmutableJSONRef
    four_node_payload: F7ImmutableJSONRef
    finalizer_template: F7ImmutableJSONRef
    payload_zip: F7ImmutableFileRef
    source_entries: tuple[F7SourceEntry, ...]
    gate_source_digest: str
    topology_order: tuple[Literal["two-node"], Literal["four-node"]]
    permanent_non_authority: Literal[True]
    promotion_authority: Literal[False]
    scorecard_update_allowed: Literal[False]

    _packet_root = field_validator("packet_root")(_absolute)
    _gate_digest = field_validator("gate_source_digest")(_digest)


def _read_exact(ref: F7ImmutableFileRef) -> bytes:
    source = Path(ref.path).resolve(strict=True)
    raw = source.read_bytes()
    if _sha256(raw) != ref.digest:
        raise F7LaunchPacketError(f"immutable input digest mismatch: {source}")
    return raw


def _read_canonical_ref(ref: F7ImmutableJSONRef, model: type[BaseModel]) -> BaseModel:
    raw = Path(ref.path).resolve(strict=True).read_bytes()
    if _sha256(raw) != ref.digest:
        raise F7LaunchPacketError(f"immutable JSON input digest mismatch: {ref.path}")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise F7LaunchPacketError(f"immutable JSON input is invalid: {ref.path}") from exc
    if canonical_json_bytes(value) != raw:
        raise F7LaunchPacketError(f"immutable JSON input is not canonical: {ref.path}")
    return model.model_validate_json(raw, strict=True)


def _authority_from_f4(
    target: F4TargetCanaryInput,
    tokenizer: F7AuthorityRef,
    timeout_ms: dict[str, float],
) -> F7AuthorityClosure:
    def ref(value: Any) -> F7AuthorityRef:
        return F7AuthorityRef(reference=value.reference, digest=value.digest)

    invariant = target.invariant_identity
    return F7AuthorityClosure(
        runtime=ref(target.execution_authority.source_runtime_ref),
        configs=tuple(
            F7ConfigAuthority(
                config_id=variant.variant_id,
                config_bundle_ref=ref(variant.config_bundle_ref),
                declared_row_timeout_ms=float(timeout_ms[variant.variant_id]),
            )
            for variant in target.variants
        ),
        task=F7AuthorityRef(
            reference=f"breadboard://phase5-f7/task-contract@{invariant.task_contract_digest}",
            digest=invariant.task_contract_digest,
        ),
        model=ref(invariant.model_ref),
        tokenizer=tokenizer,
        checkpoint=ref(invariant.checkpoint_ref),
        image=ref(invariant.task_image_ref),
        verifier=ref(invariant.verifier_ref),
        authority=ref(target.production.authority_bundle_ref),
    )


def _source_entries() -> tuple[F7SourceEntry, ...]:
    root = Path(__file__).resolve().parents[2]
    entries = []
    for relative in sorted(_SOURCE_ROLES):
        raw = (root / relative).read_bytes()
        entries.append(
            F7SourceEntry(
                relative_path=relative,
                digest=_sha256(raw),
                size_bytes=len(raw),
                mode=stat.S_IMODE((root / relative).stat().st_mode),
                role=_SOURCE_ROLES[relative],
            )
        )
    return tuple(entries)


def _write_exclusive(path: Path, value: BaseModel) -> F7ImmutableJSONRef:
    raw = canonical_json_bytes(value.model_dump(mode="json"))
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o440,
    )
    try:
        if os.write(descriptor, raw) != len(raw):
            raise F7LaunchPacketError(f"short write: {path}")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return F7ImmutableJSONRef(path=str(path.resolve()), digest=_sha256(raw))


def _deterministic_zip(source_root: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_STORED) as archive:
        for source in sorted(path for path in source_root.rglob("*") if path.is_file()):
            relative = source.relative_to(source_root).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o440 << 16
            archive.writestr(info, source.read_bytes())


def build_f7_target_launch_packet(
    spec: F7TargetLaunchAuthoringInput, packet_root: str
) -> F7TargetLaunchPacket:
    if type(spec) is not F7TargetLaunchAuthoringInput:
        raise TypeError("exact F7TargetLaunchAuthoringInput required")
    destination = Path(_absolute(packet_root))
    if os.path.lexists(destination):
        raise F7LaunchPacketError("launch packet destination already exists")
    f4_raw = _read_exact(spec.f4_target_input)
    try:
        if canonical_json_bytes(json.loads(f4_raw)) != f4_raw:
            raise F7LaunchPacketError("F4 target input is not canonical JSON")
        f4 = F4TargetCanaryInput.model_validate_json(f4_raw, strict=True)
    except F7LaunchPacketError:
        raise
    except Exception as exc:
        raise F7LaunchPacketError("F4 target input schema is invalid") from exc
    _read_exact(spec.scontrol)
    authority = _authority_from_f4(f4, spec.tokenizer_ref, spec.config_timeout_ms)
    identity = authority.pinned_identity()
    baseline = _read_canonical_ref(spec.baseline_observation, F7BaselineObservation)
    if baseline.identity != identity:
        raise F7LaunchPacketError("baseline observation identity drift")
    sources = _source_entries()
    workload = F7FrozenWorkload(
        load_levels=REQUIRED_LOAD_LEVELS,
        soak_total_seconds=MIN_SOAK_SECONDS,
        soak_warmup_seconds=SOAK_WARMUP_SECONDS,
        soak_measured_seconds=SOAK_MEASURED_SECONDS,
        sample_interval_seconds=SOAK_SAMPLE_INTERVAL_SECONDS,
        minimum_terminal_attempts=MIN_SOAK_ATTEMPTS,
        minimum_attempts_per_config=MIN_CONFIG_ATTEMPTS,
        minimum_r_swe_attempts=MIN_SWE_ATTEMPTS,
    )
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        payload_dir = temporary / "payload"
        payload_dir.mkdir()
        two = F7TargetWorkloadPayload(
            schema_version="bb.rl.phase5-f7-target-workload-payload.v1",
            packet_id=spec.packet_id,
            gate_id=spec.gate_id,
            topology=spec.two_node,
            predecessor="none",
            predecessor_receipt_relative_path=None,
            f4_target_input=spec.f4_target_input,
            baseline_observation=spec.baseline_observation,
            scontrol=spec.scontrol,
            authority=authority,
            expected_identity=identity,
            source_entries=sources,
            gate_source_digest=next(
                entry.digest
                for entry in sources
                if entry.role == "f7_gate_contract"
            ),
            workload=workload,
            driver_rank=0,
            tasks_per_node=1,
            permanent_non_authority=True,
            promotion_authority=False,
            scorecard_update_allowed=False,
        )
        four = F7TargetWorkloadPayload(
            **{
                **two.model_dump(mode="python"),
                "topology": spec.four_node,
                "predecessor": "two-node:passed",
                "predecessor_receipt_relative_path": "two-node/topology-complete.json",
            }
        )
        finalizer = F7FinalizerTemplate(
            schema_version="bb.rl.phase5-f7-finalizer-template.v1",
            packet_id=spec.packet_id,
            gate_id=spec.gate_id,
            expected_identity=identity,
            gate_source_digest=two.gate_source_digest,
            topology_order=("two-node", "four-node"),
            completion_receipts=(
                "two-node/topology-complete.json",
                "four-node/topology-complete.json",
            ),
            phase3_manifest_placeholders=(
                "PHASE3_TWO_NODE_MANIFEST",
                "PHASE3_FOUR_NODE_MANIFEST",
            ),
            promotion_authority=False,
            scorecard_update_allowed=False,
        )
        two_ref = _write_exclusive(payload_dir / "two-node-payload.json", two)
        four_ref = _write_exclusive(payload_dir / "four-node-payload.json", four)
        finalizer_ref = _write_exclusive(payload_dir / "f7-finalizer-template.json", finalizer)
        source_root = payload_dir / "source"
        repo_root = Path(__file__).resolve().parents[2]
        for entry in sources:
            target = source_root / entry.relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((repo_root / entry.relative_path).read_bytes())
            target.chmod(entry.mode)
        archive = temporary / "f7-payload.zip"
        _deterministic_zip(payload_dir, archive)
        final_two = F7ImmutableJSONRef(
            path=str((destination / "payload/two-node-payload.json").resolve()),
            digest=two_ref.digest,
        )
        final_four = F7ImmutableJSONRef(
            path=str((destination / "payload/four-node-payload.json").resolve()),
            digest=four_ref.digest,
        )
        final_template = F7ImmutableJSONRef(
            path=str((destination / "payload/f7-finalizer-template.json").resolve()),
            digest=finalizer_ref.digest,
        )
        packet = F7TargetLaunchPacket(
            schema_version="bb.rl.phase5-f7-target-launch-packet.v1",
            packet_id=spec.packet_id,
            packet_root=str(destination),
            authority=authority,
            expected_identity=identity,
            two_node_payload=final_two,
            four_node_payload=final_four,
            finalizer_template=final_template,
            payload_zip=F7ImmutableFileRef(
                path=str((destination / "f7-payload.zip").resolve()),
                digest=_sha256(archive.read_bytes()),
                media_type="application/zip",
            ),
            source_entries=sources,
            topology_order=("two-node", "four-node"),
            gate_source_digest=two.gate_source_digest,
            permanent_non_authority=True,
            promotion_authority=False,
            scorecard_update_allowed=False,
        )
        _write_exclusive(temporary / "f7-launch-packet.json", packet)
        shutil.rmtree(payload_dir / "source")
        os.rename(temporary, destination)
        return packet
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def read_f7_target_launch_authoring_input(path: str) -> F7TargetLaunchAuthoringInput:
    source = Path(_absolute(path)).resolve(strict=True)
    raw = source.read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise F7LaunchPacketError("F7 authoring input is not JSON") from exc
    if canonical_json_bytes(value) != raw:
        raise F7LaunchPacketError("F7 authoring input is not canonical JSON")
    return F7TargetLaunchAuthoringInput.model_validate_json(raw, strict=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the ordered source-closed F7 target launch packet")
    parser.add_argument("--input", required=True)
    parser.add_argument("--packet-root", required=True)
    args = parser.parse_args(argv)
    packet = build_f7_target_launch_packet(
        read_f7_target_launch_authoring_input(args.input), args.packet_root
    )
    os.write(1, canonical_json_bytes(packet.model_dump(mode="json")) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "F7AuthorityClosure",
    "F7AuthorityRef",
    "F7BaselineObservation",
    "F7ConfigAuthority",
    "F7FinalizerTemplate",
    "F7FrozenWorkload",
    "F7ImmutableFileRef",
    "F7LaunchPacketError",
    "F7TargetLaunchAuthoringInput",
    "F7TargetLaunchPacket",
    "F7TargetWorkloadPayload",
    "F7TopologyAuthoring",
    "build_f7_target_launch_packet",
    "read_f7_target_launch_authoring_input",
]
