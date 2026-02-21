from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _canonical_manifest() -> dict:
    return {
        "schema_version": "ink_reference_manifest_v2",
        "generated_basis_utc": "2026-02-18T20:09:59Z",
        "repositories": [
            {
                "name": "alpha",
                "source_url": "https://github.com/example/alpha.git",
                "branch": "main",
                "remote_url": "https://github.com/example/alpha.git",
                "sha": "a" * 40,
                "short_sha": "aaaaaaa",
                "commit_date": "2026-02-18T20:09:59+00:00",
                "workspace_relative_path": "other_harness_refs/alpha",
                "capture_date_utc": "2026-02-18T20:09:59Z",
                "license_spdx": "Apache-2.0",
                "license_note": "Pattern-reference only; do not copy code.",
                "license_file": "LICENSE",
            },
            {
                "name": "beta",
                "source_url": "https://github.com/example/beta.git",
                "branch": "main",
                "remote_url": "https://github.com/example/beta.git",
                "sha": "b" * 40,
                "short_sha": "bbbbbbb",
                "commit_date": "2026-02-18T20:09:59+00:00",
                "workspace_relative_path": "other_harness_refs/beta",
                "capture_date_utc": "2026-02-18T20:09:59Z",
                "license_spdx": "MIT",
                "license_note": "Pattern-reference only; do not copy code.",
                "license_file": "LICENSE",
            },
        ],
    }


def _write_fixture(tmp_path: Path, payload: dict) -> tuple[Path, Path]:
    manifest_json = tmp_path / "manifest.json"
    manifest_md = tmp_path / "manifest.md"
    manifest_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    # Generate canonical markdown using the sync script in verify mode over this isolated manifest dir.
    manifest_md.write_text(
        "# Ink Reference Repositories Manifest\n\n"
        "- Schema: `ink_reference_manifest_v2`\n"
        "- Generated basis (UTC): `2026-02-18T20:09:59Z`\n\n"
        "| Repo | Branch | Short SHA | Full SHA | Commit Date | Capture Date (UTC) | License | Source URL | Workspace Path |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
        "| `alpha` | `main` | `aaaaaaa` | `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa` | `2026-02-18T20:09:59+00:00` | `2026-02-18T20:09:59Z` | `Apache-2.0` | `https://github.com/example/alpha.git` | `other_harness_refs/alpha` |\n"
        "| `beta` | `main` | `bbbbbbb` | `bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb` | `2026-02-18T20:09:59+00:00` | `2026-02-18T20:09:59Z` | `MIT` | `https://github.com/example/beta.git` | `other_harness_refs/beta` |\n\n"
        "## License Notes\n\n"
        "| Repo | License File | Note |\n"
        "|---|---|---|\n"
        "| `alpha` | `LICENSE` | Pattern-reference only; do not copy code. |\n"
        "| `beta` | `LICENSE` | Pattern-reference only; do not copy code. |\n",
        encoding="utf-8",
    )
    return manifest_json, manifest_md


def test_validate_ink_manifest_accepts_canonical(tmp_path: Path) -> None:
    payload = _canonical_manifest()
    manifest_json, manifest_md = _write_fixture(tmp_path, payload)
    script = Path(__file__).resolve().parents[1] / "scripts" / "validate_ink_references_manifest.py"
    proc = subprocess.run(
        [
            "python",
            str(script),
            "--manifest-json",
            str(manifest_json),
            "--manifest-md",
            str(manifest_md),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_validate_ink_manifest_rejects_bad_sha(tmp_path: Path) -> None:
    payload = _canonical_manifest()
    payload["repositories"][0]["sha"] = "not-a-sha"
    manifest_json, manifest_md = _write_fixture(tmp_path, payload)
    script = Path(__file__).resolve().parents[1] / "scripts" / "validate_ink_references_manifest.py"
    proc = subprocess.run(
        [
            "python",
            str(script),
            "--manifest-json",
            str(manifest_json),
            "--manifest-md",
            str(manifest_md),
            "--skip-md-parity",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    combined = proc.stdout + proc.stderr
    assert "invalid sha" in combined

