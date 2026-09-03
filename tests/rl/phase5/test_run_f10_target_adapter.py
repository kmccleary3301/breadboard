from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from breadboard_engine.compilation.contracts import canonical_json_bytes
from breadboard.rl.phase5.f5_fault_campaign import F5PinnedIdentity
from scripts.rl_phase5 import run_f10_target_adapter as adapter_module
from scripts.rl_phase5.run_f10_isolation_decision import (
    F10IdentityClosure,
    F10IsolationDecisionError,
    F10IsolationDecisionInput,
    F10TargetIdentity,
    f10_container_name,
)
from scripts.rl_phase5.run_f10_target_adapter import (
    F10CaseAuthority,
    F10CommandResult,
    F10CommandTimeout,
    F10OutputLimit,
    F10DockerTargetRuntime,
    F10SubprocessCommandRunner,
    F10TargetAdapterAuthoringInput,
    F10TargetAdapterError,
    author_f10_target_input,
    run_f10_target_adapter,
    write_f10_target_input,
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
_NEGATIVE_CASES = ("bad-1", "bad-2", "no-op-1", "no-op-2")


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _identity(label: str, *, scheme: str = "cas", exact_runtime: bool = False) -> F5PinnedIdentity:
    digest = _digest(label.encode("utf-8"))
    if exact_runtime:
        immutable_ref = f"oci-runtime://runc@{digest}"
    elif scheme == "docker":
        immutable_ref = f"docker://registry.example/breadboard-f10@{digest}"
    else:
        immutable_ref = f"{scheme}://f10/{label}@{digest}"
    return F5PinnedIdentity(identity_id=label, digest=digest, immutable_ref=immutable_ref)


def _identities() -> F10IdentityClosure:
    return F10IdentityClosure(
        docker=_identity("docker", scheme="docker-engine"),
        image=_identity("image", scheme="docker"),
        runtime=_identity("runc", exact_runtime=True),
        config=_identity("config"),
        task=_identity("task"),
        verifier=_identity("verifier"),
    )


def _authoring(tmp_path: Path) -> F10TargetAdapterAuthoringInput:
    identities = _identities()
    authorities = tuple(
        F10CaseAuthority(
            case_id=case_id,
            specimen=specimen,
            expected_classification=classification,
            case_ref=_identity(f"case-{case_id}", scheme="case"),
            specimen_ref=_identity(f"specimen-{case_id}", scheme="specimen"),
            contract_join=identities,
            manifest_path=f"/opt/breadboard/f10/cases/{case_id}.json",
            expected_task_output_digest=_digest(
                f"expected-{case_id}".encode("utf-8")
            ),
        )
        for case_id, specimen, classification in _CASES
    )
    return F10TargetAdapterAuthoringInput(
        schema_version="bb.rl.phase5-f10-target-adapter-authoring-input.v2",
        target=F10TargetIdentity(
            target_run_id="f10-runsc-decision-20260714t022839z",
            job_id="272565",
            node_id="cnode-12",
        ),
        identities=identities,
        case_authorities=authorities,
        output_dir=str((tmp_path / "f10-target").resolve()),
    )


def _case_id(argv: tuple[str, ...]) -> str:
    prefix = "breadboard.f10.case_id="
    return next(argument[len(prefix) :] for argument in argv if argument.startswith(prefix))


def _argument(argv: tuple[str, ...], name: str) -> str:
    return argv[argv.index(name) + 1]


def _verifier_bytes(
    spec: F10IsolationDecisionInput,
    case_id: str,
    runtime_name: Literal["runsc", "runc"],
    *,
    classification: str | None = None,
    reason_code: str | None = None,
    observed_digest: str | None = None,
    malformed: bool = False,
) -> bytes:
    if malformed:
        return b"not canonical verifier json\n"
    case = next(candidate for candidate in spec.cases if candidate.case_id == case_id)
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
            "reason_code": reason_code
            or {
                "gold": "MATCH",
                "bad": "OUTPUT_MISMATCH",
                "no-op": "NO_OUTPUT",
            }[case.specimen],
            "runtime_name": runtime_name,
            "case_digest": case.case_ref.digest,
            "specimen_digest": case.specimen_ref.digest,
            "docker_digest": spec.identities.docker.digest,
            "image_digest": spec.identities.image.digest,
            "runtime_digest": spec.identities.runtime.digest,
            "config_digest": spec.identities.config.digest,
            "task_digest": spec.identities.task.digest,
            "verifier_digest": spec.identities.verifier.digest,
            "observed_task_output_digest": observed_digest or observed,
        }
    )


