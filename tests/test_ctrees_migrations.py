from __future__ import annotations

import pytest

from agentic_coder_prototype.ctrees.migrations import migrate_eventlog_header, migrate_snapshot, needs_migration
from agentic_coder_prototype.ctrees.schema import CTREE_SCHEMA_VERSION


def test_migration_noop_for_current_version() -> None:
    snapshot = {"schema_version": CTREE_SCHEMA_VERSION, "node_count": 1}
    header = {"schema_version": CTREE_SCHEMA_VERSION}

    assert needs_migration(snapshot.get("schema_version")) is False
    assert migrate_snapshot(dict(snapshot)) == snapshot
    assert migrate_eventlog_header(dict(header)) == header


def test_migration_sets_schema_version_when_missing() -> None:
    snapshot = {"node_count": 1}
    header = {}

    migrated_snapshot = migrate_snapshot(dict(snapshot))
    migrated_header = migrate_eventlog_header(dict(header))

    assert migrated_snapshot["schema_version"] == CTREE_SCHEMA_VERSION
    assert migrated_header["schema_version"] == CTREE_SCHEMA_VERSION


def test_migration_raises_for_unknown_version() -> None:
    snapshot = {"schema_version": "0.0"}
    with pytest.raises(NotImplementedError):
        migrate_snapshot(snapshot)
