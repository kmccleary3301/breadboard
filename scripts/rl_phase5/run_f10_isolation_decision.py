from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard_engine.compilation.contracts import canonical_json_bytes, canonical_json_loads
from breadboard.rl.phase5.f5_fault_campaign import F5PinnedIdentity
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_REPORT_NAME = "f10-isolation-decision.report.json"
_COMPONENT = "rl_phase5_f10_isolation_decision"
_INFEASIBLE = "runsc is absent or incompatible with the pinned Docker/image/runtime/task contract"
_EMPTY_SHA256 = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_MAX_RAW_BYTES = 16 * 1024 * 1024
_PROBE_COMMANDS = (
    ("docker-runtimes", ("docker", "info", "--format", "{{json .Runtimes}}")),
    ("runsc-path", ("command", "-v", "runsc")),
    ("runsc-version", ("runsc", "--version")),
)
_CASES = (
    ("gold-1", "gold", "passed"),
    ("gold-2", "gold", "passed"),
    ("bad-1", "bad", "rejected"),
    ("bad-2", "bad", "rejected"),
    ("no-op-1", "no-op", "rejected"),
    ("no-op-2", "no-op", "rejected"),
)


class F10IsolationDecisionError(RuntimeError):
    pass


class _ExactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _digest(value: str) -> str:
    if (
        type(value) is not str
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError("F10 requires a lowercase sha256 digest")
    return value


def _absolute(value: str) -> str:
    if type(value) is not str or not value.startswith("/") or os.path.normpath(value) != value:
        raise ValueError("F10 paths must be absolute and normalized")
    return value


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _write_all(descriptor: int, raw: bytes) -> None:
    view = memoryview(raw)
    written = 0
    while written < len(view):
        count = os.write(descriptor, view[written:])
        if count <= 0:
            raise OSError("short write while persisting F10 canonical bytes")
        written += count


class F10TargetIdentity(_ExactModel):
    target_run_id: str = Field(min_length=1, max_length=256)
    job_id: str = Field(min_length=1, max_length=256)
    node_id: str = Field(min_length=1, max_length=512)


class F10IdentityClosure(_ExactModel):
    docker: F5PinnedIdentity
    image: F5PinnedIdentity
    runtime: F5PinnedIdentity
    config: F5PinnedIdentity
    task: F5PinnedIdentity
    verifier: F5PinnedIdentity


class F10ProbeCommand(_ExactModel):
    purpose: Literal["docker-runtimes", "runsc-path", "runsc-version"]
    argv: tuple[str, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def bounded_argv(self) -> "F10ProbeCommand":
        if any(not arg or len(arg) > 4096 or "\x00" in arg for arg in self.argv):
            raise ValueError("probe argv is empty, oversized, or contains NUL")
        return self


class F10IsolationPolicy(_ExactModel):
    network_mode: Literal["none"]
    read_only_root: Literal[True]
    nonroot: Literal[True]
    uid: Literal[65532]
    gid: Literal[65532]
    cap_drop: tuple[Literal["ALL"]]
    no_new_privileges: Literal[True]
    docker_socket_mounted: Literal[False]
    single_tenant: Literal[True]
    hardened_oci_runtime: Literal["runc"]


class F10DigitalOceanDecision(_ExactModel):
    activated: bool
    reason: str = Field(min_length=1, max_length=512)
    approved_question: str | None = Field(min_length=1, max_length=1024)
    provider: Literal["digitalocean"] | None
    droplet_id: str | None = Field(min_length=1, max_length=128)
    region: str | None = Field(min_length=1, max_length=128)
    network_ref: F5PinnedIdentity | None
    image_ref: F5PinnedIdentity | None
    runtime_ref: F5PinnedIdentity | None
    secret_source_ref: F5PinnedIdentity | None
    maximum_cost_usd: float | None
    ttl_seconds: int | None
    episode_evidence_ref: F5PinnedIdentity | None
    cleanup_ref: F5PinnedIdentity | None
    provider_teardown_ref: F5PinnedIdentity | None

    @model_validator(mode="after")
    def conditional_activation(self) -> "F10DigitalOceanDecision":
        details = (
            self.approved_question,
            self.provider,
            self.droplet_id,
            self.region,
            self.network_ref,
            self.image_ref,
            self.runtime_ref,
            self.secret_source_ref,
            self.maximum_cost_usd,
            self.ttl_seconds,
            self.episode_evidence_ref,
            self.cleanup_ref,
            self.provider_teardown_ref,
        )
        if self.activated:
            if any(value is None for value in details):
                raise ValueError(
                    "activated DigitalOcean requires approved question, exact provider/droplet/region/network/image/runtime/secret-source, cost/TTL, episode evidence, cleanup, and provider teardown"
                )
            if self.maximum_cost_usd <= 0 or self.ttl_seconds <= 0:  # type: ignore[operator]
                raise ValueError("activated DigitalOcean cost and TTL must be positive")
        elif any(value is not None for value in details):
            raise ValueError(
                "inactive DigitalOcean forbids activation-only provider and evidence details"
            )
        return self


class F10EpisodeCase(_ExactModel):
    case_id: str
    specimen: Literal["gold", "bad", "no-op"]
    expected_classification: Literal["passed", "rejected"]
    episode_id: str = Field(min_length=1, max_length=256)
    attempt_id: str = Field(min_length=1, max_length=256)
    case_ref: F5PinnedIdentity
    specimen_ref: F5PinnedIdentity
    contract_join: F10IdentityClosure
    manifest_path: str
    expected_task_output_digest: str

    _expected_output = field_validator("expected_task_output_digest")(_digest)

    @model_validator(mode="after")
    def exact_manifest(self) -> "F10EpisodeCase":
        expected = f"/opt/breadboard/f10/cases/{self.case_id}.json"
        if self.manifest_path != expected:
            raise ValueError("F10 case manifest path is not exact")
        if self.expected_task_output_digest == _EMPTY_SHA256:
            raise ValueError("F10 cases require a nonempty frozen expected task output")
        return self


class F10IsolationDecisionInput(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f10-isolation-decision-input.v2"]
    target: F10TargetIdentity
    identities: F10IdentityClosure
    approved_question: str = Field(min_length=1, max_length=1024)
    expected_branch: Literal["compatible", "incompatible"]
    commands: tuple[F10ProbeCommand, F10ProbeCommand, F10ProbeCommand]
    isolation: F10IsolationPolicy
    cases: tuple[
        F10EpisodeCase,
        F10EpisodeCase,
        F10EpisodeCase,
        F10EpisodeCase,
        F10EpisodeCase,
        F10EpisodeCase,
    ]
    digitalocean: F10DigitalOceanDecision
    output_dir: str

    _output = field_validator("output_dir")(_absolute)

    @model_validator(mode="after")
    def closed_gate(self) -> "F10IsolationDecisionInput":
        if tuple((command.purpose, command.argv) for command in self.commands) != _PROBE_COMMANDS:
            raise ValueError("F10 probe commands are not the exact closed three-probe set")
        actual = tuple(
            (case.case_id, case.specimen, case.expected_classification)
            for case in self.cases
        )
        if actual != _CASES:
            raise ValueError("F10 requires exact 2x gold/bad/no-op classification cases")
        if len({case.episode_id for case in self.cases}) != 6 or len(
            {case.attempt_id for case in self.cases}
        ) != 6:
            raise ValueError("F10 episode and attempt IDs must be unique")
        if any(case.contract_join != self.identities for case in self.cases):
            raise ValueError("F10 case fixture identity closure mismatch")
        if len({case.case_ref.digest for case in self.cases}) != 6 or len(
            {case.specimen_ref.digest for case in self.cases}
        ) != 6:
            raise ValueError("F10 case and specimen authorities must be unique")
        if (
            self.digitalocean.activated
            and self.digitalocean.approved_question != self.approved_question
        ):
            raise ValueError(
                "activated DigitalOcean does not bind the approved portability question"
            )
        return self


class F10RawArtifact(_ExactModel):
    path: str
    digest: str
    size: int = Field(ge=0, le=_MAX_RAW_BYTES)

    _path = field_validator("path")(_absolute)
    _digest = field_validator("digest")(_digest)


class F10CommandObservation(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f10-command-observation.v2"]
    observation_id: str = Field(min_length=1, max_length=256)
    argv: tuple[str, ...] = Field(min_length=1, max_length=128)
    exit_code: int = Field(ge=0, le=255)
    stdout: F10RawArtifact
    stderr: F10RawArtifact

    @model_validator(mode="after")
    def closed_command(self) -> "F10CommandObservation":
        if any(not arg or len(arg) > 4096 or "\x00" in arg for arg in self.argv):
            raise ValueError("observed command argv is empty, oversized, or contains NUL")
        return self


class F10VerifierResult(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f10-verifier-result.v1"]
    case_id: str
    episode_id: str
    attempt_id: str
    specimen: Literal["gold", "bad", "no-op"]
    classification: Literal["passed", "rejected"]
    reason_code: Literal["MATCH", "OUTPUT_MISMATCH", "NO_OUTPUT"]
    runtime_name: Literal["runsc", "runc"]
    case_digest: str
    specimen_digest: str
    docker_digest: str
    image_digest: str
    runtime_digest: str
    config_digest: str
    task_digest: str
    verifier_digest: str
    observed_task_output_digest: str

    _digests = field_validator(
        "case_digest",
        "specimen_digest",
        "docker_digest",
        "image_digest",
        "runtime_digest",
        "config_digest",
        "task_digest",
        "verifier_digest",
        "observed_task_output_digest",
    )(_digest)


class F10EnvironmentObservation(_ExactModel):
    docker_runtimes: tuple[str, ...]
    runsc_path: str | None
    runsc_version: str | None
    compatible: bool
    infeasibility: str | None
    probes: tuple[F10CommandObservation, F10CommandObservation, F10CommandObservation]
    runsc_canary: F10CommandObservation | None
    runsc_canary_cleanup: F10CommandObservation | None


class F10CleanupObservation(_ExactModel):
    released: Literal[True]
    no_orphan: Literal[True]
    active_lease_ids: tuple[str, ...]
    orphan_resource_ids: tuple[str, ...]
    cleanup_errors: tuple[str, ...]

    @model_validator(mode="after")
    def clean(self) -> "F10CleanupObservation":
        if self.active_lease_ids or self.orphan_resource_ids or self.cleanup_errors:
            raise ValueError("F10 cleanup contains a lease, orphan, or error")
        return self


class F10EpisodeRuntimeObservation(_ExactModel):
    case_id: str
    episode_id: str
    attempt_id: str
    selected_runtime: Literal["runsc", "hardened-docker"]
    terminal_state: Literal["closed"]
    contract_join: F10IdentityClosure
    isolation: F10IsolationPolicy
    execution: F10CommandObservation
    runtime_inspection: F10CommandObservation
    cleanup_remove: F10CommandObservation
    cleanup_probe: F10CommandObservation
    cleanup: F10CleanupObservation


class F10EpisodeObservation(F10EpisodeRuntimeObservation):
    classification: Literal["passed", "rejected"]
    verifier_result: F10VerifierResult


class F10IsolationDecisionReport(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f10-isolation-decision-report.v2"]
    report_id: str
    input_digest: str
    target: F10TargetIdentity
    identities: F10IdentityClosure
    approved_question: str = Field(min_length=1, max_length=1024)
    branch: Literal["compatible", "incompatible"]
    disposition: Literal["RUNSC_SELECTED_AFTER_PARITY", "INFEASIBLE_WITH_REQUIRED_NONCLAIM"]
    environment: F10EnvironmentObservation
    cases: tuple[F10EpisodeObservation, ...]
    raw_artifact_snapshot: tuple[F10RawArtifact, ...]
    digitalocean: F10DigitalOceanDecision
    summary: dict[str, Any]
    permanent_non_authority: Literal[True]
    promotion_authority: Literal[False]
    scorecard_authority: Literal[False]

    _input = field_validator("input_digest")(_digest)

    @model_validator(mode="after")
    def complete_gate(self) -> "F10IsolationDecisionReport":
        expected_runtime = "runsc" if self.branch == "compatible" else "hardened-docker"
        if self.branch == "compatible" and self.disposition != "RUNSC_SELECTED_AFTER_PARITY":
            raise ValueError("compatible branch disposition mismatch")
        if self.branch == "incompatible" and self.disposition != "INFEASIBLE_WITH_REQUIRED_NONCLAIM":
            raise ValueError("incompatible branch disposition mismatch")
        if self.environment.compatible != (self.branch == "compatible"):
            raise ValueError("environment and selected branch mismatch")
        if len(self.cases) != 6 or any(
            case.selected_runtime != expected_runtime for case in self.cases
        ):
            raise ValueError("F10 requires six exact reruns on the selected runtime")
        expected_summary = {
            "gold_passed": 2,
            "bad_rejected": 2,
            "no_op_rejected": 2,
            "cleanup_complete": True,
            "no_orphans": True,
            "digitalocean_activated": self.digitalocean.activated,
        }
        if self.summary != expected_summary:
            raise ValueError("F10 report summary is not the exact gate projection")
        if not self.raw_artifact_snapshot or len(
            {artifact.path for artifact in self.raw_artifact_snapshot}
        ) != len(self.raw_artifact_snapshot):
            raise ValueError("F10 raw artifact snapshot is empty or contains duplicate paths")
        return self


@runtime_checkable
class F10TargetRuntime(Protocol):
    async def observe_environment(
        self, commands: tuple[F10ProbeCommand, F10ProbeCommand, F10ProbeCommand]
    ) -> F10EnvironmentObservation: ...

    async def execute_episode(
        self,
        case: F10EpisodeCase,
        *,
        selected_runtime: Literal["runsc", "hardened-docker"],
        identities: F10IdentityClosure,
        isolation: F10IsolationPolicy,
    ) -> F10EpisodeRuntimeObservation: ...

    async def close(self) -> None: ...


def f10_container_name(
    target: F10TargetIdentity, case: F10EpisodeCase, *, suffix: str = "episode"
) -> str:
    raw = canonical_json_bytes(
        {
            "attempt_id": case.attempt_id,
            "case_id": case.case_id,
            "episode_id": case.episode_id,
            "suffix": suffix,
            "target": target.model_dump(mode="json"),
        }
    )
    return f"bb-f10-{case.case_id}-{suffix}-{hashlib.sha256(raw).hexdigest()[:16]}"


def f10_verifier_argv(
    case: F10EpisodeCase, identities: F10IdentityClosure
) -> tuple[str, ...]:
    return (
        "/opt/breadboard/f10/verifier",
        "--case-manifest",
        case.manifest_path,
        "--case-digest",
        case.case_ref.digest,
        "--specimen-digest",
        case.specimen_ref.digest,
        "--docker-digest",
        identities.docker.digest,
        "--image-digest",
        identities.image.digest,
        "--runtime-digest",
        identities.runtime.digest,
        "--config-digest",
        identities.config.digest,
        "--task-digest",
        identities.task.digest,
        "--verifier-digest",
        identities.verifier.digest,
    )


def _docker_image_ref(identities: F10IdentityClosure) -> str:
    value = identities.image.immutable_ref
    if not value.startswith("docker://"):
        raise F10IsolationDecisionError("F10 image is not a docker:// content address")
    return value[len("docker://") :]


def f10_docker_argv(
    target: F10TargetIdentity,
    case: F10EpisodeCase,
    identities: F10IdentityClosure,
    *,
    runtime_name: Literal["runsc", "runc"],
    remove: bool,
    suffix: str = "episode",
) -> tuple[str, ...]:
    name = f10_container_name(target, case, suffix=suffix)
    remove_argv = ("--rm",) if remove else ()
    return (
        "docker",
        "run",
        *remove_argv,
        "--name",
        name,
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--user",
        "65532:65532",
        "--runtime",
        runtime_name,
        "--label",
        f"breadboard.f10.case_id={case.case_id}",
        "--label",
        f"breadboard.f10.episode_id={case.episode_id}",
        "--label",
        f"breadboard.f10.attempt_id={case.attempt_id}",
        "--label",
        f"breadboard.f10.case_digest={case.case_ref.digest}",
        "--label",
        f"breadboard.f10.specimen_digest={case.specimen_ref.digest}",
        _docker_image_ref(identities),
        *f10_verifier_argv(case, identities),
    )


def _cleanup_probe_argv(name: str) -> tuple[str, ...]:
    return (
        "docker",
        "container",
        "ls",
        "--all",
        "--quiet",
        "--filter",
        f"name=^/{name}$",
    )


def _artifact_bytes(reference: F10RawArtifact, output_dir: str) -> bytes:
    root = Path(output_dir).resolve(strict=True)
    path = Path(reference.path)
    resolved = path.resolve(strict=True)
    if resolved != path or os.path.commonpath((os.fspath(root), os.fspath(resolved))) != os.fspath(root):
        raise F10IsolationDecisionError("raw F10 artifact escapes its canonical output directory")
    descriptor = os.open(
        resolved,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise F10IsolationDecisionError("raw F10 artifact is not a regular file")
        chunks: list[bytes] = []
        size = 0
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, _MAX_RAW_BYTES + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > _MAX_RAW_BYTES:
                raise F10IsolationDecisionError("raw F10 artifact exceeds the evidence bound")
            digest.update(chunk)
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    if size != reference.size or "sha256:" + digest.hexdigest() != reference.digest:
        raise F10IsolationDecisionError("raw F10 artifact size or digest mismatch")
    return raw


def _command_bytes(
    observation: F10CommandObservation, output_dir: str
) -> tuple[bytes, bytes]:
    return (
        _artifact_bytes(observation.stdout, output_dir),
        _artifact_bytes(observation.stderr, output_dir),
    )


def _single_line(raw: bytes) -> str | None:
    try:
        value = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    return value if value and "\n" not in value and "\r" not in value else None


def _parse_runtime_map(raw: bytes) -> tuple[str, ...]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise F10IsolationDecisionError("Docker runtime-map probe was not JSON") from exc
    if type(value) is not dict or any(type(name) is not str or not name for name in value):
        raise F10IsolationDecisionError("Docker runtime-map probe was not an exact runtime map")
    return tuple(sorted(value))


def _parse_verifier(raw: bytes) -> F10VerifierResult:
    try:
        value = canonical_json_loads(raw)
    except Exception as exc:
        raise F10IsolationDecisionError("F10 verifier output is not JSON") from exc
    if canonical_json_bytes(value) != raw:
        raise F10IsolationDecisionError("F10 verifier output is not canonical JSON")
    try:
        return F10VerifierResult.model_validate_json(raw, strict=True)
    except Exception as exc:
        raise F10IsolationDecisionError("F10 verifier output is malformed") from exc


def _validate_verifier(
    result: F10VerifierResult,
    case: F10EpisodeCase,
    identities: F10IdentityClosure,
    runtime_name: Literal["runsc", "runc"],
) -> None:
    expected_reason = {
        "gold": "MATCH",
        "bad": "OUTPUT_MISMATCH",
        "no-op": "NO_OUTPUT",
    }[case.specimen]
    expected = {
        "case_id": case.case_id,
        "episode_id": case.episode_id,
        "attempt_id": case.attempt_id,
        "specimen": case.specimen,
        "classification": case.expected_classification,
        "reason_code": expected_reason,
        "runtime_name": runtime_name,
        "case_digest": case.case_ref.digest,
        "specimen_digest": case.specimen_ref.digest,
        "docker_digest": identities.docker.digest,
        "image_digest": identities.image.digest,
        "runtime_digest": identities.runtime.digest,
        "config_digest": identities.config.digest,
        "task_digest": identities.task.digest,
        "verifier_digest": identities.verifier.digest,
    }
    actual = result.model_dump(mode="json", exclude={"schema_version", "observed_task_output_digest"})
    if actual != expected:
        raise F10IsolationDecisionError("F10 verifier result does not bind the frozen case contract")
    if case.specimen == "gold" and result.observed_task_output_digest != case.expected_task_output_digest:
        raise F10IsolationDecisionError("gold verifier output does not match the frozen expected output")
    if case.specimen == "bad" and result.observed_task_output_digest == case.expected_task_output_digest:
        raise F10IsolationDecisionError("bad verifier did not observe an output mismatch")
    if case.specimen == "no-op" and result.observed_task_output_digest != _EMPTY_SHA256:
        raise F10IsolationDecisionError("no-op verifier did not observe empty output")


def _validate_runsc_canary_verifier(
    result: F10VerifierResult,
    case: F10EpisodeCase,
    identities: F10IdentityClosure,
) -> bool:
    expected_join = {
        "case_id": case.case_id,
        "episode_id": case.episode_id,
        "attempt_id": case.attempt_id,
        "specimen": case.specimen,
        "runtime_name": "runsc",
        "case_digest": case.case_ref.digest,
        "specimen_digest": case.specimen_ref.digest,
        "docker_digest": identities.docker.digest,
        "image_digest": identities.image.digest,
        "runtime_digest": identities.runtime.digest,
        "config_digest": identities.config.digest,
        "task_digest": identities.task.digest,
        "verifier_digest": identities.verifier.digest,
    }
    actual_join = result.model_dump(
        mode="json",
        exclude={
            "schema_version",
            "classification",
            "reason_code",
            "observed_task_output_digest",
        },
    )
    if actual_join != expected_join:
        raise F10IsolationDecisionError(
            "runsc canary verifier does not bind the frozen case contract"
        )
    if result.classification == "passed":
        if (
            result.reason_code != "MATCH"
            or result.observed_task_output_digest
            != case.expected_task_output_digest
        ):
            raise F10IsolationDecisionError(
                "runsc canary pass is not an exact verifier match"
            )
        return True
    rejected_output = (
        result.reason_code == "OUTPUT_MISMATCH"
        and result.observed_task_output_digest
        != case.expected_task_output_digest
    )
    rejected_no_output = (
        result.reason_code == "NO_OUTPUT"
        and result.observed_task_output_digest == _EMPTY_SHA256
    )
    if not rejected_output and not rejected_no_output:
        raise F10IsolationDecisionError(
            "runsc canary rejection is not an exact verifier disposition"
        )
    return False


def _validate_environment(
    spec: F10IsolationDecisionInput, environment: F10EnvironmentObservation
) -> Literal["compatible", "incompatible"]:
    if tuple((probe.observation_id, probe.argv) for probe in environment.probes) != tuple(
        (command.purpose, command.argv) for command in spec.commands
    ):
        raise F10IsolationDecisionError("environment probe command evidence mismatch")
    probe_bytes = tuple(_command_bytes(probe, spec.output_dir) for probe in environment.probes)
    if environment.probes[0].exit_code != 0:
        raise F10IsolationDecisionError("Docker runtime-map probe failed")
    runtimes = _parse_runtime_map(probe_bytes[0][0])
    path = (
        _single_line(probe_bytes[1][0])
        if environment.probes[1].exit_code == 0
        else None
    )
    if path is not None and (not path.startswith("/") or os.path.normpath(path) != path):
        path = None
    version = (
        _single_line(probe_bytes[2][0])
        if environment.probes[2].exit_code == 0
        else None
    )
    installed = "runsc" in runtimes and path is not None and version is not None
    compatible = False
    if installed:
        canary = environment.runsc_canary
        cleanup = environment.runsc_canary_cleanup
        if canary is None or cleanup is None:
            raise F10IsolationDecisionError("installed runsc requires effective task canary evidence")
        gold = spec.cases[0]
        expected_canary = f10_docker_argv(
            spec.target,
            gold,
            spec.identities,
            runtime_name="runsc",
            remove=True,
            suffix="runsc-canary",
        )
        if canary.observation_id != "runsc-effective-canary" or canary.argv != expected_canary:
            raise F10IsolationDecisionError("runsc canary command evidence mismatch")
        canary_stdout, _ = _command_bytes(canary, spec.output_dir)
        canary_name = f10_container_name(spec.target, gold, suffix="runsc-canary")
        if cleanup.observation_id != "runsc-canary-cleanup" or cleanup.argv != _cleanup_probe_argv(canary_name):
            raise F10IsolationDecisionError("runsc canary cleanup command evidence mismatch")
        cleanup_stdout, _ = _command_bytes(cleanup, spec.output_dir)
        if cleanup.exit_code != 0 or cleanup_stdout != b"":
            raise F10IsolationDecisionError("runsc canary cleanup did not prove absence")
        if canary.exit_code == 0:
            verifier = _parse_verifier(canary_stdout)
            compatible = _validate_runsc_canary_verifier(
                verifier, gold, spec.identities
            )
    elif environment.runsc_canary is not None or environment.runsc_canary_cleanup is not None:
        raise F10IsolationDecisionError("absent runsc forbids effective canary declarations")
    if not compatible and "runc" not in runtimes:
        raise F10IsolationDecisionError("incompatible runsc requires the pinned runc fallback")
    expected_infeasibility = None if compatible else _INFEASIBLE
    if (
        environment.docker_runtimes != runtimes
        or environment.runsc_path != path
        or environment.runsc_version != version
        or environment.compatible != compatible
        or environment.infeasibility != expected_infeasibility
    ):
        raise F10IsolationDecisionError("environment declarations differ from persisted raw evidence")
    return "compatible" if compatible else "incompatible"


def _derive_episode(
    spec: F10IsolationDecisionInput,
    case: F10EpisodeCase,
    runtime_observation: F10EpisodeRuntimeObservation,
    selected_runtime: Literal["runsc", "hardened-docker"],
) -> F10EpisodeObservation:
    if (
        runtime_observation.case_id != case.case_id
        or runtime_observation.episode_id != case.episode_id
        or runtime_observation.attempt_id != case.attempt_id
        or runtime_observation.selected_runtime != selected_runtime
        or runtime_observation.contract_join != spec.identities
        or runtime_observation.isolation != spec.isolation
    ):
        raise F10IsolationDecisionError(f"case {case.case_id} identity or isolation mismatch")
    runtime_name: Literal["runsc", "runc"] = (
        "runsc" if selected_runtime == "runsc" else spec.isolation.hardened_oci_runtime
    )
    name = f10_container_name(spec.target, case)
    expected_execution = f10_docker_argv(
        spec.target,
        case,
        spec.identities,
        runtime_name=runtime_name,
        remove=False,
    )
    execution = runtime_observation.execution
    if execution.observation_id != f"case:{case.case_id}" or execution.argv != expected_execution:
        raise F10IsolationDecisionError(f"case {case.case_id} execution command mismatch")
    stdout, _ = _command_bytes(execution, spec.output_dir)
    if execution.exit_code != 0:
        raise F10IsolationDecisionError(
            f"case {case.case_id} launcher or infrastructure failure: exit {execution.exit_code}"
        )
    verifier = _parse_verifier(stdout)
    _validate_verifier(verifier, case, spec.identities, runtime_name)
    inspection = runtime_observation.runtime_inspection
    expected_inspection = ("docker", "inspect", "--format", "{{.HostConfig.Runtime}}", name)
    if inspection.observation_id != f"runtime:{case.case_id}" or inspection.argv != expected_inspection:
        raise F10IsolationDecisionError(f"case {case.case_id} runtime inspection mismatch")
    inspection_stdout, _ = _command_bytes(inspection, spec.output_dir)
    if inspection.exit_code != 0 or _single_line(inspection_stdout) != runtime_name:
        raise F10IsolationDecisionError(f"case {case.case_id} effective runtime mismatch")
    removal = runtime_observation.cleanup_remove
    if removal.observation_id != f"remove:{case.case_id}" or removal.argv != (
        "docker",
        "rm",
        name,
    ):
        raise F10IsolationDecisionError(f"case {case.case_id} cleanup removal mismatch")
    _command_bytes(removal, spec.output_dir)
    if removal.exit_code != 0:
        raise F10IsolationDecisionError(f"case {case.case_id} cleanup removal failed")
    cleanup = runtime_observation.cleanup_probe
    if cleanup.observation_id != f"cleanup:{case.case_id}" or cleanup.argv != _cleanup_probe_argv(name):
        raise F10IsolationDecisionError(f"case {case.case_id} cleanup probe mismatch")
    cleanup_stdout, _ = _command_bytes(cleanup, spec.output_dir)
    if cleanup.exit_code != 0 or cleanup_stdout != b"":
        raise F10IsolationDecisionError(f"case {case.case_id} cleanup did not prove absence")
    return F10EpisodeObservation(
        **runtime_observation.model_dump(mode="python"),
        classification=verifier.classification,
        verifier_result=verifier,
    )


def _write_report(report: F10IsolationDecisionReport, output_dir: str) -> str:
    root = Path(output_dir)
    root.mkdir(mode=0o750, parents=False, exist_ok=True)
    output = root / _REPORT_NAME
    raw = canonical_json_bytes(report.model_dump(mode="json"))
    descriptor = os.open(
        output,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o440,
    )
    try:
        _write_all(descriptor, raw)
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        output.unlink(missing_ok=True)
        raise
    else:
        os.close(descriptor)
    if output.read_bytes() != raw:
        output.unlink(missing_ok=True)
        raise F10IsolationDecisionError("persisted F10 report mismatch")
    return os.fspath(output.resolve())


def _evidence_snapshot(
    spec: F10IsolationDecisionInput,
    environment: F10EnvironmentObservation,
    runtime_observations: tuple[F10EpisodeRuntimeObservation, ...],
) -> tuple[F10RawArtifact, ...]:
    commands: list[F10CommandObservation] = list(environment.probes)
    if environment.runsc_canary is not None:
        commands.append(environment.runsc_canary)
    if environment.runsc_canary_cleanup is not None:
        commands.append(environment.runsc_canary_cleanup)
    for observation in runtime_observations:
        commands.extend(
            (
                observation.execution,
                observation.runtime_inspection,
                observation.cleanup_remove,
                observation.cleanup_probe,
            )
        )
    snapshot = tuple(
        artifact
        for command in commands
        for artifact in (command.stdout, command.stderr)
    )
    if len({artifact.path for artifact in snapshot}) != len(snapshot):
        raise F10IsolationDecisionError(
            "F10 raw evidence paths are not immutable one-to-one snapshots"
        )
    for artifact in snapshot:
        _artifact_bytes(artifact, spec.output_dir)
    return snapshot


async def _run(
    spec: F10IsolationDecisionInput, input_digest: str, runtime: F10TargetRuntime
) -> tuple[F10IsolationDecisionReport, str]:
    if not isinstance(runtime, F10TargetRuntime):
        raise TypeError("runtime must implement the F10 target seam")
    environment: F10EnvironmentObservation
    observations: list[F10EpisodeObservation] = []
    runtime_observations: list[F10EpisodeRuntimeObservation] = []
    try:
        environment = await runtime.observe_environment(spec.commands)
        if type(environment) is not F10EnvironmentObservation:
            raise TypeError("environment observation must be exact")
        observed_branch = _validate_environment(spec, environment)
        if observed_branch != spec.expected_branch:
            raise F10IsolationDecisionError(
                "observed runsc branch differs from approved input branch"
            )
        selected_runtime: Literal["runsc", "hardened-docker"] = (
            "runsc" if observed_branch == "compatible" else "hardened-docker"
        )
        for case in spec.cases:
            runtime_observation = await runtime.execute_episode(
                case,
                selected_runtime=selected_runtime,
                identities=spec.identities,
                isolation=spec.isolation,
            )
            if type(runtime_observation) is not F10EpisodeRuntimeObservation:
                raise TypeError("episode runtime observation must be exact")
            runtime_observations.append(runtime_observation)
            observation = _derive_episode(
                spec, case, runtime_observation, selected_runtime
            )
            if observation.classification != case.expected_classification:
                raise F10IsolationDecisionError(
                    f"case {case.case_id} verifier classification mismatch"
                )
            observations.append(observation)
    finally:
        await runtime.close()
    post_close_branch = _validate_environment(spec, environment)
    if post_close_branch != observed_branch:
        raise F10IsolationDecisionError(
            "runtime close changed the persisted environment evidence"
        )
    post_close_observations = tuple(
        _derive_episode(spec, case, runtime_observation, selected_runtime)
        for case, runtime_observation in zip(
            spec.cases, runtime_observations, strict=True
        )
    )
    if post_close_observations != tuple(observations):
        raise F10IsolationDecisionError(
            "runtime close changed the persisted episode evidence"
        )
    raw_artifact_snapshot = _evidence_snapshot(
        spec, environment, tuple(runtime_observations)
    )
    report = F10IsolationDecisionReport(
        schema_version="bb.rl.phase5-f10-isolation-decision-report.v2",
        report_id=f"f10-isolation-decision-{spec.target.target_run_id}",
        input_digest=input_digest,
        target=spec.target,
        identities=spec.identities,
        approved_question=spec.approved_question,
        branch=spec.expected_branch,
        disposition=(
            "RUNSC_SELECTED_AFTER_PARITY"
            if spec.expected_branch == "compatible"
            else "INFEASIBLE_WITH_REQUIRED_NONCLAIM"
        ),
        environment=environment,
        cases=tuple(observations),
        raw_artifact_snapshot=raw_artifact_snapshot,
        digitalocean=spec.digitalocean,
        summary={
            "gold_passed": 2,
            "bad_rejected": 2,
            "no_op_rejected": 2,
            "cleanup_complete": True,
            "no_orphans": True,
            "digitalocean_activated": spec.digitalocean.activated,
        },
        permanent_non_authority=True,
        promotion_authority=False,
        scorecard_authority=False,
    )
    return report, _write_report(report, spec.output_dir)


def _component_line(report: F10IsolationDecisionReport, path: str) -> bytes:
    raw = canonical_json_bytes(report.model_dump(mode="json"))
    persisted = Path(path).read_bytes()
    if persisted != raw:
        raise F10IsolationDecisionError("persisted F10 report mismatch")
    envelope = {
        "schema_version": "bb.rl.phase5-f10-isolation-component-report.v2",
        "report_id": report.report_id,
        "component": _COMPONENT,
        "passed": True,
        "permanent_non_authority": True,
        "promotion_authority": False,
        "scorecard_authority": False,
        "scorecard_update_allowed": False,
        "report_sha256": _sha256(raw),
        "report_path": path,
        "summary": report.summary,
    }
    return b"PHASE3_COMPONENT_REPORT_JSON=" + canonical_json_bytes(envelope) + b"\n"


def run_f10_isolation_decision(
    spec: F10IsolationDecisionInput,
    *,
    input_digest: str,
    runtime: F10TargetRuntime,
) -> tuple[F10IsolationDecisionReport, str]:
    if type(spec) is not F10IsolationDecisionInput:
        raise TypeError("spec must be an exact F10IsolationDecisionInput")
    expected_input_digest = _sha256(
        canonical_json_bytes(spec.model_dump(mode="json"))
    )
    if input_digest != expected_input_digest:
        raise F10IsolationDecisionError(
            "F10 input digest does not match the exact canonical input bytes"
        )
    report, path = asyncio.run(_run(spec, expected_input_digest, runtime))
    os.write(1, _component_line(report, path))
    return report, path


def _read_input(path: str) -> tuple[F10IsolationDecisionInput, str]:
    raw = Path(path).resolve(strict=True).read_bytes()
    value = canonical_json_loads(raw)
    if canonical_json_bytes(value) != raw:
        raise F10IsolationDecisionError("F10 input is not canonical JSON")
    return F10IsolationDecisionInput.model_validate_json(raw, strict=True), _sha256(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the strict F10 runsc isolation decision gate")
    parser.add_argument("--input", required=True)
    raise F10IsolationDecisionError(
        "F10 CLI requires the production target runtime seam; invoke run_f10_isolation_decision from the target launcher"
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "F10CleanupObservation",
    "F10CommandObservation",
    "F10DigitalOceanDecision",
    "F10EnvironmentObservation",
    "F10EpisodeCase",
    "F10EpisodeObservation",
    "F10EpisodeRuntimeObservation",
    "F10IdentityClosure",
    "F10IsolationDecisionError",
    "F10IsolationDecisionInput",
    "F10IsolationDecisionReport",
    "F10IsolationPolicy",
    "F10ProbeCommand",
    "F10RawArtifact",
    "F10TargetIdentity",
    "F10TargetRuntime",
    "F10VerifierResult",
    "f10_container_name",
    "f10_docker_argv",
    "f10_verifier_argv",
    "run_f10_isolation_decision",
]
