from __future__ import annotations

import json
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_json(relative_path: str) -> dict[str, object]:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def engine_range_contains(range_value: str, version: str) -> bool:
    lower, upper = range_value.split()
    if not lower.startswith(">=") or not upper.startswith("<"):
        raise ValueError(f"unsupported engine interface range: {range_value}")

    def triplet(value: str) -> tuple[int, int, int]:
        parts = value.split(".")
        if len(parts) != 3 or any(not part.isdigit() for part in parts):
            raise ValueError(f"unsupported engine interface version: {value}")
        return int(parts[0]), int(parts[1]), int(parts[2])

    current = triplet(version)
    return triplet(lower[2:]) <= current < triplet(upper[1:])


def test_primary_tui_authority_uses_canonical_sdk_and_non_distributable_legacy_harness() -> None:
    authority = read_json("config/product/tui-release.json")
    sdk_package = read_json("sdk/ts/package.json")
    legacy_package = read_json("tui_skeleton/package.json")

    assert authority["schemaVersion"] == "bb.product-tui-authority.v1"
    assert authority["status"] == "primary"
    assert authority["repository"] == "https://github.com/kmccleary3301/breadboard-tui"
    assert authority["defaultBranch"] == "main"
    assert authority["binary"] == "bb"

    sdk = authority["sdk"]
    assert isinstance(sdk, dict)
    assert sdk["name"] == sdk_package["name"] == "@breadboard/sdk"
    assert sdk["version"] == sdk_package["version"] == "0.3.0"

    legacy = authority["legacyHarness"]
    assert isinstance(legacy, dict)
    assert legacy["path"] == "tui_skeleton"
    assert legacy["distributable"] is False
    assert legacy["packageName"] == legacy_package["name"] == "breadboard-tui-contract-harness"
    assert "bin" not in legacy_package
    scripts = legacy_package["scripts"]
    assert isinstance(scripts, dict)
    assert "postbuild" not in scripts

    contribution_fork = authority["contributionFork"]
    assert isinstance(contribution_fork, dict)
    assert contribution_fork["releaseAuthority"] is False


def test_primary_tui_authority_pins_sdk_identity_and_compatible_engine(monkeypatch) -> None:
    authority = read_json("config/product/tui-release.json")
    sdk = authority["sdk"]
    upstream = authority["upstream"]
    assert isinstance(sdk, dict)
    assert isinstance(upstream, dict)

    for value in (
        authority["sourceCommit"],
        authority["sourceTree"],
        upstream["commit"],
        upstream["tree"],
        sdk["backendCommit"],
        sdk["backendTree"],
    ):
        assert isinstance(value, str)
        assert len(value) == 40
        int(value, 16)

    artifact_sha256 = sdk["artifactSha256"]
    assert isinstance(artifact_sha256, str)
    assert len(artifact_sha256) == 64
    int(artifact_sha256, 16)

    sdk_version = sdk["version"]
    assert isinstance(sdk_version, str)
    artifact = ROOT / "tui_skeleton" / "vendor" / f"breadboard-sdk-{sdk_version}.tgz"
    assert artifact.is_file()
    with tarfile.open(artifact, "r:gz") as archive:
        package_file = archive.extractfile("package/package.json")
        assert package_file is not None
        package = json.loads(package_file.read())
    assert package["name"] == sdk["name"]
    assert package["version"] == sdk_version

    monkeypatch.delenv("BREADBOARD_ENGINE_VERSION", raising=False)
    from breadboard_engine.api.cli_bridge.app import create_app
    from breadboard_engine.api.cli_bridge.events import PROTOCOL_VERSION

    engine_version = create_app().version
    engine_interface_range = sdk["engineInterfaceRange"]
    assert isinstance(engine_interface_range, str)
    assert engine_range_contains(engine_interface_range, engine_version)
    assert PROTOCOL_VERSION == "1.0"
