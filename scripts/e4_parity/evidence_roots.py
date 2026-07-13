from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVIDENCE_ROOT = WORKSPACE_ROOT.parent / "docs_tmp"
_ALLOWED_LITERAL_FILES = {
    "scripts/e4_parity/evidence_roots.py",
}


@dataclass(frozen=True)
class DocsTmpLiteral:
    path: str
    line: int
    text: str


def evidence_root(value: str | Path | None = None, *, workspace_root: Path = WORKSPACE_ROOT) -> Path:
    """Return the canonical evidence root.

    Relative roots resolve from the workspace root so callers can pass the
    project-standard ../docs_tmp regardless of their current directory.
    """

    root = Path(value) if value is not None else DEFAULT_EVIDENCE_ROOT
    if root.is_absolute():
        return root.resolve()
    return (workspace_root / root).resolve()


def resolve_evidence_path(*parts: str | Path, root: str | Path | None = None, workspace_root: Path = WORKSPACE_ROOT) -> Path:
    base = evidence_root(root, workspace_root=workspace_root)
    candidate = base.joinpath(*(str(part) for part in parts)).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"evidence path escapes root: {candidate}") from exc
    return candidate


def _iter_files(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if path.is_dir():
            yield from sorted(
                child
                for child in path.rglob("*")
                if child.is_file()
                and ".git" not in child.parts
                and "node_modules" not in child.parts
                and "__pycache__" not in child.parts
            )
        elif path.is_file():
            yield path


def find_docs_tmp_literals(paths: Sequence[Path], *, workspace_root: Path = WORKSPACE_ROOT, allowed_files: set[str] | None = None) -> list[DocsTmpLiteral]:
    allowed = set(_ALLOWED_LITERAL_FILES if allowed_files is None else allowed_files)
    findings: list[DocsTmpLiteral] = []
    for file_path in _iter_files(paths):
        try:
            rel = file_path.resolve().relative_to(workspace_root).as_posix()
        except ValueError:
            rel = file_path.as_posix()
        if rel in allowed:
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if "docs_tmp" in line:
                findings.append(DocsTmpLiteral(path=rel, line=line_number, text=line.strip()))
    return findings


def _literal_key(finding: DocsTmpLiteral) -> tuple[str, str]:
    return finding.path, finding.text


def load_literal_baseline(path: Path) -> set[tuple[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        (str(item["path"]), str(item["text"]))
        for item in data.get("literals", [])
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve and lint BreadBoard evidence roots.")
    parser.add_argument("paths", nargs="*", default=["scripts", "agentic_coder_prototype", "tests"], help="Files or directories to scan")
    parser.add_argument("--root", default=None, help="Evidence root to resolve; relative paths resolve from workspace root")
    parser.add_argument("--baseline", default="", help="Optional JSON baseline of accepted docs_tmp literals")
    parser.add_argument("--json-out", default="", help="Optional JSON report path")
    parser.add_argument("--check", action="store_true", help="Exit non-zero when unexpected docs_tmp literals are found")
    args = parser.parse_args(argv)

    resolved_root = evidence_root(args.root)
    findings = find_docs_tmp_literals([Path(path) for path in args.paths])
    baseline = load_literal_baseline(Path(args.baseline)) if args.baseline else set()
    unexpected = [finding for finding in findings if _literal_key(finding) not in baseline]
    report = {
        "schema_version": "bb.e4.evidence_roots_lint.v1",
        "workspace_root": WORKSPACE_ROOT.as_posix(),
        "evidence_root": resolved_root.as_posix(),
        "literal_count": len(findings),
        "baseline_count": len(baseline),
        "unexpected_count": len(unexpected),
        "literals": [asdict(finding) for finding in findings],
        "unexpected_literals": [asdict(finding) for finding in unexpected],
        "ok": len(unexpected) == 0,
    }
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 3 if args.check and unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main())
