from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

try:
    from jinja2 import Environment, StrictUndefined  # type: ignore
except Exception:  # pragma: no cover - fallback when jinja2 unavailable
    Environment = None  # type: ignore
    StrictUndefined = None  # type: ignore


ROOT_DIR = Path(__file__).resolve().parents[2]


@dataclass
class GuardrailDefinition:
    """Structured guardrail declaration loaded from YAML bundles."""

    id: str
    type: str
    templates: Dict[str, str] = field(default_factory=dict)
    parameters: Dict[str, Any] = field(default_factory=dict)
    enable_if: Optional[Any] = None
    disable_if: Optional[Any] = None

    def snapshot(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "templates": dict(self.templates),
            "parameters": dict(self.parameters),
        }


class GuardrailPromptRenderer:
    """Lightweight Jinja renderer for guard prompt templates."""

    def __init__(self, root: Optional[Path] = None):
        self.root = root or ROOT_DIR
        self._env: Optional[Environment] = None

    def _ensure_env(self) -> Optional[Environment]:
        if Environment is None:
            return None
        if self._env is None:
            self._env = Environment(undefined=StrictUndefined, autoescape=False)
        return self._env

    def render(self, template_path: Optional[str], context: Optional[Dict[str, Any]] = None) -> Optional[str]:
        if not template_path:
            return None
        path = Path(template_path)
        if not path.is_absolute():
            path = (self.root / template_path).resolve()
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return None
        context = context or {}
        if ("{{" not in text) and ("{% " not in text):
            return text
        env = self._ensure_env()
        if env is None:
            return text
        try:
            template = env.from_string(text)
            return template.render(**context)
        except Exception:
            # Fall back to raw text to avoid blocking the guard.
            return text


class GuardrailHandlerRegistry:
    """Simple registry that maps guard `type` -> handler classes."""

    _registry: Dict[str, Any] = {}

    @classmethod
    def register(cls, guard_type: str):
        def decorator(handler_cls):
            cls._registry[guard_type] = handler_cls
            return handler_cls

        return decorator

    @classmethod
    def create(
        cls,
        definition: GuardrailDefinition,
        renderer: GuardrailPromptRenderer,
        config: Dict[str, Any],
    ):
        handler_cls = cls._registry.get(definition.type)
        if not handler_cls:
            return None
        return handler_cls(definition, renderer, config)


def _get_from_config(config: Dict[str, Any], dotted_path: str) -> Any:
    cursor: Any = config
    for token in dotted_path.split("."):
        if isinstance(cursor, dict):
            cursor = cursor.get(token)
        else:
            return None
    return cursor


def _ensure_list(value: Any) -> List[Any]:
    if not value:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _coerce_guard_definition(raw: Dict[str, Any]) -> GuardrailDefinition:
    return GuardrailDefinition(
        id=str(raw.get("id") or raw.get("name")),
        type=str(raw.get("type")),
        templates=dict(raw.get("templates") or {}),
        parameters=dict(raw.get("parameters") or {}),
        enable_if=raw.get("enable_if"),
        disable_if=raw.get("disable_if"),
    )


def _load_bundle(path: Path) -> Dict[str, GuardrailDefinition]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    guards = {}
    for entry in data.get("guards", []):
        definition = _coerce_guard_definition(entry)
        if not definition.id or not definition.type:
            continue
        guards[definition.id] = definition
    return guards


def _should_enable(definition: GuardrailDefinition, config: Dict[str, Any]) -> bool:
    if definition.disable_if is not None:
        if isinstance(definition.disable_if, str):
            if _get_from_config(config, definition.disable_if):
                return False
        elif isinstance(definition.disable_if, bool):
            if definition.disable_if:
                return False
    if definition.enable_if is None:
        return True
    if isinstance(definition.enable_if, bool):
        return definition.enable_if
    if isinstance(definition.enable_if, str):
        return bool(_get_from_config(config, definition.enable_if))
    return True


class GuardrailManager:
    """Central access point for configured guard handlers."""

    def __init__(
        self,
        definitions: Dict[str, GuardrailDefinition],
        config: Dict[str, Any],
        renderer: Optional[GuardrailPromptRenderer] = None,
    ) -> None:
        self.config = config
        self.renderer = renderer or GuardrailPromptRenderer()
        self.definitions = definitions
        self.handlers: Dict[str, Any] = {}
        for guard_id, definition in self.definitions.items():
            handler = GuardrailHandlerRegistry.create(definition, self.renderer, config)
            if handler:
                self.handlers[guard_id] = handler

    def get_handler(self, guard_id: str):
        return self.handlers.get(guard_id)

    def handlers_by_type(self, guard_type: str):
        return [h for h in self.handlers.values() if getattr(h, "guard_type", "") == guard_type]

    def get_first_handler_of_type(self, guard_type: str):
        handlers = self.handlers_by_type(guard_type)
        return handlers[0] if handlers else None

    def snapshot(self) -> Dict[str, Any]:
        return {guard_id: definition.snapshot() for guard_id, definition in self.definitions.items()}


def build_guardrail_manager(config: Dict[str, Any]) -> Optional[GuardrailManager]:
    guardrails_cfg = dict(config.get("guardrails") or {})
    include_paths = _ensure_list(guardrails_cfg.get("include"))
    inline_defs = _ensure_list(guardrails_cfg.get("inline"))
    overrides = guardrails_cfg.get("overrides") or {}
    if not (include_paths or inline_defs):
        return None

    resolved_defs: Dict[str, GuardrailDefinition] = {}
    for raw_path in include_paths:
        if not raw_path:
            continue
        path = Path(raw_path)
        if not path.is_absolute():
            path = (ROOT_DIR / raw_path).resolve()
        if not path.exists():
            continue
        bundle = _load_bundle(path)
        resolved_defs.update(bundle)

    for inline in inline_defs:
        if not isinstance(inline, dict):
            continue
        definition = _coerce_guard_definition(inline)
        if definition.id and definition.type:
            resolved_defs[definition.id] = definition

    for guard_id, override in overrides.items():
        if guard_id not in resolved_defs or not isinstance(override, dict):
            continue
        definition = resolved_defs[guard_id]
        if "parameters" in override and isinstance(override["parameters"], dict):
            definition.parameters.update(override["parameters"])
        if "templates" in override and isinstance(override["templates"], dict):
            definition.templates.update({k: v for k, v in override["templates"].items() if v})

    final_defs = {
        guard_id: definition
        for guard_id, definition in resolved_defs.items()
        if definition.id and _should_enable(definition, config)
    }

    if not final_defs:
        return None

    return GuardrailManager(final_defs, config)
