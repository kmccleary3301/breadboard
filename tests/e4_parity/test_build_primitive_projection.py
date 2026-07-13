from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from scripts.e4_parity import build_primitive_projection as projection

ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_schema_valid(contract: str, record: dict[str, object]) -> None:
    schema_path = projection._SCHEMA_PATHS[contract]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(record),
        key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
    )
    assert errors == []


def _build(tmp_path: Path) -> tuple[dict[str, object], Path]:
    output_dir = tmp_path / "projection"
    report = projection.build_projection(output_dir=output_dir)
    assert report["ok"] is True
    return report, output_dir


def test_builder_emits_schema_valid_projection_records(tmp_path: Path) -> None:
    report, output_dir = _build(tmp_path)

    config_graph = _load(output_dir / "effective_config_graph.v1.json")
    registry = _load(output_dir / "capability_registry.v1.json")
    tool_surface = _load(output_dir / "effective_tool_surface.v1.json")
    manifest = _load(output_dir / "primitive_projection_manifest.v1.json")

    _assert_schema_valid("bb.effective_config_graph.v1", config_graph)
    _assert_schema_valid("bb.capability_registry.v1", registry)
    _assert_schema_valid("bb.effective_tool_surface.v1", tool_surface)

    assert [item["ok"] for item in report["validation_status"]] == [True, True, True]
    assert manifest["schema_version"] == "bb.e4.primitive_projection_manifest.v1"
    assert manifest["input_hash"].startswith("sha256:")
    assert {output["contract"] for output in manifest["outputs"]} == {
        "bb.capability_registry.v1",
        "bb.effective_config_graph.v1",
        "bb.effective_tool_surface.v1",
    }
    assert all(input_row["sha256"].startswith("sha256:") for input_row in manifest["inputs"])
    assert str(ROOT) not in json.dumps([config_graph, registry, tool_surface, manifest], sort_keys=True)


def test_projection_content_maps_l1_l2_packet_data(tmp_path: Path) -> None:
    _, output_dir = _build(tmp_path)
    config_graph = _load(output_dir / "effective_config_graph.v1.json")
    registry = _load(output_dir / "capability_registry.v1.json")
    tool_surface = _load(output_dir / "effective_tool_surface.v1.json")

    effective_values = {item["path"]: item["value"] for item in config_graph["effective_values"]}
    assert effective_values["target.family"] == "oh_my_pi"
    assert effective_values["target.version"] == "@oh-my-pi/pi-coding-agent@16.2.13"
    assert effective_values["provider.model"] == "no-provider"
    assert effective_values["sandbox.mode"] == "read-only"
    assert effective_values["settings.tools.discoveryMode"] == "off"
    assert effective_values["settings.mcp.discoveryMode"] == "off"
    assert effective_values["settings.mcp.enableProjectConfig"] is False
    assert effective_values["settings.browser.enabled"] is False
    assert effective_values["settings.webSearch.enabled"] is False
    assert effective_values["settings.includeWorkspaceTree"] is False
    assert effective_values["provider.dispatchObserved"] is False
    assert effective_values["network.observed"] is False
    assert config_graph["env_gates"] == []
    assert config_graph["visibility"]["redacted_paths"] == []

    capabilities = {item["capability_id"]: item for item in registry["capabilities"]}
    builtin_ids = [cap_id for cap_id in capabilities if cap_id.startswith("tool.oh_my_pi.builtin.")]
    assert len(builtin_ids) == 30
    assert capabilities["tool.oh_my_pi.builtin.grep"]["metadata"]["aliases"] == ["search"]
    assert capabilities["tool.oh_my_pi.builtin.glob"]["metadata"]["aliases"] == ["find"]
    selected_content = capabilities["tool.oh_my_pi.custom.p6_echo"]["metadata"]["selected_execution"]["content"][0]
    assert selected_content["sha256"] == "sha256:" + hashlib.sha256(
        selected_content["text"].encode("utf-8")
    ).hexdigest()
    assert capabilities["runtime.oh_my_pi.custom_command.p6__l2"]["metadata"]["selected_execution"]["execStdout"] == (
        "p6-l2-custom-command"
    )
    assert capabilities["hook.oh_my_pi.pre.p6_l2"]["exposure"]["model_visible"] is False
    assert capabilities["hook.oh_my_pi.pre.p6_l2"]["metadata"]["handler_events"] == [
        "tool_call",
        "tool_result",
    ]

    assert len([cap for cap in capabilities.values() if cap["metadata"].get("kind") == "context_file"]) == 2
    assert len([cap for cap in capabilities.values() if cap["metadata"].get("kind") == "skill"]) == 2
    assert len([cap for cap in capabilities.values() if cap["metadata"].get("kind") == "slash_command"]) == 2
    assert len([cap for cap in capabilities.values() if cap["metadata"].get("kind") == "custom_command"]) == 3

    assert len(tool_surface["tool_ids"]) == 31
    assert "tool.oh_my_pi.custom.p6_echo" in tool_surface["tool_ids"]
    assert "tool.oh_my_pi.builtin.search" not in tool_surface["tool_ids"]
    assert "tool.oh_my_pi.builtin.find" not in tool_surface["tool_ids"]
    assert len(tool_surface["binding_ids"]) == len(tool_surface["tool_ids"])


def test_projection_output_is_byte_deterministic(tmp_path: Path) -> None:
    first_report, output_dir = _build(tmp_path)
    first_bytes = {path.name: path.read_bytes() for path in sorted(output_dir.glob("*.json"))}

    second_report = projection.build_projection(output_dir=output_dir)
    second_bytes = {path.name: path.read_bytes() for path in sorted(output_dir.glob("*.json"))}

    assert first_bytes == second_bytes
    assert {output["status"] for output in second_report["outputs"]} == {"unchanged"}
    assert first_report["input_hash"] == second_report["input_hash"]

    manifest = _load(output_dir / "primitive_projection_manifest.v1.json")
    output_hashes = {Path(row["path"]).name: row["sha256"] for row in manifest["outputs"]}
    assert output_hashes == {
        "capability_registry.v1.json": _sha256_file(output_dir / "capability_registry.v1.json"),
        "effective_config_graph.v1.json": _sha256_file(output_dir / "effective_config_graph.v1.json"),
        "effective_tool_surface.v1.json": _sha256_file(output_dir / "effective_tool_surface.v1.json"),
    }

    config_graph = _load(output_dir / "effective_config_graph.v1.json")
    graph_preimage = dict(config_graph)
    graph_preimage["graph_hash"] = None
    assert config_graph["graph_hash"] == projection._sha256_bytes(
        projection._canonical_json_bytes(graph_preimage)
    )

    tool_surface = _load(output_dir / "effective_tool_surface.v1.json")
    surface_preimage = dict(tool_surface)
    surface_preimage["surface_hash"] = None
    assert tool_surface["surface_hash"] == projection._sha256_bytes(
        projection._canonical_json_bytes(surface_preimage)
    )
