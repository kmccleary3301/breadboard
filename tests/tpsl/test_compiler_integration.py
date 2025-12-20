from agentic_coder_prototype.compilation.system_prompt_compiler import SystemPromptCompiler
from agentic_coder_prototype.tool_calling import ToolDefinition, ToolParameter


def test_compile_v2_prompts_uses_tpsl_when_enabled(tmp_path, monkeypatch):
	cfg = {
		"version": 2,
		"providers": {"default_model": "openrouter/openai/gpt-5-nano", "models": [{"id": "openrouter/openai/gpt-5-nano", "adapter": "openai"}]},
		"prompts": {
			"packs": {"base": {"system": "implementations/system_prompts/default.md"}},
			"injection": {"system_order": ["@pack(base).system"], "per_turn_order": []},
			"tool_prompt_synthesis": {
				"enabled": True,
				"dialects": {
					"pythonic": {"system_full": "implementations/tool_prompt_synthesis/pythonic/system_full.j2.md"}
				},
				"selection": {"by_mode": {"build": "pythonic"}},
				"detail": {"system": "full", "per_turn": "short"}
			},
		},
		"modes": [{"name": "build", "prompt": "@pack(base).system"}],
		"loop": {"sequence": [{"mode": "build"}]},
	}

	tools = [
		ToolDefinition(type_id="python", name="run_shell", description="Run a shell command", parameters=[
			ToolParameter(name="command", type="string"),
		])
	]
	comp = SystemPromptCompiler()
	out = comp.compile_v2_prompts(cfg, mode_name="build", tools=tools, dialects=["pythonic02"]) 
	sys_text = out["system"]
	assert ("TOOL CATALOG" in sys_text)


