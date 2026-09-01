"""Hermetic built-artifact observations for the F6 provider differential gate."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

ARTIFACT_ROW_IDS = (
    "artifact.wheel_provider_catalog_auth_role",
    "artifact.sdk_local_responses",
    "artifact.installed_end_to_end_trace",
)
_SAFE_ARTIFACT_AMBIENT_ENV = frozenset(
    {
        "COMSPEC",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PATH",
        "PATHEXT",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "SYSTEMROOT",
        "WINDIR",
    }
)
_CANARY = "F6_ARTIFACT_SECRET_CANARY_7b61d29f"


@dataclass(frozen=True, slots=True)
class ArtifactBundle:
    wheel: Path
    wheel_sha256: str
    python: Path
    sdk_tarball: Path
    sdk_sha256: str
    sdk_installed_files: int
    consumer_root: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_environment(*, home: Path | None = None) -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in _SAFE_ARTIFACT_AMBIENT_ENV
    }
    environment.update(
        {
            "NO_PROXY": "127.0.0.1,localhost,::1",
            "no_proxy": "127.0.0.1,localhost,::1",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "http://127.0.0.1:9",
            "PIP_NO_INDEX": "1",
            "UV_OFFLINE": "1",
            "npm_config_offline": "true",
            "npm_config_audit": "false",
            "npm_config_fund": "false",
            "PYTHONNOUSERSITE": "1",
        }
    )
    if home is not None:
        environment["HOME"] = str(home)
        environment["TMPDIR"] = str(home / "tmp")
    return environment


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
    timeout: float = 300,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        env=dict(environment) if environment is not None else None,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    if completed.returncode != 0:
        output = completed.stdout[-8_000:]
        if _CANARY in output:
            output = output.replace(_CANARY, "[redacted]")
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n{output}"
        )
    return completed


def _git_value(root: Path, *arguments: str) -> str:
    return _run(("git", *arguments), cwd=root, timeout=30).stdout.strip()


def build_artifact_bundle(
    candidate_root: Path,
    work_root: Path,
    *,
    expected_commit: str,
    expected_tree: str,
) -> ArtifactBundle:
    """Build and install the exact clean candidate without provider or package network access."""
    candidate_root = candidate_root.resolve()
    work_root = work_root.resolve()
    if _git_value(candidate_root, "rev-parse", "HEAD") != expected_commit:
        raise RuntimeError(
            "candidate HEAD does not match the requested F6 evidence commit"
        )
    if _git_value(candidate_root, "rev-parse", "HEAD^{tree}") != expected_tree:
        raise RuntimeError(
            "candidate tree does not match the requested F6 evidence tree"
        )
    _run(("git", "diff", "--quiet"), cwd=candidate_root, timeout=30)
    _run(("git", "diff", "--cached", "--quiet"), cwd=candidate_root, timeout=30)

    uv = shutil.which("uv")
    npm = shutil.which("npm")
    if uv is None or npm is None:
        raise RuntimeError("F6 artifact rows require uv and npm")
    uv_cache = Path(
        _run((uv, "cache", "dir"), cwd=candidate_root, timeout=30).stdout.strip()
    ).resolve()
    if not uv_cache.is_dir():
        raise RuntimeError("uv cache directory is unavailable for the offline build")

    npm_cache = Path(
        _run(
            (npm, "config", "get", "cache"), cwd=candidate_root, timeout=30
        ).stdout.strip()
    ).resolve()
    if not npm_cache.is_dir():
        raise RuntimeError("npm cache directory is unavailable for the offline build")
    work_root.mkdir(parents=True, exist_ok=False)
    home = work_root / "home"
    (home / "tmp").mkdir(parents=True)
    environment = _safe_environment(home=home)
    environment["UV_CACHE_DIR"] = str(uv_cache)
    environment["npm_config_cache"] = str(npm_cache)
    environment.update(
        {
            "BREADBOARD_BUILD_SOURCE_COMMIT": expected_commit,
            "BREADBOARD_BUILD_SOURCE_TREE": expected_tree,
            "BREADBOARD_BUILD_SOURCE_REPOSITORY": _git_value(
                candidate_root, "remote", "get-url", "origin"
            ),
        }
    )

    source_archive = work_root / "candidate-source.tar"
    _run(
        (
            "git",
            "archive",
            "--format=tar",
            "--output",
            str(source_archive),
            expected_commit,
        ),
        cwd=candidate_root,
        timeout=120,
    )
    python_source = work_root / "python-source"
    python_source.mkdir()
    shutil.unpack_archive(source_archive, python_source, format="tar")
    sdk_source = python_source / "sdk" / "ts"
    _run(
        (
            npm,
            "ci",
            "--ignore-scripts",
            "--offline",
            "--no-audit",
            "--no-fund",
        ),
        cwd=sdk_source,
        environment=environment,
        timeout=300,
    )

    wheel_root = work_root / "wheel"
    wheel_root.mkdir()
    _run(
        (uv, "build", "--wheel", "--offline", "--out-dir", str(wheel_root)),
        cwd=python_source,
        environment=environment,
        timeout=600,
    )
    wheels = sorted(wheel_root.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one wheel, found {len(wheels)}")
    wheel = wheels[0]

    venv_root = work_root / "venv"
    _run(
        (uv, "venv", "--seed", "--python", sys.executable, str(venv_root)),
        cwd=work_root,
        environment=environment,
        timeout=180,
    )
    python = venv_root / "bin" / "python"
    _run(
        (uv, "pip", "install", "--offline", "--python", str(python), str(wheel)),
        cwd=work_root,
        environment=environment,
        timeout=300,
    )
    installed_provenance = json.loads(
        _run(
            (
                str(python),
                "-c",
                (
                    "import json;"
                    "from breadboard_engine.api.cli_bridge.app import ENGINE_PROVENANCE;"
                    "print(json.dumps(ENGINE_PROVENANCE,sort_keys=True))"
                ),
            ),
            cwd=work_root,
            environment=environment,
            timeout=120,
        ).stdout
    )
    if installed_provenance.get("commit") != expected_commit:
        raise RuntimeError(
            "installed wheel provenance is not bound to the candidate commit"
        )
    if installed_provenance.get("dirty") is not False:
        raise RuntimeError("installed wheel provenance is not immutable")

    sdk_out = work_root / "sdk-pack"
    sdk_out.mkdir()
    _run(
        (npm, "run", "pack:canonical", "--", str(sdk_out)),
        cwd=sdk_source,
        environment=environment,
        timeout=300,
    )
    tarballs = sorted(sdk_out.glob("breadboard-sdk-*.tgz"))
    if len(tarballs) != 1:
        raise RuntimeError(f"expected one canonical SDK tarball, found {len(tarballs)}")
    sdk_tarball = tarballs[0]
    installed_manifest = Path(f"{sdk_tarball}.installed-files.json")
    installed_files = json.loads(installed_manifest.read_text(encoding="utf-8"))[
        "files"
    ]

    dependency_out = work_root / "sdk-dependencies"
    dependency_out.mkdir()
    _run(
        (
            npm,
            "pack",
            "--json",
            "--ignore-scripts",
            "--pack-destination",
            str(dependency_out),
            str(sdk_source / "node_modules" / "eventsource-parser"),
        ),
        cwd=sdk_source,
        environment=environment,
        timeout=120,
    )
    dependencies = sorted(dependency_out.glob("*.tgz"))
    if len(dependencies) != 1:
        raise RuntimeError("expected one packed eventsource-parser dependency")

    consumer_root = work_root / "sdk-consumer"
    consumer_root.mkdir()
    (consumer_root / "package.json").write_text(
        '{"name":"f6-artifact-consumer","private":true,"type":"module"}\n',
        encoding="utf-8",
    )
    _run(
        (
            npm,
            "install",
            "--ignore-scripts",
            "--offline",
            "--no-audit",
            "--no-fund",
            "--package-lock=false",
            str(dependencies[0]),
            str(sdk_tarball),
        ),
        cwd=consumer_root,
        environment=environment,
        timeout=180,
    )

    return ArtifactBundle(
        wheel=wheel,
        wheel_sha256=_sha256(wheel),
        python=python,
        sdk_tarball=sdk_tarball,
        sdk_sha256=_sha256(sdk_tarball),
        sdk_installed_files=len(installed_files),
        consumer_root=consumer_root,
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    body: Any = None,
    timeout: float = 5,
) -> Any:
    payload = None if body is None else _canonical(body).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=payload,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


@contextlib.contextmanager
def _installed_server(bundle: ArtifactBundle, root: Path) -> Iterator[str]:
    server_root = root / "server"
    server_root.mkdir()
    home = server_root / "home"
    (home / "tmp").mkdir(parents=True)
    workspace = server_root / "workspace"
    workspace.mkdir()
    port = _free_port()
    environment = _safe_environment(home=home)
    environment.update(
        {
            "BREADBOARD_CLI_HOST": "127.0.0.1",
            "BREADBOARD_CLI_PORT": str(port),
            "BREADBOARD_CLI_LOG_LEVEL": "warning",
            "BREADBOARD_PUBLIC_WORKSPACE": str(workspace),
            "BREADBOARD_SESSION_STATE_ROOT": str(server_root / "session-state"),
            "BREADBOARD_SESSION_EVENT_ROOT": str(server_root / "session-events"),
            "BREADBOARD_ENABLE_E4_API": "0",
            "RAY_SCE_LOCAL_MODE": "1",
        }
    )
    log_path = server_root / "server.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            (str(bundle.python), "-m", "breadboard_engine.api.cli_bridge.server"),
            cwd=server_root,
            env=environment,
            text=True,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        base_url = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        f"installed engine exited during startup: {log_path.read_text(encoding='utf-8')[-8000:]}"
                    )
                try:
                    _request_json(base_url, "/v1/auth/providers", timeout=0.5)
                    break
                except (OSError, urllib.error.URLError, json.JSONDecodeError):
                    time.sleep(0.1)
            else:
                raise RuntimeError("installed engine did not become ready")
            yield base_url
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    if _CANARY in log_path.read_text(encoding="utf-8"):
        raise RuntimeError("secret canary escaped into installed-engine logs")


def _roles_document() -> dict[str, Any]:
    return {
        "schema_version": "bb.model_roles.v1",
        "defaults": {
            "role": "default",
            "known_but_unbound_role": "error",
            "unknown_role": "error",
        },
        "roles": {
            "default": {
                "primary": {"provider_id": "mock", "model_id": "reference"},
                "fallbacks": [],
                "fallback_on": [],
            }
        },
        "dispatch": {"subagents": {}, "lanes": {"main": "default"}},
        "policy": {
            "allow_environment_overrides": False,
            "cross_provider_fallback": "forbidden",
            "account_failover": "forbidden",
        },
    }


def _write_sdk_probe(path: Path) -> None:
    path.write_text(
        """import { createCanonicalE4Client, createInternalBreadboardClient } from '@breadboard/sdk/internal';
