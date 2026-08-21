from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from agentic_coder_prototype.compilation import v2_loader
from agentic_coder_prototype.compilation.effective_config_graph import compile_effective_config_graph, graph_content_hash

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts/kernel/schemas/bb.effective_config_graph.v1.schema.json"


# Expected compiler API for P3.1:
# compile_effective_config_graph(
#     *,
#     graph_id: str,
#     layers: list[dict[str, Any]],
#     merge_policy: dict[str, Any] | None = None,
#     migrations: list[dict[str, Any]] | None = None,
#     redacted_paths: tuple[str, ...] = (),
#     host_only_paths: tuple[str, ...] = (),
#     env_required: dict[str, bool] | None = None,
# ) -> dict[str, Any]
#
# Layer input records are source-layer metadata plus a `values` mapping. Literal
# leaves become effective values. Leaves shaped as {"value": ..., "env_name":
# ..., "env_satisfied": ...} are env-backed leaves; when their path is redacted
# or secret-looking, the compiler emits a secret-ref and env gate instead of the
# literal value. The compiler emits schema-valid bb.effective_config_graph.v1
# records and keeps the existing v2_loader.load_agent_config API untouched.


def _schema_validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _schema_errors(record: dict[str, Any]) -> list[str]:
    return [
        f"{'.'.join(str(part) for part in error.absolute_path)}: {error.message}"
        for error in sorted(
            _schema_validator().iter_errors(record),
            key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
        )
    ]


def _assert_schema_valid(record: dict[str, Any]) -> None:
    assert _schema_errors(record) == []


def _canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, separators=(",", ": ")) + "\n").encode(
        "utf-8"
    )


def _sha256_record(record: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json_bytes(record)).hexdigest()


def _base_migrations() -> list[dict[str, Any]]:
    return [
        {
            "migration_id": "agent-config-v2-to-effective-config-graph-v1",
            "from_version": "agent-config-v2",
            "to_version": "bb.effective_config_graph.v1",
            "applied": True,
        }
    ]


def _merge_policy() -> dict[str, str]:
    return {
        "policy_id": "test-layer-precedence",
        "strategy": "deep-merge",
        "conflict_resolution": "highest-precedence",
    }


def _layer(
    *,
    layer_id: str,
    source_kind: str,
    precedence: int,
    values: dict[str, Any],
    model_visible: bool = True,
    host_visible: bool = True,
) -> dict[str, Any]:
    return {
        "layer_id": layer_id,
        "source_kind": source_kind,
        "scope": "unit-test",
        "precedence": precedence,
        "source_ref": f"test://{layer_id}",
        "layer_hash": "sha256:" + hashlib.sha256(_canonical_json_bytes(values)).hexdigest(),
        "model_visible": model_visible,
        "host_visible": host_visible,
        "values": values,
    }


def _compile(
    layers: list[dict[str, Any]],
    *,
    redacted_paths: tuple[str, ...] = (),
    host_only_paths: tuple[str, ...] = (),
    env_required: dict[str, bool] | None = None,
) -> dict[str, Any]:
    graph = compile_effective_config_graph(
        graph_id="unit_test_effective_config_graph",
        layers=layers,
        migrations=_base_migrations(),
        merge_policy=_merge_policy(),
        redacted_paths=redacted_paths,
        host_only_paths=host_only_paths,
        env_required=env_required,
    )
    _assert_schema_valid(graph)
    return graph


def _effective_values_by_path(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["path"]: item for item in graph["effective_values"]}


def test_precedence_layers_override_lower_layers_and_emit_schema_valid_graph() -> None:
    graph = _compile(
        [
            _layer(
                layer_id="defaults",
                source_kind="default",
                precedence=0,
                values={
                    "model": {
                        "name": "default-model",
                        "temperature": 0.2,
                    },
                    "provider": {"model": "default-provider"},
                    "sandbox": {"mode": "permissive"},
                },
            ),
            _layer(
                layer_id="project",
                source_kind="project",
                precedence=20,
                values={
                    "model": {"name": "project-model"},
                    "sandbox": {"mode": "read-only"},
                },
            ),
            _layer(
                layer_id="runtime",
                source_kind="runtime",
                precedence=40,
                values={
                    "sandbox": {"mode": "locked-down"},
                    "provider": {"model": "runtime-provider"},
                },
            ),
            _layer(
                layer_id="environment",
                source_kind="environment",
                precedence=60,
                values={
                    "provider": {"model": "env-provider"},
                },
            ),
        ]
    )

    values = _effective_values_by_path(graph)
    assert values["model.name"] == {
        "path": "model.name",
        "value_kind": "string",
        "value": "project-model",
        "source_layer_id": "project",
        "visibility": "model-visible",
        "env_gate_ids": [],
    }
    assert values["model.temperature"] == {
        "path": "model.temperature",
        "value_kind": "number",
        "value": 0.2,
        "source_layer_id": "defaults",
        "visibility": "model-visible",
        "env_gate_ids": [],
    }
    assert values["sandbox.mode"]["value"] == "locked-down"
    assert values["sandbox.mode"]["source_layer_id"] == "runtime"
    assert values["provider.model"] == {
        "path": "provider.model",
        "value_kind": "string",
        "value": "env-provider",
        "source_layer_id": "environment",
        "visibility": "model-visible",
        "env_gate_ids": [],
    }
    assert graph["visibility"]["model_visible_paths"] == [
        "model.name",
        "model.temperature",
        "provider.model",
        "sandbox.mode",
    ]
    assert graph["visibility"]["host_only_paths"] == []
    assert graph["visibility"]["redacted_paths"] == []
    assert [layer["layer_id"] for layer in graph["source_layers"]] == [
        "defaults",
        "project",
        "runtime",
        "environment",
    ]


