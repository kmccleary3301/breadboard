from __future__ import annotations

import copy
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

import pytest

from agentic_coder_prototype.compilation.primitive_records import canonical_record_bytes, sha256_ref
from agentic_coder_prototype.conformance.catalog_binding import catalog_segments, stable_entries_hash


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
CATALOG_PATH = ROOT / "docs" / "conformance" / "e4_artifact_catalog.json"
MAINTAINED_EVIDENCE_ROOTS = (
    ROOT / "docs" / "conformance",
    WORKSPACE / "docs_tmp" / "phase_15",
)
SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
TEXT_SUFFIXES = {".csv", ".json", ".md", ".txt", ".yaml", ".yml"}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _catalog_hashes(catalog: Mapping[str, Any]) -> set[str]:
    return {str(entry.get("sha256")) for entry in catalog.get("entries", []) if isinstance(entry, Mapping)}


def _catalog_integrity(catalog: Mapping[str, Any]) -> dict[str, Any]:
    entries = catalog.get("entries")
    assert isinstance(entries, list)
    integrity = {
        "entry_count": len(entries),
        "entries_hash": sha256_ref(canonical_record_bytes(entries)),
        "stable_entries_hash": stable_entries_hash(entries),
    }
    if catalog.get("schema_version") == "bb.e4.artifact_catalog.v2":
        integrity["segments_hash"] = sha256_ref(canonical_record_bytes(catalog_segments(entries)))
    return integrity


def _text_files(roots: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.is_file() and root.suffix.lower() in TEXT_SUFFIXES:
            files.append(root)
            continue
        if root.is_dir():
            files.extend(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES)
    return sorted(files)


def _duplicate_hash_literals(files: Iterable[Path]) -> dict[str, set[Path]]:
    occurrences: dict[str, set[Path]] = defaultdict(set)
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for digest in SHA256_RE.findall(text):
            occurrences[digest].add(path)
    return {digest: paths for digest, paths in occurrences.items() if len(paths) >= 2}


def _assert_duplicate_literals_are_cataloged(
    *,
    catalog: Mapping[str, Any],
    roots: Iterable[Path],
    allowed_uncataloged_hashes: set[str] | None = None,
) -> None:
    cataloged = _catalog_hashes(catalog)
    allowed = allowed_uncataloged_hashes or set()
    duplicate_literals = _duplicate_hash_literals(_text_files(roots))
    missing = sorted(digest for digest in duplicate_literals if digest not in cataloged and digest not in allowed)
    assert missing == []


def test_catalog_integrity_block_matches_entries() -> None:
    catalog = _load_json(CATALOG_PATH)

    assert catalog["integrity"] == _catalog_integrity(catalog)


def test_maintained_evidence_duplicate_catalog_hashes_remain_cataloged() -> None:
    catalog = _load_json(CATALOG_PATH)
    cataloged = _catalog_hashes(catalog)
    duplicate_literals = _duplicate_hash_literals(_text_files(MAINTAINED_EVIDENCE_ROOTS))
    duplicated_catalog_hashes = {digest for digest in duplicate_literals if digest in cataloged}

    assert duplicated_catalog_hashes
    assert duplicated_catalog_hashes <= cataloged


def test_duplicate_literal_gate_rejects_uncataloged_reused_sha(tmp_path: Path) -> None:
    catalog = _load_json(CATALOG_PATH)
    orphan_digest = "sha256:" + "f" * 64
    docs = tmp_path / "docs" / "conformance"
    reports = tmp_path / "docs_tmp" / "phase_15"
    docs.mkdir(parents=True)
    reports.mkdir(parents=True)
    (docs / "a.json").write_text(json.dumps({"sha256": orphan_digest}) + "\n", encoding="utf-8")
    (reports / "b.json").write_text(json.dumps({"sha256": orphan_digest}) + "\n", encoding="utf-8")

    with pytest.raises(AssertionError, match=orphan_digest):
        _assert_duplicate_literals_are_cataloged(catalog=catalog, roots=(docs, reports))


def test_duplicate_literal_gate_accepts_cataloged_reused_sha(tmp_path: Path) -> None:
    catalog = copy.deepcopy(_load_json(CATALOG_PATH))
    reused_digest = next(entry["sha256"] for entry in catalog["entries"])
    docs = tmp_path / "docs" / "conformance"
    reports = tmp_path / "docs_tmp" / "phase_15"
    docs.mkdir(parents=True)
    reports.mkdir(parents=True)
    (docs / "a.json").write_text(json.dumps({"sha256": reused_digest}) + "\n", encoding="utf-8")
    (reports / "b.json").write_text(json.dumps({"sha256": reused_digest}) + "\n", encoding="utf-8")

    _assert_duplicate_literals_are_cataloged(catalog=catalog, roots=(docs, reports))
