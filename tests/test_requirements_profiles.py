from __future__ import annotations

from pathlib import Path


def _normalized_requirement_lines(path: Path) -> list[str]:
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            lines.append(line)
    return lines


def test_core_profile_excludes_atp_api_extras() -> None:
    root = Path(".")
    core_lines = _normalized_requirement_lines(root / "requirements-core.txt")
    forbidden_prefixes = (
        "fastapi",
        "uvicorn",
        "requests",
        "python-dotenv",
        "python-multipart",
    )
    for prefix in forbidden_prefixes:
        assert not any(line.startswith(prefix) for line in core_lines), (
            f"requirements-core.txt must not include ATP/API extra dependency: {prefix}"
        )


def test_atp_profile_includes_core_and_api_extras() -> None:
    root = Path(".")
    atp_lines = _normalized_requirement_lines(root / "requirements-atp.txt")
    assert "-r requirements-core.txt" in atp_lines
    required_prefixes = (
        "fastapi",
        "uvicorn",
        "requests",
        "python-dotenv",
        "python-multipart",
        "aristotlelib",
    )
    for prefix in required_prefixes:
        assert any(line.startswith(prefix) for line in atp_lines), (
            f"requirements-atp.txt must include ATP/API extra dependency: {prefix}"
        )
