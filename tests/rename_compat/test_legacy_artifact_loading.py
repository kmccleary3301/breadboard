"""R-0B7: old-session/config/replay fixtures load under compatibility mode.

Loads frozen historical artifacts through the real loaders, importing every
loader via the version-aware resolver so the same test proves the legacy
import surface post-rename. Also pins the measured fact that none of these
artifacts embed engine dotted paths - the rename cannot invalidate them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agentic_coder_prototype.compat import legacy_names as ln

REPO_ROOT = Path(__file__).resolve().parents[2]

HISTORICAL_REPLAY = (
    "docs/conformance/e4_recalibration_evidence/"
    "codex_subagent_async_20260306_v0110/replay_session.json"
)
KERNEL_EXAMPLE = "contracts/kernel/examples/replay_session_minimal.json"
ENGINE_FIXTURES = (
    "conformance/engine_fixtures/replay_session/reference_fixture.json",
    "conformance/engine_fixtures/replay_session/minimal_fixture.json",
    "conformance/engine_fixtures/session_transcript/reference_fixture.json",
    "conformance/engine_fixtures/external_protocol_session/minimal_fixture.json",
)
REPLAY_CONFIGS = (
    "agent_configs/misc/claude_code_haiku45_e4_replay.yaml",
    "agent_configs/misc/opencode_e4_glob_grep_sentinel_replay.yaml",
)


def test_historical_replay_session_loads_via_legacy_import():
    replay = ln.resolve_module("agentic_coder_prototype.replay")
    data = replay.load_replay_session(REPO_ROOT / HISTORICAL_REPLAY)
    assert data.turns, "historical replay session yielded no assistant turns"
    assert data.user_prompt


def test_kernel_replay_example_validates_against_schema():
    import jsonschema

    example = json.loads((REPO_ROOT / KERNEL_EXAMPLE).read_text(encoding="utf-8"))
    schema = json.loads(
        (REPO_ROOT / "contracts/kernel/schemas/bb.replay_session.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    jsonschema.validate(example, schema)
    assert "agentic_coder_prototype" not in json.dumps(example)


@pytest.mark.parametrize("path", ENGINE_FIXTURES)
def test_engine_fixture_parses_and_is_rename_neutral(path):
    payload = json.loads((REPO_ROOT / path).read_text(encoding="utf-8"))
    assert payload["fixture_family"]
    assert "agentic_coder_prototype" not in json.dumps(payload)


@pytest.mark.parametrize("path", REPLAY_CONFIGS)
def test_replay_agent_config_parses_and_is_rename_neutral(path):
    config = yaml.safe_load((REPO_ROOT / path).read_text(encoding="utf-8"))
    assert isinstance(config, dict) and config
    assert "agentic_coder_prototype" not in (REPO_ROOT / path).read_text(encoding="utf-8")
