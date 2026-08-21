from breadboard_engine.compilation.tool_prompt_synth import ToolPromptSynthesisEngine


def test_tpsl_pythonic_render_system_full():
    engine = ToolPromptSynthesisEngine()
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
    text, metadata = engine.render(
        "pythonic",
        "system_full",
        tools,
        templates={
            "system_full": "implementations/tool_prompt_synthesis/pythonic/system_full.j2.md",
        },
    )
    assert "## run_shell" in text
    assert "Run a shell command" in text
    assert metadata["template_id"] == "implementations/tool_prompt_synthesis/pythonic/system_full.j2.md"




