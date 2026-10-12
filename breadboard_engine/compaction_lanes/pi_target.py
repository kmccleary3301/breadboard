"""pi target responsibility owner."""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]

def resolve_pi_cli(version: str = "0.73.1") -> Path:
    """Resolve only the explicitly configured package for this version."""
    env_var = {
        "0.73.1": "PI_CODING_AGENT_NODE_MODULES",
        "0.57.1": "PI057_CODING_AGENT_NODE_MODULES",
    }[version]
    base = os.environ.get(env_var)
    if not base:
        raise ValueError(f"{env_var} must be set for Pi {version}")
    package = Path(base) / "@mariozechner" / "pi-coding-agent"
    metadata = json.loads((package / "package.json").read_text(encoding="utf-8"))
    if metadata["version"] != version:
        raise ValueError(f"{env_var} contains Pi {metadata['version']}, expected {version}")
    cli = package / "dist" / "cli.js"
    if not cli.is_file():
        raise FileNotFoundError(cli)
    return cli

def resolve_target_harness_path(target_id: str) -> Path:
    """Resolve harness.yaml path from target_id strictly without fallbacks."""
    if "@" in target_id:
        target_name, ver = target_id.split("@", 1)
        if target_name.startswith("pi-r"):
            rev = target_name.split("-", 1)[1]
            cand = REPO_ROOT / "config" / "e4_targets" / "pi" / f"{ver}-{rev}" / "harness.yaml"
            if cand.is_file():
                return cand
        cand = REPO_ROOT / "config" / "e4_targets" / target_name / ver / "harness.yaml"
        if cand.is_file():
            return cand
    cand = REPO_ROOT / "config" / "e4_targets" / target_id / "harness.yaml"
    if cand.is_file():
        return cand
    raise FileNotFoundError(f"Target harness.yaml not found for target_id {target_id!r}")

def get_target_deviations(target_id: str) -> List[str]:
    """Extract policy.deviations ids from target harness.yaml."""
    harness_path = resolve_target_harness_path(target_id)
    import yaml  # type: ignore
    doc = yaml.safe_load(harness_path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"Invalid harness.yaml at {harness_path}")
    deviations = doc["policy"]["deviations"]
    if not isinstance(deviations, list) or any(
        not isinstance(dev, dict) or not isinstance(dev.get("id"), str)
        for dev in deviations
    ):
        raise ValueError(f"Missing or invalid policy.deviations in {harness_path}")
    return [dev["id"] for dev in deviations]

def load_target_document(target_id: str, filename: str) -> Dict[str, Any]:
    path = resolve_target_harness_path(target_id).with_name(filename)
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"Invalid target document at {path}")
    return doc

def get_target_overlay_argv(target_id: str) -> List[str]:
    doc = load_target_document(target_id, "target.json")
    argv = doc["overlay"]["argv"]
    if not isinstance(argv, list) or not argv or any(
        not isinstance(arg, str) or not arg for arg in argv
    ):
        raise ValueError(f"Invalid overlay.argv for {target_id}")
    return argv
