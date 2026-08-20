#!/usr/bin/env python3
"""Engine-rename codemod driver (rename plan R-0E1, executes at R-1).

Applies ONLY manifest-approved live rewrites of the engine package name.
Every other disposition (preserve-byte, archive-member, compat-resolve) is a
hard denylist: attempting to rewrite such a file raises ``RefusedRewrite``.

Discipline:
- The audit manifest (``scripts/dev/audit_engine_rename.py`` output) is the
  single approval authority; zero mixed-disposition files (verified by the
  audit guard test), so approval is file-granular.
- Pre-flight verifies each approved file's bytes still match the manifest
  sha256 (no drift since audit) unless ``--allow-stale`` is given.
- Rewritten ``.py`` files must still parse (``ast.parse``) - AST validation
  per plan WS2.2.
- ``--check`` (default) writes nothing; ``--apply`` rewrites in place.
- The report accounts for every approved path plus a repo-wide recount of
  remaining old references so unexpected references are provably zero.

The move itself (``git mv``) is a separate commit performed at R-1; when run
on a post-move tree the driver maps manifest paths through
``legacy_names.canonical_repo_path``.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from agentic_coder_prototype.compat import legacy_names  # noqa: E402

DEFAULT_OLD = "agentic_coder_prototype"


class RefusedRewrite(RuntimeError):
    """Raised when a rewrite of a non-approved file is requested."""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class RenameCodemod:
    def __init__(
        self,
        manifest: dict,
        *,
        old: str,
        new: str,
        root: Path,
        allow_stale: bool = False,
    ) -> None:
        if old == new:
            raise ValueError("old and new names are identical; nothing to do")
        self.old = old
        self.new = new
        self.root = root
        self.allow_stale = allow_stale
        self.approved: dict[str, dict] = {}
        self.denied: dict[str, str] = {}
        for entry in manifest["files"]:
            dispositions = {occ["disposition"] for occ in entry["occurrences"]}
            if len(dispositions) != 1:
                raise SystemExit(f"mixed dispositions in {entry['path']}; audit invariant broken")
            disposition = dispositions.pop()
            if disposition == "live-rewrite":
                self.approved[entry["path"]] = entry
            else:
                self.denied[entry["path"]] = disposition

    # -- path mapping -----------------------------------------------------
    def on_disk(self, manifest_path: str) -> Path:
        """Map a manifest (pre-move) path onto the current tree."""
        direct = self.root / manifest_path
        if direct.exists():
            return direct
        moved = self.root / manifest_path.replace(self.old, self.new, 1)
        if moved.exists():
            return moved
        return direct  # missing; surfaced by caller

    # -- rewrite ----------------------------------------------------------
    def rewrite_file(self, manifest_path: str, *, apply: bool) -> dict:
        if manifest_path in self.denied:
            raise RefusedRewrite(
                f"{manifest_path} is dispositioned {self.denied[manifest_path]!r}; "
                "rewrites are forbidden"
            )
        entry = self.approved.get(manifest_path)
        if entry is None:
            raise RefusedRewrite(f"{manifest_path} is not in the approved live-rewrite set")
        path = self.on_disk(manifest_path)
        if not path.exists():
            return {"path": manifest_path, "status": "missing"}
        payload = path.read_bytes()
        recorded = entry.get("sha256")
        if recorded and sha256_bytes(payload) != recorded and not self.allow_stale:
            return {"path": manifest_path, "status": "stale", "expected_sha256": recorded}
        replaced = payload.replace(self.old.encode(), self.new.encode())
        if replaced == payload:
            return {"path": manifest_path, "status": "no-op"}
        if path.suffix == ".py":
            try:
                ast.parse(replaced.decode("utf-8"), filename=str(path))
            except SyntaxError as exc:
                return {"path": manifest_path, "status": "syntax-error", "error": str(exc)}
        if apply:
            path.write_bytes(replaced)
        return {
            "path": manifest_path,
            "status": "rewritten" if apply else "would-rewrite",
            "occurrences": payload.count(self.old.encode()),
            "sha256_before": sha256_bytes(payload),
            "sha256_after": sha256_bytes(replaced),
        }

    # -- verification -----------------------------------------------------
    def verify_preserved(self) -> list[dict]:
        problems = []
        for manifest_path, disposition in sorted(self.denied.items()):
            path = self.on_disk(manifest_path)
            if not path.exists():
                problems.append({"path": manifest_path, "problem": "missing"})
        return problems

    def remaining_old_references(self) -> dict[str, int]:
        """Repo-wide recount of the old name across tracked files."""
        out = subprocess.run(
            ["git", "grep", "-c", "--untracked", "--no-color", self.old, "--", "."],
            capture_output=True,
            text=True,
            cwd=self.root,
        )
        counts: dict[str, int] = {}
        for line in out.stdout.splitlines():
            path, _, num = line.rpartition(":")
            if path:
                counts[path] = int(num)
        return counts

    def run(self, *, apply: bool) -> dict:
        results = [self.rewrite_file(p, apply=apply) for p in sorted(self.approved)]
        by_status: dict[str, list] = {}
        for res in results:
            by_status.setdefault(res["status"], []).append(res)
        remaining = self.remaining_old_references()
        expected_paths = set(self.denied) | {
            p.replace(self.old, self.new, 1) for p in self.denied
        }
        unexpected = {
            path: count
            for path, count in remaining.items()
            if path not in expected_paths and path not in self.approved
        }
        report = {
            "schema": "bb.rename_codemod_report.v1",
            "old": self.old,
            "new": self.new,
            "mode": "apply" if apply else "check",
            "approved_files": len(self.approved),
            "denied_files": len(self.denied),
            "results_by_status": {k: len(v) for k, v in sorted(by_status.items())},
            "stale": [r["path"] for r in by_status.get("stale", [])],
            "syntax_errors": by_status.get("syntax-error", []),
            "missing": [r["path"] for r in by_status.get("missing", [])],
            "preserved_problems": self.verify_preserved(),
            "remaining_old_reference_files": len(remaining),
            "unexpected_old_references": unexpected,
        }
        report["ok"] = (
            not report["stale"]
            and not report["syntax_errors"]
            and not report["preserved_problems"]
            and not (apply and unexpected)
        )
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--old", default=DEFAULT_OLD)
    parser.add_argument("--new", required=True)
    parser.add_argument("--root", default=str(REPO_ROOT))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-stale", action="store_true")
    parser.add_argument("--report", help="write JSON report here")
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    codemod = RenameCodemod(
        manifest,
        old=args.old,
        new=args.new,
        root=Path(args.root),
        allow_stale=args.allow_stale,
    )
    report = codemod.run(apply=args.apply)
    text = json.dumps(report, indent=1, sort_keys=True)
    if args.report:
        Path(args.report).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
