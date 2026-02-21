#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKSPACE_ROOT="${WORKSPACE_ROOT:-$(cd "$REPO_ROOT/.." && pwd)}"
REFS_ROOT="${INK_REFS_ROOT:-$WORKSPACE_ROOT/other_harness_refs}"
MANIFEST_JSON="${INK_MANIFEST_JSON:-$REPO_ROOT/docs/ink_references/INK_REFERENCE_REPOS_MANIFEST_CURRENT.json}"
MANIFEST_MD="${INK_MANIFEST_MD:-$REPO_ROOT/docs/ink_references/INK_REFERENCE_REPOS_MANIFEST_CURRENT.md}"
VALIDATION_REPORT_JSON="${INK_MANIFEST_VALIDATION_REPORT_JSON:-}"

if [[ ! -f "$MANIFEST_JSON" ]]; then
  echo "ERROR: manifest not found: $MANIFEST_JSON" >&2
  exit 1
fi

validator_args=(
  "scripts/validate_ink_references_manifest.py"
  "--manifest-json" "$MANIFEST_JSON"
  "--manifest-md" "$MANIFEST_MD"
)
if [[ -n "$VALIDATION_REPORT_JSON" ]]; then
  validator_args+=( "--output-json" "$VALIDATION_REPORT_JSON" )
fi
python "${validator_args[@]}"

if [[ ! -d "$REFS_ROOT" ]]; then
  echo "SKIP: references directory absent ($REFS_ROOT); nothing to verify."
  exit 0
fi

python - "$MANIFEST_JSON" "$REFS_ROOT" <<'PY'
import json
import os
import pathlib
import subprocess
import sys

manifest_path = pathlib.Path(sys.argv[1])
refs_root = pathlib.Path(sys.argv[2])
payload = json.loads(manifest_path.read_text(encoding="utf-8"))
repos = payload.get("repositories", [])

present_count = 0
errors = []
missing = []

def git(repo_dir: pathlib.Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()

for item in repos:
    name = item["name"]
    repo_dir = refs_root / name
    git_dir = repo_dir / ".git"
    if not git_dir.exists():
        missing.append(name)
        continue

    present_count += 1
    expected_sha = item["sha"]
    expected_branch = item["branch"]
    expected_remote = item["remote_url"]

    actual_sha = git(repo_dir, "rev-parse", "HEAD")
    actual_branch = git(repo_dir, "branch", "--show-current")
    actual_remote = git(repo_dir, "remote", "get-url", "origin")

    if actual_sha != expected_sha:
        errors.append(f"{name}: sha mismatch expected={expected_sha} actual={actual_sha}")
    if actual_branch != expected_branch:
        errors.append(f"{name}: branch mismatch expected={expected_branch} actual={actual_branch}")
    if actual_remote != expected_remote:
        errors.append(f"{name}: remote mismatch expected={expected_remote} actual={actual_remote}")

if present_count == 0:
    print(f"SKIP: no reference repos present under {refs_root}; check is non-blocking in this environment.")
    sys.exit(0)

if missing:
    print("WARN: manifest repos missing locally:", ", ".join(sorted(missing)))

if errors:
    print("ERROR: ink reference manifest verification failed:")
    for err in errors:
        print(f"  - {err}")
    sys.exit(1)

print(f"PASS: verified {present_count} reference repo(s) against manifest {manifest_path}.")
PY
