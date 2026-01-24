from agentic_coder_prototype.conductor_components import initialize_yaml_tools, tool_defs_from_yaml


class _DummyConductor:
    def __init__(self, config):
        self.config = config
        self.yaml_tools = []
        self.yaml_tool_manipulations = {}


def test_registry_paths_load_multiple_tool_dirs() -> None:
    conductor = _DummyConductor(
        {
            "tools": {
                "registry": {
                    "paths": [
                        "implementations/tools/defs_oc",
                        "implementations/tools/defs",
                    ],
                    "include": ["todo.create"],
                }
            }
        }
    )

    initialize_yaml_tools(conductor)
    tool_defs = tool_defs_from_yaml(conductor)
    assert tool_defs is not None
    assert {tool.name for tool in tool_defs} == {"todo.create"}

