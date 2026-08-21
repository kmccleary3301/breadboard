import threading
import types
import time
from collections import defaultdict
from unittest import mock
from pathlib import Path


import pytest

from agentic_coder_prototype.execution.agent_executor import AgentToolExecutor
from agentic_coder_prototype.execution.concurrency_validator import (
    ConcurrencyConfigError,
    validate_concurrency_config,
)


def _make_call(function: str, **arguments):
    return types.SimpleNamespace(function=function, arguments=arguments)


def _write_executor_tool_defs(defs_dir: Path) -> None:
    defs_dir.mkdir()
    (defs_dir / "read_file.yaml").write_text(
        """id: read_file
name: read_file
type_id: python
binding:
  handler: read_handler
  type_id: python
aliases:
  - peek
classification:
  guardrail_sets: [read]
manipulations:
  - file.read
execution:
  blocking: false
""",
        encoding="utf-8",
    )
    (defs_dir / "apply_unified_patch.yaml").write_text(
        """id: apply_unified_patch
name: apply_unified_patch
type_id: python
binding:
  handler: patch_handler
  type_id: python
aliases:
  - patch
classification:
  guardrail_sets: [edit]
manipulations:
  - diff.apply
execution:
  blocking: true
""",
        encoding="utf-8",
    )



def test_validate_concurrency_config_normalizes_lists():
    cfg = validate_concurrency_config({
        "nonblocking_tools": [" read_file ", "read_file"],
        "at_most_one_of": ["run_shell", "run_shell "],
        "groups": [
            {
                "name": " reads ",
                "match_tools": [" read_file ", "list_dir"],
                "max_parallel": 2,
            }
        ],
    })

    assert cfg["nonblocking_tools"] == ["read_file"]
    assert cfg["at_most_one_of"] == ["run_shell"]
    assert cfg["groups"][0]["name"] == "reads"
    assert cfg["groups"][0]["match_tools"] == ["read_file", "list_dir"]
    assert cfg["groups"][0]["max_parallel"] == 2


def test_validate_concurrency_config_rejects_bad_barrier():
    with pytest.raises(ConcurrencyConfigError):
        validate_concurrency_config({
            "groups": [
                {
                    "name": "reads",
                    "match_tools": ["read_file"],
                    "max_parallel": 1,
                    "barrier_after": "list_dir",
                }
            ]
        })


def test_agent_executor_raises_on_invalid_concurrency():
    with pytest.raises(ValueError) as exc:
        AgentToolExecutor(
            {
                "concurrency": {
                    "groups": [
                        {"match_tools": ["read_file"], "max_parallel": 0},
                    ]
                }
            },
            workspace="/tmp",
        )

    assert "max_parallel" in str(exc.value)


def test_at_most_one_of_enforced_with_alias():
    config = {
        "tools": {"aliases": {"bash": "run_shell"}},
        "concurrency": {"at_most_one_of": ["bash"]},
    }
    executor = AgentToolExecutor(config, workspace="/tmp")

    parsed_calls = [
        _make_call("run_shell", command="ls"),
        _make_call("run_shell", command="pwd"),
    ]

    executed, failed_at, error, plan = executor.execute_parsed_calls(
        parsed_calls,
        lambda call: {"stdout": "", "exit": 0},
    )

    assert executed == []
    assert failed_at == -1
    assert error and error.get("constraint_violation")
    assert "Only one" in error["error"]
    assert plan["strategy"] == "constraint_violation"


def test_concurrency_group_max_parallel_applies():
    config = {
        "concurrency": {
            "groups": [
                {"name": "reads", "match_tools": ["read_file"], "max_parallel": 2},
            ],
            "nonblocking_tools": ["read_file"],
        }
    }
    executor = AgentToolExecutor(config, workspace="/tmp")

    parsed_calls = [_make_call("read_file", path=f"file_{i}") for i in range(3)]

    with mock.patch.object(
        executor,
        "execute_calls_concurrent",
        wraps=executor.execute_calls_concurrent,
    ) as mocked:
        executed, failed_at, error, plan = executor.execute_parsed_calls(
            parsed_calls,
            lambda call: {"ok": True},
        )

    assert error is None
    assert failed_at == -1
    assert len(executed) == 3
    mocked.assert_called_once()
    # max_workers passed positionally as the fourth argument
    assert mocked.call_args.args[3] == 2
    assert plan["strategy"] == "nonblocking_concurrent"


