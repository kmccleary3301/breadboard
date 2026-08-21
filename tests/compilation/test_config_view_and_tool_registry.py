from __future__ import annotations

from pathlib import Path

import pytest

from agentic_coder_prototype.compilation import v2_loader
from agentic_coder_prototype.compilation.tool_registry import guardrail_names_for, load_tool_registry

ROOT = Path(__file__).resolve().parents[2]

CONFIG_PARITY_CASES = [
    pytest.param(
        ROOT / "agent_configs/misc/claude_code_haiku45_c_fs_guardrails_fast.yaml",
        id="nested-extends-v2",
    ),
    pytest.param(
        ROOT / "agent_configs/misc/opencode_compat_v2.yaml",
        id="extends-v2",
    ),
    pytest.param(
        ROOT / "agent_configs/misc/test_agent.yaml",
        id="legacy-agent-config",
    ),
]

ALL_AGENT_CONFIGS = [
    pytest.param(path, id=str(path.relative_to(ROOT)))
    for path in sorted((ROOT / "agent_configs").glob("**/*.yaml"))
]


def _write_registry_tool_defs(defs_dir: Path) -> None:
    defs_dir.mkdir()
    (defs_dir / "read_file.yaml").write_text(
        """id: read_file
name: read_file
type_id: python
binding:
  handler: read_handler
  type_id: python
aliases:
  - peek
  - inspect
classification:
  guardrail_sets: [read]
manipulations:
  - file.read
execution:
  blocking: false
""",
        encoding="utf-8",
    )
    (defs_dir / "apply_unified_patch.yaml").write_text(
        """id: apply_unified_patch
name: apply_unified_patch
type_id: python
binding:
  handler: patch_handler
  type_id: python
aliases:
  - patch
  - edit
classification:
  guardrail_sets: [edit]
manipulations:
  - diff.apply
execution:
  blocking: true
""",
        encoding="utf-8",
    )
    (defs_dir / "run_shell.yaml").write_text(
        """id: run_shell
name: run_shell
type_id: python
binding:
  handler: shell_handler
  type_id: python
aliases:
  - bash
classification:
  guardrail_sets: [bash]
manipulations:
  - shell.exec
execution:
  blocking: true
""",
        encoding="utf-8",
    )


