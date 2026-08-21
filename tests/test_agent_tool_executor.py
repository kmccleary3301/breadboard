import threading
import types
import time
from collections import defaultdict
from unittest import mock
from pathlib import Path


import pytest

from breadboard_engine.execution.agent_executor import AgentToolExecutor
from breadboard_engine.execution.enhanced_executor import EnhancedToolExecutor
from breadboard_engine.execution.concurrency_validator import (
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


def test_patch_execution_updates_enhanced_workspace_context(tmp_path):
    executor = AgentToolExecutor({}, workspace=str(tmp_path))
    enhanced = EnhancedToolExecutor(sandbox=object(), config={"workspace": str(tmp_path)})
    executor.set_enhanced_executor(enhanced)
    calls = []
    patch = """*** Begin Patch
*** Add File: created.py
+print("created")
*** End Patch
"""

    result = executor.execute_tool_call(
        {"function": "apply_unified_patch", "arguments": {"patch": patch}},
        lambda call: calls.append(call) or {"ok": True},
    )

    assert result == {"ok": True}
    assert calls == [
        {"function": "apply_unified_patch", "arguments": {"patch": patch}}
    ]
    context = enhanced.get_workspace_context()
    assert context["files_created_this_session"] == ["created.py"]
    assert context["recent_operations"][-1]["files_affected"] == ["created.py"]


def test_patch_execution_honors_enhanced_validation(tmp_path):
    config = {
        "enhanced_tools": {
            "validation": {
                "enabled": True,
                "rules": ["read_before_edit"],
                "rule_config": {"read_before_edit": {"mode": "error"}},
            }
        }
    }
    executor = AgentToolExecutor(config, workspace=str(tmp_path))
    enhanced = EnhancedToolExecutor(
        sandbox=object(),
        config={**config, "workspace": str(tmp_path)},
    )
    executor.set_enhanced_executor(enhanced)
    calls = []
    patch = """*** Begin Patch
*** Update File: existing.py
@@
-old
+new
*** End Patch
"""

    result = executor.execute_tool_call(
        {"function": "apply_unified_patch", "arguments": {"patch": patch}},
        lambda call: calls.append(call) or {"ok": True},
    )

    assert result["validation_failure"] is True
    assert "must be read" in result["error"]
    assert calls == []


def test_aider_create_policy_routes_through_production_writer(tmp_path):
    from breadboard_engine.agent_llm_openai import OpenAIConductor

    config = {
        "tools": {
            "dialects": {
                "create_file_policy": {
                    "aider_search_replace": {"prefer_write_file_tool": True}
                }
            }
        }
    }
    executor = AgentToolExecutor(config, workspace=str(tmp_path))
    enhanced = EnhancedToolExecutor(
        sandbox=object(),
        config={**config, "workspace": str(tmp_path)},
    )
    executor.set_enhanced_executor(enhanced)

    writes = []
    conductor_class = OpenAIConductor.__ray_metadata__.modified_class
    conductor = object.__new__(conductor_class)
    conductor.config = config
    conductor.workspace = str(tmp_path)
    conductor._ray_get = lambda value: value
    conductor.sandbox = types.SimpleNamespace(
        write_text=types.SimpleNamespace(
            remote=lambda path, content: writes.append((path, content)) or {"ok": True}
        )
    )

    result = executor.execute_tool_call(
        {
            "function": "apply_search_replace",
            "arguments": {
                "file_name": "created.txt",
                "search": "",
                "replace": "created",
            },
        },
        conductor._exec_raw,
    )

    assert result == {"ok": True}
    assert writes == [(str(tmp_path / "created.txt"), "created")]
    assert enhanced.get_workspace_context()["files_created_this_session"] == [
        "created.txt"
    ]


def test_aider_create_policy_remains_an_edit_permission(tmp_path):
    config = {
        "tools": {
            "dialects": {
                "create_file_policy": {
                    "aider_search_replace": {"prefer_write_file_tool": True}
                }
            }
        },
        "enhanced_tools": {"permissions": {"edit": {"default": "deny"}}},
    }
    executor = AgentToolExecutor(config, workspace=str(tmp_path))
    enhanced = EnhancedToolExecutor(
        sandbox=object(),
        config={**config, "workspace": str(tmp_path)},
    )
    executor.set_enhanced_executor(enhanced)
    calls = []

    result = executor.execute_tool_call(
        {
            "function": "apply_search_replace",
            "arguments": {
                "file_name": "created.txt",
                "search": "",
                "replace": "created",
            },
        },
        lambda call: calls.append(call) or {"ok": True},
    )

    assert result["validation_failure"] is True
    assert "Permission denied" in result["error"]
    assert calls == []
