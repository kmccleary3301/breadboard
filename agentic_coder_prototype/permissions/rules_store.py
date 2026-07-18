from __future__ import annotations

import json, os, threading, time, uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
try: import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]
    try: import msvcrt
    except ImportError: msvcrt = None  # type: ignore[assignment]
else: msvcrt = None  # type: ignore[assignment]


RULES_VERSION = 1
RULES_REL_PATH = Path(".breadboard") / "permission_rules.json"
_RULES_LOCK = threading.RLock()
@contextmanager
def _locked_rules(path: Path) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _RULES_LOCK, path.with_suffix(path.suffix + ".lock").open("a+b") as stream:
        if fcntl is not None: fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        elif msvcrt is not None:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0: stream.write(b"\0"); stream.flush()
            stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        try: yield
        finally:
            if fcntl is not None: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None: stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


@dataclass(frozen=True)
class PermissionRule:
    category: str
    pattern: str
    decision: str  # "allow" | "deny"
    scope: str = "project"
    updated_at_ms: int | None = None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _load_raw(path: Path) -> Dict[str, Any]:
    try: data = json.loads(path.read_text(encoding="utf-8"))
    except Exception: return {}
    return data if isinstance(data, dict) else {}

def _write_raw(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream: stream.write(json.dumps(payload, indent=2, sort_keys=True).encode()); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
    finally: temporary.unlink(missing_ok=True)


def load_permission_rules(workspace_dir: Path) -> List[PermissionRule]:
    """Load persisted permission rules for the workspace (best-effort)."""
    path = Path(workspace_dir).resolve() / RULES_REL_PATH
    raw = _load_raw(path)
    rules_raw = raw.get("rules")
    if not isinstance(rules_raw, list):
        return []
    rules: List[PermissionRule] = []
    for entry in rules_raw:
        if not isinstance(entry, dict):
            continue
        category = str(entry.get("category") or "").strip().lower()
        pattern = str(entry.get("pattern") or "").strip()
        decision = str(entry.get("decision") or "").strip().lower()
        scope = str(entry.get("scope") or "project").strip().lower()
        if not category or not pattern or decision not in {"allow", "deny"}:
            continue
        updated_at = entry.get("updated_at_ms")
        updated_at_ms = int(updated_at) if isinstance(updated_at, (int, float)) else None
        rules.append(PermissionRule(category=category, pattern=pattern, decision=decision, scope=scope, updated_at_ms=updated_at_ms))
    return rules


def upsert_permission_rule(
    workspace_dir: Path,
    *,
    category: str,
    pattern: str,
    decision: str,
    scope: str = "project",
) -> bool:
    """Insert or update a single permission rule on disk."""
    path = Path(workspace_dir).resolve() / RULES_REL_PATH
    cat, pat = str(category or "").strip().lower(), str(pattern or "").strip()
    dec, scp = str(decision or "").strip().lower(), str(scope or "project").strip().lower()
    if not cat or not pat or dec not in {"allow", "deny"}: return False
    with _locked_rules(path):
        raw = _load_raw(path); rules = raw.get("rules")
        rules = rules if isinstance(rules, list) else []; updated_at_ms = _now_ms(); replaced = False; next_rules: List[Dict[str, Any]] = []
        replacement = {"category": cat, "pattern": pat, "decision": dec, "scope": scp, "updated_at_ms": updated_at_ms}
        for entry in rules:
            if not isinstance(entry, dict): continue
            if str(entry.get("category") or "").strip().lower() == cat and str(entry.get("pattern") or "").strip() == pat: next_rules.append(replacement); replaced = True
            else: next_rules.append(dict(entry))
        if not replaced: next_rules.append(replacement)
        _write_raw(path, {"version": RULES_VERSION, "updated_at_ms": updated_at_ms, "rules": next_rules})
        return True


def build_permission_overrides(config: Dict[str, Any], rules: List[PermissionRule]) -> Dict[str, Any]:
    """Build dotted-key overrides to merge persisted rules into `permissions.*` config."""
    allow_by_cat: Dict[str, List[str]] = {}
    deny_by_cat: Dict[str, List[str]] = {}
    for rule in rules or []:
        if rule.scope != "project":
            continue
        bucket = allow_by_cat if rule.decision == "allow" else deny_by_cat
        bucket.setdefault(rule.category, [])
        if rule.pattern not in bucket[rule.category]:
            bucket[rule.category].append(rule.pattern)

    permissions = config.get("permissions") or {}
    if not isinstance(permissions, dict):
        permissions = {}

    overrides: Dict[str, Any] = {}

    def _merged(category: str, key: str, extra: List[str]) -> List[str]:
        cfg = permissions.get(category) or {}
        if not isinstance(cfg, dict):
            cfg = {}
        existing = cfg.get(key) or cfg.get(f"{key}list") or []
        if not isinstance(existing, list):
            existing = []
        merged: List[str] = []
        for item in list(existing) + list(extra):
            text = str(item).strip()
            if text and text not in merged:
                merged.append(text)
        return merged

    for category, patterns in allow_by_cat.items():
        overrides[f"permissions.{category}.allow"] = _merged(category, "allow", patterns)
    for category, patterns in deny_by_cat.items():
        overrides[f"permissions.{category}.deny"] = _merged(category, "deny", patterns)
    return overrides

