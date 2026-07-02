from __future__ import annotations

import argparse
import re
import json
import zipfile
from pathlib import Path
from typing import Iterable


try:
    from scripts.e4_parity.validators import hash_utils as _hash_utils
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from validators import hash_utils as _hash_utils


SCHEMA_VERSION = "bb.e4_source_index.v1"
INDEX_FILENAME = "BB_E4_SOURCE_INDEX.json"
GENERATED_NAMES = {
    INDEX_FILENAME,
    "e4_source_index.json",
    "BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json",
    "atomic_feature_ledger_seed.json",
    "BB_E4_ATOMIC_FEATURE_LEDGER_REPORT.json",
    "atomic_feature_ledger_report.json",
    "BB_E4_FINAL_READINESS_REPORT.md",
}
SKIP_DIR_NAMES = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules", ".venv", ".venv_linux_import_20260618"}
# E4 packet source freeze (accepted 2026-07-04): the accepted packet's atomic feature
# ledger derives row source_refs from this index by keyword scoring over MEMBERSHIP,
# so accepted evidence requires a frozen file set. Files introduced by the post-
# acceptance v2 kernel dialect (BB-ER hardening campaign) are intentionally excluded.
# Extending the E4 packet's source scope is a deliberate act: update these patterns
# AND record a dated amendment in docs_tmp/phase_16/BB_ER_PROGRESS.json.
POST_FREEZE_FILE_PATTERNS = (
    re.compile(r"\.v(?:[2-9]|\d{2,})\.schema\.json$"),
    re.compile(r"_v(?:[2-9]|\d{2,})_minimal\.json$"),
    re.compile(r"^runtime_records_v(?:[2-9]|\d{2,})\.md$"),
)
# Files that match the post-freeze patterns but are MEMBERS of the accepted packet
# (bb.e4.support_claim.v2 predates the freeze). Never remove entries from this set.
E4_PACKET_V2_MEMBERS = {
    "bb.e4.support_claim.v2.schema.json",
    "e4_support_claim_v2_minimal.json",
}

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = REPO_ROOT.parent
PLAN_ROOT = WORKSPACE_ROOT / "docs_tmp" / "phase_15"
DEFAULT_OUT = PLAN_ROOT / INDEX_FILENAME


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    for base in (WORKSPACE_ROOT.resolve(), REPO_ROOT.resolve()):
        try:
            return resolved.relative_to(base).as_posix()
        except ValueError:
            continue
    return resolved.as_posix()


def _sha256_bytes(data: bytes) -> str:
    return _hash_utils.sha256_hex(data)


def _sha256_file(path: Path) -> str:
    return _hash_utils.sha256_file(path).removeprefix("sha256:")


def _entry_id(kind: str, source_ref: str) -> str:
    return f"src_{_hash_utils.sha256_hex(f'{kind}:{source_ref}'.encode('utf-8'))[:16]}"


def _iter_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        if root.name in GENERATED_NAMES or root.name == ".DS_Store":
            return
        if root.name not in E4_PACKET_V2_MEMBERS and any(
            pattern.search(root.name) for pattern in POST_FREEZE_FILE_PATTERNS
        ):
            return
        yield root
        return
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.name in GENERATED_NAMES or path.name == ".DS_Store":
            continue
        if path.name not in E4_PACKET_V2_MEMBERS and any(
            pattern.search(path.name) for pattern in POST_FREEZE_FILE_PATTERNS
        ):
            continue
        yield path


def _index_fs_path(path: Path) -> list[dict]:
    rows: list[dict] = []
    for file_path in _iter_files(path):
        rel_path = _display_path(file_path)
        source_ref = f"fs:{rel_path}"
        rows.append(
            {
                "entry_id": _entry_id("fs", source_ref),
                "kind": "filesystem",
                "source_ref": source_ref,
                "path": rel_path,
                "byte_size": file_path.stat().st_size,
                "sha256": _sha256_file(file_path),
            }
        )
    return rows


def _index_zip(path: Path) -> list[dict]:
    rows: list[dict] = []
    zip_path = _display_path(path)
    with zipfile.ZipFile(path) as archive:
        for info in sorted(archive.infolist(), key=lambda member: member.filename):
            if info.is_dir():
                continue
            if Path(info.filename).name == ".DS_Store":
                continue
            # Frozen archives predate post-freeze exclusions, so zip members are not pattern-filtered.
            data = archive.read(info.filename)
            source_ref = f"zip:{zip_path}!{info.filename}"
            rows.append(
                {
                    "entry_id": _entry_id("zip", source_ref),
                    "kind": "zip_member",
                    "source_ref": source_ref,
                    "zip_path": zip_path,
                    "member_path": info.filename,
                    "byte_size": info.file_size,
                    "compressed_size": info.compress_size,
                    "crc32": f"{info.CRC:08x}",
                    "sha256": _sha256_bytes(data),
                }
            )
    return rows


def default_roots() -> list[Path]:
    candidates = [
        REPO_ROOT / "agent_configs",
        REPO_ROOT / "config" / "e4_target_freeze_manifest.yaml",
        REPO_ROOT / "contracts" / "kernel",
        REPO_ROOT / "docs" / "contracts",
    ]
    candidates.extend(sorted(PLAN_ROOT.glob("*.md"), key=lambda item: item.as_posix()))
    candidates.extend(sorted(PLAN_ROOT.glob("*.txt"), key=lambda item: item.as_posix()))
    candidates.extend(sorted(PLAN_ROOT.glob("*ATTACHMENTS_FLAT"), key=lambda item: item.as_posix()))
    candidates.extend(sorted(PLAN_ROOT.glob("*.zip"), key=lambda item: item.as_posix()))
    return [path for path in candidates if path.exists()]


def build_source_index(roots: list[Path] | None = None) -> dict:
    selected_roots = roots or default_roots()
    entries: list[dict] = []
    seen_refs: set[str] = set()
    for root in sorted((path.resolve() for path in selected_roots), key=lambda item: item.as_posix()):
        root_entries = _index_zip(root) if root.is_file() and root.suffix.lower() == ".zip" else _index_fs_path(root)
        for entry in root_entries:
            if entry["source_ref"] in seen_refs:
                continue
            seen_refs.add(entry["source_ref"])
            entries.append(entry)
    entries.sort(key=lambda row: row["source_ref"])
    return {
        "schema_version": SCHEMA_VERSION,
        "repo_root": _display_path(REPO_ROOT),
        "plan_root": _display_path(PLAN_ROOT),
        "root_refs": [_display_path(path) for path in sorted(selected_roots, key=lambda item: item.resolve().as_posix())],
        "entry_count": len(entries),
        "entries": entries,
    }


def write_source_index(out_path: Path = DEFAULT_OUT, roots: list[Path] | None = None) -> dict:
    payload = build_source_index(roots)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"out_path": _display_path(out_path), "entry_count": payload["entry_count"]}


def _parse_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (Path.cwd() / path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic E4 source/path index metadata for paths and zip files.")
    parser.add_argument("--root", action="append", default=[], help="Filesystem file/directory or .zip to index; repeatable. Defaults to phase_15 E4 inputs and BreadBoard refs.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output JSON path.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable summary.")
    args = parser.parse_args()

    roots = [_parse_path(value) for value in args.root] if args.root else None
    summary = write_source_index(_parse_path(args.out), roots)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"source_index={summary['out_path']} entries={summary['entry_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
