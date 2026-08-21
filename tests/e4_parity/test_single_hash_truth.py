from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_ROOT = Path("scripts/e4_parity")
ALLOWED_HASHLIB_FILES = {
    Path("scripts/e4_parity/build_artifact_catalog.py"),
    Path("agentic_coder_prototype/compilation/primitive_records.py"),
}
ALLOWED_HASHLIB_PREFIXES = (Path("scripts/e4_parity/validators"),)


def _is_allowed_hashlib_path(relative_path: Path) -> bool:
    if relative_path in ALLOWED_HASHLIB_FILES:
        return True
    return any(
        relative_path == prefix or prefix in relative_path.parents
        for prefix in ALLOWED_HASHLIB_PREFIXES
    )


def _hashlib_import_violations(repo_root: Path) -> list[str]:
    script_root = repo_root / SCRIPT_ROOT
    if not script_root.exists():
        return []

    violations: list[str] = []
    for path in sorted(script_root.rglob("*.py")):
        relative_path = path.relative_to(repo_root)
        if _is_allowed_hashlib_path(relative_path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == "hashlib" or alias.name.startswith("hashlib.") for alias in node.names):
                    violations.append(f"{relative_path.as_posix()}:{node.lineno} imports hashlib")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "hashlib" or module.startswith("hashlib."):
                    violations.append(f"{relative_path.as_posix()}:{node.lineno} imports from hashlib")
    return violations


def test_scripts_e4_parity_keep_hashlib_usage_at_single_truth_sources() -> None:
    assert _hashlib_import_violations(REPO_ROOT) == []


def test_lint_reports_unauthorized_hashlib_usage_fixture(tmp_path: Path) -> None:
    allowed_builder = tmp_path / "scripts" / "e4_parity" / "build_artifact_catalog.py"
    allowed_builder.parent.mkdir(parents=True, exist_ok=True)
    allowed_builder.write_text("import hashlib\n", encoding="utf-8")

    allowed_validator = tmp_path / "scripts" / "e4_parity" / "validators" / "hashes.py"
    allowed_validator.parent.mkdir(parents=True, exist_ok=True)
    allowed_validator.write_text("from hashlib import sha256\n", encoding="utf-8")

    offender = tmp_path / "scripts" / "e4_parity" / "rogue_hash.py"
    offender.write_text("import hashlib\n\nVALUE = hashlib.sha256(b'bad').hexdigest()\n", encoding="utf-8")

    assert _hashlib_import_violations(tmp_path) == [
        "scripts/e4_parity/rogue_hash.py:1 imports hashlib",
    ]
