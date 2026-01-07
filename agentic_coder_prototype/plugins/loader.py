from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class PluginManifest:
    plugin_id: str
    version: str = ""
    name: str = ""
    description: str = ""
    root: str = ""
    source: str = "unknown"
    trusted: bool = False
    permissions: Dict[str, Any] = field(default_factory=dict)
    runtime: Dict[str, Any] = field(default_factory=dict)
    skills_paths: List[str] = field(default_factory=list)
    mcp_servers: List[Dict[str, Any]] = field(default_factory=list)


MANIFEST_FILENAME = "breadboard.plugin.json"

_PLUGIN_ID_RE = re.compile(r"^[a-zA-Z0-9_.:-]+$")


def validate_plugin_manifest_payload(payload: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    plugin_id = payload.get("id") or payload.get("plugin_id") or payload.get("pluginId")
    if not isinstance(plugin_id, str) or not plugin_id.strip():
        errors.append("Missing required field: id")
    else:
        pid = plugin_id.strip()
        if not _PLUGIN_ID_RE.match(pid):
            errors.append("Field id contains invalid characters")

    for key in ("version", "name", "description"):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"Missing required field: {key}")

    skills = payload.get("skills")
    if skills is not None:
        if isinstance(skills, list):
            if not all(isinstance(item, str) and item.strip() for item in skills):
                errors.append("skills must be a list of non-empty strings")
        elif isinstance(skills, dict):
            paths = skills.get("paths")
            if paths is not None and not (
                isinstance(paths, list) and all(isinstance(item, str) and item.strip() for item in paths)
            ):
                errors.append("skills.paths must be a list of non-empty strings")
        else:
            errors.append("skills must be an array or an object")

    permissions = payload.get("permissions")
    if permissions is not None and not isinstance(permissions, dict):
        errors.append("permissions must be an object")

    runtime = payload.get("runtime")
    if runtime is not None and not isinstance(runtime, dict):
        errors.append("runtime must be an object")

    mcp = payload.get("mcp")
    if mcp is not None:
        if not isinstance(mcp, dict):
            errors.append("mcp must be an object")
        else:
            servers = mcp.get("servers")
            if servers is not None:
                if not isinstance(servers, list) or not all(isinstance(item, dict) for item in servers):
                    errors.append("mcp.servers must be a list of objects")

    return errors


def _split_path_list(value: str) -> List[str]:
    parts = [item.strip() for item in value.split(os.pathsep)]
    return [p for p in parts if p]


def _default_plugin_roots(workspace: str) -> List[Tuple[str, str]]:
    ws = Path(workspace).resolve()
    roots: List[Tuple[str, str]] = []
    roots.append((str(ws / ".breadboard" / "plugins"), "workspace"))
    roots.append((str(Path.home() / ".breadboard" / "plugins"), "user"))
    env_dirs = os.environ.get("BREADBOARD_PLUGIN_DIRS", "").strip()
    for directory in _split_path_list(env_dirs) if env_dirs else []:
        roots.append((str(Path(directory).expanduser()), "env"))
    return roots


def _resolve_config_roots(plugins_cfg: Dict[str, Any], workspace: str) -> List[Tuple[str, str]]:
    search_paths = plugins_cfg.get("search_paths") or plugins_cfg.get("plugin_dirs") or plugins_cfg.get("dirs")
    if not isinstance(search_paths, list):
        return []
    ws = Path(workspace).resolve()
    roots: List[Tuple[str, str]] = []
    for entry in search_paths:
        if not isinstance(entry, str) or not entry.strip():
            continue
        raw = entry.strip()
        raw = raw.replace("{workspace}", str(ws))
        raw = raw.replace("{home}", str(Path.home()))
        roots.append((str(Path(raw).expanduser()), "config"))
    return roots


