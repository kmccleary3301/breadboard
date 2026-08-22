from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from breadboard.rl.phase5.f2_terminal import F1_PREREQUISITE_ID, F1_PREREQUISITE_REF, F1_PREREQUISITE_ROOT, TARGET_ARTIFACTS
from scripts.rl_phase5 import build_f2_phase3_payload as payload_builder
from scripts.rl_phase5 import build_f2_source_bundle as source_builder
from scripts.rl_phase5 import ingest_f2_phase3_attempt as ingest_module
from scripts.rl_phase5 import run_f2_target_command as runner

ATTEMPT = "f2-transport-test"
REFS = {"f1_prerequisite_ref": F1_PREREQUISITE_REF, "config_ref": "sha256:" + "2" * 64, "task_ref": "sha256:" + "3" * 64, "verifier_ref": "sha256:" + "4" * 64, "policy_ref": "sha256:" + "5" * 64}


def test_source_bundle_is_deterministic_and_inventory_is_exact(tmp_path: Path) -> None:
    breadboard = Path(__file__).resolve().parents[3]
    wrapper = breadboard.parent / "verl_wrapper_breadboard_integration_20260709"
    first, second = tmp_path / "first.tar.gz", tmp_path / "second.tar.gz"
    left = source_builder.build_bundle(breadboard, wrapper, first)
    right = source_builder.build_bundle(breadboard, wrapper, second)
    assert first.read_bytes() == second.read_bytes()
    assert left == right
    with tarfile.open(first, "r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        embedded = json.loads(archive.extractfile(source_builder.INVENTORY_NAME).read())
    assert len(names) == len(set(names))
    assert set(names) == {member["path"] for member in embedded["members"]} | {source_builder.INVENTORY_NAME}
    assert "scripts/rl_phase5/author_f2_target_dynamic_packet.py" in names
    assert all(member["mode"] in (0o644, 0o755) for member in embedded["members"])
    assert all(member["sha256"].startswith("sha256:") for member in embedded["members"])


def test_payload_is_deterministic_executable_and_has_no_nested_transport(tmp_path: Path) -> None:
    breadboard = Path(__file__).resolve().parents[3]
    wrapper = breadboard.parent / "verl_wrapper_breadboard_integration_20260709"
    outputs = [tmp_path / "a.zip", tmp_path / "b.zip"]
    results = [payload_builder.build_payload(breadboard_root=breadboard, wrapper_root=wrapper, output=path, attempt_id=ATTEMPT, **REFS) for path in outputs]
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    assert results[0]["payload_sha256"] == results[1]["payload_sha256"]
    with zipfile.ZipFile(outputs[0]) as archive:
        assert set(archive.namelist()) == {"F2_PAYLOAD_MANIFEST.json", "f2-source-bundle.tar.gz", "run.sh"}
        run = archive.read("run.sh")
        mode = archive.getinfo("run.sh").external_attr >> 16
        manifest = json.loads(archive.read("F2_PAYLOAD_MANIFEST.json"))
    assert mode == 0o700
    assert not any(token in run for token in (b"ssh ", b"scp ", b"srun ", b"SECRET", b"/Users/", b"/home/"))
    assert manifest["f1_prerequisite_ref"] == REFS["f1_prerequisite_ref"]
    assert manifest["f1_prerequisite_id"] == F1_PREREQUISITE_ID
    assert manifest["f1_prerequisite_root"] == F1_PREREQUISITE_ROOT
    assert manifest["source_bundle_sha256"] == "sha256:" + hashlib.sha256(zipfile.ZipFile(outputs[0]).read("f2-source-bundle.tar.gz")).hexdigest()


def test_payload_rejects_mutable_authority_and_no_overwrite(tmp_path: Path) -> None:
    breadboard = Path(__file__).resolve().parents[3]; wrapper = breadboard.parent / "verl_wrapper_breadboard_integration_20260709"
    output = tmp_path / "payload.zip"; output.write_bytes(b"owned")
    with pytest.raises(ValueError, match="canonical lowercase"):
        payload_builder.build_payload(breadboard_root=breadboard, wrapper_root=wrapper, output=tmp_path / "bad.zip", attempt_id=ATTEMPT, **{**REFS, "policy_ref": "latest"})
    with pytest.raises(ValueError, match="independently approved"):
        payload_builder.build_payload(breadboard_root=breadboard, wrapper_root=wrapper, output=tmp_path / "wrong-f1.zip", attempt_id=ATTEMPT, **{**REFS, "f1_prerequisite_ref": "sha256:" + "9" * 64})
    with pytest.raises(FileExistsError):
        payload_builder.build_payload(breadboard_root=breadboard, wrapper_root=wrapper, output=output, attempt_id=ATTEMPT, **REFS)
    assert output.read_bytes() == b"owned"


def test_archive_envelope_requires_canonical_base64_pad_bits() -> None:
    raw = b"\x00"
    canonical = runner._envelope(runner.RUNNER_PREFIX, raw)
    assert runner._decode_envelope(canonical, runner.RUNNER_PREFIX) == raw
    value = json.loads(canonical.split(b"=", 1)[1])
    assert value["payload"] == "AA=="
    for noncanonical in ("AB==", "AC==", "AP=="):
        mutated = {**value, "payload": noncanonical}
        encoded = runner.RUNNER_PREFIX + runner.canon(mutated) + b"\n"
        with pytest.raises(ValueError, match="payload is not canonical base64"):
            runner._decode_envelope(encoded, runner.RUNNER_PREFIX)


def test_archive_envelope_rejects_hash_mismatch_and_traversal(tmp_path: Path) -> None:
    raw = runner._archive({"ok": b"data"})
    line = runner._envelope(runner.RUNNER_PREFIX, raw)
    value = json.loads(line.split(b"=", 1)[1]); value["sha256"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="size/hash"):
        runner._decode_envelope(runner.RUNNER_PREFIX + json.dumps(value).encode() + b"\n", runner.RUNNER_PREFIX)
    malicious = io.BytesIO()
    with gzip.GzipFile(fileobj=malicious, mode="wb", mtime=0) as compressed, tarfile.open(fileobj=compressed, mode="w") as archive:
        info = tarfile.TarInfo("../escape"); info.size = 1; archive.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match="unsafe"):
        runner._safe_extract(malicious.getvalue(), tmp_path / "extract")
    assert not (tmp_path / "escape").exists()


