from tool_calling.tool_yaml_loader import load_yaml_tools
from tool_calling.provider_schema import build_openai_tools_schema_from_yaml


def test_build_openai_tools_schema_from_yaml():
    loaded = load_yaml_tools("implementations/tools/defs")
    schema = build_openai_tools_schema_from_yaml(loaded.tools)
    # Ensure we got a list of function schemas
    assert isinstance(schema, list) and schema
    assert all(s.get("type") == "function" for s in schema)
    names = {s["function"]["name"] for s in schema}
    # Expect known tools mapped
    assert "run_shell" in names
    assert "read_file" in names


