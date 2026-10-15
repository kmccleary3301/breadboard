"""Concurrent/local stock capabilities are optional, strict booleans in both SDKs."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

from tests.compilation.test_config_surface_v2 import _minimal_v2_yaml, _v2_schema_errors

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "sdk/ts-kernel-contracts/dist/src/generated/registry.js"


@pytest.mark.parametrize("field", ["native_concurrent_prompts", "native_local_only", "pty"])
@pytest.mark.parametrize("value", [False, True, None, 1, "true"])
def test_python_native_capability_schema_is_boolean(field, value):
    config = yaml.safe_load(_minimal_v2_yaml())
    config["tools"] = {field: value}
    assert bool(_v2_schema_errors(config)) is (type(value) is not bool)


@pytest.mark.parametrize("value", [False, True, None, 1, "true"])
def test_python_failed_turn_continuation_schema_is_boolean(value):
    config = yaml.safe_load(_minimal_v2_yaml())
    config["session"] = {"failed_turn_continues": value}
    assert bool(_v2_schema_errors(config)) is (type(value) is not bool)


def test_native_capabilities_are_optional_and_default_false():
    config = yaml.safe_load(_minimal_v2_yaml())
    assert _v2_schema_errors(config) == []
    schema = json.loads((ROOT / "contracts/kernel/schemas/bb.agent_config_surface.v2.schema.json").read_bytes())
    tools = schema["properties"]["tools"]
    for name in ("native_concurrent_prompts", "native_local_only", "pty"):
        assert tools["properties"][name] == {"type": "boolean", "default": False}
        assert name not in tools.get("required", [])
    session = schema["properties"]["session"]
    assert session["properties"]["failed_turn_continues"] == {"type": "boolean", "default": False}
    assert "session" not in schema.get("required", [])
    assert "failed_turn_continues" not in session.get("required", [])


def test_compiled_node_validator_enforces_native_capability_booleans():
    node = shutil.which("node")
    if node is None or not REGISTRY.is_file():
        pytest.skip("Build the Node kernel-contracts SDK to exercise its compiled validator")
    config = yaml.safe_load(_minimal_v2_yaml())
    script = """
import { GENERATED_SCHEMAS } from './sdk/ts-kernel-contracts/dist/src/generated/registry.js';
let text = '';
for await (const chunk of process.stdin) text += chunk;
const config = JSON.parse(text);
const entry = GENERATED_SCHEMAS['https://breadboard.dev/contracts/kernel/schemas/bb.agent_config_surface.v2.schema.json'];
const checks = [entry.validate(config)];
for (const field of ['native_concurrent_prompts', 'native_local_only', 'pty']) {
  for (const value of [false, true, null, 1, 'true']) {
    checks.push(entry.validate({...config, tools: {[field]: value}}));
  }
}
for (const value of [false, true, null, 1, 'true']) {
  checks.push(entry.validate({...config, session: {failed_turn_continues: value}}));
}
console.log(JSON.stringify(checks));
"""
    completed = subprocess.run([node, "--input-type=module", "--eval", script], cwd=ROOT,
        input=json.dumps(config), text=True, capture_output=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == [True, *([True, True, False, False, False] * 4)]
