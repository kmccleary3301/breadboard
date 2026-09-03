from __future__ import annotations

from pathlib import Path

import pytest

from breadboard_engine.security import ChildProcessPolicy


def test_environment_plan_is_sanitized_and_immutable() -> None:
    plan = ChildProcessPolicy(
        source_environment={"PATH": "/usr/bin", "OPENAI_API_KEY": "secret"}
    ).environment_only()

    assert plan.as_dict()["PATH"] == "/usr/bin"
    assert "OPENAI_API_KEY" not in plan.environment
    with pytest.raises(TypeError):
        plan.environment["OPENAI_API_KEY"] = "replacement"  # type: ignore[index]


def test_environment_override_admission_remains_explicit() -> None:
    with pytest.raises(ValueError, match="not permitted"):
        ChildProcessPolicy(
            source_environment={"PATH": "/usr/bin"},
            overrides={"CUSTOM_FLAG": "enabled"},
        ).environment_only()

    plan = ChildProcessPolicy(
        source_environment={"PATH": "/usr/bin"},
        overrides={"CUSTOM_FLAG": "enabled"},
        allowed_override_keys=("CUSTOM_FLAG",),
    ).environment_only()
    assert plan.environment["CUSTOM_FLAG"] == "enabled"


def test_nonisolated_launch_plan_validates_workspace_and_freezes_shape(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    working_directory = workspace / "nested"
    working_directory.mkdir(parents=True)

    plan = ChildProcessPolicy(
        source_environment={"PATH": "/usr/bin"},
        workspace=workspace,
        working_directory=working_directory,
        isolate=False,
    ).command_and_environment(("python", "-V"))

    assert plan.argv == ("python", "-V")
    assert plan.workspace == workspace.resolve()
    assert plan.working_directory == working_directory.resolve()
    assert plan.environment_dict() == {"PATH": "/usr/bin"}
