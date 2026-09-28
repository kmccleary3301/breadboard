"""Tests for Mini native shell action helper and sandbox dispatch."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from breadboard.rl.harness import mini_tools
from breadboard.rl.harness.mini_tools import (
    DEFAULT_RAW_OUTPUT_LIMIT,
    execute_mini_shell_action,
)


def test_mini_environment_overrides() -> None:
    result = execute_mini_shell_action(
        {"command": "printf '%s|' \"$PAGER\" \"$MANPAGER\" \"$LESS\" \"$PIP_PROGRESS_BAR\" \"$TQDM_DISABLE\""}
    )
    assert result["returncode"] == 0
    assert result["output"] == "cat|cat|-R|off|1|"
    assert "exit" not in result


def test_command_mapping_order_preserved() -> None:
    """Ordered command mapping executes the first key in insertion order."""
    cmd_map = {"printf mapping_first": "val1", "printf mapping_second": "val2"}
    result = execute_mini_shell_action({"command": cmd_map})
    assert result["returncode"] == 0
    assert result["output"] == "mapping_first"
    assert "exit" not in result


def test_native_subprocess_behavior_variants() -> None:
    """Sequences, empty command, and non-string types follow native subprocess."""
    # Sequence: sh -c arg0 arg1 ...
    seq_result = execute_mini_shell_action({"command": ["echo seq", "ignored_pos0"]})
    assert seq_result["returncode"] == 0
    assert seq_result["output"] == "seq\n"

    # Empty string: sh -c ""
    empty_result = execute_mini_shell_action({"command": ""})
    assert empty_result["returncode"] == 0
    assert empty_result["output"] == ""

    # Non-iterable (e.g. integer): TypeError caught into returncode -1
    int_result = execute_mini_shell_action({"command": 123})
    assert int_result["extra"]["exception_type"] == "TypeError"
    assert "is not iterable" in int_result["exception_info"]


def test_submission_action_ordering_and_guards() -> None:
    """Submission requires COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT on first line with rc==0."""
    # Successful submission with payload
    sub_action = {
        "command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && echo 'final output payload'"
    }
    sub_result = execute_mini_shell_action(sub_action)
    assert sub_result["returncode"] == 0
    assert "exit" in sub_result
    assert sub_result["exit"] == {
        "role": "exit",
        "content": "final output payload\n",
        "extra": {
            "exit_status": "Submitted",
            "submission": "final output payload\n",
        },
    }
    assert "output" in sub_result
    assert "returncode" in sub_result
    assert "exception_info" in sub_result

    # Empty payload submission
    empty_sub = {"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}
    empty_res = execute_mini_shell_action(empty_sub)
    assert empty_res["returncode"] == 0
    assert empty_res["exit"]["content"] == ""
    assert empty_res["exit"]["extra"]["submission"] == ""

    # Marker on second line: does NOT submit
    non_first_line = {
        "command": "echo 'not the marker' && echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
    }
    res_non_first = execute_mini_shell_action(non_first_line)
    assert res_non_first["returncode"] == 0
    assert "exit" not in res_non_first

    # Marker with non-zero returncode: does NOT submit
    failed_sub = {
        "command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && false"
    }
    res_failed = execute_mini_shell_action(failed_sub)
    assert res_failed["returncode"] != 0
    assert "exit" not in res_failed


def test_native_timeout_rendering() -> None:
    """Timeout kills group, drains partial output, and renders native TimeoutExpired."""
    timeout_action = {
        "command": "printf 'partial_before_sleep\n' && sleep 10 && printf 'after_sleep\n'"
    }
    result = execute_mini_shell_action(timeout_action, timeout=1)
    assert result["returncode"] == -1
    assert "partial_before_sleep" in result["output"]
    assert "timed out after 1 seconds" in result["exception_info"]
    assert result["extra"]["exception_type"] == "TimeoutExpired"
    assert "exit" not in result




def test_shell_exit_does_not_discard_descendant_pipe_output() -> None:
    result = execute_mini_shell_action({"command": "(sleep 0.1; printf late) &"})
    assert result["output"] == "late"
    assert result["returncode"] == 0


def test_closed_stdout_does_not_disable_native_deadline() -> None:
    result = execute_mini_shell_action(
        {"command": "exec 1>&- 2>&-; sleep 10"}, timeout=1
    )
    assert result["returncode"] == -1
    assert result["extra"]["exception_type"] == "TimeoutExpired"


def test_helper_bootstrap_environment_cannot_break_guest_python(tmp_path: Path) -> None:
    launcher = (
        "import os,runpy,sys;"
        "os.environ['PYTHONHOME']='/transport-only';"
        "os.environ['LD_LIBRARY_PATH']='/transport-only';"
        "runpy.run_path(sys.argv[1],run_name='__main__')"
    )
    request = {
        "tool_id": "bash",
        "arguments": {
            "command": shlex.join([sys.executable, "-c", "print('guest-python-ok')"])
        },
        "environment": {"PATH": os.defpath, "HOME": str(tmp_path)},
    }
    result = subprocess.run(
        [sys.executable, "-c", launcher, mini_tools.__file__],
        input=json.dumps(request), capture_output=True, text=True, cwd=tmp_path, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    observation = json.loads(result.stdout)
    assert observation["returncode"] == 0, observation
    assert observation["output"] == "guest-python-ok\n"


def test_helper_raw_cap_retains_exact_prefix_on_failure(tmp_path: Path) -> None:
    request = {
        "tool_id": "bash",
        "arguments": {
            "command": shlex.join([
                sys.executable, "-c",
                f"import os; os.write(1, b'x' * {DEFAULT_RAW_OUTPUT_LIMIT + 1})",
            ])
        },
        "environment": {"PATH": os.defpath, "HOME": str(tmp_path)},
    }
    result = subprocess.run(
        [sys.executable, mini_tools.__file__],
        input=json.dumps(request), capture_output=True, text=True, cwd=tmp_path, timeout=10,
    )
    assert result.returncode != 0
    failure = json.loads(result.stdout)
    assert failure["outer_error"] == "raw_output_limit_exceeded"
    assert failure["examined_bytes"] == DEFAULT_RAW_OUTPUT_LIMIT + 1
    assert base64.b64decode(failure["raw_prefix_base64"], validate=True) == (
        b"x" * DEFAULT_RAW_OUTPUT_LIMIT
    )

    # The runtime keeps the explicit outer error distinct from a helper crash.
    from breadboard.rl.harness.sandbox import SandboxLaunchError, _decode_native_tool_result

    with pytest.raises(SandboxLaunchError) as captured:
        _decode_native_tool_result(
            {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr},
            lease_id=None,
        )
    assert captured.value.code == "native_output_limit_exceeded"
    assert captured.value.details["examined_bytes"] == DEFAULT_RAW_OUTPUT_LIMIT + 1
    with pytest.raises(SandboxLaunchError) as crashed:
        _decode_native_tool_result({"returncode": 1, "stdout": "", "stderr": "boom"}, lease_id=None)
    assert crashed.value.code == "runtime_launch_failed"
