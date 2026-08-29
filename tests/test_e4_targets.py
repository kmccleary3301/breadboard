from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest
import yaml

from breadboard_engine.e4_targets import (
    E4TargetError,
    _load_e4_target_from_root,
    list_e4_target_ids,
    load_e4_target,
)


ROOT = Path(__file__).resolve().parents[1]
TARGET_ROOT = ROOT / "config" / "e4_targets"


def test_pinned_targets_load_with_exact_release_source_and_runtime_assets() -> None:
    assert list_e4_target_ids() == ("oh-my-pi@16.2.13", "pi@0.57.1")

    pi = load_e4_target("pi@0.57.1")
    assert pi.descriptor["upstream"] == {
        "repository": "https://github.com/badlogic/pi-mono.git",
        "source": {
            "commit": "a9cedccdde77e9d765303463d8a6cd11c58f7a7f",
            "directory": "packages/coding-agent",
            "archive_sha256": (
                "bd64909b10a34c30890606f8787ee2ac47b9e7989e3db581978dd8214d62e87b"
            ),
            "archive_bytes": 4755355,
        },
        "package": {
            "name": "@mariozechner/pi-coding-agent",
            "version": "0.57.1",
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
        "argv": [
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
        ],
        "settings": {"retry.enabled": False, "retry.maxRetries": 0},
    }
    pi_config = pi.read_asset_text(pi.descriptor["execution"]["config_asset"])
    assert "target_id: pi@0.57.1" in pi_config
    assert "max_retries: 0" in pi_config

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


def test_target_loader_rejects_corrupt_runtime_asset(tmp_path: Path) -> None:
    copied_root = tmp_path / "e4_targets"
    shutil.copytree(TARGET_ROOT, copied_root)
    harness = copied_root / "pi" / "0.57.1" / "harness.yaml"
    harness.write_text(harness.read_text(encoding="utf-8") + "corrupt: true\n")

    with pytest.raises(E4TargetError, match="SHA-256 mismatch"):
        _load_e4_target_from_root(copied_root, "pi@0.57.1")


def test_target_loader_rejects_unsafe_descriptor_path(tmp_path: Path) -> None:
    copied_root = tmp_path / "e4_targets"
    shutil.copytree(TARGET_ROOT, copied_root)
    index_path = copied_root / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["targets"]["pi@0.57.1"]["descriptor"] = "../target.json"
    index_path.write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(E4TargetError, match="unsafe target resource path"):
        _load_e4_target_from_root(copied_root, "pi@0.57.1")
