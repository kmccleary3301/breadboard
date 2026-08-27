from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

import yaml

from scripts import breadboard_cli
from breadboard.product.operations import harness as harness_operations
from breadboard.product.harness import resolution as harness_resolution
from breadboard.product.harness.lock import sha256_bytes
from breadboard.product.evidence import load_lane
from breadboard.product.runtime.artifacts import ArtifactStore


@pytest.fixture(autouse=True)
def _enable_internal_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "1")


def _invoke(argv: list[str], capsys) -> tuple[int, str, str]:
    exit_code = breadboard_cli.main(argv)
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


def test_harness_init_produces_a_valid_explainable_bundle_without_overwriting(
    tmp_path: Path,
    capsys,
) -> None:
    out_dir = tmp_path / "harness"

    exit_code, _, stderr = _invoke(["harness", "create", "--out", str(out_dir)], capsys)

    assert exit_code == 0, stderr
    harness_path = out_dir / "daily_driver.v1.yaml"
    prompt_path = out_dir / "prompts" / "daily_driver_system.md"
    model_roles_path = out_dir / "daily_driver_roles.v1.json"
    assert harness_path.is_file()
    assert prompt_path.is_file()
    assert model_roles_path.is_file()
    harness = yaml.safe_load(harness_path.read_text(encoding="utf-8"))
    assert harness["prompts"]["packs"]["base"]["system"] == (
        "prompts/daily_driver_system.md"
    )
    assert json.loads(model_roles_path.read_text())["defaults"]["role"] == "default"
    prompt_path.write_text(
        "This content exists only in the initialized bundle.\n",
        encoding="utf-8",
    )

    exit_code, _, stderr = _invoke(
        ["harness", "validate", str(harness_path)], capsys
    )
    assert exit_code == 0, stderr

    exit_code, stdout, stderr = _invoke(
        ["harness", "explain", str(harness_path)], capsys
    )
    assert exit_code == 0, stderr
    explanation = json.loads(stdout)
    assert explanation["schema_version"] == "bb.config_explanation.v1"
    assert explanation["surface_schema_version"] == "bb.agent_config_surface.v1"
    assert explanation["ok"] is True
    assert explanation["diagnostics"] == []
    assert explanation["resolved_summary"]["prompt_files"] == [
        prompt_path.resolve().as_posix()
    ]

    harness_path.write_text("author-owned harness\n", encoding="utf-8")
    prompt_path.write_text("author-owned prompt\n", encoding="utf-8")
    model_roles_path.write_text('{"owner": "author-owned model roles"}\n', encoding="utf-8")
    before = {
        harness_path: harness_path.read_bytes(),
        prompt_path: prompt_path.read_bytes(),
        model_roles_path: model_roles_path.read_bytes(),
    }

    exit_code, _, stderr = _invoke(["harness", "create", "--out", str(out_dir)], capsys)

    assert exit_code == 2
    assert "exist" in stderr.lower() or "overwrite" in stderr.lower()
    assert {path: path.read_bytes() for path in before} == before


