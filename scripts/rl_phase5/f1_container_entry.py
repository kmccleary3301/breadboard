from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import dataclasses
import errno
import hashlib
import hmac
import io
import json
import os
import shutil
import re
import signal
import socket
import secrets
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from types import MappingProxyType
from urllib.error import HTTPError
from urllib.request import Request, urlopen

MARKER_SCHEMA = "bb.rl.f1.artifact-marker.v1"
ARTIFACTS = {
    "source": "source-inventory.json",
    "dependencies_lock": "dependency-lock.txt",
    "dependencies_freeze": "pip-freeze.txt",
    "dependencies_check": "pip-check.json",
    "resolver": "resolved-env.json",
    "config": "resolved-config.yaml",
    "composition_ref": "composition-reference-observation.json",
    "composition_manifest": "composition-manifest-observation.json",
    "composition_inspect": "composition-inspect-observation.json",
    "composition_inspect_stderr": "inspect.stderr",
    "unauthenticated": "unauthenticated-response.json",
    "wrapper_request": "wrapper-request.json",
    "wrapper_response": "wrapper-response.json",
    "status_response": "status-response.json",
    "completed_response": "completed-response.json",
    "closed_response": "closed-response.json",
    "callback": "callback-observations.json",
    "harness_stdout": "harness.stdout",
    "harness_stderr": "harness.stderr",
    "policy_stdout": "policy.stdout",
    "policy_stderr": "policy.stderr",
    "process_before": "process-before.json",
    "process_after": "process-after.json",
    "private_cleanup": "private-cleanup.json",
}

CLI_BRIDGE = "import json,os,sys; from breadboard.rl.harness.__main__ import main; b=json.loads(os.environ.pop('F1_SECRET_BINDINGS')); a=[sys.argv[1],'--composition-ref',sys.argv[2]]; [a.extend(['--secret-file',k+'='+v]) for k,v in b.items()]; raise SystemExit(main(a))"


def canon(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def derive_secrets(seed: bytes) -> dict[str, bytes]:
    if len(seed) < 32:
        raise ValueError("secret seed must contain at least 32 bytes")
    def derive(label: bytes, size: int) -> bytes:
        return hmac.new(seed, b"bb.rl.f1/" + label, hashlib.sha256).hexdigest()[:size].encode()
    return {
        "api-auth": b"fixture-api-" + derive(b"api-auth", 48),
        "policy-callback": b"fixture-policy-" + derive(b"policy-callback", 48),
        "receipt-signing": b"fixture-receipt-" + derive(b"receipt-signing", 64),
    }


def deterministic_token_hex_values(
    seed: bytes,
    derived: dict[str, bytes],
) -> tuple[str, str, str, str]:
    candidate = hmac.new(
        seed,
        b"bb.rl.f1/generated-candidate",
        hashlib.sha256,
    ).hexdigest()[:24]
    return (
        *(
            derived[handle].split(b"-", 2)[-1].decode()
            for handle in ("api-auth", "policy-callback", "receipt-signing")
        ),
        candidate,
    )


def write_file(path: Path, raw: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0), 0o600)
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short artifact write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_json(path: Path, value: object) -> None:
    write_file(path, canon(value))


def http_observation(
    method: str,
    base: str,
    path: str,
    token: str | None = None,
    payload: object | None = None,
) -> dict[str, object]:
    headers = {"Accept": "application/json"}
    data = None
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = canon(payload)
    try:
        with urlopen(
            Request(base + path, data=data, headers=headers, method=method),
            timeout=10,
        ) as response:
            status, response_payload = response.status, response.read()
    except HTTPError as error:
        status, response_payload = error.code, error.read()
    try:
        body: object = json.loads(response_payload)
    except json.JSONDecodeError:
        body = {
            "raw_body_base64": base64.b64encode(response_payload).decode()
        }
    return {
        "schema_version": "bb.rl.f1.http-observation.v1",
        "request": {"method": method, "path": path},
        "status": status,
        "body": body,
    }


