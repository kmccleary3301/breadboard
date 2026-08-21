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


def test_read_before_edit_blocks_unread_opencode_update():
    validator = SequenceValidator(
        enabled_rules=["read_before_edit"],
        config={"read_before_edit": {"mode": "error"}},
        workspace_root=".",
    )
    tool_call = {
        "function": "apply_unified_patch",
        "arguments": {
            "patch": "*** Begin Patch\n*** Update File: existing.py\n@@\n-old\n+new\n*** End Patch\n"
        },
    }
    context = {
        "files_read_this_session": [],
        "files_created_this_session": [],
        "files_last_read_at": {},
    }

    err = validator.validate_call(tool_call, context)

    assert err == "File existing.py must be read with read_file before editing."


def test_file_exists_blocks_missing_opencode_update_but_allows_add(tmp_path):
    validator = SequenceValidator(
        enabled_rules=["file_exists"],
        config={},
        workspace_root=str(tmp_path),
    )
    context = {"files_created_this_session": []}
    update = {
        "function": "patch",
        "arguments": {
            "patchText": "*** Begin Patch\n*** Update File: missing.py\n@@\n-old\n+new\n*** End Patch\n"
        },
    }
    add = {
        "function": "patch",
        "arguments": {
            "patchText": "*** Begin Patch\n*** Add File: new.py\n+new\n*** End Patch\n"
        },
    }

    assert validator.validate_call(update, context) == (
        "File missing.py does not exist and is required for patch"
    )
    assert validator.validate_call(add, context) is None


