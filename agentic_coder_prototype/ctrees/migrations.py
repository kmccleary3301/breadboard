from __future__ import annotations

from typing import Any, Dict, Optional

from .schema import CTREE_SCHEMA_VERSION


def needs_migration(current_version: Optional[str], target_version: str = CTREE_SCHEMA_VERSION) -> bool:
    if not current_version:
        return True
    return str(current_version) != str(target_version)


def migrate_snapshot(
    snapshot: Dict[str, Any],
    *,
    target_version: str = CTREE_SCHEMA_VERSION,
) -> Dict[str, Any]:
    current_version = snapshot.get("schema_version")
    if not current_version:
        migrated = dict(snapshot)
        migrated["schema_version"] = target_version
        return migrated
    if not needs_migration(current_version, target_version):
        return snapshot
    raise NotImplementedError(f"C-Trees snapshot migration not implemented for {current_version} -> {target_version}")


def migrate_eventlog_header(
    header: Dict[str, Any],
    *,
    target_version: str = CTREE_SCHEMA_VERSION,
) -> Dict[str, Any]:
    current_version = header.get("schema_version")
    if not current_version:
        migrated = dict(header)
        migrated["schema_version"] = target_version
        return migrated
    if not needs_migration(current_version, target_version):
        return header
    raise NotImplementedError(f"C-Trees header migration not implemented for {current_version} -> {target_version}")