def test_barrier_after_forces_sequential():
    config = {
        "concurrency": {
            "groups": [
                {
                    "name": "edits",
                    "match_tools": ["apply_unified_patch"],
                    "max_parallel": 1,
                    "barrier_after": "apply_unified_patch",
                }
            ],
            "nonblocking_tools": ["read_file"],
        }
    }
    executor = AgentToolExecutor(config, workspace="/tmp")

    strategy = executor.determine_execution_strategy([
        _make_call("apply_unified_patch", patch=""),
        _make_call("read_file", path="foo"),
    ])

    assert not strategy["can_run_concurrent"]
    assert strategy["strategy"] == "sequential"


def test_group_concurrency_limit_is_enforced_during_execution():
    config = {
        "concurrency": {
            "groups": [
                {"name": "reads", "match_tools": ["read_file"], "max_parallel": 3},
                {"name": "lists", "match_tools": ["list_dir"], "max_parallel": 2},
            ],
            "nonblocking_tools": ["read_file", "list_dir"],
        }
    }
    executor = AgentToolExecutor(config, workspace="/tmp")

    parsed_calls = [
        _make_call("read_file", path=f"file_{i}") for i in range(4)
    ] + [
        _make_call("list_dir", path=f"dir_{i}") for i in range(2)
    ]

    active_counts = defaultdict(int)
    max_seen = defaultdict(int)
    lock = threading.Lock()

    read_group = executor.tool_to_group["read_file"].get("_id")
    list_group = executor.tool_to_group["list_dir"].get("_id")

    def exec_stub(call):
        tool = call["function"]
        group = executor.tool_to_group.get(tool)
        group_id = group.get("_id") if group else "ungrouped"
        with lock:
            active_counts[group_id] += 1
            max_seen[group_id] = max(max_seen[group_id], active_counts[group_id])
        try:
            time.sleep(0.01)
            return {"ok": True}
        finally:
            with lock:
                active_counts[group_id] -= 1

    executed, failed_at, error, plan = executor.execute_parsed_calls(parsed_calls, exec_stub)

    assert error is None
    assert failed_at == -1
    assert len(executed) == len(parsed_calls)
    assert max_seen[read_group] <= 3
    assert max_seen[list_group] <= 2
    assert plan["strategy"] == "nonblocking_concurrent"


def test_agent_executor_uses_registry_aliases_and_nonblocking_defaults(tmp_path: Path) -> None:
    """Without an explicit nonblocking list, registry metadata must make reads concurrent and patches blocking."""
    defs_dir = tmp_path / "defs"
    _write_executor_tool_defs(defs_dir)
    executor = AgentToolExecutor(
        {"tools": {"defs_dir": str(defs_dir)}},
        workspace=str(tmp_path),
    )

    seen: list[str] = []

    def exec_stub(call):
        seen.append(call["function"])
        return {"ok": True}

    read_calls = [
        _make_call("peek", path="first.txt"),
        _make_call("read_file", path="second.txt"),
    ]
    executed, failed_at, error, plan = executor.execute_parsed_calls(read_calls, exec_stub)

    assert error is None
    assert failed_at == -1
    assert len(executed) == 2
    assert plan["strategy"] == "nonblocking_concurrent"
    assert plan["max_workers"] == 2
    assert seen.count("read_file") == 2

    seen.clear()
    patch_and_read = [
        _make_call("peek", path="after.patch"),
        _make_call("patch", patch="diff --git a/file b/file\n"),
    ]
    executed, failed_at, error, plan = executor.execute_parsed_calls(patch_and_read, exec_stub)

    assert error is None
    assert failed_at == -1
    assert len(executed) == 2
    assert plan["strategy"] == "sequential"
    assert seen == ["apply_unified_patch", "read_file"]


def test_alias_canonicalization_applied_before_execution():
    config = {
        "tools": {"aliases": {"patch": "apply_unified_patch"}},
        "concurrency": {"nonblocking_tools": ["patch"]},
    }
    executor = AgentToolExecutor(config, workspace="/tmp")

    seen = []

    def exec_stub(call):
        seen.append(call["function"])
        return {"ok": True}

    parsed_calls = [_make_call("patch", patch="diff")]
    executed, failed_at, error, plan = executor.execute_parsed_calls(parsed_calls, exec_stub)

    assert error is None
    assert failed_at == -1
    assert len(executed) == 1
    assert seen == ["apply_unified_patch"]
    assert plan["strategy"] in {"nonblocking_concurrent", "sequential"}
