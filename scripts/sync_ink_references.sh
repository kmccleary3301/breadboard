#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKSPACE_ROOT="${WORKSPACE_ROOT:-$(cd "$REPO_ROOT/.." && pwd)}"
REFS_ROOT="${INK_REFS_ROOT:-$WORKSPACE_ROOT/other_harness_refs}"
MANIFEST_DIR="${INK_MANIFEST_DIR:-$REPO_ROOT/docs/ink_references}"
MANIFEST_JSON="$MANIFEST_DIR/INK_REFERENCE_REPOS_MANIFEST_CURRENT.json"
MANIFEST_MD="$MANIFEST_DIR/INK_REFERENCE_REPOS_MANIFEST_CURRENT.md"
REPORT_JSON="${INK_MANIFEST_REPORT_JSON:-}"
ALLOW_MISSING_ON_VERIFY="${INK_VERIFY_ALLOW_MISSING:-1}"

MODE="apply"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      MODE="dry-run"
      ;;
    --apply)
      MODE="apply"
      ;;
    --verify)
      MODE="verify"
      ;;
    --help|-h)
      cat <<'EOF'
Usage: scripts/sync_ink_references.sh [--dry-run|--apply|--verify]

Modes:
  --dry-run  : compute canonical manifest payload locally; do not write files.
  --apply    : refresh repos (clone/pull), then write canonical JSON+MD if changed.
  --verify   : verify tracked JSON+MD are canonical for current refs (or static canonical if refs are absent).

Environment:
  WORKSPACE_ROOT
  INK_REFS_ROOT
  INK_MANIFEST_DIR
  INK_MANIFEST_REPORT_JSON
  INK_VERIFY_ALLOW_MISSING (default: 1)
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
  shift
done

repos=(
  "gemini-cli|https://github.com/google-gemini/gemini-cli.git|main|Apache-2.0|Pattern-reference only; do not copy code."
  "qwen-code|https://github.com/QwenLM/qwen-code.git|main|Apache-2.0|Pattern-reference only; do not copy code."
  "dev3000|https://github.com/vercel-labs/dev3000.git|main|MIT|Pattern-reference only; do not copy code."
)

mkdir -p "$REFS_ROOT" "$MANIFEST_DIR"

rows_tsv="$(mktemp)"
expected_json_tmp="$(mktemp)"
expected_md_tmp="$(mktemp)"
trap 'rm -f "$rows_tsv" "$expected_json_tmp" "$expected_md_tmp"' EXIT

missing=()
present_count=0

for spec in "${repos[@]}"; do
  IFS='|' read -r name url branch license_spdx license_note <<<"$spec"
  target="$REFS_ROOT/$name"

  if [[ ! -d "$target/.git" ]]; then
    if [[ "$MODE" == "apply" ]]; then
      git clone --branch "$branch" "$url" "$target" >/dev/null 2>&1
    else
      missing+=("$name")
      continue
    fi
  fi

  if [[ "$MODE" == "apply" ]]; then
    git -C "$target" fetch origin --prune >/dev/null 2>&1
    git -C "$target" checkout "$branch" >/dev/null 2>&1
    git -C "$target" pull --ff-only origin "$branch" >/dev/null 2>&1
  fi

  remote_url="$(git -C "$target" remote get-url origin)"
  sha="$(git -C "$target" rev-parse HEAD)"
  short_sha="$(git -C "$target" rev-parse --short=7 HEAD)"
  commit_date="$(git -C "$target" show -s --format=%cI HEAD)"
  commit_ts="$(git -C "$target" show -s --format=%ct HEAD)"
  capture_date_utc="$(python - "$commit_ts" <<'PY'
import datetime
import sys
ts = int(sys.argv[1])
print(datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
PY
)"
  rel_path="$(python - "$WORKSPACE_ROOT" "$target" <<'PY'
import os
import sys
root = os.path.realpath(sys.argv[1])
target = os.path.realpath(sys.argv[2])
print(os.path.relpath(target, root))
PY
)"

  license_file=""
  for candidate in LICENSE LICENSE.md LICENSE.txt COPYING COPYING.md COPYING.txt; do
    if [[ -f "$target/$candidate" ]]; then
      license_file="$candidate"
      break
    fi
  done

  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
    "$name" "$url" "$branch" "$remote_url" "$sha" "$short_sha" "$commit_date" "$rel_path" "$capture_date_utc" "$license_spdx" "$license_note" "$license_file" "$commit_ts" >>"$rows_tsv"
  present_count=$((present_count + 1))
done

