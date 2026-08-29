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
    assert list_e4_target_ids() == ("oh-my-pi@16.2.13", "pi@0.57.1")


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

    assert (
        _editable_source_root(SimpleNamespace(read_text=unreadable_metadata)) is None
    )

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
    assert list_e4_target_ids() == ("oh-my-pi@16.2.13", "pi@0.57.1")

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
    with pytest.raises(E4TargetError, match="unknown E4 target"):
        load_e4_target("pi@latest")

    target = load_e4_target("pi@0.57.1")
    with pytest.raises(E4TargetError, match="does not declare asset"):
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

    with pytest.raises(E4TargetError, match="SHA-256 mismatch"):
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

    with pytest.raises(E4TargetError, match="bytes must be a non-negative integer"):
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

    with pytest.raises(E4TargetError, match="unsafe target resource path"):
        _load_e4_target_from_root(copied_root, "pi@0.57.1")
