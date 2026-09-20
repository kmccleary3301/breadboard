from importlib.resources import files
import json

import pytest

from breadboard.rl.harness.runners.mini_semantics import (
    LimitsExceeded,
    MiniAction,
    MiniSemanticsState,
    format_toolcall_observation_messages,
)


CONFIG = json.loads(
    files("config.e4_targets").joinpath("mini_swe_agent/2.4.6/native-config.json").read_text()
)


def _state():
    return MiniSemanticsState(
        task="Keep {{ task }} literally in the issue text",
        system_template=CONFIG["agent"]["system_template"],
        instance_template=CONFIG["agent"]["instance_template"],
        observation_template=CONFIG["model"]["observation_template"],
        format_error_template=CONFIG["model"]["format_error_template"],
        runtime_template_vars={
            "system": "Linux", "release": "synthetic-release",
            "version": "synthetic-version", "machine": "x86_64",
        },
    )


def _response(*arguments, finish_reason="tool_calls"):
    return {
        "id": "chatcmpl-mini",
        "choices": [{
            "finish_reason": finish_reason,
            "message": {
                "role": "assistant", "content": None,
                "reasoning_content": "source reasoning field",
                "tool_calls": [
                    {"id": f"call-{i}", "type": "function", "function": {"name": "bash", "arguments": args}}
                    for i, args in enumerate(arguments)
                ],
            },
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }


def _consume(state, response):
    state.begin_query()
    return state.parse_and_commit_response(response, cost=0.125, timestamp=123.0)


@pytest.mark.parametrize("bad", ['{"command":', '{"wrong": "key"}', "[]"])
def test_malformed_second_call_commits_feedback_but_no_assistant_or_actions(bad):
    state = _state()
    response = _response('{"command":"effect A"}', bad, '{"command":"effect C"}')
    parsed = _consume(state, response)
    assert parsed.actions == ()
    assert [message["role"] for message in state.messages] == ["system", "user", "user"]
    assert state.messages[-1]["extra"] == {
        "interrupt_type": "FormatError", "cost": 0.125, "response": response,
    }
    assert state.cost == 0.125
    assert state.n_calls == 1


def test_submission_preserves_grouped_assistant_without_observation_commit():
    state = _state()
    response = _response('{"command":"A"}', '{"command":"submit B"}', '{"command":"C"}')
    _consume(state, response)
    state.commit_native_exit("Submitted", "payload\n")
    assert [message["role"] for message in state.messages] == ["system", "user", "assistant", "exit"]
    assert {key: value for key, value in state.messages[2].items() if key != "extra"} == response["choices"][0]["message"]
    assert state.messages[-1] == {
        "role": "exit", "content": "payload\n",
        "extra": {"exit_status": "Submitted", "submission": "payload\n"},
    }


def test_returned_environment_error_resets_format_error_streak():
    state = _state()
    _consume(state, _response())
    _consume(state, _response('{"command":4}'))
    state.commit_whole_batch_observations([{
        "output": "", "returncode": -1, "exception_info": "source execution error",
    }], timestamp=124.0)
    _consume(state, _response())
    _consume(state, _response())
    assert not state.is_exited
    _consume(state, _response())
    assert state.exit_status == "RepeatedFormatError"
    assert [message["role"] for message in state.messages[-2:]] == ["user", "exit"]


def test_eighth_query_finishes_phases_before_next_guard_without_ninth_sample():
    state = _state()
    for _ in range(8):
        _consume(state, _response('{"command":"continue"}'))
        state.commit_whole_batch_observations([{
            "output": "ok", "returncode": 0, "exception_info": "",
        }], timestamp=124.0)
    assert state.messages[-1]["role"] == "tool"
    with pytest.raises(LimitsExceeded):
        state.begin_query()
    assert state.n_calls == 8
    assert state.messages[-1] == {
        "role": "exit", "content": "LimitsExceeded",
        "extra": {"exit_status": "LimitsExceeded", "submission": ""},
    }


@pytest.mark.parametrize("size", [9999, 10000, 10001])
def test_native_character_boundary_and_jinja_json_escaping(size):
    output = '<é>\n"' + "x" * (size - 5)
    message = format_toolcall_observation_messages(
        actions=[MiniAction("command", "call")],
        outputs=[{"output": output, "returncode": 0, "exception_info": ""}],
        observation_template=CONFIG["model"]["observation_template"],
        timestamp=123.0,
    )[0]
    rendered = json.loads(message["content"])
    if size < 10000:
        assert rendered["output"] == output
    else:
        assert rendered["output_head"] == output[:5000]
        assert rendered["output_tail"] == output[-5000:]
        assert rendered["elided_chars"] == size - 10000
    assert "\\u003c" in message["content"]
    assert message["extra"]["raw_output"] == output


def test_argument_values_and_order_survive_while_unused_keys_are_not_actions():
    state = _state()
    ordered_command = {"z-first": 1, "a-last": 2}
    values = ["", ["printf '%s' \"$1\"", "shell", "value"], ordered_command, None]
    parsed = _consume(state, _response(*(json.dumps({"command": value, "unused": [1, 2]}) for value in values)))
    assert [action.command for action in parsed.actions] == values
    assert list(parsed.actions[2].command) == ["z-first", "a-last"]
    assert all(set(action.to_dict()) == {"command", "tool_call_id"} for action in parsed.actions)
    prepared = state.prepare_request_history()
    assert prepared[-1]["content"] is None
    assert prepared[-1]["reasoning_content"] == "source reasoning field"
    assert "extra" not in prepared[-1]
    assert "Keep {{ task }} literally in the issue text" in prepared[1]["content"]
