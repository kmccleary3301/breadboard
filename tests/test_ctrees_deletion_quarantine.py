from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path


_SCAN_EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "artifacts",
    "legacy",
    "logging",
    "misc",
    "tmp",
}


def _active_text_files(repo_root: Path):
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_root)
        if rel == Path("tests/test_ctrees_deletion_quarantine.py"):
            continue
        if any(part in _SCAN_EXCLUDED_DIRS for part in rel.parts):
            continue
        if path.suffix in {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".zip", ".tar", ".gz"}:
            continue
        yield rel, path


def _active_reference_hits(repo_root: Path, needles: tuple[str, ...]) -> list[str]:
    hits: list[str] = []
    for rel, path in _active_text_files(repo_root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for needle in needles:
            if needle in text:
                hits.append(f"{rel}:{needle}")
    return hits



def test_ctrees_package_imports_after_h4_quarantine() -> None:
    ctrees = importlib.import_module("agentic_coder_prototype.ctrees")

    assert ctrees.__name__ == "agentic_coder_prototype.ctrees"


def test_h4_quarantined_dead_modules_are_not_on_ctrees_import_surface() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    for module_name in ("finish_closure_scoring", "runtime_trace_schema"):
        active_path = repo_root / "agentic_coder_prototype" / "ctrees" / f"{module_name}.py"
        quarantine_path = (
            repo_root
            / "agentic_coder_prototype"
            / "legacy"
            / "ctrees"
            / f"{module_name}.py"
        )

        assert (
            importlib.util.find_spec(
                f"agentic_coder_prototype.ctrees.{module_name}"
            )
            is None
        )

        assert not active_path.exists()
        assert quarantine_path.is_file()


def test_h4_quarantined_dead_modules_have_no_active_string_references() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    assert _active_reference_hits(
        repo_root,
        (
            "agentic_coder_prototype.ctrees.finish_closure_scoring",
            "agentic_coder_prototype/ctrees/finish_closure_scoring.py",
            "ctrees/finish_closure_scoring.py",
            "agentic_coder_prototype.ctrees.runtime_trace_schema",
            "agentic_coder_prototype/ctrees/runtime_trace_schema.py",
            "ctrees/runtime_trace_schema.py",
        ),
    ) == []
