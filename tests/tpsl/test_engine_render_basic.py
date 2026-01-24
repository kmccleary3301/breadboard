from agentic_coder_prototype.compilation.tool_prompt_synth import ToolPromptSynthesisEngine


def test_tpsl_pythonic_render_system_full():
    engine = ToolPromptSynthesisEngine(root="implementations/tool_prompt_synthesis")
    tools = [
        {
            "name": "run_shell",
            "description": "Run a shell command",
            "parameters": [
                {"name": "command", "type": "string"},
                {"name": "timeout", "type": "int", "default": 30},
            ],
        }
    ]
    text, template_id = engine.render(
        "pythonic",
        "system_full",
        tools,
        template_map={
            "system_full": "implementations/tool_prompt_synthesis/pythonic/system_full.j2.md",
        },
    )
    assert "def run_shell(" in text
    assert "Run a shell command" in text
    assert ":" in template_id




