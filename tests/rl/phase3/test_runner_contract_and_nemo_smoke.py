from __future__ import annotations
import shutil

from pathlib import Path

from scripts.rl_phase3.build_phase3_runner_contract import _parse_deps_yaml, build_contract
from scripts.rl_phase3.run_phase3_nemo_agentloop_smoke import build_smoke_report

TARGET = "20260624T040000Z-slurm-243958"
WRAPPER_HEAD = "98b09b5603d2ef84ffa8ac0baa0ee55448a69122"
VERL_HEAD = "abc123"
NEMO_HEAD = "def456"


def _git_runner(marker: str = ""):
    def run(args: list[str], cwd: Path) -> str:
        cwd_text = str(cwd).replace("\\", "/")
        if args == ["submodule", "status", "--recursive"]:
            return f"{marker}{VERL_HEAD} third_party/verl (heads/main)\n{marker}{NEMO_HEAD} third_party/nemo-gym (heads/main)"
        if args == ["rev-parse", "HEAD"]:
            if cwd_text.endswith("third_party/verl"):
                return VERL_HEAD
            if cwd_text.endswith("third_party/nemo-gym"):
                return NEMO_HEAD
            return WRAPPER_HEAD
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return "main"
        return ""
    return run



def _wrapper(tmp_path: Path) -> Path:
    root = tmp_path / "verl_wrapper"
    (root / "patches" / "verl").mkdir(parents=True)
    (root / "patches" / "verl" / "0001.patch").write_text("patch bytes\n")
    (root / "src" / "zyphra_verl").mkdir(parents=True)
    (root / "src" / "zyphra_verl" / "nemo_gym_loop.py").write_text(
        'register("nemo_gym_tool_use")\nToolParser\nToolCallComparator\nreward_score\n'
    )
    (root / "launch").mkdir()
    (root / "launch" / "train.sh").write_text("#!/usr/bin/env bash\n")
    (root / "deps.yaml").write_text(
        "verl:\n  pin: abc123\nnemo_gym:\n  pin: def456\n"
    )
    (root / ".gitmodules").write_text("[submodule]\n")
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    (root / "third_party" / "verl").mkdir(parents=True)
    (root / "third_party" / "verl" / "pyproject.toml").write_text("[project]\nname='verl'\n")
    (root / "third_party" / "nemo-gym").mkdir(parents=True)
    (root / "third_party" / "nemo-gym" / "pyproject.toml").write_text("[project]\nname='nemo-gym'\n")
    return root


def test_runner_contract_deps_parser_strips_inline_comments(tmp_path: Path) -> None:
    deps = tmp_path / "deps.yaml"
    deps.write_text(
        "\n".join(
            [
                "verl:",
                "  pin: ed89419c23653730e95c43954c00e6c24277e1c8 # v0.8.0 branch",
                "nemo_gym:",
                "  pin: 'fd1c91cb83256546bdef024d7dbd013c377748539' # canonical",
                "reward_models:",
                '  commit: \"abc#inside\" # keep quoted hash marker',
                "trainer:",
                "  rev: main # comment",
                "ignored:",
                "  image: should-not-appear # unsupported key",
            ]
        )
        + "\n"
    )

    assert _parse_deps_yaml(deps) == {
        "verl_pin": "ed89419c23653730e95c43954c00e6c24277e1c8",
        "nemo_gym_pin": "fd1c91cb83256546bdef024d7dbd013c377748539",
        "reward_models_commit": "abc#inside",
        "trainer_rev": "main",
    }


def test_runner_contract_deps_parser_missing_file_returns_empty(tmp_path: Path) -> None:
    assert _parse_deps_yaml(tmp_path / "missing.yaml") == {}


def test_runner_contract_requires_immutable_container_digest(tmp_path: Path) -> None:
    wrapper = _wrapper(tmp_path)

    report = build_contract(
        wrapper_dir=wrapper,
        target_run_id=TARGET,
        container_image="vllm/vllm-openai-rocm:nightly",
        container_digest="",
        launch_command="launch/train.sh STEPS=1",
        git_runner=_git_runner(),
    )

    assert report["passed"] is False
    assert "container_digest" in report["missing_required_identities"]
    assert report["required_identities"]["verl_pin"] == "abc123"
    assert report["required_identities"]["nemo_gym_pin"] == "def456"
    assert report["patch_queue_sha256"].startswith("sha256:")
    assert report["recipe_package_sha256"].startswith("sha256:")


def test_runner_contract_passes_with_complete_identity(tmp_path: Path) -> None:
    wrapper = _wrapper(tmp_path)

    report = build_contract(
        wrapper_dir=wrapper,
        target_run_id=TARGET,
        container_image="vllm/vllm-openai-rocm:nightly",
        container_digest="vllm/vllm-openai-rocm@sha256:abc",
        launch_command="launch/train.sh STEPS=1",
        git_runner=_git_runner(),
    )

    assert report["passed"] is True
    assert report["missing_required_identities"] == []