def test_harness_init_rolls_back_partial_bundle_after_late_publish_failure(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_dir = tmp_path / "harness"
    publish_seed = harness_operations._publish_seed
    def fail_on_roles(path: Path, content: bytes):
        if path.name == "daily_driver_roles.v1.json":
            raise OSError("injected late publish failure")
        return publish_seed(path, content)
    monkeypatch.setattr(harness_operations, "_publish_seed", fail_on_roles)
    exit_code, _, _ = _invoke(["harness", "create", "--out", str(out_dir)], capsys)
    assert exit_code != 0
    assert not any(path.exists() for path in harness_operations.daily_driver_bundle_paths(out_dir))
    monkeypatch.setattr(harness_operations, "_publish_seed", publish_seed)
    exit_code, _, stderr = _invoke(["harness", "create", "--out", str(out_dir)], capsys)
    assert exit_code == 0, stderr


def _extended_prompt_harness(tmp_path: Path) -> tuple[Path, Path, Path]:
    base_dir = tmp_path / "base"
    root_dir = tmp_path / "root"
    (base_dir / "prompts").mkdir(parents=True)
    root_dir.mkdir()
    prompt_path = base_dir / "prompts" / "daily_driver_system.md"
    prompt_path.write_text("Extended system prompt.\n", encoding="utf-8")
    definition = yaml.safe_load(
        (
            Path(__file__).resolve().parents[1]
            / "agent_configs/templates/daily_driver.v1.yaml"
        ).read_text(encoding="utf-8")
    )
    prompt_config = definition.pop("prompts")
    base_path = base_dir / "base.yaml"
    base_path.write_text(
        yaml.safe_dump({"prompts": prompt_config}, sort_keys=False),
        encoding="utf-8",
    )
    definition["extends"] = "../base/base.yaml"
    harness_path = root_dir / "custom.yaml"
    harness_path.write_text(
        yaml.safe_dump(definition, sort_keys=False),
        encoding="utf-8",
    )
    return harness_path, base_path, prompt_path

def test_lock_resolves_prompt_relative_to_declaring_extended_config(
    tmp_path: Path,
    capsys,
) -> None:
    harness_path, _, prompt_path = _extended_prompt_harness(tmp_path)

    exit_code, stdout, stderr = _invoke(
        ["--json", "harness", "lock", str(harness_path)],
        capsys,
    )
    assert exit_code == 0, (stdout, stderr)
    prompt_path.write_text("Changed extended prompt.\n", encoding="utf-8")
    exit_code, stdout, stderr = _invoke(
        ["--json", "harness", "lock", str(harness_path), "--check"],
        capsys,
    )
    assert exit_code == 5
    assert stderr == ""
    assert json.loads(stdout)["error"]["error_code"] == "lock_drift"

def test_daily_driver_role_resource_is_content_addressed_lock_input(
    tmp_path: Path,
    capsys,
) -> None:
    out_dir = tmp_path / "harness"
    assert _invoke(["harness", "create", "--out", str(out_dir)], capsys)[0] == 0
    harness_path = out_dir / "daily_driver.v1.yaml"
    model_roles_path = out_dir / "daily_driver_roles.v1.json"
    exit_code, _, stderr = _invoke(["harness", "lock", str(harness_path)], capsys)
    assert exit_code == 0, stderr
    lock = json.loads((out_dir / "daily_driver.v1.lock.json").read_text())
    role_layers = [
        layer for layer in lock["source_layers"]
        if str(layer["source_ref"]).endswith("::daily_driver_roles.v1.json")
    ]
    assert len(role_layers) == 1
    assert role_layers[0]["layer_hash"] == sha256_bytes(model_roles_path.read_bytes())
    model_roles = json.loads(model_roles_path.read_text())
    model_roles["roles"]["smol"]["primary"]["model_id"] = "changed"
    model_roles_path.write_text(json.dumps(model_roles, indent=2) + "\n")
    exit_code, stdout, stderr = _invoke(
        ["--json", "harness", "lock", str(harness_path), "--check"],
        capsys,
    )
    assert exit_code == 5
    assert stderr == ""
    assert json.loads(stdout)["error"]["error_code"] == "lock_drift"

def test_resource_binding_uses_one_resolved_config_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness_path, base_path, prompt_path = _extended_prompt_harness(tmp_path)
    original_prompt_bytes = prompt_path.read_bytes()
    resolve_resources = harness_resolution._prompt_resources

    def mutate_extended_config(*args):
        resources = resolve_resources(*args)
        base = yaml.safe_load(base_path.read_text(encoding="utf-8"))
        base["prompts"]["packs"]["base"]["system"] = (
            "prompts/replacement.md"
        )
        base_path.write_text(
            yaml.safe_dump(base, sort_keys=False),
            encoding="utf-8",
        )
        return resources

    monkeypatch.setattr(
        harness_resolution,
        "_prompt_resources",
        mutate_extended_config,
    )
    compiled = harness_resolution.compile_harness_source(
        harness_path,
        tmp_path,
    )

    values = {
        row["path"]: row["value"]
        for row in compiled.lock["effective_values"]
    }
    resource_layers = [
        layer for layer in compiled.lock["source_layers"]
        if layer["scope"] == "resource"
    ]
    assert values["prompts.packs.base.system"] == (
        "prompts/daily_driver_system.md"
    )
    assert resource_layers == [{
        "host_visible": True,
        "layer_hash": sha256_bytes(original_prompt_bytes),
        "layer_id": "harness-resource:0000",
        "model_visible": True,
        "precedence": 20,
        "scope": "resource",
        "source_kind": "project",
        "source_ref": (
            "base/base.yaml::prompts/daily_driver_system.md"
        ),
    }]

def test_harness_update_replaces_definition_from_explicit_source(tmp_path: Path, capsys) -> None:
    out_dir = tmp_path / "harness"
    assert _invoke(["harness", "create", "--out", str(out_dir)], capsys)[0] == 0
    harness_path = out_dir / "daily_driver.v1.yaml"
    definition = yaml.safe_load(harness_path.read_text())
    definition["modes"][0]["name"] = "review"
    definition["loop"]["sequence"][0]["mode"] = "review"
    source = tmp_path / "replacement.yaml"
    source.write_text(yaml.safe_dump(definition), encoding="utf-8")
    exit_code, _, stderr = _invoke(["harness", "update", str(harness_path), "--from", str(source)], capsys)
    assert exit_code == 0, stderr
    assert yaml.safe_load(harness_path.read_text())["modes"][0]["name"] == "review"


def test_json_harness_explain_validates_resolved_legacy_surface(
    tmp_path: Path,
    capsys,
) -> None:
    harness_path = tmp_path / "invalid.yaml"
    harness = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "agent_configs/templates/minimal_harness.v2.yaml").read_text()
    )
    del harness["providers"]["models"][0]["adapter"]
    harness_path.write_text(yaml.safe_dump(harness))

    exit_code, stdout, stderr = _invoke(
        ["--json", "harness", "explain", str(harness_path)],
        capsys,
    )

    assert exit_code == 2, stderr
    result = json.loads(stdout)
    assert "/providers/models/0/adapter" in result["error"]["message"]