const config = { baseUrl: process.env.F6_BASE_URL, requestTimeoutMs: 30000 };
const client = createInternalBreadboardClient(config);
const sessions = createCanonicalE4Client(config);
const roles = JSON.parse(process.env.F6_ROLES);
const providers = await client.listProviders();
const credentials = await client.listCredentials();
const resolved = await client.resolveModelRoles({ model_roles: roles });
const output = {
  provider_ids: providers.map((row) => row.provider_id),
  auth_schemes: Object.fromEntries(providers.map((row) => [row.provider_id, row.auth_schemes])),
  credentials_empty: credentials.length === 0,
  role_schema: resolved.lock?.schema_version ?? null,
  role_route: resolved.lock?.roles?.default?.primary?.route_id ?? null,
  role_lock_hash: resolved.lock_hash ?? null,
};
if (process.env.F6_CONFIG_PATH) {
  const opened = await sessions.create({
    configPath: process.env.F6_CONFIG_PATH,
    task: '',
    stream: true,
  });
  const eventTypes = [];
  let exchangeRef = null;
  const eventsDone = (async () => {
    for await (const event of opened.events()) {
      eventTypes.push(event.kind);
      if (event.kind === 'turn_completed') {
        exchangeRef = event.payload.exchangeRef === null ? null : {
          exchange_id: event.payload.exchangeRef.exchangeId,
          schema_version: event.payload.exchangeRef.schemaVersion,
        };
        break;
      }
    }
  })();
  await opened.submit('Emit <promise>COMPLETE</promise>.');
  await Promise.race([
    eventsDone,
    new Promise((_, reject) => setTimeout(() => reject(new Error('turn timeout')), 45000)),
  ]);
  const summary = await opened.snapshot();
  await opened.close();
  output.session = {
    session_id: opened.sessionId,
    status: summary.status,
    event_types: eventTypes,
    exchange_ref: exchangeRef,
  };
}
process.stdout.write(JSON.stringify(output));
""",
        encoding="utf-8",
    )


def _run_sdk_probe(
    bundle: ArtifactBundle,
    base_url: str,
    *,
    roles: Mapping[str, Any],
    config_path: Path | None,
) -> dict[str, Any]:
    probe = bundle.consumer_root / "f6-probe.mjs"
    _write_sdk_probe(probe)
    environment = _safe_environment(home=bundle.consumer_root)
    environment.update(
        {
            "F6_BASE_URL": base_url,
            "F6_ROLES": _canonical(roles),
        }
    )
    if config_path is not None:
        environment["F6_CONFIG_PATH"] = str(config_path)
    completed = _run(
        ("node", str(probe)),
        cwd=bundle.consumer_root,
        environment=environment,
        timeout=75,
    )
    if _CANARY in completed.stdout:
        raise RuntimeError("secret canary escaped through the packed SDK")
    return json.loads(completed.stdout)


def _installed_exchange_projection(
    bundle: ArtifactBundle, conversation: Path
) -> dict[str, Any]:
    script = """import json, sys
