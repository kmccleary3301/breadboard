"""Explicit internal client for the ATP adapter pending C1 removal.

This module is not exported from :mod:`breadboard_sdk` and is not part of the
ordinary product client surface. Raw session operations use the internal
namespace and never overlap public session routes.
"""
from __future__ import annotations

from typing import Any, Dict

from .client import BreadBoardClient


class CompatibilityBreadboardClient(BreadBoardClient):
    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/health")

    def create_session(
        self,
        *,
        config_path: str | None = None,
        task: str = "",
        metadata: Dict[str, Any] | None = None,
        workspace: str | None = None,
        max_steps: int | None = None,
        permission_mode: str | None = None,
        stream: bool = True,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"task": task, "stream": bool(stream)}
        if config_path is not None:
            payload["config_path"] = config_path
        if metadata:
            payload["metadata"] = dict(metadata)
        if workspace:
            payload["workspace"] = workspace
        if max_steps is not None:
            payload["max_steps"] = int(max_steps)
        if permission_mode:
            payload["permission_mode"] = permission_mode
        return self._request("POST", "/v1/internal/sessions", body=payload)

    def post_command(self, session_id: str, *, command: str, payload: Dict[str, Any] | None = None) -> None:
        self._request("POST", f"/v1/internal/sessions/{session_id}/command", body={"command": command, "payload": payload or {}})
