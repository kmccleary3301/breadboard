from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


class PlanBootstrapper:
    """Deterministic todo board seeding when providers skip plan tools."""

    def __init__(self, config: Dict[str, Any]) -> None:
        guardrails_cfg = dict((config.get("guardrails") or {}))
        pb_cfg = dict(guardrails_cfg.get("plan_bootstrap") or {})
        self.strategy = str(pb_cfg.get("strategy") or "never").lower()
        self.max_turns = int(pb_cfg.get("max_turns") or 1)
        self.warmup_turns = int(pb_cfg.get("warmup_turns") or 1)
        self.seed_spec = pb_cfg.get("seed")
        seed_file = pb_cfg.get("seed_file")
        if seed_file and not self.seed_spec:
            path = Path(seed_file)
            if not path.is_absolute():
                path = (REPO_ROOT / seed_file).resolve()
            try:
                self.seed_spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception:
                self.seed_spec = None
        self.config = {
            "strategy": self.strategy,
            "max_turns": self.max_turns,
            "warmup_turns": self.warmup_turns,
        }

    def is_enabled(self) -> bool:
        return self.strategy in {"soft", "hard"} and bool(self.seed_spec)

    def should_seed(self, session_state: Any) -> bool:
        if not self.is_enabled():
            return False
        if session_state.get_provider_metadata("replay_mode"):
            return False
        # If the workspace already has a populated TODO board (e.g., when reusing a
        # preserved workspace across runs), avoid reseeding and duplicating items.
        try:
            manager = session_state.get_todo_manager()
            snapshot = manager.snapshot() if manager else None
            todos = snapshot.get("todos", []) if isinstance(snapshot, dict) else []
            if todos:
                session_state.set_provider_metadata("todo_seed_completed", True)
                session_state.set_provider_metadata("plan_bootstrap_seeded", True)
                return False
        except Exception:
            pass
        if session_state.get_provider_metadata("plan_bootstrap_seeded"):
            return False
        if session_state.get_provider_metadata("plan_bootstrap_failed"):
            return False
        if session_state.get_provider_metadata("todo_seed_completed"):
            return False
        if session_state.get_provider_metadata("plan_mode_disabled"):
            return False
        turn_index = session_state.get_provider_metadata("current_turn_index")
        if not isinstance(turn_index, int):
            return False
        if self.strategy == "soft":
            if turn_index < max(self.max_turns, 1):
                return False
            if not session_state.get_provider_metadata("plan_bootstrap_event_emitted"):
                return False
        if self.strategy == "hard" and turn_index < 1:
            return False
        return True

    def build_calls(self) -> List[SimpleNamespace]:
        if not self.seed_spec:
            return []
        calls: List[SimpleNamespace] = []
        payload = self._build_write_board_payload()
        if payload:
            call_id = f"plan_bootstrap_{uuid.uuid4().hex[:8]}"
            calls.append(
                SimpleNamespace(
                    function="todo.write_board",
                    arguments=payload,
                    provider_name="todo.write_board",
                    call_id=call_id,
                )
            )
        extra_calls = self.seed_spec.get("extra_calls") if isinstance(self.seed_spec, dict) else None
        if isinstance(extra_calls, list):
            for idx, entry in enumerate(extra_calls):
                if not isinstance(entry, dict):
                    continue
                fn = str(entry.get("function") or "").strip()
                args = entry.get("arguments")
                if not fn:
                    continue
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                if not isinstance(args, dict):
                    args = {}
                calls.append(
                    SimpleNamespace(
                        function=fn,
                        arguments=args,
                        provider_name=fn,
                        call_id=f"plan_bootstrap_extra_{idx}",
                    )
                )
        return calls

    def _build_write_board_payload(self) -> Optional[Dict[str, Any]]:
        if not isinstance(self.seed_spec, dict):
            return None
        write_board_spec = self.seed_spec.get("write_board")
        if isinstance(write_board_spec, dict) and isinstance(write_board_spec.get("todos"), list):
            return {
                "todos": write_board_spec["todos"],
            }
        todos = self.seed_spec.get("todos")
        if isinstance(todos, list):
            return {"todos": todos}
        return None
