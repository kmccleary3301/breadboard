from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, RefResolver


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "kernel" / "schemas"
SCHEMA_PATH = SCHEMA_DIR / "bb.config_explanation.v1.schema.json"
EXAMPLE_PATH = ROOT / "contracts" / "kernel" / "examples" / "config_explanation_minimal.json"
EXPLAIN_SCRIPT = ROOT / "scripts" / "authoring" / "explain_agent_config.py"
PUBLIC_DOSSIERS = [
    pytest.param("agent_configs/claude_code_2-1-63_e4_3-6-2026.yaml", id="claude-code"),
    pytest.param("agent_configs/codex_0-107-0_e4_3-6-2026.yaml", id="codex"),
    pytest.param("agent_configs/oh_my_opencode_3-10-0_e4_3-6-2026.yaml", id="oh-my-opencode"),
    pytest.param("agent_configs/opencode_1-2-17_e4_3-6-2026.yaml", id="opencode"),
]
DIAGNOSTIC_CLASSES = {
    "dead_ref",
    "unknown_tool",
    "missing_prompt",
    "unconsumed_field",
    "schema_violation",
    "registry_miss",
    "other",
}


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _validator() -> Draft202012Validator:
    schema = _load_json(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    store: dict[str, dict[str, Any]] = {}
    for schema_path in SCHEMA_DIR.glob("*.json"):
        loaded = _load_json(schema_path)
        schema_id = loaded.get("$id")
        if isinstance(schema_id, str):
            store[schema_id] = loaded
        store[schema_path.name] = loaded
    resolver = RefResolver(
        base_uri=SCHEMA_DIR.resolve().as_uri() + "/",
        referrer=schema,
        store=store,
    )
    return Draft202012Validator(schema, resolver=resolver)


def _validation_messages(record: dict[str, Any]) -> list[str]:
    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in sorted(
            _validator().iter_errors(record),
            key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
        )
    ]


def _assert_schema_valid(record: dict[str, Any]) -> None:
    assert _validation_messages(record) == []


