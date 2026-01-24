from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


class ToolPromptSynthesisEngine:
    def __init__(self, root: str = "implementations/tool_prompt_synthesis") -> None:
        self._repo_root = Path(__file__).resolve().parents[2]
        root_path = Path(root)
        if not root_path.is_absolute():
            root_path = self._repo_root / root_path
        self.root = root_path

        self._dialect_dir_aliases = {
            "pythonic02": "pythonic",
            "pythonic_inline": "pythonic",
        }

    def _resolve_dialect_dir(self, dialect: str) -> str:
        if not dialect:
            return "pythonic"
        dialect = str(dialect)
        alias = self._dialect_dir_aliases.get(dialect)
        if alias:
            return alias
        return dialect

    def _normalize_tools(self, tools: List[Any]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for tool in tools or []:
            if isinstance(tool, dict):
                params = tool.get("parameters") or []
                normalized_params: List[Dict[str, Any]] = []
                if isinstance(params, list):
                    for param in params:
                        if isinstance(param, dict):
                            normalized_params.append(
                                {
                                    "name": _safe_str(param.get("name")),
                                    "type": _safe_str(param.get("type")),
                                    "description": _safe_str(param.get("description")),
                                    "default": param.get("default", None),
                                    "required": bool(param.get("required", False)),
                                }
                            )
                        else:
                            normalized_params.append(
                                {
                                    "name": _safe_str(getattr(param, "name", None)),
                                    "type": _safe_str(getattr(param, "type", None)),
                                    "description": _safe_str(getattr(param, "description", None)),
                                    "default": getattr(param, "default", None),
                                    "required": bool(getattr(param, "required", False)),
                                }
                            )
                normalized.append(
                    {
                        "name": _safe_str(tool.get("name")),
                        "display_name": _safe_str(tool.get("display_name") or tool.get("displayName")),
                        "description": _safe_str(tool.get("description")),
                        "parameters": normalized_params,
                    }
                )
                continue
            name = _safe_str(getattr(tool, "name", None))
            display_name = _safe_str(getattr(tool, "display_name", None) or getattr(tool, "displayName", None))
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
            normalized.append(
                {
                    "name": name,
                    "display_name": display_name,
                    "description": description,
                    "parameters": params,
                }
            )
        return normalized

    def _render_fallback(self, dialect: str, detail: str, tools: List[Any]) -> str:
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

    def _render_template(self, template_path: str, tools: List[Any]) -> str:
        path = Path(template_path)
        if not path.is_absolute():
            path = self._repo_root / path
        template_text = path.read_text(encoding="utf-8")
        try:
            from jinja2 import Environment

            env = Environment(
                autoescape=False,
                trim_blocks=True,
                lstrip_blocks=True,
            )
            template = env.from_string(template_text)
            rendered = template.render(tools=tools)
            return str(rendered or "").strip()
        except Exception:
            return template_text.strip()

    def render(
        self,
        dialect: str,
        detail: str,
        tools: List[Any],
        template_map: Optional[Dict[str, Any]] = None,
        *,
        templates: Optional[Dict[str, Any]] = None,
        return_meta: bool = False,
    ) -> Tuple[str, Any]:
        if template_map is None and isinstance(templates, dict):
            template_map = templates

        dialect_dir = self._resolve_dialect_dir(dialect)
        template_path: Optional[str] = None
        if isinstance(template_map, dict):
            raw = template_map.get(detail)
            if raw:
                template_path = str(raw)

        if not template_path:
            candidate = self.root / dialect_dir / f"{detail}.j2.md"
            if candidate.exists():
                template_path = str(candidate)

        template_id = f"{dialect_dir}::{detail}"
        if template_path:
            template_id = f"{dialect_dir}::{detail}:{template_path}"
            normalized_tools = self._normalize_tools(tools)
            text = self._render_template(template_path, normalized_tools)
        else:
            text = self._render_fallback(dialect_dir, detail, tools)

        if return_meta:
            return text, {"template_id": template_id, "dialect": dialect_dir, "detail": detail}
        return text, template_id

    def compile(self, *args, **kwargs) -> Dict[str, Any]:
        # Backwards-compatible stub interface; prefer render().
        return {}
