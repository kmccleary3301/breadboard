"""Tests for Mini native shell action helper and sandbox dispatch."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from breadboard.rl.harness.mini_tools import (
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_RAW_OUTPUT_LIMIT,
    MAX_REQUEST_BYTES,
    MINI_ENVIRONMENT_OVERRIDES,
    MINI_SWE_AGENT_LOCAL_ADAPTER_ID,
    MINI_TOOL_ID,
    RawOutputLimitExceeded,
    check_finished,
    execute_mini_shell_action,
)
from breadboard.rl.harness.runners.base import (
    RunnerToolBinding,
    freeze_json_object,
    thaw_json,
)
from breadboard.rl.harness.sandbox import (
    InstalledToolAdapter,
    LeaseBackedRunnerWorkspace,
    RuntimeClass,
    WorkspaceStateError,
)


def test_mini_environment_overrides() -> None:
    """Exact five Mini overrides are present."""
    assert dict(MINI_ENVIRONMENT_OVERRIDES) == {
        "PAGER": "cat",
        "MANPAGER": "cat",
        "LESS": "-R",
        "PIP_PROGRESS_BAR": "off",
        "TQDM_DISABLE": "1",
    }
    result = execute_mini_shell_action(
        {"command": "echo PAGER=$PAGER LESS=$LESS TQDM=$TQDM_DISABLE"}
    )
    assert result["returncode"] == 0
    assert result["output"].strip() == "PAGER=cat LESS=-R TQDM=1"
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


def test_raw_output_limit_exceeded() -> None:
    """Raw output exceeding limit raises RawOutputLimitExceeded explicitly with prefix/count."""
    large_action = {"command": "python3 -c 'print(\"A\" * 1000)'"}
    with pytest.raises(RawOutputLimitExceeded) as exc_info:
        execute_mini_shell_action(large_action, raw_output_limit=128)
    assert len(exc_info.value.raw_prefix) == 128
    assert exc_info.value.total_bytes >= 128


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