def test_scratch_image_payload_is_deterministic_and_shared_only(tmp_path: Path) -> None:
    breadboard = Path(__file__).resolve().parents[3]
    wrapper = breadboard.parent / "verl_wrapper_breadboard_integration_20260709"
    outputs = [tmp_path / "image-a.zip", tmp_path / "image-b.zip"]
    results = [payload_builder.build_image_payload(breadboard_root=breadboard, wrapper_root=wrapper, output=path, attempt_id="f2-image-build-test") for path in outputs]
    assert outputs[0].read_bytes() == outputs[1].read_bytes()
    assert results[0] == results[1]
    with zipfile.ZipFile(outputs[0]) as archive:
        assert set(archive.namelist()) == {"f2-source-bundle.tar.gz", "F2_SOURCE_INVENTORY.json", "F2_IMAGE_BUILD_PAYLOAD_MANIFEST.json", "run.sh"}
        run = archive.read("run.sh")
    source_key = str(results[0]["source_tree_ref"]).removeprefix("sha256:")
    assert f"/shared/breadboard-f2/wrapper-images/{source_key}".encode() in run
    assert b"build-image" in run and b"--emit-phase3-component" in run
    assert b"docker pull" not in run


def test_scratch_host_runtime_payload_is_deterministic_and_uv_bound(tmp_path: Path) -> None:
    breadboard = Path(__file__).resolve().parents[3]
    wrapper = breadboard.parent / "verl_wrapper_breadboard_integration_20260709"
    uv_ref = runner.MANAGED_UV_REF
    first, second = tmp_path / "host-a.zip", tmp_path / "host-b.zip"
    kwargs = {"breadboard_root": breadboard, "wrapper_root": wrapper, "attempt_id": ATTEMPT, "uv_path": "/root/.local/bin/uv", "uv_ref": uv_ref}
    one = payload_builder.build_host_runtime_payload(output=first, **kwargs)
    two = payload_builder.build_host_runtime_payload(output=second, **kwargs)
    assert first.read_bytes() == second.read_bytes() and one == two
    with zipfile.ZipFile(first) as archive:
        run = archive.read("run.sh")
        manifest = json.loads(archive.read("F2_HOST_RUNTIME_PAYLOAD_MANIFEST.json"))
    assert b"/shared/breadboard-f2/host-runtime/" in run and b"sha256sum -c -" in run
    assert b"build-managed-host-runtime" in run and b"--emit-phase3-component" in run
    assert manifest["uv_ref"] == uv_ref and manifest["canonical_episode_allowed"] is False


def test_host_runtime_build_is_sealed_and_non_authorizing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bundle = tmp_path / "source.tar.gz"; bundle.write_bytes(runner._archive({"breadboard/rl/__init__.py": b"", "agentic_coder_prototype/compilation/server_compiler.py": b""}))
    inventory = tmp_path / "inventory.json"; inventory.write_text("{}")
    output, report_path = tmp_path / "runtime", tmp_path / "runtime-report.json"
    def bounded(argv: list[str], **kwargs: object):
        if "venv" in argv:
            (output / "bin").mkdir(parents=True)
            shutil.copyfile(__import__("sys").executable, output / "bin/python")
            (output / "bin/python").chmod(0o755)
        elif "install" in argv:
            Path(argv[argv.index("--report") + 1]).write_text(json.dumps({"install": [{"metadata": {"name": "pydantic", "version": "2.11.7"}, "download_info": {"archive_info": {"hashes": {"sha256": "a" * 64}}}}]}))
        stdout = b'{"implementation":"cpython","version":[3,12,8]}\n' if any("version_info" in value for value in argv) else (b"pydantic==2.11.7\n" if "freeze" in argv else b"")
        return __import__("subprocess").CompletedProcess(argv, 0, stdout, b"")
    monkeypatch.setattr(runner, "_bounded", bounded)
    monkeypatch.setattr(runner.subprocess, "run", lambda argv, **kwargs: __import__("subprocess").CompletedProcess(argv, 0, b"", b""))
    report = runner.build_host_runtime(python=Path(__import__("sys").executable), source_bundle=bundle, source_inventory=inventory, output=output, report_path=report_path)
    assert report["canonical_episode_allowed"] is False and report["independent_review_required"] is True
    assert report["sealed_read_only"] is True
    assert report["runtime_python"]["sha256"].startswith("sha256:")
    assert (output.stat().st_mode & 0o777) == 0o555
    with pytest.raises(FileExistsError):
        runner.build_host_runtime(python=Path(__import__("sys").executable), source_bundle=bundle, source_inventory=inventory, output=output, report_path=report_path)
    output.chmod(0o700)


def test_scratch_image_build_report_is_exact_and_non_authorizing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bundle = tmp_path / "source.tar.gz"; bundle.write_bytes(b"source")
    inventory = tmp_path / "inventory.json"; inventory.write_text(json.dumps({"members": [{"path": "third_party/nemo-gym/uv.lock", "sha256": "sha256:" + "1" * 64}]}))
    image_id = "sha256:" + "a" * 64
    def fake_run(argv: list[str], **kwargs: object):
        if argv[:3] == ["docker", "image", "inspect"]:
            stdout = json.dumps([{"Id": image_id, "Config": {}, "RootFS": {"Layers": []}}])
        elif "freeze" in argv:
            stdout = "nemo-gym==1.0\\npydantic==2.0\\n"
        elif "/opt/f2-install-report.json" in argv:
            stdout = json.dumps({"install": [{"metadata": {"name": "nemo-gym", "version": "1.0"}, "download_info": {"archive_info": {"hashes": {"sha256": "f" * 64}}}}]})
        elif "history" in argv:
            stdout = json.dumps({"ID": image_id})
        else:
            stdout = ""
        return {"argv": argv, "exit_code": 0, "stdout": stdout, "stderr": ""}
    monkeypatch.setattr(runner, "_run", fake_run)
    def fake_subprocess(argv: list[str], **kwargs: object):
        Path(argv[argv.index("--output") + 1]).write_bytes(b"image-tar")
        return __import__("subprocess").CompletedProcess(argv, 0, b"", b"")
    monkeypatch.setattr(runner.subprocess, "run", fake_subprocess)
    output, report_path = tmp_path / "image.tar", tmp_path / "report.json"
    report = runner.build_wrapper_image(bundle, inventory, output, report_path)
    assert report["canonical_episode_allowed"] is False
    assert report["independent_review_required"] is True
    assert report["image_id"] == image_id
    assert report["image_tar_ref"] == "sha256:" + hashlib.sha256(b"image-tar").hexdigest()
    assert report["resolved_packages"][0]["archive_hashes"] == {"sha256": "f" * 64}
    with pytest.raises(FileExistsError):
        runner.build_wrapper_image(bundle, inventory, output, report_path)


