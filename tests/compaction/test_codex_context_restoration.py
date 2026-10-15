"""Stock contextual fragments and explicit compaction placement phases."""
from types import SimpleNamespace

import pytest

from breadboard_engine.compaction import CompactionContext, CompactionState, ProjectionTarget, load_compaction_config
from breadboard_engine.compaction.methods import SummaryResponse
from breadboard_engine.compaction.params import Params, BuildEnv, PresetError
from breadboard_engine.compaction.presets import build_recipe
from breadboard_engine.compaction.primitives.context_markers import contextual_user, marker_pairs

PAIR = {"start": "<environment_context>", "end": "</environment_context>"}


@pytest.mark.parametrize("text,expected", [
    (" \t<ENVIRONMENT_CONTEXT>cwd</environment_context>\n", True),
    ("<environment_context>cwd", False),
    ("cwd</environment_context>", False),
    ("<environment_context>cwd</environment_context> extra", False),
    ("\x1c<environment_context>cwd</environment_context>", False),
])
def test_paired_marker_matches_rust_fragment_boundaries(text, expected):
    pairs = marker_pairs(Params({"markers": [PAIR]}, "test", BuildEnv(None)), "markers")
    assert contextual_user({"role": "user", "content": text}, pairs) is expected
    assert not contextual_user({"role": "assistant", "content": text}, pairs)


def test_empty_marker_is_rejected():
    with pytest.raises(PresetError, match="nonempty"):
        marker_pairs(Params({"markers": [{"start": "", "end": "end"}]}, "test", BuildEnv(None)), "markers")


@pytest.mark.parametrize("phase", ["pre_turn", "mid_turn"])
def test_production_recipe_restores_initial_context_at_explicit_phase(phase):
    config = load_compaction_config({"enabled": True, "preset": "codex@0.139.0", "contextWindow": 16000})
    calls = []
    class Summary:
        def complete(self, request):
            calls.append(request)
            return SummaryResponse("checkpoint")
    developer = {"role": "developer", "content": "permissions"}
    environment = {"role": "user", "content": "<environment_context>cwd</environment_context>"}
    old = {"role": "user", "content": "old task"}
    pending = {"role": "user", "content": "new task"}
    messages = [{"role": "system", "content": "instructions"}, developer, environment, old,
                {"role": "assistant", "content": "work"}]
    if phase == "pre_turn":
        messages.append(pending)
    context = CompactionContext(messages, CompactionState(), config.settings, "threshold",
                                ProjectionTarget("openai", "responses", "gpt-5.5"), 16000, 14500,
                                summarizer=Summary(), sampling_phase=phase,
                                pending_user_index=len(messages) - 1 if phase == "pre_turn" else None)
    recipe = build_recipe(config)
    output = recipe.pipeline.stages["summary"].step.run(context)
    assert environment not in [m for m in output.record.summary_messages if m.get("content") == "old task"]
    projected = list(output.record.summary_messages)
    if phase == "pre_turn":
        assert [m["role"] for m in projected] == ["user", "user", "developer", "user"]
        assert projected[0] == old and projected[-2:] == [developer, environment]
        assert output.record.first_kept_index == len(messages) - 1
        assert pending not in calls[0].messages
    else:
        assert projected[:3] == [developer, environment, old]
        assert output.record.first_kept_index == len(messages)
    assert len(calls) == 1


@pytest.mark.parametrize("models,whole", [([], False), ([{"id": "gpt-5.5", "context_window": 272000}], True)])
def test_codex_skills_budget_without_a_configured_context_window(tmp_path, monkeypatch, models, whole):
    from breadboard_engine.compaction.controller import CompactionController
    from breadboard_engine.compilation.context_builders.codex import build_context
    from breadboard_engine.state.session_state import SessionState

    workspace = (tmp_path / "workspace").resolve()
    (workspace / ".git").mkdir(parents=True)
    for index in range(12):
        skill = workspace / ".agents/skills" / f"skill{index:02}"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"---\nname: skill{index:02}\ndescription: {'d' * 1000}\n---\n")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    config = {"prompts": {"initial_context": {}}, "providers": {"models": models},
              "compaction": {"enabled": True, "preset": "codex@0.139.0"}}
    owner = SimpleNamespace(config=config, workspace=str(workspace), model="gpt-5.5",
                            compaction_controller=CompactionController(config))
    state = SessionState(str(workspace), "image", {})
    state.messages.append({"role": "system", "content": ""})
    state.provider_messages.append({"role": "system", "content": ""})
    developer, _ = build_context(owner, state, {"native_environment": {"cwd": str(workspace), "shell": "zsh"}},
                                 fresh=True, user_message=None)
    skills = next(part["text"] for part in developer["content"] if part["text"].startswith("<skills_instructions>"))
    # render.rs:143-158: a known window budgets 2% in tokens; an unknown one budgets 8000 characters.
    assert (skills.count("d" * 1000) == 12) is whole
