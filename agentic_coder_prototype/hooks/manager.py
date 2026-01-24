from __future__ import annotations

import importlib
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..extensions import ExtensionPhase, MiddlewareSpec, order_middleware
from ..extensions.registry import ExtensionRegistry
from ..extensions.spine import PHASE_ORDER, normalize_phase
from .ctree_context import CTreeContextHook
from .ctree_context_engine import CTreeContextEngineHook
from .ctree_recorder import CTreeRecorderHook
from .model import HookResult

logger = logging.getLogger(__name__)

LEGACY_PHASE_MAP: Dict[str, str] = {
    "pre_turn": ExtensionPhase.ON_TURN_START.value,
    "post_turn": ExtensionPhase.ON_TURN_END.value,
    "pre_tool": ExtensionPhase.BEFORE_TOOL.value,
    "post_tool": ExtensionPhase.AFTER_TOOL.value,
    "completion": ExtensionPhase.ON_SESSION_END.value,
    "ctree_record": ExtensionPhase.BEFORE_EMIT.value,
    # Canonical phases may be used verbatim by hooks.
    ExtensionPhase.ON_SESSION_INIT.value: ExtensionPhase.ON_SESSION_INIT.value,
    ExtensionPhase.ON_TURN_START.value: ExtensionPhase.ON_TURN_START.value,
    ExtensionPhase.BEFORE_MODEL.value: ExtensionPhase.BEFORE_MODEL.value,
    ExtensionPhase.AFTER_MODEL.value: ExtensionPhase.AFTER_MODEL.value,
    ExtensionPhase.BEFORE_TOOL.value: ExtensionPhase.BEFORE_TOOL.value,
    ExtensionPhase.AFTER_TOOL.value: ExtensionPhase.AFTER_TOOL.value,
    ExtensionPhase.BEFORE_EMIT.value: ExtensionPhase.BEFORE_EMIT.value,
    ExtensionPhase.ON_TURN_END.value: ExtensionPhase.ON_TURN_END.value,
    ExtensionPhase.ON_SESSION_END.value: ExtensionPhase.ON_SESSION_END.value,
}


def _normalize_phase(phase: str) -> str:
    return str(phase or "").strip()


@dataclass(frozen=True)
class HookEntry:
    hook: Any
    legacy_phase: str
    canonical_phase: str
    spec: MiddlewareSpec


class HookManager:
    def __init__(
        self,
        hooks: Sequence[Any],
        *,
        phase_map: Optional[Dict[str, str]] = None,
        collect_decisions: bool = False,
    ) -> None:
        self._phase_map = dict(phase_map or LEGACY_PHASE_MAP)
        self._entries: List[HookEntry] = []
        self._ordered: Dict[str, List[Any]] = {}
        self._unmapped_phases: List[str] = []
        self._collect_decisions = bool(collect_decisions)
        self._decision_log: List[Any] = []
        self._build_entries(list(hooks))
        self._build_ordering()

    def _map_phase(self, legacy_phase: str) -> str:
        if legacy_phase in self._phase_map:
            return self._phase_map[legacy_phase]
        if legacy_phase not in self._unmapped_phases:
            self._unmapped_phases.append(legacy_phase)
        return ExtensionPhase.BEFORE_EMIT.value

    def _build_entries(self, hooks: Iterable[Any]) -> None:
        for hook in hooks:
            phases = getattr(hook, "phases", None) or []
            for legacy in phases:
                legacy_phase = _normalize_phase(str(legacy))
                canonical_phase = self._map_phase(legacy_phase)
                hook_id = str(getattr(hook, "hook_id", "") or hook.__class__.__name__).strip()
                owner = str(getattr(hook, "owner", "") or "engine").strip()
                priority = int(getattr(hook, "priority", 100) or 100)
                before = tuple(getattr(hook, "before", ()) or ())
                after = tuple(getattr(hook, "after", ()) or ())
                spec = MiddlewareSpec(
                    middleware_id=hook_id,
                    plugin_id=owner,
                    phase=canonical_phase,
                    priority=priority,
                    before=before,
                    after=after,
                    handler=hook,
                )
                self._entries.append(HookEntry(hook=hook, legacy_phase=legacy_phase, canonical_phase=canonical_phase, spec=spec))

    def _build_ordering(self) -> None:
        grouped: Dict[str, List[MiddlewareSpec]] = {}
        for entry in self._entries:
            grouped.setdefault(entry.legacy_phase, []).append(entry.spec)

        for legacy_phase, specs in grouped.items():
            ordered_specs = order_middleware(specs) if specs else []
            self._ordered[legacy_phase] = [spec.handler for spec in ordered_specs if spec.handler is not None]

    def snapshot(self) -> Dict[str, Any]:
        hook_entries = []
        for entry in self._entries:
            hook_entries.append(
                {
                    "hook_id": entry.spec.middleware_id,
                    "owner": entry.spec.plugin_id,
                    "legacy_phase": entry.legacy_phase,
                    "canonical_phase": entry.canonical_phase,
                    "priority": entry.spec.priority,
                    "before": list(entry.spec.before),
                    "after": list(entry.spec.after),
                }
            )
        ordered = {phase: [getattr(hook, "hook_id", hook.__class__.__name__) for hook in hooks]
                   for phase, hooks in self._ordered.items()}
        snapshot = {
            "hooks": hook_entries,
            "ordered": ordered,
        }
        if self._unmapped_phases:
            snapshot["unmapped_phases"] = list(self._unmapped_phases)
        return snapshot

    def decision_log(self) -> List[Any]:
        return list(self._decision_log)

    def _capture_decisions(self, result: HookResult) -> None:
        if not self._collect_decisions:
            return
        payload = getattr(result, "payload", None)
        if not isinstance(payload, dict):
            return
        items = payload.get("policy_decisions") or payload.get("decisions")
        if not isinstance(items, list):
            return
        for item in items:
            self._decision_log.append(item)

    def run(
        self,
        phase: str,
        payload: Any,
        *,
        session_state: Optional[Any] = None,
        turn: Optional[int] = None,
        hook_executor: Optional[Any] = None,
    ) -> HookResult:
        phase_key = _normalize_phase(phase)
        hooks = self._ordered.get(phase_key, [])
        if not hooks:
            return HookResult(action="allow")

        current_payload = payload
        last_transform: Optional[HookResult] = None
        for hook in hooks:
            result = hook.run(
                phase_key,
                current_payload,
                session_state=session_state,
                turn=turn,
                hook_executor=hook_executor,
            )
            try:
                if isinstance(result, HookResult):
                    self._capture_decisions(result)
            except Exception:
                pass
            action = getattr(result, "action", "allow")
            if action == "deny":
                return result
            if action == "transform":
                if isinstance(result, HookResult):
                    last_transform = result
                if isinstance(getattr(result, "payload", None), dict):
                    current_payload = result.payload

        return last_transform or HookResult(action="allow")


