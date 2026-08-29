from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Mapping

import pytest

from breadboard.rl.harness.composition import load_production_composition
from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.runners.base import RunnerOpenRequest
from breadboard.rl.harness.runners.conductor import _project_ir, _validate_arguments
from breadboard.rl.harness.qualification import (
    _read_resource,
    materialize_production_composition_fixture,
)

PRODUCTION_SOURCE_ROOT_NAMES = (
    "agent_configs",
    "agentic_coder_prototype",
    "breadboard",
    "breadboard_ext",
    "breadboard_sdk",
    "config",
    "conformance",
    "container_templates",
    "contracts",
    "examples",
    "implementations",
    "scripts",
    "sdk",
    "tool_calling",
    "tools",
)


def test_qualification_resource_requires_pinned_digest(tmp_path: Path) -> None:
    resource = tmp_path / "resource.json"
    resource.write_bytes(b'{"value":1}')
    digest = hashlib.sha256(resource.read_bytes()).hexdigest()
    assert _read_resource(resource, expected_sha256=digest) == b'{"value":1}'
    hardlink = tmp_path / "installed-hardlink.json"
    os.link(resource, hardlink)
    assert _read_resource(resource, expected_sha256=digest) == b'{"value":1}'
    resource.write_bytes(b'{"value":2}')
    with pytest.raises(RuntimeError, match="digest mismatch"):
        _read_resource(resource, expected_sha256=digest)

PRODUCTION_SOURCE_EXTENSIONS = {
    ".cfg",
    ".ini",
    ".json",
    ".py",
    ".sh",
    ".toml",
    ".yaml",
    ".yml",
}


def production_source_occurrences(value: str) -> tuple[Path, ...]:
    project_root = Path(__file__).resolve().parents[3]
    needle = value.encode("utf-8")
    return tuple(
        path
        for name in PRODUCTION_SOURCE_ROOT_NAMES
        if (root := project_root / name).is_dir()
        for path in root.rglob("*")
        if path.is_file()
        and (
            path.suffix in PRODUCTION_SOURCE_EXTENSIONS
            or path.name.endswith("Dockerfile")
        )
        and needle in path.read_bytes()
    )


@pytest.mark.parametrize("working_directory", ("first", "unrelated/nested"))
def test_materialized_production_composition_loads_from_unrelated_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, working_directory: str
) -> None:
    fixture = materialize_production_composition_fixture(tmp_path / "fixture")
    cwd = tmp_path / working_directory
    cwd.mkdir(parents=True)
    monkeypatch.chdir(cwd)

    composition = load_production_composition(
        str(fixture.composition_ref_path), fixture.secret_files
    )
    try:
        assert composition.manifest.composition_id == "production-fixture-composition"
        assert (
            composition.authority_graph.cas.get_ref(composition.manifest_ref).sha256
            == composition.manifest_ref
        )
        assert fixture.server_port != fixture.policy_server_port
        assert composition.server.port == fixture.server_port
        assert production_source_occurrences(fixture.generated_candidate_name) == ()
        manifest_bytes = fixture.composition_manifest_path.read_bytes()
        assert b"PRIVATE KEY" not in manifest_bytes
        assert all(
            secret not in manifest_bytes
            for secret in fixture.secret_seed_bytes.values()
        )
    finally:
        asyncio.run(composition.close())


