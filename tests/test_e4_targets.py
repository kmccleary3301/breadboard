from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
import yaml

from breadboard_engine.e4_targets import (
    E4TargetError,
    _load_e4_target_from_root,
    _editable_source_root,
    _location_key,
    _resource_root,
    list_e4_target_ids,
    load_e4_target,
)

from breadboard.product.harness.targets import (
    bind_e4_target_inputs,
    serialize_e4_target_inputs,
)
from breadboard.product.harness.validate import (
    HarnessDefinitionValidationError,
    validate_e4_target_document,
)


ROOT = Path(__file__).resolve().parents[1]
TARGET_ROOT = ROOT / "config" / "e4_targets"


def test_target_resources_bind_to_loader_distribution_root() -> None:
    assert _resource_root() == TARGET_ROOT


def test_target_resources_load_outside_editable_checkout_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert _resource_root() == TARGET_ROOT
    assert list_e4_target_ids() == (
        "mini-swe-agent@2.4.6",
        "oh-my-pi@16.2.13",
        "openhands-sdk@1.47.0",
        "pi@0.57.1",
    )

def test_editable_source_root_accepts_only_absolute_local_file_urls(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source root"
    valid = SimpleNamespace(
        read_text=lambda _: json.dumps(
            {"url": source_root.as_uri(), "dir_info": {"editable": True}}
        )
    )
    assert _editable_source_root(valid) == source_root

    def unreadable_metadata(_: str) -> str:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte")

    assert _editable_source_root(SimpleNamespace(read_text=unreadable_metadata)) is None

    for url in (
        "https://example.invalid/source",
        "file:relative/source",
        "file:///tmp/source?unexpected=query",
        "file://remote.invalid/source",
        "file://[",
    ):
        invalid = SimpleNamespace(
            read_text=lambda _, url=url: json.dumps(
                {"url": url, "dir_info": {"editable": True}}
            )
        )
        assert _editable_source_root(invalid) is None


def test_distribution_owner_match_does_not_resolve_symlink_aliases(
    tmp_path: Path,
) -> None:
    loader = tmp_path / "installed" / "breadboard_engine" / "e4_targets.py"
    loader.parent.mkdir(parents=True)
    loader.write_text("", encoding="utf-8")
    alias = tmp_path / "hostile" / "breadboard_engine" / "e4_targets.py"
    alias.parent.mkdir(parents=True)
    try:
        alias.symlink_to(loader)
    except OSError:
        pytest.skip("symlink creation is not available")

    assert _location_key(alias) != _location_key(loader)


def test_pinned_targets_load_with_exact_release_source_and_runtime_assets() -> None:
    assert list_e4_target_ids() == (
        "mini-swe-agent@2.4.6",
        "oh-my-pi@16.2.13",
        "openhands-sdk@1.47.0",
        "pi@0.57.1",
    )
    pi = load_e4_target("pi@0.57.1")
    assert pi.descriptor["upstream"] == {
        "repository": "https://github.com/badlogic/pi-mono.git",
        "source": {
            "identity_kind": "archive_snapshot_without_git_dir",
            "directory": "packages/coding-agent",
            "archive_sha256": (
                "bd64909b10a34c30890606f8787ee2ac47b9e7989e3db581978dd8214d62e87b"
            ),
            "archive_bytes": 4755355,
        },
        "package": {
            "name": "@mariozechner/pi-coding-agent",
            "version": "0.57.1",
            "git_head": "a9cedccdde77e9d765303463d8a6cd11c58f7a7f",
            "tarball": (
                "https://registry.npmjs.org/@mariozechner/pi-coding-agent/-/"
                "pi-coding-agent-0.57.1.tgz"
            ),
            "integrity": (
                "sha512-u5MQEduj68rwVIsRsqrWkJYiJCyPph/a6bMoJAQKo1sb+Pc17Y/"
                "ojwa+wGssnUMjEB38AQKofWTVe8NFEpSWNw=="
            ),
            "shasum_sha1": "58433481f4a469e28f3faac7ea3d2b10cb1bfefb",
            "tarball_sha256": (
                "8648e71d5553388ed710f1ef4165d9f090e1783e446629410c545875ac564b6f"
            ),
            "tarball_bytes": 3761279,
        },
    }
    assert pi.descriptor["overlay"] == {
        "overlay_id": "r3-json-no-session.v1",
        "argv": (
            "--mode",
            "json",
            "--no-session",
            "--thinking",
            "off",
            "--tools",
            "read,bash,edit,write,grep,find,ls",
            "--no-extensions",
            "--no-skills",
            "--no-prompt-templates",
        ),
        "settings": {"retry.enabled": False, "retry.maxRetries": 0},
    }
    pi_config = pi.read_asset_text(pi.descriptor["execution"]["config_asset"])
    assert "target_id: pi@0.57.1" in pi_config
    assert "max_retries: 0" in pi_config

    pi_surface = json.loads(pi.read_asset_text("tool-surface.json"))
    assert pi_surface["ordered_tools"] == [
        "read",
        "bash",
        "edit",
        "write",
        "grep",
        "find",
        "ls",
    ]
    assert "breadboard_implementation_ids" not in pi_surface
    assert pi_surface["dispatch"] == {
        "kind": "target_adapter",
        "adapter_id": "pi-0.57.1",
        "binding_requirement": "RL-E4-2",
    }
    assert pi_surface["tools"]["edit"]["parameters"]["required"] == [
        "path",
        "oldText",
        "newText",
    ]
    assert pi_surface["tools"]["write"]["parameters"]["required"] == [
        "path",
        "content",
    ]
    assert tuple(pi_surface["tools"]["grep"]["parameters"]["properties"]) == (
        "pattern",
        "path",
        "glob",
        "ignoreCase",
        "literal",
        "context",
        "limit",
    )
    omp = load_e4_target("oh-my-pi@16.2.13")
    assert omp.descriptor["upstream"]["source"] == {
        "commit": "5356713eae60e67ee64d9b02e3b5e377d248ee7f",
        "directory": "packages/coding-agent",
        "archive_sha256": (
            "03fd855b01bb7457f85e929ff747d8560d7cf7ed420fd0676bf32b917fb264a3"
        ),
        "archive_bytes": 42065260,
    }
    assert omp.descriptor["upstream"]["package"]["version"] == "16.2.13"
    omp_config = yaml.safe_load(omp.read_asset_text("harness.yaml"))
    assert omp_config["prompt"]["dynamic_fields"] == [
        "alwaysApplyRules",
        "eagerTasks",
        "eagerTasksAlways",
        "hasMCPDiscoveryServers",
        "hasMemoryRoot",
        "hasObsidian",
        "intentField",
        "intentTracing",
        "mcpDiscoveryMode",
        "mcpDiscoveryServerSummaries",
        "personality",
        "renderMermaid",
        "rules",
        "secretsEnabled",
        "skills",
        "taskBatch",
        "toolInfo",
        "toolInventory",
        "toolListMode",
        "toolRefs",
        "tools",
    ]
    omp_surface = json.loads(omp.read_asset_text("tool-surface.json"))
    assert omp_surface["ordered_tools"][:5] == [
        "read",
        "bash",
        "edit",
        "ast_grep",
        "ast_edit",
    ]
    assert omp_surface["legacy_aliases"] == {"search": "grep", "find": "glob"}


def test_target_freeze_references_match_calibrated_source_rows() -> None:
    manifest = yaml.safe_load(
        (ROOT / "config" / "e4_target_freeze_manifest.yaml").read_text(encoding="utf-8")
    )
    freeze_rows = manifest["e4_configs"]

    pi = load_e4_target("pi@0.57.1")
    for entry_id in pi.descriptor["freeze_manifest_entries"]:
        harness = freeze_rows[entry_id]["harness"]
        assert harness["upstream_release_label"] == (
            "@mariozechner/pi-coding-agent@0.57.1"
        )
        assert harness["upstream_commit"] == (
            "archive:sha256:"
            "bd64909b10a34c30890606f8787ee2ac47b9e7989e3db581978dd8214d62e87b"
        )

    omp = load_e4_target("oh-my-pi@16.2.13")
    for entry_id in omp.descriptor["freeze_manifest_entries"]:
        harness = freeze_rows[entry_id]["harness"]
        assert harness["upstream_release_label"] == (
            "@oh-my-pi/pi-coding-agent@16.2.13"
        )
        assert harness["upstream_commit"] == (
            "5356713eae60e67ee64d9b02e3b5e377d248ee7f"
        )


def test_target_loader_rejects_unknown_and_undeclared_assets() -> None:
    with pytest.raises(E4TargetError):
        load_e4_target("pi@latest")

    target = load_e4_target("pi@0.57.1")
    with pytest.raises(E4TargetError):
        target.read_asset_text("../target.json")


def test_loaded_target_descriptor_is_deeply_immutable() -> None:
    target = load_e4_target("pi@0.57.1")

    with pytest.raises(TypeError):
        target.descriptor["overlay"]["argv"][0] = "--mutated"
    with pytest.raises(TypeError):
        target.descriptor["overlay"]["settings"]["retry.enabled"] = True


def test_target_loader_rejects_corrupt_runtime_asset(tmp_path: Path) -> None:
    copied_root = tmp_path / "e4_targets"
    shutil.copytree(TARGET_ROOT, copied_root)
    harness = copied_root / "pi" / "0.57.1" / "harness.yaml"
    harness.write_text(harness.read_text(encoding="utf-8") + "corrupt: true\n")

    with pytest.raises(E4TargetError):
        _load_e4_target_from_root(copied_root, "pi@0.57.1")


def test_loaded_target_serves_only_verified_asset_bytes(tmp_path: Path) -> None:
    copied_root = tmp_path / "e4_targets"
    shutil.copytree(TARGET_ROOT, copied_root)
    target = _load_e4_target_from_root(copied_root, "pi@0.57.1")
    expected = target.read_asset_bytes("harness.yaml")
    harness = copied_root / "pi" / "0.57.1" / "harness.yaml"

    harness.write_text("changed after verification\n", encoding="utf-8")

    assert target.read_asset_bytes("harness.yaml") == expected


def test_target_loader_rejects_boolean_asset_size(tmp_path: Path) -> None:
    copied_root = tmp_path / "e4_targets"
    shutil.copytree(TARGET_ROOT, copied_root)
    descriptor_path = copied_root / "pi" / "0.57.1" / "target.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    descriptor["assets"][0]["bytes"] = True
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")

    index_path = copied_root / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["targets"]["pi@0.57.1"]["sha256"] = hashlib.sha256(
        descriptor_path.read_bytes()
    ).hexdigest()
    index_path.write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(E4TargetError):
        _load_e4_target_from_root(copied_root, "pi@0.57.1")


@pytest.mark.parametrize(
    "unsafe_path",
    ("../target.json", "D:/outside/target.json"),
)
def test_target_loader_rejects_unsafe_descriptor_path(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    copied_root = tmp_path / "e4_targets"
    shutil.copytree(TARGET_ROOT, copied_root)
    index_path = copied_root / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["targets"]["pi@0.57.1"]["descriptor"] = unsafe_path
    index_path.write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(E4TargetError):
        _load_e4_target_from_root(copied_root, "pi@0.57.1")


def _write_v2_fixture(root: Path) -> Path:
    target_root = root / "e4_targets"
    target_dir = target_root / "example" / "2.0"
    target_dir.mkdir(parents=True)
    descriptor = json.loads(
        (ROOT / "contracts/kernel/examples/e4_target_v2_minimal.json").read_text(
            encoding="utf-8"
        )
    )
    config = json.loads(
        (ROOT / "contracts/kernel/examples/e4_target_config_v2_minimal.json").read_text(
            encoding="utf-8"
        )
    )
    assets = {
        "harness.yaml": json.dumps(config, separators=(",", ":")).encode("utf-8"),
        "prompts/system-prompt.md": b"example prompt\\n",
        "tool-surface.json": b'{"ordered_tools":["terminal"]}\\n',
    }
    for asset in descriptor["assets"]:
        content = assets[asset["path"]]
        asset["sha256"] = "sha256:" + hashlib.sha256(content).hexdigest()
        asset["bytes"] = len(content)
        asset_path = target_dir / asset["path"]
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_path.write_bytes(content)
    descriptor_path = target_dir / "target.json"
    descriptor_path.write_text(
        json.dumps(descriptor, separators=(",", ":")),
        encoding="utf-8",
    )
    index = {
        "schema_version": "bb.e4.target_index.v1",
        "targets": {
            "example@2.0": {
                "descriptor": "example/2.0/target.json",
                "sha256": hashlib.sha256(descriptor_path.read_bytes()).hexdigest(),
            }
        },
    }
    (target_root / "index.json").write_text(
        json.dumps(index, separators=(",", ":")),
        encoding="utf-8",
    )
    return target_root


def _refresh_v2_descriptor(root: Path) -> None:
    target_dir = root / "example" / "2.0"
    descriptor_path = target_dir / "target.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    for asset in descriptor["assets"]:
        content = (target_dir / asset["path"]).read_bytes()
        asset["sha256"] = "sha256:" + hashlib.sha256(content).hexdigest()
        asset["bytes"] = len(content)
    descriptor_path.write_text(
        json.dumps(descriptor, separators=(",", ":")),
        encoding="utf-8",
    )
    index_path = root / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["targets"]["example@2.0"]["sha256"] = hashlib.sha256(
        descriptor_path.read_bytes()
    ).hexdigest()
    index_path.write_text(
        json.dumps(index, separators=(",", ":")),
        encoding="utf-8",
    )


def test_target_loader_accepts_a_closed_v2_descriptor_and_configuration(
    tmp_path: Path,
) -> None:
    root = _write_v2_fixture(tmp_path)

    target = _load_e4_target_from_root(root, "example@2.0")

    assert target.descriptor["schema_version"] == "bb.e4.target.v2"
    assert target.descriptor["overlay"]["overlay_id"] == "example-overlay.v2"
    assert target.read_asset_text("prompts/system-prompt.md") == "example prompt\\n"


def test_target_loader_rejects_duplicate_v2_configuration_keys(tmp_path: Path) -> None:
    root = _write_v2_fixture(tmp_path)
    config_path = root / "example" / "2.0" / "harness.yaml"
    payload = config_path.read_text(encoding="utf-8")
    payload = payload.replace(
        '"target_id":"example@2.0"',
        '"target_id":"discarded","target_id":"example@2.0"',
    )
    config_path.write_text(payload, encoding="utf-8")
    _refresh_v2_descriptor(root)

    with pytest.raises(E4TargetError):
        _load_e4_target_from_root(root, "example@2.0")


def test_target_loader_checks_nested_schema_without_parent_required(
    tmp_path: Path,
) -> None:
    root = _write_v2_fixture(tmp_path)
    config_path = root / "example" / "2.0" / "harness.yaml"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["inputs"]["fields"][0]["value_schema"] = {
        "type": "object",
        "properties": {
            "child": {
                "type": "object",
                "properties": {},
                "required": ["ghost"],
                "additionalProperties": False,
            }
        },
        "additionalProperties": False,
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")
    _refresh_v2_descriptor(root)

    with pytest.raises(E4TargetError):
        _load_e4_target_from_root(root, "example@2.0")

    config["inputs"]["fields"][0]["value_schema"]["properties"]["child"][
        "properties"
    ] = {"ghost": {"type": "string"}}
    config_path.write_text(json.dumps(config), encoding="utf-8")
    _refresh_v2_descriptor(root)
    package = _load_e4_target_from_root(root, "example@2.0")
    frame = bind_e4_target_inputs(
        package,
        "bb.rl.headless-run-request.v2",
        {"task": {"child": {"ghost": "present"}}},
    )
    assert json.loads(frame)["target_dynamic_fields"] == {
        "task": {"child": {"ghost": "present"}}
    }


def test_v2_input_schema_supports_nullable_typed_arrays_and_closed_objects() -> None:
    config = json.loads(
        (ROOT / "contracts/kernel/examples/e4_target_config_v2_minimal.json").read_text(
            encoding="utf-8"
        )
    )
    field = config["inputs"]["fields"][0]
    field["required"] = False
    field["omission"] = "null"
    field["value_schema"] = {
        "type": ["object", "null"],
        "properties": {
            "enabled": {"type": "boolean"},
            "labels": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["enabled", "labels"],
        "additionalProperties": False,
    }

    assert validate_e4_target_document(config) == ()


def test_v2_input_default_must_validate_against_its_value_schema() -> None:
    config = json.loads(
        (ROOT / "contracts/kernel/examples/e4_target_config_v2_minimal.json").read_text(
            encoding="utf-8"
        )
    )
    field = config["inputs"]["fields"][0]
    field["required"] = False
    field["omission"] = "default"
    field["default"] = {"enabled": "yes"}
    field["value_schema"] = {
        "type": "object",
        "properties": {"enabled": {"type": "boolean"}},
        "required": ["enabled"],
        "additionalProperties": False,
    }

    findings = validate_e4_target_document(config)
    assert any(
        finding.pointer == "/inputs/fields/0/default/enabled" for finding in findings
    )


def test_v2_input_schema_is_required_and_value_type_is_not_admitted() -> None:
    config = json.loads(
        (ROOT / "contracts/kernel/examples/e4_target_config_v2_minimal.json").read_text(
            encoding="utf-8"
        )
    )
    field = config["inputs"]["fields"][0]
    field.pop("value_schema")
    field["value_type"] = "string"

    findings = validate_e4_target_document(config)
    assert any(
        finding.pointer == "/inputs/fields/0/value_type"
        and finding.code == "additionalProperties"
        for finding in findings
    )
    assert any(
        finding.pointer == "/inputs/fields/0/value_schema"
        and finding.code == "required"
        for finding in findings
    )


def test_target_loader_rejects_unknown_v2_descriptor_fields(tmp_path: Path) -> None:
    root = _write_v2_fixture(tmp_path)
    descriptor_path = root / "example" / "2.0" / "target.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    descriptor["unexpected"] = True
    descriptor_path.write_text(
        json.dumps(descriptor, separators=(",", ":")),
        encoding="utf-8",
    )
    _refresh_v2_descriptor(root)

    with pytest.raises(E4TargetError, match="undeclared field"):
        _load_e4_target_from_root(root, "example@2.0")


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (
            lambda config: config.__setitem__("unexpected", True),
            "undeclared field",
        ),
        (
            lambda config: config["policy"].__setitem__("unexpected", True),
            "undeclared field",
        ),
        (
            lambda config: config.__setitem__(
                "semantic_revision", "bb.e4.target-semantics.v99"
            ),
            "semantic_revision",
        ),
    ),
)
def test_target_loader_rejects_unknown_v2_configuration_declarations(
    tmp_path: Path,
    mutation: Any,
    message: str,
) -> None:
    root = _write_v2_fixture(tmp_path)
    config_path = root / "example" / "2.0" / "harness.yaml"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    mutation(config)
    config_path.write_text(
        json.dumps(config, separators=(",", ":")),
        encoding="utf-8",
    )
    _refresh_v2_descriptor(root)

    with pytest.raises(E4TargetError, match=message):
        _load_e4_target_from_root(root, "example@2.0")


def test_target_loader_rejects_v2_materialization_reference_outside_package(
    tmp_path: Path,
) -> None:
    root = _write_v2_fixture(tmp_path)
    config_path = root / "example" / "2.0" / "harness.yaml"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["materialization"]["assets"][0]["path"] = "missing.txt"
    config_path.write_text(
        json.dumps(config, separators=(",", ":")),
        encoding="utf-8",
    )
    _refresh_v2_descriptor(root)

    with pytest.raises(E4TargetError, match="declared package asset"):
        _load_e4_target_from_root(root, "example@2.0")


def test_target_loader_rejects_nontext_descriptor_revision(tmp_path: Path) -> None:
    root = _write_v2_fixture(tmp_path)
    descriptor_path = root / "example" / "2.0" / "target.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    descriptor["schema_version"] = ["bb.e4.target.v2"]
    descriptor_path.write_text(
        json.dumps(descriptor, separators=(",", ":")),
        encoding="utf-8",
    )
    _refresh_v2_descriptor(root)

    with pytest.raises(E4TargetError):
        _load_e4_target_from_root(root, "example@2.0")


def test_target_loader_rejects_unknown_v2_configuration_revision(
    tmp_path: Path,
) -> None:
    root = _write_v2_fixture(tmp_path)
    config_path = root / "example" / "2.0" / "harness.yaml"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["schema_version"] = "bb.e4.target_config.v99"
    config_path.write_text(
        json.dumps(config, separators=(",", ":")),
        encoding="utf-8",
    )
    _refresh_v2_descriptor(root)

    with pytest.raises(E4TargetError, match="schema_version"):
        _load_e4_target_from_root(root, "example@2.0")


def test_v2_input_binding_preserves_presence_and_runtime_ownership(
    tmp_path: Path,
) -> None:
    root = _write_v2_fixture(tmp_path)
    config_path = root / "example" / "2.0" / "harness.yaml"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    field_base = {
        "required": False,
        "producer": "operator",
        "lifetime": "episode",
        "source_ref": "fixture/inputs.json",
        "omission": "missing",
    }
    config["inputs"]["fields"].extend(
        [
            dict(field_base, name="tag", value_schema={"type": ["string", "null"]}),
            dict(
                field_base,
                name="payload",
                required=True,
                omission="required",
                value_schema={
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "count": {"type": "integer"},
                    },
                    "required": ["enabled", "count"],
                    "additionalProperties": False,
                },
            ),
            dict(
                field_base,
                name="fallback",
                omission="default",
                default=0,
                value_schema={"type": "integer"},
            ),
            dict(
                field_base,
                name="tick",
                producer="runtime",
                lifetime="turn",
                required=True,
                omission="required",
                value_schema={"type": "integer", "minimum": 0},
            ),
        ]
    )
    config["inputs"]["order"] = ["task", "tag", "payload", "fallback", "tick"]
    config_path.write_text(json.dumps(config), encoding="utf-8")
    _refresh_v2_descriptor(root)
    package = _load_e4_target_from_root(root, "example@2.0")
    values = {"task": "fixture", "payload": {"enabled": False, "count": 0}}
    version = "bb.rl.headless-run-request.v2"
    missing = bind_e4_target_inputs(package, version, values)
    null = bind_e4_target_inputs(package, version, dict(values, tag=None))
    empty = bind_e4_target_inputs(package, version, dict(values, tag=""))
    assert len({missing, null, empty}) == 3
    observed = json.loads(null)["target_dynamic_fields"]
    assert observed["tag"] is None
    assert observed["payload"]["enabled"] is False
    assert type(observed["payload"]["count"]) is int
    assert "tick" not in observed
    assert "fallback" not in observed
    explicit_default = bind_e4_target_inputs(package, version, dict(values, fallback=0))
    assert explicit_default != missing
    with pytest.raises(HarnessDefinitionValidationError) as runtime:
        bind_e4_target_inputs(package, version, dict(values, tick=0))
    assert [(finding.pointer, finding.code) for finding in runtime.value.findings] == [
        ("/tick", "input_producer")
    ]
    with pytest.raises(HarnessDefinitionValidationError) as encoded_object:
        bind_e4_target_inputs(
            package, version, dict(values, payload='{"enabled":false,"count":0}')
        )
    assert [
        (finding.pointer, finding.code) for finding in encoded_object.value.findings
    ] == [("/payload", "type")]


def test_v2_input_identity_preserves_numeric_form_and_nested_key_order() -> None:
    version = "bb.rl.headless-run-request.v2"
    integer = serialize_e4_target_inputs(version, {"value": {"first": 1, "second": 2}})
    floating = serialize_e4_target_inputs(
        version, {"value": {"first": 1.0, "second": 2}}
    )
    reordered = serialize_e4_target_inputs(
        version, {"value": {"second": 2, "first": 1}}
    )
    assert len({integer, floating, reordered}) == 3
    assert (
        type(json.loads(floating)["target_dynamic_fields"]["value"]["first"]) is float
    )
    assert list(json.loads(reordered)["target_dynamic_fields"]["value"]) == [
        "second",
        "first",
    ]