def _hooks_enabled(config: Dict[str, Any] | None) -> bool:
    cfg = dict(config or {})
    hooks_cfg = cfg.get("hooks")
    if isinstance(hooks_cfg, dict) and hooks_cfg.get("enabled") is True:
        return True
    return False


def _load_hook_target(path: str) -> Any:
    spec = str(path or "").strip()
    if not spec:
        raise ValueError("Hook import path is empty")
    module_name: str
    attr_name: str
    if ":" in spec:
        module_name, attr_name = spec.split(":", 1)
    else:
        module_name, _, attr_name = spec.rpartition(".")
    module_name = (module_name or "").strip()
    attr_name = (attr_name or "").strip()
    if not module_name or not attr_name:
        raise ValueError(f"Invalid hook import path: {spec}")
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)


def _instantiate_hook(target: Any, *, kwargs: Optional[Dict[str, Any]] = None) -> Any:
    payload = dict(kwargs or {})
    if isinstance(target, type):
        return target(**payload) if payload else target()
    if callable(target):
        return target(**payload) if payload else target()
    return target


def _load_extra_hooks(hooks_cfg: Dict[str, Any], workspace: str) -> List[Any]:
    items = hooks_cfg.get("extra_hooks")
    if items is None:
        return []
    if not isinstance(items, list):
        logger.warning("hooks.extra_hooks must be a list")
        return []

    workspace_path = Path(str(workspace)).resolve()
    added_sys_path = False
    if str(workspace_path) and str(workspace_path) not in sys.path:
        sys.path.insert(0, str(workspace_path))
        added_sys_path = True

    hooks: List[Any] = []
    try:
        for entry in items:
            kwargs: Dict[str, Any] = {}
            enabled = True
            path = None
            if isinstance(entry, str):
                path = entry
            elif isinstance(entry, dict):
                enabled = bool(entry.get("enabled", True))
                path = entry.get("path") or entry.get("import") or entry.get("ref")
                raw_kwargs = entry.get("kwargs") or {}
                if isinstance(raw_kwargs, dict):
                    kwargs = dict(raw_kwargs)
            if not enabled or not isinstance(path, str) or not path.strip():
                continue
            try:
                target = _load_hook_target(path)
                hook = _instantiate_hook(target, kwargs=kwargs)
                hooks.append(hook)
            except Exception as exc:
                logger.warning("Failed to load hook %s: %s", path, exc)
                continue
    finally:
        if added_sys_path:
            try:
                sys.path.remove(str(workspace_path))
            except ValueError:
                pass
    return hooks