def _run_explainer(config: str | Path, json_out: Path) -> tuple[int, dict[str, Any], str, str]:
    completed = subprocess.run(
        [
            sys.executable,
            str(EXPLAIN_SCRIPT),
            "--config",
            str(config),
            "--json",
            str(json_out),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert json_out.is_file(), completed.stderr
    return completed.returncode, _load_json(json_out), completed.stdout, completed.stderr


def _write_config_fixture(
    tmp_path: Path,
    *,
    mode_prompt: str = "@pack(base).plan",
    create_plan_prompt: bool = True,
    include_base_pack: bool = True,
) -> Path:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "system.md").write_text("system prompt\n", encoding="utf-8")
    if create_plan_prompt:
        (prompts_dir / "plan.md").write_text("plan prompt\n", encoding="utf-8")
    (tmp_path / "defs").mkdir()
    pack_block = ""
    if include_base_pack:
        pack_block = """
    base:
      system: prompts/system.md
      plan: prompts/plan.md
"""
    config_path = tmp_path / "agent.yaml"
    config_path.write_text(
        f"""version: 2
workspace: {{}}
providers:
  default_model: model-a
  models:
    - id: model-a
      adapter: mock
prompts:
  packs:{pack_block or ' {}'}
  injection:
    system_order:
      - '@pack(base).system'
tools:
  registry:
    paths:
      - defs
modes:
  - name: plan
    prompt: '{mode_prompt}'
    tools_enabled:
      - glob
    tools_disabled: []
loop:
  sequence:
    - mode: plan
""",
        encoding="utf-8",
    )
    return config_path


def test_config_explanation_minimal_example_validates_and_bad_diagnostics_are_rejected() -> None:
    example = _load_json(EXAMPLE_PATH)

    _assert_schema_valid(example)

    invalid_class = copy.deepcopy(example)
    invalid_class["diagnostics"] = [
        {"severity": "warning", "class": "broken", "path": "modes.plan", "message": "bad class"}
    ]
    assert any("'broken' is not one of" in message for message in _validation_messages(invalid_class))

    invalid_severity = copy.deepcopy(example)
    invalid_severity["diagnostics"] = [
        {"severity": "fatal", "class": "dead_ref", "path": "modes.plan", "message": "bad severity"}
    ]
    assert any("'fatal' is not one of" in message for message in _validation_messages(invalid_severity))


@pytest.mark.parametrize(
    ("ok", "diagnostics"),
    [
        pytest.param(
            True,
            [{"severity": "error", "class": "dead_ref", "path": "workspace.root", "message": "missing"}],
            id="ok-with-error",
        ),
        pytest.param(False, [], id="not-ok-without-error"),
    ],
)
def test_explanation_schema_rejects_ok_values_inconsistent_with_error_diagnostics(
    ok: bool,
    diagnostics: list[dict[str, str]],
) -> None:
    record = _load_json(EXAMPLE_PATH)
    record["ok"] = ok
    record["diagnostics"] = diagnostics

    errors = list(_validator().iter_errors(record))

    assert len(errors) == 1
    assert list(errors[0].absolute_path) == ["diagnostics"]


def test_explainer_checks_each_path_collection_item_and_ignores_dossier_commentary(tmp_path: Path) -> None:
    config_path = _write_config_fixture(tmp_path)
    (tmp_path / "secondary_defs").mkdir()
    (tmp_path / "present-input.txt").write_text("present\n", encoding="utf-8")
    text = config_path.read_text(encoding="utf-8").replace(
        """    paths:
      - defs
""",
        """    paths:
      - defs
      - secondary_defs
      - missing_registry
""",
    )
    config_path.write_text(
        text
        + """multi_agent:
  event_log_path: missing-events.jsonl
resources:
  files:
    - present-input.txt
    - missing-input.txt
dossier:
  example_path: missing-commentary.md
  references:
    paths:
      - missing-commentary-dir
""",
        encoding="utf-8",
    )

    exit_code, record, _stdout, stderr = _run_explainer(config_path, tmp_path / "explanation.json")

    assert exit_code == 1, stderr
    _assert_schema_valid(record)
    assert record["ok"] is False
    dead_refs = {
        diagnostic["path"]: diagnostic["message"]
        for diagnostic in record["diagnostics"]
        if diagnostic["class"] == "dead_ref"
    }
    assert {
        "multi_agent.event_log_path": "path reference does not exist: missing-events.jsonl",
        "resources.files.1": "path reference does not exist: missing-input.txt",
        "tools.registry.paths.2": "path reference does not exist: missing_registry",
    }.items() <= dead_refs.items()
    assert all(not path.startswith("dossier") for path in dead_refs)


@pytest.mark.parametrize("config_path", PUBLIC_DOSSIERS)
def test_public_e4_dossiers_emit_schema_valid_records_with_ok_matching_errors(
    config_path: str,
    tmp_path: Path,
) -> None:
    exit_code, record, stdout, stderr = _run_explainer(config_path, tmp_path / "explanation.json")

    error_diagnostics = [
        diagnostic for diagnostic in record["diagnostics"] if diagnostic["severity"] == "error"
    ]
    assert exit_code == (1 if error_diagnostics else 0), stderr
    assert stdout == ""
    _assert_schema_valid(record)
    assert record["schema_version"] == "bb.config_explanation.v1"
    assert record["config_path"] == config_path
    assert record["ok"] is (not error_diagnostics)
    assert {diagnostic["class"] for diagnostic in record["diagnostics"]} <= DIAGNOSTIC_CLASSES


@pytest.mark.parametrize(
    ("tool_name", "expected_path"),
    [
        pytest.param("glob", "modes.plan.tools_enabled.0", id="glob"),
        pytest.param("grep", "modes.plan.tools_enabled.0", id="grep"),
    ],
)
def test_absent_conceptual_mode_tools_are_warning_only(tool_name: str, expected_path: str, tmp_path: Path) -> None:
    config_path = _write_config_fixture(tmp_path)
    text = config_path.read_text(encoding="utf-8").replace("- glob", f"- {tool_name}")
    config_path.write_text(text, encoding="utf-8")

    exit_code, record, _stdout, stderr = _run_explainer(config_path, tmp_path / "explanation.json")

    assert exit_code == 0, stderr
    _assert_schema_valid(record)
    assert record["ok"] is True
    assert record["diagnostics"] == [
        {
            "severity": "warning",
            "class": "unknown_tool",
            "path": expected_path,
            "message": f"tool name '{tool_name}' is absent from loaded registry",
        }
    ]


def test_mode_tool_alias_resolves_existing_registry_tool_without_unknown_tool(tmp_path: Path) -> None:
    config_path = _write_config_fixture(tmp_path)
    (tmp_path / "defs" / "glob.yaml").write_text(
        """id: glob
name: glob
type_id: python
binding:
  handler: glob_handler
  type_id: python
""",
        encoding="utf-8",
    )
    text = config_path.read_text(encoding="utf-8")
    text = text.replace(
        """tools:
  registry:
    paths:
      - defs
""",
        """tools:
  registry:
    paths:
      - defs
  aliases:
    inspect_files: glob
""",
    ).replace("- glob", "- inspect_files")
    config_path.write_text(text, encoding="utf-8")

    exit_code, record, _stdout, stderr = _run_explainer(config_path, tmp_path / "explanation.json")

    assert exit_code == 0, stderr
    _assert_schema_valid(record)
    assert record["ok"] is True
    assert all(diagnostic["class"] != "unknown_tool" for diagnostic in record["diagnostics"])


def test_mode_pack_prompt_ref_resolves_to_prompt_file(tmp_path: Path) -> None:
    config_path = _write_config_fixture(tmp_path, mode_prompt="@pack(base).plan")

    exit_code, record, _stdout, stderr = _run_explainer(config_path, tmp_path / "explanation.json")

    assert exit_code == 0, stderr
    _assert_schema_valid(record)
    assert record["ok"] is True
    prompt_files = record["resolved_summary"]["prompt_files"]
    assert (tmp_path / "prompts" / "plan.md").resolve().as_posix() in prompt_files
    assert all(not prompt_file.startswith("@pack(") for prompt_file in prompt_files)
    assert [diagnostic["class"] for diagnostic in record["diagnostics"]] == ["unknown_tool"]


@pytest.mark.parametrize(
    ("case_kwargs", "expected_class", "expected_message"),
    [
        pytest.param(
            {"mode_prompt": "@pack(absent).plan"},
            "dead_ref",
            "prompt pack does not exist: absent",
            id="missing-pack",
        ),
        pytest.param(
            {"mode_prompt": "@pack(base).missing"},
            "dead_ref",
            "prompt pack slot does not exist: base.missing",
            id="missing-slot",
        ),
        pytest.param(
            {"mode_prompt": "@pack(base).plan", "create_plan_prompt": False},
            "missing_prompt",
            "prompt file does not exist: prompts/plan.md",
            id="missing-file",
        ),
    ],
)
def test_bad_mode_pack_prompt_refs_exit_nonzero_with_schema_valid_diagnostics(
    case_kwargs: dict[str, Any],
    expected_class: str,
    expected_message: str,
    tmp_path: Path,
) -> None:
    config_path = _write_config_fixture(tmp_path, **case_kwargs)

    exit_code, record, _stdout, stderr = _run_explainer(config_path, tmp_path / "explanation.json")

    assert exit_code == 1, stderr
    _assert_schema_valid(record)
    assert record["ok"] is False
    error_diagnostics = [diagnostic for diagnostic in record["diagnostics"] if diagnostic["severity"] == "error"]
    assert error_diagnostics
    assert all(diagnostic["class"] in DIAGNOSTIC_CLASSES for diagnostic in error_diagnostics)
    assert any(
        diagnostic["class"] == expected_class and diagnostic["message"] == expected_message
        for diagnostic in error_diagnostics
    )
