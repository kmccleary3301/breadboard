from __future__ import annotations

import fnmatch
import json
import re
from typing import Any, Callable, ClassVar, Dict, List, Optional, Sequence, Tuple

from ..core.core import ToolCallParsed, ToolDefinition
from ..dialects.aider_diff import AiderDiffDialect
from ..dialects.bash_block import BashBlockDialect
from ..dialects.opencode_patch import OpenCodePatchDialect
from ..dialects.pythonic02 import Pythonic02Dialect
from ..dialects.pythonic_inline import PythonicInlineDialect
from ..dialects.unified_diff import UnifiedDiffDialect
from ..dialects.yaml_command import YAMLCommandDialect


DialectFactory = Callable[[], Any]


class DialectManager:
    """Authoritative catalog and selector for text tool-calling dialects.

    The factory table is intentionally ordered. Its order is the default
    runtime and prompt order, while configuration only reorders the names
    selected from this catalog.
    """

    _DIALECT_FACTORIES: ClassVar[Tuple[Tuple[str, DialectFactory], ...]] = (
        ("pythonic02", Pythonic02Dialect),
        ("pythonic_inline", PythonicInlineDialect),
        ("bash_block", BashBlockDialect),
        ("aider_diff", AiderDiffDialect),
        ("unified_diff", UnifiedDiffDialect),
        ("opencode_patch", OpenCodePatchDialect),
        ("yaml_command", YAMLCommandDialect),
    )
    _DIALECT_ALIASES: ClassVar[Dict[str, str]] = {
        "pythonic": "pythonic02",
    }
    _FORMAT_PREFERENCE: ClassVar[Tuple[str, ...]] = (
        "aider_diff",
        "opencode_patch",
        "unified_diff",
        "pythonic02",
        "bash_block",
        "pythonic_inline",
    )

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config or {}

    @classmethod
    def dialect_names(cls) -> List[str]:
        """Return the stable default catalog order."""
        return [name for name, _ in cls._DIALECT_FACTORIES]

    @classmethod
    def create_dialect_mapping(cls) -> Dict[str, Any]:
        """Create fresh dialect instances in the catalog's stable order."""
        return {name: factory() for name, factory in cls._DIALECT_FACTORIES}

    @classmethod
    def resolve_dialect_name(cls, name: Any) -> Optional[str]:
        """Resolve a configured name or alias to its canonical catalog name."""
        if not name:
            return None
        canonical = str(name)
        return cls._DIALECT_ALIASES.get(canonical, canonical)

    @classmethod
    def dialects_for_names(cls, names: Optional[Sequence[str]] = None) -> List[Any]:
        """Return fresh instances for names, preserving requested order."""
        lookup = cls.create_dialect_mapping()
        if not names:
            return list(lookup.values())

        selected: List[Any] = []
        seen: set[str] = set()
        for name in names:
            canonical = cls.resolve_dialect_name(name)
            if canonical is None or canonical in seen:
                continue
            dialect = lookup.get(canonical)
            if dialect is None:
                continue
            selected.append(dialect)
            seen.add(canonical)
        return selected

    @classmethod
    def build_prompt(
        cls,
        tools: List[ToolDefinition],
        dialect_names: Optional[Sequence[str]] = None,
    ) -> str:
        """Render the selected dialect sections in catalog/requested order."""
        sections: List[str] = []
        for dialect in cls.dialects_for_names(dialect_names):
            dialect_prompt = dialect.prompt_for_tools(tools)
            if dialect_prompt.strip():
                sections.append(dialect_prompt)
                sections.append("")
        return "\n".join(sections)

    @classmethod
    def parse_calls(
        cls,
        text: str,
        tool_defs: List[ToolDefinition],
        dialect_names: Optional[Sequence[str]] = None,
    ) -> List[ToolCallParsed]:
        """Parse calls using the same selected catalog instances as prompts."""
        all_calls: List[ToolCallParsed] = []
        seen: set[str] = set()
        for dialect in cls.dialects_for_names(dialect_names):
            subset = [tool for tool in tool_defs if tool.type_id == dialect.type_id]
            for call in dialect.parse_calls(text, subset):
                try:
                    key = f"{call.function}|" + json.dumps(
                        call.arguments,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                except Exception:
                    key = f"{call.function}|{str(call.arguments)}"
                if key in seen:
                    continue
                seen.add(key)
                if getattr(call, "dialect", None) is None:
                    call.dialect = cls._dialect_identifier(dialect)
                all_calls.append(call)
        return all_calls

    @staticmethod
    def _dialect_identifier(dialect: Any) -> str:
        identifier = getattr(dialect, "dialect_id", None)
        if identifier:
            return str(identifier)
        if hasattr(dialect, "type_id") and isinstance(dialect.type_id, str):
            base = dialect.type_id
        else:
            base = dialect.__class__.__name__
        name = base.replace("Dialect", "")
        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
        return snake.strip("_") or base.lower()

    def get_dialects_for_model(
        self,
        model_id: str,
        available: Optional[Sequence[str]] = None,
    ) -> List[str]:
        """Return the configured by-model order for the requested model."""
        available_names = list(available) if available is not None else self.dialect_names()
        try:
            tools_cfg = self.config.get("tools", {}) or {}
            dialects_cfg = tools_cfg.get("dialects", {}) or {}
            selection_cfg = dialects_cfg.get("selection", {}) or {}
            by_model = selection_cfg.get("by_model", {}) or {}
        except Exception:
            return available_names

        if not model_id or not by_model:
            return available_names

        ordered: List[str] = []
        seen: set[str] = set()
        for pattern, names in by_model.items():
            try:
                if not fnmatch.fnmatch(model_id, str(pattern)):
                    continue
            except Exception:
                continue
            for name in names or []:
                canonical = self.resolve_dialect_name(name)
                if canonical in available_names and canonical not in seen:
                    ordered.append(canonical)
                    seen.add(canonical)

        if not ordered:
            return available_names

        remaining = [name for name in available_names if name not in seen]
        return ordered + remaining

    @staticmethod
    def apply_selection_legacy(
        current: List[str],
        model_id: str,
        tool_defs: List[ToolDefinition],
        selection_cfg: Dict[str, Any],
    ) -> List[str]:
        by_model: Dict[str, List[str]] = selection_cfg.get("by_model", {}) or {}
        by_tool_kind: Dict[str, List[str]] = selection_cfg.get("by_tool_kind", {}) or {}

        def ordered_intersection(prefer: List[str], available: List[str]) -> List[str]:
            seen = set()
            out: List[str] = []
            for name in prefer:
                if name in available and name not in seen:
                    out.append(name)
                    seen.add(name)
            return out

        preferred: List[str] = []
        if model_id:
            for pattern, prefer_list in by_model.items():
                try:
                    if fnmatch.fnmatch(model_id, pattern):
                        preferred.extend(
                            ordered_intersection(
                                [
                                    DialectManager.resolve_dialect_name(x) or str(x)
                                    for x in (prefer_list or [])
                                ],
                                current,
                            )
                        )
                except Exception:
                    continue

        present_types = {
            tool.type_id
            for tool in (tool_defs or [])
            if getattr(tool, "type_id", None)
        }
        diff_pref_list = [
            DialectManager.resolve_dialect_name(x) or str(x)
            for x in (by_tool_kind.get("diff", []) or [])
        ]
        bash_pref_list = [
            DialectManager.resolve_dialect_name(x) or str(x)
            for x in (by_tool_kind.get("bash", []) or [])
        ]

        known_diff_names = set(diff_pref_list) | {
            "aider_diff",
            "unified_diff",
            "opencode_patch",
        }
        diff_present = ("diff" in present_types) or any(
            name in current for name in known_diff_names
        )
        bash_present = any(
            name in current for name in (bash_pref_list or ["bash_block"])
        )

        if diff_present and diff_pref_list:
            preferred.extend(ordered_intersection(diff_pref_list, current))
        if bash_present and bash_pref_list:
            preferred.extend(ordered_intersection(bash_pref_list, current))

        seen = set()
        ordered_pref = [name for name in preferred if (name not in seen and not seen.add(name))]
        remaining = [name for name in current if name not in ordered_pref]
        return ordered_pref + remaining

    @staticmethod
    def apply_preference_order(
        base_order: List[str],
        model_id: str,
        tool_defs: List[ToolDefinition],
        preference_cfg: Dict[str, Any],
    ) -> Tuple[List[str], Optional[bool]]:
        available = list(base_order)
        available_set = set(available)
        preferred: List[str] = []
        seen = set()
        native_hint: Optional[bool] = None

        def normalize_entry(entry: Any) -> Tuple[List[str], Optional[bool]]:
            native_override: Optional[bool] = None
            order_values: Any

            if isinstance(entry, dict):
                order_values = entry.get("order")
                if order_values is None and "list" in entry:
                    order_values = entry.get("list")
                native_val = entry.get("native")
                if native_val is not None:
                    native_override = bool(native_val)
            else:
                order_values = entry

            if isinstance(order_values, str):
                raw_items = [order_values]
            elif isinstance(order_values, (list, tuple)):
                raw_items = list(order_values)
            elif order_values is None:
                raw_items = []
            else:
                raw_items = [str(order_values)]

            cleaned: List[str] = []
            for item in raw_items:
                item_str = str(item).strip()
                if not item_str:
                    continue
                if item_str == "provider_native":
                    if native_override is None:
                        native_override = True
                    continue
                canonical = DialectManager.resolve_dialect_name(item_str)
                cleaned.append(canonical or item_str)
            return cleaned, native_override

        def extend(entry: Any) -> None:
            nonlocal native_hint
            order_list, native_override = normalize_entry(entry)
            for name in order_list:
                if name not in available_set:
                    continue
                if name in seen:
                    try:
                        preferred.remove(name)
                    except ValueError:
                        pass
                else:
                    seen.add(name)
                preferred.append(name)
            if native_override is not None and native_hint is None:
                native_hint = native_override

        if model_id:
            for pattern, entry in (preference_cfg.get("by_model", {}) or {}).items():
                try:
                    if fnmatch.fnmatch(model_id, pattern):
                        extend(entry)
                except Exception:
                    continue

        present_types = {
            tool.type_id
            for tool in (tool_defs or [])
            if getattr(tool, "type_id", None)
        }
        available_names = set(available)

        for kind, entry in (preference_cfg.get("by_tool_kind", {}) or {}).items():
            if kind == "diff":
                diff_names = normalize_entry(entry)[0]
                diff_present = ("diff" in present_types) or any(
                    name in available_names for name in diff_names
                )
                if not diff_present:
                    continue
            elif kind == "bash":
                bash_names = normalize_entry(entry)[0]
                bash_present = any(
                    name in available_names for name in (bash_names or ["bash_block"])
                )
                if not bash_present:
                    continue
            extend(entry)

        extend(preference_cfg.get("default"))

        remaining = [name for name in available if name not in seen]
        return preferred + remaining, native_hint

    def select_v2_dialects(
        self,
        current: List[str],
        model_id: str,
        tool_defs: List[ToolDefinition],
    ) -> Tuple[List[str], Optional[bool]]:
        """Apply the complete v2 selection policy to an existing catalog order."""
        tools_cfg = self.config.get("tools", {}) or {}
        dialects_cfg = tools_cfg.get("dialects", {}) or {}
        selection_cfg = dialects_cfg.get("selection", {}) or {}
        base_order = self.apply_selection_legacy(
            current,
            model_id,
            tool_defs,
            selection_cfg,
        )

        preference_cfg = dialects_cfg.get("preference", {}) or {}
        if not preference_cfg:
            return base_order, None
        return self.apply_preference_order(
            base_order,
            model_id,
            tool_defs,
            preference_cfg,
        )

    @classmethod
    def get_preferred_formats(cls, available_dialects: Sequence[str]) -> List[str]:
        """Return format preference ordering without changing unknown names."""
        preference_map = {
            name: index for index, name in enumerate(cls._FORMAT_PREFERENCE, start=1)
        }
        return sorted(
            list(available_dialects),
            key=lambda name: preference_map.get(name, 999),
        )
