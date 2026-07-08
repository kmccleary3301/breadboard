from __future__ import annotations

import json
import stat
import zipfile
from pathlib import Path

from scripts.rl_phase3.build_phase4_native_inference_payload import REQUIRED_ZIP_ENTRIES, build_payload


REPO_ROOT = Path(__file__).resolve().parents[3]
WRAPPER_HEAD = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
VERL_HEAD = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
NEMO_HEAD = "cccccccccccccccccccccccccccccccccccccccc"


def _git_runner(args: list[str], cwd: Path) -> str:
    cwd_text = str(cwd).replace("\\", "/")
    if args == ["submodule", "status", "--recursive"]:
        return f"{VERL_HEAD} third_party/verl (heads/main)\n{NEMO_HEAD} third_party/nemo-gym (heads/main)"
    if args == ["rev-parse", "HEAD"]:
        if cwd_text.endswith("third_party/verl"):
            return VERL_HEAD
        if cwd_text.endswith("third_party/nemo-gym"):
            return NEMO_HEAD
        return WRAPPER_HEAD
    if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
        return "main"
    return ""


def _write_canonical_wrapper(wrapper_root: Path, *, include_submodules: bool = True) -> None:
    (wrapper_root / "src/zyphra_verl/configs").mkdir(parents=True)
    (wrapper_root / "src/zyphra_verl/nemo_gym_loop.py").write_text(
        'register("nemo_gym_tool_use")\nToolParser\nToolCallComparator\nreward_score\n'
    )
    (wrapper_root / "src/zyphra_verl/configs/agent_loops.yaml").write_text("agent_loop: native\n")
    (wrapper_root / "deps.yaml").write_text(f"verl:\n  pin: {VERL_HEAD}\nnemo_gym:\n  pin: {NEMO_HEAD}\n")
    if include_submodules:
        (wrapper_root / "third_party" / "verl").mkdir(parents=True)
        (wrapper_root / "third_party" / "verl" / "pyproject.toml").write_text("[project]\nname='verl'\n")
        (wrapper_root / "third_party" / "nemo-gym").mkdir(parents=True)
        (wrapper_root / "third_party" / "nemo-gym" / "pyproject.toml").write_text("[project]\nname='nemo-gym'\n")


def test_phase4_native_payload_zip_contains_native_lane_and_excludes_bytecode(tmp_path: Path) -> None:
    source_payload_dir = tmp_path / "source_payload"
    wrapper_root = source_payload_dir / "verl_wrapper"
    _write_canonical_wrapper(wrapper_root)
    (wrapper_root / "src/zyphra_verl/__pycache__").mkdir()
    (wrapper_root / "src/zyphra_verl/__pycache__/nemo_gym_loop.cpython-312.pyc").write_bytes(b"bytecode")
    (wrapper_root / "src/zyphra_verl/configs/cache.pyc").write_bytes(b"bytecode")
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    report = build_payload(
        repo_root=REPO_ROOT,
        source_payload_dir=source_payload_dir,
        output_dir=output_dir,
        stamp="20260707T000000Z",
        git_runner=_git_runner,
    )

    assert report["passed"] is True
    assert report["errors"] == []
    persisted_report = json.loads(Path(report["report_path"]).read_text())
    assert persisted_report["passed"] is True
    assert persisted_report["errors"] == []

    stage_dir = Path(report["stage_dir"])
    run_sh = stage_dir / "run.sh"
    assert run_sh.exists()
    assert run_sh.stat().st_mode & stat.S_IXUSR

    zip_path = Path(report["zip_path"])
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        assert set(REQUIRED_ZIP_ENTRIES).issubset(names)
        assert "repo/breadboard/rl/phase4/native_inference.py" in names
        assert "verl_wrapper/src/zyphra_verl/nemo_gym_loop.py" in names
        assert "verl_wrapper/src/zyphra_verl/configs/agent_loops.yaml" in names
        assert "repo/breadboard/rl/phase4/wrapper_identity.py" in names
        assert "verl_wrapper/wrapper_identity.json" in names
        assert "verl_wrapper/third_party/verl/pyproject.toml" in names
        assert "verl_wrapper/third_party/nemo-gym/pyproject.toml" in names
        assert all("__pycache__" not in name for name in names)
        assert all(not name.endswith(".pyc") for name in names)
        run_info = archive.getinfo("run.sh")
        assert (run_info.external_attr >> 16) & stat.S_IXUSR
        native_source = archive.read("repo/breadboard/rl/phase4/native_inference.py").decode("utf-8")
        assert "BREADBOARD_NATIVE_INFERENCE_OWNER" in native_source
        assert "NativeInferenceLane" in native_source

        identity = json.loads(archive.read("verl_wrapper/wrapper_identity.json").decode("utf-8"))
        assert identity["passed"] is True
        assert identity["components"]["verl"]["actual_commit"] == VERL_HEAD


def test_phase4_native_payload_blocks_missing_exact_wrapper_submodules(tmp_path: Path) -> None:
    source_payload_dir = tmp_path / "source_payload"
    wrapper_root = source_payload_dir / "verl_wrapper"
    _write_canonical_wrapper(wrapper_root, include_submodules=False)
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    report = build_payload(
        repo_root=REPO_ROOT,
        source_payload_dir=source_payload_dir,
        output_dir=output_dir,
        stamp="20260707T000001Z",
        git_runner=_git_runner,
    )

    assert report["passed"] is False
    assert "wrapper identity manifest must pass" in report["errors"]
    assert "submodule_path_missing:third_party/verl" in report["wrapper_identity"]["blockers"]