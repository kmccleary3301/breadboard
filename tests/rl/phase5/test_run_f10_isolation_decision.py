from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from breadboard_engine.compilation.contracts import canonical_json_bytes
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
    F10IsolationPolicy,
    F10ProbeCommand,
    F10RawArtifact,
    F10TargetIdentity,
    f10_container_name,
    f10_docker_argv,
    run_f10_isolation_decision,
)

_EMPTY_DIGEST = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_CASES = (
    ("gold-1", "gold", "passed"),
    ("gold-2", "gold", "passed"),
    ("bad-1", "bad", "rejected"),
    ("bad-2", "bad", "rejected"),
    ("no-op-1", "no-op", "rejected"),
    ("no-op-2", "no-op", "rejected"),
)


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _input_digest(spec: F10IsolationDecisionInput) -> str:
    return _digest(canonical_json_bytes(spec.model_dump(mode="json")))


def _ref(label: str, *, scheme: str = "cas") -> F5PinnedIdentity:
    digest = _digest(label.encode("utf-8"))
    return F5PinnedIdentity(
        identity_id=label,
        digest=digest,
        immutable_ref=f"{scheme}://f10/{label}@{digest}",
    )


def _identities() -> F10IdentityClosure:
    return F10IdentityClosure(
        docker=_ref("docker", scheme="docker-engine"),
        image=_ref("image", scheme="docker"),
        runtime=_ref("runc", scheme="oci-runtime"),
        config=_ref("config"),
        task=_ref("task"),
        verifier=_ref("verifier"),
    )


def _spec(tmp_path: Path, branch: Literal["compatible", "incompatible"]) -> F10IsolationDecisionInput:
    identities = _identities()
    cases = tuple(
        F10EpisodeCase(
            case_id=case_id,
            specimen=specimen,
            expected_classification=classification,
            episode_id=f"episode-{case_id}",
            attempt_id=f"attempt-{case_id}",
            case_ref=_ref(f"case-{case_id}", scheme="case"),
            specimen_ref=_ref(f"specimen-{case_id}", scheme="specimen"),
            contract_join=identities,
            manifest_path=f"/opt/breadboard/f10/cases/{case_id}.json",
            expected_task_output_digest=_digest(f"expected-{case_id}".encode("utf-8")),
        )
        for case_id, specimen, classification in _CASES
    )
    return F10IsolationDecisionInput(
        schema_version="bb.rl.phase5-f10-isolation-decision-input.v2",
        target=F10TargetIdentity(
            target_run_id="target-run-10", job_id="job-10", node_id="node-10"
        ),
        identities=identities,
        approved_question="Can the pinned CPU task execute under runsc?",
        expected_branch=branch,
        commands=(
            F10ProbeCommand(
                purpose="docker-runtimes",
                argv=("docker", "info", "--format", "{{json .Runtimes}}"),
            ),
            F10ProbeCommand(purpose="runsc-path", argv=("command", "-v", "runsc")),
            F10ProbeCommand(purpose="runsc-version", argv=("runsc", "--version")),
        ),
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
            reason="provider activation was not requested",
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
        output_dir=str((tmp_path / f"f10-{branch}").resolve()),
    )


def _verifier_bytes(
    case: F10EpisodeCase,
    identities: F10IdentityClosure,
    runtime_name: Literal["runsc", "runc"],
    *,
    classification: str | None = None,
) -> bytes:
    observed = (
        case.expected_task_output_digest
        if case.specimen == "gold"
        else _digest(f"observed-{case.case_id}".encode("utf-8"))
    )
    if case.specimen == "no-op":
        observed = _EMPTY_DIGEST
    return canonical_json_bytes(
        {
            "schema_version": "bb.rl.phase5-f10-verifier-result.v1",
            "case_id": case.case_id,
            "episode_id": case.episode_id,
            "attempt_id": case.attempt_id,
            "specimen": case.specimen,
            "classification": classification or case.expected_classification,
            "reason_code": {
                "gold": "MATCH",
                "bad": "OUTPUT_MISMATCH",
                "no-op": "NO_OUTPUT",
            }[case.specimen],
            "runtime_name": runtime_name,
            "case_digest": case.case_ref.digest,
            "specimen_digest": case.specimen_ref.digest,
            "docker_digest": identities.docker.digest,
            "image_digest": identities.image.digest,
            "runtime_digest": identities.runtime.digest,
            "config_digest": identities.config.digest,
            "task_digest": identities.task.digest,
            "verifier_digest": identities.verifier.digest,
            "observed_task_output_digest": observed,
        }
    )


