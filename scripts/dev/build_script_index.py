#!/usr/bin/env python3
"""S-1/S-2: SCRIPT_INDEX.yaml generator + CI guard.

Classifies every tracked file under scripts/ by measured references:
  - CI workflows (.github/workflows/*.yml)
  - tests/** (imports and subprocess invocations)
  - pyproject.toml entry points
  - docs operational surfaces (getting-started, quickstarts, reference)
  - agent_configs/**
  - other scripts (transitive, one hop recorded)

Modes:
  (default)  regenerate scripts/SCRIPT_INDEX.yaml
  --check    fail (exit 1) when: a tracked live script lacks an index row, an
             index row points at a missing file, or the file set drifted from
             the committed index. Prints the live-script count (S-2 report).
"""

from __future__ import annotations

from collections.abc import Sequence
import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INDEX_PATH = REPO_ROOT / "scripts" / "SCRIPT_INDEX.yaml"

REFERENCE_ROOTS = [
    ".github/workflows",
    "tests",
    "pyproject.toml",
    "docs/getting-started",
    "docs/quickstarts",
    "docs/reference",
    "agent_configs",
    "scripts",
    "Makefile",
]

# Program prefixes whose root-level scripts are campaign artifacts by default
# (archived by S-3 unless referenced from a live surface).
CAMPAIGN_PATTERNS = (
    "atp_",
    "darwin_",
    "build_darwin_",
    "phase4_",
    "phase5_",
    "phase12_",
    "tmux_",
    "bless_",
    "aggregate_phase",
    "analyze_phase",
    "backfill_",
    "build_atp_",
    "verify_phase",
    "validate_tmux",
    "capture_tmux",
    "replay_phase",
    "run_tmux",
    "start_tmux",
)

GENERIC_BASENAMES = frozenset({"__init__.py"})


def _reference_pattern(full: str) -> re.Pattern[str]:
    """Match script references without treating generic basenames as calls."""
    name = Path(full).name
    relative = full.removeprefix("scripts/")
    module = relative.removesuffix(".py").replace("/", ".")
    terms = [full, f"scripts.{module}"]
    if name not in GENERIC_BASENAMES:
        terms.insert(0, name)
        return re.compile(
            r"(?<![\w/])(?:%s)(?![\w])" % "|".join(map(re.escape, terms))
        )

    package = relative.removesuffix("/__init__.py")
    package = "scripts" if package == relative else f"scripts.{package.replace('/', '.')}"
    direct = r"(?<![\w/])(?:%s)(?![\w])" % "|".join(map(re.escape, terms))
    package_import = rf"(?<![\w.])(?:from|import)\s+{re.escape(package)}(?=\s|[.;])"
    return re.compile(rf"(?:{direct}|{package_import})")



def tracked_scripts() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "scripts"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    return [p for p in out.stdout.splitlines() if p and not p.endswith((".pyc",))]


def reference_map(scripts: list[str]) -> dict[str, set[str]]:
    """Map script path -> set of referencing surfaces (measured)."""
    names = {p: (Path(p).name, p) for p in scripts}
    refs: dict[str, set[str]] = {p: set() for p in scripts}
    haystacks: list[tuple[str, str]] = []
    for root in REFERENCE_ROOTS:
        rp = REPO_ROOT / root
        if rp.is_file():
            haystacks.append((root, rp.read_text(encoding="utf-8", errors="ignore")))
            continue
        if not rp.is_dir():
            continue
        out = subprocess.run(
            ["git", "ls-files", root], cwd=REPO_ROOT, capture_output=True, text=True
        )
        for rel in out.stdout.splitlines():
            fp = REPO_ROOT / rel
            if (
                fp.suffix in {".png", ".zip", ".whl", ".pkl", ".pyc"}
                or not fp.is_file()
            ):
                continue
            try:
                haystacks.append((rel, fp.read_text(encoding="utf-8", errors="ignore")))
            except Exception:
                continue
    for p, (_, full) in names.items():
        pat = _reference_pattern(full)
        for rel, text in haystacks:
            if rel == full:
                continue
            if pat.search(text):
                refs[p].add(rel)
    return refs


