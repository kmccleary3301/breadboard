from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple


RLM_TOOL_NAMES = {
    "blob.put",
    "blob.put_file_slice",
    "blob.get",
    "blob.search",
    "llm.query",
    "llm.batch_query",
}


def get_rlm_config(config: Mapping[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(config, Mapping):
        return {}
    features = config.get("features")
    if not isinstance(features, Mapping):
        return {}
    rlm = features.get("rlm")
    if not isinstance(rlm, Mapping):
        return {}
    return dict(rlm)


def is_rlm_enabled(config: Mapping[str, Any] | None) -> bool:
    rlm_cfg = get_rlm_config(config)
    return bool(rlm_cfg.get("enabled"))


def _as_non_negative_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return int(default)
    return parsed if parsed >= 0 else int(default)


def _as_non_negative_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return float(default)
    return parsed if parsed >= 0.0 else float(default)


def build_budget_limits(config: Mapping[str, Any] | None) -> Dict[str, Any]:
    rlm_cfg = get_rlm_config(config)
    budget = rlm_cfg.get("budget")
    budget = dict(budget) if isinstance(budget, Mapping) else {}
    per_branch = budget.get("per_branch")
    per_branch = dict(per_branch) if isinstance(per_branch, Mapping) else {}
    return {
        "max_depth": _as_non_negative_int(budget.get("max_depth"), 0),
        "max_subcalls": _as_non_negative_int(budget.get("max_subcalls"), 0),
        "max_total_tokens": _as_non_negative_int(budget.get("max_total_tokens"), 0),
        "max_total_cost_usd": _as_non_negative_float(budget.get("max_total_cost_usd"), 0.0),
        "max_wallclock_seconds": _as_non_negative_int(budget.get("max_wallclock_seconds"), 0),
        "per_branch": {
            "max_subcalls": _as_non_negative_int(per_branch.get("max_subcalls"), 0),
            "max_total_tokens": _as_non_negative_int(per_branch.get("max_total_tokens"), 0),
            "max_total_cost_usd": _as_non_negative_float(per_branch.get("max_total_cost_usd"), 0.0),
        },
    }


def init_budget_state(existing: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    state = dict(existing) if isinstance(existing, Mapping) else {}
    started_at = _as_non_negative_float(state.get("started_at"), 0.0)
    if started_at <= 0.0:
        started_at = float(time.time())
    out = {
        "started_at": started_at,
        "subcalls": _as_non_negative_int(state.get("subcalls"), 0),
        "total_tokens": _as_non_negative_int(state.get("total_tokens"), 0),
        "total_cost_usd": _as_non_negative_float(state.get("total_cost_usd"), 0.0),
        "max_depth_seen": _as_non_negative_int(state.get("max_depth_seen"), 0),
        "branch_usage": {},
    }
    branch_usage = state.get("branch_usage")
    if isinstance(branch_usage, Mapping):
        for key, value in branch_usage.items():
            if not key or not isinstance(value, Mapping):
                continue
            out["branch_usage"][str(key)] = {
                "subcalls": _as_non_negative_int(value.get("subcalls"), 0),
                "total_tokens": _as_non_negative_int(value.get("total_tokens"), 0),
                "total_cost_usd": _as_non_negative_float(value.get("total_cost_usd"), 0.0),
            }
    return out


def _branch_state(state: Dict[str, Any], branch_id: str) -> Dict[str, Any]:
    branch_usage = state.setdefault("branch_usage", {})
    row = branch_usage.get(branch_id)
    if not isinstance(row, dict):
        row = {"subcalls": 0, "total_tokens": 0, "total_cost_usd": 0.0}
        branch_usage[branch_id] = row
    return row


def can_start_subcall(
    *,
    state: Mapping[str, Any],
    limits: Mapping[str, Any],
    branch_id: str,
    depth: int,
    now_ts: Optional[float] = None,
) -> Tuple[bool, Optional[str]]:
    now = float(now_ts if now_ts is not None else time.time())
    started_at = _as_non_negative_float(state.get("started_at"), now)
    elapsed = max(0.0, now - started_at)

    max_depth = _as_non_negative_int(limits.get("max_depth"), 0)
    if max_depth > 0 and int(depth) > max_depth:
        return False, "depth_exceeded"

    max_subcalls = _as_non_negative_int(limits.get("max_subcalls"), 0)
    if max_subcalls > 0 and _as_non_negative_int(state.get("subcalls"), 0) >= max_subcalls:
        return False, "subcall_limit_exceeded"

    max_tokens = _as_non_negative_int(limits.get("max_total_tokens"), 0)
    if max_tokens > 0 and _as_non_negative_int(state.get("total_tokens"), 0) >= max_tokens:
        return False, "token_budget_exceeded"

    max_cost = _as_non_negative_float(limits.get("max_total_cost_usd"), 0.0)
    if max_cost > 0 and _as_non_negative_float(state.get("total_cost_usd"), 0.0) >= max_cost:
        return False, "cost_budget_exceeded"

    max_wallclock = _as_non_negative_int(limits.get("max_wallclock_seconds"), 0)
    if max_wallclock > 0 and elapsed >= float(max_wallclock):
        return False, "wallclock_budget_exceeded"

    per_branch = limits.get("per_branch")
    per_branch = dict(per_branch) if isinstance(per_branch, Mapping) else {}
    branch_usage = state.get("branch_usage")
    branch_row = dict(branch_usage.get(branch_id) or {}) if isinstance(branch_usage, Mapping) else {}

    branch_max_subcalls = _as_non_negative_int(per_branch.get("max_subcalls"), 0)
    if branch_max_subcalls > 0 and _as_non_negative_int(branch_row.get("subcalls"), 0) >= branch_max_subcalls:
        return False, "branch_subcall_limit_exceeded"

    branch_max_tokens = _as_non_negative_int(per_branch.get("max_total_tokens"), 0)
    if branch_max_tokens > 0 and _as_non_negative_int(branch_row.get("total_tokens"), 0) >= branch_max_tokens:
        return False, "branch_token_budget_exceeded"

    branch_max_cost = _as_non_negative_float(per_branch.get("max_total_cost_usd"), 0.0)
    if branch_max_cost > 0 and _as_non_negative_float(branch_row.get("total_cost_usd"), 0.0) >= branch_max_cost:
        return False, "branch_cost_budget_exceeded"

    return True, None


def extract_usage_metrics(usage: Mapping[str, Any] | None) -> Tuple[int, float]:
    if not isinstance(usage, Mapping):
        return 0, 0.0
    total_tokens = usage.get("total_tokens")
    if total_tokens is None:
        try:
            total_tokens = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
        except Exception:
            total_tokens = 0
    tokens = _as_non_negative_int(total_tokens, 0)
    cost = usage.get("cost_usd", usage.get("total_cost_usd", usage.get("cost", usage.get("total_cost", 0.0))))
    cost_usd = _as_non_negative_float(cost, 0.0)
    return tokens, cost_usd


def consume_subcall(
    *,
    state: Dict[str, Any],
    branch_id: str,
    depth: int,
    usage_tokens: int,
    usage_cost_usd: float,
) -> Dict[str, Any]:
    out = init_budget_state(state)
    out["subcalls"] = _as_non_negative_int(out.get("subcalls"), 0) + max(0, int(usage_tokens >= 0))
    out["total_tokens"] = _as_non_negative_int(out.get("total_tokens"), 0) + max(0, int(usage_tokens))
    out["total_cost_usd"] = round(_as_non_negative_float(out.get("total_cost_usd"), 0.0) + max(0.0, float(usage_cost_usd)), 8)
    out["max_depth_seen"] = max(_as_non_negative_int(out.get("max_depth_seen"), 0), max(0, int(depth)))
    row = _branch_state(out, branch_id)
    row["subcalls"] = _as_non_negative_int(row.get("subcalls"), 0) + 1
    row["total_tokens"] = _as_non_negative_int(row.get("total_tokens"), 0) + max(0, int(usage_tokens))
    row["total_cost_usd"] = round(_as_non_negative_float(row.get("total_cost_usd"), 0.0) + max(0.0, float(usage_cost_usd)), 8)
    return out


class BlobStore:
    def __init__(self, workspace: str, config: Mapping[str, Any] | None = None) -> None:
        self.workspace_root = Path(str(workspace or ".")).resolve()
        rlm_cfg = get_rlm_config(config)
        blob_cfg = rlm_cfg.get("blob_store")
        blob_cfg = dict(blob_cfg) if isinstance(blob_cfg, Mapping) else {}
        root_value = str(blob_cfg.get("root") or ".breadboard/rlm_blobs")
        root_path = Path(root_value)
        if not root_path.is_absolute():
            root_path = (self.workspace_root / root_path).resolve()
        self.root = root_path
        self.max_total_bytes = _as_non_negative_int(blob_cfg.get("max_total_bytes"), 0)
        self.max_blob_bytes = _as_non_negative_int(blob_cfg.get("max_blob_bytes"), 0)
        self.default_preview_bytes = _as_non_negative_int(blob_cfg.get("mvi_excerpt_bytes"), 1200) or 1200
        self.root.mkdir(parents=True, exist_ok=True)

    def _blob_path(self, blob_id: str) -> Path:
        digest = str(blob_id).split("sha256:", 1)[-1].strip().lower()
        return self.root / f"{digest}.txt"

    def _meta_path(self, blob_id: str) -> Path:
        digest = str(blob_id).split("sha256:", 1)[-1].strip().lower()
        return self.root / f"{digest}.meta.json"

    def _hash_bytes(self, content: bytes) -> str:
        return f"sha256:{hashlib.sha256(content).hexdigest()}"

    def _current_size_bytes(self) -> int:
        total = 0
        try:
            for path in self.root.glob("*.txt"):
                try:
                    total += int(path.stat().st_size)
                except Exception:
                    continue
        except Exception:
            return total
        return total

    def _enforce_limits(self, content_bytes: bytes) -> None:
        if self.max_blob_bytes > 0 and len(content_bytes) > self.max_blob_bytes:
            raise ValueError("blob_too_large")
        if self.max_total_bytes > 0:
            projected = self._current_size_bytes() + len(content_bytes)
            if projected > self.max_total_bytes:
                raise ValueError("blob_store_full")

    def put_content(
        self,
        *,
        content: str,
        content_type: str = "text/plain",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        text = str(content or "")
        raw = text.encode("utf-8", errors="ignore")
        self._enforce_limits(raw)
        blob_id = self._hash_bytes(raw)
        blob_path = self._blob_path(blob_id)
        meta_path = self._meta_path(blob_id)
        if not blob_path.exists():
            blob_path.write_bytes(raw)
        meta = {
            "blob_id": blob_id,
            "size_bytes": len(raw),
            "content_type": str(content_type or "text/plain"),
            "metadata": dict(metadata) if isinstance(metadata, Mapping) else {},
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta

    def put_file_slice(
        self,
        *,
        path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        branch_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        rel_or_abs = Path(str(path))
        target = rel_or_abs if rel_or_abs.is_absolute() else (self.workspace_root / rel_or_abs)
        target = target.resolve()
        try:
            target.relative_to(self.workspace_root)
        except Exception as exc:
            raise ValueError("path_outside_workspace") from exc
        if not target.exists() or not target.is_file():
            raise ValueError("file_not_found")
        text = target.read_text(encoding="utf-8", errors="ignore")
        lines = text.splitlines()
        s_line = 1 if start_line is None else max(1, int(start_line))
        e_line = len(lines) if end_line is None else max(s_line, int(end_line))
        selected = lines[s_line - 1 : e_line]
        payload = "\n".join(selected)
        if text.endswith("\n") and e_line >= len(lines):
            payload += "\n"
        meta = self.put_content(
            content=payload,
            content_type="text/plain",
            metadata={
                "path": str(target.relative_to(self.workspace_root)),
                "start_line": s_line,
                "end_line": e_line,
                "branch_id": str(branch_id or "root"),
            },
        )
        meta["path"] = str(target.relative_to(self.workspace_root))
        meta["start_line"] = s_line
        meta["end_line"] = e_line
        return meta

    def get(self, blob_id: str, preview_bytes: Optional[int] = None) -> Dict[str, Any]:
        blob_path = self._blob_path(blob_id)
        meta_path = self._meta_path(blob_id)
        if not blob_path.exists():
            raise ValueError("blob_not_found")
        raw = blob_path.read_bytes()
        size = len(raw)
        preview_n = self.default_preview_bytes if preview_bytes is None else max(0, int(preview_bytes))
        preview = raw[:preview_n].decode("utf-8", errors="ignore")
        meta = {}
        if meta_path.exists():
            try:
                loaded = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    meta = loaded.get("metadata") if isinstance(loaded.get("metadata"), dict) else {}
            except Exception:
                meta = {}
        return {
            "blob_id": str(blob_id),
            "size_bytes": size,
            "content_type": "text/plain",
            "preview": preview,
            "truncated": size > preview_n,
            "metadata": meta,
            "content": raw.decode("utf-8", errors="ignore"),
        }

    def search(self, *, blob_ids: Optional[List[str]], query: str, max_results: int = 20) -> Dict[str, Any]:
        needle = str(query or "")
        if not needle:
            raise ValueError("empty_query")
        limit = max(1, int(max_results or 20))
        scan_ids: List[str] = []
        if blob_ids:
            scan_ids = sorted({str(blob_id) for blob_id in blob_ids if blob_id})
        else:
            for meta_path in sorted(self.root.glob("*.meta.json")):
                try:
                    loaded = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if isinstance(loaded, dict) and isinstance(loaded.get("blob_id"), str):
                    scan_ids.append(str(loaded["blob_id"]))
        results: List[Dict[str, Any]] = []
        for blob_id in scan_ids:
            try:
                entry = self.get(blob_id, preview_bytes=0)
            except Exception:
                continue
            content = str(entry.get("content") or "")
            lines = content.splitlines()
            for idx, line in enumerate(lines, start=1):
                if needle not in line:
                    continue
                snippet = line.strip()
                if len(snippet) > 220:
                    snippet = snippet[:220] + "..."
                results.append({"blob_id": blob_id, "line": idx, "snippet": snippet})
                if len(results) >= limit:
                    return {"query": needle, "results": results}
        return {"query": needle, "results": results}

