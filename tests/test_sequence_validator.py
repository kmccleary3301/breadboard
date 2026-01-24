from tool_calling.sequence_validator import SequenceValidator


def _make_context_with_recent_run_shell():
    # Minimal recent operation with a bash call
    return {
        "recent_operations": [
            {
                "function": "run_shell",
                "timestamp": 9999999999.0,
            }
        ]
    }


def test_one_bash_per_turn_rule_blocks_second_bash():
    # Enable the new rule only
    validator = SequenceValidator(
        enabled_rules=["one_bash_per_turn"],
        config={"one_bash_per_turn": {"turn_window_seconds": 999999}},
        workspace_root="."
    )
    tool_call = {"function": "run_shell", "arguments": {"command": "echo hi"}}
    context = _make_context_with_recent_run_shell()
    err = validator.validate_call(tool_call, context)
    assert err and "one bash" in err.lower()


def test_one_bash_per_turn_allows_first_bash():
    validator = SequenceValidator(
        enabled_rules=["one_bash_per_turn"],
        config={"one_bash_per_turn": {"turn_window_seconds": 5}},
        workspace_root="."
    )
    tool_call = {"function": "run_shell", "arguments": {"command": "echo hi"}}
    context = {"recent_operations": []}
    err = validator.validate_call(tool_call, context)
    assert err is None