if [[ "$MODE" == "verify" && "${#missing[@]}" -gt 0 && "$ALLOW_MISSING_ON_VERIFY" != "1" ]]; then
  echo "ERROR: missing reference repos in verify mode: ${missing[*]}" >&2
  exit 1
fi

python - "$rows_tsv" "$MANIFEST_JSON" "$expected_json_tmp" "$expected_md_tmp" <<'PY'
import csv
import datetime
import json
import pathlib
import sys
from typing import Any, Dict, List

rows_path = pathlib.Path(sys.argv[1])
current_manifest_json = pathlib.Path(sys.argv[2])
expected_json = pathlib.Path(sys.argv[3])
expected_md = pathlib.Path(sys.argv[4])


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


records: List[Dict[str, Any]] = []
if rows_path.exists() and rows_path.read_text(encoding="utf-8").strip():
    with rows_path.open("r", encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for row in reader:
            (
                name,
                source_url,
                branch,
                remote_url,
                sha,
                short_sha,
                commit_date,
                rel_path,
                capture_date_utc,
                license_spdx,
                license_note,
                license_file,
                commit_ts,
            ) = row
            records.append(
                {
                    "name": name,
                    "source_url": source_url,
                    "branch": branch,
                    "remote_url": remote_url,
                    "sha": sha,
                    "short_sha": short_sha,
                    "commit_date": commit_date,
                    "workspace_relative_path": rel_path,
                    "capture_date_utc": capture_date_utc,
                    "license_spdx": license_spdx,
                    "license_note": license_note,
                    "license_file": license_file,
                    "_commit_ts": int(commit_ts),
                }
            )

if not records:
    payload = json.loads(current_manifest_json.read_text(encoding="utf-8"))
    payload["repositories"] = sorted(payload.get("repositories", []), key=lambda r: str(r.get("name", "")))
else:
    records = sorted(records, key=lambda r: r["name"])
    basis_ts = max(int(r["_commit_ts"]) for r in records)
    generated_basis_utc = datetime.datetime.fromtimestamp(basis_ts, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for rec in records:
        rec.pop("_commit_ts", None)
    payload = {
        "schema_version": "ink_reference_manifest_v2",
        "generated_basis_utc": generated_basis_utc,
        "repositories": records,
    }

expected_json.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
expected_md.write_text(_render_md(payload), encoding="utf-8")
PY

changed_json=0
changed_md=0
if [[ ! -f "$MANIFEST_JSON" ]] || ! cmp -s "$expected_json_tmp" "$MANIFEST_JSON"; then
  changed_json=1
fi
if [[ ! -f "$MANIFEST_MD" ]] || ! cmp -s "$expected_md_tmp" "$MANIFEST_MD"; then
  changed_md=1
fi

changed=$(( changed_json || changed_md ))

if [[ "$MODE" == "apply" ]]; then
  if [[ "$changed_json" -eq 1 ]]; then
    cp "$expected_json_tmp" "$MANIFEST_JSON"
  fi
  if [[ "$changed_md" -eq 1 ]]; then
    cp "$expected_md_tmp" "$MANIFEST_MD"
  fi
  echo "Updated:"
  echo "  $MANIFEST_JSON"
  echo "  $MANIFEST_MD"
  echo "Changed: $changed (json=$changed_json md=$changed_md)"
elif [[ "$MODE" == "verify" ]]; then
  if [[ "$changed" -eq 1 ]]; then
    echo "ERROR: ink reference manifest drift detected (json=$changed_json md=$changed_md)." >&2
    echo "Run: scripts/sync_ink_references.sh --apply" >&2
    exit 1
  fi
  echo "PASS: ink reference manifest is canonical."
else
  echo "Dry run complete."
  echo "Would change: $changed (json=$changed_json md=$changed_md)"
fi

if [[ -n "$REPORT_JSON" ]]; then
  mkdir -p "$(dirname "$REPORT_JSON")"
  python - "$REPORT_JSON" "$MODE" "$present_count" "$changed" "$changed_json" "$changed_md" "$(IFS=,; echo "${missing[*]}")" <<'PY'
import json
import pathlib
import sys

report_path = pathlib.Path(sys.argv[1])
payload = {
    "mode": sys.argv[2],
    "repos_present": int(sys.argv[3]),
    "changed": bool(int(sys.argv[4])),
    "changed_json": bool(int(sys.argv[5])),
    "changed_md": bool(int(sys.argv[6])),
    "missing_repos": [x for x in sys.argv[7].split(",") if x],
}
report_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
  echo "Wrote report: $REPORT_JSON"
fi
