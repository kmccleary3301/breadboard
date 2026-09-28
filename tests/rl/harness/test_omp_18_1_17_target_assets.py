from __future__ import annotations

import hashlib
import json
from pathlib import Path


def test_omp_target_assets_are_closed_and_four_tool() -> None:
    root = Path(__file__).resolve().parents[3] / "config/e4_targets/oh_my_pi/18.1.17"
    target = json.loads((root / "target.json").read_text())
    surface = json.loads((root / "tool-surface.json").read_text())
    policy = json.loads((root / "semantic-policy.json").read_text())
    assert target["target_id"] == "oh-my-pi@18.1.17"
    native = json.loads((root / "native-config.json").read_text())
    denials = native["capability_denials"]
    assert set(denials) == set(surface["denied_capabilities"])
    for capability in surface["denied_capabilities"]:
        entry = denials[capability]
        expected = {
            "schema_version": "bb.omp-capability-denial.v1",
            "capability": capability,
            "message": f"OMP capability denied: {capability}",
            "source_ref": "semantic-policy.json",
        }
        assert {key: entry[key] for key in expected} == expected
        if capability not in {"pty", "async"}:
            assert isinstance(entry.get("route"), dict)
    assert surface["ordered_tools"] == ["read", "bash", "edit", "write"]
    assert policy["requests"]["retry_transport_attempts"] == 1
    assert policy["turn_recovery"]["max_corrective_continuations"] == 3



def test_declared_tool_schemas_are_pinned_packet_shapes() -> None:
    root = Path(__file__).resolve().parents[3] / "config/e4_targets/oh_my_pi/18.1.17"
    surface = json.loads((root / "tool-surface.json").read_text())
    expected = {
        "read": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "i": {"description": "concise intent", "type": "string"},
                "path": {
                    "description": "Local path, internal URI (e.g. skill://), or URL. Inline selectors are supported.",
                    "type": "string",
                },
            },
            "required": ["path", "i"],
        },
        "bash": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "async": {"description": "run in background", "type": "boolean"},
                "command": {"type": "string"},
                "cwd": {"type": "string"},
                "env": {
                    "additionalProperties": {"type": "string"},
                    "properties": {},
                    "type": "object",
                },
                "i": {"description": "concise intent", "type": "string"},
                "pty": {"type": "boolean"},
                "timeout": {
                    "description": "timeout in seconds; 0 disables the command deadline; nonzero values are clamped to 1-3600",
                    "type": "number",
                },
            },
            "required": ["command", "i"],
        },
        "edit": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "i": {"description": "concise intent", "type": "string"},
                "input": {"type": "string"},
            },
            "required": ["input", "i"],
        },
        "write": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "content": {"description": "file content", "type": "string"},
                "i": {"description": "concise intent", "type": "string"},
                "path": {"description": "file path", "type": "string"},
            },
            "required": ["path", "content", "i"],
        },
    }
    assert set(surface["tools"]) == set(expected)
    assert {name: surface["tools"][name]["parameters"] for name in expected} == expected


def test_native_description_bytes_are_pinned() -> None:
    root = Path(__file__).resolve().parents[3] / "config/e4_targets/oh_my_pi/18.1.17"
    surface = json.loads((root / "tool-surface.json").read_text())
    assert surface["native_description_provenance"]["artifact_sha256"] == "cd548ef0285e5fc3987d79a1a47c1bc64fe93e1e68c71c0dc6a8ee3b30de244b"
    expected = {
        "read": "49e5ffad5701bffb28f32abe8e244de4d8f42ebb1ddefd94b216fbaf70103e6b",
        "bash": "6eeede5ff294f2ddad993e702bc14ebeacdf021a7612e31391ff9f02f69498a0",
        "edit": "e3f0898b6f449926b09810e5e53ef42492048caf18dce5ade65cfacf54ffc94e",
        "write": "3fb372a889a4aa0bcde2fd2ef61f27009b0f73873e5c87b46810127b8afc0e37",
    }
    assert surface["native_description_provenance"]["bounded_sha256"] == expected
    assert {name: hashlib.sha256(value.encode()).hexdigest() for name, value in surface["bounded_descriptions"].items()} == expected
