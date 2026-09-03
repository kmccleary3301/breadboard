from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from breadboard.rl.harness.qualification import (
    FIXTURE_ROOT,
    STORE_NAMES,
    install_runtime_paths,
)


UNKNOWN_CANDIDATE_NAME = "fixture-candidate-canonical-7d4a6f"


def test_runtime_fixture_installs_exact_distinct_secret_and_root_authorities(
    tmp_path: Path,
) -> None:
    installed = install_runtime_paths(tmp_path / "installed")

    assert tuple(installed.stores) == STORE_NAMES
    root_identities = set()
    for path in installed.stores.values():
        current = path.stat(follow_symlinks=False)
        assert stat.S_ISDIR(current.st_mode)
        assert stat.S_IMODE(current.st_mode) == 0o700
        assert current.st_uid == os.geteuid()
        root_identities.add((current.st_dev, current.st_ino))
    assert len(root_identities) == len(STORE_NAMES)

    secret_identities = set()
    secret_payloads = set()
    for path in installed.secrets.values():
        current = path.stat(follow_symlinks=False)
        assert stat.S_ISREG(current.st_mode)
        assert current.st_nlink == 1
        assert current.st_uid == os.geteuid()
        assert stat.S_IMODE(current.st_mode) == 0o400
        secret_identities.add((current.st_dev, current.st_ino))
        secret_payloads.add(path.read_bytes())
    assert len(secret_identities) == len(installed.secrets)
    assert len(secret_payloads) == len(installed.secrets)
    assert secret_payloads == set(installed.launch_seeds.values())
    second = install_runtime_paths(tmp_path / "second-installed")
    assert set(second.launch_seeds.values()).isdisjoint(installed.launch_seeds.values())


def test_https_runtime_uses_a_dedicated_0600_copy_of_checked_in_server_key(
    tmp_path: Path,
) -> None:
    installed = install_runtime_paths(tmp_path / "installed")
    source = FIXTURE_ROOT / "tls" / "server.key.pem"
    copied = installed.tls_server_key

    assert copied != source.resolve()
    assert copied.read_bytes() == source.read_bytes()
    assert stat.S_IMODE(copied.stat(follow_symlinks=False).st_mode) == 0o600
    assert (
        copied.stat(follow_symlinks=False).st_ino
        != source.stat(follow_symlinks=False).st_ino
    )
    assert (
        hashlib.sha256(copied.read_bytes()).digest()
        == hashlib.sha256(source.read_bytes()).digest()
    )


def test_generated_unknown_candidate_name_is_absent_from_production_source() -> None:
    production_roots = tuple(
        Path(name)
        for name in (
            "agent_configs",
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
        if Path(name).is_dir()
    )
    production_extensions = {
        ".cfg",
        ".ini",
        ".json",
        ".py",
        ".sh",
        ".toml",
        ".yaml",
        ".yml",
    }
    occurrences = [
        path
        for root in production_roots
        for path in root.rglob("*")
        if path.is_file()
        and (path.suffix in production_extensions or path.name.endswith("Dockerfile"))
        and UNKNOWN_CANDIDATE_NAME.encode() in path.read_bytes()
    ]
    assert occurrences == []