def sanitize_runtime_paths(value: object, private_root: Path) -> object:
    if isinstance(value, dict):
        return {
            str(key): sanitize_runtime_paths(child, private_root)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [sanitize_runtime_paths(child, private_root) for child in value]
    if isinstance(value, str):
        if (
            value.startswith(("/", "~/"))
            or re.match(r"^[A-Za-z]:[\\/]", value)
            or str(private_root) in value
        ):
            return "[RUNTIME_PATH]"
    return value


def composition_evidence_observations(
    *,
    composition_ref: bytes,
    composition_manifest: bytes,
    inspect_stdout: bytes,
    inspect_stderr: bytes,
    inspect_exit_code: int,
    private_root: Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    ref = json.loads(composition_ref)
    manifest = json.loads(composition_manifest)
    inspected = json.loads(inspect_stdout)
    ref_observation = {
        "schema_version": "bb.rl.f1.composition-reference-observation.v1",
        "composition_ref_sha256": digest(composition_ref),
        "composition_ref_size_bytes": len(composition_ref),
        "ref_schema_version": ref["schema_version"],
        "manifest_sha256": ref["manifest_sha256"],
        "manifest_size_bytes": ref["manifest_size_bytes"],
        "manifest_media_type": ref["manifest_media_type"],
        "manifest_path_disposition": "absolute_runtime_path_omitted",
    }
    manifest_observation = {
        "schema_version": "bb.rl.f1.composition-manifest-observation.v1",
        "raw_sha256": digest(composition_manifest),
        "raw_size_bytes": len(composition_manifest),
        "semantic": sanitize_runtime_paths(manifest, private_root),
    }
    inspect_observation = {
        "schema_version": "bb.rl.f1.composition-inspect-observation.v1",
        "exit_code": inspect_exit_code,
        "raw_stdout_sha256": digest(inspect_stdout),
        "raw_stdout_size_bytes": len(inspect_stdout),
        "raw_stderr_sha256": digest(inspect_stderr),
        "raw_stderr_size_bytes": len(inspect_stderr),
        "semantic": sanitize_runtime_paths(inspected, private_root),
    }
    return ref_observation, manifest_observation, inspect_observation


def free_port() -> int:
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        return int(reserved.getsockname()[1])


def process_probe(pid: int) -> int:
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return int(exc.errno or 0)
    return 0


def deterministic_archive(artifacts: Path) -> bytes:
    compressed = io.BytesIO()
    import gzip
    with gzip.GzipFile(filename="", mode="wb", fileobj=compressed, compresslevel=9, mtime=0) as sink:
        with tarfile.open(fileobj=sink, mode="w") as archive:
            for path in sorted(artifacts.iterdir()):
                raw = path.read_bytes()
                info = tarfile.TarInfo(path.name)
                info.size = len(raw); info.mode = 0o600; info.mtime = 0
                info.uid = info.gid = 0; info.uname = info.gname = ""
                archive.addfile(info, io.BytesIO(raw))
    return compressed.getvalue()

def reject_private_material(artifacts: Path, secrets_: tuple[bytes, ...], private_paths: tuple[Path, ...]) -> None:
    from urllib.parse import quote_from_bytes
    forbidden: set[bytes] = set()
    for secret in secrets_:
        forbidden.update((
            secret,
            secret.hex().encode(),
            base64.b64encode(secret),
            base64.urlsafe_b64encode(secret),
            quote_from_bytes(secret).encode(),
        ))
        try:
            text = secret.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            forbidden.update(
                (text.encode("utf-16-le"), text.encode("utf-16-be"))
            )
    forbidden.update(str(path).encode() for path in private_paths)
    for artifact in artifacts.iterdir():
        raw = artifact.read_bytes()
        if any(value and value in raw for value in forbidden):
            raise RuntimeError(f"private seed, derived secret, or absolute secret path reached {artifact.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Produce raw F1 preflight observations")
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--secret-seed-file", type=Path)
    args = parser.parse_args()
    sys.path[:0] = [str(args.source_root), str(args.source_root / "third_party/nemo-gym")]
    from omegaconf import OmegaConf
    from nemo_gym.config_types import BaseServerConfig
    from nemo_gym.server_utils import ServerClient
    from responses_api_agents.breadboard_agent.app import BreadBoardAgentConfig, BreadBoardAgent, BreadBoardContractError, BreadBoardLifecycleError, BreadBoardRunRequest, BreadBoardTransportError
    from breadboard.rl.harness.qualification import (
        materialize_production_composition_fixture,
        qualification_policy_server,
    )
    from recipe.nemo_async.envs.catalog import resolve_env

    private = Path(tempfile.mkdtemp(prefix="f1-private-"))
    fixture_root = private / "fixture"
    artifacts = private / "artifacts"
    artifacts.mkdir(mode=0o700)
    harness: subprocess.Popen[bytes] | None = None
    secret_fds: list[int] = []
    try:
        seed = args.secret_seed_file.read_bytes() if args.secret_seed_file is not None else sys.stdin.buffer.read(33)
        if len(seed) != 32:
            raise ValueError("exactly 32 private seed bytes required")
        derived = derive_secrets(seed)
        original_token_hex = secrets.token_hex
        seeded_hex = iter(deterministic_token_hex_values(seed, derived))
        secrets.token_hex = lambda _size: next(seeded_hex)
        try:
            fixture = materialize_production_composition_fixture(
                fixture_root, server_port=free_port(), policy_server_port=free_port()
            )
        finally:
            secrets.token_hex = original_token_hex
        if dict(fixture.secret_seed_bytes) != derived:
            raise RuntimeError("fixture did not consume the private seed exactly")
        os.environ["BREADBOARD_HARNESS_BASE_URL"] = f"http://{fixture.server_host}:{fixture.server_port}"
        resolved_env = resolve_env(args.source_root, "breadboard", data_override="f1-preflight-input.jsonl")
        resolved = {
            "schema_version": "bb.rl.f1.env-resolution-observation.v1",
            "requested_name": "breadboard",
            "resolved": {
                "env": resolved_env["env"],
                "kind": resolved_env["kind"],
                "agent_name": resolved_env["agent_name"],
                "config_paths": resolved_env["config_paths"],
                "required_env": resolved_env["required_env"],
                "missing_required_env": resolved_env["missing_required_env"],
                "service_servers": resolved_env["service_servers"],
                "data_override": resolved_env["data_override"],
            },
        }
        write_json(artifacts / ARTIFACTS["resolver"], resolved)
        config_rel = resolved["resolved"]["config_paths"][0]
        write_file(artifacts / ARTIFACTS["config"], (args.source_root / config_rel).read_bytes())
        write_file(artifacts / ARTIFACTS["source"], (args.source_root / "F1_SOURCE_INVENTORY.json").read_bytes())
        write_file(artifacts / ARTIFACTS["dependencies_lock"], (args.source_root / "scripts/rl_phase5/f1_requirements.lock").read_bytes())
        composition_ref_bytes = fixture.composition_ref_path.read_bytes()
        composition_manifest_bytes = fixture.composition_manifest_path.read_bytes()

        cli_environment = os.environ.copy()
        cli_environment["PYTHONPATH"] = os.pathsep.join(
            (
                str(args.source_root),
                str(args.source_root / "third_party/nemo-gym"),
                cli_environment.get("PYTHONPATH", ""),
            )
        )
        cli_environment["F1_SECRET_BINDINGS"] = json.dumps({handle: str(path) for handle, path in fixture.secret_paths.items()}, sort_keys=True, separators=(",", ":"))
        inspect = subprocess.run(
            [sys.executable, "-c", CLI_BRIDGE, "inspect", str(fixture.composition_ref_path)],
            env=cli_environment, capture_output=True, check=False,
        )
        if inspect.returncode != 0:
            raise RuntimeError(f"composition inspect failed: {inspect.returncode}")
        (
            composition_ref_observation,
            composition_manifest_observation,
            composition_inspect_observation,
        ) = composition_evidence_observations(
            composition_ref=composition_ref_bytes,
            composition_manifest=composition_manifest_bytes,
            inspect_stdout=inspect.stdout.rstrip(b"\n"),
            inspect_stderr=inspect.stderr,
            inspect_exit_code=inspect.returncode,
            private_root=private,
        )
        write_json(artifacts / ARTIFACTS["composition_ref"], composition_ref_observation)
        write_json(artifacts / ARTIFACTS["composition_manifest"], composition_manifest_observation)
        write_json(artifacts / ARTIFACTS["composition_inspect"], composition_inspect_observation)
        write_file(artifacts / ARTIFACTS["composition_inspect_stderr"], inspect.stderr)

        callback_requests: list[dict[str, object]] = []
        with qualification_policy_server(fixture) as (_, _, callback_requests):
            harness = subprocess.Popen(
                [sys.executable, "-c", CLI_BRIDGE, "serve", str(fixture.composition_ref_path)],
                env=cli_environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            write_json(artifacts / ARTIFACTS["process_before"], {
                "schema_version": "bb.rl.f1.process-identities.v1",
                "processes": [{"role": "harness", "pid": harness.pid, "ppid": os.getpid()}],
            })
            base = f"http://{fixture.server_host}:{fixture.server_port}"
            for _ in range(200):
                try:
                    with urlopen(base + "/healthz", timeout=0.2) as response:
                        if response.status == 200:
                            break
                except Exception:
                    time.sleep(0.05)
            else:
                raise RuntimeError("harness health timeout")
            write_json(
                artifacts / ARTIFACTS["unauthenticated"],
                http_observation(
                    "POST",
                    base,
                    "/v2/episodes",
                    payload=dict(fixture.create_body),
                ),
            )
            config = BreadBoardAgentConfig(
                name="breadboard",
                host="127.0.0.1",
                port=1,
                entrypoint="app.py",
                model_server={
                    "type": "responses_api_models",
                    "name": "unused_policy_model",
                },
                breadboard_base_url=base,
                breadboard_auth_token_file=str(
                    fixture.secret_paths["api-auth"]
                ),
                breadboard_timeout_seconds=120,
            )
            client = ServerClient(head_server_config=BaseServerConfig(host="127.0.0.1", port=1), global_config_dict=OmegaConf.create({}))
            agent = BreadBoardAgent(config=config, server_client=client)
            opaque = {
                "resolution": dict(fixture.create_body["resolution"]),
                "task_input": {"task_id": "f1", "input": "authenticated preflight"},
                "context": {"purpose": "preflight"},
            }
            episode_id = str(opaque["resolution"]["episode_id"])
            run_request = BreadBoardRunRequest.model_validate({
                "responses_create_params": {"input": "authenticated preflight", "model": "unused", "metadata": {"request_id": episode_id}},
                "breadboard_v2": opaque,
            })
            write_json(artifacts / ARTIFACTS["wrapper_request"], run_request.model_dump(mode="json"))
            try:
                result = asyncio.run(agent.run(run_request))
            except BreadBoardLifecycleError as exc:
                primary = exc.primary_error
                diagnostic = {
                    "schema_version": "bb.rl.f1.lifecycle-failure-diagnostic.v1",
                    "primary_error_type": (
                        None if primary is None else type(primary).__name__
                    ),
                    "primary_error_code": (
                        primary.code
                        if isinstance(primary, BreadBoardContractError)
                        else None
                    ),
                    "primary_error_observed": (
                        primary.observed
                        if isinstance(primary, BreadBoardContractError)
                        else None
                    ),
                    "cleanup_error_type": (
                        None
                        if exc.cleanup_error is None
                        else type(exc.cleanup_error).__name__
                    ),
                }
                try:
                    from breadboard.artifacts.cas import FilesystemCAS
                    from breadboard.artifacts.references import ArtifactRef

                    completed_observation = http_observation(
                        "GET",
                        base,
                        f"/v2/episodes/{episode_id}/envelopes/completed",
                        fixture.api_bearer,
                    )
                    completed_body = completed_observation["body"]
                    completed_event_ref = ArtifactRef(
                        **completed_body["completed_event_ref"]
                    )
                    completed_event = json.loads(
                        FilesystemCAS(fixture.object_cas_root).get_bytes(
                            completed_event_ref,
                            max_bytes=65_536,
                        )
                    )
                    diagnostic["completed_event"] = {
                        key: completed_event.get(key)
                        for key in (
                            "from_state",
                            "to_state",
                            "event_kind",
                            "primary_fact",
                            "cleanup_fact",
                        )
                    }
                except Exception as diagnostic_exc:
                    diagnostic["evidence_diagnostic_error_type"] = type(
                        diagnostic_exc
                    ).__name__
                sys.stderr.write(
                    "F1_LIFECYCLE_DIAGNOSTIC="
                    + canon(sanitize_runtime_paths(diagnostic, private)).decode()
                    + "\n"
                )
                raise
            except BreadBoardTransportError as exc:
                raise RuntimeError(f"wrapper transport failed: status={exc.status_code} v2_error={exc.v2_error}") from exc
            write_json(artifacts / ARTIFACTS["wrapper_response"], result.model_dump(mode="json"))
            write_json(artifacts / ARTIFACTS["status_response"], http_observation("GET", base, f"/v2/episodes/{episode_id}", fixture.api_bearer))
            write_json(artifacts / ARTIFACTS["completed_response"], http_observation("GET", base, f"/v2/episodes/{episode_id}/envelopes/completed", fixture.api_bearer))
            write_json(artifacts / ARTIFACTS["closed_response"], http_observation("GET", base, f"/v2/episodes/{episode_id}/envelopes/closed", fixture.api_bearer))
        sanitized_callbacks = []
        for index, observation in enumerate(callback_requests):
            sanitized_callbacks.append({
                "path": observation["path"],
                "request_body_sha256": digest(canon(observation["body"])),
            })
        write_json(artifacts / ARTIFACTS["callback"], {"schema_version": "bb.rl.f1.callback-observations.v1", "observations": sanitized_callbacks})
        write_file(artifacts / ARTIFACTS["policy_stdout"], b"")
        write_file(artifacts / ARTIFACTS["policy_stderr"], b"")

        harness.send_signal(signal.SIGTERM)
        harness_stdout, harness_stderr = harness.communicate(timeout=30)
        harness_pid = harness.pid
        harness_returncode = harness.returncode
        harness = None
        write_file(artifacts / ARTIFACTS["harness_stdout"], harness_stdout)
        write_file(artifacts / ARTIFACTS["harness_stderr"], harness_stderr)
        write_json(artifacts / ARTIFACTS["process_after"], {
            "schema_version": "bb.rl.f1.process-probes.v2",
            "processes": [{
                "role": "harness",
                "pid": harness_pid,
                "returncode": harness_returncode,
                "probe_errno": process_probe(harness_pid),
            }],
        })
        secret_fds.clear()

        freeze = subprocess.run([sys.executable, "-m", "pip", "freeze", "--all"], capture_output=True, check=True)
        check = subprocess.run([sys.executable, "-m", "pip", "check"], capture_output=True, check=False)
        write_file(artifacts / ARTIFACTS["dependencies_freeze"], freeze.stdout)
        write_json(artifacts / ARTIFACTS["dependencies_check"], {
            "schema_version": "bb.rl.f1.command-observation.v1", "argv": ["python", "-m", "pip", "check"],
            "exit_code": check.returncode, "stdout": check.stdout.decode("utf-8"), "stderr": check.stderr.decode("utf-8"),
        })

        shutil.rmtree(fixture_root)
        write_json(artifacts / ARTIFACTS["private_cleanup"], {
            "schema_version": "bb.rl.f1.private-cleanup-observation.v1",
            "observations": [{"relative_path": "fixture", "lstat_errno": errno.ENOENT if not fixture_root.exists() else 0}],
        })
        directory_fd = os.open(artifacts, os.O_RDONLY)
        try: os.fsync(directory_fd)
        finally: os.close(directory_fd)

        actual = {path.name for path in artifacts.iterdir()}
        if actual != set(ARTIFACTS.values()):
            raise RuntimeError("target artifact inventory incomplete")
        private_paths = tuple(fixture.secret_paths.values())
        if args.secret_seed_file is not None:
            private_paths += (args.secret_seed_file,)
        reject_private_material(
            artifacts,
            (seed, *derived.values()),
            private_paths,
        )
        markers = []
        for sequence, (kind, name) in enumerate(ARTIFACTS.items()):
            raw = (artifacts / name).read_bytes()
            markers.append({
                "schema_version": MARKER_SCHEMA, "attempt_id": args.attempt_id, "sequence": sequence,
                "kind": kind, "artifact_path": name, "size_bytes": len(raw), "sha256": digest(raw),
            })
        archive = deterministic_archive(artifacts)
        for marker in markers:
            print("F1_ARTIFACT=" + canon(marker).decode())
        envelope = {"encoding": "base64", "size_bytes": len(archive), "sha256": digest(archive), "payload": base64.b64encode(archive).decode()}
        print("F1_RESULT_ARCHIVE=" + canon(envelope).decode())
        return 0
    finally:
        if harness is not None and harness.poll() is None:
            harness.kill()
            harness.wait()
        for fd in secret_fds:
            with contextlib.suppress(OSError): os.close(fd)
        shutil.rmtree(private, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
