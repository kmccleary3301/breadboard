from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agentic_coder_prototype.compilation.contracts import canonical_json_bytes, canonical_json_loads
from breadboard.rl.phase5.f5_fault_campaign import F5PinnedIdentity
from scripts.rl_phase5.run_f10_isolation_decision import (
    F10CleanupObservation,
    F10CommandObservation,
    F10DigitalOceanDecision,
    F10EnvironmentObservation,
    F10EpisodeCase,
    F10EpisodeRuntimeObservation,
    F10IdentityClosure,
    F10IsolationDecisionError,
    F10IsolationDecisionInput,
    F10IsolationDecisionReport,
    F10IsolationPolicy,
    F10ProbeCommand,
    F10RawArtifact,
    F10TargetIdentity,
    f10_container_name,
    f10_docker_argv,
    run_f10_isolation_decision,
)

_INPUT_NAME = "f10-isolation-decision.input.json"
_RAW_DIR_NAME = "f10-raw"
_APPROVED_QUESTION = (
    "Can the pinned CPU task execute with exact classification parity under runsc "
    "on this observed IBM target?"
)
_DIGITALOCEAN_INACTIVE_REASON = (
    "inactive: the observed IBM target directly answered the approved runsc "
    "portability question"
)
_INFEASIBLE = "runsc is absent or incompatible with the pinned Docker/image/runtime/task contract"
_MAX_OUTPUT_BYTES = 16 * 1024 * 1024
_PROBE_COMMANDS = (
    F10ProbeCommand(
        purpose="docker-runtimes",
        argv=("docker", "info", "--format", "{{json .Runtimes}}"),
    ),
    F10ProbeCommand(purpose="runsc-path", argv=("command", "-v", "runsc")),
    F10ProbeCommand(purpose="runsc-version", argv=("runsc", "--version")),
)
_CASES = (
    ("gold-1", "gold", "passed"),
    ("gold-2", "gold", "passed"),
    ("bad-1", "bad", "rejected"),
    ("bad-2", "bad", "rejected"),
    ("no-op-1", "no-op", "rejected"),
    ("no-op-2", "no-op", "rejected"),
)


class F10TargetAdapterError(RuntimeError):
    pass


class F10CommandTimeout(F10TargetAdapterError):
    pass


class F10OutputLimit(F10TargetAdapterError):
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
        raise ValueError("F10 target adapter requires a lowercase sha256 digest")
    return value


def _absolute(value: str) -> str:
    if type(value) is not str or not value.startswith("/") or os.path.normpath(value) != value:
        raise ValueError("F10 target adapter paths must be absolute and normalized")
    return value


def _write_all(descriptor: int, raw: bytes) -> None:
    view = memoryview(raw)
    written = 0
    while written < len(view):
        count = os.write(descriptor, view[written:])
        if count <= 0:
            raise OSError("short write while persisting F10 canonical bytes")
        written += count


@dataclass(frozen=True, slots=True)
class F10CommandResult:
    exit_code: int
    stdout: bytes
    stderr: bytes

    def __post_init__(self) -> None:
        if type(self.exit_code) is not int or not 0 <= self.exit_code <= 255:
            raise ValueError("command exit code must be an unsigned byte")
        if type(self.stdout) is not bytes or type(self.stderr) is not bytes:
            raise TypeError("command output must be raw bytes")
        if len(self.stdout) > _MAX_OUTPUT_BYTES or len(self.stderr) > _MAX_OUTPUT_BYTES:
            raise ValueError("command output exceeds the F10 evidence bound")


class F10CommandRunner(Protocol):
    async def run(self, argv: tuple[str, ...]) -> F10CommandResult: ...