def test_presubmit_binds_known_host_and_live_target_before_phase3(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    key_blob = b"operator-host-key"
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key_blob).digest()).decode().rstrip("=")
    record = {"schema_version": "bb.rl.f2.ibm-target-record.v1", "ssh_alias": "ZYPHRA_IBM_AMD_1", "host_key_fingerprint": fingerprint, "cluster_name": "ibm", "controller": "ctrl-1", "partition": "gpu", "account": "acct", "qos": "normal", "reservation": "ReservationName=resv", "owner": "operator"}
    target = tmp_path / "target.json"; target.write_text(json.dumps(record))
    known = tmp_path / "known_hosts"; known.write_text("host ssh-ed25519 " + base64.b64encode(key_blob).decode() + "\n")
    probe = b"F2_OWNER=operator\nClusterName              = ibm\nctrl-1\nPartitionName=gpu\noperator|acct|normal\nReservationName=resv\n"
    def bounded(argv: list[str], **kwargs: object):
        if argv[:2] == ["ssh", "-G"]:
            stdout = b"hostname host\nuser operator\n"
        elif argv[:2] == ["ssh-keygen", "-F"]:
            stdout = known.read_bytes()
        else:
            stdout = probe
        return __import__("subprocess").CompletedProcess(argv, 0, stdout, b"")
    monkeypatch.setattr(runner, "_bounded", bounded)
    submitted: list[list[str]] = []
    monkeypatch.setattr(runner.subprocess, "run", lambda argv, **kwargs: (submitted.append(argv) or __import__("subprocess").CompletedProcess(argv, 0, b"", b"")))
    output = tmp_path / "phase3"
    rc = runner.precheck_and_submit(target_record_path=target, known_hosts=known, phase3_args=["--ssh-alias", "ZYPHRA_IBM_AMD_1", "--partition", "gpu", "--gres", "gpu:1", "--nodes", "1", "--ntasks", "1", "--qos", "normal", "--reservation", "resv", "--output-dir", str(output)])
    assert rc == 0 and submitted
    report = json.loads((output / "f2_target_precheck.json").read_text())
    assert report["passed"] is True
    assert report["raw_ref"] == "sha256:" + hashlib.sha256((output / "f2_target_precheck.raw").read_bytes()).hexdigest()


def test_presubmit_rejects_host_key_mismatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    record = {"schema_version": "bb.rl.f2.ibm-target-record.v1", "ssh_alias": "ZYPHRA_IBM_AMD_1", "host_key_fingerprint": "SHA256:wrong", "cluster_name": "ibm", "controller": "ctrl", "partition": "gpu", "account": "acct", "qos": "normal", "reservation": "resv", "owner": "operator"}
    target = tmp_path / "target.json"; target.write_text(json.dumps(record))
    known = tmp_path / "known_hosts"; known.write_text("host ssh-ed25519 " + base64.b64encode(b"different").decode())
    monkeypatch.setattr(runner, "_bounded", lambda argv, **kwargs: __import__("subprocess").CompletedProcess(argv, 0, b"hostname host\\n" if argv[:2] == ["ssh", "-G"] else known.read_bytes(), b""))
    with pytest.raises(ValueError, match="fingerprint"):
        runner.precheck_and_submit(target_record_path=target, known_hosts=known, phase3_args=[])