def _infer_trust(plugins_cfg: Dict[str, Any], source: str, root: str, plugin_id: str) -> bool:
    trusted_ids = plugins_cfg.get("trusted_ids")
    if isinstance(trusted_ids, list) and plugin_id in [str(x) for x in trusted_ids]:
        return True
    untrusted_ids = plugins_cfg.get("untrusted_ids")
    if isinstance(untrusted_ids, list) and plugin_id in [str(x) for x in untrusted_ids]:
        return False

    # Default trust policy: workspace plugins are untrusted unless explicitly allowed.
    if source == "workspace":
        return bool(plugins_cfg.get("trust_workspace", False))
    if source == "user":
        return bool(plugins_cfg.get("trust_user", True))
    if source in {"env", "config"}:
        return bool(plugins_cfg.get("trust_external", True))
    return False


def _load_manifest_file(path: Path) -> Dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _parse_skill_paths(payload: Dict[str, Any]) -> List[str]:
    skills = payload.get("skills")
    if isinstance(skills, list):
        return [str(item) for item in skills if isinstance(item, str) and item.strip()]
    if isinstance(skills, dict):
        paths = skills.get("paths")
        if isinstance(paths, list):
            return [str(item) for item in paths if isinstance(item, str) and item.strip()]
    return []


def _parse_mcp_servers(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    mcp = payload.get("mcp")
    if not isinstance(mcp, dict):
        return []
    servers = mcp.get("servers")
    if not isinstance(servers, list):
        return []
    out: List[Dict[str, Any]] = []
    for server in servers:
        if isinstance(server, dict):
            out.append(dict(server))
    return out


def discover_plugin_manifests(config: Dict[str, Any], workspace: str) -> List[PluginManifest]:
    # Plugins are intentionally disabled unless explicitly enabled/configured.
    plugins_cfg = (config or {}).get("plugins") if isinstance(config, dict) else None
    if not isinstance(plugins_cfg, dict) or not plugins_cfg.get("enabled"):
        return []

    roots: List[Tuple[str, str]] = []
    roots.extend(_resolve_config_roots(plugins_cfg, workspace))
    roots.extend(_default_plugin_roots(workspace))

    manifests: List[PluginManifest] = []
    seen: set[str] = set()

    for root_dir, source in roots:
        root_path = Path(root_dir).expanduser()
        if not root_path.exists() or not root_path.is_dir():
            continue
        try:
            candidates = list(root_path.rglob(MANIFEST_FILENAME))
        except Exception:
            continue
        for manifest_path in candidates:
            payload = _load_manifest_file(manifest_path)
            if not payload:
                continue
            errors = validate_plugin_manifest_payload(payload)
            if errors:
                continue
            plugin_id = payload.get("id") or payload.get("plugin_id") or payload.get("pluginId")
            if not isinstance(plugin_id, str) or not plugin_id.strip():
                continue
            plugin_id = plugin_id.strip()
            if plugin_id in seen:
                continue
            version = payload.get("version")
            name = payload.get("name")
            description = payload.get("description")
            if not (isinstance(version, str) and isinstance(name, str) and isinstance(description, str)):
                continue

            trusted = _infer_trust(plugins_cfg, source, str(root_path), plugin_id)
            permissions = payload.get("permissions") if isinstance(payload.get("permissions"), dict) else {}
            runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
            runtime = dict(runtime)
            runtime["trusted"] = trusted
            skills_paths = _parse_skill_paths(payload)
            mcp_servers = _parse_mcp_servers(payload)

            manifests.append(
                PluginManifest(
                    plugin_id=plugin_id,
                    version=version.strip(),
                    name=name.strip(),
                    description=description.strip(),
                    root=str(manifest_path.parent.resolve()),
                    source=source,
                    trusted=trusted,
                    permissions=dict(permissions or {}),
                    runtime=runtime,
                    skills_paths=skills_paths,
                    mcp_servers=mcp_servers,
                )
            )
            seen.add(plugin_id)

    return manifests


def plugin_snapshot(manifests: List[PluginManifest]) -> Dict[str, Any]:
    return {
        "plugins": [
            {
                "id": m.plugin_id,
                "version": m.version,
                "name": m.name,
                "source": m.source,
                "trusted": bool(m.trusted),
            }
            for m in manifests
        ]
    }
