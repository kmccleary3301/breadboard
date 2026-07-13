from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from breadboard.rl.m12 import validate_m12_transfer_archive_manifest  # noqa: E402


def _resolve_manifest_path(manifest_path: Path, manifest: dict, key: str, fallback_name: str) -> Path:
    raw = manifest.get(key)
    if not raw:
        return manifest_path.parent / fallback_name
    path = Path(str(raw))
    return path if path.is_absolute() else manifest_path.parent / path


def _build_archive_verify_report(manifest_path: Path, errors: list[str]) -> dict:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        manifest = {}
        manifest_read_error = f"{type(exc).__name__}: {exc}"
    else:
        manifest_read_error = None

    archive_name = str(manifest.get("archive_name") or "m12_transfer_evidence_pack.tar.gz")
    archive_path = _resolve_manifest_path(manifest_path, manifest, "archive_path", archive_name)
    sha_path = _resolve_manifest_path(manifest_path, manifest, "archive_sha256_file", archive_name + ".sha256")
    return {
        "report_id": "bb_zyphra_rl_phase1_m12_archive_verify_report_v1",
        "claim_boundary": "transfer_archive_verification_not_m12_validation",
        "scorecard_update_allowed": False,
        "m12_points_awarded": False,
        "status": "passed" if not errors else "failed",
        "manifest_path": str(manifest_path),
        "manifest_read_error": manifest_read_error,
        "archive_manifest_id": manifest.get("archive_manifest_id"),
        "archive_claim_boundary": manifest.get("claim_boundary"),
        "archive_path": str(archive_path),
        "archive_sha256_file": str(sha_path),
        "archive_sha256": manifest.get("archive_sha256"),
        "archive_size_bytes": manifest.get("archive_size_bytes"),
        "included_entry_count": manifest.get("included_entry_count"),
        "all_required_artifacts_present": manifest.get("all_required_artifacts_present"),
        "all_transfer_requirements_covered": manifest.get("all_transfer_requirements_covered"),
        "archive_contains_source_overlay": manifest.get("archive_contains_source_overlay"),
        "archive_deterministic": manifest.get("archive_deterministic"),
        "source_paths_portable": manifest.get("source_paths_portable"),
        "errors": list(errors),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify an M12 transfer evidence archive manifest and tarball.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_transfer_prep/m12_transfer_archive_manifest.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path for a non-scoring machine-readable archive-verifier report.",
    )
    args = parser.parse_args()

    errors = validate_m12_transfer_archive_manifest(args.manifest)
    report = _build_archive_verify_report(args.manifest, errors)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if errors:
        print("status=failed archive_manifest_verified=false")
        if args.output is not None:
            print(f"archive_verify_report={args.output}")
        for error in errors:
            print(f"error={error}")
        raise SystemExit(5)
    output_fragment = f" archive_verify_report={args.output}" if args.output is not None else ""
    print("status=passed archive_manifest_verified=true" + output_fragment)


if __name__ == "__main__":
    main()
