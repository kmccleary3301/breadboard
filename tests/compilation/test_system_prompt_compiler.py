from __future__ import annotations

from pathlib import Path

from agentic_coder_prototype.compilation.system_prompt_compiler import SystemPromptCompiler


def test_compile_v2_prompts_resolves_pack_path_from_explicit_base_dir_without_decorating_config(
    tmp_path: Path,
) -> None:
    """Prompt-pack files resolve from caller-owned roots without leaking resolution metadata into config."""
    prompt_base_dir = tmp_path / "config-root"
    prompt_pack = prompt_base_dir / "prompt-packs" / "system.md"
    prompt_pack.parent.mkdir(parents=True)
    prompt_pack.write_text("System instructions from the prompt pack.\n", encoding="utf-8")
    config = {
        "prompts": {
            "packs": {"base": {"system": "prompt-packs/system.md"}},
            "injection": {"system_order": ["@pack(base).system"]},
        }
    }

    result = SystemPromptCompiler(cache_dir=str(tmp_path / "cache")).compile_v2_prompts(
        config,
        mode_name="build",
        tools=[],
        dialects=[],
        prompt_base_dirs=[prompt_base_dir],
    )

    assert result["system"] == "System instructions from the prompt pack."
    assert config == {
        "prompts": {
            "packs": {"base": {"system": "prompt-packs/system.md"}},
            "injection": {"system_order": ["@pack(base).system"]},
        }
    }