def test_materialized_production_composition_resolves_typed_request(
    tmp_path: Path,
) -> None:
    fixture = materialize_production_composition_fixture(
        tmp_path / "fixture", long_running=True
    )
    composition = load_production_composition(
        str(fixture.composition_ref_path), fixture.secret_files
    )
    try:
        request = c.ResolveEpisodeRequest.model_validate(
            fixture.create_body["resolution"]
        )
        receipt = next(iter(composition.authority_graph.admitted_set.receipt_digests))
        receipt_value = c.AdmissionReceipt.model_validate_json(
            composition.authority_graph.store.load(
                receipt, kind=c.ArtifactKind.ADMISSION_RECEIPT, max_bytes=4_000_000
            )
        )
        manifest_bytes = composition.authority_graph.store.load(
            receipt_value.compiled.manifest_digest,
            kind=c.ArtifactKind.COMPILED_MANIFEST,
            max_bytes=4_000_000,
        )
        assert manifest_bytes
        resolved = composition.authority_graph.config_runtime.resolve_episode(request)
        assert resolved.effective_plan.selector_digest == fixture.selector_digest
        projection = _project_ir(
            RunnerOpenRequest(
                episode_id=request.episode_id,
                effective_plan=resolved.effective_plan,
            )
        )
        response_payload = fixture.policy_response_body["response_payload"]
        assert isinstance(response_payload, Mapping)
        output = response_payload["output"]
        assert isinstance(output, list) and len(output) == 1
        arguments = json.loads(output[0]["arguments"])
        assert arguments == {"command": "sleep 30"}
        _validate_arguments(arguments, projection.tools[0].schema["parameters"])
        observation = c.PolicyCapabilityObservation.model_validate(
            fixture.policy_observation
        )
        assert (
            projection.models[0].model_id,
            projection.models[0].provider_id,
        ) == (observation.model_id, observation.provider_id)
        assert (
            resolved.effective_plan.policy_capability_observation_digest
            == observation.canonical_digest()
        )
        assert (
            resolved.effective_plan.policy_capability_digest
            == observation.capability_digest
        )
    finally:
        asyncio.run(composition.close())


def test_measured_verifier_scores_only_exact_snapshot_output(tmp_path: Path) -> None:
    fixture = materialize_production_composition_fixture(tmp_path / "fixture")
    verifier = fixture.verifier_executable_identity
    metadata = verifier.path.stat(follow_symlinks=False)
    assert stat.S_IMODE(metadata.st_mode) == 0o500
    assert (metadata.st_dev, metadata.st_ino) == (verifier.device, verifier.inode)
    assert (
        "sha256:" + hashlib.sha256(verifier.path.read_bytes()).hexdigest()
        == verifier.sha256
    )
    request = {
        "schema_version": "bb.rl.verifier-request.v1",
        "episode_id": "episode-verifier-test",
        "effective_plan_digest": "sha256:" + "1" * 64,
        "task_digest": "sha256:" + "2" * 64,
        "snapshot_digest": "sha256:" + "3" * 64,
        "verifier_digest": "sha256:" + "4" * 64,
    }
    request_bytes = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()

    def run_verifier(
        name: str, task_output: bytes | None
    ) -> subprocess.CompletedProcess[bytes]:
        workspace = tmp_path / name
        (workspace / "input").mkdir(parents=True)
        (workspace / "snapshot").mkdir()
        (workspace / "result").mkdir()
        (workspace / "input/verifier-request.json").write_bytes(request_bytes)
        if task_output is not None:
            (workspace / "snapshot/task-output.json").write_bytes(task_output)
        return subprocess.run(
            [fixture.verifier_executable_identity.path],
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    valid = run_verifier("valid", b'{"answer":"breadboard-production-fixture"}')
    assert valid.returncode == 0, valid.stderr.decode(errors="replace")
    result = json.loads((tmp_path / "valid/result/result.json").read_bytes())
    assert result == {
        "effective_plan_digest": request["effective_plan_digest"],
        "episode_id": request["episode_id"],
        "score": 1.0,
        "snapshot_digest": request["snapshot_digest"],
        "task_digest": request["task_digest"],
        "verifier_digest": request["verifier_digest"],
    }

    tampered = run_verifier("tampered", b'{"answer":"attacker-controlled"}')
    assert tampered.returncode != 0
    assert not (tmp_path / "tampered/result/result.json").exists()

    missing = run_verifier("missing", None)
    assert missing.returncode != 0
    assert not (tmp_path / "missing/result/result.json").exists()
