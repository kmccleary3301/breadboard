from __future__ import annotations

from pathlib import Path

from breadboard.rl.phase4.wrapper_identity import collect_wrapper_identity, parse_deps_pins, runtime_module_provenance

WRAPPER_HEAD = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
VERL_HEAD = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
NEMO_HEAD = "cccccccccccccccccccccccccccccccccccccccc"


def _wrapper(tmp_path: Path, *, deps: bool = True, submodules: bool = True) -> Path:
    root = tmp_path / "verl_wrapper"
    (root / "src" / "zyphra_verl").mkdir(parents=True)
    (root / "src" / "zyphra_verl" / "nemo_gym_loop.py").write_text('register("nemo_gym_tool_use")\nToolParser\nToolCallComparator\nreward_score\n')
    if deps:
        (root / "deps.yaml").write_text(f"verl:\n  pin: {VERL_HEAD}\nnemo_gym:\n  pin: {NEMO_HEAD}\n")
    if submodules:
        (root / "third_party" / "verl").mkdir(parents=True)
        (root / "third_party" / "verl" / "pyproject.toml").write_text("[project]\nname='verl'\n")
        (root / "third_party" / "nemo-gym").mkdir(parents=True)
        (root / "third_party" / "nemo-gym" / "pyproject.toml").write_text("[project]\nname='nemo-gym'\n")
    return root


def _git_runner(status_marker: str = ""):
    def run(args: list[str], cwd: Path) -> str:
        cwd_text = str(cwd).replace("\\", "/")
        if args == ["submodule", "status", "--recursive"]:
            return f"{status_marker}{VERL_HEAD} third_party/verl (heads/main)\n{status_marker}{NEMO_HEAD} third_party/nemo-gym (heads/main)"
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


def test_parse_deps_pins_requires_real_yaml_values(tmp_path: Path) -> None:
    deps = tmp_path / "deps.yaml"
    deps.write_text("verl:\n  pin: abc123 # comment\nnemo_gym:\n  commit: def456\n")

    assert parse_deps_pins(deps) == {"verl_pin": "abc123", "nemo_gym_commit": "def456"}


def test_wrapper_identity_passes_with_expected_pins_and_clean_submodules(tmp_path: Path) -> None:
    wrapper = _wrapper(tmp_path)

    identity = collect_wrapper_identity(wrapper, git_runner=_git_runner())
    payload = identity.to_dict()

    assert payload["passed"] is True
    assert payload["wrapper_commit"] == WRAPPER_HEAD
    assert payload["components"]["verl"]["expected_commit"] == VERL_HEAD
    assert payload["components"]["verl"]["actual_commit"] == VERL_HEAD
    assert payload["components"]["nemo_gym"]["expected_commit"] == NEMO_HEAD
    assert payload["components"]["nemo_gym"]["actual_commit"] == NEMO_HEAD


def test_wrapper_identity_blocks_when_deps_yaml_or_expected_pins_are_missing(tmp_path: Path) -> None:
    wrapper = _wrapper(tmp_path, deps=False)

    identity = collect_wrapper_identity(wrapper, git_runner=_git_runner()).to_dict()

    assert identity["passed"] is False
    assert "deps_yaml_missing" in identity["blockers"]
    assert "submodule_expected_pin_missing:third_party/verl" in identity["blockers"]
    assert "submodule_expected_pin_missing:third_party/nemo-gym" in identity["blockers"]


def test_wrapper_identity_blocks_on_uninitialized_dirty_or_conflicted_submodule(tmp_path: Path) -> None:
    wrapper = _wrapper(tmp_path)

    for marker, blocker in (
        ("-", "submodule_uninitialized:third_party/verl"),
        ("+", "submodule_dirty:third_party/verl"),
        ("U", "submodule_conflicted:third_party/verl"),
    ):
        identity = collect_wrapper_identity(wrapper, git_runner=_git_runner(marker)).to_dict()
        assert identity["passed"] is False
        assert blocker in identity["blockers"]


def test_runtime_module_provenance_requires_staged_roots(tmp_path: Path) -> None:
    wrapper = _wrapper(tmp_path)
    inside = runtime_module_provenance(
        wrapper,
        {
            "zyphra_verl": str(wrapper / "src" / "zyphra_verl" / "__init__.py"),
            "verl": str(wrapper / "third_party" / "verl" / "verl" / "__init__.py"),
            "nemo_gym": str(wrapper / "third_party" / "nemo-gym" / "nemo_gym" / "__init__.py"),
        },
    )
    outside = runtime_module_provenance(
        wrapper,
        {
            "zyphra_verl": str(wrapper / "src" / "zyphra_verl" / "__init__.py"),
            "verl": "/usr/local/lib/python3.12/site-packages/verl/__init__.py",
            "nemo_gym": str(wrapper / "third_party" / "nemo-gym" / "nemo_gym" / "__init__.py"),
        },
    )

    assert inside["passed"] is True
    assert outside["passed"] is False
    assert "runtime_identity_mismatch:verl" in outside["blockers"]
