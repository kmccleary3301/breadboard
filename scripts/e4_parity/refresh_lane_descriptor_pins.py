#!/usr/bin/env python3
"""Refresh stale literal sha256/bytes pins inside a lane descriptor YAML.

Restores of preserved raw evidence legitimately change accepted bytes; lane
descriptors that carry literal hash pins (pre auto-bind architecture) must be
re-pinned through this sanctioned route, then the lane recaptured. Rendered
artifacts pin each other, so we iterate: re-pin -> render+promote -> fold the
rendered hashes back into the descriptor -> repeat until stable.

Pin forms handled, each strictly line- or block-scoped (no window heuristics):
1. Map entries:  "<path>": "sha256:<hex>"            (single line)
2. Row dicts:    { ... "path": "<p>" ... "sha256": ... "bytes": N ... }
   parsed by brace matching from the row's opening line.
"""
from __future__ import annotations

import argparse
try:
    from scripts.e4_parity import compile_lane_lock
    from scripts.e4_parity.validators import hash_utils as _hash_utils
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    import compile_lane_lock
    from validators import hash_utils as _hash_utils
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAP_RE = re.compile(r'^(\s*)"([^"]+)":\s*"(sha256:[0-9a-f]{64})"(,?)\s*$')
PATH_RE = re.compile(r'^\s*"path":\s*"([^"]+)"(,?)\s*$')
SHA_RE = re.compile(r'^(\s*"sha256":\s*")(sha256:[0-9a-f]{64})("(?:,?))\s*$')
BYTES_RE = re.compile(r'^(\s*"bytes":\s*)(\d+)(,?)\s*$')
REF_RE = re.compile(r'([a-zA-Z0-9_./@-]+)#(sha256:[0-9a-f]{64})')
FREEZE_REF_RE = re.compile(r'config/e4_target_freeze_manifest\.yaml#([A-Za-z0-9_.-]+)#(sha256:[0-9a-f]{64})')
CATALOG_HASH_RE = re.compile(r'^(\s*"catalog_hash":\s*")(sha256:[0-9a-f]{64})("(?:,?))\s*$')
CATALOG_REV_RE = re.compile(r'^(\s*"catalog_revision":\s*)(\d+)(,?)\s*$')


FREEZE_MANIFEST_PATH = "config/e4_target_freeze_manifest.yaml"
CONFIG_ID_RE = re.compile(r'"config_id":\s*"([^"]+)"')


def _sha(path: Path) -> str:
    return _hash_utils.sha256_file(path)


def _resolve_pin_path(path_str: str) -> Path | None:
    """Resolve a descriptor pin path: repo root first, workspace fallback for docs_tmp/."""
    physical = ROOT / path_str
    if physical.is_file():
        return physical
    if path_str.startswith("docs_tmp/"):
        workspace = ROOT.parent / path_str
        if workspace.is_file():
            return workspace
    return None


def _block_bounds(lines: list[str], idx: int) -> tuple[int, int] | None:
    """Given a line inside a row dict, find the enclosing { } bounds by brace depth."""
    depth = 0
    start = None
    for i in range(idx, -1, -1):
        opens = lines[i].count("{")
        closes = lines[i].count("}")
        depth += opens - closes
        if depth > 0:
            start = i
            break
    if start is None:
        return None
    depth = 0
    for j in range(start, len(lines)):
        depth += lines[j].count("{") - lines[j].count("}")
        if depth == 0:
            return start, j
    return None


def _freeze_row_hash(config_id: str) -> str | None:
    sys.path.insert(0, str(ROOT / "scripts" / "e4_parity"))
    try:
        from adapters.oh_my_pi_compiler_capture import _freeze_row_hash as row_hash
        return row_hash(config_id)
    except Exception:
        return None


def _live_catalog() -> tuple[str | None, int | None]:
    catalog_path = ROOT / "docs" / "conformance" / "e4_artifact_catalog.json"
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None
    integrity = catalog.get("integrity")
    stable = integrity.get("stable_entries_hash") if isinstance(integrity, dict) else None
    revision = catalog.get("revision")
    return (stable if isinstance(stable, str) else None,
            revision if isinstance(revision, int) else None)


