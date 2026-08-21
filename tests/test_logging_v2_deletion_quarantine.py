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
        if rel == Path("tests/test_logging_v2_deletion_quarantine.py"):
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



def test_logging_v2_package_and_canonical_provider_logger_import() -> None:
    logging_v2 = importlib.import_module("agentic_coder_prototype.logging_v2")
    canonical = importlib.import_module(
        "agentic_coder_prototype.run_logging.provider_native_logger"
    )

    assert logging_v2.__name__ == "agentic_coder_prototype.logging_v2"
    assert hasattr(canonical, "ProviderNativeLogger")


def test_h2_quarantined_logging_v2_provider_wrapper_is_not_importable() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    active_path = (
        repo_root
        / "agentic_coder_prototype"
        / "logging_v2"
        / "provider_native_logger.py"
    )
    quarantine_path = (
        repo_root
        / "agentic_coder_prototype"
        / "legacy"
        / "logging_v2"
        / "provider_native_logger.py"
    )

    assert (
        importlib.util.find_spec(
            "agentic_coder_prototype.logging_v2.provider_native_logger"
        )
        is None
    )
    assert not active_path.exists()
    assert quarantine_path.is_file()


def test_h2_quarantined_provider_wrapper_has_no_active_string_references() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    assert _active_reference_hits(
        repo_root,
        (
            "agentic_coder_prototype.logging_v2.provider_native_logger",
            "agentic_coder_prototype/logging_v2/provider_native_logger.py",
            "logging_v2/provider_native_logger.py",
        ),
    ) == []