from pathlib import Path
from breadboard_engine.provider.contracts import encode_provider_exchange

def walk(value):
    if isinstance(value, dict):
        if value.get('schema_version') == 'bb.provider_exchange.v2':
            yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)

data = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
items = list(walk(data))
if not items:
    raise SystemExit('provider exchange missing')
exchange = encode_provider_exchange(items[-1])
events = exchange['events']
stream_text = ''.join(
    event.get('delta', '') for event in events if event['kind'] == 'text_delta'
)
assistant_text = ''.join(
    block.get('text', '')
    for message in exchange['terminal'].get('assistant_messages', [])
    for block in message.get('content', [])
    if block.get('type') == 'text'
)
print(json.dumps({
    'schema_version': exchange['schema_version'],
    'exchange_id': exchange['exchange_id'],
    'provider_id': exchange['provider']['provider_id'],
    'runtime_id': exchange['provider']['runtime_id'],
    'request_roles': [message['role'] for message in exchange['request']['messages']],
    'events': events,
    'event_kinds': [event['kind'] for event in events],
    'stream_text': stream_text,
    'assistant_text': assistant_text,
    'terminal_kind': exchange['terminal']['kind'],
    'finish_reason': exchange['terminal'].get('finish_reason'),
    'output_emitted': exchange['terminal']['output_emitted'],
    'usage': exchange['terminal'].get('usage'),
}, sort_keys=True, separators=(',', ':')))
"""
    environment = _safe_environment(home=bundle.consumer_root)
    completed = _run(
        (str(bundle.python), "-c", script, str(conversation)),
        cwd=bundle.consumer_root,
        environment=environment,
        timeout=30,
    )
    return json.loads(completed.stdout)


def observe_artifact_rows(
    bundle: ArtifactBundle, work_root: Path
) -> tuple[dict[str, Any], ...]:
    """Exercise all three built-artifact rows through one installed loopback engine."""
    work_root = work_root.resolve()
    runtime_root = work_root / "runtime"
    runtime_root.mkdir()
    config_path = runtime_root / "smoke-config.json"
    config_path.write_text(
        _canonical(
            {
                "version": 2,
                "workspace": {"root": str(runtime_root / "workspace")},
                "providers": {
                    "default_model": "smoke/probe",
                    "models": [
                        {
                            "id": "smoke/probe",
                            "adapter": "smoke_chat",
                            "metadata": {"input": ["text"]},
                        }
                    ],
                },
                "modes": [
                    {"name": "build", "prompt": "F6 provider-free artifact trace"}
                ],
                "loop": {"sequence": [{"mode": "build"}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (runtime_root / "workspace").mkdir()

    with _installed_server(bundle, runtime_root) as base_url:
        providers = _request_json(base_url, "/v1/auth/providers")
        credentials = _request_json(base_url, "/v1/auth/credentials")
        role = _request_json(
            base_url,
            "/v1/model-roles/resolve",
            method="POST",
            body={"model_roles": _roles_document()},
        )
        sdk = _run_sdk_probe(
            bundle,
            base_url,
            roles=_roles_document(),
            config_path=config_path,
        )

    provider_ids = [row["provider_id"] for row in providers]
    role_lock = role["lock"]
    python_observed = {
        "package_origin": "installed_wheel",
        "provider_ids": provider_ids,
        "auth_schemes": {row["provider_id"]: row["auth_schemes"] for row in providers},
        "credentials_empty": credentials == [],
        "role_schema": role_lock["schema_version"],
        "role_route": role_lock["roles"]["default"]["primary"]["route_id"],
        "role_lock_hash": role["lock_hash"],
    }
    sdk_observed = {
        key: sdk[key]
        for key in (
            "provider_ids",
            "auth_schemes",
            "credentials_empty",
            "role_schema",
            "role_route",
            "role_lock_hash",
        )
    }
    if sdk_observed != {key: python_observed[key] for key in sdk_observed}:
        raise RuntimeError(
            "packed SDK did not preserve installed engine auth/role responses"
        )

    session = sdk.get("session")
    if not isinstance(session, Mapping):
        raise RuntimeError("packed SDK session observation is missing")
    conversations = sorted(
        (runtime_root / "server" / "logging").glob("*/meta/conversation_ir.json")
    )
    if len(conversations) != 1:
        raise RuntimeError(
            f"installed session produced {len(conversations)} conversation traces"
        )
    exchange = _installed_exchange_projection(bundle, conversations[0])
    exchange_ref = session.get("exchange_ref")
    exchange_ref_matches = bool(
        isinstance(exchange_ref, Mapping)
        and exchange_ref.get("exchange_id") == exchange["exchange_id"]
        and exchange_ref.get("schema_version") == exchange["schema_version"]
    )
    end_to_end = {
        "transport": "loopback_http_sse",
        "terminal_event_observed": "turn_completed" in session["event_types"],
        "exchange_ref_matches": exchange_ref_matches,
        "provider_id": exchange["provider_id"],
        "runtime_id": exchange["runtime_id"],
        "request_roles": exchange["request_roles"],
        "events": exchange["events"],
        "event_kinds": exchange["event_kinds"],
        "stream_text": exchange["stream_text"],
        "assistant_text": exchange["assistant_text"],
        "terminal_kind": exchange["terminal_kind"],
        "finish_reason": exchange["finish_reason"],
        "output_emitted": exchange["output_emitted"],
        "usage": exchange["usage"],
    }

    serialized = _canonical(
        {"python": python_observed, "sdk": sdk_observed, "end_to_end": end_to_end}
    )
    if _CANARY in serialized:
        raise RuntimeError("secret canary escaped into artifact observations")
    evidence = [
        f"wheel:sha256:{bundle.wheel_sha256}",
        f"packed-sdk:sha256:{bundle.sdk_sha256}",
    ]
    return (
        {
            "row_id": ARTIFACT_ROW_IDS[0],
            "subject": "installed Python engine",
            "claim": "source-free wheel serves provider catalog, auth, and role responses",
            "observed": python_observed,
            "evidence": evidence,
        },
        {
            "row_id": ARTIFACT_ROW_IDS[1],
            "subject": "packed TypeScript SDK",
            "claim": "clean installed SDK consumes exact loopback provider, auth, and role responses",
            "observed": sdk_observed,
            "evidence": evidence + [f"installed-files:{bundle.sdk_installed_files}"],
        },
        {
            "row_id": ARTIFACT_ROW_IDS[2],
            "subject": "installed wheel plus packed SDK",
            "claim": "provider-free session exposes one strict provider stream and terminal trace",
            "observed": end_to_end,
            "evidence": evidence + ["loopback:http+sse"],
        },
    )


__all__ = [
    "ARTIFACT_ROW_IDS",
    "ArtifactBundle",
    "build_artifact_bundle",
    "observe_artifact_rows",
]