class RecordingRunner:
    def __init__(
        self,
        spec: F10IsolationDecisionInput,
        *,
        registered_runsc: bool = False,
        broken_canary: bool = False,
        incompatible_canary: bool = False,
        fail_case: str | None = None,
        fail_exit: int = 125,
        malformed_case: str | None = None,
        timeout_case: str | None = None,
        cancel_case: str | None = None,
        raise_cases: tuple[str, ...] = (),
        forced_rm_exception: bool = False,
        forced_rm_exit: int = 0,
    ) -> None:
        self.spec = spec
        self.registered_runsc = registered_runsc
        self.broken_canary = broken_canary
        self.incompatible_canary = incompatible_canary
        self.fail_case = fail_case
        self.fail_exit = fail_exit
        self.malformed_case = malformed_case
        self.timeout_case = timeout_case
        self.cancel_case = cancel_case
        self.raise_cases = set(raise_cases)
        self.forced_rm_exception = forced_rm_exception
        self.forced_rm_exit = forced_rm_exit
        self.calls: list[tuple[str, ...]] = []
        self.results: list[F10CommandResult] = []
        self.runtime_by_name: dict[str, str] = {}

    async def run(self, argv: tuple[str, ...]) -> F10CommandResult:
        self.calls.append(argv)
        if argv == ("docker", "info", "--format", "{{json .Runtimes}}"):
            runtimes = {"runc": {"path": "runc"}}
            if self.registered_runsc:
                runtimes["runsc"] = {"path": "/usr/local/bin/runsc"}
            result = F10CommandResult(0, canonical_json_bytes(runtimes), b"")
        elif argv == ("command", "-v", "runsc"):
            result = (
                F10CommandResult(0, b"/usr/local/bin/runsc\n", b"")
                if self.registered_runsc
                else F10CommandResult(1, b"", b"")
            )
        elif argv == ("runsc", "--version"):
            result = (
                F10CommandResult(0, b"runsc version release-test\n", b"")
                if self.registered_runsc
                else F10CommandResult(127, b"", b"runsc: not found\n")
            )
        elif argv[:2] == ("docker", "run"):
            case_id = _case_id(argv)
            runtime_name = _argument(argv, "--runtime")
            name = _argument(argv, "--name")
            is_canary = "runsc-canary" in name
            self.runtime_by_name[name] = runtime_name
            if case_id in self.raise_cases and not is_canary:
                raise RuntimeError(f"runner failure for {case_id}")
            if self.timeout_case == case_id and not is_canary:
                await asyncio.sleep(60)
            if self.cancel_case == case_id and not is_canary:
                raise asyncio.CancelledError()
            if is_canary and self.broken_canary:
                result = F10CommandResult(125, b"", b"runsc launch failed\n")
            elif is_canary and self.incompatible_canary:
                result = F10CommandResult(
                    0,
                    _verifier_bytes(
                        self.spec,
                        case_id,
                        "runsc",
                        classification="rejected",
                        reason_code="OUTPUT_MISMATCH",
                        observed_digest=_digest(b"runsc-task-incompatible"),
                    ),
                    b"",
                )
            elif self.fail_case == case_id and not is_canary:
                result = F10CommandResult(
                    self.fail_exit,
                    b"",
                    b"docker: image pull or launcher failure\n",
                )
            else:
                result = F10CommandResult(
                    0,
                    _verifier_bytes(
                        self.spec,
                        case_id,
                        runtime_name,  # type: ignore[arg-type]
                        malformed=self.malformed_case == case_id and not is_canary,
                    ),
                    b"",
                )
        elif argv[:2] == ("docker", "inspect"):
            result = F10CommandResult(
                0, self.runtime_by_name[argv[-1]].encode("utf-8") + b"\n", b""
            )
        elif argv[:3] == ("docker", "rm", "--force"):
            if self.forced_rm_exception:
                raise RuntimeError("forced rm transport failure")
            result = F10CommandResult(
                self.forced_rm_exit,
                argv[-1].encode("utf-8") + b"\n",
                b"forced removal failed\n" if self.forced_rm_exit else b"",
            )
        elif argv[:2] == ("docker", "rm"):
            result = F10CommandResult(0, argv[-1].encode("utf-8") + b"\n", b"")
        elif argv[:4] == ("docker", "container", "ls", "--all"):
            result = F10CommandResult(0, b"", b"")
        else:
            raise AssertionError(f"unexpected command: {argv!r}")
        self.results.append(result)
        return result