def test_pending_target_run_id_is_concretized_from_slurm_job(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("PHASE3_TARGET_RUN_ID", "20260711T000000Z-f2-slurm-pending")
    monkeypatch.setenv("SLURM_JOB_ID", "234567")
    monkeypatch.setattr(runner, "_run", lambda argv, **kwargs: {"argv": argv, "exit_code": 127, "stdout": "", "stderr": "unavailable"})
    runner.remote_run(tmp_path, ATTEMPT, **REFS)
    reports = [json.loads(line.split("=", 1)[1]) for line in capfd.readouterr().out.splitlines() if line.startswith("PHASE3_COMPONENT_REPORT_JSON=")]
    assert reports[0]["target_run_id"] == "20260711T000000Z-f2-slurm-234567"
    assert {key: reports[0]["input_hashes"][key] for key in REFS} == REFS
    assert reports[0]["input_hashes"]["docker_identity"].startswith("sha256:")


def test_stock_docker_blocker_is_pre_create_failed_component(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    calls: list[list[str]] = []
    def fake_run(argv: list[str], *, input_: bytes | None = None):
        calls.append(argv)
        if argv[:2] == ["docker", "version"]:
            return {"argv": argv, "exit_code": 0, "stdout": "{}", "stderr": ""}
        if argv[:2] == ["docker", "info"]:
            return {"argv": argv, "exit_code": 0, "stdout": json.dumps({"Runtimes": {"runc": {"path": "runc", "runtimeArgs": []}}}), "stderr": ""}
        return {"argv": argv, "exit_code": 0, "stdout": "", "stderr": ""}
    monkeypatch.setattr(runner, "_run", fake_run)
    monkeypatch.setenv("PHASE3_TARGET_RUN_ID", "f2-test-slurm-pending")
    monkeypatch.setenv("SLURM_JOB_ID", "42")
    rc = runner.remote_run(tmp_path, ATTEMPT, **REFS)
    output = capfd.readouterr().out
    reports = [json.loads(line.split("=", 1)[1]) for line in output.splitlines() if line.startswith("PHASE3_COMPONENT_REPORT_JSON=")]
    assert rc != 0 and len(reports) == 1
    assert reports[0]["passed"] is False and reports[0]["blocked_reason"] == "runtime_unsupported"
    assert not any(argv[:2] == ["docker", "create"] for argv in calls)
    archive = runner._decode_envelope(output.encode(), runner.RUNNER_PREFIX)
    extracted = tmp_path / "blocked"; runner._safe_extract(archive, extracted)
    cleanup = json.loads((extracted / "runner/post-cleanup.json").read_text())
    assert cleanup["container_create_attempted"] is False
    assert cleanup["outer_bridge_cleanup_receipt"] == {}


def test_runner_observation_envelope_is_exact_and_unambiguous() -> None:
    observation = {"image_inspect": {}, "container_inspect": {}, "post_cleanup": {}}
    line = runner.RUNNER_OBSERVATION_PREFIX + runner.canon(observation) + b"\n"
    assert runner._parse_runner_observation(line) == observation
    with pytest.raises(ValueError, match="exactly one"):
        runner._parse_runner_observation(line + line)
    with pytest.raises(ValueError, match="canonical"):
        runner._parse_runner_observation(runner.RUNNER_OBSERVATION_PREFIX + b'{"post_cleanup": {}, "container_inspect": {}, "image_inspect": {}}\n')

def test_gateway_socket_uses_verified_freebind_and_port_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, ...]] = []
    class FakeSocket:
        def setsockopt(self, *args: object) -> None: calls.append(("setsockopt", *args))
        def getsockopt(self, *args: object) -> int: calls.append(("getsockopt", *args)); return 1
        def bind(self, value: object) -> None: calls.append(("bind", value))
        def listen(self) -> None: calls.append(("listen",))
        def getsockname(self) -> tuple[str, int]: return ("172.28.0.1", 43123)
        def fileno(self) -> int: return 9
        def close(self) -> None: calls.append(("close",))
    metadata = type("Metadata", (), {"st_dev": 2, "st_ino": 3, "st_mode": 0o140777, "st_uid": 0})()
    monkeypatch.setattr(runner.socket, "socket", lambda *args: FakeSocket())
    monkeypatch.setattr(runner.os, "fstat", lambda fd: metadata)
    owned, plan = runner._prebind_gateway_socket("172.28.0.1", role="harness")
    assert isinstance(owned, FakeSocket)
    assert ("bind", ("172.28.0.1", 0)) in calls and ("listen",) in calls
    assert plan.observed_port == 43123 and plan.ip_freebind is True
    assert "fd" not in plan.model_dump() and plan.getsockname_host == "172.28.0.1"


def test_pinned_host_runtime_revalidates_full_inventory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "runtime"; (root / "bin").mkdir(parents=True)
    python = root / "bin/python"; shutil.copyfile(__import__("sys").executable, python); python.chmod(0o555)
    (root / "bin").chmod(0o555); root.chmod(0o555)
    python_ref = runner.sha(python.read_bytes())
    inventory = [{"path": "bin/python", "type": "file", "size": python.stat().st_size, "sha256": python_ref, "mode": 0o555}]
    report = tmp_path / "build-report.json"
    report_raw = runner.canon({"file_inventory": inventory, "runtime_python": {"sha256": python_ref}, "sealed_read_only": True}) + b"\n"
    report.write_bytes(report_raw)
    monkeypatch.setattr(runner, "HOST_RUNTIME_ROOT", root)
    monkeypatch.setattr(runner, "HOST_RUNTIME_REPORT", report)
    monkeypatch.setattr(runner, "HOST_RUNTIME_REPORT_REF", runner.sha(report_raw))
    monkeypatch.setattr(runner, "HOST_RUNTIME_PYTHON_REF", python_ref)
    assert runner._verify_pinned_host_runtime() == python
    python.chmod(0o755); python.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="inventory changed"):
        runner._verify_pinned_host_runtime()



