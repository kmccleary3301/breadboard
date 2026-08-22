from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_json(relative_path: str) -> dict[str, object]:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


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
