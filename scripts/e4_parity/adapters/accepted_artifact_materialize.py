from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Mapping

from agentic_coder_prototype.conformance.c4_chain import validate_c4_chain
from scripts.e4_parity import lane_inventory_utils as lane_inventory
from scripts.e4_parity import lane_runtime

ROOT = Path(__file__).resolve().parents[3]
WORKSPACE = ROOT.parent
CATALOG_PATH = ROOT / "docs/conformance/e4_artifact_catalog.json"
FREEZE_MANIFEST_PATH = ROOT / "config/e4_target_freeze_manifest.yaml"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _display(path: Path) -> str:
    return lane_runtime.display_path(path, repo_root=ROOT)


def _resolve(path_text: str) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    if path_text.startswith("docs_tmp/") or path_text.startswith(ROOT.name + "/"):
        return WORKSPACE / path
    return ROOT / path


def _catalog_entries_by_role() -> dict[str, Mapping[str, Any]]:
    catalog = _read_json(CATALOG_PATH)
    entries = catalog.get("entries") if isinstance(catalog, Mapping) else None
    if not isinstance(entries, list):
        raise ValueError("artifact catalog must contain entries list")
    result: dict[str, Mapping[str, Any]] = {}
    for entry in entries:
        if isinstance(entry, Mapping) and isinstance(entry.get("role_id"), str):
            result[entry["role_id"]] = entry
    return result


def _copy_catalog_artifacts(inventory_lane: Mapping[str, Any], out_dir: Path) -> list[dict[str, str]]:
    roles = inventory_lane.get("artifact_roles")
    if not isinstance(roles, Mapping):
        raise ValueError("inventory lane missing artifact_roles")
    catalog = _catalog_entries_by_role()
    copied: list[dict[str, str]] = []
    for role, role_id in sorted(roles.items()):
        if not isinstance(role_id, str):
            continue
        entry = catalog.get(role_id)
        if not entry or not isinstance(entry.get("path"), str):
            raise ValueError(f"catalog missing role_id {role_id!r}")
        source = _resolve(entry["path"])
        if not source.is_file():
            raise ValueError(f"accepted artifact missing: {entry['path']}")
        target = out_dir / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.append({"role": str(role), "path": str(entry["path"]), "sha256": lane_runtime.sha256_file(source)})
    return copied


def _copy_node_gate(inventory_lane: Mapping[str, Any], out_dir: Path) -> str:
    output = lane_inventory.ct_output(inventory_lane)
    source = _resolve(output)
    if not source.is_file():
        raise ValueError(f"node gate missing: {output}")
    target = out_dir / output
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return output


def capture(
    lane_def: Mapping[str, Any],
    inventory_lane: Mapping[str, Any] | None = None,
    *,
    promote_accepted: bool,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    if inventory_lane is None:
        raise ValueError("accepted_artifact_materialize requires inventory lane metadata")
    if not promote_accepted and out_dir is None:
        raise ValueError("accepted_artifact_materialize requires --out")
    claim_id = lane_inventory.claim_id(inventory_lane)
    support_claim = ROOT / "docs/conformance/support_claims" / f"{claim_id}.json"
    evidence_manifest = ROOT / "docs/conformance/support_claims" / f"{claim_id.replace('_support_claim', '_evidence_manifest')}.json"
    report = validate_c4_chain(
        repo_root=ROOT,
        freeze_manifest_path=FREEZE_MANIFEST_PATH,
        config_id=str(lane_def["config_id"]),
        support_claim_path=support_claim,
        evidence_manifest_path=evidence_manifest,
        rerun_comparators=True,
        comparator_registry_path=ROOT / "conformance/comparators/registry.json",
        enforce_catalog_binding=False,
    )
    if not report.get("ok"):
        raise ValueError(f"accepted C4 chain is not valid: {report.get('errors')}")
    if promote_accepted:
        copied: list[dict[str, str]] = []
        node_gate = lane_inventory.ct_output(inventory_lane)
    else:
        copied = _copy_catalog_artifacts(inventory_lane, out_dir)
        node_gate = _copy_node_gate(inventory_lane, out_dir)
    return {
        "ok": True,
        "lane_id": lane_def.get("lane_id"),
        "config_id": lane_def.get("config_id"),
        "claim_id": claim_id,
        "node_gate": node_gate,
        "copied_artifacts": copied,
    }


capture.supports_scratch_out_dir = True
