from __future__ import annotations

import builtins
import glob
import os
import socket
import tempfile
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from agentic_coder_prototype.compilation.bundle import (
    ManifestReader,
    build_dependency_closure,
    ingest_member_map,
)
from agentic_coder_prototype.compilation.contracts import (
    CompileErrorCode,
    CompileOptions,
    CompileStage,
    CompileTarget,
    ConfigCompileError,
    DependencyEdge,
    TaskContract,
    TaskEvidenceContract,
    TaskRetentionContract,
    TaskVerifierContract,
)
from agentic_coder_prototype.compilation.server_compiler import compile_config
from breadboard.rl.state import InMemoryCAS


_MINIMAL_CONFIG = b"""version: 2
workspace:
  root: workspace
providers:
  default_model: test-model
  models:
    - id: test-model
      adapter: openai
prompts:
  injection:
    system_order: []
    per_turn_order: []
modes:
  - id: build
    prompt: ''
loop:
  sequence: [build]
"""


def _options() -> CompileOptions:
    return CompileOptions(
        target=CompileTarget(
            runner_adapter_id="breadboard.conductor.v1",
            runtime_abi="breadboard.conductor.v1",
        ),
        task_contract=TaskContract(
            contract_id="test-task.v1",
            parameter_schema={"type": "object", "additionalProperties": False},
            artifacts=(),
            verifier=TaskVerifierContract(
                binding_id=None,
                input_artifact_ids=(),
                result_schema=None,
                timeout_ms=None,
            ),
            evidence=TaskEvidenceContract(
                required_event_types=(),
                required_artifact_ids=(),
            ),
            retention=TaskRetentionContract(
                retention_class_id="test",
                minimum_retention_seconds=None,
            ),
        ),
    )


def _reader() -> tuple[ManifestReader, object]:
    cas = InMemoryCAS()
    bundle = ingest_member_map(
        {"config.yaml": _MINIMAL_CONFIG},
        cas,
        entrypoints={"main": "config.yaml"},
        source_label="compiler-purity-vector",
    )
    closure = build_dependency_closure(bundle, root_entrypoint="main")
    return ManifestReader(cas=cas, bundle=bundle, closure=closure), closure


class _ForbiddenEnvironment(dict[str, str]):
    def __getitem__(self, key: str) -> str:
        raise AssertionError(f"ambient environment read attempted: {key}")

    def get(self, key: str, default: Any = None) -> Any:
        raise AssertionError(f"ambient environment read attempted: {key}")

    def __contains__(self, key: object) -> bool:
        raise AssertionError(f"ambient environment read attempted: {key}")

    def items(self):
        raise AssertionError("ambient environment enumeration attempted")


def test_compile_config_uses_only_manifest_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_reader, baseline_closure = _reader()
    expected = compile_config(baseline_reader, baseline_closure, _options()).canonical_bytes()
    guarded_reader, guarded_closure = _reader()

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError(f"ambient authority attempted: {args!r} {kwargs!r}")

    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", forbidden)
        guard.setattr(os, "getcwd", forbidden)
        guard.setattr(os, "getenv", forbidden)
        guard.setattr(os, "listdir", forbidden)
        guard.setattr(os, "scandir", forbidden)
        guard.setattr(os, "walk", forbidden)
        guard.setattr(os, "mkdir", forbidden)
        guard.setattr(os, "makedirs", forbidden)
        guard.setattr(os, "environ", _ForbiddenEnvironment())
        guard.setattr(Path, "home", forbidden)
        guard.setattr(Path, "open", forbidden)
        guard.setattr(Path, "read_bytes", forbidden)
        guard.setattr(Path, "read_text", forbidden)
        guard.setattr(Path, "write_bytes", forbidden)
        guard.setattr(Path, "write_text", forbidden)
        guard.setattr(Path, "exists", forbidden)
        guard.setattr(Path, "is_file", forbidden)
        guard.setattr(Path, "is_dir", forbidden)
        guard.setattr(Path, "resolve", forbidden)
        guard.setattr(Path, "absolute", forbidden)
        guard.setattr(Path, "expanduser", forbidden)
        guard.setattr(Path, "glob", forbidden)
        guard.setattr(Path, "rglob", forbidden)
        guard.setattr(Path, "iterdir", forbidden)
        guard.setattr(Path, "mkdir", forbidden)
        guard.setattr(glob, "glob", forbidden)
        guard.setattr(glob, "iglob", forbidden)
        guard.setattr(socket, "socket", forbidden)
        guard.setattr(socket, "create_connection", forbidden)
        guard.setattr(urllib.request, "urlopen", forbidden)
        guard.setattr(tempfile, "NamedTemporaryFile", forbidden)
        guard.setattr(tempfile, "TemporaryDirectory", forbidden)

        actual = compile_config(guarded_reader, guarded_closure, _options()).canonical_bytes()

    assert actual == expected


def test_compile_config_is_invariant_to_cwd_home_and_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first_reader, first_closure = _reader()
    first = compile_config(first_reader, first_closure, _options()).canonical_bytes()

    other_cwd = tmp_path / "other-cwd"
    other_home = tmp_path / "other-home"
    other_cwd.mkdir()
    other_home.mkdir()
    monkeypatch.chdir(other_cwd)
    monkeypatch.setenv("HOME", str(other_home))
    monkeypatch.setenv("AGENT_SCHEMA_V2_ENABLED", "hostile")
    monkeypatch.setenv("BREADBOARD_PLUGIN_DIRS", str(tmp_path / "plugins"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))

    second_reader, second_closure = _reader()
    second = compile_config(second_reader, second_closure, _options()).canonical_bytes()

    assert second == first


def test_compile_config_does_not_read_or_write_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader, closure = _reader()

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("compiler attempted cache I/O")

    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "glob", forbidden)
    monkeypatch.setattr(Path, "rglob", forbidden)

    manifest = compile_config(reader, closure, _options())
    assert manifest.inputs.compiler_input_digest.startswith("sha256:")


def test_compile_config_does_not_open_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader, closure = _reader()

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("compiler attempted network I/O")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)

    manifest = compile_config(reader, closure, _options())
    assert manifest.semantic.providers["default_model_id"] == "test-model"


def test_admitted_template_is_denied_without_ambient_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _MINIMAL_CONFIG.replace(
        b"prompts:\n",
        b"prompts:\n  tool_catalog: {source: templates/catalog.j2}\n",
    )
    template = b"{{ cycler.__init__.__globals__.os.popen('id').read() }}"
    cas = InMemoryCAS()
    bundle = ingest_member_map(
        {"config.yaml": payload, "templates/catalog.j2": template},
        cas,
        entrypoints={"main": "config.yaml"},
        source_label="hostile-template-vector",
    )
    closure = build_dependency_closure(
        bundle,
        root_entrypoint="main",
        edges=(
            DependencyEdge(
                "config.yaml",
                "prompt_template",
                "templates/catalog.j2",
                "templates/catalog.j2",
                0,
            ),
        ),
    )
    reader = ManifestReader(cas=cas, bundle=bundle, closure=closure)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError(f"template reached ambient authority: {args!r} {kwargs!r}")

    monkeypatch.setattr(os, "popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(os, "getenv", forbidden)
    monkeypatch.setattr(os, "environ", _ForbiddenEnvironment())
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)

    with pytest.raises(ConfigCompileError) as caught:
        compile_config(reader, closure, _options())

    assert caught.value.stage is CompileStage.RENDER
    assert caught.value.code is CompileErrorCode.PROMPT_TEMPLATE_INVALID
    assert caught.value.logical_path == "templates/catalog.j2"
