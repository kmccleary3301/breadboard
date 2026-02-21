from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .anchor_store import FileSystemAnchorStore
from .contracts import (
    EVOLAKE_CAMPAIGN_API_VERSION,
    EVOLAKE_CAMPAIGN_RECORD_SCHEMA_ID,
    EVOLAKE_CHECKPOINT_SCHEMA_ID,
    EVOLAKE_REPLAY_MANIFEST_SCHEMA_ID,
    validate_campaign_record,
    validate_checkpoint,
    validate_replay_manifest,
)


def _campaign_root() -> Path:
    root = os.environ.get("EVOLAKE_CAMPAIGN_ROOT") or os.environ.get("BREADBOARD_EVOLAKE_CAMPAIGN_ROOT")
    return Path(root or "artifacts/evolake_campaigns")


def _checkpoint_root() -> Path:
    root = os.environ.get("EVOLAKE_CHECKPOINT_ROOT") or os.environ.get("BREADBOARD_EVOLAKE_CHECKPOINT_ROOT")
    return Path(root or "artifacts/evolake_checkpoints")


def _replay_root() -> Path:
    root = os.environ.get("EVOLAKE_REPLAY_ROOT") or os.environ.get("BREADBOARD_EVOLAKE_REPLAY_ROOT")
    return Path(root or "artifacts/evolake_replay")


def _now() -> float:
    return time.time()


def _build_campaign_record(payload: Dict[str, Any]) -> Dict[str, Any]:
    campaign_id = payload.get("campaign_id") or uuid.uuid4().hex
    return {
        "schema": EVOLAKE_CAMPAIGN_RECORD_SCHEMA_ID,
        "api_version": EVOLAKE_CAMPAIGN_API_VERSION,
        "campaign_id": campaign_id,
        "submitted_at": _now(),
        "payload": payload,
    }


def normalize_tenant_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        raw = (os.environ.get("EVOLAKE_DEFAULT_TENANT") or "default").strip() or "default"
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in raw)
    return safe[:120] or "default"


def _anchor_root() -> Path:
    root = os.environ.get("EVOLAKE_ANCHOR_ROOT") or os.environ.get("BREADBOARD_EVOLAKE_ANCHOR_ROOT")
    return Path(root or "artifacts/evolake_anchors")


