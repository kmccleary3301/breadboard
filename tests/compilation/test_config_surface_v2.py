from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest
from jsonschema import Draft202012Validator
import yaml

from agentic_coder_prototype.compilation import v2_loader

ROOT = Path(__file__).resolve().parents[2]
V2_SCHEMA_PATH = ROOT / "contracts/kernel/schemas/bb.agent_config_surface.v2.schema.json"
PUBLIC_V1_DOSSIER_CONFIG = ROOT / "agent_configs/atp_hilbert_like_gpt54_v1.yaml"
PUBLIC_V2_OPERATIONAL_SHAPES = [
    pytest.param(ROOT / "agent_configs/v2/claude_code_2-1-63_e4_3-6-2026.yaml", id="anthropic-cache-control"),
    pytest.param(ROOT / "agent_configs/v2/codex_0-107-0_e4_3-6-2026.yaml", id="codex-team"),
    pytest.param(ROOT / "agent_configs/v2/oh_my_opencode_3-10-0_e4_3-6-2026.yaml", id="rule-config-and-team"),
]
TYPED_SPINE_UNKNOWN_KEYS = [
    pytest.param(
        ROOT / "agent_configs/v2/oh_my_opencode_3-10-0_e4_3-6-2026.yaml",
        ("enhanced_tools", "validation", "rule_config", "one_bash_per_turn"),
        id="one-bash-per-turn-rule",
    ),
    pytest.param(
        ROOT / "agent_configs/v2/oh_my_opencode_3-10-0_e4_3-6-2026.yaml",
        ("enhanced_tools", "validation", "rule_config", "read_before_edit"),
        id="read-before-edit-rule",
    ),
    pytest.param(
        ROOT / "agent_configs/v2/codex_0-107-0_e4_3-6-2026.yaml",
        ("multi_agent", "team_config", "team", "agents", "main"),
        id="dynamic-team-agent",
    ),
    pytest.param(
        ROOT / "agent_configs/v2/claude_code_2-1-63_e4_3-6-2026.yaml",
        ("provider_tools", "anthropic", "prompt_cache", "cache_control"),
        id="cache-control-object",
    ),
]

LOADER_ENTRYPOINTS = [
    pytest.param(v2_loader.build_config_view, id="build-config-view"),
    pytest.param(v2_loader.load_agent_config, id="load-agent-config"),
]


