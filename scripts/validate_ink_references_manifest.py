#!/usr/bin/env python3
"""Validate Ink reference manifest structure and optional markdown parity."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Dict, List

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX7P = re.compile(r"^[0-9a-f]{7,12}$")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate docs/ink_references manifest JSON and markdown parity.")
    p.add_argument(
        "--manifest-json",
        default="docs/ink_references/INK_REFERENCE_REPOS_MANIFEST_CURRENT.json",
        help="Manifest JSON path.",
    )
    p.add_argument(
        "--manifest-md",
        default="docs/ink_references/INK_REFERENCE_REPOS_MANIFEST_CURRENT.md",
        help="Manifest markdown path.",
    )
    p.add_argument(
        "--skip-md-parity",
        action="store_true",
        help="Skip markdown parity check against canonical rendering.",
    )
    p.add_argument(
        "--output-json",
        help="Optional path for machine-readable validation summary.",
    )
    return p.parse_args()


def _iso_parse(value: str) -> None:
    candidate = value
    if value.endswith("Z"):
        candidate = value[:-1] + "+00:00"
    dt.datetime.fromisoformat(candidate)


def _render_md(payload: Dict[str, Any]) -> str:
    generated_basis = payload.get("generated_basis_utc", "unknown")
    lines = [
        "# Ink Reference Repositories Manifest",
        "",
        f"- Schema: `{payload.get('schema_version', 'unknown')}`",
        f"- Generated basis (UTC): `{generated_basis}`",
        "",
        "| Repo | Branch | Short SHA | Full SHA | Commit Date | Capture Date (UTC) | License | Source URL | Workspace Path |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for rec in payload.get("repositories", []):
        lines.append(
            "| `{name}` | `{branch}` | `{short_sha}` | `{sha}` | `{commit_date}` | `{capture_date_utc}` | `{license}` | `{source_url}` | `{path}` |".format(
                name=rec["name"],
                branch=rec["branch"],
                short_sha=rec["short_sha"],
                sha=rec["sha"],
                commit_date=rec["commit_date"],
                capture_date_utc=rec["capture_date_utc"],
                license=rec["license_spdx"],
                source_url=rec["source_url"],
                path=rec["workspace_relative_path"],
            )
        )
    lines.extend(
        [
            "",
            "## License Notes",
            "",
            "| Repo | License File | Note |",
            "|---|---|---|",
        ]
    )
    for rec in payload.get("repositories", []):
        lines.append(
            "| `{name}` | `{license_file}` | {note} |".format(
                name=rec["name"],
                license_file=rec.get("license_file", ""),
                note=rec.get("license_note", ""),
            )
        )
    return "\n".join(lines) + "\n"


def _validate(payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []

    if payload.get("schema_version") != "ink_reference_manifest_v2":
        errors.append("schema_version must be ink_reference_manifest_v2")

    basis = payload.get("generated_basis_utc")
    if not isinstance(basis, str) or not basis.strip():
        errors.append("generated_basis_utc missing or empty")
    else:
        try:
            _iso_parse(basis)
        except Exception:
            errors.append(f"generated_basis_utc invalid ISO timestamp: {basis!r}")

    repos = payload.get("repositories")
    if not isinstance(repos, list) or not repos:
        errors.append("repositories must be a non-empty list")
        return errors

    names = [str(r.get("name", "")) for r in repos if isinstance(r, dict)]
    if len(set(names)) != len(names):
        errors.append("repository names must be unique")
    if names != sorted(names):
        errors.append("repositories must be sorted by name")

    required = [
        "name",
        "source_url",
        "branch",
        "remote_url",
        "sha",
        "short_sha",
        "commit_date",
        "workspace_relative_path",
        "capture_date_utc",
        "license_spdx",
        "license_note",
        "license_file",
    ]

    for idx, rec in enumerate(repos):
        if not isinstance(rec, dict):
            errors.append(f"repositories[{idx}] must be an object")
            continue
        for field in required:
            if field not in rec:
                errors.append(f"repositories[{idx}] missing field: {field}")
        sha = str(rec.get("sha", ""))
        short_sha = str(rec.get("short_sha", ""))
        src = str(rec.get("source_url", ""))
        remote = str(rec.get("remote_url", ""))
        rel = str(rec.get("workspace_relative_path", ""))
        branch = str(rec.get("branch", ""))
        license_spdx = str(rec.get("license_spdx", ""))
        license_note = str(rec.get("license_note", ""))

        if not HEX40.match(sha):
            errors.append(f"repositories[{idx}] invalid sha: {sha!r}")
        if not HEX7P.match(short_sha):
            errors.append(f"repositories[{idx}] invalid short_sha: {short_sha!r}")
        if sha and short_sha and not sha.startswith(short_sha):
            errors.append(f"repositories[{idx}] short_sha not prefix of sha")
        if not src.startswith("https://"):
            errors.append(f"repositories[{idx}] source_url must be https://")
        if not remote.startswith("https://"):
            errors.append(f"repositories[{idx}] remote_url must be https://")
        if branch.strip() == "":
            errors.append(f"repositories[{idx}] branch is empty")
        if rel.startswith("/") or rel.startswith("\\"):
            errors.append(f"repositories[{idx}] workspace_relative_path must be relative")
        if not license_spdx.strip():
            errors.append(f"repositories[{idx}] license_spdx is empty")
        if not license_note.strip():
            errors.append(f"repositories[{idx}] license_note is empty")
        for ts_field in ("commit_date", "capture_date_utc"):
            ts = str(rec.get(ts_field, ""))
            try:
                _iso_parse(ts)
            except Exception:
                errors.append(f"repositories[{idx}] invalid {ts_field}: {ts!r}")
    return errors


def main() -> int:
    args = _parse_args()
    manifest_json = Path(args.manifest_json).resolve()
    manifest_md = Path(args.manifest_md).resolve()

    payload: Dict[str, Any] = json.loads(manifest_json.read_text(encoding="utf-8"))
    errors = _validate(payload)
    md_parity_ok = True

    if not args.skip_md_parity:
        expected_md = _render_md(payload)
        actual_md = manifest_md.read_text(encoding="utf-8")
        if expected_md != actual_md:
            md_parity_ok = False
            errors.append("manifest markdown is not canonical; regenerate with sync_ink_references.sh --apply")

    result = {
        "ok": not errors,
        "manifest_json": str(manifest_json),
        "manifest_md": str(manifest_md),
        "md_parity_checked": not args.skip_md_parity,
        "md_parity_ok": md_parity_ok,
        "errors": errors,
    }
    if args.output_json:
        out = Path(args.output_json).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    if errors:
        print("[ink-manifest] FAIL")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("[ink-manifest] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