def persist_campaign_record_scoped(record: Dict[str, Any], tenant_id: str) -> Path:
    normalized = dict(record)
    normalized.setdefault("schema", EVOLAKE_CAMPAIGN_RECORD_SCHEMA_ID)
    normalized.setdefault("api_version", EVOLAKE_CAMPAIGN_API_VERSION)
    normalized.setdefault("campaign_id", uuid.uuid4().hex)
    normalized.setdefault("submitted_at", _now())
    normalized.setdefault("payload", {})
    normalized.setdefault("tenant_id", normalize_tenant_id(tenant_id))
    try:
        rounds = int(normalized.get("rounds", 1))
    except Exception:
        rounds = 1
    normalized["rounds"] = rounds if rounds > 0 else 1

    issues = validate_campaign_record(normalized)
    if issues:
        raise ValueError(
            "campaign record schema validation failed: "
            + "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
        )
    root = _campaign_root() / normalize_tenant_id(tenant_id)
    root.mkdir(parents=True, exist_ok=True)
    out_path = root / f"{normalized['campaign_id']}.json"
    out_path.write_text(json.dumps(normalized, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return out_path


def append_anchor_records(tenant_id: str, campaign_id: str, records: Iterable[Dict[str, Any]]) -> Path:
    store = FileSystemAnchorStore(_anchor_root())
    return store.append_rows(tenant_id=tenant_id, campaign_id=campaign_id, rows=records)


def list_anchor_records(tenant_id: str, campaign_id: str) -> List[Dict[str, Any]]:
    store = FileSystemAnchorStore(_anchor_root())
    rows = store.read_rows(tenant_id=tenant_id, campaign_id=campaign_id)
    return [dict(row) for row in rows]


def summarize_batch_result(batch_payload: Dict[str, Any], *, round_index: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for idx, item in enumerate(batch_payload.get("results") or []):
        metrics = item.get("metrics") or []
        repl_values = [
            float(m.get("repl_ms"))
            for m in metrics
            if isinstance(m, dict) and isinstance(m.get("repl_ms"), (int, float))
        ]
        restore_values = [
            float(m.get("restore_ms"))
            for m in metrics
            if isinstance(m, dict) and isinstance(m.get("restore_ms"), (int, float))
        ]
        rows.append(
            {
                "ts": time.time(),
                "round": int(round_index),
                "request_index": int(idx),
                "request_id": item.get("request_id"),
                "success": bool(item.get("success")),
                "error_code": item.get("error_code"),
                "new_state_ref": item.get("new_state_ref"),
                "error_count": len(item.get("errors") or []),
                "sorry_count": len(item.get("sorries") or []),
                "repl_ms_avg": (sum(repl_values) / len(repl_values)) if repl_values else None,
                "restore_ms_avg": (sum(restore_values) / len(restore_values)) if restore_values else None,
            }
        )
    return rows


def _checkpoint_dir(tenant_id: str, campaign_id: str) -> Path:
    return _checkpoint_root() / normalize_tenant_id(tenant_id) / str(campaign_id)


def write_campaign_checkpoint(
    *,
    tenant_id: str,
    campaign_id: str,
    round_index: int,
    status: str,
    payload: Dict[str, Any],
    checkpoint_id: Optional[str] = None,
) -> Path:
    checkpoint_id = checkpoint_id or f"r{int(round_index):04d}-{uuid.uuid4().hex[:12]}"
    record = {
        "schema": EVOLAKE_CHECKPOINT_SCHEMA_ID,
        "api_version": EVOLAKE_CAMPAIGN_API_VERSION,
        "checkpoint_id": checkpoint_id,
        "campaign_id": campaign_id,
        "tenant_id": normalize_tenant_id(tenant_id),
        "round": int(round_index),
        "status": str(status),
        "created_at": _now(),
        "payload": payload,
    }
    issues = validate_checkpoint(record)
    if issues:
        raise ValueError(
            "checkpoint schema validation failed: "
            + "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
        )
    root = _checkpoint_dir(tenant_id, campaign_id)
    root.mkdir(parents=True, exist_ok=True)
    out_path = root / f"{checkpoint_id}.json"
    out_path.write_text(json.dumps(record, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return out_path


def list_campaign_checkpoints(tenant_id: str, campaign_id: str) -> List[Dict[str, Any]]:
    root = _checkpoint_dir(tenant_id, campaign_id)
    if not root.exists():
        return []
    checkpoints: List[Dict[str, Any]] = []
    for path in sorted(root.glob("*.json"), key=lambda p: p.name):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = dict(payload)
            payload["path"] = str(path)
            checkpoints.append(payload)
    checkpoints.sort(key=lambda row: (int(row.get("round") or -1), str(row.get("checkpoint_id") or "")))
    return checkpoints


def load_campaign_checkpoint(tenant_id: str, campaign_id: str, checkpoint_id: str) -> Dict[str, Any] | None:
    if not checkpoint_id or not str(checkpoint_id).strip():
        return None
    path = _checkpoint_dir(tenant_id, campaign_id) / f"{str(checkpoint_id).strip()}.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def resolve_resume_round(
    *,
    tenant_id: str,
    campaign_id: str,
    checkpoint_id: str | None,
    resume_from: int | None,
) -> tuple[int, Dict[str, Any] | None]:
    if checkpoint_id:
        checkpoint = load_campaign_checkpoint(tenant_id, campaign_id, checkpoint_id)
        if checkpoint is None:
            raise ValueError(f"checkpoint_id not found: {checkpoint_id}")
        return int(checkpoint.get("round") or 0) + 1, checkpoint
    if resume_from is not None:
        return max(0, int(resume_from)), None
    checkpoints = list_campaign_checkpoints(tenant_id, campaign_id)
    if not checkpoints:
        return 0, None
    latest = checkpoints[-1]
    if str(latest.get("status")) == "ok":
        return int(latest.get("round") or 0) + 1, latest
    return int(latest.get("round") or 0), latest


def write_replay_artifacts(
    *,
    tenant_id: str,
    campaign_id: str,
    rows: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    replay_rows = [dict(row) for row in rows]
    replay_rows.sort(key=lambda row: (int(row.get("round") or 0), int(row.get("request_index") or 0)))
    root = _replay_root() / normalize_tenant_id(tenant_id) / str(campaign_id)
    root.mkdir(parents=True, exist_ok=True)

    rows_path = root / "anchor_rows.jsonl"
    with rows_path.open("w", encoding="utf-8") as handle:
        for row in replay_rows:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")

    quality_score = max(0.0, 100.0 - (10.0 * sum(int(row.get("error_count") or 0) for row in replay_rows)))
    candidates = {
        "schema": "breadboard.evolake.replay_candidates.v1",
        "campaign_id": str(campaign_id),
        "tenant_id": normalize_tenant_id(tenant_id),
        "candidates": [{"name": str(campaign_id), "metrics": {"quality_score": round(float(quality_score), 6)}}],
    }
    candidates_path = root / "replay_eval_candidates.json"
    candidates_path.write_text(json.dumps(candidates, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    manifest = {
        "schema": EVOLAKE_REPLAY_MANIFEST_SCHEMA_ID,
        "api_version": EVOLAKE_CAMPAIGN_API_VERSION,
        "campaign_id": str(campaign_id),
        "tenant_id": normalize_tenant_id(tenant_id),
        "row_count": len(replay_rows),
        "generated_at": _now(),
        "rows_path": str(rows_path),
        "candidates_path": str(candidates_path),
    }
    issues = validate_replay_manifest(manifest)
    if issues:
        raise ValueError(
            "replay manifest schema validation failed: "
            + "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
        )
    manifest_path = root / "replay_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return {
        "rows_path": str(rows_path),
        "candidates_path": str(candidates_path),
        "manifest_path": str(manifest_path),
        "row_count": len(replay_rows),
    }


def run_campaign(payload: Dict[str, Any], *, dry_run: bool = True) -> Dict[str, Any]:
    """Minimal EvoLake campaign runner (extension-only)."""
    record = _build_campaign_record(payload)
    if dry_run:
        return {
            "status": "dry_run",
            "dry_run": True,
            "received": record,
        }

    root = _campaign_root()
    root.mkdir(parents=True, exist_ok=True)
    out_path = root / f"{record['campaign_id']}.json"
    out_path.write_text(json.dumps(record, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return {
        "status": "queued",
        "dry_run": False,
        "received": record,
    }