def _assert_sequence(argv: tuple[str, ...], sequence: tuple[str, ...]) -> None:
    assert any(argv[index : index + len(sequence)] == sequence for index in range(len(argv)))


def test_incompatible_target_runs_frozen_cases_through_real_verifier_contract(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    authoring = _authoring(tmp_path)
    spec = author_f10_target_input(authoring)
    runner = RecordingRunner(spec)

    report, report_path, input_path = run_f10_target_adapter(authoring, runner=runner)

    input_raw = Path(input_path).read_bytes()
    report_raw = Path(report_path).read_bytes()
    assert input_raw == canonical_json_bytes(json.loads(input_raw))
    assert report_raw == canonical_json_bytes(json.loads(report_raw))
    assert report.input_digest == _digest(input_raw)
    assert report.branch == "incompatible"
    assert report.environment.compatible is False
    assert report.environment.runsc_canary is None
    assert report.digitalocean.activated is False
    assert report.raw_artifact_snapshot
    assert all(
        Path(artifact.path).is_file()
        for artifact in report.raw_artifact_snapshot
    )
    assert [(case.case_id, case.classification) for case in report.cases] == [
        (case_id, classification) for case_id, _, classification in _CASES
    ]
    assert all(case.verifier_result.runtime_name == "runc" for case in report.cases)
    assert all(case.contract_join == authoring.identities for case in report.cases)
    assert all(case.cleanup.released and case.cleanup.no_orphan for case in report.cases)
    assert all(case.cleanup.active_lease_ids == () for case in report.cases)
    assert all(case.cleanup.orphan_resource_ids == () for case in report.cases)
    assert all(case.cleanup.cleanup_errors == () for case in report.cases)

    run_calls = [argv for argv in runner.calls if argv[:2] == ("docker", "run")]
    assert len(run_calls) == 6
    for argv in run_calls:
        _assert_sequence(argv, ("--network", "none"))
        _assert_sequence(argv, ("--read-only",))
        _assert_sequence(argv, ("--cap-drop", "ALL"))
        _assert_sequence(argv, ("--security-opt", "no-new-privileges:true"))
        _assert_sequence(argv, ("--user", "65532:65532"))
        _assert_sequence(argv, ("--runtime", "runc"))
        assert "--rm" not in argv
        assert "-v" not in argv and "--volume" not in argv and "--mount" not in argv
        assert "/opt/breadboard/f10/verifier" in argv
        assert "--case-manifest" in argv
        for flag, identity in (
            ("--docker-digest", spec.identities.docker),
            ("--image-digest", spec.identities.image),
            ("--runtime-digest", spec.identities.runtime),
            ("--config-digest", spec.identities.config),
            ("--task-digest", spec.identities.task),
            ("--verifier-digest", spec.identities.verifier),
        ):
            assert _argument(argv, flag) == identity.digest

    for case in report.cases:
        assert Path(case.execution.stdout.path).read_bytes() == canonical_json_bytes(
            case.verifier_result.model_dump(mode="json")
        )
        assert case.execution.stdout.digest == _digest(
            Path(case.execution.stdout.path).read_bytes()
        )
        assert Path(case.runtime_inspection.stdout.path).read_bytes() == b"runc\n"
        assert Path(case.cleanup_probe.stdout.path).read_bytes() == b""

    line = capfd.readouterr().out.encode("utf-8")
    prefix = b"PHASE3_COMPONENT_REPORT_JSON="
    assert line.startswith(prefix)
    envelope = json.loads(line[len(prefix) :])
    assert envelope["report_path"] == report_path
    assert envelope["report_sha256"] == _digest(report_raw)
    assert envelope["passed"] is True
    assert envelope["promotion_authority"] is False
    assert envelope["scorecard_update_allowed"] is False


def test_registered_broken_runsc_uses_explicit_observed_runc_fallback(tmp_path: Path) -> None:
    authoring = _authoring(tmp_path)
    spec = author_f10_target_input(authoring)
    runner = RecordingRunner(spec, registered_runsc=True, broken_canary=True)

    report, _, _ = run_f10_target_adapter(authoring, runner=runner)

    assert report.branch == "incompatible"
    assert report.environment.runsc_canary is not None
    assert report.environment.runsc_canary.exit_code == 125
    episode_runs = [
        argv
        for argv in runner.calls
        if argv[:2] == ("docker", "run") and "runsc-canary" not in _argument(argv, "--name")
    ]
    assert len(episode_runs) == 6
    assert all(_argument(argv, "--runtime") == "runc" for argv in episode_runs)
    assert all(case.verifier_result.runtime_name == "runc" for case in report.cases)


def test_task_incompatible_runsc_canary_uses_explicit_runc_fallback(
    tmp_path: Path,
) -> None:
    authoring = _authoring(tmp_path)
    spec = author_f10_target_input(authoring)
    runner = RecordingRunner(
        spec, registered_runsc=True, incompatible_canary=True
    )

    report, _, _ = run_f10_target_adapter(authoring, runner=runner)

    assert report.branch == "incompatible"
    assert report.environment.compatible is False
    assert report.environment.runsc_canary is not None
    episode_runs = [
        argv
        for argv in runner.calls
        if argv[:2] == ("docker", "run")
        and "runsc-canary" not in _argument(argv, "--name")
    ]
    assert len(episode_runs) == 6
    assert all(_argument(argv, "--runtime") == "runc" for argv in episode_runs)
    assert all(
        case.verifier_result.runtime_name == "runc"
        for case in report.cases
    )


@pytest.mark.parametrize("case_id", _NEGATIVE_CASES)
@pytest.mark.parametrize("exit_code", [125, 126, 127])
def test_negative_docker_launcher_failures_never_count_as_rejection(
    tmp_path: Path, case_id: str, exit_code: int, capfd: pytest.CaptureFixture[str]
) -> None:
    authoring = _authoring(tmp_path)
    spec = author_f10_target_input(authoring)
    runner = RecordingRunner(spec, fail_case=case_id, fail_exit=exit_code)

    with pytest.raises(F10TargetAdapterError, match="launcher or infrastructure"):
        run_f10_target_adapter(authoring, runner=runner)

    assert any(argv[:3] == ("docker", "rm", "--force") for argv in runner.calls)
    assert "PHASE3_COMPONENT_REPORT_JSON=" not in capfd.readouterr().out


@pytest.mark.parametrize("case_id", _NEGATIVE_CASES)
def test_malformed_negative_verifier_output_fails_closed(
    tmp_path: Path, case_id: str
) -> None:
    authoring = _authoring(tmp_path)
    spec = author_f10_target_input(authoring)

    with pytest.raises(F10IsolationDecisionError, match="verifier output"):
        run_f10_target_adapter(
            authoring,
            runner=RecordingRunner(spec, malformed_case=case_id),
        )


@pytest.mark.parametrize("case_id", _NEGATIVE_CASES)
def test_negative_case_timeout_is_not_rejection(tmp_path: Path, case_id: str) -> None:
    authoring = _authoring(tmp_path)
    spec = author_f10_target_input(authoring)
    runner = RecordingRunner(spec, timeout_case=case_id)

    with pytest.raises(F10CommandTimeout):
        run_f10_target_adapter(
            authoring,
            runner=runner,
            timeout_seconds=0.001,
        )
    assert any(argv[:3] == ("docker", "rm", "--force") for argv in runner.calls)
    assert any(
        argv[:4] == ("docker", "container", "ls", "--all")
        for argv in runner.calls
    )


@pytest.mark.parametrize("case_id", _NEGATIVE_CASES)
def test_negative_case_cancellation_is_not_rejection(tmp_path: Path, case_id: str) -> None:
    authoring = _authoring(tmp_path)
    spec = author_f10_target_input(authoring)
    runner = RecordingRunner(spec, cancel_case=case_id)

    with pytest.raises(asyncio.CancelledError):
        run_f10_target_adapter(
            authoring,
            runner=runner,
        )
    assert any(argv[:3] == ("docker", "rm", "--force") for argv in runner.calls)
    assert any(
        argv[:4] == ("docker", "container", "ls", "--all")
        for argv in runner.calls
    )


@pytest.mark.parametrize("role", ["docker", "image", "runtime", "config", "task", "verifier"])
def test_each_identity_mismatch_against_frozen_case_authorities_is_rejected(
    tmp_path: Path, role: str
) -> None:
    payload = _authoring(tmp_path).model_dump(mode="json")
    replacement = _identity(
        f"wrong-{role}",
        scheme="docker" if role == "image" else "cas",
        exact_runtime=role == "runtime",
    )
    payload["identities"][role] = replacement.model_dump(mode="json")

    with pytest.raises(ValidationError, match="identity closure mismatch"):
        F10TargetAdapterAuthoringInput.model_validate_json(
            canonical_json_bytes(payload), strict=True
        )


def test_noncanonical_case_manifest_is_rejected_before_execution(tmp_path: Path) -> None:
    payload = _authoring(tmp_path).model_dump(mode="json")
    payload["case_authorities"][0]["manifest_path"] = "/tmp/gold-1.json"

    with pytest.raises(ValidationError, match="manifest path is not exact"):
        F10TargetAdapterAuthoringInput.model_validate_json(
            canonical_json_bytes(payload), strict=True
        )


def test_short_input_write_removes_partial_file_and_emits_no_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    spec = author_f10_target_input(_authoring(tmp_path))
    real_write = os.write
    calls = 0

    def short_then_stall(descriptor: int, data: bytes | memoryview) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raw = bytes(data)
            return real_write(descriptor, raw[: max(1, len(raw) // 2)])
        return 0

    monkeypatch.setattr(adapter_module.os, "write", short_then_stall)

    with pytest.raises(OSError, match="short write"):
        write_f10_target_input(spec)

    assert not (Path(spec.output_dir) / "f10-isolation-decision.input.json").exists()
    assert "PHASE3_COMPONENT_REPORT_JSON=" not in capfd.readouterr().out


def test_close_attempts_every_active_container_after_runner_exceptions(tmp_path: Path) -> None:
    authoring = _authoring(tmp_path)
    spec = author_f10_target_input(authoring)
    Path(spec.output_dir).mkdir(mode=0o750)
    runner = RecordingRunner(
        spec,
        raise_cases=("gold-1", "gold-2"),
        forced_rm_exception=True,
    )
    runtime = F10DockerTargetRuntime(runner=runner, spec=spec, timeout_seconds=0.01)

    async def exercise() -> None:
        for case in spec.cases[:2]:
            with pytest.raises(RuntimeError, match="runner failure"):
                await runtime.execute_episode(
                    case,
                    selected_runtime="hardened-docker",
                    identities=spec.identities,
                    isolation=spec.isolation,
                )
        with pytest.raises(F10TargetAdapterError, match="cleanup incomplete"):
            await runtime.close()

    asyncio.run(exercise())

    forced = [argv for argv in runner.calls if argv[:3] == ("docker", "rm", "--force")]
    probes = [argv for argv in runner.calls if argv[:4] == ("docker", "container", "ls", "--all")]
    assert len(forced) == 2
    assert len(probes) == 2
    assert {argv[-1] for argv in forced} == {
        f10_container_name(spec.target, spec.cases[0]),
        f10_container_name(spec.target, spec.cases[1]),
    }


def test_close_records_nonzero_removal_and_still_probes_every_name(
    tmp_path: Path,
) -> None:
    authoring = _authoring(tmp_path)
    spec = author_f10_target_input(authoring)
    Path(spec.output_dir).mkdir(mode=0o750)
    runner = RecordingRunner(
        spec,
        raise_cases=("gold-1", "gold-2"),
        forced_rm_exit=1,
    )
    runtime = F10DockerTargetRuntime(
        runner=runner, spec=spec, timeout_seconds=0.01
    )

    async def exercise() -> None:
        for case in spec.cases[:2]:
            with pytest.raises(RuntimeError, match="runner failure"):
                await runtime.execute_episode(
                    case,
                    selected_runtime="hardened-docker",
                    identities=spec.identities,
                    isolation=spec.isolation,
                )
        with pytest.raises(
            F10TargetAdapterError, match="forced removal exit 1"
        ):
            await runtime.close()

    asyncio.run(exercise())

    forced = [
        argv for argv in runner.calls if argv[:3] == ("docker", "rm", "--force")
    ]
    probes = [
        argv
        for argv in runner.calls
        if argv[:4] == ("docker", "container", "ls", "--all")
    ]
    assert len(forced) == 2
    assert len(probes) == 2


def _assert_process_gone(pid: int) -> None:
    deadline = time.monotonic() + 2.0
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        if time.monotonic() >= deadline:
            pytest.fail(f"subprocess {pid} survived bounded teardown")
        time.sleep(0.01)


def test_production_runner_streams_with_hard_sixteen_mib_bound(
    tmp_path: Path,
) -> None:
    pid_path = tmp_path / "overflow.pid"
    script = "\n".join(
        (
            "import os, sys",
            "with open(sys.argv[1], 'w') as stream:",
            "    stream.write(str(os.getpid()))",
            "chunk = b'x' * 65536",
            "while True:",
            "    os.write(1, chunk)",
        )
    )
    runner = F10SubprocessCommandRunner(
        timeout_seconds=5.0, cleanup_timeout_seconds=0.5
    )
    started = time.monotonic()

    with pytest.raises(F10OutputLimit, match="evidence bound"):
        asyncio.run(runner.run((sys.executable, "-c", script, str(pid_path))))

    assert time.monotonic() - started < 6.0
    _assert_process_gone(int(pid_path.read_text()))


def test_production_runner_timeout_kills_descendant_retaining_pipe(
    tmp_path: Path,
) -> None:
    pid_path = tmp_path / "timeout.pids"
    script = "\n".join(
        (
            "import os, subprocess, sys, time",
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])",
            "with open(sys.argv[1], 'w') as stream:",
            "    stream.write(f'{os.getpid()} {child.pid}')",
            "time.sleep(60)",
        )
    )
    runner = F10SubprocessCommandRunner(
        timeout_seconds=0.1, cleanup_timeout_seconds=0.5
    )
    started = time.monotonic()

    with pytest.raises(F10CommandTimeout, match="timed out"):
        asyncio.run(runner.run((sys.executable, "-c", script, str(pid_path))))

    assert time.monotonic() - started < 2.0
    for pid in (int(value) for value in pid_path.read_text().split()):
        _assert_process_gone(pid)


def test_production_runner_cancellation_kills_process_group(
    tmp_path: Path,
) -> None:
    pid_path = tmp_path / "cancel.pid"
    script = "\n".join(
        (
            "import os, sys, time",
            "with open(sys.argv[1], 'w') as stream:",
            "    stream.write(str(os.getpid()))",
            "time.sleep(60)",
        )
    )
    runner = F10SubprocessCommandRunner(
        timeout_seconds=10.0, cleanup_timeout_seconds=0.5
    )

    async def exercise() -> None:
        task = asyncio.create_task(
            runner.run((sys.executable, "-c", script, str(pid_path)))
        )
        for _ in range(200):
            if pid_path.exists():
                break
            await asyncio.sleep(0.005)
        assert pid_path.exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    started = time.monotonic()
    asyncio.run(exercise())

    assert time.monotonic() - started < 2.0
    _assert_process_gone(int(pid_path.read_text()))


def test_canonical_input_rejects_altered_probe_surface(tmp_path: Path) -> None:
    payload = author_f10_target_input(_authoring(tmp_path)).model_dump(mode="json")
    payload["commands"][0]["argv"] = ["docker", "info"]

    with pytest.raises(ValidationError, match="exact closed three-probe set"):
        F10IsolationDecisionInput.model_validate_json(
            canonical_json_bytes(payload), strict=True
        )