class PersistedRuntime:
    def __init__(
        self,
        spec: F10IsolationDecisionInput,
        *,
        fabricated_hash: bool = False,
        tamper_probe: bool = False,
        verifier_mutation: str | None = None,
        launcher_exit: int | None = None,
        close_mutation: Literal["tamper", "delete"] | None = None,
    ) -> None:
        self.spec = spec
        self.fabricated_hash = fabricated_hash
        self.tamper_probe = tamper_probe
        self.verifier_mutation = verifier_mutation
        self.launcher_exit = launcher_exit
        self.close_mutation = close_mutation
        self.closed = False
        self.counter = 0
        Path(spec.output_dir).mkdir(mode=0o750)
        self.raw_root = Path(spec.output_dir) / "runtime-raw"
        self.raw_root.mkdir(mode=0o750)
        self.first_evidence_path: Path | None = None

    def _artifact(self, label: str, raw: bytes) -> F10RawArtifact:
        self.counter += 1
        path = self.raw_root / f"{self.counter:03d}-{label}"
        if self.fabricated_hash and label == "docker-runtimes.stdout":
            return F10RawArtifact(
                path=str((self.raw_root / "missing.stdout").resolve()),
                digest=_digest(raw),
                size=len(raw),
            )
        path.write_bytes(raw)
        if self.first_evidence_path is None:
            self.first_evidence_path = path
        reference = F10RawArtifact(
            path=str(path.resolve()), digest=_digest(raw), size=len(raw)
        )
        if self.tamper_probe and label == "docker-runtimes.stdout":
            path.write_bytes(raw + b"tamper")
        return reference

    def _observation(
        self,
        observation_id: str,
        argv: tuple[str, ...],
        exit_code: int,
        stdout: bytes,
        stderr: bytes = b"",
    ) -> F10CommandObservation:
        return F10CommandObservation(
            schema_version="bb.rl.phase5-f10-command-observation.v2",
            observation_id=observation_id,
            argv=argv,
            exit_code=exit_code,
            stdout=self._artifact(f"{observation_id}.stdout", stdout),
            stderr=self._artifact(f"{observation_id}.stderr", stderr),
        )

    async def observe_environment(self, commands: tuple[F10ProbeCommand, ...]) -> F10EnvironmentObservation:
        compatible = self.spec.expected_branch == "compatible"
        runtime_map = {"runc": {"path": "runc"}}
        if compatible:
            runtime_map["runsc"] = {"path": "/usr/local/bin/runsc"}
        probes = (
            self._observation(commands[0].purpose, commands[0].argv, 0, canonical_json_bytes(runtime_map)),
            self._observation(
                commands[1].purpose,
                commands[1].argv,
                0 if compatible else 1,
                b"/usr/local/bin/runsc\n" if compatible else b"",
            ),
            self._observation(
                commands[2].purpose,
                commands[2].argv,
                0 if compatible else 127,
                b"runsc version test\n" if compatible else b"",
            ),
        )
        canary = None
        canary_cleanup = None
        if compatible:
            case = self.spec.cases[0]
            name = f10_container_name(self.spec.target, case, suffix="runsc-canary")
            argv = f10_docker_argv(
                self.spec.target,
                case,
                self.spec.identities,
                runtime_name="runsc",
                remove=True,
                suffix="runsc-canary",
            )
            canary = self._observation(
                "runsc-effective-canary",
                argv,
                0,
                _verifier_bytes(case, self.spec.identities, "runsc"),
            )
            cleanup_argv = (
                "docker",
                "container",
                "ls",
                "--all",
                "--quiet",
                "--filter",
                f"name=^/{name}$",
            )
            canary_cleanup = self._observation(
                "runsc-canary-cleanup", cleanup_argv, 0, b""
            )
        return F10EnvironmentObservation(
            docker_runtimes=tuple(sorted(runtime_map)),
            runsc_path="/usr/local/bin/runsc" if compatible else None,
            runsc_version="runsc version test" if compatible else None,
            compatible=compatible,
            infeasibility=(
                None
                if compatible
                else "runsc is absent or incompatible with the pinned Docker/image/runtime/task contract"
            ),
            probes=probes,
            runsc_canary=canary,
            runsc_canary_cleanup=canary_cleanup,
        )

    async def execute_episode(
        self,
        case: F10EpisodeCase,
        *,
        selected_runtime: Literal["runsc", "hardened-docker"],
        identities: F10IdentityClosure,
        isolation: F10IsolationPolicy,
    ) -> F10EpisodeRuntimeObservation:
        runtime_name: Literal["runsc", "runc"] = (
            "runsc" if selected_runtime == "runsc" else "runc"
        )
        name = f10_container_name(self.spec.target, case)
        stdout = _verifier_bytes(case, identities, runtime_name)
        if self.verifier_mutation == case.case_id:
            stdout = _verifier_bytes(
                case,
                identities,
                runtime_name,
                classification="passed" if case.expected_classification == "rejected" else "rejected",
            )
        execution = self._observation(
            f"case:{case.case_id}",
            f10_docker_argv(
                self.spec.target,
                case,
                identities,
                runtime_name=runtime_name,
                remove=False,
            ),
            self.launcher_exit if self.launcher_exit and case.specimen != "gold" else 0,
            stdout,
            b"launcher failed\n" if self.launcher_exit and case.specimen != "gold" else b"",
        )
        inspection_argv = (
            "docker",
            "inspect",
            "--format",
            "{{.HostConfig.Runtime}}",
            name,
        )
        removal_argv = ("docker", "rm", name)
        cleanup_argv = (
            "docker",
            "container",
            "ls",
            "--all",
            "--quiet",
            "--filter",
            f"name=^/{name}$",
        )
        return F10EpisodeRuntimeObservation(
            case_id=case.case_id,
            episode_id=case.episode_id,
            attempt_id=case.attempt_id,
            selected_runtime=selected_runtime,
            terminal_state="closed",
            contract_join=identities,
            isolation=isolation,
            execution=execution,
            runtime_inspection=self._observation(
                f"runtime:{case.case_id}", inspection_argv, 0, runtime_name.encode() + b"\n"
            ),
            cleanup_remove=self._observation(
                f"remove:{case.case_id}", removal_argv, 0, name.encode() + b"\n"
            ),
            cleanup_probe=self._observation(
                f"cleanup:{case.case_id}", cleanup_argv, 0, b""
            ),
            cleanup=F10CleanupObservation(
                released=True,
                no_orphan=True,
                active_lease_ids=(),
                orphan_resource_ids=(),
                cleanup_errors=(),
            ),
        )

    async def close(self) -> None:
        self.closed = True
        if self.close_mutation == "tamper" and self.first_evidence_path is not None:
            self.first_evidence_path.write_bytes(b"tampered after close")
        elif self.close_mutation == "delete" and self.first_evidence_path is not None:
            self.first_evidence_path.unlink()


