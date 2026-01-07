from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


RULES_VERSION = 1
RULES_REL_PATH = Path(".breadboard") / "permission_rules.json"


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
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_raw(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


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
    ws = Path(workspace_dir).resolve()
    path = ws / RULES_REL_PATH
    raw = _load_raw(path)
    rules = raw.get("rules")
    if not isinstance(rules, list):
        rules = []

    cat = str(category or "").strip().lower()
    pat = str(pattern or "").strip()
    dec = str(decision or "").strip().lower()
    scp = str(scope or "project").strip().lower()
    if not cat or not pat or dec not in {"allow", "deny"}:
        return False

    updated_at_ms = _now_ms()
    replaced = False
    next_rules: List[Dict[str, Any]] = []
    for entry in rules:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("category") or "").strip().lower() == cat and str(entry.get("pattern") or "").strip() == pat:
            next_rules.append(
                {
                    "category": cat,
                    "pattern": pat,
                    "decision": dec,
                    "scope": scp,
                    "updated_at_ms": updated_at_ms,
                }
            )
            replaced = True
        else:
            next_rules.append(dict(entry))
    if not replaced:
        next_rules.append(
            {
                "category": cat,
                "pattern": pat,
                "decision": dec,
                "scope": scp,
                "updated_at_ms": updated_at_ms,
            }
        )

    raw = {
        "version": RULES_VERSION,
        "updated_at_ms": updated_at_ms,
        "rules": next_rules,
    }
    _write_raw(path, raw)
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

