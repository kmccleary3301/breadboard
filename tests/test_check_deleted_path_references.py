from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = REPO_ROOT / "scripts" / "check_deleted_path_references.py"
DELETED_PATH = "scripts/e4_parity/build_old_lane.py"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _track_all(repo: Path) -> None:
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)


def _audit(repo: Path, *extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(AUDIT_SCRIPT),
            "--repo-root",
            str(repo),
            "--deleted-path",
            DELETED_PATH,
            *extra_args,
            "--json",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("live_path", "reference"),
    [
        ("docs/runbook.md", "python scripts/e4_parity/build_old_lane.py --json"),
        ("tests/test_old_lane.py", 'BUILDER = "scripts/e4_parity/build_old_lane.py"'),
        (".github/workflows/old-lane.yml", "run: python scripts/e4_parity/build_old_lane.py"),
        (
            "tests/test_dynamic_import.py",
            'importlib.import_module("scripts.e4_parity.build_old_lane")',
        ),
        ("pyproject.toml", 'old-lane = "scripts.e4_parity.build_old_lane:main"'),
    ],
)
def test_deleted_path_audit_blocks_a_live_reference(tmp_path: Path, live_path: str, reference: str) -> None:
    repo = tmp_path / "repo"
    _write(repo / live_path, f"{reference}\n")
    _track_all(repo)

    proc = _audit(repo)

    assert proc.returncode == 1, proc.stderr or proc.stdout
    report = json.loads(proc.stdout)
    assert report["ok"] is False
    assert report["live_reference_count"] == 1
    assert report["live_references"][0]["path"] == live_path
    assert report["immutable_historical_reference_count"] == 0


