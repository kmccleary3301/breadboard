#!/usr/bin/env python3
"""Generate trusted pre-rename compatibility fixtures (rename plan R-0B).

Run ONCE on the pre-rename tree; outputs are committed and then asserted by
``tests/rename_compat/``. Post-rename, the tests prove class-D/E preservation
and pickle/dotted-path compatibility against these recorded values.

Outputs (under tests/rename_compat/fixtures/):
- recorded_p3_provenance.json   (R-0B4): per-config byte sha256, freeze-row
  hash recomputed with the freeze protocol's own canonicalization and
  cross-checked against the accepted support-claim ``freeze_ref``.
- recorded_pinned_artifacts.json (R-0B8): exact sha256 for code-pinned
  artifacts plus recursive member digests of ``e4_immutable_inputs.v1.zip``.
- recorded_preserve_byte_digests.json (R-0B5): digest of every repo-frozen
  file whose rename-audit occurrences are all preserve-byte.
- ``*.pkl`` (R-0B6): pickles produced on the pre-rename tree, including a
  by-reference function pickle (stores the dotted module path - the exact
  thing the rename breaks without the alias layer).
"""

from __future__ import annotations

import hashlib
import json
import pickle
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from scripts.e4_parity import lane_runtime  # noqa: E402
from agentic_coder_prototype.compat import legacy_names  # noqa: E402
from agentic_coder_prototype.security.redaction import RedactionProblem  # noqa: E402
from agentic_coder_prototype.compilation.primitive_records import (  # noqa: E402
    PrimitiveCompileError,
)
from agentic_coder_prototype.compilation.effective_config_graph import (  # noqa: E402
    graph_content_hash,
)

OUT = ROOT / "tests" / "rename_compat" / "fixtures"
FREEZE_MANIFEST = ROOT / "config" / "e4_target_freeze_manifest.yaml"
PINNED = (
    "contracts/public/frozen_public_surface.v1.json",
    "docs/plans/phase_20_right_shape/SCOUT_FACTS.json",
    "config/e4_lanes/evidence_inputs/e4_immutable_inputs.v1.manifest.json",
)
ARCHIVE = "config/e4_lanes/evidence_inputs/e4_immutable_inputs.v1.zip"
CLAIM_DIR = ROOT / "docs" / "conformance" / "support_claims"


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def row_hash(row_id: str, row: dict) -> str:
    return lane_runtime.sha256_text(
        lane_runtime.canonical_json({"row_id": row_id, "row": row}, separators_style="compact")
    )


def p3_provenance() -> dict:
    manifest = yaml.safe_load(FREEZE_MANIFEST.read_text(encoding="utf-8"))
    records = {}
    for config_id, row in sorted(manifest["e4_configs"].items()):
        if "_p3_" not in config_id:
            continue
        config_path = row["config_path"]
        config = yaml.safe_load((ROOT / config_path).read_text(encoding="utf-8"))
        acceptance = config.get("p3_acceptance") or {}
        if "helper_module" in acceptance:
            dotted_ref, ref_kind = acceptance["helper_module"], "module"
        else:
            dotted_ref, ref_kind = config["compiler"], "callable"
        recomputed = row_hash(config_id, row)
        claim_path = CLAIM_DIR / f"{config_id}_c4_support_claim.json"
        claim_ref = None
        if claim_path.exists():
            # Cross-check against the currently accepted claim; archived v1
            # claims may predate freeze-manifest key backfills (P20 M1).
            claim = json.loads(claim_path.read_text(encoding="utf-8"))
            claim_ref = claim["freeze_ref"]
            claimed = claim_ref.rsplit("#", 1)[-1].removeprefix("sha256:")
            if recomputed.removeprefix("sha256:") != claimed:
                raise SystemExit(
                    f"freeze-row hash mismatch for {config_id}: {recomputed} != claim {claimed}"
                )
        # Measured baseline: p3_7's frozen `compiler:` ref is already dangling
        # on the pre-rename tree (compile_memory_work_bundle was superseded by
        # validate_memory_work_evidence). Record what IS; tests assert no drift.
        try:
            legacy_names.resolve_callable(dotted_ref)
        except (ModuleNotFoundError, AttributeError):
            dotted_ref_resolves = False
        else:
            dotted_ref_resolves = True
        records[config_id] = {
            "config_path": config_path,
            "config_sha256": sha256_file(ROOT / config_path),
            "dotted_ref": dotted_ref,
            "ref_kind": ref_kind,
            "dotted_ref_resolves": dotted_ref_resolves,
            "freeze_row_hash": recomputed,
            "support_claim_freeze_ref": claim_ref,
        }
    if len(records) != 8:
        raise SystemExit(f"expected 8 P3 configs, found {len(records)}")
    return records


def pinned_artifacts() -> dict:
    record = {path: sha256_file(ROOT / path) for path in PINNED}
    archive_path = ROOT / ARCHIVE
    members = {}
    with zipfile.ZipFile(archive_path) as zf:
        for name in sorted(zf.namelist()):
            members[name] = "sha256:" + hashlib.sha256(zf.read(name)).hexdigest()
    record[ARCHIVE] = {"sha256": sha256_file(archive_path), "members": members}
    return record


def preserve_byte_digests() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "manifest.json"
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/dev/audit_engine_rename.py"), "--out", str(out)],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        if result.returncode != 0:
            raise SystemExit(f"rename audit failed:\n{result.stdout}\n{result.stderr}")
        manifest = json.loads(out.read_text(encoding="utf-8"))
    digests = {}
    for entry in manifest["files"]:
        dispositions = {occ["disposition"] for occ in entry["occurrences"]}
        if dispositions == {"preserve-byte"} and not entry["path"].startswith("docs_tmp/"):
            digests[entry["path"]] = f"sha256:{entry['sha256']}"
    return digests


def write_pickles() -> dict:
    fixtures = {
        "redaction_problem.pkl": RedactionProblem("secret_key", "$.k", "fixture"),
        # By-reference pickles: the stream stores the dotted module path -
        # the exact thing the rename breaks without the alias layer.
        "class_ref.pkl": PrimitiveCompileError,
        "function_ref.pkl": graph_content_hash,
    }
    meta = {}
    for name, obj in fixtures.items():
        payload = pickle.dumps(obj, protocol=4)
        (OUT / name).write_bytes(payload)
        meta[name] = {
            "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "type": f"{type(obj).__module__}.{type(obj).__qualname__}",
        }
    return meta


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    p3 = p3_provenance()
    (OUT / "recorded_p3_provenance.json").write_text(
        json.dumps(p3, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    pinned = pinned_artifacts()
    (OUT / "recorded_pinned_artifacts.json").write_text(
        json.dumps(pinned, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    digests = preserve_byte_digests()
    (OUT / "recorded_preserve_byte_digests.json").write_text(
        json.dumps(digests, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    pickles = write_pickles()
    (OUT / "pickle_fixtures.json").write_text(
        json.dumps(pickles, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    archive_members = len(pinned[ARCHIVE]["members"])
    print(
        f"p3 configs: {len(p3)} | pinned: {len(PINNED)} + zip({archive_members} members) | "
        f"preserve-byte digests: {len(digests)} | pickles: {len(pickles)}"
    )


if __name__ == "__main__":
    main()
