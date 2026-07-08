from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from breadboard.rl.phase2.service import ArtifactRecord, ResourceCaps, RunStatus, RunSubmission, StreamEvent
from breadboard.rl.phase3.security_enforcement import enforce_workspace_path


class SQLiteRLRunStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            create table if not exists rl_runs(
              run_id text primary key,
              tenant_id text not null,
              workspace_id text not null,
              env_package_ref text not null,
              target_run_id text not null,
              state text not null,
              cancellation_state text not null,
              created_at real not null,
              updated_at real not null,
              reason text not null,
              resource_caps_json text not null
            );
            create table if not exists rl_events(
              run_id text not null,
              sequence integer not null,
              event_type text not null,
              state text not null,
              message text not null,
              target_run_id text not null,
              payload_json text not null,
              primary key(run_id, sequence)
            );
            create table if not exists rl_artifacts(
              run_id text not null,
              artifact_id text not null,
              relative_path text not null,
              sha256 text not null,
              bytes integer not null,
              egress_allowed integer not null,
              primary key(run_id, artifact_id)
            );
            """
        )
        self._conn.commit()

    def create_run(self, submission: RunSubmission, caps: ResourceCaps, *, state: str, reason: str = "") -> None:
        now = time.time()
        with self._conn:
            self._conn.execute(
                "insert into rl_runs values(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    submission.run_id,
                    submission.tenant_id,
                    submission.workspace_id,
                    submission.env_package_ref,
                    submission.target_run_id,
                    state,
                    "not_cancelled",
                    now,
                    now,
                    reason,
                    json.dumps(caps.to_dict(), sort_keys=True),
                ),
            )

    def update_state(self, run_id: str, *, state: str, cancellation_state: str | None = None, reason: str = "") -> None:
        row = self.get_run_row(run_id)
        if row is None:
            raise KeyError(run_id)
        with self._conn:
            self._conn.execute(
                "update rl_runs set state=?, cancellation_state=?, reason=?, updated_at=? where run_id=?",
                (state, cancellation_state or row["cancellation_state"], reason, time.time(), run_id),
            )

    def get_run_row(self, run_id: str) -> sqlite3.Row | None:
        return self._conn.execute("select * from rl_runs where run_id=?", (run_id,)).fetchone()

    def status(self, run_id: str) -> RunStatus:
        row = self.get_run_row(run_id)
        if row is None:
            raise KeyError(run_id)
        return RunStatus(
            run_id=row["run_id"],
            state=row["state"],
            target_run_id=row["target_run_id"],
            accepted=row["state"] != "rejected",
            cancellation_state=row["cancellation_state"],
            reason=row["reason"],
        )

    def assert_tenant(self, run_id: str, *, tenant_id: str, workspace_id: str | None = None) -> sqlite3.Row:
        row = self.get_run_row(run_id)
        if row is None:
            raise KeyError(run_id)
        if row["tenant_id"] != tenant_id or (workspace_id is not None and row["workspace_id"] != workspace_id):
            raise PermissionError("tenant mismatch")
        return row

    def append_event(self, run_id: str, *, event_type: str, state: str, message: str, target_run_id: str, payload: dict[str, Any] | None = None) -> StreamEvent:
        last = self._conn.execute("select max(sequence) as seq from rl_events where run_id=?", (run_id,)).fetchone()["seq"]
        sequence = int(last or 0) + 1
        event = StreamEvent(sequence, run_id, event_type, state, message, target_run_id, payload or {})
        with self._conn:
            self._conn.execute(
                "insert into rl_events values(?,?,?,?,?,?,?)",
                (run_id, sequence, event_type, state, message, target_run_id, json.dumps(event.payload, sort_keys=True)),
            )
        return event

    def events_since(self, run_id: str, sequence: int = 0) -> list[StreamEvent]:
        rows = self._conn.execute(
            "select * from rl_events where run_id=? and sequence>? order by sequence", (run_id, sequence)
        ).fetchall()
        return [
            StreamEvent(row["sequence"], row["run_id"], row["event_type"], row["state"], row["message"], row["target_run_id"], json.loads(row["payload_json"]))
            for row in rows
        ]

    def add_artifact(self, record: ArtifactRecord, *, tenant_id: str, workspace_id: str) -> None:
        enforce_workspace_path(record.relative_path, tenant_id=tenant_id, workspace_id=workspace_id)
        with self._conn:
            self._conn.execute(
                "insert or replace into rl_artifacts values(?,?,?,?,?,?)",
                (record.run_id, record.artifact_id, record.relative_path, record.sha256, record.bytes, int(record.egress_allowed)),
            )

    def artifacts(self, run_id: str) -> list[ArtifactRecord]:
        rows = self._conn.execute("select * from rl_artifacts where run_id=? order by artifact_id", (run_id,)).fetchall()
        return [ArtifactRecord(row["run_id"], row["artifact_id"], row["relative_path"], row["sha256"], int(row["bytes"]), bool(row["egress_allowed"])) for row in rows]

    def artifact(self, run_id: str, artifact_id: str) -> ArtifactRecord:
        row = self._conn.execute("select * from rl_artifacts where run_id=? and artifact_id=?", (run_id, artifact_id)).fetchone()
        if row is None:
            raise KeyError(artifact_id)
        return ArtifactRecord(row["run_id"], row["artifact_id"], row["relative_path"], row["sha256"], int(row["bytes"]), bool(row["egress_allowed"]))