def test_json_harness_explain_rejects_recursive_yaml_alias(
    tmp_path: Path,
    capsys,
) -> None:
    harness_path = tmp_path / "recursive.yaml"
    harness_path.write_text(
        "schema_version: bb.agent_config_surface.v2\n"
        "version: 2\n"
        "dossier: &recursive\n"
        "  self: *recursive\n"
    )

    exit_code, stdout, stderr = _invoke(
        ["--json", "harness", "explain", str(harness_path)],
        capsys,
    )

    assert exit_code == 2, stderr
    assert "json_cycle" in json.loads(stdout)["error"]["message"]


def test_harness_extends_resolve_from_external_source_directory(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "workspace"
    external = tmp_path / "external"
    workspace.mkdir()
    external.mkdir()
    nested = external / "nested"
    nested.mkdir()
    template = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "agent_configs/templates/minimal_harness.v3.yaml").read_text()
    )
    (external / "base.yaml").write_text(yaml.safe_dump(template))
    (nested / "child.yaml").write_text(yaml.safe_dump({**template, "extends": "../base.yaml"}))

    exit_code, stdout, stderr = _invoke(
        ["--json", "harness", "--workspace", str(workspace), "explain", str(nested / "child.yaml")],
        capsys,
    )

    assert exit_code == 0, stderr
    result = json.loads(stdout)
    assert result["data"]["resolved_summary"]["extends_chain"] == ["base.yaml"]
    assert str(tmp_path) not in stdout


def test_lane_init_produces_a_loader_valid_manifest_without_overwriting(tmp_path: Path, capsys, monkeypatch) -> None:
    out_dir = tmp_path / "lane"

    exit_code, _, stderr = _invoke(["lane", "init", "--out", str(out_dir)], capsys)

    assert exit_code == 0, stderr
    manifest_path = out_dir / ".breadboard/lanes/new_lane.manifest.json"
    loaded = load_lane(manifest_path)
    assert loaded["schema_version"] == "bb.e4.lane_manifest.v2"

    exit_code, _, stderr = _invoke(["lane", "validate", str(manifest_path)], capsys); assert exit_code == 0, stderr
    legacy = Path(__file__).resolve().parents[1] / "config/e4_lanes/oh_my_pi_p6_6_task_job_subagent.yaml"; exit_code, _, stderr = _invoke(["lane", "validate", str(legacy)], capsys); assert exit_code == 0, stderr
    exit_code, stdout, stderr = _invoke(["--json", "lane", "lock", str(manifest_path)], capsys); assert exit_code == 3 and json.loads(stdout)["error"]["error_code"] == "path_unavailable", stderr
    original = manifest_path.read_bytes(); payload = json.loads(original); payload["lane_id"] = "new.lane"; manifest_path.write_text(json.dumps(payload)); yaml_path = manifest_path.with_name("new.lane.manifest.yaml"); manifest_path.rename(yaml_path); monkeypatch.chdir(tmp_path); (tmp_path / "new.lane").write_text(json.dumps({**payload, "lane_id": "hijacked"})); exit_code, stdout, stderr = _invoke(["--json", "lane", "--workspace", str(out_dir), "get", "new.lane"], capsys); assert exit_code == 0, stderr; yaml_path.rename(manifest_path); manifest_path.write_bytes(original)

    manifest_path.write_text("author-owned lane\n", encoding="utf-8")
    before = manifest_path.read_bytes()

    exit_code, _, stderr = _invoke(["lane", "init", "--out", str(out_dir)], capsys)

    assert exit_code == 2
    assert "exist" in stderr.lower() or "overwrite" in stderr.lower()
    assert manifest_path.read_bytes() == before