def refresh_pins_once(yaml_path: Path, prefixes: tuple[str, ...]) -> list[str]:
    """One structured pass: pin every matching path row/map entry to disk truth."""
    config_id_match = CONFIG_ID_RE.search(yaml_path.read_text(encoding="utf-8"))
    lane_config_id = config_id_match.group(1) if config_id_match else None
    lane_freeze_row = _freeze_row_hash(lane_config_id) if lane_config_id else None
    lines = yaml_path.read_text(encoding="utf-8").splitlines()
    stale: set[str] = set()
    # Pass 1: single-line map entries.
    for i, line in enumerate(lines):
        m = MAP_RE.match(line)
        if not m:
            continue
        key = m.group(2)
        if key == "freeze_manifest_row":
            if lane_freeze_row and lane_freeze_row != m.group(3):
                lines[i] = f'{m.group(1)}"{key}": "{lane_freeze_row}"{m.group(4)}'
                stale.add("freeze_manifest_row")
            continue
        if not key.startswith(prefixes):
            continue
        physical = _resolve_pin_path(key)
        if physical is None:
            continue
        actual = _sha(physical)
        if actual != m.group(3):
            lines[i] = f'{m.group(1)}"{key}": "{actual}"{m.group(4)}'
            stale.add(key)
    # Pass 2: row dicts, strictly within the row's brace block.
    for i, line in enumerate(lines):
        p = PATH_RE.match(line)
        if not p or not p.group(1).startswith(prefixes):
            continue
        bounds = _block_bounds(lines, i)
        if bounds is None:
            continue
        if p.group(1) == FREEZE_MANIFEST_PATH:
            # Freeze rows pin the lane's config row hash, not the whole file.
            if lane_freeze_row is None:
                continue
            start, end = bounds
            for j in range(start, end + 1):
                s = SHA_RE.match(lines[j])
                if s and s.group(2) != lane_freeze_row:
                    lines[j] = f"{s.group(1)}{lane_freeze_row}{s.group(3)}"
                    stale.add(f"freeze_row:{p.group(1)}")
            continue
        physical = _resolve_pin_path(p.group(1))
        if physical is None:
            continue
        start, end = bounds
        actual = _sha(physical)
        size = physical.stat().st_size
        for j in range(start, end + 1):
            s = SHA_RE.match(lines[j])
            if s and s.group(2) != actual:
                lines[j] = f"{s.group(1)}{actual}{s.group(3)}"
                stale.add(p.group(1))
            b = BYTES_RE.match(lines[j])
            if b and int(b.group(2)) != size:
                lines[j] = f"{b.group(1)}{size}{b.group(3)}"
                stale.add(p.group(1))
    # Pass 3: embedded "<path>#sha256:<hex>" refs (whole-file pins), freeze row
    # refs (row-hash pins), and catalog_binding constants (live catalog truth).
    freeze_cache: dict[str, str | None] = {}
    live_stable, live_revision = _live_catalog()

    def _ref_sub(match: re.Match[str]) -> str:
        path_str, pinned = match.group(1), match.group(2)
        physical = ROOT / path_str
        if not physical.is_file():
            return match.group(0)
        actual = _sha(physical)
        if actual != pinned:
            stale.add(path_str)
        return f"{path_str}#{actual}"

    def _freeze_sub(match: re.Match[str]) -> str:
        config_id, pinned = match.group(1), match.group(2)
        if config_id not in freeze_cache:
            freeze_cache[config_id] = _freeze_row_hash(config_id)
        actual = freeze_cache[config_id]
        if actual is None:
            return match.group(0)
        if actual != pinned:
            stale.add(f"freeze_ref:{config_id}")
        return f"config/e4_target_freeze_manifest.yaml#{config_id}#{actual}"

    for i, line in enumerate(lines):
        if FREEZE_REF_RE.search(line):
            lines[i] = FREEZE_REF_RE.sub(_freeze_sub, lines[i])
        elif REF_RE.search(line):
            lines[i] = REF_RE.sub(_ref_sub, lines[i])
        ch = CATALOG_HASH_RE.match(lines[i])
        if ch and live_stable and ch.group(2) != live_stable:
            lines[i] = f"{ch.group(1)}{live_stable}{ch.group(3)}"
            stale.add("catalog_binding.catalog_hash")
        cr = CATALOG_REV_RE.match(lines[i])
        if cr and live_revision is not None and int(cr.group(2)) != live_revision:
            lines[i] = f"{cr.group(1)}{live_revision}{cr.group(3)}"
            stale.add("catalog_binding.catalog_revision")
    yaml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sorted(stale)


def refresh_migrated_lane(
    manifest_path: Path,
    *,
    check: bool = False,
) -> int:
    """Regenerate only machine-owned outputs for one migrated lane."""
    manifest_path = Path(manifest_path)
    if not manifest_path.name.endswith(".manifest.yaml"):
        raise ValueError("migrated refresh target must end with .manifest.yaml")
    if not manifest_path.is_file():
        raise ValueError(f"manifest does not exist: {manifest_path}")
    argv = ["compile", str(manifest_path)]
    if check:
        argv.append("--check")
    return compile_lane_lock.main(argv)


def refresh(lane_id: str, max_iterations: int = 6) -> dict[str, object]:
    yaml_path = ROOT / "config" / "e4_lanes" / f"{lane_id}.yaml"
    prefixes = (
        f"docs/conformance/e4_target_support/{lane_id}/",
        f"docs/conformance/support_claims/{lane_id}_",
        FREEZE_MANIFEST_PATH,
        "docs_tmp/phase_15/",
    )
    scratch = ROOT / "tmp" / "e4_regen_capture" / lane_id
    history: list[dict[str, object]] = []
    for iteration in range(max_iterations):
        stale = refresh_pins_once(yaml_path, prefixes)
        entry: dict[str, object] = {"iteration": iteration, "stale_pins": stale}
        history.append(entry)
        if not stale and iteration > 0:
            break
        if scratch.exists():
            shutil.rmtree(scratch)
        run = subprocess.run(
            [sys.executable, "scripts/e4_parity/run_lane.py", "--lane", lane_id, "--stage", "capture",
             "--promote-accepted", "--defer-promotion-refresh", "--json"],
            cwd=ROOT, capture_output=True, text=True,
        )
        try:
            ok = bool(json.loads(run.stdout).get("ok"))
        except json.JSONDecodeError:
            ok = False
        entry["capture_ok"] = ok
        if not ok:
            entry["capture_tail"] = (run.stdout + run.stderr)[-2000:]
            break
        if not stale:
            break
    return {"lane_id": lane_id, "yaml": str(yaml_path.relative_to(ROOT)), "history": history}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", required=True)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()
    report = refresh(args.lane)
    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    last = report["history"][-1]
    return 0 if not last["stale_pins"] or last.get("capture_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
