from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from jinja2 import Environment, StrictUndefined


class ToolPromptSynthesisEngine:
    """Render tool prompt synthesis templates with a safe, minimal context."""

    def __init__(self, root: str = "implementations/tool_prompt_synthesis") -> None:
        self.root = Path(root)
        self._env = Environment(
            undefined=StrictUndefined,
            autoescape=False,
            trim_blocks=False,
            lstrip_blocks=False,
        )

    def _normalize_tools(self, tools: Iterable[Any]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for tool in tools or []:
            if isinstance(tool, dict):
                tdict = dict(tool)
                params_in = tdict.get("parameters") or []
                params_out: List[Dict[str, Any]] = []
                for p in params_in:
                    if isinstance(p, dict):
                        params_out.append(dict(p))
                    else:
                        params_out.append({
                            "name": getattr(p, "name", None),
                            "type": getattr(p, "type", None),
                            "default": getattr(p, "default", None),
                            "required": bool(getattr(p, "required", False)),
                            "description": getattr(p, "description", None),
                        })
                tdict["parameters"] = params_out
                normalized.append(tdict)
                continue

            params = []
            for p in (getattr(tool, "parameters", None) or []):
                params.append({
                    "name": getattr(p, "name", None),
                    "type": getattr(p, "type", None),
                    "default": getattr(p, "default", None),
                    "required": bool(getattr(p, "required", False)),
                    "description": getattr(p, "description", None),
                })
            normalized.append({
                "name": getattr(tool, "name", None),
                "display_name": getattr(tool, "display_name", None),
                "description": getattr(tool, "description", "") or "",
                "blocking": bool(getattr(tool, "blocking", False)),
                "max_per_turn": getattr(tool, "max_per_turn", None),
                "parameters": params,
                "return_type": getattr(tool, "return_type", None),
                "syntax_style": getattr(tool, "syntax_style", None),
            })
        return normalized

    def _resolve_template_path(self, path: str) -> Optional[Path]:
        if not path:
            return None
        candidate = Path(path)
        if candidate.exists():
            return candidate
        if not candidate.is_absolute():
            root_candidate = self.root / candidate
            if root_candidate.exists():
                return root_candidate
        return None

    def render(
        self,
        dialect_id: str,
        detail: str,
        tools: Iterable[Any],
        template_map: Optional[Dict[str, str]] = None,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, str]:
        template_map = template_map or {}
        template_key = detail if detail in template_map else detail.strip()
        template_path = self._resolve_template_path(template_map.get(template_key, ""))
        if template_path is None:
            return "", f"{dialect_id}:{detail}:missing"

        template_text = template_path.read_text(encoding="utf-8")
        template = self._env.from_string(template_text)
        context = {"tools": self._normalize_tools(tools)}
        if extra_context:
            context.update(extra_context)
        rendered = template.render(**context)
        template_hash = hashlib.sha256(template_text.encode("utf-8")).hexdigest()[:10]
        template_id = f"{dialect_id}:{detail}:{template_hash}"
        return rendered, template_id