def classify(path: str, refs: set[str]) -> str:
    external = {r for r in refs if not r.startswith("scripts/")}
    ci = any(r.startswith(".github/") for r in external)
    tests = any(r.startswith("tests/") for r in external)
    if ci or tests or "pyproject.toml" in external:
        return "live-wired"
    rel = path.removeprefix("scripts/")
    if rel.startswith(
        (
            "dev/",
            "release/",
            "ops/",
            "migration/",
            "e4_parity/",
            "rl_phase3/",
            "rl_phase1/",
            "_inventory/",
            "quality/",
            "authoring/",
            "research/",
        )
    ):
        return "live-owned"
    if external:
        return "live-referenced"
    # Scripts-internal references do not confer liveness: the referencing
    # script may itself be campaign residue. They are still recorded as callers.
    return "campaign"


def build_index() -> str:
    scripts = tracked_scripts()
    refs = reference_map(scripts)
    lines = [
        "# GENERATED by scripts/dev/build_script_index.py - edit fields, then keep",
        "# regenerable structure. CI runs --check (S-2): file-set drift fails.",
        "schema: bb.script_index.v1",
        "default_owner: kmccleary3301",
        "entries:",
    ]
    live = 0
    for p in sorted(scripts):
        cls = classify(p, refs[p])
        callers = sorted(refs[p])[:6]
        if cls != "campaign":
            live += 1
        expiry = (
            "archive-when-campaign-closes"
            if cls == "campaign"
            else "none (durable tooling)"
        )
        lines.append(f"  - path: {p}")
        lines.append(f"    class: {cls}")
        lines.append("    owner: kmccleary3301")
        if callers:
            lines.append(f"    callers: [{', '.join(callers)}]")
        else:
            lines.append(
                "    callers: []  # operator-invoked; purpose documented by module docstring"
            )
        lines.append(f"    expiry: {expiry}")
    lines.insert(4, f"live_script_count: {live}")
    return "\n".join(lines) + "\n"


def _indexed_paths(content: str) -> list[str]:
    """Return index paths in source order."""
    return re.findall(r"^  - path: (.+)$", content, re.M)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    content = build_index()
    live_match = re.search(r"^live_script_count: (\d+)$", content, re.M)
    if live_match is None:
        print("generated SCRIPT_INDEX is missing live_script_count", file=sys.stderr)
        return 1
    live = int(live_match.group(1))
    generated_paths = _indexed_paths(content)
    if len(generated_paths) != len(set(generated_paths)):
        print("generated SCRIPT_INDEX contains duplicate paths", file=sys.stderr)
        return 1
    if args.check:
        if not INDEX_PATH.is_file():
            print("SCRIPT_INDEX.yaml missing", file=sys.stderr)
            return 1
        committed = INDEX_PATH.read_text(encoding="utf-8")
        committed_paths = _indexed_paths(committed)
        if len(committed_paths) != len(set(committed_paths)):
            print("SCRIPT_INDEX contains duplicate paths", file=sys.stderr)
            return 1
        if len(committed_paths) != len(generated_paths):
            print(
                f"SCRIPT_INDEX count drift: committed={len(committed_paths)} "
                f"generated={len(generated_paths)}",
                file=sys.stderr,
            )
            return 1
        if committed != content:
            print(
                "SCRIPT_INDEX drift: committed content is not canonical",
                file=sys.stderr,
            )
            return 1
        print(
            f"script-index check: OK ({live} live scripts of {len(generated_paths)} tracked)"
        )
        return 0
    INDEX_PATH.write_text(content, encoding="utf-8")
    print(f"wrote {INDEX_PATH} ({live} live)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
