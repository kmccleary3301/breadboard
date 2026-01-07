from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional


def _as_str_list(value: Any) -> Optional[List[str]]:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v) for v in value if isinstance(v, (str, int, float)) and str(v).strip()]
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        return [text] if text else []
    return None


def wildcard_match(value: str, pattern: str) -> bool:
    """Wildcard match where only `*` and `?` are special."""
    pat = str(pattern or "")
    text = str(value or "")
    try:
        escaped = re.escape(pat)
        escaped = escaped.replace(r"\*", ".*").replace(r"\?", ".")
        return re.match("^" + escaped + "$", text, flags=re.S) is not None
    except Exception:
        return False


def _matches_any(value: str, patterns: Iterable[str]) -> bool:
    for pat in patterns or []:
        if wildcard_match(value, pat):
            return True
    return False


@dataclass(frozen=True)
class PolicyPack:
    """Policy pack enforced server-side for enterprise/security controls."""

    tool_allowlist: Optional[List[str]] = None
    tool_denylist: Optional[List[str]] = None
    model_allowlist: Optional[List[str]] = None
    model_denylist: Optional[List[str]] = None

    @classmethod
    def from_config(cls, config: Dict[str, Any] | None) -> "PolicyPack":
        cfg = dict(config or {})
        raw = cfg.get("policies") or cfg.get("policy") or {}
        if not isinstance(raw, dict):
            raw = {}

        tools_cfg = raw.get("tools") or raw.get("tool_allowlist") or raw.get("tool_allow") or {}
        models_cfg = raw.get("models") or raw.get("model_allowlist") or raw.get("model_allow") or {}

        tool_allow = None
        tool_deny = None
        if isinstance(tools_cfg, dict):
            tool_allow = _as_str_list(tools_cfg.get("allow") or tools_cfg.get("allowlist"))
            tool_deny = _as_str_list(tools_cfg.get("deny") or tools_cfg.get("denylist") or tools_cfg.get("block"))
        else:
            tool_allow = _as_str_list(tools_cfg)

        model_allow = None
        model_deny = None
        if isinstance(models_cfg, dict):
            model_allow = _as_str_list(models_cfg.get("allow") or models_cfg.get("allowlist"))
            model_deny = _as_str_list(models_cfg.get("deny") or models_cfg.get("denylist") or models_cfg.get("block"))
        else:
            model_allow = _as_str_list(models_cfg)

        return cls(
            tool_allowlist=tool_allow,
            tool_denylist=tool_deny,
            model_allowlist=model_allow,
            model_denylist=model_deny,
        )

    def is_tool_allowed(self, tool_name: str) -> bool:
        name = str(tool_name or "")
        if not name:
            return False
        if self.tool_allowlist is not None and not _matches_any(name, self.tool_allowlist):
            return False
        if self.tool_denylist and _matches_any(name, self.tool_denylist):
            return False
        return True

    def filter_tool_names(self, tool_names: Iterable[str]) -> List[str]:
        return [name for name in tool_names if self.is_tool_allowed(name)]

    def is_model_allowed(self, model_id: str) -> bool:
        name = str(model_id or "")
        if not name:
            return False
        if self.model_allowlist is not None and not _matches_any(name, self.model_allowlist):
            return False
        if self.model_denylist and _matches_any(name, self.model_denylist):
            return False
        return True

