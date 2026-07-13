from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from agentic_coder_prototype.compilation import v2_loader

ROOT = Path(__file__).resolve().parents[2]
DOSSIER_ONLY_TOP_LEVEL = {"profile", "tool_packs", "tool_bindings", "terminal_sessions"}
ORIGINAL_HASHES = {
    "agent_configs/codex_0-107-0_e4_3-6-2026.yaml": "sha256:2e2ba957f396b8dbc7d5adb4b35d6ccaccf322d21b792bafe1d996ec0d661768",
    "agent_configs/claude_code_2-1-63_e4_3-6-2026.yaml": "sha256:d940ce0063528bad25c0692970a9ab567300a9ff39d056f46a5c70966997f072",
    "agent_configs/opencode_1-2-17_e4_3-6-2026.yaml": "sha256:0ddc16b9d3567a4be4e4dfe8a16f75f31d2a24c2abab3344692a508e3f224ff2",
    "agent_configs/oh_my_opencode_3-10-0_e4_3-6-2026.yaml": "sha256:70b2095d04b4a5ae81491ef2cfe5b0ebc3445b51c643d6a9d972cac347bb5a47",
}
VARIANT_PAIRS = [
    (
        "agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
        "agent_configs/v2/codex_0-107-0_e4_3-6-2026.yaml",
    ),
    (
        "agent_configs/claude_code_2-1-63_e4_3-6-2026.yaml",
        "agent_configs/v2/claude_code_2-1-63_e4_3-6-2026.yaml",
    ),
    (
        "agent_configs/opencode_1-2-17_e4_3-6-2026.yaml",
        "agent_configs/v2/opencode_1-2-17_e4_3-6-2026.yaml",
    ),
    (
        "agent_configs/oh_my_opencode_3-10-0_e4_3-6-2026.yaml",
        "agent_configs/v2/oh_my_opencode_3-10-0_e4_3-6-2026.yaml",
    ),
]
OLD_ROOT_VARIANTS = [
    "agent_configs/codex_0-107-0_e4_3-6-2026_v2.yaml",
    "agent_configs/claude_code_2-1-63_e4_3-6-2026_v2.yaml",
    "agent_configs/opencode_1-2-17_e4_3-6-2026_v2.yaml",
    "agent_configs/oh_my_opencode_3-10-0_e4_3-6-2026_v2.yaml",
]


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_operational_projection(config_path: str) -> dict[str, Any]:
    projection = deepcopy(v2_loader.load_agent_config(str(ROOT / config_path)))
    for key in DOSSIER_ONLY_TOP_LEVEL | {"dossier", "schema_version"}:
        projection.pop(key, None)

    tools = projection.get("tools")
    if isinstance(tools, dict):
        registry = tools.get("registry")
        paths = registry.get("paths") if isinstance(registry, dict) else None
        if paths and tools.get("defs_dir") == paths[0]:
            tools.pop("defs_dir", None)


    loop = projection.get("loop")
    if isinstance(loop, dict) and projection.get("turn_strategy") == loop.get("turn_strategy"):
        projection.pop("turn_strategy", None)
    for key in ("turn_strategy", "concurrency", "long_running"):
        if projection.get(key) == {}:
            projection.pop(key)

    return projection


@pytest.mark.parametrize(("original", "variant"), VARIANT_PAIRS)
def test_v2_dossier_variant_matches_original_operational_projection(original: str, variant: str) -> None:
    assert _canonical_operational_projection(variant) == _canonical_operational_projection(original)


@pytest.mark.parametrize(("original", "variant"), VARIANT_PAIRS)
def test_v2_dossier_variant_keeps_original_public_dossier_byte_frozen(original: str, variant: str) -> None:
    assert _sha256(ROOT / original) == ORIGINAL_HASHES[original]
    assert (ROOT / variant).is_file()


@pytest.mark.parametrize("old_root_variant", OLD_ROOT_VARIANTS)
def test_v2_dossier_variant_removes_old_root_level_duplicate(old_root_variant: str) -> None:
    assert not (ROOT / old_root_variant).exists()