def test_remote_run_uses_typed_composition_flow_when_authorities_are_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    from types import SimpleNamespace
    from breadboard.rl.phase5.f2_authority_authoring import (
        F2C4DynamicAuthorityInput,
        TlsCallbackLiveHandoffV1,
    )
    credential = tmp_path / "credential"; credential.write_bytes(b"x" * 32); credential.chmod(0o400)
    bundle = tmp_path / "bundle"; bundle.mkdir()
    static = bundle / "static-authority.json"; static.write_bytes(b"{}")
    dynamic = tmp_path / "dynamic-authority.json"; dynamic.write_bytes(b"{}"); dynamic.chmod(0o600)
    manifest = tmp_path / "composition-manifest.json"; manifest.write_bytes(b"{}")
    image_id = "sha256:" + "a" * 64
    immutable = "bb-f2-wrapper@sha256:" + "b" * 64
    (tmp_path / "wrapper-image-operator-authorization.json").write_text(json.dumps({"image_id": image_id}))
    image = SimpleNamespace(image_digest=image_id, immutable_reference=immutable)
    composition = SimpleNamespace(manifest=SimpleNamespace(installed=SimpleNamespace(images=[image])))
    lease = SimpleNamespace(composition_digest="sha256:" + "c" * 64)
    session = SimpleNamespace(build=SimpleNamespace(composition_manifest_path=str(manifest)), composition=composition, outer_bridge_lease=lease)
    semantic = {"outer_bridge_plan": {"schema_version": "bb.rl.harness-outer-bridge-plan.v1"}}
    semantic_path = tmp_path / "semantic.json"; semantic_path.write_bytes(runner.canon(semantic))
    opened: list[object] = []
    def open_session(*args: object):
        args[-1]["semantic_input"] = runner.sha(semantic_path.read_bytes())
        opened.append(args)
        return session, {}, semantic_path, tmp_path / "operator.json"
    monkeypatch.setattr(runner, "_open_f2_production_session", open_session)
    receipt = {"lease_id": "sha256:" + "d" * 64, "lease_digest": "sha256:" + "e" * 64, "id_absent": True, "name_absent": True}
    observation = {"image_inspect": {}, "container_inspect": {}, "post_cleanup": {"schema_version": "bb.rl.f2.cleanup-observation.v1", "remove": {"exit_code": 0}, "name_matches": [], "label_matches": [], "container_create_attempted": True, "outer_bridge_cleanup_receipt": receipt}}
    monkeypatch.setattr(runner, "_execute_retained_episode", lambda **kwargs: {"exit_code": 0, "stdout": "", "stderr": "", "runner_observation": observation, "callback_observation_journal": b"journal\n", "callback_observation_snapshot": b"{}", "callback_verification_receipt": b"{}", "callback_verification_signature": b"{}", "callback_verification_authority": b"{}", "callback_verification_public_key": b"public"})
    monkeypatch.setattr(runner, "_verify_pinned_host_runtime", lambda: Path(__import__("sys").executable))
    monkeypatch.setattr(runner, "_validate_target_result", lambda *args: {"validated": True})
    monkeypatch.setenv("PHASE3_TARGET_RUN_ID", "20260712T060000Z-slurm-pending")
    monkeypatch.setenv("SLURM_JOB_ID", "42")
    live = TlsCallbackLiveHandoffV1(
        runtime_input=SimpleNamespace(private_key_secret_handle_id="tls"),
        tls_private_key=None,  # type: ignore[arg-type]
        callback_socket=None,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(TlsCallbackLiveHandoffV1, "validate_against", lambda *_: None)
    monkeypatch.setattr(F2C4DynamicAuthorityInput, "model_validate_json", lambda *_args, **_kwargs: object())
    rc = runner.remote_run(bundle, ATTEMPT, credential, static_authority_fragment=static, dynamic_authority_input=dynamic, live_callback_runtime=live, observation_signing_handoff=SimpleNamespace(), evidence_receipt_signing_handoff=SimpleNamespace(), **REFS)
    reports = [json.loads(line.split("=", 1)[1]) for line in capfd.readouterr().out.splitlines() if line.startswith("PHASE3_COMPONENT_REPORT_JSON=")]
    assert rc == 0 and opened and reports[0]["passed"] is True
    assert not Path(opened[0][2]).parent.exists()

    semantic_raw = runner.canon({"schema_version": "semantic-test"})
    def fail_after_semantic_materialization(*args: object):
        semantic_output = Path(args[2]) / "semantic" / "semantic-input.json"
        semantic_output.parent.mkdir(parents=True)
        semantic_output.write_bytes(semantic_raw)
        args[-1]["semantic_input"] = runner.sha(semantic_output.read_bytes())
        raise RuntimeError("injected after semantic materialization")
    monkeypatch.setattr(
        runner, "_open_f2_production_session", fail_after_semantic_materialization
    )
    failed_rc = runner.remote_run(
        bundle,
        ATTEMPT,
        credential,
        static_authority_fragment=static,
        dynamic_authority_input=dynamic,
        live_callback_runtime=live,
        observation_signing_handoff=SimpleNamespace(),
        evidence_receipt_signing_handoff=SimpleNamespace(),
        **REFS,
    )
    failed_reports = [
        json.loads(line.split("=", 1)[1])
        for line in capfd.readouterr().out.splitlines()
        if line.startswith("PHASE3_COMPONENT_REPORT_JSON=")
    ]
    assert failed_rc != 0 and failed_reports[0]["passed"] is False
    failed_hashes = failed_reports[0]["input_hashes"]
    assert failed_hashes["static_authority"] == runner.sha(static.read_bytes())
    assert failed_hashes["dynamic_authority"] == runner.sha(dynamic.read_bytes())
    assert failed_hashes["semantic_input"] == runner.sha(semantic_raw)
    assert "credential" not in failed_hashes


def test_callback_receipt_is_independently_ed25519_verified(tmp_path: Path) -> None:
    from breadboard.rl.harness.composition import (
        ArtifactFileRefV1,
        CallbackJournalVerificationReceiptV1,
        EvidenceReceiptSignatureV1,
        EvidenceReceiptSigningAuthorityV1,
    )
    openssl = "/opt/homebrew/opt/openssl@3/bin/openssl"

    private_key = tmp_path / "receipt.key"
    public_key = tmp_path / "receipt.pub.pem"
    public_der = tmp_path / "receipt.pub.der"
    for command in (
        [openssl, "genpkey", "-algorithm", "ED25519", "-out", str(private_key)],
        [openssl, "pkey", "-in", str(private_key), "-pubout", "-out", str(public_key)],
        [openssl, "pkey", "-in", str(private_key), "-pubout", "-outform", "DER", "-out", str(public_der)],
    ):
        assert subprocess.run(command, capture_output=True).returncode == 0
    public_raw = public_key.read_bytes()
    authority = EvidenceReceiptSigningAuthorityV1(
        schema_version="bb.rl.harness-evidence-receipt-signing-authority.v1",
        attempt_id=ATTEMPT,
        composition_digest="sha256:" + "1" * 64,
        evidence_policy_digest="sha256:" + "2" * 64,
        algorithm="Ed25519",
        public_key_ref=ArtifactFileRefV1(path=str(public_key), sha256=runner.sha(public_raw), size_bytes=len(public_raw), media_type="application/x-pem-file"),
        public_key_sha256=runner.sha(public_raw),
        public_key_spki_sha256=runner.sha(public_der.read_bytes()),
        private_key_secret_handle_id="receipt-signing",
        openssl_authority_digest="sha256:" + "3" * 64,
    )
    journal = b'{"entry":1}\n'
    snapshot = runner.canon({"entry_count": 1})
    journal_path = tmp_path / "journal.jsonl"; journal_path.write_bytes(journal)
    snapshot_path = tmp_path / "snapshot.json"; snapshot_path.write_bytes(snapshot)
    receipt = CallbackJournalVerificationReceiptV1(
        schema_version="bb.rl.callback-journal-verification-receipt.v1",
        attempt_id=ATTEMPT,
        composition_digest=authority.composition_digest,
        route_id="f2-fixed-policy-callback",
        journal_ref=ArtifactFileRefV1(path=str(journal_path), sha256=runner.sha(journal), size_bytes=len(journal), media_type="application/x-ndjson"),
        snapshot_ref=ArtifactFileRefV1(path=str(snapshot_path), sha256=runner.sha(snapshot), size_bytes=len(snapshot), media_type="application/json"),
        head_mac="4" * 64,
        event_count=1,
        chain_verified=True,
        snapshot_verified=True,
        evidence_policy_digest=authority.evidence_policy_digest,
        signer_public_key_spki_sha256=authority.public_key_spki_sha256,
        signer_authority_digest=authority.canonical_digest(),
    )
    receipt_path = tmp_path / "receipt.json"; receipt_path.write_bytes(receipt.canonical_bytes())
    signature_path = tmp_path / "receipt.sig"
    assert subprocess.run([openssl, "pkeyutl", "-sign", "-rawin", "-inkey", str(private_key), "-in", str(receipt_path), "-out", str(signature_path)], capture_output=True).returncode == 0
    signature = EvidenceReceiptSignatureV1(
        schema_version="bb.rl.evidence-receipt-signature.v1",
        algorithm="Ed25519",
        signer_authority_digest=authority.canonical_digest(),
        receipt_digest=receipt.canonical_digest(),
        signature_base64=base64.b64encode(signature_path.read_bytes()).decode(),
    )
    private_key.unlink()
    verified = runner.verify_callback_journal_receipt(
        journal=journal,
        snapshot=snapshot,
        receipt_raw=receipt.canonical_bytes(),
        signature_raw=signature.canonical_bytes(),
        authority_raw=authority.canonical_bytes(),
        public_key=public_raw,
        openssl_path=openssl,
    )
    assert verified["event_count"] == 1
    with pytest.raises(ValueError, match="join"):
        runner.verify_callback_journal_receipt(
            journal=journal + b"x",
            snapshot=snapshot,
            receipt_raw=receipt.canonical_bytes(),
            signature_raw=signature.canonical_bytes(),
            authority_raw=authority.canonical_bytes(),
            public_key=public_raw,
            openssl_path=openssl,
        )
    mutated_count = receipt.model_copy(update={"event_count": 2})
    with pytest.raises(ValueError, match="join"):
        runner.verify_callback_journal_receipt(
            journal=journal,
            snapshot=snapshot,
            receipt_raw=mutated_count.canonical_bytes(),
            signature_raw=signature.canonical_bytes(),
            authority_raw=authority.canonical_bytes(),
            public_key=public_raw,
            openssl_path=openssl,
        )
    mutated_signer = signature.model_copy(update={"signer_authority_digest": "sha256:" + "9" * 64})
    with pytest.raises(ValueError, match="join"):
        runner.verify_callback_journal_receipt(
            journal=journal,
            snapshot=snapshot,
            receipt_raw=receipt.canonical_bytes(),
            signature_raw=mutated_signer.canonical_bytes(),
            authority_raw=authority.canonical_bytes(),
            public_key=public_raw,
            openssl_path=openssl,
        )
    mutated_signature = signature.model_copy(update={"signature_base64": base64.b64encode(b"x" * 64).decode()})
    with pytest.raises(ValueError, match="Ed25519"):
        runner.verify_callback_journal_receipt(
            journal=journal,
            snapshot=snapshot,
            receipt_raw=receipt.canonical_bytes(),
            signature_raw=mutated_signature.canonical_bytes(),
            authority_raw=authority.canonical_bytes(),
            public_key=public_raw,
            openssl_path=openssl,
        )
    with pytest.raises(ValueError, match="join"):
        runner.verify_callback_journal_receipt(
            journal=journal,
            snapshot=snapshot,
            receipt_raw=receipt.canonical_bytes(),
            signature_raw=signature.canonical_bytes(),
            authority_raw=authority.canonical_bytes(),
            public_key=public_raw + b"x",
            openssl_path=openssl,
        )
    with pytest.raises(ValueError, match="join"):
        runner.verify_callback_journal_receipt(
            journal=journal,
            snapshot=snapshot + b"x",
            receipt_raw=receipt.canonical_bytes(),
            signature_raw=signature.canonical_bytes(),
            authority_raw=authority.canonical_bytes(),
            public_key=public_raw,
            openssl_path=openssl,
        )
    mutated_spki = authority.model_copy(update={"public_key_spki_sha256": "sha256:" + "8" * 64})
    with pytest.raises(ValueError, match="join"):
        runner.verify_callback_journal_receipt(
            journal=journal,
            snapshot=snapshot,
            receipt_raw=receipt.canonical_bytes(),
            signature_raw=signature.canonical_bytes(),
            authority_raw=mutated_spki.canonical_bytes(),
            public_key=public_raw,
            openssl_path=openssl,
        )

def test_leaf_der_digest_is_pem_format_independent_and_wrong_der_fails(tmp_path: Path) -> None:
    openssl = "/opt/homebrew/opt/openssl@3/bin/openssl"
    key = tmp_path / "leaf.key"
    certificate = tmp_path / "leaf.pem"
    generated = subprocess.run(
        [
            openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(certificate),
            "-days", "1", "-subj", "/CN=172.28.0.1",
            "-addext", "subjectAltName=IP:172.28.0.1",
        ],
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
    )
    assert generated.returncode == 0
    expected = runner._measure_leaf_der_digest(certificate, openssl)
    pem_before = certificate.read_bytes()
    certificate.write_bytes(pem_before.replace(b"\n", b"\r\n"))
    assert runner.sha(certificate.read_bytes()) != runner.sha(pem_before)
    assert runner._verify_leaf_der_digest(certificate, expected, openssl) == expected
    with pytest.raises(RuntimeError, match="DER digest"):
        runner._verify_leaf_der_digest(certificate, "sha256:" + "0" * 64, openssl)


def test_socket_lease_projection_is_exact_complete_and_sorted() -> None:
    from types import SimpleNamespace

    class Lease:
        def __init__(self, role: str) -> None:
            self.role = role
        def model_dump(self, **_kwargs: object) -> dict[str, str]:
            return {"role": self.role}

    session = SimpleNamespace(prebound_service_sockets={
        "harness": Lease("harness"),
        "callback_tls": Lease("callback_tls"),
        "fixed_policy": Lease("fixed_policy"),
    })
    assert [item["role"] for item in runner._project_prebound_socket_leases(session)] == [
        "callback_tls", "fixed_policy", "harness",
    ]
    with pytest.raises(RuntimeError, match="not exact"):
        runner._project_prebound_socket_leases(SimpleNamespace(prebound_service_sockets={
            "fixed_policy": Lease("fixed_policy"),
            "harness": Lease("harness"),
        }))
    with pytest.raises(RuntimeError, match="role mismatch"):
        runner._project_prebound_socket_leases(SimpleNamespace(prebound_service_sockets={
            "callback_tls": Lease("fixed_policy"),
            "fixed_policy": Lease("fixed_policy"),
            "harness": Lease("harness"),
        }))


@pytest.mark.parametrize("callback_host", ["127.0.0.1", "0.0.0.0", "8.8.8.8", "169.254.1.1"])
def test_callback_authoring_rejects_non_private_gateway_topology(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    callback_host: str,
) -> None:
    from types import SimpleNamespace
    from breadboard.rl.phase5.f2_authority_authoring import F2C4TargetDynamicPlanInput

    packet = tmp_path / "packet.json"
    packet.write_bytes(runner.canon({"observations": {}, "plan": {}}))
    plan = SimpleNamespace(outer_bridge_plan=SimpleNamespace(gateway=callback_host))
    monkeypatch.setattr(F2C4TargetDynamicPlanInput, "model_validate_json", lambda *_args, **_kwargs: plan)
    with pytest.raises(ValueError, match="exact private bridge gateway"):
        runner._prepare_live_dynamic(tmp_path / "static.json", packet, tmp_path / "private")


def test_live_callback_authoring_produces_verified_tls13_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import ipaddress
    import socket
    import ssl
    import threading
    from types import SimpleNamespace
    import breadboard.rl.phase5.f2_authority_authoring as authority

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 53))
        target_ip = probe.getsockname()[0]
    finally:
        probe.close()
    address = ipaddress.ip_address(target_ip)
    if not address.is_private or address.is_loopback:
        pytest.skip("no target-private interface is available")
    inode_probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        inode_probe.bind((target_ip, 0))
        if os.fstat(inode_probe.fileno()).st_ino == 0:
            pytest.skip("Darwin does not expose socket inode identity")
    finally:
        inode_probe.close()
    packet = tmp_path / "packet.json"
    packet.write_bytes(runner.canon({
        "observations": {
            "secret_handles": {
                "records": [
                    {
                        "handle_id": "tls-key",
                        "purpose": "callback_tls_private_key",
                    },
                    {
                        "handle_id": "callback-observation-signing",
                        "purpose": "callback_observation_signing_key",
                    },
                    {
                        "handle_id": "evidence-receipt-signing",
                        "purpose": "evidence_receipt_signing_key",
                    },
                ],
            },
            "callback_observation_evidence_policy_revision_digest": "sha256:" + "7" * 64,
            "evidence_receipt_signing_authority": {
                "composition_digest": "sha256:" + "8" * 64,
            },
        },
        "plan": {},
    }))
    plan = SimpleNamespace(
        attempt_id=ATTEMPT,
        outer_bridge_plan=SimpleNamespace(gateway=target_ip),
    )
    monkeypatch.setattr(authority.F2C4TargetDynamicPlanInput, "model_validate_json", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(authority.F2C4StaticAuthorityFragment, "model_validate_json", lambda *_args, **_kwargs: SimpleNamespace(authority=SimpleNamespace(openssl=SimpleNamespace(path="/usr/bin/openssl", model_dump=lambda **_kwargs: {}))))
    monkeypatch.setattr(authority.F2C4TargetDynamicObservations, "model_validate_json", lambda value, **_kwargs: value)
    dynamic = SimpleNamespace(model_dump=lambda **_kwargs: {})
    monkeypatch.setattr(authority, "author_f2_target_dynamic_authority", lambda *_args: dynamic)
    monkeypatch.setattr(authority.TlsCallbackLiveHandoffV1, "validate_against", lambda *_args: None)
    monkeypatch.setattr(authority.CallbackObservationSigningKeyHandoffV1, "validate_against", lambda *_args: None)
    monkeypatch.setattr(authority.EvidenceReceiptSigningKeyRuntimeHandoffV1, "validate_against", lambda *_args, **_kwargs: None)
    live_socket = None
    key_fd = -1
    signing_fd = -1
    receipt_signing_fd = -1
    try:
        (tmp_path / "static.json").write_bytes(b"{}")
        _dynamic, live, _signing, _receipt_signing, live_socket, key_fd, signing_fd, receipt_signing_fd = runner._prepare_live_dynamic(
            tmp_path / "static.json",
            packet,
            tmp_path / "private",
        )
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.minimum_version = ssl.TLSVersion.TLSv1_3
        server_context.maximum_version = ssl.TLSVersion.TLSv1_3
        server_context.load_cert_chain(
            live.runtime_input.leaf_certificate_ref.path,
            f"/proc/self/fd/{key_fd}",
        )
        observed: list[tuple[str, str]] = []
        def serve_once() -> None:
            accepted, _ = live_socket.accept()
            with accepted, server_context.wrap_socket(accepted, server_side=True) as secured:
                observed.append((secured.version(), secured.cipher()[0]))
                secured.recv(1)
        thread = threading.Thread(target=serve_once)
        thread.start()
        client_context = ssl.create_default_context(cafile=live.runtime_input.ca_certificate_ref.path)
        client_context.minimum_version = ssl.TLSVersion.TLSv1_3
        client_context.maximum_version = ssl.TLSVersion.TLSv1_3
        with socket.create_connection((target_ip, live.runtime_input.observed_port), timeout=5) as raw:
            with client_context.wrap_socket(raw, server_hostname=target_ip) as secured:
                secured.sendall(b"x")
        thread.join(timeout=5)
        assert observed == [("TLSv1.3", "TLS_AES_256_GCM_SHA384")]
        assert live.runtime_input.host == plan.outer_bridge_plan.gateway
        assert live.runtime_input.socket_role == "callback_tls"
    finally:
        if live_socket is not None:
            live_socket.close()
        if key_fd >= 0:
            os.close(key_fd)
        if signing_fd >= 0:
            os.close(signing_fd)
        if receipt_signing_fd >= 0:
            os.close(receipt_signing_fd)


def test_remote_run_rejects_partial_composition_authority(tmp_path: Path) -> None:
    credential = tmp_path / "credential"; credential.write_bytes(b"x" * 32); credential.chmod(0o400)
    bundle = tmp_path / "bundle"; bundle.mkdir()
    static = bundle / "static-authority.json"; static.write_bytes(b"{}")
    with pytest.raises(ValueError, match="supplied together"):
        runner.remote_run(bundle, ATTEMPT, credential, static_authority_fragment=static, **REFS)


def _outer(tmp_path: Path, payload: Path, *, status: str = "passed", raw_hash: str | None = None) -> tuple[Path, str]:
    output = tmp_path / "phase3"; logs = output / "command_logs"; logs.mkdir(parents=True)
    raw = logs / f"{ATTEMPT}.log"; raw.write_bytes(b"transport")
    requested = "20260711T000000Z-f2-slurm-pending"; job = "42"; final = requested.removesuffix("pending") + job
    argv = ["runner", "--ssh-alias", "ZYPHRA_IBM_AMD_1", "--partition", "gpu", "--command-id", ATTEMPT, "--target-run-id", requested, "--payload-zip", str(payload), "--gres", "gpu:1", "--nodes", "1", "--ntasks", "1"]
    row = {"command_id": ATTEMPT, "status": status, "exit_code": 0, "blocked_reason": "", "component_failed_count": 0, "component_passed": True, "slurm_job_id": job, "node": "ibm-1", "target_run_id": final, "argv": argv, "raw_log_path": f"command_logs/{ATTEMPT}.log", "raw_log_sha256": raw_hash or "sha256:" + hashlib.sha256(raw.read_bytes()).hexdigest()}
    (output / "phase3_command_log_manifest.json").write_text(json.dumps({"commands": [row]}))
    precheck_raw = b"verified-target"
    (output / "f2_target_precheck.raw").write_bytes(precheck_raw)
    (output / "f2_target_precheck.json").write_text(json.dumps({"schema_version": "bb.rl.f2.target-precheck.v1", "passed": True, "ssh_alias": "ZYPHRA_IBM_AMD_1", "raw_ref": "sha256:" + hashlib.sha256(precheck_raw).hexdigest()}))
    return output, final


def test_ingest_rejects_attempt_path_injection_before_writes(tmp_path: Path) -> None:
    payload = tmp_path / "payload.zip"; payload.write_bytes(b"zip")
    phase3 = tmp_path / "phase3"; phase3.mkdir()
    for attempt_id in ("../escape", "/tmp/f2-escape", "f2-UPPER", "f2-a/child"):
        with pytest.raises(ValueError, match="invalid F2 attempt id"):
            ingest_module.ingest(phase3_output=phase3, attempt_id=attempt_id, target_run_id="target", payload_zip=payload, scratch_root=tmp_path / "scratch")
    assert not (tmp_path / "escape").exists()


def test_ingest_rejects_failed_outer_row_and_raw_hash_mismatch(tmp_path: Path) -> None:
    payload = tmp_path / "payload.zip"; payload.write_bytes(b"zip")
    output, target = _outer(tmp_path, payload, status="failed")
    with pytest.raises(ValueError, match="did not pass"):
        ingest_module.ingest(phase3_output=output, attempt_id=ATTEMPT, target_run_id=target, payload_zip=payload, scratch_root=tmp_path / "scratch")
    output2, target2 = _outer(tmp_path / "other", payload, raw_hash="sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="raw log hash"):
        ingest_module.ingest(phase3_output=output2, attempt_id=ATTEMPT, target_run_id=target2, payload_zip=payload, scratch_root=tmp_path / "scratch2")


def test_ingest_validation_failure_is_atomic_and_destination_no_overwrite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = tmp_path / "payload.zip"; payload.write_bytes(b"zip")
    output, target = _outer(tmp_path, payload)
    monkeypatch.setattr(ingest_module, "_decode_envelope", lambda raw, prefix: b"archive")
    def fake_extract(raw: bytes, destination: Path) -> None:
        destination.mkdir()
        if destination.name == "decoded-runner":
            (destination / "target.stdout").write_bytes(b"result")
            (destination / "target.stderr").write_bytes(b"")
        else:
            for name in TARGET_ARTIFACTS.values():
                (destination / name).write_bytes(b"{}")
    monkeypatch.setattr(ingest_module, "_safe_extract", fake_extract)
    marker_hash = "sha256:" + hashlib.sha256(b"{}").hexdigest()
    monkeypatch.setattr(ingest_module, "parse_artifact_markers", lambda stdout, attempt: [
        {"name": name, "path": "artifacts/" + filename, "sha256": marker_hash, "size": 2}
        for name, filename in TARGET_ARTIFACTS.items()
    ])
    observed_transport: dict[str, object] = {}
    def reject_after_observing(path: Path, **kwargs: object) -> None:
        observed_transport.update(json.loads((path / "outer/transport.json").read_text()))
        raise ValueError("invalid target evidence")
    monkeypatch.setattr(ingest_module, "validate_scratch", reject_after_observing)
    scratch = tmp_path / "scratch"
    with pytest.raises(ValueError, match="invalid target"):
        ingest_module.ingest(phase3_output=output, attempt_id=ATTEMPT, target_run_id=target, payload_zip=payload, scratch_root=scratch)
    assert not (scratch / ATTEMPT).exists()
    assert observed_transport["runner_archive_ref"] == "sha256:" + hashlib.sha256(b"archive").hexdigest()
    (scratch / ATTEMPT).mkdir()
    with pytest.raises(FileExistsError):
        ingest_module.ingest(phase3_output=output, attempt_id=ATTEMPT, target_run_id=target, payload_zip=payload, scratch_root=scratch)