def test_graph_hash_is_stable_and_computed_with_graph_hash_null() -> None:
    layers = [
        _layer(
            layer_id="runtime",
            source_kind="runtime",
            precedence=40,
            values={"sandbox.mode": "locked-down"},
        ),
        _layer(
            layer_id="defaults",
            source_kind="default",
            precedence=0,
            values={"sandbox.mode": "permissive"},
        ),
    ]

    first = _compile(layers)
    second = _compile(list(reversed(layers)))

    assert first == second
    preimage = copy.deepcopy(first)
    preimage["graph_hash"] = None
    assert first["graph_hash"] == graph_content_hash(preimage)

    poisoned = copy.deepcopy(first)
    poisoned["graph_hash"] = "sha256:" + "0" * 64
    assert graph_content_hash(poisoned) == first["graph_hash"]


def test_migration_records_preserve_from_to_and_applied_semantics() -> None:
    migrations = [
        {
            "migration_id": "legacy-yaml-to-agent-config-v2",
            "from_version": "legacy-yaml",
            "to_version": "agent-config-v2",
            "applied": True,
        },
        {
            "migration_id": "agent-config-v2-to-effective-config-graph-v1",
            "from_version": "agent-config-v2",
            "to_version": "bb.effective_config_graph.v1",
            "applied": True,
        },
        {
            "migration_id": "future-dry-run-not-applied",
            "from_version": "bb.effective_config_graph.v1",
            "to_version": "bb.effective_config_graph.v2",
            "applied": False,
        },
    ]

    graph = compile_effective_config_graph(
        graph_id="unit_test_effective_config_graph",
        layers=[
            _layer(
                layer_id="defaults",
                source_kind="default",
                precedence=0,
                values={"model.name": "default-model"},
            )
        ],
        migrations=migrations,
        merge_policy=_merge_policy(),
    )

    _assert_schema_valid(graph)
    assert graph["migrations"] == migrations


def test_secret_env_values_are_redacted_and_gate_on_environment_presence() -> None:
    graph = _compile(
        [
            _layer(
                layer_id="defaults",
                source_kind="default",
                precedence=0,
                values={"providers.openai.api_key": "placeholder"},
            ),
            _layer(
                layer_id="environment",
                source_kind="environment",
                precedence=60,
                model_visible=False,
                host_visible=True,
                values={
                    "providers.openai.api_key": {
                        "value": "sk-live-secret-value",
                        "env_name": "OPENAI_API_KEY",
                        "env_satisfied": True,
                    }
                },
            ),
        ],
        redacted_paths=("providers.openai.api_key",),
        env_required={"OPENAI_API_KEY": True},
    )

    values = _effective_values_by_path(graph)
    secret_value = values["providers.openai.api_key"]
    assert secret_value == {
        "path": "providers.openai.api_key",
        "value_kind": "secret-ref",
        "value": "secret://env/OPENAI_API_KEY",
        "source_layer_id": "environment",
        "visibility": "redacted",
        "env_gate_ids": ["env.OPENAI_API_KEY"],
    }
    assert graph["visibility"]["redacted_paths"] == ["providers.openai.api_key"]
    assert graph["visibility"]["model_visible_paths"] == []
    assert graph["visibility"]["host_only_paths"] == []
    assert graph["env_gates"] == [
        {
            "gate_id": "env.OPENAI_API_KEY",
            "env_name": "OPENAI_API_KEY",
            "required": True,
            "satisfied": True,
        }
    ]
    assert "sk-live-secret-value" not in json.dumps(graph, sort_keys=True)


def test_compiler_use_does_not_replace_or_mutate_v2_loader_behavior(tmp_path: Path, monkeypatch) -> None:
    legacy_config = tmp_path / "legacy.yaml"
    legacy_config.write_text(
        "providers:\n"
        "  openai:\n"
        "    model: legacy-model\n"
        "custom_flag: preserved\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("AGENT_SCHEMA_V2_ENABLED", raising=False)

    expected = {
        "providers": {"openai": {"model": "legacy-model"}},
        "custom_flag": "preserved",
    }
    assert v2_loader.load_agent_config(str(legacy_config)) == expected

    graph = _compile(
        [
            _layer(
                layer_id="defaults",
                source_kind="default",
                precedence=0,
                values={"model.name": "default-model"},
            )
        ]
    )

    assert graph["schema_version"] == "bb.effective_config_graph.v1"
    assert v2_loader.load_agent_config(str(legacy_config)) == expected