def test_harness_validate_returns_pointerful_schema_failure(
    tmp_path: Path,
    capsys,
) -> None:
    harness_path = tmp_path / "invalid-harness.yaml"
    harness_path.write_text(
        """schema_version: bb.agent_config_surface.v2
version: 2
workspace:
  root: .
providers:
  default_model: broken
  models:
    - id: broken
modes:
  - name: main
loop:
  sequence:
    - mode: main
""",
        encoding="utf-8",
    )

    exit_code, _, stderr = _invoke(
        ["harness", "validate", str(harness_path)], capsys
    )

    assert exit_code == 2
    assert "/providers/models/0/adapter" in stderr
    assert "required" in stderr.lower()


def test_lane_validate_returns_pointerful_schema_failure(
    tmp_path: Path,
    capsys,
) -> None:
    out_dir = tmp_path / "lane"
    exit_code, _, stderr = _invoke(["lane", "init", "--out", str(out_dir)], capsys)
    assert exit_code == 0, stderr
    manifest_path = out_dir / ".breadboard/lanes/new_lane.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["references"]["target"] = []
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

    exit_code, _, stderr = _invoke(["lane", "validate", str(manifest_path)], capsys)

    assert exit_code == 2
    assert "invalid_lane" in stderr
    assert "references.target" in stderr
    assert "repo-relative path" in stderr.lower()


def test_pyproject_installs_cli_and_runtime_import_packages() -> None:
    project_root = Path(breadboard_cli.__file__).resolve().parents[1]
    metadata = tomllib.loads(
        (project_root / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert metadata["project"]["scripts"]["bbh"] == "scripts.breadboard_cli:main"
    package_find = metadata["tool"]["setuptools"]["packages"]["find"]
    assert set(package_find["include"]) >= {
        "scripts*",
        "breadboard*",
        "breadboard_engine*",
        "conformance*",
    }
    assert package_find["namespaces"] is True
    assert "adaptive_iter" in metadata["tool"]["setuptools"]["py-modules"]
    assert metadata["tool"]["setuptools"]["package-data"]["contracts.kernel.schemas"] == ["*.schema.json"]
    from breadboard.product.coordination import WorkItem as ExportedWorkItem; assert ExportedWorkItem.__name__ == "WorkItem"


@pytest.mark.parametrize("namespace", ["harness", "lane"])
@pytest.mark.parametrize("path_state", ["missing", "unreadable"])
def test_validate_returns_resolution_failure_for_unresolvable_paths(
    namespace: str,
    path_state: str,
    tmp_path: Path,
    capsys,
) -> None:
    target = tmp_path / f"{path_state}.yaml"
    if path_state == "unreadable":
        target.write_text("{}\n", encoding="utf-8")
        target.chmod(0)

    try:
        exit_code, _, _ = _invoke([namespace, "validate", str(target)], capsys)
    finally:
        if target.exists():
            target.chmod(0o600)

    assert exit_code == 3


@pytest.mark.parametrize("extra_global_flags", [(), ("--quiet",)])
@pytest.mark.parametrize(
    ("namespace", "created_paths"),
    [
        (
            "harness",
            ("daily_driver.v1.yaml", "prompts/daily_driver_system.md", "daily_driver_roles.v1.json"),
        ),
        ("lane", (".breadboard/lanes/new_lane.manifest.json",)),
    ],
)
def test_init_json_is_the_only_output_and_identifies_every_created_file(
    namespace: str,
    created_paths: tuple[str, ...],
    extra_global_flags: tuple[str, ...],
    tmp_path: Path,
    capsys,
) -> None:
    out_dir = tmp_path / namespace

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            *extra_global_flags,
            namespace,
            "create" if namespace == "harness" else "init",
            "--out",
            str(out_dir),
        ],
        capsys,
    )

    assert exit_code == 0, stderr
    payload = json.loads(stdout)
    assert payload["ok"] is True
    assert payload["schema_version"] == "bb.cli.result.v1"
    assert payload["data"]["path"] == created_paths[0]
    if namespace == "harness":
        assert payload["data"]["prompt_path"] == Path(created_paths[1]).name
        assert payload["data"]["model_roles_path"] == Path(created_paths[2]).name
    assert stderr == ""