def test_deleted_path_audit_allows_a_digest_bound_immutable_snapshot(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    snapshot = repo / "docs" / "conformance" / "evidence_snapshots" / "campaign" / "run.json"
    snapshot_text = json.dumps({"command": "python scripts/e4_parity/build_old_lane.py --json"}) + "\n"
    _write(snapshot, snapshot_text)
    manifest = snapshot.parent / "MANIFEST.json"
    _write(
        manifest,
        json.dumps(
            {
                "schema": "bb.evidence_snapshot.v1",
                "files": {"run.json": {"sha256": hashlib.sha256(snapshot_text.encode()).hexdigest()}},
            }
        )
        + "\n",
    )
    _track_all(repo)

    proc = _audit(
        repo,
        "--immutable-snapshot-manifest",
        manifest.relative_to(repo).as_posix(),
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    report = json.loads(proc.stdout)
    assert report["ok"] is True
    assert report["live_reference_count"] == 0
    assert report["immutable_historical_reference_count"] == 1
    assert report["immutable_historical_references"][0]["path"] == snapshot.relative_to(repo).as_posix()


def test_deleted_path_audit_does_not_exempt_unlisted_snapshot_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    snapshot_root = repo / "docs" / "conformance" / "evidence_snapshots" / "campaign"
    listed = snapshot_root / "listed.json"
    _write(listed, '{"status":"historical"}\n')
    _write(snapshot_root / "unlisted.json", '{"command":"python scripts/e4_parity/build_old_lane.py"}\n')
    manifest = snapshot_root / "MANIFEST.json"
    _write(
        manifest,
        json.dumps({"files": {"listed.json": hashlib.sha256(listed.read_bytes()).hexdigest()}}) + "\n",
    )
    _track_all(repo)

    proc = _audit(
        repo,
        "--immutable-snapshot-manifest",
        manifest.relative_to(repo).as_posix(),
    )

    assert proc.returncode == 1, proc.stderr or proc.stdout
    report = json.loads(proc.stdout)
    assert report["live_reference_count"] == 1
    assert report["live_references"][0]["path"].endswith("unlisted.json")


def test_deleted_path_audit_allows_the_digest_bound_sp6_historical_record(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    record = repo / "docs_tmp" / "phase_20" / "evidence" / "SP6" / "implementer.json"
    record_text = '{"command":"python scripts/e4_parity/build_old_lane.py"}\n'
    _write(record, record_text)
    manifest = repo / "config" / "deletion_audit" / "immutable_historical_records.v1.json"
    _write(
        manifest,
        json.dumps(
            {
                "records": [
                    {
                        "path": record.relative_to(repo).as_posix(),
                        "sha256": hashlib.sha256(record_text.encode()).hexdigest(),
                    }
                ]
            }
        )
        + "\n",
    )
    _track_all(repo)

    proc = _audit(
        repo,
        "--immutable-snapshot-manifest",
        manifest.relative_to(repo).as_posix(),
    )

    assert proc.returncode == 0, proc.stderr or proc.stdout
    report = json.loads(proc.stdout)
    assert report["live_reference_count"] == 0
    assert report["immutable_historical_reference_count"] == 1



def test_deleted_path_audit_rejects_external_record_from_an_unrelated_manifest(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    record = repo / "docs_tmp" / "phase_20" / "evidence" / "SP6" / "implementer.json"
    record_text = '{"command":"python scripts/e4_parity/build_old_lane.py"}\n'
    _write(record, record_text)
    manifest = repo / "docs" / "conformance" / "unrelated_manifest.json"
    _write(
        manifest,
        json.dumps(
            {
                "records": [
                    {
                        "path": record.relative_to(repo).as_posix(),
                        "sha256": hashlib.sha256(record_text.encode()).hexdigest(),
                    }
                ]
            }
        )
        + "\n",
    )
    _track_all(repo)

    proc = _audit(
        repo,
        "--immutable-snapshot-manifest",
        manifest.relative_to(repo).as_posix(),
    )

    assert proc.returncode == 1, proc.stderr or proc.stdout
    report = json.loads(proc.stdout)
    assert report["live_reference_count"] == 1
    assert report["immutable_historical_reference_count"] == 0

def test_deleted_path_audit_cannot_exempt_an_arbitrary_docs_tmp_record(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    record = repo / "docs_tmp" / "phase_20" / "evidence" / "SP6" / "other.json"
    record_text = '{"command":"python scripts/e4_parity/build_old_lane.py"}\n'
    _write(record, record_text)
    manifest = repo / "config" / "deletion_audit" / "immutable_historical_records.v1.json"
    _write(
        manifest,
        json.dumps(
            {
                "records": [
                    {
                        "path": record.relative_to(repo).as_posix(),
                        "sha256": hashlib.sha256(record_text.encode()).hexdigest(),
                    }
                ]
            }
        )
        + "\n",
    )
    _track_all(repo)

    proc = _audit(
        repo,
        "--immutable-snapshot-manifest",
        manifest.relative_to(repo).as_posix(),
    )

    assert proc.returncode == 1, proc.stderr or proc.stdout
    report = json.loads(proc.stdout)
    assert report["live_reference_count"] == 1
    assert report["live_references"][0]["path"] == record.relative_to(repo).as_posix()


def test_deleted_path_audit_rejects_a_historical_record_digest_mismatch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    record = repo / "docs_tmp" / "phase_20" / "evidence" / "SP6" / "implementer.json"
    record_text = '{"command":"python scripts/e4_parity/build_old_lane.py"}\n'
    _write(record, record_text)
    manifest = repo / "config" / "deletion_audit" / "immutable_historical_records.v1.json"
    _write(
        manifest,
        json.dumps(
            {
                "records": [
                    {
                        "path": record.relative_to(repo).as_posix(),
                        "sha256": hashlib.sha256(b"different bytes").hexdigest(),
                    }
                ]
            }
        )
        + "\n",
    )
    _track_all(repo)

    proc = _audit(
        repo,
        "--immutable-snapshot-manifest",
        manifest.relative_to(repo).as_posix(),
    )

    assert proc.returncode == 2, proc.stderr or proc.stdout
    report = json.loads(proc.stdout)
    assert report["live_reference_count"] == 1
    assert report["immutable_historical_reference_count"] == 0
    assert report["manifest_errors"][0].startswith("immutable snapshot digest mismatch:")