def _write_config(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _minimal_v2_yaml(*, dossier: str = "  notes: runtime projection ignores this\n", extra_top_level: str = "") -> str:
    return f"""schema_version: bb.agent_config_surface.v2
version: 2
workspace:
  root: .
providers:
  default_model: openai/example
  models:
    - id: openai/example
      adapter: openai
modes:
  - name: default
    prompt: config/e4_targets/example/prompts/system.md
    tools_enabled:
      - read
loop:
  sequence:
    - mode: default
dossier:
{dossier}{extra_top_level}"""


def _v2_schema_errors(config: dict[str, Any]) -> list[str]:
    schema = json.loads(V2_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    return [
        f"/{'/'.join(str(part) for part in error.absolute_path)}: {error.message}"
        for error in sorted(
            validator.iter_errors(config),
            key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
        )
    ]


def _graph_paths(view: v2_loader.ConfigView) -> set[str]:
    return {str(item["path"]) for item in view.graph["effective_values"]}


def test_build_config_view_accepts_minimal_v2_strips_dossier_from_projection_and_graph(tmp_path: Path) -> None:
    """V2 dossier commentary may author alongside config but never enters runtime or graph values."""
    config_path = tmp_path / "agent.v2.yaml"
    _write_config(config_path, _minimal_v2_yaml())
    raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert _v2_schema_errors(raw_config) == []

    view = v2_loader.build_config_view(str(config_path))

    projection = view.as_dict()
    assert projection["schema_version"] == "bb.agent_config_surface.v2"
    assert projection["providers"]["default_model"] == "openai/example"
    assert "dossier" not in projection
    assert not {path for path in _graph_paths(view) if path == "dossier" or path.startswith("dossier.")}


@pytest.mark.parametrize("config_path", PUBLIC_V2_OPERATIONAL_SHAPES)
def test_representative_public_v2_operational_shapes_validate(config_path: Path) -> None:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert _v2_schema_errors(config) == []


@pytest.mark.parametrize(("config_path", "spine"), TYPED_SPINE_UNKNOWN_KEYS)
def test_v2_typed_operational_spines_reject_unknown_keys(
    config_path: Path,
    spine: tuple[str, ...],
) -> None:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    target = config
    for segment in spine:
        target = target[segment]
    target["unexpected_contract_key"] = True

    errors = _v2_schema_errors(config)

    assert len(errors) == 1
    assert errors[0].startswith(f"/{'/'.join(spine)}:")


@pytest.mark.parametrize("entrypoint", LOADER_ENTRYPOINTS)
def test_v2_surface_rejects_legacy_dossier_key_at_top_level(
    tmp_path: Path,
    entrypoint: Callable[[str], object],
) -> None:
    """V2's closed top-level schema rejects legacy dossier-only keys such as profile."""
    config_path = tmp_path / "agent.v2.yaml"
    _write_config(
        config_path,
        _minimal_v2_yaml(extra_top_level="profile:\n  name: legacy-dossier-key\n"),
    )

    with pytest.raises(ValueError) as exc:
        entrypoint(str(config_path))

    message = str(exc.value)
    assert "agent config schema error at /:" in message
    assert "Additional properties are not allowed" in message
    assert "profile" in message


@pytest.mark.parametrize("entrypoint", LOADER_ENTRYPOINTS)
def test_v2_surface_rejects_non_object_dossier_before_runtime_projection(
    tmp_path: Path,
    entrypoint: Callable[[str], object],
) -> None:
    """A scalar dossier is invalid authoring input, not a field to silently strip."""
    config_path = tmp_path / "agent.v2.yaml"
    _write_config(config_path, _minimal_v2_yaml(dossier="  not-object\n"))

    with pytest.raises(ValueError) as exc:
        entrypoint(str(config_path))

    message = str(exc.value)
    assert "agent config schema error at /dossier:" in message
    assert "is not of type 'object'" in message


@pytest.mark.parametrize("entrypoint", LOADER_ENTRYPOINTS)
def test_present_unsupported_schema_version_is_rejected_by_loader_entrypoints(
    tmp_path: Path,
    entrypoint: Callable[[str], object],
) -> None:
    """A present schema_version must dispatch only to known agent config surface versions."""
    config_path = tmp_path / "agent.unsupported.yaml"
    _write_config(
        config_path,
        """schema_version: bb.agent_config_surface.v3
version: 2
workspace:
  root: .
providers:
  default_model: openai/example
  models:
    - id: openai/example
      adapter: openai
modes:
  - name: default
loop:
  sequence:
    - mode: default
""",
    )

    with pytest.raises(ValueError) as exc:
        entrypoint(str(config_path))

    assert str(exc.value) == "unsupported agent config surface schema_version: bb.agent_config_surface.v3"

def test_schema_less_non_numeric_version_stays_on_legacy_raw_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A legacy schema-less config may use a non-numeric version label without triggering v1 validation."""
    monkeypatch.setenv("BREADBOARD_CONFIG_AUTHORITY", "config")
    config_path = tmp_path / "agent.legacy.yaml"
    _write_config(
        config_path,
        """version: alpha
settings:
  ok: true
""",
    )

    expected = {"version": "alpha", "settings": {"ok": True}}

    assert v2_loader.load_agent_config(str(config_path)) == expected
    assert v2_loader.build_config_view(str(config_path)).as_dict() == expected



def test_public_v1_dossier_with_top_level_profile_still_loads_as_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    """Legacy public v1 dossiers omit schema_version and keep their top-level profile payload loadable."""
    monkeypatch.delenv("BREADBOARD_CONFIG_AUTHORITY", raising=False)
    monkeypatch.delenv("BREADBOARD_CONFIG_EFFECTIVE_DEFAULT", raising=False)
    monkeypatch.delenv("CI", raising=False)

    loaded = v2_loader.load_agent_config(str(PUBLIC_V1_DOSSIER_CONFIG))
    view = v2_loader.build_config_view(str(PUBLIC_V1_DOSSIER_CONFIG))

    assert loaded["version"] == 2
    assert loaded["profile"] == {"name": "atp-hilbert-like-gpt54-v1"}
    assert "schema_version" not in loaded
    assert view.as_dict()["profile"] == {"name": "atp-hilbert-like-gpt54-v1"}