def _load_plugin_hooks(
    hooks_cfg: Dict[str, Any],
    plugin_manifests: Sequence[Any],
) -> List[Any]:
    if not plugin_manifests:
        return []
    if not hooks_cfg.get("allow_plugin_hooks"):
        return []
    allow_untrusted = bool(hooks_cfg.get("allow_untrusted_plugins") or hooks_cfg.get("allow_untrusted_hooks"))
    hooks: List[Any] = []
    for manifest in plugin_manifests:
        plugin_id = str(getattr(manifest, "plugin_id", "") or "").strip()
        trusted = bool(getattr(manifest, "trusted", False))
        if not trusted and not allow_untrusted:
            continue
        entries = getattr(manifest, "hooks", None)
        if not isinstance(entries, list):
            continue
        root = getattr(manifest, "root", None)
        root_path = Path(str(root)).resolve() if root else None
        added_sys_path = False
        if root_path and str(root_path) and str(root_path) not in sys.path:
            sys.path.insert(0, str(root_path))
            added_sys_path = True
        try:
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                enabled = bool(entry.get("enabled", True))
                if not enabled:
                    continue
                path = entry.get("path") or entry.get("import") or entry.get("ref")
                if not isinstance(path, str) or not path.strip():
                    continue
                kwargs = entry.get("kwargs") if isinstance(entry.get("kwargs"), dict) else {}
                try:
                    target = _load_hook_target(path)
                    hook = _instantiate_hook(target, kwargs=kwargs)
                except Exception as exc:
                    logger.warning("Failed to load plugin hook %s (%s): %s", path, plugin_id, exc)
                    continue

                hook_id = entry.get("id") or entry.get("hook_id") or entry.get("hookId")
                phase = entry.get("phase") or entry.get("phases")
                priority = entry.get("priority")
                before = entry.get("before")
                after = entry.get("after")

                if hook_id and hasattr(hook, "hook_id"):
                    setattr(hook, "hook_id", str(hook_id))
                elif hook_id:
                    setattr(hook, "hook_id", str(hook_id))

                if plugin_id:
                    setattr(hook, "owner", plugin_id)

                if phase:
                    if isinstance(phase, (list, tuple)):
                        setattr(hook, "phases", [str(item) for item in phase])
                    else:
                        setattr(hook, "phases", [str(phase)])
                if priority is not None:
                    try:
                        setattr(hook, "priority", int(priority))
                    except Exception:
                        pass
                if before is not None:
                    if isinstance(before, (list, tuple)):
                        setattr(hook, "before", tuple(str(item) for item in before))
                    else:
                        setattr(hook, "before", tuple([str(before)]))
                if after is not None:
                    if isinstance(after, (list, tuple)):
                        setattr(hook, "after", tuple(str(item) for item in after))
                    else:
                        setattr(hook, "after", tuple([str(after)]))
                hooks.append(hook)
        finally:
            if added_sys_path:
                try:
                    sys.path.remove(str(root_path))
                except ValueError:
                    pass
    return hooks


def build_hook_manager(config: Dict[str, Any], workspace: str, *, plugin_manifests: Optional[List[Any]] = None) -> Any:
    if not _hooks_enabled(config):
        return None

    hooks: List[Any] = []
    hooks_cfg = (config or {}).get("hooks") if isinstance(config, dict) else None
    if not isinstance(hooks_cfg, dict):
        hooks_cfg = {}

    if hooks_cfg.get("ctree_recorder", True):
        hooks.append(CTreeRecorderHook())
    if hooks_cfg.get("ctree_context", False):
        hooks.append(CTreeContextHook())
    if hooks_cfg.get("ctree_context_engine", False):
        hooks.append(CTreeContextEngineHook())

    hooks.extend(_load_extra_hooks(hooks_cfg, workspace))

    if plugin_manifests:
        hooks.extend(_load_plugin_hooks(hooks_cfg, plugin_manifests))

    # Plugin-provided hooks are not wired yet (future).
    collect_decisions = bool(hooks_cfg.get("collect_decisions", False))
    return HookManager(hooks, collect_decisions=collect_decisions)


class _RegistryHook:
    def __init__(self, spec: MiddlewareSpec) -> None:
        self.hook_id = spec.middleware_id
        self.owner = spec.plugin_id
        self.phases = (normalize_phase(spec.phase),)
        self.priority = spec.priority
        self.before = spec.before
        self.after = spec.after

    def run(self, phase, payload, **kwargs):  # type: ignore[no-untyped-def]
        return HookResult(action="allow", payload={"phase": phase})


def build_hook_manager_from_registry(
    registry: ExtensionRegistry,
    *,
    collect_decisions: bool = False,
) -> HookManager:
    """Dry-run bridge from ExtensionRegistry to HookManager for ordering verification."""
    phase_map = {phase: phase for phase in PHASE_ORDER}
    hooks = [_RegistryHook(spec) for spec in registry.list_all()]
    return HookManager(hooks, phase_map=phase_map, collect_decisions=collect_decisions)