class F10SubprocessCommandRunner:
    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        cleanup_timeout_seconds: float = 2.0,
        max_output_bytes: int = _MAX_OUTPUT_BYTES,
    ) -> None:
        if timeout_seconds <= 0 or cleanup_timeout_seconds <= 0:
            raise ValueError("F10 command and cleanup timeouts must be positive")
        if not 0 < max_output_bytes <= _MAX_OUTPUT_BYTES:
            raise ValueError("F10 command output bound is invalid")
        self._timeout_seconds = timeout_seconds
        self._cleanup_timeout_seconds = cleanup_timeout_seconds
        self._max_output_bytes = max_output_bytes

    async def _read_stream(
        self, stream: asyncio.StreamReader, label: str
    ) -> bytes:
        captured = bytearray()
        while True:
            remaining = self._max_output_bytes + 1 - len(captured)
            chunk = await stream.read(min(64 * 1024, remaining))
            if not chunk:
                return bytes(captured)
            captured.extend(chunk)
            if len(captured) > self._max_output_bytes:
                raise F10OutputLimit(
                    f"command {label} exceeds the F10 evidence bound"
                )

    async def _bounded_teardown(
        self,
        process: asyncio.subprocess.Process,
        tasks: tuple[asyncio.Task[bytes], asyncio.Task[bytes], asyncio.Task[int]],
    ) -> str | None:
        errors: list[str] = []
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as exc:
            errors.append(f"SIGTERM failed: {exc}")
        grace = self._cleanup_timeout_seconds / 2
        try:
            await asyncio.wait_for(asyncio.shield(process.wait()), timeout=grace)
        except asyncio.TimeoutError:
            pass
        except BaseException as exc:
            errors.append(f"TERM reap failed: {type(exc).__name__}")
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as exc:
            errors.append(f"SIGKILL failed: {exc}")
        try:
            await asyncio.wait_for(
                asyncio.shield(process.wait()),
                timeout=self._cleanup_timeout_seconds - grace,
            )
        except BaseException as exc:
            errors.append(f"KILL reap failed: {type(exc).__name__}")
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=self._cleanup_timeout_seconds,
            )
        except BaseException as exc:
            errors.append(f"pipe cleanup failed: {type(exc).__name__}")
            for task in tasks:
                if not task.done():
                    task.cancel()
            transport = getattr(process, "_transport", None)
            if transport is not None:
                transport.close()
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=self._cleanup_timeout_seconds,
                )
            except BaseException as drain_exc:
                errors.append(
                    f"cancelled pipe drain failed: {type(drain_exc).__name__}"
                )
        transport = getattr(process, "_transport", None)
        if transport is not None:
            transport.close()
            await asyncio.sleep(0)
        return "; ".join(errors) if errors else None

    async def run(self, argv: tuple[str, ...]) -> F10CommandResult:
        if not argv or any(not argument or "\x00" in argument for argument in argv):
            raise F10TargetAdapterError(
                "refusing an empty or NUL-containing command"
            )
        executable_argv = argv
        if argv == ("command", "-v", "runsc"):
            executable_argv = ("/bin/sh", "-c", "command -v runsc")
        try:
            process = await asyncio.create_subprocess_exec(
                *executable_argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            return F10CommandResult(
                exit_code=127,
                stdout=b"",
                stderr=(
                    f"{argv[0]}: executable not found: {exc}\n"
                ).encode("utf-8"),
            )
        assert process.stdout is not None and process.stderr is not None
        stdout_task = asyncio.create_task(
            self._read_stream(process.stdout, "stdout")
        )
        stderr_task = asyncio.create_task(
            self._read_stream(process.stderr, "stderr")
        )
        wait_task = asyncio.create_task(process.wait())
        tasks = (stdout_task, stderr_task, wait_task)
        combined = asyncio.gather(*tasks)
        try:
            stdout, stderr, returncode = await asyncio.wait_for(
                asyncio.shield(combined), timeout=self._timeout_seconds
            )
        except F10OutputLimit as exc:
            cleanup_error = await asyncio.shield(
                self._bounded_teardown(process, tasks)
            )
            if cleanup_error:
                exc.add_note(cleanup_error)
            raise
        except asyncio.TimeoutError as exc:
            cleanup_error = await asyncio.shield(
                self._bounded_teardown(process, tasks)
            )
            error = F10CommandTimeout(f"command timed out: {argv[0]}")
            if cleanup_error:
                error.add_note(cleanup_error)
            raise error from exc
        except asyncio.CancelledError as exc:
            cleanup_error = await asyncio.shield(
                self._bounded_teardown(process, tasks)
            )
            if cleanup_error:
                exc.add_note(cleanup_error)
            raise
        except BaseException:
            await asyncio.shield(self._bounded_teardown(process, tasks))
            raise
        return F10CommandResult(
            exit_code=(
                returncode
                if returncode >= 0
                else min(255, 128 + abs(returncode))
            ),
            stdout=stdout,
            stderr=stderr,
        )


class F10CaseAuthority(_ExactModel):
    case_id: str
    specimen: Literal["gold", "bad", "no-op"]
    expected_classification: Literal["passed", "rejected"]
    case_ref: F5PinnedIdentity
    specimen_ref: F5PinnedIdentity
    contract_join: F10IdentityClosure
    manifest_path: str
    expected_task_output_digest: str

    _expected_output = field_validator("expected_task_output_digest")(_digest)

    @model_validator(mode="after")
    def exact_manifest(self) -> "F10CaseAuthority":
        if self.manifest_path != f"/opt/breadboard/f10/cases/{self.case_id}.json":
            raise ValueError("F10 case authority manifest path is not exact")
        return self


class F10TargetAdapterAuthoringInput(_ExactModel):
    schema_version: Literal["bb.rl.phase5-f10-target-adapter-authoring-input.v2"]
    target: F10TargetIdentity
    identities: F10IdentityClosure
    case_authorities: tuple[
        F10CaseAuthority,
        F10CaseAuthority,
        F10CaseAuthority,
        F10CaseAuthority,
        F10CaseAuthority,
        F10CaseAuthority,
    ]
    output_dir: str

    _output = field_validator("output_dir")(_absolute)

    @model_validator(mode="after")
    def exact_authorities(self) -> "F10TargetAdapterAuthoringInput":
        if not self.identities.image.immutable_ref.startswith("docker://"):
            raise ValueError("F10 target image identity must be an exact docker:// content address")
        if not self.identities.runtime.immutable_ref.startswith("oci-runtime://runc@"):
            raise ValueError("F10 fallback runtime must be the pinned non-runsc runc authority")
        actual = tuple(
            (case.case_id, case.specimen, case.expected_classification)
            for case in self.case_authorities
        )
        if actual != _CASES:
            raise ValueError("F10 authoring requires the exact six frozen case authorities")
        if any(case.contract_join != self.identities for case in self.case_authorities):
            raise ValueError("F10 case authority identity closure mismatch")
        if len({case.case_ref.digest for case in self.case_authorities}) != 6 or len(
            {case.specimen_ref.digest for case in self.case_authorities}
        ) != 6:
            raise ValueError("F10 case and specimen authorities must be unique")
        return self


def author_f10_target_input(
    authoring: F10TargetAdapterAuthoringInput,
) -> F10IsolationDecisionInput:
    if type(authoring) is not F10TargetAdapterAuthoringInput:
        raise TypeError("authoring must be an exact F10TargetAdapterAuthoringInput")
    target_digest = hashlib.sha256(
        canonical_json_bytes(authoring.target.model_dump(mode="json"))
    ).hexdigest()[:16]
    cases = tuple(
        F10EpisodeCase(
            case_id=authority.case_id,
            specimen=authority.specimen,
            expected_classification=authority.expected_classification,
            episode_id=f"f10-{target_digest}-{authority.case_id}-episode",
            attempt_id=f"f10-{target_digest}-{authority.case_id}-attempt",
            case_ref=authority.case_ref,
            specimen_ref=authority.specimen_ref,
            contract_join=authority.contract_join,
            manifest_path=authority.manifest_path,
            expected_task_output_digest=authority.expected_task_output_digest,
        )
        for authority in authoring.case_authorities
    )
    return F10IsolationDecisionInput(
        schema_version="bb.rl.phase5-f10-isolation-decision-input.v2",
        target=authoring.target,
        identities=authoring.identities,
        approved_question=_APPROVED_QUESTION,
        expected_branch="incompatible",
        commands=_PROBE_COMMANDS,
        isolation=F10IsolationPolicy(
            network_mode="none",
            read_only_root=True,
            nonroot=True,
            uid=65532,
            gid=65532,
            cap_drop=("ALL",),
            no_new_privileges=True,
            docker_socket_mounted=False,
            single_tenant=True,
            hardened_oci_runtime="runc",
        ),
        cases=cases,
        digitalocean=F10DigitalOceanDecision(
            activated=False,
            reason=_DIGITALOCEAN_INACTIVE_REASON,
            approved_question=None,
            provider=None,
            droplet_id=None,
            region=None,
            network_ref=None,
            image_ref=None,
            runtime_ref=None,
            secret_source_ref=None,
            maximum_cost_usd=None,
            ttl_seconds=None,
            episode_evidence_ref=None,
            cleanup_ref=None,
            provider_teardown_ref=None,
        ),
        output_dir=authoring.output_dir,
    )


def write_f10_target_input(spec: F10IsolationDecisionInput) -> tuple[str, str]:
    if type(spec) is not F10IsolationDecisionInput:
        raise TypeError("spec must be an exact F10IsolationDecisionInput")
    root = Path(spec.output_dir)
    root.mkdir(mode=0o750, parents=False, exist_ok=True)
    path = root / _INPUT_NAME
    raw = canonical_json_bytes(spec.model_dump(mode="json"))
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o440,
    )
    try:
        _write_all(descriptor, raw)
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        path.unlink(missing_ok=True)
        raise
    else:
        os.close(descriptor)
    persisted = path.read_bytes()
    try:
        value = canonical_json_loads(persisted)
        validated = F10IsolationDecisionInput.model_validate_json(persisted, strict=True)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    if canonical_json_bytes(value) != persisted or persisted != raw or validated != spec:
        path.unlink(missing_ok=True)
        raise F10TargetAdapterError("persisted F10 input differs from canonical input bytes")
    directory = os.open(root, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return os.fspath(path.resolve()), _sha256(persisted)


def _single_line(raw: bytes) -> str | None:
    try:
        value = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    return value if value and "\n" not in value and "\r" not in value else None


def _runtime_names(result: F10CommandResult) -> tuple[str, ...]:
    if result.exit_code != 0:
        raise F10TargetAdapterError("Docker runtime-map probe failed")
    try:
        value = json.loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise F10TargetAdapterError("Docker runtime-map probe was not JSON") from exc
    if type(value) is not dict or any(type(name) is not str or not name for name in value):
        raise F10TargetAdapterError("Docker runtime-map probe was not an exact runtime map")
    return tuple(sorted(value))


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


class F10DockerTargetRuntime:
    def __init__(
        self,
        *,
        runner: F10CommandRunner,
        spec: F10IsolationDecisionInput,
        timeout_seconds: float = 35.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("F10 runtime timeout must be positive")
        self._runner = runner
        self._spec = spec
        self._timeout_seconds = timeout_seconds
        self._active_containers: set[str] = set()
        self._closed = False
        self._raw_root = Path(spec.output_dir) / _RAW_DIR_NAME
        self._raw_root.mkdir(mode=0o750, parents=False, exist_ok=False)
        self._artifact_counter = 0

    async def _run_bounded(self, argv: tuple[str, ...]) -> F10CommandResult:
        try:
            return await asyncio.wait_for(
                self._runner.run(argv), timeout=self._timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            raise F10CommandTimeout(f"command timed out: {argv[0]}") from exc

    def _write_raw(self, observation_id: str, stream: str, raw: bytes) -> F10RawArtifact:
        self._artifact_counter += 1
        stem = hashlib.sha256(observation_id.encode("utf-8")).hexdigest()[:16]
        path = self._raw_root / f"{self._artifact_counter:04d}-{stem}.{stream}"
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o440,
        )
        try:
            _write_all(descriptor, raw)
            os.fsync(descriptor)
        except BaseException:
            os.close(descriptor)
            path.unlink(missing_ok=True)
            raise
        else:
            os.close(descriptor)
        persisted = path.read_bytes()
        if persisted != raw:
            path.unlink(missing_ok=True)
            raise F10TargetAdapterError("persisted raw command evidence mismatch")
        return F10RawArtifact(
            path=os.fspath(path.resolve()), digest=_sha256(persisted), size=len(persisted)
        )

    def _observation(
        self, observation_id: str, argv: tuple[str, ...], result: F10CommandResult
    ) -> F10CommandObservation:
        return F10CommandObservation(
            schema_version="bb.rl.phase5-f10-command-observation.v2",
            observation_id=observation_id,
            argv=argv,
            exit_code=result.exit_code,
            stdout=self._write_raw(observation_id, "stdout", result.stdout),
            stderr=self._write_raw(observation_id, "stderr", result.stderr),
        )

    async def observe_environment(
        self, commands: tuple[F10ProbeCommand, F10ProbeCommand, F10ProbeCommand]
    ) -> F10EnvironmentObservation:
        if self._closed:
            raise F10TargetAdapterError("F10 target runtime is closed")
        if commands != _PROBE_COMMANDS:
            raise F10TargetAdapterError("F10 target runtime received a non-closed probe set")
        results = (
            await self._run_bounded(commands[0].argv),
            await self._run_bounded(commands[1].argv),
            await self._run_bounded(commands[2].argv),
        )
        probes = tuple(
            self._observation(command.purpose, command.argv, result)
            for command, result in zip(commands, results, strict=True)
        )
        runtimes = _runtime_names(results[0])
        runsc_path = _single_line(results[1].stdout) if results[1].exit_code == 0 else None
        if runsc_path is not None and (
            not runsc_path.startswith("/") or os.path.normpath(runsc_path) != runsc_path
        ):
            runsc_path = None
        runsc_version = _single_line(results[2].stdout) if results[2].exit_code == 0 else None
        installed = "runsc" in runtimes and runsc_path is not None and runsc_version is not None
        canary_observation: F10CommandObservation | None = None
        cleanup_observation: F10CommandObservation | None = None
        compatible = False
        if installed:
            gold = self._spec.cases[0]
            name = f10_container_name(self._spec.target, gold, suffix="runsc-canary")
            argv = f10_docker_argv(
                self._spec.target,
                gold,
                self._spec.identities,
                runtime_name="runsc",
                remove=True,
                suffix="runsc-canary",
            )
            self._active_containers.add(name)
            canary_result = await self._run_bounded(argv)
            canary_observation = self._observation(
                "runsc-effective-canary", argv, canary_result
            )
            cleanup_argv = _cleanup_probe_argv(name)
            cleanup_result = await self._run_bounded(cleanup_argv)
            cleanup_observation = self._observation(
                "runsc-canary-cleanup", cleanup_argv, cleanup_result
            )
            if cleanup_result.exit_code != 0 or cleanup_result.stdout != b"":
                raise F10TargetAdapterError("runsc canary cleanup did not prove absence")
            self._active_containers.remove(name)
            if canary_result.exit_code == 0:
                try:
                    value = canonical_json_loads(canary_result.stdout)
                    compatible = (
                        canonical_json_bytes(value) == canary_result.stdout
                        and value.get("classification") == "passed"
                        and value.get("runtime_name") == "runsc"
                    )
                except Exception:
                    compatible = False
        return F10EnvironmentObservation(
            docker_runtimes=runtimes,
            runsc_path=runsc_path,
            runsc_version=runsc_version,
            compatible=compatible,
            infeasibility=None if compatible else _INFEASIBLE,
            probes=probes,
            runsc_canary=canary_observation,
            runsc_canary_cleanup=cleanup_observation,
        )

    async def execute_episode(
        self,
        case: F10EpisodeCase,
        *,
        selected_runtime: Literal["runsc", "hardened-docker"],
        identities: F10IdentityClosure,
        isolation: F10IsolationPolicy,
    ) -> F10EpisodeRuntimeObservation:
        if self._closed:
            raise F10TargetAdapterError("F10 target runtime is closed")
        if identities != self._spec.identities or case.contract_join != identities:
            raise F10TargetAdapterError("F10 runtime identity closure mismatch")
        if isolation != self._spec.isolation:
            raise F10TargetAdapterError("F10 runtime isolation policy mismatch")
        expected_case = next(
            (candidate for candidate in self._spec.cases if candidate.case_id == case.case_id),
            None,
        )
        if expected_case != case:
            raise F10TargetAdapterError("F10 runtime case contract mismatch")
        runtime_name: Literal["runsc", "runc"] = (
            "runsc" if selected_runtime == "runsc" else isolation.hardened_oci_runtime
        )
        name = f10_container_name(self._spec.target, case)
        argv = f10_docker_argv(
            self._spec.target,
            case,
            identities,
            runtime_name=runtime_name,
            remove=False,
        )
        self._active_containers.add(name)
        result = await self._run_bounded(argv)
        execution = self._observation(f"case:{case.case_id}", argv, result)
        if result.exit_code != 0:
            raise F10TargetAdapterError(
                f"case {case.case_id} launcher or infrastructure failure: exit {result.exit_code}"
            )
        inspection_argv = (
            "docker",
            "inspect",
            "--format",
            "{{.HostConfig.Runtime}}",
            name,
        )
        inspection_result = await self._run_bounded(inspection_argv)
        inspection = self._observation(
            f"runtime:{case.case_id}", inspection_argv, inspection_result
        )
        remove_argv = ("docker", "rm", name)
        remove_result = await self._run_bounded(remove_argv)
        removal = self._observation(f"remove:{case.case_id}", remove_argv, remove_result)
        cleanup_argv = _cleanup_probe_argv(name)
        cleanup_result = await self._run_bounded(cleanup_argv)
        cleanup_probe = self._observation(
            f"cleanup:{case.case_id}", cleanup_argv, cleanup_result
        )
        if (
            inspection_result.exit_code != 0
            or _single_line(inspection_result.stdout) != runtime_name
            or remove_result.exit_code != 0
            or cleanup_result.exit_code != 0
            or cleanup_result.stdout != b""
        ):
            raise F10TargetAdapterError(
                f"case {case.case_id} runtime inspection or cleanup failed"
            )
        self._active_containers.remove(name)
        return F10EpisodeRuntimeObservation(
            case_id=case.case_id,
            episode_id=case.episode_id,
            attempt_id=case.attempt_id,
            selected_runtime=selected_runtime,
            terminal_state="closed",
            contract_join=identities,
            isolation=isolation,
            execution=execution,
            runtime_inspection=inspection,
            cleanup_remove=removal,
            cleanup_probe=cleanup_probe,
            cleanup=F10CleanupObservation(
                released=True,
                no_orphan=True,
                active_lease_ids=(),
                orphan_resource_ids=(),
                cleanup_errors=(),
            ),
        )

    async def close(self) -> None:
        if self._closed:
            return
        errors: list[str] = []
        for name in sorted(self._active_containers):
            try:
                removal = await self._run_bounded(("docker", "rm", "--force", name))
                if removal.exit_code != 0:
                    errors.append(f"{name}: forced removal exit {removal.exit_code}")
            except BaseException as exc:
                errors.append(f"{name}: forced removal error {type(exc).__name__}")
            try:
                probe = await self._run_bounded(_cleanup_probe_argv(name))
                if probe.exit_code == 0 and probe.stdout == b"":
                    self._active_containers.discard(name)
                else:
                    errors.append(f"{name}: absence not proven")
            except BaseException as exc:
                errors.append(f"{name}: absence probe error {type(exc).__name__}")
        self._closed = not self._active_containers
        if errors or self._active_containers:
            raise F10TargetAdapterError(
                "F10 cleanup incomplete: " + "; ".join(errors or sorted(self._active_containers))
            )


def run_f10_target_adapter(
    authoring: F10TargetAdapterAuthoringInput,
    *,
    runner: F10CommandRunner,
    timeout_seconds: float = 35.0,
) -> tuple[F10IsolationDecisionReport, str, str]:
    spec = author_f10_target_input(authoring)
    input_path, input_digest = write_f10_target_input(spec)
    runtime = F10DockerTargetRuntime(
        runner=runner,
        spec=spec,
        timeout_seconds=timeout_seconds,
    )
    report, report_path = run_f10_isolation_decision(
        spec,
        input_digest=input_digest,
        runtime=runtime,
    )
    return report, report_path, input_path


def _read_authoring_input(path: str) -> F10TargetAdapterAuthoringInput:
    raw = Path(path).resolve(strict=True).read_bytes()
    value = canonical_json_loads(raw)
    if canonical_json_bytes(value) != raw:
        raise F10IsolationDecisionError(
            "F10 target adapter authoring input is not canonical JSON"
        )
    return F10TargetAdapterAuthoringInput.model_validate_json(raw, strict=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Author and execute the source-closed F10 target adapter"
    )
    parser.add_argument("--input", required=True)
    arguments = parser.parse_args()
    run_f10_target_adapter(
        _read_authoring_input(arguments.input),
        runner=F10SubprocessCommandRunner(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "F10CaseAuthority",
    "F10CommandResult",
    "F10CommandRunner",
    "F10CommandTimeout",
    "F10OutputLimit",
    "F10DockerTargetRuntime",
    "F10SubprocessCommandRunner",
    "F10TargetAdapterAuthoringInput",
    "F10TargetAdapterError",
    "author_f10_target_input",
    "run_f10_target_adapter",
    "write_f10_target_input",
]