@pytest.mark.parametrize("namespace", ["harness", "lane"])
def test_init_quiet_emits_no_success_output(
    namespace: str,
    tmp_path: Path,
    capsys,
) -> None:
    exit_code, stdout, stderr = _invoke(
        ["--quiet", namespace, "create" if namespace == "harness" else "init", "--out", str(tmp_path / namespace)],
        capsys,
    )

    assert exit_code == 0
    assert stdout == ""
    assert stderr == ""


def test_artifact_verify_infers_stored_size_when_size_is_omitted(
    tmp_path: Path,
    capsys,
) -> None:
    artifact_ref = ArtifactStore(tmp_path / ".breadboard" / "artifacts").put(
        b"verified artifact",
        media_type="text/plain",
    )

    exit_code, stdout, stderr = _invoke(
        [
            "--json",
            "artifact",
            "--workspace",
            str(tmp_path),
            "verify",
            artifact_ref.digest,
        ],
        capsys,
    )

    assert exit_code == 0
    assert stderr == ""
    payload = json.loads(stdout)
    assert payload["data"]["verified"] is True
    assert payload["data"]["artifact"]["size_bytes"] == len(b"verified artifact")


def test_artifact_put_is_content_addressed_and_immutable(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BREADBOARD_LEGACY_ROUTES", "1")
    source = tmp_path / "proof.txt"
    source.write_bytes(b"immutable proof")
    argv = [
        "--json",
        "artifact",
        "--workspace",
        str(tmp_path),
        "put",
        str(source),
        "--media-type",
        "text/plain",
    ]
    first_code, first_stdout, first_stderr = _invoke(argv, capsys)
    second_code, second_stdout, second_stderr = _invoke(argv, capsys)
    first = json.loads(first_stdout)
    second = json.loads(second_stdout)
    assert first_code == second_code == 0
    assert first_stderr == second_stderr == ""
    assert first["data"]["artifact"] == second["data"]["artifact"]
    assert first["data"]["artifact"]["media_type"] == "text/plain"
    digest = first["data"]["artifact"]["digest"].removeprefix("sha256:")
    stored = tmp_path / ".breadboard" / "artifacts" / "sha256" / digest[:2] / digest
    assert stored.read_bytes() == b"immutable proof"


def test_artifact_delete_removes_only_the_addressed_content(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BREADBOARD_LEGACY_ROUTES", "1")
    source = tmp_path / "proof.bin"
    source.write_bytes(b"delete this proof")
    put_code, stdout, _ = _invoke(
        ["--json", "artifact", "--workspace", str(tmp_path), "put", str(source)],
        capsys,
    )
    artifact = json.loads(stdout)["data"]["artifact"]
    assert put_code == 0
    delete_code, stdout, stderr = _invoke(
        ["--json", "artifact", "--workspace", str(tmp_path), "delete", artifact["digest"]],
        capsys,
    )
    deleted = json.loads(stdout)
    assert delete_code == 0 and stderr == ""
    assert deleted["data"]["deleted"] is True
    get_code, stdout, _ = _invoke(
        ["--json", "artifact", "--workspace", str(tmp_path), "get", artifact["digest"]],
        capsys,
    )
    assert get_code == 3
    assert json.loads(stdout)["error"]["error_code"] == "path_unavailable"