@pytest.mark.parametrize(
    ("branch", "selected"),
    [("compatible", "runsc"), ("incompatible", "hardened-docker")],
)
def test_canonical_gate_derives_all_outcomes_from_persisted_raw_bytes(
    tmp_path: Path,
    branch: Literal["compatible", "incompatible"],
    selected: str,
    capfd: pytest.CaptureFixture[str],
) -> None:
    spec = _spec(tmp_path, branch)
    runtime = PersistedRuntime(spec)

    report, path = run_f10_isolation_decision(
        spec, input_digest=_input_digest(spec), runtime=runtime
    )

    assert runtime.closed
    assert all(case.selected_runtime == selected for case in report.cases)
    assert [(case.case_id, case.classification) for case in report.cases] == [
        (case_id, classification) for case_id, _, classification in _CASES
    ]
    assert all(Path(case.execution.stdout.path).is_file() for case in report.cases)
    raw = Path(path).read_bytes()
    assert raw == canonical_json_bytes(json.loads(raw))
    line = capfd.readouterr().out.encode()
    assert line.startswith(b"PHASE3_COMPONENT_REPORT_JSON=")
    envelope = json.loads(line.split(b"=", 1)[1])
    assert envelope["report_sha256"] == _digest(raw)
    assert envelope["passed"] is True


