import uuid

import pytest

from breadboard_engine.agent import _admit_standalone_run
from breadboard_engine.provider.contract_wire import ProviderContractError
from breadboard_engine.provider.contract_requests import ProviderRequest


FUNCTION = {"type": "function", "name": "exec_command", "description": "Execute a command.", "parameters": {"type": "object", "properties": {}}, "strict": False}
NAMESPACE = {"type": "namespace", "name": "multi_agent_v1", "description": "Agent tools.", "tools": [FUNCTION]}
HOSTED = {"type": "web_search", "external_web_access": True}


def request(tools, variant=None):
    return ProviderRequest(stream=True, messages=[{"role": "user", "content": "task"}], tools=tools, _tool_api_variant=variant)


@pytest.mark.parametrize("tool", [FUNCTION, NAMESPACE, HOSTED])
def test_responses_shapes_are_retained_exactly(tool):
    assert request([tool], "responses").as_dict()["tools"] == [tool]


@pytest.mark.parametrize("variant", [None, "chat", "messages"])
@pytest.mark.parametrize("tool", [FUNCTION, NAMESPACE, HOSTED])
def test_other_variants_reject_flat_responses_shapes(tool, variant):
    with pytest.raises(ProviderContractError):
        request([tool], variant)


@pytest.mark.parametrize("tool", [
    {**FUNCTION, "extra": True},
    {**FUNCTION, "input_schema": {}},
    {**FUNCTION, "parameters": []},
    {**FUNCTION, "strict": None},
    {**FUNCTION, "strict": "false"},
    {"type": "function", "name": "missing_parameters"},
    {**NAMESPACE, "tools": [HOSTED]},
    {**NAMESPACE, "tools": {}},
    {**NAMESPACE, "name": ""},
    {**HOSTED, "external_web_access": "true"},
    {**HOSTED, "extra": True},
    {"type": "unapproved_hosted_tool"},
])
def test_responses_admission_rejects_malformed_shapes(tool):
    with pytest.raises(ProviderContractError):
        request([tool], "responses")


def test_legacy_admission_remains_unchanged():
    wrapped = {"type": "function", "function": {key: value for key, value in FUNCTION.items() if key != "type"}}
    expected = [{key: value for key, value in FUNCTION.items() if key != "type"}]
    assert request([wrapped]).tools == expected
    assert request([wrapped], "responses").tools == expected


def test_non_codex_standalone_admission_has_only_legacy_correlations(monkeypatch):
    from breadboard_engine.compilation.context_builders import codex
    def forbidden(*args):
        raise AssertionError("legacy admission must not capture native environment")
    monkeypatch.setattr(codex, "admit_environment", forbidden)
    monkeypatch.setattr(codex, "new_session_id", forbidden)
    context = _admit_standalone_run({})
    assert set(context) == {"session_id", "input_id", "turn_id"}
    for key, prefix in (("session_id", "run-"), ("input_id", "input-"), ("turn_id", "turn-")):
        assert context[key].startswith(prefix)
        assert uuid.UUID(hex=context[key][len(prefix):]).version == 4


def test_native_standalone_admission_generates_uuid7(tmp_path):
    context = _admit_standalone_run({"providers": {"session_uuid_version": 7}}, str(tmp_path))
    assert uuid.UUID(context["session_id"]).version == 7
    assert context["native_environment"]["cwd"] == str(tmp_path.resolve())


@pytest.mark.parametrize("tool", [FUNCTION, NAMESPACE, HOSTED])
def test_recorded_responses_tool_union_is_valid(tool):
    from jsonschema import Draft202012Validator
    from breadboard_engine.provider.contract_wire import _provider_exchange_v2_validator
    schema = _provider_exchange_v2_validator().schema
    Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/tool"}).validate(tool)


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("cap", [None, 1000, 64000])
def test_episode_output_cap_binding_is_opt_in(selected, cap):
    from types import SimpleNamespace
    from breadboard_engine.provider.contracts import ProviderRuntimeContext
    from breadboard_engine.provider.runtimes.openai.responses import OpenAIResponsesRuntime
    from breadboard_engine.state.session_state import SessionState
    config = {"provider_tools": {"openai": {"include_reasoning": False,
              "responses_max_output_tokens_from_context": selected}}}
    state = SessionState(workspace=".", image="lane", config=config)
    context = ProviderRuntimeContext(state, config, extra={"max_tokens": cap})
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai"))
    payload = runtime.project_request_body(model="gpt-5.5", messages=[{"role": "user", "content": "task"}],
                                           tools=None, stream=False, context=context)
    if selected and cap is not None:
        assert payload["max_output_tokens"] == cap
    else:
        assert "max_output_tokens" not in payload


def test_sealed_summary_cap_wins_over_episode_binding():
    from types import SimpleNamespace
    from breadboard_engine.provider.contracts import ProviderRuntimeContext
    from breadboard_engine.provider.runtimes.openai.responses import OpenAIResponsesRuntime
    from breadboard_engine.state.session_state import SessionState
    config = {"provider_tools": {"openai": {"include_reasoning": False,
              "responses_max_output_tokens_from_context": True}}}
    state = SessionState(workspace=".", image="lane", config=config)
    context = ProviderRuntimeContext(state, config, extra={"max_tokens": 64000,
        "compaction_summary": True, "compaction_request_params": {"max_output_tokens": 2000}})
    runtime = OpenAIResponsesRuntime(SimpleNamespace(provider_id="openai"))
    payload = runtime.project_request_body(model="gpt-5.5", messages=[{"role": "user", "content": "summary"}],
                                           tools=[], stream=False, context=context)
    assert payload["max_output_tokens"] == 2000


@pytest.mark.parametrize("changes", [
    {"module": "os"},
    {"module": "breadboard_engine.compilation.context_builders"},
    {"module": "breadboard_engine.compilation.context_builders.codex.external"},
    {"callable": "arbitrary_callback"},
    {"extra": True},
    {"developer_instructions": "not an array"},
])
def test_startup_hook_schema_rejects_unapproved_imports_and_fields(changes):
    import yaml
    from breadboard_engine.compaction_lanes.codex_lane import PROFILE
    from breadboard_engine.compilation.v2_loader import _validate_v2
    config = yaml.safe_load(PROFILE.read_text())
    config["prompts"]["initial_context"].update(changes)
    with pytest.raises(ValueError, match="agent config schema error"):
        _validate_v2(config)


def test_startup_hook_schema_admits_repo_callback_and_developer_role_text():
    import yaml
    from breadboard_engine.compaction_lanes.codex_lane import PROFILE
    from breadboard_engine.compilation.v2_loader import _validate_v2
    config = yaml.safe_load(PROFILE.read_text())
    config["prompts"]["initial_context"]["developer_instructions"] = ["Native role instructions."]
    _validate_v2(config)


def test_startup_hook_rejects_module_resolved_outside_repo(monkeypatch):
    from types import SimpleNamespace
    from breadboard_engine.compilation.context_builders import build_initial_context, util
    owner = SimpleNamespace(config={"prompts": {"initial_context": {
        "module": "breadboard_engine.compilation.context_builders.codex", "callable": "build_context"}}})
    monkeypatch.setattr(util, "find_spec", lambda _: SimpleNamespace(origin="/tmp/outside/codex.py"))
    with pytest.raises(ValueError, match="resolve inside the repository"):
        build_initial_context(owner, None, {}, fresh=True, user_message={})