def test_runner_contract_blocks_yaml_pins_without_checked_out_submodules(tmp_path: Path) -> None:
    wrapper = _wrapper(tmp_path)
    shutil.rmtree(wrapper / "third_party")

    report = build_contract(
        wrapper_dir=wrapper,
        target_run_id=TARGET,
        container_image="vllm/vllm-openai-rocm:nightly",
        container_digest="vllm/vllm-openai-rocm@sha256:abc",
        launch_command="launch/train.sh STEPS=1",
        git_runner=_git_runner(),
    )

    assert report["passed"] is False
    assert "submodule_path_missing:third_party/verl" in report["wrapper_identity_blockers"]


def test_runner_contract_blocks_uninitialized_submodule_marker(tmp_path: Path) -> None:
    wrapper = _wrapper(tmp_path)

    report = build_contract(
        wrapper_dir=wrapper,
        target_run_id=TARGET,
        container_image="vllm/vllm-openai-rocm:nightly",
        container_digest="vllm/vllm-openai-rocm@sha256:abc",
        launch_command="launch/train.sh STEPS=1",
        git_runner=_git_runner("-"),
    )

    assert report["passed"] is False
    assert "submodule_uninitialized:third_party/verl" in report["wrapper_identity_blockers"]

def test_nemo_agentloop_smoke_records_controls_and_hashes(tmp_path: Path) -> None:
    wrapper = _wrapper(tmp_path)

    report = build_smoke_report(
        wrapper_dir=wrapper,
        target_run_id=TARGET,
        require_canonical_runtime=False,
        local_diagnostic=False,
    )

    assert report["controls"] == {
        "gold_reward": 1.0,
        "wrong_name_reward": 0.0,
        "wrong_args_reward": 0.0,
        "missing_call_reward": 0.0,
    }
    assert report["dependency_status"]["canonical_agentloop_source_present"] is True
    assert report["hashes"]["row_sha256"].startswith("sha256:")
    assert report["mode"] == "diagnostic"
    assert report["diagnostic_passed"] is True
    assert report["passed"] is False
    assert report["promotional"] is False
    assert "diagnostic_only_not_promotional" in report["blocked_reason"]


def test_nemo_agentloop_local_diagnostic_cannot_promote(tmp_path: Path) -> None:
    wrapper = _wrapper(tmp_path)

    report = build_smoke_report(
        wrapper_dir=wrapper,
        target_run_id=TARGET,
        require_canonical_runtime=False,
        local_diagnostic=True,
    )

    assert report["passed"] is False
    assert "local_diagnostic_not_promotional" in report["blocked_reason"]


def test_nemo_agentloop_canonical_mode_blocks_when_runtime_missing(tmp_path: Path, monkeypatch) -> None:
    wrapper = _wrapper(tmp_path)
    monkeypatch.setattr(
        "scripts.rl_phase3.run_phase3_nemo_agentloop_smoke._dependency_status",
        lambda wrapper_dir: {
            "wrapper_dir": str(wrapper_dir),
            "nemo_gym_loop_path": str(wrapper_dir / "src" / "zyphra_verl" / "nemo_gym_loop.py"),
            "canonical_agentloop_source_present": True,
            "nemo_gym_loop_sha256": "sha256:test",
            "required_source_terms": ["register(\"nemo_gym_tool_use\"", "ToolParser", "ToolCallComparator", "reward_score"],
            "canonical_runtime_imports_present": False,
            "runtime_import_blocked_reason": "ModuleNotFoundError",
        },
    )

    report = build_smoke_report(
        wrapper_dir=wrapper,
        target_run_id=TARGET,
        require_canonical_runtime=True,
        local_diagnostic=False,
        canonical_mode=True,
    )

    assert report["mode"] == "canonical"
    assert report["passed"] is False
    assert report["canonical_runtime_required"] is True
    assert report["canonical_agentloop_executed"] is False
    assert report["canonical_tool_parser_used"] is False
    assert report["canonical_comparator_used"] is False
    assert report["canonical_reward_score_observed"] is False
    assert report["breadboard_toy_reward_used"] is True
    assert report["scorecard_update_allowed"] is False
    assert "canonical_runtime_imports_missing" in report["blocked_reason"]


def test_nemo_agentloop_canonical_mode_blocks_when_execution_missing(tmp_path: Path, monkeypatch) -> None:
    wrapper = _wrapper(tmp_path)
    monkeypatch.setattr(
        "scripts.rl_phase3.run_phase3_nemo_agentloop_smoke._dependency_status",
        lambda wrapper_dir: {
            "wrapper_dir": str(wrapper_dir),
            "nemo_gym_loop_path": str(wrapper_dir / "src" / "zyphra_verl" / "nemo_gym_loop.py"),
            "canonical_agentloop_source_present": True,
            "nemo_gym_loop_sha256": "sha256:test",
            "required_source_terms": ["register(\"nemo_gym_tool_use\"", "ToolParser", "ToolCallComparator", "reward_score"],
            "canonical_runtime_imports_present": True,
            "runtime_import_blocked_reason": "",
        },
    )

    report = build_smoke_report(
        wrapper_dir=wrapper,
        target_run_id=TARGET,
        require_canonical_runtime=True,
        local_diagnostic=False,
        canonical_mode=True,
    )

    assert report["mode"] == "canonical"
    assert report["passed"] is False
    assert report["canonical_runtime_required"] is True
    assert report["canonical_agentloop_executed"] is False
    assert "canonical_runtime_imports_missing" not in report["blocked_reason"]
    assert "canonical_agentloop_execution_missing" in report["blocked_reason"]


