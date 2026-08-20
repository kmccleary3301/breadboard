"""
Reward metrics recorder for Phase 5 optimization work.

Tracks per-turn reward signals such as SVS/ACS/CPS/PAS/HMR/etc. and produces
structured payloads suitable for telemetry logging or downstream analysis.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


DEFAULT_REWARD_METRIC_NAMES: List[str] = [
    "SVS",
    "ACS",
    "CPS",
    "PAS",
    "HMR",
    "LED",
    "SBS",
    "TPF_DELTA",
    "TE",
    "LE",
    "TOE",
    "SPA",
]


def _sanitize_metric_name(name: str) -> str:
    if not isinstance(name, str):
        raise TypeError("metric name must be a string")
    normalized = name.strip().upper()
    if not normalized:
        raise ValueError("metric name cannot be empty")
    return normalized


def _coerce_metric_value(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    raise TypeError(f"metric value must be numeric or None, got {type(value)!r}")


@dataclass
class RewardMetricsRecord:
    """Single per-turn reward metrics snapshot."""

    turn_index: int
    metrics: Dict[str, Optional[float]] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        payload = {
            "turn": self.turn_index,
            "metrics": {k: v for k, v in self.metrics.items() if v is not None},
        }
        if self.metadata:
            payload["meta"] = dict(self.metadata)
        return payload


class RewardMetricsRecorder:
    """
    Collects reward metrics per turn and exposes structured snapshots.

    Metric names default to the Phase 5 optimization set but can be overridden.
    """

    def __init__(self, metric_names: Optional[Iterable[str]] = None) -> None:
        names = metric_names or DEFAULT_REWARD_METRIC_NAMES
        normalized = [_sanitize_metric_name(n) for n in names]
        self._metric_names: List[str] = normalized
        self._records: Dict[int, RewardMetricsRecord] = {}

    @property
    def metric_names(self) -> List[str]:
        return list(self._metric_names)

    def record_turn(
        self,
        turn_index: int,
        metrics: Optional[Dict[str, Any]] = None,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        overwrite: bool = True,
    ) -> RewardMetricsRecord:
        if not isinstance(turn_index, int) or turn_index < 0:
            raise ValueError("turn_index must be a non-negative integer")
        record = self._records.get(turn_index)
        if record is None:
            record = RewardMetricsRecord(turn_index=turn_index)
            self._records[turn_index] = record
        elif overwrite:
            record.metrics.clear()
            record.metadata.clear()

        if metrics:
            for name, value in metrics.items():
                self.set_metric(turn_index, name, value)
        if metadata:
            record.metadata.update(metadata)
        return record

    def set_metric(self, turn_index: int, name: str, value: Any) -> None:
        normalized_name = _sanitize_metric_name(name)
        if normalized_name not in self._metric_names:
            raise KeyError(f"unknown reward metric '{normalized_name}'")
        record = self._records.get(turn_index)
        if record is None:
            record = RewardMetricsRecord(turn_index=turn_index)
            self._records[turn_index] = record
        record.metrics[normalized_name] = _coerce_metric_value(value)

    def add_metric_if_absent(self, turn_index: int, name: str, value: Any) -> None:
        normalized_name = _sanitize_metric_name(name)
        record = self._records.get(turn_index)
        if record and normalized_name in record.metrics:
            return
        self.set_metric(turn_index, name, value)

    def add_metadata(self, turn_index: int, **metadata: Any) -> None:
        record = self._records.get(turn_index)
        if record is None:
            record = RewardMetricsRecord(turn_index=turn_index)
            self._records[turn_index] = record
        record.metadata.update(metadata)

    def get_record(self, turn_index: int) -> Optional[RewardMetricsRecord]:
        return self._records.get(turn_index)

    def iter_records(self) -> List[RewardMetricsRecord]:
        return [self._records[idx] for idx in sorted(self._records)]

    def as_payload(self) -> Dict[str, Any]:
        return {
            "metric_names": list(self._metric_names),
            "turns": [record.as_dict() for record in self.iter_records()],
        }

    def clear(self) -> None:
        self._records.clear()


class RewardMetricsSQLiteWriter:
    """Persist reward metrics into a lightweight SQLite store."""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reward_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    turn INTEGER,
                    metric TEXT,
                    value REAL,
                    metadata TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reward_metrics_run
                ON reward_metrics(run_id, turn)
                """
            )
            conn.commit()

    def write(self, run_id: str, payload: Dict[str, Any]) -> None:
        turns = payload.get("turns") or []
        if not turns:
            return
        rows: List[tuple] = []
        for turn_payload in turns:
            turn_index = turn_payload.get("turn")
            if turn_index is None:
                continue
            metrics = turn_payload.get("metrics") or {}
            metadata = turn_payload.get("meta") or {}
            metadata_text = json.dumps(metadata) if metadata else None
            for metric_name, metric_value in metrics.items():
                try:
                    metric_float = float(metric_value)
                except (TypeError, ValueError):
                    continue
                rows.append((run_id, int(turn_index), metric_name, metric_float, metadata_text))
        if not rows:
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO reward_metrics (run_id, turn, metric, value, metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()


class TodoMetricsSQLiteWriter:
    """Persist aggregate todo metrics for completed runs."""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS todo_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    total INTEGER,
                    open INTEGER,
                    done INTEGER,
                    canceled INTEGER,
                    blocked INTEGER,
                    journal_events INTEGER,
                    recorded_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_todo_metrics_run
                ON todo_metrics(run_id)
                """
            )
            conn.commit()

    def write(self, run_id: str, metrics: Dict[str, Any]) -> None:
        if not metrics:
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO todo_metrics
                    (run_id, total, open, done, canceled, blocked, journal_events, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    int(metrics.get("total", 0) or 0),
                    int(metrics.get("open", 0) or 0),
                    int(metrics.get("done", 0) or 0),
                    int(metrics.get("canceled", 0) or 0),
                    int(metrics.get("blocked", 0) or 0),
                    int(metrics.get("journal_events", 0) or 0),
                    datetime.utcnow().isoformat() + "Z",
                ),
            )
            conn.commit()

