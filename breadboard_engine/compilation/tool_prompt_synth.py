from __future__ import annotations

from typing import Any, Dict, List, Tuple


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


class ToolPromptSynthesisEngine:
    """Minimal prompt synthesis engine stub for recovery."""

    def _normalize_tools(self, tools: List[Any]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for tool in tools or []:
            if isinstance(tool, dict):
                params = tool.get("parameters") or []
                normalized.append(
                    {
                        "name": _safe_str(tool.get("name")),
                        "description": _safe_str(tool.get("description")),
                        "parameters": params if isinstance(params, list) else [],
                    }
                )
                continue
            name = _safe_str(getattr(tool, "name", None))
            description = _safe_str(getattr(tool, "description", None))
            params = []
            for param in getattr(tool, "parameters", []) or []:
                if isinstance(param, dict):
                    params.append(param)
                else:
                    params.append(
                        {
                            "name": _safe_str(getattr(param, "name", None)),
                            "type": _safe_str(getattr(param, "type", None)),
                            "description": _safe_str(getattr(param, "description", None)),
                            "default": getattr(param, "default", None),
                        }
                    )
            normalized.append({"name": name, "description": description, "parameters": params})
        return normalized

    def _render_catalog(self, dialect: str, detail: str, tools: List[Any]) -> str:
        normalized = self._normalize_tools(tools)
        lines: List[str] = ["# TOOL CATALOG"]
        if dialect:
            lines.append(f"Dialect: {dialect}")
        if detail:
            lines.append(f"Detail: {detail}")
        lines.append("")
        if not normalized:
            lines.append("No tools available.")
            return "\n".join(lines).strip()

        is_short = "per_turn" in detail or "short" in detail
        if is_short:
            lines.append("Available tools:")
            for tool in normalized:
                if tool.get("name"):
                    lines.append(f"- {tool['name']}")
            return "\n".join(lines).strip()

        for tool in normalized:
            name = tool.get("name") or ""
            if not name:
                continue
            lines.append(f"## {name}")
            desc = tool.get("description") or ""
            if desc:
                lines.append(desc)
            params = tool.get("parameters") or []
            if params:
                lines.append("")
                lines.append("Parameters:")
                for param in params:
                    pname = _safe_str(param.get("name"))
                    ptype = _safe_str(param.get("type"))
                    pdesc = _safe_str(param.get("description"))
                    if pname:
                        chunk = f"- {pname}"
                        if ptype:
                            chunk += f" ({ptype})"
                        if pdesc:
                            chunk += f": {pdesc}"
                        lines.append(chunk)
            lines.append("")
        return "\n".join(lines).strip()

    def render(
        self,
        dialect: str,
        detail: str,
        tools: List[Any],
        templates: Dict[str, Any] | None = None,
    ) -> Tuple[str, Dict[str, Any]]:
        template_id = None
        if isinstance(templates, dict):
            template_id = templates.get(detail)
        if not template_id:
            template_id = f"{dialect}::{detail}"
        text = self._render_catalog(dialect, detail, tools)
        meta = {
            "template_id": template_id,
            "dialect": dialect,
            "detail": detail,
            "text": text,
        }
        return text, meta

    def compile(self, *args, **kwargs) -> Dict[str, Any]:
        # Backwards-compatible stub interface; prefer render().
        return {}
