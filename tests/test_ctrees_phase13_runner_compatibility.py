from __future__ import annotations

from pathlib import Path

from agentic_coder_prototype.agent_llm_openai import OpenAIConductor


def _make_conductor(config: dict) -> OpenAIConductor:
    cls = OpenAIConductor.__ray_metadata__.modified_class
    inst = object.__new__(cls)
    inst.config = config
    inst.sandbox = None
    return inst  # type: ignore[return-value]


def test_exec_raw_shell_command_alias_uses_run_shell_and_timeout_ms() -> None:
    conductor = _make_conductor({})
    seen: dict[str, object] = {}

    def _run_shell(command: str, timeout=None):
        seen["command"] = command
        seen["timeout"] = timeout
        return {"stdout": "ok", "exit": 0}

    conductor.run_shell = _run_shell  # type: ignore[method-assign]

    out = conductor._exec_raw(
        {
            "function": "shell_command",
            "arguments": {"command": "pwd", "workdir": "src", "timeout_ms": 2500},
        }
    )

    assert out["exit"] == 0
    assert seen["command"] == "cd src && pwd"
    assert seen["timeout"] == 3


def test_exec_raw_apply_patch_alias_uses_input_payload() -> None:
    conductor = _make_conductor({})

    def _vcs(request):
        patch = ((request.get("params") or {}).get("patch") or "")
        return {"ok": True, "patch_excerpt": patch[:24]}

    conductor.vcs = _vcs  # type: ignore[method-assign]
    conductor._apply_patch_operations_direct = lambda patch_text: None  # type: ignore[method-assign]
    conductor._retry_diff_with_aider = lambda patch_text: None  # type: ignore[method-assign]

    out = conductor._exec_raw(
        {
            "function": "apply_patch",
            "arguments": {"input": "*** Begin Patch\n*** Add File: hello.txt\n+hi\n*** End Patch\n"},
        }
    )

    assert out["ok"] is True
    assert "patch_excerpt" in out


def test_exec_raw_update_plan_returns_success_marker() -> None:
    conductor = _make_conductor({})
    out = conductor._exec_raw(
        {
            "function": "update_plan",
            "arguments": {"explanation": "track work", "plan": [{"step": "x", "status": "pending"}]},
        }
    )

    assert out["ok"] is True
    assert out["__mvi_text_output"] == "Plan updated"
    assert getattr(conductor, "_codex_update_plan_state")["explanation"] == "track work"
