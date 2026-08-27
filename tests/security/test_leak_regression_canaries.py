"""C-G0d/C-G0e: per-path closure tests plus canary leak-regression suite."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from breadboard_engine.security import redaction

CANARY_ATTACH_KEY = "canary-attach-key-4f9e7c21ab"
CANARY_HEADER_TOKEN = "canary-header-token-8d2b6e90cd"
CANARY_RAW_HEADER = "canary-rawheader-1a5c3f77ee"
CANARY_LOG_VALUE = "canary-logvalue-6e0d9b42aa"
CANARY_TEXT_VALUE = "sk-canarytextvalue123456"


def _attach_via_broker(
    tmp_path: Path,
    provider_id: str,
    *,
    api_key: str,
    headers: dict[str, str] | None = None,
):
    from breadboard_engine.provider_broker import ProviderBroker, SQLiteCredentialStore

    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / f"{provider_id}.sqlite3"))
    broker.putApiKey(
        {
            "provider_id": provider_id,
            "account_label": f"canary-{provider_id}",
            "api_key": api_key,
            "headers": headers or {},
            "base_url": "https://example.invalid" if provider_id == "openai" else None,
        }
    )
    return broker


@pytest.fixture(autouse=True)
def _clean_registry():
    redaction.clear_registered_secret_values()
    yield
    redaction.clear_registered_secret_values()


def _assert_tree_clean(root: Path, *canaries: str) -> None:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        for canary in canaries:
            assert canary not in content, f"canary leaked into {path}"


class TestPath1BrokerCredentialBoundary:
    """Leak path 1: broker credentials never need process environment projection."""

    def test_execution_scope_registers_secrets_and_public_scrub_is_env_independent(
        self, tmp_path: Path, monkeypatch
    ):
        from breadboard_engine.api.public.models import scrub_public

        monkeypatch.setitem(os.environ, "OPENAI_API_KEY", "preexisting")
        monkeypatch.setitem(os.environ, "BREADBOARD_OPENAI_AUTH_HEADERS_JSON", "{}")
        monkeypatch.setitem(os.environ, "BREADBOARD_OPENAI_AUTH_BASE_URL", "")
        broker = _attach_via_broker(
            tmp_path,
            "openai",
            api_key=CANARY_ATTACH_KEY,
            headers={"chatgpt-account-id": CANARY_HEADER_TOKEN},
        )
        assert redaction.iter_registered_secret_values() == ()
        with broker.execution_material("openai"):
            assert CANARY_HEADER_TOKEN in redaction.iter_registered_secret_values()
            scrubbed = scrub_public(
                {"detail": f"boom {CANARY_ATTACH_KEY} and {CANARY_HEADER_TOKEN}"}
            )
            assert CANARY_ATTACH_KEY not in json.dumps(scrubbed)
            assert CANARY_HEADER_TOKEN not in json.dumps(scrubbed)
        assert redaction.iter_registered_secret_values() == ()

    def test_non_openai_material_is_scoped(self, tmp_path: Path):
        secret = "anthropic-canary-key-77aa"
        broker = _attach_via_broker(tmp_path, "anthropic", api_key=secret)
        assert redaction.iter_registered_secret_values() == ()
        with broker.execution_material("anthropic"):
            assert secret in redaction.iter_registered_secret_values()
        assert redaction.iter_registered_secret_values() == ()


class TestPath2RawHeaders:
    """Leak path: rate-limit parser persisted nearly all raw headers."""

    def test_raw_headers_sanitized_at_source(self):
        from breadboard_engine.limits.parse_headers import parse_rate_limit_headers

        snapshot = parse_rate_limit_headers(
            {
                "x-ratelimit-limit-requests": "100",
                "x-ratelimit-remaining-requests": "99",
                "authorization": f"Bearer {CANARY_RAW_HEADER}",
                "x-api-key": CANARY_RAW_HEADER,
                "cookie": f"session={CANARY_RAW_HEADER}",
            },
            provider="openai",
        )
        assert snapshot is not None
        assert snapshot["buckets"]
        assert CANARY_RAW_HEADER not in json.dumps(snapshot)
        assert snapshot["raw_headers"]["x-ratelimit-remaining-requests"] == "99"


class TestPath3RunLoggerKeylists:
    """Leak path: run logger recognized only four key spellings."""

    def _logger(self, tmp_path: Path):
        from breadboard_engine.run_logging import LoggerV2Manager

        manager = LoggerV2Manager({"logging": {"root_dir": str(tmp_path / "logs")}})
        manager.start_run("canary-session")
        return manager

    def test_wide_key_coverage_in_json(self, tmp_path):
        manager = self._logger(tmp_path)
        manager.write_json(
            "meta/canary.json",
            {
                "access_token": CANARY_LOG_VALUE,
                "refresh_token": CANARY_LOG_VALUE,
                "cookie": CANARY_LOG_VALUE,
                "id_token": CANARY_LOG_VALUE,
                "session_access_token": CANARY_LOG_VALUE,
                "kept": "fine",
            },
        )
        manager.append_jsonl("events/canary.jsonl", {"x-api-key": CANARY_LOG_VALUE})
        _assert_tree_clean(Path(manager.run_dir), CANARY_LOG_VALUE)

    def test_text_writers_no_longer_bypass_redaction(self, tmp_path):
        with redaction.secret_value_scope(CANARY_LOG_VALUE):
            manager = self._logger(tmp_path)
            manager.write_text("notes/trace.txt", f"exception with {CANARY_LOG_VALUE}")
            manager.append_text(
                "notes/trace.log", f"pattern {CANARY_TEXT_VALUE} leak\n"
            )
            _assert_tree_clean(
                Path(manager.run_dir), CANARY_LOG_VALUE, CANARY_TEXT_VALUE
            )


class TestPath4RecorderAndProviderDump:
    """Leak paths: API recorder and provider dump had drifting deny-lists."""

    def test_api_recorder_wide_coverage(self):
        from breadboard_engine.run_logging.api_recorder import _redact_payload

        sanitized = _redact_payload(
            {"headers": {"x-goog-api-key": CANARY_LOG_VALUE}, "body": "ok"}
        )
        assert CANARY_LOG_VALUE not in json.dumps(sanitized)
        assert sanitized["body"] == "ok"

    def test_provider_dump_wide_coverage(self):
        from breadboard_engine.logging.provider_dump import _scrub

        sanitized = _scrub(
            {"set-cookie": CANARY_LOG_VALUE, "detail": f"Bearer {CANARY_RAW_HEADER}"}
        )
        assert CANARY_LOG_VALUE not in json.dumps(sanitized)
        assert CANARY_RAW_HEADER not in json.dumps(sanitized)

    def test_provider_dump_omits_native_image_payloads(self):
        from breadboard_engine.logging.provider_dump import _scrub

        encoded = base64.b64encode(b"private image").decode("ascii")
        sanitized = _scrub(
            {
                "openai": f"data:image/png;base64,{encoded}",
                "anthropic": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": encoded,
                },
            }
        )
        serialized = json.dumps(sanitized)
        assert encoded not in serialized
        assert serialized.count("[binary media omitted]") == 2

    def test_provider_dump_scrubs_text_and_omits_binary(self, tmp_path, monkeypatch):
        from breadboard_engine.logging.provider_dump import ProviderDumpLogger

        monkeypatch.setenv("KC_PROVIDER_LOG_DIR", str(tmp_path / "provider-dump"))
        logger = ProviderDumpLogger()
        encoded = base64.b64encode(CANARY_LOG_VALUE.encode()).decode()
        with redaction.secret_value_scope(CANARY_LOG_VALUE):
            logger.log_response(
                provider="openai",
                model="gpt-test",
                request_id="request-canary",
                status_code=500,
                headers={"Authorization": CANARY_LOG_VALUE},
                content_type="application/octet-stream",
                body_text=f"failure {CANARY_LOG_VALUE}",
                body_base64=encoded,
                context=None,
                metadata={"detail": CANARY_LOG_VALUE},
            )

        payload = (
            tmp_path / "provider-dump" / "request-canary_response.json"
        ).read_text()
        assert CANARY_LOG_VALUE not in payload
        assert encoded not in payload
        assert '"binaryOmitted": true' in payload


class TestCanarySweep:
    """C-G0e: one combined run injecting canaries through every historic path."""

    def test_end_to_end_zero_occurrences(self, tmp_path, monkeypatch):
        from breadboard_engine.api.public.models import scrub_public
        from breadboard_engine.limits.parse_headers import parse_rate_limit_headers
        from breadboard_engine.run_logging.api_recorder import APIRequestRecorder
        from breadboard_engine.run_logging import LoggerV2Manager

        monkeypatch.setitem(os.environ, "OPENAI_API_KEY", "preexisting")
        monkeypatch.setitem(os.environ, "BREADBOARD_OPENAI_AUTH_HEADERS_JSON", "{}")
        monkeypatch.setitem(os.environ, "BREADBOARD_OPENAI_AUTH_BASE_URL", "")
        canaries = (
            CANARY_ATTACH_KEY,
            CANARY_HEADER_TOKEN,
            CANARY_RAW_HEADER,
            CANARY_LOG_VALUE,
            CANARY_TEXT_VALUE,
        )

        broker = _attach_via_broker(
            tmp_path,
            "openai",
            api_key=CANARY_ATTACH_KEY,
            headers={"chatgpt-account-id": CANARY_HEADER_TOKEN},
        )

        with broker.execution_material("openai"):
            manager = LoggerV2Manager({"logging": {"root_dir": str(tmp_path / "logs")}})
            manager.start_run("canary-e2e")
            recorder = APIRequestRecorder(manager)
            snapshot = parse_rate_limit_headers(
                {
                    "x-ratelimit-limit-requests": "10",
                    "x-api-key": CANARY_RAW_HEADER,
                    "retry-after": "1",
                },
                provider="openai",
            )
            manager.write_json("events/limits.json", snapshot)
            manager.append_jsonl(
                "events/stream.jsonl",
                {"access_token": CANARY_LOG_VALUE, "note": CANARY_ATTACH_KEY},
            )
            manager.write_text(
                "raw/error.txt",
                f"trace {CANARY_HEADER_TOKEN} {CANARY_TEXT_VALUE}",
            )
            recorder.save_request(1, {"headers": {"authorization": CANARY_RAW_HEADER}})
            recorder.save_response(1, {"cookie": CANARY_LOG_VALUE})

            value_scrub_canaries = (
                CANARY_ATTACH_KEY,
                CANARY_HEADER_TOKEN,
                CANARY_TEXT_VALUE,
            )
            public = scrub_public({"blob": " ".join(canaries)})
            assert not any(
                canary in json.dumps(public) for canary in value_scrub_canaries
            )
        _assert_tree_clean(Path(manager.run_dir), *canaries)


class TestChildEnvironmentBoundary:
    def test_allowlist_rejects_provider_credentials_and_store_location(self):
        from breadboard_engine.security import (
            build_child_environment,
            is_provider_credential_env_key,
        )

        source = {
            "PATH": "/usr/bin",
            "HOME": "/safe-home",
            "NODE_PATH": "prefix ambient-openai-canary suffix",
            "OPENAI_API_KEY": "ambient-openai-canary",
            "ANTHROPIC_API_KEY": "ambient-anthropic-canary",
            "BREADBOARD_OPENAI_AUTH_HEADERS_JSON": "ambient-header-canary",
            "BREADBOARD_CREDENTIAL_STORE_PATH": "/private/credential-store",
            "UNRELATED_SECRET": "not-allowlisted",
        }
        child = build_child_environment(
            source=source,
            overrides={"CI": "1"},
        )

        assert child == {
            "PATH": "/usr/bin",
            "HOME": "/safe-home",
            "CI": "1",
        }
        with pytest.raises(ValueError, match="credential material"):
            build_child_environment(
                source=source,
                overrides={"CI": "ambient-openai-canary"},
            )
        with pytest.raises(ValueError, match="not permitted"):
            build_child_environment(
                source=source,
                overrides={"SAFE_OVERRIDE": "kept"},
            )
        custom = build_child_environment(
            source=source,
            overrides={"SAFE_OVERRIDE": "kept"},
            allowed_override_keys=("SAFE_OVERRIDE",),
        )
        assert custom["SAFE_OVERRIDE"] == "kept"
        with pytest.raises(ValueError, match="not permitted"):
            build_child_environment(
                source=source,
                overrides={"OPENAI_API_KEY": "replacement"},
                allowed_override_keys=("OPENAI_API_KEY",),
            )
        assert is_provider_credential_env_key("BREADBOARD_CREDENTIAL_STORE_PATH")
        assert is_provider_credential_env_key("BREADBOARD_CREDENTIAL_DB")

    def test_ray_runtime_controls_survive_sanitization(self):
        from breadboard_engine.security import build_child_environment

        ray_environment = {
            "RAY_BACKEND_LOG_LEVEL": "error",
            "RAY_LOG_TO_DRIVER": "0",
            "RAY_LOGGER_LEVEL": "error",
            "RAY_LOG_TO_STDERR": "0",
            "RAY_ROTATION_BACKUP_COUNT": "1",
            "RAY_ROTATION_MAX_BYTES": "262144",
            "RAY_TMPDIR": "/private/ephemeral-ray",
        }
        child = build_child_environment(
            source={
                **ray_environment,
                "OPENAI_API_KEY": "ambient-openai-canary",
            }
        )

        assert child == ray_environment

    def test_hidden_credentials_restore_after_exception(self):
        from breadboard_engine.security import provider_credentials_hidden

        environment = {
            "PATH": "/usr/bin",
            "NODE_PATH": "prefix restore-openai-canary suffix",
            "OPENAI_API_KEY": "restore-openai-canary",
            "BREADBOARD_CREDENTIAL_STORE_PATH": "/private/credential-store",
        }
        with pytest.raises(RuntimeError, match="probe failed"):
            with provider_credentials_hidden(environment):
                assert environment == {"PATH": "/usr/bin"}
                environment["ANTHROPIC_API_KEY"] = "transient-anthropic-canary"
                environment["CI"] = "transient-anthropic-canary"
                raise RuntimeError("probe failed")

        assert environment == {
            "PATH": "/usr/bin",
            "NODE_PATH": "prefix restore-openai-canary suffix",
            "OPENAI_API_KEY": "restore-openai-canary",
            "BREADBOARD_CREDENTIAL_STORE_PATH": "/private/credential-store",
        }

    def test_ray_start_environment_is_allowlisted_and_restored(self):
        from breadboard_engine.security import sanitized_process_environment

        environment = {
            "PATH": "/usr/bin",
            "NODE_PATH": "prefix ray-openai-canary suffix",
            "OPENAI_API_KEY": "ray-openai-canary",
            "UNCLASSIFIED": "must-not-cross",
        }
        with sanitized_process_environment(
            environment,
            overrides={"RAY_DISABLE_DASHBOARD": "1"},
        ):
            assert environment == {
                "PATH": "/usr/bin",
                "RAY_DISABLE_DASHBOARD": "1",
            }
        assert environment == {
            "PATH": "/usr/bin",
            "NODE_PATH": "prefix ray-openai-canary suffix",
            "OPENAI_API_KEY": "ray-openai-canary",
            "UNCLASSIFIED": "must-not-cross",
        }

    def test_remote_initialization_never_falls_back_local(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        import breadboard_engine.agent as agent_module

        monkeypatch.setattr(
            agent_module.AgenticCoder,
            "_load_config",
            lambda _self: {"workspace": {"root": str(tmp_path / "workspace")}},
        )
        monkeypatch.setattr(agent_module, "_get_ray", lambda: None)
        agent = agent_module.AgenticCoder(
            "unused.json",
            workspace_dir=str(tmp_path / "workspace"),
        )

        with pytest.raises(RuntimeError, match="Remote execution requested"):
            agent.initialize()

        assert agent._local_mode is False
        assert agent.agent is None

    def test_sandbox_actor_implementation_and_child_hide_ambient_credentials(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        from breadboard.sandbox import DevSandboxV2

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        canaries = {
            "OPENAI_API_KEY": "actor-openai-canary-91e4",
            "NODE_PATH": "prefix actor-openai-canary-91e4 suffix",
            "ANTHROPIC_API_KEY": "actor-anthropic-canary-36bd",
            "BREADBOARD_OPENAI_AUTH_HEADERS_JSON": "actor-header-canary-77ca",
            "BREADBOARD_CREDENTIAL_STORE_PATH": str(
                tmp_path / "protected" / "credentials-canary.sqlite3"
            ),
        }
        for key, value in canaries.items():
            monkeypatch.setenv(key, value)

        sandbox_class = DevSandboxV2.__ray_metadata__.modified_class
        sandbox = sandbox_class(
            image="python-dev:latest",
            session_id="e3-actor-probe",
            workspace=str(workspace),
        )

        assert sandbox.provider_environment_is_clean() is True
        child = sandbox.run_shell("env", stream=False)
        output = json.dumps(child)
        assert child["exit"] == 0, child
        for key, value in canaries.items():
            assert os.environ.get(key) is None
            assert f"{key}=" not in output
            assert value not in output

    def test_sandbox_rejects_provider_credential_in_process_input(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        from breadboard.sandbox import DevSandboxV2

        canary = "sandbox-argv-canary-48e2"
        monkeypatch.setenv("OPENAI_API_KEY", canary)
        sandbox_class = DevSandboxV2.__ray_metadata__.modified_class
        sandbox = sandbox_class(
            image="python-dev:latest",
            session_id="e3-local-input-probe",
            workspace=str(tmp_path),
            purge_process_environment=False,
        )

        result = sandbox.run_shell(
            f"printf %s {canary}",
            stdin_data=canary,
            stream=False,
        )

        assert result["exit"] == 126
        assert canary not in json.dumps(result)

    def test_sandbox_filesystem_blocks_same_uid_credential_read(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        from breadboard.sandbox import DevSandboxV2

        canary = "sandbox-filesystem-canary-a11c"
        protected = tmp_path / "protected"
        protected.mkdir()
        credential_file = protected / "credentials.sqlite3"
        credential_file.write_text(canary, encoding="utf-8")
        workspace = tmp_path / "workspace"
        monkeypatch.setenv(
            "BREADBOARD_CREDENTIAL_STORE_PATH",
            str(credential_file),
        )
        workspace.mkdir()
        (workspace / "credential-link").symlink_to(credential_file)

        sandbox_class = DevSandboxV2.__ray_metadata__.modified_class
        sandbox = sandbox_class(
            image="python-dev:latest",
            session_id="e3-filesystem-probe",
            workspace=str(workspace),
            purge_process_environment=False,
        )

        direct = sandbox.run_shell(
            f"cat {credential_file}",
            stream=False,
        )
        linked = sandbox.run_shell(
            "cat credential-link",
            stream=False,
        )
        allowed = sandbox.run_shell(
            "printf allowed > inside.txt",
            stream=False,
        )

        assert allowed["exit"] == 0, allowed
        assert (workspace / "inside.txt").read_text(encoding="utf-8") == "allowed"
        unsafe_workspace = tmp_path / "unsafe-workspace"
        unsafe_workspace.mkdir()
        unsafe_store = unsafe_workspace / "credentials.sqlite3"
        unsafe_store.write_text(canary, encoding="utf-8")
        monkeypatch.setenv(
            "BREADBOARD_CREDENTIAL_STORE_PATH",
            str(unsafe_store),
        )
        unsafe = sandbox_class(
            image="python-dev:latest",
            session_id="e3-overlap-probe",
            workspace=str(unsafe_workspace),
            purge_process_environment=False,
        )
        overlap = unsafe.run_shell("cat credentials.sqlite3", stream=False)
        assert overlap["exit"] == 126
        assert canary not in json.dumps(overlap)
        assert unsafe.read_text("credentials.sqlite3").get("error") == (
            "path_outside_workspace"
        )
        assert direct["exit"] != 0
        assert linked["exit"] != 0
        assert canary not in json.dumps((allowed, direct, linked))

    def test_sandbox_rejects_protected_hardlinks_before_process_launch(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        from breadboard.sandbox import DevSandboxV2

        canary = "sandbox-hardlink-canary-e7"
        protected = tmp_path / "protected"
        protected.mkdir()
        credential_file = protected / "credentials.sqlite3"
        credential_file.write_text(canary, encoding="utf-8")
        monkeypatch.setenv(
            "BREADBOARD_CREDENTIAL_STORE_PATH",
            str(credential_file),
        )
        sandbox_class = DevSandboxV2.__ray_metadata__.modified_class

        prelinked_workspace = tmp_path / "prelinked-workspace"
        prelinked_workspace.mkdir()
        os.link(
            credential_file,
            prelinked_workspace / "credential-hardlink",
        )
        prelinked = sandbox_class(
            image="python-dev:latest",
            session_id="e7-hardlink-preexisting",
            workspace=str(prelinked_workspace),
            purge_process_environment=False,
        )
        rejected = prelinked.run_shell(
            "cat credential-hardlink",
            stream=False,
        )

        clean_workspace = tmp_path / "clean-workspace"
        clean_workspace.mkdir()
        clean = sandbox_class(
            image="python-dev:latest",
            session_id="e7-hardlink-create",
            workspace=str(clean_workspace),
            purge_process_environment=False,
        )
        create = clean.run_shell(
            f"ln {credential_file} credential-hardlink",
            stream=False,
        )

        assert rejected["exit"] == 126
        assert create["exit"] != 0
        assert not (clean_workspace / "credential-hardlink").exists()
        assert canary not in json.dumps((rejected, create))

    def test_hardlink_boundary_revalidates_workspace_contents(
        self,
        tmp_path: Path,
    ) -> None:
        from breadboard_engine.security.process_isolation import (
            ProcessIsolationUnavailable,
            validate_workspace_credential_boundary,
        )

        protected = tmp_path / "protected"
        protected.mkdir()
        credential_file = protected / "credentials.sqlite3"
        credential_file.write_text("credential-canary", encoding="utf-8")
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        validate_workspace_credential_boundary(
            workspace,
            protected_paths=(credential_file,),
        )
        os.link(credential_file, workspace / "late-hardlink")

        with pytest.raises(
            ProcessIsolationUnavailable,
            match="protected credential hardlink",
        ):
            validate_workspace_credential_boundary(
                workspace,
                protected_paths=(credential_file,),
            )

    def test_macos_sandbox_denies_network_and_parent_process_inspection(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        import socket
        import sys

        if sys.platform != "darwin":
            pytest.skip("macOS sandbox boundary")

        from breadboard.sandbox import DevSandboxV2

        canary = "parent-process-memory-canary-e7"
        monkeypatch.setenv("UNRELATED_PROCESS_CANARY", canary)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        sandbox = DevSandboxV2.__ray_metadata__.modified_class(
            image="python-dev:latest",
            session_id="e7-process-network",
            workspace=str(workspace),
            purge_process_environment=False,
        )
        listener = socket.socket()
        try:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            network = sandbox.run_shell(
                f"/usr/bin/nc -z 127.0.0.1 {port}",
                stream=False,
            )
        finally:
            listener.close()

        parent_pid = os.getpid()
        process_environment = sandbox.run_shell(
            f"/bin/ps eww -p {parent_pid}",
            stream=False,
        )
        process_memory = sandbox.run_shell(
            f"/usr/bin/vmmap {parent_pid}",
            stream=False,
        )
        allowed = sandbox.run_shell(
            "printf allowed > inside.txt",
            stream=False,
        )

        assert network["exit"] != 0
        assert process_environment["exit"] != 0
        assert process_memory["exit"] != 0
        assert allowed["exit"] == 0
        assert (workspace / "inside.txt").read_text() == "allowed"
        assert canary not in json.dumps(
            (network, process_environment, process_memory, allowed)
        )

    def test_macos_sandbox_allows_metadata_traversal_to_external_runtime(
        self,
        tmp_path: Path,
    ) -> None:
        import shutil
        import subprocess
        import sys

        if sys.platform != "darwin":
            pytest.skip("macOS sandbox boundary")

        from breadboard_engine.security import (
            build_child_environment,
            build_restricted_process_command,
        )

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        runtime = tmp_path / "runtime"
        runtime_bin = runtime / "bin"
        runtime_lib = runtime / "lib"
        runtime_bin.mkdir(parents=True)
        runtime_lib.mkdir()
        node = shutil.which("node")
        if node is None:
            pytest.skip("Node runtime is unavailable")
        target = runtime_lib / "tool"
        target.write_text(
            f'#!{node}\nconsole.log(require("fs").realpathSync(__filename));\n',
            encoding="utf-8",
        )
        target.chmod(0o755)
        executable = runtime_bin / "tool"
        executable.symlink_to(Path("..") / "lib" / "tool")
        environment = build_child_environment()
        command, child_environment = build_restricted_process_command(
            [str(executable)],
            workspace=workspace,
            working_directory=workspace,
            shell=False,
            environment=environment,
        )
        profile = command[2]
        assert "(allow sysctl-read)" not in profile
        assert 'sysctl-name-regex #"^hw\\."' in profile
        assert 'sysctl-name "kern.ostype"' in profile
        assert "kern.proc" not in profile
        assert "(allow mach-lookup)" not in profile
        assert "(allow ipc-posix*)" not in profile

        result = subprocess.run(
            command,
            cwd=workspace,
            env=child_environment,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(target)


    def test_macos_rejects_model_process_with_startup_credentials(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        from breadboard_engine.security import process_isolation

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        monkeypatch.setattr(
            process_isolation.platform,
            "system",
            lambda: "Darwin",
        )
        monkeypatch.setattr(
            process_isolation,
            "initial_provider_credential_keys",
            lambda: ("OPENAI_API_KEY",),
        )

        with pytest.raises(
            process_isolation.ProcessIsolationUnavailable,
            match="outside the startup environment",
        ):
            process_isolation.build_restricted_process_command(
                ["/usr/bin/true"],
                workspace=workspace,
                shell=False,
                environment={"PATH": "/usr/bin"},
            )

    def test_restricted_process_command_purges_late_provider_credentials(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        from breadboard_engine.security import process_isolation

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        monkeypatch.setattr(process_isolation.platform, "system", lambda: "Linux")
        secret = "late-provider-secret"

        _, child_environment = (
            process_isolation.build_restricted_process_command(
                ["/usr/bin/true"],
                workspace=workspace,
                shell=False,
                environment={
                    "PATH": "/usr/bin",
                    "OPENAI_API_KEY": secret,
                    "SECRET_ALIAS": secret,
                    "CUSTOM_RUNTIME_FLAG": "kept",
                },
            )
        )

        assert "OPENAI_API_KEY" not in child_environment
        assert "SECRET_ALIAS" not in child_environment
        assert child_environment["CUSTOM_RUNTIME_FLAG"] == "kept"
        assert child_environment["HOME"] == str(workspace)


    def test_linux_helper_loads_without_package_context(self) -> None:
        import subprocess
        import sys

        from breadboard_engine.security import process_isolation

        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(Path(process_isolation.__file__).resolve()),
                "--invalid",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

        assert result.returncode == 2
        assert "attempted relative import" not in result.stderr

    def test_linux_landlock_requires_truncate_mediation(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        from breadboard_engine.security import process_isolation

        monkeypatch.setattr(
            process_isolation.ctypes,
            "CDLL",
            lambda *_args, **_kwargs: object(),
        )
        monkeypatch.setattr(
            process_isolation,
            "_syscall",
            lambda *_args, **_kwargs: 2,
        )

        with pytest.raises(
            process_isolation.ProcessIsolationUnavailable,
            match="ABI v3",
        ):
            process_isolation._apply_linux_landlock(tmp_path, ())

    def test_linux_rejects_protected_path_beneath_read_root(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        from breadboard_engine.security import process_isolation

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        monkeypatch.setattr(process_isolation.platform, "system", lambda: "Linux")

        with pytest.raises(
            process_isolation.ProcessIsolationUnavailable,
            match="overlaps a Linux read root",
        ):
            process_isolation.build_restricted_process_command(
                "true",
                workspace=workspace,
                shell=True,
                environment={"PATH": "/usr/bin"},
                protected_paths=("/usr/local/share/breadboard-credential",),
            )

    def test_linux_wrapper_accepts_home_replaced_by_workspace(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        from breadboard_engine.security import process_isolation

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        monkeypatch.setattr(process_isolation.platform, "system", lambda: "Linux")
        argv, child_environment = process_isolation.build_restricted_process_command(
            "true",
            workspace=workspace,
            shell=True,
            environment={"PATH": "/usr/bin"},
            protected_paths=(tmp_path / "protected",),
        )
        monkeypatch.setenv("HOME", child_environment["HOME"])

        workspace_arg_index = argv.index("--workspace")
        parsed_workspace, command = process_isolation._parse_args(
            argv[workspace_arg_index:]
        )
        assert parsed_workspace == workspace.resolve()
        assert command == ("/bin/bash", "-lc", "true")

    def test_restricted_builder_retains_ambient_store_after_sanitizing_environment(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        from breadboard_engine.security import (
            ProcessIsolationUnavailable,
            build_child_environment,
            build_restricted_process_command,
        )

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        store = workspace / "custom-credentials.sqlite3"
        monkeypatch.setenv("BREADBOARD_CREDENTIAL_STORE_PATH", str(store))
        child_environment = build_child_environment()
        assert "BREADBOARD_CREDENTIAL_STORE_PATH" not in child_environment

        with pytest.raises(
            ProcessIsolationUnavailable,
            match="overlaps a protected credential location",
        ):
            build_restricted_process_command(
                ("true",),
                workspace=workspace,
                shell=False,
                environment=child_environment,
            )


class TestE7CredentialBoundary:
    def test_custom_db_paths_propagate_across_sanitized_sandbox_hop(
        self, tmp_path: Path, monkeypatch
    ):
        from breadboard import sandbox_driver
        from breadboard_engine.security import (
            protected_credential_paths,
            sanitized_process_environment,
        )

        credential_db = tmp_path / "custom-credentials.sqlite3"
        parent_environment = {
            "BREADBOARD_CREDENTIAL_DB": str(credential_db),
            "HOME": str(tmp_path / "trusted-home"),
            "PATH": "/usr/bin",
        }
        protected_paths = tuple(
            str(path) for path in protected_credential_paths(parent_environment)
        )
        observed: dict[str, object] = {}

        class _Options:
            def remote(self, **kwargs):
                observed.update(kwargs)
                return kwargs

        class _Actor:
            @classmethod
            def options(cls, **_kwargs):
                return _Options()

        monkeypatch.setattr(sandbox_driver, "DevSandboxV2", _Actor)
        hop_environment = dict(parent_environment)
        with sanitized_process_environment(hop_environment):
            assert "BREADBOARD_CREDENTIAL_DB" not in hop_environment
            sandbox_driver.create_sandbox(
                sandbox_driver.SandboxLaunchSpec(
                    driver="process",
                    image="python-dev:latest",
                    workspace=str(tmp_path / "workspace"),
                    protected_paths=protected_paths,
                )
            )

        assert observed["protected_paths"] == protected_paths
        assert isinstance(observed["protected_paths"], tuple)

    @pytest.mark.parametrize("sandbox_kind", ("dev", "docker"))
    def test_explicit_parent_paths_protect_custom_db_after_locator_removal(
        self, sandbox_kind: str, tmp_path: Path
    ):
        from breadboard import sandbox as dev_module
        from breadboard import sandbox_docker as docker_module
        from breadboard_engine.security import (
            protected_credential_paths,
            sanitized_process_environment,
        )

        canary = f"custom-db-hop-canary-{sandbox_kind}-e7"
        credential_db = tmp_path / "protected" / "credentials.sqlite3"
        credential_db.parent.mkdir()
        credential_db.write_text(canary, encoding="utf-8")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        parent_environment = {
            "BREADBOARD_CREDENTIAL_DB": str(credential_db),
            "HOME": str(tmp_path / "trusted-home"),
            "PATH": "/usr/bin",
        }
        protected_paths = tuple(
            str(path) for path in protected_credential_paths(parent_environment)
        )
        actor_module = dev_module if sandbox_kind == "dev" else docker_module
        actor_type = (
            actor_module.DevSandboxV2
            if sandbox_kind == "dev"
            else actor_module.DockerSandboxV2
        )

        hop_environment = dict(parent_environment)
        with sanitized_process_environment(hop_environment):
            actor = actor_type.__ray_metadata__.modified_class(
                image="python-dev:latest",
                session_id=f"e7-custom-db-{sandbox_kind}",
                workspace=str(workspace),
                protected_paths=protected_paths,
                purge_process_environment=False,
            )
            assert "BREADBOARD_CREDENTIAL_DB" not in hop_environment
            file_result = actor.read_text(str(credential_db))
            shell_result = actor.run_shell(
                f"cat {credential_db}",
                stream=False,
            )

        assert canary not in json.dumps(file_result)
        assert shell_result["exit"] != 0
        assert canary not in json.dumps(shell_result)

    def test_credential_db_sidecars_are_in_protected_path_inventory(
        self, tmp_path: Path
    ):
        from breadboard_engine.security import protected_credential_paths

        credential_db = tmp_path / "credentials.sqlite3"
        inventory = set(
            protected_credential_paths({"BREADBOARD_CREDENTIAL_DB": str(credential_db)})
        )
        assert {
            credential_db.resolve(),
            Path(f"{credential_db}-wal").resolve(),
            Path(f"{credential_db}-shm").resolve(),
            Path(f"{credential_db}-journal").resolve(),
        } <= inventory

    @pytest.mark.parametrize("sandbox_kind", ("dev", "docker"))
    def test_host_file_apis_reject_symlink_and_hardlink_canary_reads(
        self, sandbox_kind: str, tmp_path: Path, monkeypatch
    ):
        from breadboard import sandbox as dev_module
        from breadboard import sandbox_docker as docker_module

        canary = f"host-file-link-canary-{sandbox_kind}-e7"
        protected = tmp_path / "protected"
        protected.mkdir()
        credential_db = protected / "credentials.sqlite3"
        credential_db.write_text(canary, encoding="utf-8")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "credential-link").symlink_to(credential_db)
        (workspace / "protected-link").symlink_to(protected, target_is_directory=True)
        os.link(credential_db, workspace / "credential-hardlink")
        monkeypatch.setenv("BREADBOARD_CREDENTIAL_DB", str(credential_db))

        actor_module = dev_module if sandbox_kind == "dev" else docker_module
        actor_type = (
            actor_module.DevSandboxV2
            if sandbox_kind == "dev"
            else actor_module.DockerSandboxV2
        )
        actor = actor_type.__ray_metadata__.modified_class(
            image="python-dev:latest",
            session_id=f"e7-link-{sandbox_kind}",
            workspace=str(workspace),
            purge_process_environment=False,
        )

        for path in ("credential-link", "credential-hardlink"):
            assert canary not in actor.get(path).decode("utf-8", errors="replace")
            assert canary not in json.dumps(actor.read_text(path))

        assert canary not in json.dumps(actor.grep(re.escape(canary), "protected-link"))
        assert canary not in json.dumps(actor.grep(re.escape(canary), "."))

    def test_docker_model_sandbox_rejects_host_network(
        self,
        tmp_path: Path,
    ) -> None:
        from breadboard import sandbox_docker
        from breadboard_engine.security import ProcessIsolationUnavailable

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        with pytest.raises(
            ProcessIsolationUnavailable,
            match="network must be disabled",
        ):
            sandbox_docker.DockerSandboxV2.__ray_metadata__.modified_class(
                image="python-dev:latest",
                session_id="e7-network",
                workspace=str(workspace),
                network="host",
                purge_process_environment=False,
            )

    def test_docker_run_shell_rejects_hardlink_canary_before_bind_mount(
        self, tmp_path: Path, monkeypatch
    ):
        from breadboard import sandbox_docker

        canary = "docker-bind-hardlink-canary-e7"
        protected = tmp_path / "protected"
        protected.mkdir()
        credential_db = protected / "credentials.sqlite3"
        credential_db.write_text(canary, encoding="utf-8")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        os.link(credential_db, workspace / "credential-hardlink")
        monkeypatch.setenv("BREADBOARD_CREDENTIAL_DB", str(credential_db))
        monkeypatch.setattr(
            sandbox_docker.subprocess,
            "run",
            lambda *args, **kwargs: SimpleNamespace(
                returncode=0, stdout=canary, stderr=""
            ),
        )
        actor = sandbox_docker.DockerSandboxV2.__ray_metadata__.modified_class(
            image="python-dev:latest",
            session_id="e7-docker-bind-hardlink",
            workspace=str(workspace),
            purge_process_environment=False,
        )

        result = actor.run_shell("cat credential-hardlink", stream=False)

        assert result["exit"] != 0
        assert canary not in json.dumps(result)

    @pytest.mark.parametrize("sandbox_kind", ("dev", "docker"))
    def test_host_file_apis_reject_ancestor_swap_canary_reads(
        self, sandbox_kind: str, tmp_path: Path, monkeypatch
    ):
        from breadboard import sandbox as dev_module
        from breadboard import sandbox_docker as docker_module

        canary = f"ancestor-swap-canary-{sandbox_kind}-e7"
        protected = tmp_path / "protected"
        protected.mkdir()
        (protected / "payload.txt").write_text(canary, encoding="utf-8")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        ancestor = workspace / "ancestor"
        ancestor.mkdir()
        victim = ancestor / "payload.txt"
        victim.write_text("workspace-only", encoding="utf-8")
        monkeypatch.setenv(
            "BREADBOARD_CREDENTIAL_DB", str(protected / "credentials.sqlite3")
        )

        actor_module = dev_module if sandbox_kind == "dev" else docker_module
        actor_type = (
            actor_module.DevSandboxV2
            if sandbox_kind == "dev"
            else actor_module.DockerSandboxV2
        )
        actor = actor_type.__ray_metadata__.modified_class(
            image="python-dev:latest",
            session_id=f"e7-ancestor-{sandbox_kind}",
            workspace=str(workspace),
            purge_process_environment=False,
        )

        original_resolve = Path.resolve
        swapped = False

        def resolve_with_ancestor_swap(path, *args, **kwargs):
            nonlocal swapped
            resolved = original_resolve(path, *args, **kwargs)
            if not swapped and path == victim:
                victim.unlink()
                ancestor.rmdir()
                ancestor.symlink_to(protected, target_is_directory=True)
                swapped = True
            return resolved

        monkeypatch.setattr(Path, "resolve", resolve_with_ancestor_swap)
        result = actor.get("ancestor/payload.txt")

        assert canary not in result.decode("utf-8", errors="replace")
        assert canary not in json.dumps(actor.grep(re.escape(canary), "ancestor"))

    def test_linux_builder_uses_isolated_python_trusted_helper_and_fixed_roots(
        self, tmp_path: Path, monkeypatch
    ):
        from breadboard_engine.security import process_isolation

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        tool_root = tmp_path / "tool-root"
        tool_root.mkdir()
        attacker_home = tmp_path / "attacker-home"
        replacement_home = tmp_path / "replacement-home"
        replacement_pythonpath = tmp_path / "replacement-pythonpath"
        parent_environment = {
            "PATH": "/usr/bin",
            "HOME": str(attacker_home),
            "PYTHONPATH": str(tool_root),
        }
        monkeypatch.setattr(process_isolation.platform, "system", lambda: "Linux")
        argv, child_environment = process_isolation.build_restricted_process_command(
            ("/bin/echo", "e7"),
            workspace=workspace,
            working_directory=workspace,
            shell=False,
            environment=parent_environment,
            protected_paths=(tmp_path / "protected",),
        )
        serialized_argv = "\x00".join(argv)
        helper = Path(argv[2])

        parent_environment["HOME"] = str(replacement_home)
        parent_environment["PYTHONPATH"] = str(replacement_pythonpath)
        assert argv[0] == process_isolation.sys.executable
        assert Path(argv[0]).is_absolute()
        assert argv[1] == "-I"
        assert helper.is_absolute()
        assert helper.name == "process_isolation.py"
        assert str(workspace.resolve()) in serialized_argv
        assert str(tool_root) in serialized_argv
        assert str(replacement_home) not in serialized_argv
        assert str(replacement_pythonpath) not in serialized_argv
        assert child_environment["HOME"] == str(workspace.resolve())

    def test_remote_broker_credential_url_never_crosses_child_environment(self):
        from breadboard_engine.security import build_child_environment

        broker_url = "https://broker.invalid/connect?token=e7-remote-canary"
        source = {
            "PATH": "/usr/bin",
            "BREADBOARD_AUTH_BROKER_URL": broker_url,
        }
        try:
            child = build_child_environment(source=source)
        except ValueError as exc:
            assert "broker" in str(exc).lower()
            return

        propagated = child.get("BREADBOARD_AUTH_BROKER_URL", "")
        assert propagated in {"remote", "configured", "sentinel"}
        assert broker_url not in propagated
        assert "e7-remote-canary" not in propagated

    def test_evaluator_subprocess_uses_restricted_builder_and_scrubs_canary(
        self, tmp_path: Path, monkeypatch
    ):
        from breadboard_engine.artifact_tasks import evaluators

        canary = "e7-evaluator-capture-canary"
        root = tmp_path / "workspace"
        root.mkdir()
        output_dir = tmp_path / "output"
        monkeypatch.setenv("OPENAI_API_KEY", canary)
        credential_store = tmp_path / "custom-evaluator-store.sqlite3"
        monkeypatch.setenv(
            "BREADBOARD_CREDENTIAL_STORE_PATH",
            str(credential_store),
        )
        builder_calls = []

        def builder(command, **kwargs):
            builder_calls.append((command, kwargs))
            return (("/trusted/evaluator-helper", "run"), {"PATH": "/usr/bin"})

        monkeypatch.setattr(
            evaluators,
            "build_restricted_process_command",
            builder,
            raising=False,
        )
        monkeypatch.setattr(
            evaluators.subprocess,
            "run",
            lambda *args, **kwargs: SimpleNamespace(
                returncode=0, stdout=canary, stderr=canary
            ),
        )
        result = evaluators.run_evaluator(
            evaluators.EvaluatorSpec(name="e7-evaluator", command=("true",)),
            root=root,
            output_dir=output_dir,
        )

        assert builder_calls
        observed_paths = {str(path) for path in builder_calls[0][1]["protected_paths"]}
        assert str(credential_store.resolve()) in observed_paths
        assert str(Path(f"{credential_store}-wal").resolve()) in observed_paths
        assert result.ok
        assert canary not in json.dumps(result.to_dict())
        assert canary not in (output_dir / "stdout.txt").read_text()
        assert canary not in (output_dir / "stderr.txt").read_text()

    def test_receipt_subprocess_uses_restricted_builder_and_scrubs_canary(
        self, tmp_path: Path, monkeypatch
    ):
        from breadboard_engine.conductor import implementation_receipts as receipts

        canary = "e7-receipt-capture-canary"
        monkeypatch.setenv("OPENAI_API_KEY", canary)
        builder_calls = []

        def builder(command, **kwargs):
            builder_calls.append((command, kwargs))
            return (("/trusted/receipt-helper", "run"), {"PATH": "/usr/bin"})

        class _Process:
            pid = 7
            returncode = 0

            def communicate(self, timeout):
                return canary, canary

        monkeypatch.setattr(
            receipts,
            "build_restricted_process_command",
            builder,
            raising=False,
        )
        monkeypatch.setattr(receipts.subprocess, "Popen", lambda *a, **k: _Process())
        result = receipts._run_subprocess_capture_with_group_timeout(
            ["true"],
            cwd=str(tmp_path),
            timeout=1,
        )

        assert builder_calls
        assert canary not in json.dumps(result)

    def test_longrun_verification_uses_restricted_builder_and_scrubs_canary(
        self, tmp_path: Path, monkeypatch
    ):
        import breadboard_engine.agent_llm_openai as agent_module

        canary = "e7-longrun-capture-canary"
        monkeypatch.setenv("OPENAI_API_KEY", canary)
        builder_calls = []

        def builder(command, **kwargs):
            builder_calls.append((command, kwargs))
            return (("/trusted/longrun-helper", "run"), {"PATH": "/usr/bin"})

        monkeypatch.setattr(
            agent_module,
            "build_restricted_process_command",
            builder,
            raising=False,
        )
        monkeypatch.setattr(
            agent_module.subprocess,
            "run",
            lambda *args, **kwargs: SimpleNamespace(
                returncode=0, stdout=canary, stderr=canary
            ),
        )
        agent_type = agent_module.OpenAIConductor.__ray_metadata__.modified_class
        agent = object.__new__(agent_type)
        agent.workspace = str(tmp_path)
        result = agent._run_longrun_verification_command(
            command="true",
            timeout_seconds=1,
        )

        assert builder_calls
        assert canary not in json.dumps(result)

    def test_checkpoint_git_uses_restricted_builder_and_scrubs_canary(
        self, tmp_path: Path, monkeypatch
    ):
        from breadboard_engine.checkpointing import checkpoint_manager

        canary = "e7-checkpoint-capture-canary"
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        monkeypatch.setenv("OPENAI_API_KEY", canary)
        builder_calls = []

        def builder(command, **kwargs):
            builder_calls.append((command, kwargs))
            return (("/trusted/checkpoint-helper", "run"), {"PATH": "/usr/bin"})

        monkeypatch.setattr(
            checkpoint_manager,
            "build_restricted_process_command",
            builder,
            raising=False,
        )
        monkeypatch.setattr(
            checkpoint_manager.subprocess,
            "run",
            lambda *args, **kwargs: SimpleNamespace(
                returncode=0, stdout=canary, stderr=""
            ),
        )
        result = checkpoint_manager.CheckpointManager(workspace)._run_git(["status"])

        assert builder_calls
        assert canary not in result

    def test_lsp_server_uses_restricted_builder_without_canary_environment(
        self, tmp_path: Path, monkeypatch
    ):
        import breadboard.lsp_manager as lsp_module

        canary = "e7-lsp-server-capture-canary"
        monkeypatch.setenv("OPENAI_API_KEY", canary)
        monkeypatch.setenv("LSP_USE_CONTAINERS", "0")
        builder_calls = []

        def builder(command, **kwargs):
            builder_calls.append((command, kwargs))
            return (("/trusted/lsp-helper", "run"), {"PATH": "/usr/bin"})

        class _Process:
            pass

        popen_calls = []

        def fake_popen(*args, **kwargs):
            popen_calls.append((args, kwargs))
            return _Process()

        monkeypatch.setattr(
            lsp_module,
            "build_restricted_process_command",
            builder,
            raising=False,
        )
        monkeypatch.setattr(lsp_module.subprocess, "Popen", fake_popen)
        server_type = lsp_module.LSPServer.__ray_metadata__.modified_class
        server = server_type("python", str(tmp_path))
        process = asyncio.run(server._spawn_server())

        assert process is not None
        assert popen_calls
        assert builder_calls
        assert canary not in json.dumps(popen_calls[0][1].get("env", {}))

    def test_container_lsp_validates_workspace_without_wrapping_docker(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        import breadboard.lsp_manager as lsp_module

        monkeypatch.setenv("LSP_USE_CONTAINERS", "1")
        server_type = lsp_module.LSPServer.__ray_metadata__.modified_class
        server = server_type("python", str(tmp_path))
        validated = []
        popen_calls = []
        monkeypatch.setattr(
            lsp_module,
            "validate_workspace_credential_boundary",
            lambda workspace, **kwargs: validated.append((workspace, kwargs)),
        )
        monkeypatch.setattr(
            lsp_module,
            "build_restricted_process_command",
            lambda *_args, **_kwargs: pytest.fail(
                "docker client must retain daemon connectivity"
            ),
        )
        monkeypatch.setattr(
            lsp_module.subprocess,
            "Popen",
            lambda *args, **kwargs: popen_calls.append((args, kwargs))
            or object(),
        )

        process = asyncio.run(server._spawn_server())

        assert process is not None
        assert validated == [
            (
                server.workspace_root,
                {"protected_paths": server._protected_paths},
            )
        ]
        assert popen_calls[0][0][0][:4] == (
            "docker",
            "run",
            "-i",
            "--rm",
        )


    def test_lsp_linter_uses_restricted_builder_and_scrubs_canary_output(
        self, tmp_path: Path, monkeypatch
    ):
        import breadboard.lsp_manager as lsp_module

        canary = "e7-lsp-linter-capture-canary"
        monkeypatch.setenv("OPENAI_API_KEY", canary)
        builder_calls = []

        def builder(command, **kwargs):
            builder_calls.append((command, kwargs))
            return (("/trusted/lsp-linter-helper", "run"), {"PATH": "/usr/bin"})

        monkeypatch.setattr(
            lsp_module,
            "build_restricted_process_command",
            builder,
            raising=False,
        )
        monkeypatch.setattr(
            lsp_module.subprocess,
            "run",
            lambda *args, **kwargs: SimpleNamespace(
                returncode=0,
                stdout=json.dumps([{"message": canary}]),
                stderr="",
            ),
        )
        runner_type = lsp_module.CLILinterRunner.__ray_metadata__.modified_class
        runner = object.__new__(runner_type)
        diagnostics = asyncio.run(runner.run_ruff(str(tmp_path / "probe.py")))

        assert builder_calls
        assert canary not in json.dumps(diagnostics)

    def test_web_scraper_actor_rejects_non_http_schemes_before_fetch(self):
        from breadboard_engine.web.ray_actors import WebScraperActor

        actor = WebScraperActor.__ray_metadata__.modified_class()
        calls = []

        class _Scraper:
            async def scrape_url(self, url, *, options=None):
                calls.append(url)
                return {"url": url, "text": "unexpected fetch"}

        actor.scraper = _Scraper()
        for url in ("file:///etc/passwd", "data:text/plain,canary", "unknown://host"):
            try:
                result = asyncio.run(actor.scrape_url(url))
            except Exception:
                continue
            assert result.get("text") != "unexpected fetch"
        assert calls == []

    def test_web_scraper_rejects_redirect_landing_on_non_http_scheme(self, monkeypatch):
        from breadboard_engine.web.scraper import WebScraper
        from breadboard_engine.web import scraper as scraper_module

        scraper = WebScraper()
        body_fetches = []

        async def headers(*args, **kwargs):
            return 302, {"location": "file:///tmp/e7-canary"}

        async def body(*args, **kwargs):
            body_fetches.append(args[0])
            return 200, {"content-type": "text/html"}, b"redirected canary"

        monkeypatch.setattr(scraper, "_fetch_headers", headers)
        monkeypatch.setattr(
            scraper_module.socket,
            "getaddrinfo",
            lambda *_args, **_kwargs: [
                (
                    scraper_module.socket.AF_INET,
                    scraper_module.socket.SOCK_STREAM,
                    6,
                    "",
                    ("93.184.216.34", 443),
                )
            ],
        )
        monkeypatch.setattr(scraper, "_fetch_bytes", body)
        result = asyncio.run(scraper.scrape_url("https://safe.invalid"))

        assert body_fetches == []
        assert result.text != "redirected canary"

    def test_artifact_contract_rejects_ancestor_swap_before_hashing(
        self, tmp_path: Path, monkeypatch
    ):
        from breadboard_engine.artifact_tasks.contracts import (
            ArtifactContract,
            ArtifactRequirement,
            validate_artifact_contract,
        )

        root = tmp_path / "workspace"
        root.mkdir()
        protected = tmp_path / "protected"
        protected.mkdir()
        (protected / "artifact.txt").write_text("credential-canary", encoding="utf-8")
        ancestor = root / "nested"
        ancestor.mkdir()
        victim = ancestor / "artifact.txt"
        victim.write_text("workspace", encoding="utf-8")
        original_resolve = Path.resolve
        swapped = False

        def resolve_and_swap(path, *args, **kwargs):
            nonlocal swapped
            resolved = original_resolve(path, *args, **kwargs)
            if not swapped and path == victim:
                victim.unlink()
                ancestor.rmdir()
                ancestor.symlink_to(protected, target_is_directory=True)
                swapped = True
            return resolved

        monkeypatch.setattr(Path, "resolve", resolve_and_swap)
        result = validate_artifact_contract(
            ArtifactContract(
                requirements=(ArtifactRequirement(path="nested/artifact.txt"),)
            ),
            root=root,
        )

        assert not result.ok
        assert all(not check.ok for check in result.checks)

    def test_evidence_copy_retains_admitted_file_across_ancestor_swap(
        self, tmp_path: Path, monkeypatch
    ):
        from breadboard_engine.artifact_tasks.contracts import (
            ArtifactCheck,
            ArtifactValidationResult,
        )
        from breadboard_engine.artifact_tasks.evidence import _copy_artifacts
        from breadboard_engine.security import WorkspaceFilesystem

        artifact_root = tmp_path / "workspace"
        artifact_root.mkdir()
        protected = tmp_path / "protected"
        protected.mkdir()
        protected_file = protected / "artifact.txt"
        protected_file.write_text("credential-canary", encoding="utf-8")
        ancestor = artifact_root / "nested"
        ancestor.mkdir()
        victim = ancestor / "artifact.txt"
        victim.write_text("workspace", encoding="utf-8")
        output_root = tmp_path / "output"
        output_root.mkdir()
        original_open = WorkspaceFilesystem._open_regular_parts
        swapped = False

        def open_and_swap(filesystem, parts):
            nonlocal swapped
            descriptor, metadata = original_open(filesystem, parts)
            if not swapped and filesystem.root == artifact_root.resolve():
                victim.unlink()
                ancestor.rmdir()
                ancestor.symlink_to(protected, target_is_directory=True)
                swapped = True
            return descriptor, metadata

        monkeypatch.setattr(
            WorkspaceFilesystem,
            "_open_regular_parts",
            open_and_swap,
        )
        with WorkspaceFilesystem(output_root) as output:
            output.create_directory("bundle")
            _copy_artifacts(
                output,
                Path("bundle"),
                artifact_root,
                ArtifactValidationResult(
                    ok=True,
                    checks=(
                        ArtifactCheck(
                            path="nested/artifact.txt",
                            required=True,
                            exists=True,
                            ok=True,
                        ),
                    ),
                    artifact_root=str(artifact_root),
                ),
            )

        copied = output_root / "bundle" / "artifacts" / "nested" / "artifact.txt"
        assert copied.read_text(encoding="utf-8") == "workspace"
        assert protected_file.read_text(encoding="utf-8") == ("credential-canary")

    def test_materialize_rejects_ancestor_swap_without_overwriting_canary(
        self, tmp_path: Path, monkeypatch
    ):
        from breadboard_engine.artifact_tasks.materialize import (
            MaterializationSpec,
            materialize_response_artifact,
        )

        root = tmp_path / "workspace"
        root.mkdir()
        protected = tmp_path / "protected"
        protected.mkdir()
        protected_target = protected / "result.txt"
        protected_target.write_text("credential-canary", encoding="utf-8")
        ancestor = root / "nested"
        ancestor.mkdir()
        victim = ancestor / "result.txt"
        original_resolve = Path.resolve
        swapped = False

        def resolve_and_swap(path, *args, **kwargs):
            nonlocal swapped
            resolved = original_resolve(path, *args, **kwargs)
            if not swapped and path == victim:
                ancestor.rmdir()
                ancestor.symlink_to(protected, target_is_directory=True)
                swapped = True
            return resolved

        monkeypatch.setattr(Path, "resolve", resolve_and_swap)
        result = materialize_response_artifact(
            "```text\nattacker overwrite\n```",
            MaterializationSpec(output_path="nested/result.txt", overwrite=True),
            root=root,
        )

        assert not result.ok
        assert protected_target.read_text(encoding="utf-8") == "credential-canary"


class TestPostInvocationBoundary:
    def _invoker(self, *, client_lease):
        from types import SimpleNamespace
        from unittest.mock import Mock

        from breadboard_engine.provider import ProviderInvoker

        route_health = Mock()
        route_health.is_circuit_open.return_value = False
        return ProviderInvoker(
            provider_metrics=Mock(),
            route_health=route_health,
            logger_v2=SimpleNamespace(run_dir=None),
            md_writer=SimpleNamespace(system=lambda message: message),
            retry_with_fallback=Mock(return_value=None),
            update_health_metadata=Mock(),
            set_last_latency=Mock(),
            set_html_detected=Mock(),
            client_lease=client_lease,
        )

    def _lease(self, broker, monkeypatch):
        from contextlib import contextmanager

        import breadboard_engine.provider_broker as provider_broker_module
        from breadboard_engine.provider.routing import provider_router

        monkeypatch.setattr(
            provider_broker_module,
            "get_provider_broker",
            lambda: broker,
        )

        @contextmanager
        def lease(route_id, _runtime):
            with provider_router.execution_client_config(
                route_id,
                endpoint_id="e3-post-invocation",
            ):
                yield object()

        return lease

    def test_generic_response_echo_is_clean_before_artifact_write(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        from types import SimpleNamespace
        from unittest.mock import Mock

        from breadboard_engine.provider_runtime import (
            ProviderMessage,
            ProviderResult,
            ProviderRuntimeContext,
        )
        from breadboard_engine.conductor.modes import (
            _record_raw_provider_response,
        )
        from breadboard_engine.run_logging import LoggerV2Manager
        from breadboard_engine.run_logging.api_recorder import APIRequestRecorder
        from breadboard_engine.state.session_state import SessionState

        canary = "opaque-post-invocation-canary-3b71"
        broker = _attach_via_broker(
            tmp_path,
            "openai",
            api_key=canary,
        )
        lease = self._lease(broker, monkeypatch)

        class RawResponse:
            def model_dump(self):
                return {
                    "debug": f"provider echoed {canary}",
                    "nested": {"trace": canary},
                }

        runtime_result = ProviderResult(
            messages=[
                ProviderMessage(
                    role="assistant",
                    content=f"answer {canary}",
                    raw_message={"debug": canary},
                    annotations={"trace": canary},
                )
            ],
            raw_response=RawResponse(),
            usage={"debug": canary},
            metadata={"trace": canary},
        )
        runtime_result.provider_replay = [
            {
                "provider_id": "openai",
                "schema_version": "openai.responses.v1",
                "replay_scope": "same_provider",
                "payload": {
                    "encrypted_content": f"provider replay echoed {canary}",
                    "item_id": f"item-{canary}",
                },
            }
        ]
        runtime = SimpleNamespace(
            descriptor=SimpleNamespace(
                provider_id="openai",
                runtime_id="openai_chat",
            ),
            invoke=Mock(return_value=runtime_result),
        )
        state = SessionState(workspace=".", image="cli", config={})
        result, _ = self._invoker(client_lease=lease).invoke(
            runtime=runtime,
            client=None,
            model="gpt-test",
            send_messages=[],
            tools_schema=None,
            stream_responses=False,
            runtime_context=ProviderRuntimeContext(
                session_state=state,
                agent_config={},
                session_id="canary-session",
                input_id="canary-input",
                turn_id="canary-turn",
            ),
            session_state=state,
            markdown_logger=Mock(),
            turn_index=1,
            route_id="openai/gpt-test",
        )

        assert redaction.iter_registered_secret_values() == ()
        assert canary not in json.dumps(result.raw_response)
        assert canary not in json.dumps(state.provider_metadata)
        assert canary not in json.dumps(result.provider_replay)

        manager = LoggerV2Manager(
            {"logging": {"root_dir": str(tmp_path / "post-invocation")}}
        )
        manager.start_run("e3-post-invocation")
        _record_raw_provider_response(
            SimpleNamespace(
                logger_v2=manager,
                api_recorder=APIRequestRecorder(manager),
            ),
            result,
            1,
        )
        manager.write_json("meta/session-provider.json", state.provider_metadata)
        manager.write_text(
            "conversation/conversation.md",
            result.messages[0].content or "",
        )
        _assert_tree_clean(Path(manager.run_dir), canary)

    def test_generic_exception_echo_is_clean_before_retry_processing(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        from types import SimpleNamespace
        from unittest.mock import Mock

        from breadboard_engine.provider_runtime import (
            ProviderRuntimeContext,
            ProviderRuntimeError,
        )
        from breadboard_engine.state.session_state import SessionState

        canary = "opaque-exception-canary-5e90"
        broker = _attach_via_broker(
            tmp_path,
            "openai",
            api_key=canary,
        )
        lease = self._lease(broker, monkeypatch)
        error = ProviderRuntimeError(
            f"provider failed with {canary}",
            details={"debug": canary},
            kind="adapter",
        )
        runtime = SimpleNamespace(
            descriptor=SimpleNamespace(
                provider_id="openai",
                runtime_id="openai_chat",
            ),
            invoke=Mock(side_effect=error),
        )
        state = SessionState(workspace=".", image="cli", config={})

        with pytest.raises(ProviderRuntimeError) as exc_info:
            self._invoker(client_lease=lease).invoke(
                runtime=runtime,
                client=None,
                model="gpt-test",
                send_messages=[],
                tools_schema=None,
                stream_responses=False,
                runtime_context=ProviderRuntimeContext(
                    session_state=state,
                    agent_config={},
                    session_id="canary-session",
                    input_id="canary-input",
                    turn_id="canary-turn",
                ),
                session_state=state,
                markdown_logger=Mock(),
                turn_index=1,
                route_id="openai/gpt-test",
            )

        assert exc_info.value is error
        assert redaction.iter_registered_secret_values() == ()
        assert canary not in str(error)
        assert canary not in json.dumps(error.details)
        assert canary not in json.dumps(state.provider_metadata)

    def test_arbitrary_runtime_exception_is_scrubbed_before_lease_release(
        self,
        tmp_path: Path,
        monkeypatch,
    ):
        from types import SimpleNamespace
        from unittest.mock import Mock

        from breadboard_engine.provider_runtime import (
            ProviderRuntimeContext,
            ProviderRuntimeError,
        )
        from breadboard_engine.state.session_state import SessionState

        canary = "opaque-runtime-crash-canary-6f23"
        broker = _attach_via_broker(
            tmp_path,
            "openai",
            api_key=canary,
        )
        lease = self._lease(broker, monkeypatch)
        error = RuntimeError(f"custom runtime crashed with {canary}")
        error.details = {"trace": canary}
        runtime = SimpleNamespace(
            descriptor=SimpleNamespace(
                provider_id="openai",
                runtime_id="custom_runtime",
            ),
            invoke=Mock(side_effect=error),
        )
        state = SessionState(workspace=".", image="cli", config={})

        with pytest.raises(ProviderRuntimeError) as exc_info:
            self._invoker(client_lease=lease).invoke(
                runtime=runtime,
                client=None,
                model="gpt-test",
                send_messages=[],
                tools_schema=None,
                stream_responses=False,
                runtime_context=ProviderRuntimeContext(
                    session_state=state,
                    agent_config={},
                    session_id="canary-session",
                    input_id="canary-input",
                    turn_id="canary-turn",
                ),
                session_state=state,
                markdown_logger=Mock(),
                turn_index=1,
                route_id="openai/gpt-test",
            )

        assert exc_info.value is not error
        assert str(exc_info.value) == "provider runtime failed"
        assert redaction.iter_registered_secret_values() == ()
        assert canary not in str(error)
        assert canary not in json.dumps(error.details)