@pytest.mark.parametrize("fabricated_hash,tamper_probe", [(True, False), (False, True)])
def test_probe_declarations_without_matching_persisted_raw_bytes_fail_closed(
    tmp_path: Path,
    fabricated_hash: bool,
    tamper_probe: bool,
    capfd: pytest.CaptureFixture[str],
) -> None:
    spec = _spec(tmp_path, "incompatible")
    runtime = PersistedRuntime(
        spec, fabricated_hash=fabricated_hash, tamper_probe=tamper_probe
    )

    with pytest.raises((F10IsolationDecisionError, FileNotFoundError)):
        run_f10_isolation_decision(
            spec, input_digest=_input_digest(spec), runtime=runtime
        )

    assert runtime.closed
    assert "PHASE3_COMPONENT_REPORT_JSON=" not in capfd.readouterr().out


def test_verifier_classification_mismatch_fails_closed(tmp_path: Path) -> None:
    spec = _spec(tmp_path, "incompatible")

    with pytest.raises(F10IsolationDecisionError, match="frozen case contract"):
        run_f10_isolation_decision(
            spec,
            input_digest=_input_digest(spec),
            runtime=PersistedRuntime(spec, verifier_mutation="bad-1"),
        )


@pytest.mark.parametrize("exit_code", [125, 126, 127])
def test_negative_launcher_failure_is_not_verifier_rejection(
    tmp_path: Path, exit_code: int
) -> None:
    spec = _spec(tmp_path, "incompatible")

    with pytest.raises(F10IsolationDecisionError, match="launcher or infrastructure"):
        run_f10_isolation_decision(
            spec,
            input_digest=_input_digest(spec),
            runtime=PersistedRuntime(spec, launcher_exit=exit_code),
        )


def test_unrelated_input_digest_fails_before_runtime_execution(tmp_path: Path) -> None:
    spec = _spec(tmp_path, "incompatible")
    runtime = PersistedRuntime(spec)

    with pytest.raises(F10IsolationDecisionError, match="exact canonical input bytes"):
        run_f10_isolation_decision(
            spec,
            input_digest=_digest(b"unrelated input"),
            runtime=runtime,
        )

    assert runtime.closed is False


@pytest.mark.parametrize("close_mutation", ["tamper", "delete"])
def test_runtime_close_cannot_change_validated_raw_evidence(
    tmp_path: Path,
    close_mutation: Literal["tamper", "delete"],
    capfd: pytest.CaptureFixture[str],
) -> None:
    spec = _spec(tmp_path, "incompatible")
    runtime = PersistedRuntime(spec, close_mutation=close_mutation)

    with pytest.raises((F10IsolationDecisionError, FileNotFoundError)):
        run_f10_isolation_decision(
            spec,
            input_digest=_input_digest(spec),
            runtime=runtime,
        )

    assert runtime.closed
    assert "PHASE3_COMPONENT_REPORT_JSON=" not in capfd.readouterr().out


def test_case_identity_closure_mismatch_is_rejected(tmp_path: Path) -> None:
    payload = _spec(tmp_path, "incompatible").model_dump(mode="json")
    payload["cases"][0]["contract_join"] = _identities().model_copy(
        update={"task": _ref("wrong-task")}
    ).model_dump(mode="json")

    with pytest.raises(ValidationError, match="identity closure mismatch"):
        F10IsolationDecisionInput.model_validate_json(
            canonical_json_bytes(payload), strict=True
        )


def test_inactive_digitalocean_forbids_provider_details() -> None:
    decision = _spec(Path("/tmp"), "incompatible").digitalocean.model_dump(mode="json")
    decision["provider"] = "digitalocean"

    with pytest.raises(ValidationError, match="inactive DigitalOcean"):
        F10DigitalOceanDecision.model_validate(decision, strict=True)
