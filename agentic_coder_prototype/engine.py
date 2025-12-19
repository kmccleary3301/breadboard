from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Protocol, Tuple

from .agent import AgenticCoder, create_agent
from .parity import RunIR, build_run_ir_from_run_dir


class AgentEngine(Protocol):
    """
    Abstract engine interface for running a task and obtaining a RunIR.

    This protocol is intentionally minimal: higher-level callers can use the
    RunIR for parity checks, reporting, or regression analysis without needing
    to know about the underlying conductor or logging implementation.
    """

    def run(
        self,
        task: str,
        *,
        max_iterations: Optional[int] = None,
        stream: bool = False,
        replay_session: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], RunIR]:
        """
        Execute a task and return (raw_result, RunIR).

        - raw_result is the same dict produced by AgenticCoder.run_task
        - RunIR is built from the run's logging directory.
        """
        ...


class RayAgentEngine:
    """
    Concrete AgentEngine implementation that delegates to AgenticCoder and
    builds a RunIR from the run's logging directory.

    This keeps the RunIR path consistent with existing parity tooling while
    exposing a direct engine API for future callers.
    """

    def __init__(
        self,
        config_path: str,
        workspace_dir: Optional[str] = None,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.config_path = config_path
        self.workspace_dir = workspace_dir
        self.overrides = overrides or {}
        self._agent: Optional[AgenticCoder] = None

    def _ensure_agent(self) -> AgenticCoder:
        if self._agent is None:
            self._agent = create_agent(self.config_path, self.workspace_dir, overrides=self.overrides)
        return self._agent

    def run(
        self,
        task: str,
        *,
        max_iterations: Optional[int] = None,
        stream: bool = False,
        replay_session: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], RunIR]:
        agent = self._ensure_agent()
        result = agent.run_task(
            task,
            max_iterations,
            stream=stream,
            replay_session=replay_session,
        )
        if not isinstance(result, dict):
            raise RuntimeError("Agent run did not return a dict result.")

        run_dir_str = result.get("run_dir") or result.get("logging_dir")
        if not run_dir_str:
            raise RuntimeError("Agent result does not contain a run_dir/logging_dir for RunIR construction.")
        run_dir = Path(run_dir_str).resolve()

        # Build RunIR using existing parity helper, keeping behavior consistent
        actual_ir = build_run_ir_from_run_dir(run_dir)
        return result, actual_ir


def create_engine(
    config_path: str,
    workspace_dir: Optional[str] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> AgentEngine:
    """
    Factory for creating an AgentEngine backed by the Ray-based conductor.

    This mirrors the create_agent entrypoint for AgenticCoder.
    """
    return RayAgentEngine(config_path, workspace_dir, overrides=overrides)