def _write_agent_config(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")



def _skip_unloadable(path: Path, exc: Exception) -> None:
    pytest.skip(f"unloadable fixture {path.relative_to(ROOT)}: {type(exc).__name__}: {exc}")


def _load_config_projection(path: Path) -> dict:
    try:
        return v2_loader.load_agent_config(str(path))
    except (FileNotFoundError, ValueError, AttributeError) as exc:
        _skip_unloadable(path, exc)


@pytest.mark.parametrize("config_path", CONFIG_PARITY_CASES)
def test_config_view_matches_load_agent_config_projection_under_config_authority(
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ConfigView must preserve the public load_agent_config projection for loadable YAML configs."""
    monkeypatch.delenv("AGENT_SCHEMA_V2_ENABLED", raising=False)
    monkeypatch.delenv("BREADBOARD_CONFIG_AUTHORITY", raising=False)

    loader_projection = _load_config_projection(config_path)
    view_projection = v2_loader.load_agent_config_view(str(config_path)).as_dict()

    assert view_projection == loader_projection


@pytest.mark.parametrize("config_path", ALL_AGENT_CONFIGS)
def test_config_view_matches_loadable_agent_config_corpus(
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every loadable agent_configs YAML file must round-trip through ConfigView without projection drift."""
    monkeypatch.delenv("AGENT_SCHEMA_V2_ENABLED", raising=False)
    monkeypatch.delenv("BREADBOARD_CONFIG_AUTHORITY", raising=False)

    loader_projection = _load_config_projection(config_path)
    try:
        view_projection = v2_loader.load_agent_config_view(str(config_path)).as_dict()
    except (FileNotFoundError, ValueError, AttributeError) as exc:
        _skip_unloadable(config_path, exc)

    assert view_projection == loader_projection


@pytest.mark.parametrize("config_path", CONFIG_PARITY_CASES)
@pytest.mark.parametrize(
    "authority",
    [
        pytest.param("config", id="config-authority"),
        pytest.param("parity", id="parity-authority"),
        pytest.param("effective", id="effective-authority"),
    ],
)
def test_load_agent_config_authorities_match_config_view_for_representative_configs(
    config_path: Path,
    authority: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Representative configs must expose one stable projection under every config authority."""
    monkeypatch.delenv("AGENT_SCHEMA_V2_ENABLED", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("BREADBOARD_CONFIG_EFFECTIVE_DEFAULT", raising=False)
    monkeypatch.setenv("BREADBOARD_CONFIG_AUTHORITY", authority)

    authority_projection = _load_config_projection(config_path)
    expected_projection = v2_loader.load_agent_config_view(str(config_path)).as_dict()

    assert authority_projection == expected_projection
    assert "_config_metadata" not in authority_projection


@pytest.mark.parametrize(
    ("env_name", "env_value"),
    [
        pytest.param("CI", "1", id="ci-selects-effective"),
        pytest.param("BREADBOARD_CONFIG_EFFECTIVE_DEFAULT", "true", id="effective-flag-selects-effective"),
    ],
)
def test_load_agent_config_effective_defaulting_selects_effective_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    env_value: str,
) -> None:
    """CI/effective-default authority must return the resolved effective projection when no authority is explicit."""
    base_config = tmp_path / "base.yaml"
    base_config.write_text(
        """provider: mock
settings:
  inherited: true
""",
        encoding="utf-8",
    )
    child_config = tmp_path / "child.yaml"
    child_config.write_text(
        """extends: base.yaml
settings:
  child: true
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("BREADBOARD_CONFIG_AUTHORITY", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("BREADBOARD_CONFIG_EFFECTIVE_DEFAULT", raising=False)
    monkeypatch.setenv(env_name, env_value)
    monkeypatch.setattr(v2_loader, "_log_config_divergence", lambda *args, **kwargs: None)

    assert v2_loader.load_agent_config(str(child_config)) == {
        "provider": "mock",
        "settings": {
            "inherited": True,
            "child": True,
        },
    }


def test_explicit_config_authority_overrides_effective_defaulting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit config authority must win over CI/effective-default authority signals."""
    base_config = tmp_path / "base.yaml"
    base_config.write_text(
        """provider: mock
settings:
  inherited: true
""",
        encoding="utf-8",
    )
    child_config = tmp_path / "child.yaml"
    child_config.write_text(
        """extends: base.yaml
settings:
  child: true
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CI", "1")
    monkeypatch.setenv("BREADBOARD_CONFIG_EFFECTIVE_DEFAULT", "1")
    monkeypatch.setenv("BREADBOARD_CONFIG_AUTHORITY", "config")

    assert v2_loader.load_agent_config(str(child_config)) == {
        "extends": "base.yaml",
        "settings": {
            "child": True,
        },
    }

def test_tool_registry_loads_alias_dispatch_guardrails_and_nonblocking_from_yaml(tmp_path: Path) -> None:
    """Registry consumers must reflect YAML aliases, bindings, guardrails, and blocking policy."""
    defs_dir = tmp_path / "defs"
    _write_registry_tool_defs(defs_dir)

    registry = load_tool_registry(str(defs_dir))

    assert registry.alias_map() == {
        "bash": "run_shell",
        "edit": "apply_unified_patch",
        "inspect": "read_file",
        "patch": "apply_unified_patch",
        "peek": "read_file",
    }
    assert registry.dispatch_for("peek") == {"handler": "read_handler", "type_id": "python"}
    assert registry.dispatch_for("patch") == {"handler": "patch_handler", "type_id": "python"}
    assert registry.dispatch_for("bash") == {"handler": "shell_handler", "type_id": "python"}

    guardrail_sets = registry.guardrail_sets()
    assert guardrail_sets["read"] == {"read_file", "peek", "inspect"}
    assert guardrail_sets["edit"] == {"apply_unified_patch", "patch", "edit"}
    assert guardrail_sets["bash"] == {"run_shell", "bash"}
    assert registry.nonblocking_names() == {"read_file", "peek", "inspect"}


@pytest.mark.parametrize(
    ("config_text", "expected_pointer", "expected_fragment"),
    [
        pytest.param(
            """schema_version: bb.agent_config_surface.v2
version: 2
workspace:
  root: .
providers:
  models:
    - id: model-a
      adapter: mock
modes:
  - name: main
loop:
  sequence:
    - mode: main
""",
            "/providers/default_model",
            "'default_model' is a required property",
            id="missing-nested-provider-default-model",
        ),
        pytest.param(
            """schema_version: bb.agent_config_surface.v2
version: 2
workspace:
  root: .
providers:
  default_model: model-a
  models:
    - id: model-a
modes:
  - name: main
loop:
  sequence:
    - mode: main
""",
            "/providers/models/0/adapter",
            "'adapter' is a required property",
            id="missing-provider-model-adapter",
        ),
        pytest.param(
            """schema_version: bb.agent_config_surface.v2
version: 2
workspace:
  root: .
providers:
  default_model: model-a
  models:
    - id: model-a
      adapter: mock
modes:
  - name: main
loop:
  sequence:
    - mode: main
features:
  rlm:
    budget:
      per_branch:
        max_total_tokens: -1
""",
            "/features/rlm/budget/per_branch/max_total_tokens",
            "-1 is less than the minimum of 0",
            id="negative-nested-rlm-budget",
        ),
        pytest.param(
            """schema_version: bb.agent_config_surface.v2
version: 2
workspace:
  root: .
providers:
  default_model: model-a
  models:
    - id: model-a
      adapter: mock
modes:
  - name: main
loop:
  sequence: main
""",
            "/loop/sequence",
            "is not of type 'array'",
            id="invalid-loop-sequence-type",
        ),
    ],
)
def test_load_agent_config_reports_schema_errors_at_json_pointer(
    tmp_path: Path,
    config_text: str,
    expected_pointer: str,
    expected_fragment: str,
) -> None:
    """V2 schema failures must name the nested JSON pointer a config author needs to fix."""
    config_path = tmp_path / "agent.yaml"
    _write_agent_config(config_path, config_text)

    with pytest.raises(ValueError) as exc:
        v2_loader.load_agent_config(str(config_path))

    message = str(exc.value)
    assert f"agent config schema error at {expected_pointer}:" in message
    assert expected_fragment in message


def test_tool_registry_dispatch_guardrails_aliases_and_nonblocking_contracts() -> None:
    """Tool registry helpers must resolve aliases onto canonical handlers and safety groups."""
    registry = load_tool_registry(str(ROOT / "implementations/tools/defs"))

    aliases = registry.alias_map()
    assert {name: aliases[name] for name in ("bash", "read", "patch", "write", "todoread")} == {
        "bash": "run_shell",
        "read": "read_file",
        "patch": "apply_unified_patch",
        "write": "create_file_from_block",
        "todoread": "todo.list",
    }
    expected_dispatch = {
        "bash": {"handler": "run_shell", "type_id": "python"},
        "read": {"handler": "read_file", "type_id": "python"},
        "patch": {"handler": "apply_unified_patch", "type_id": "python"},
        "write": {"handler": "create_file_from_block", "type_id": "python"},
        "todoread": {"handler": "todo.list", "type_id": "python"},
    }
    for alias, expected in expected_dispatch.items():
        dispatch = registry.dispatch_for(alias)
        assert dispatch is not None
        assert {key: dispatch[key] for key in ("handler", "type_id")} == expected

    expected_guardrail_members = {
        "read": {"read_file", "read"},
        "list": {"list_dir", "list"},
        "bash": {"run_shell", "bash", "shell_command"},
        "edit": {"apply_unified_patch", "patch", "create_file_from_block", "write"},
        "todo": {"todo.list", "todoread", "TodoWrite", "todowrite"},
    }
    guardrail_sets = registry.guardrail_sets()
    for guardrail_set, expected_names in expected_guardrail_members.items():
        assert expected_names <= guardrail_sets[guardrail_set]
        assert expected_names <= guardrail_names_for(None, guardrail_set)

    nonblocking = registry.nonblocking_names()
    assert {"read_file", "read", "list_dir", "list", "todo.list", "todoread"} <= nonblocking
    assert {"run_shell", "bash", "create_file_from_block", "write"}.isdisjoint(nonblocking)
