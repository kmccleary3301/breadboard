from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

import ray

from lsp_manager import LSPManager
from sandbox_v2 import DevSandboxV2


def _now_ms() -> int:
    return int(time.time() * 1000)


@ray.remote
class OpenCodeAgent:
    """
    Minimal agent session actor that executes tool-call style parts via DevSandboxV2
    and collects diagnostics from LSPManager after edits/patches.

    This is an orchestration skeleton; LLM integration to be added later.
    """

    def __init__(
        self,
        workspace: str,
        sandbox_image: str = "python-dev:latest",
        network: str = "none",
    ) -> None:
        self.session_id = str(uuid.uuid4())
        self.time_created = _now_ms()
        # Create LSP and Sandbox actors
        self.lsp = LSPManager.remote()
        ray.get(self.lsp.register_root.remote(workspace))
        # Create sandbox bound to LSP, local-host fallback is controlled by env in factory, here we call actor directly
        self.sandbox = DevSandboxV2.options(name=f"sb-{self.session_id}").remote(
            image=sandbox_image, workspace=workspace, lsp_actor=self.lsp
        )
        self.messages: List[Dict[str, Any]] = []
        # storage placeholders
        self.storage_root: Optional[str] = None

    def get_session_info(self) -> Dict[str, Any]:
        return {
            "id": self.session_id,
            "created": self.time_created,
            "messages": len(self.messages),
        }

    def run_message(self, parts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Execute a message comprised of parts. Supported part types:
          - {type: 'text', text: str}
          - {type: 'tool_call', name: str, args: dict}
        Returns a response with parts, where tool results appear as {type: 'tool_result', name, output, metadata}.
        """
        response_parts: List[Dict[str, Any]] = []
        for p in parts:
            if p.get("type") == "text":
                response_parts.append({"type": "echo", "text": p.get("text", "")})
                continue
            if p.get("type") == "tool_call":
                name = p.get("name")
                args = p.get("args", {})
                result = self._execute_tool(name, args)
                response_parts.append({"type": "tool_result", "name": name, **result})
                continue
        msg = {"time": _now_ms(), "request": parts, "response": response_parts}
        self.messages.append(msg)
        return msg

    # Storage integration (optional)
    def enable_storage(self, storage_root: str) -> None:
        from storage import JSONStorage

        self.storage_root = storage_root
        self.store = JSONStorage(storage_root)

    def persist_message(self, message: Dict[str, Any]) -> None:
        if not getattr(self, "store", None):
            return
        rel = f"session/{self.session_id}/messages/{message['time']}.json"
        self.store.write_json(rel, message)

    def _execute_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatch to sandbox methods and optionally collect diagnostics."""
        # File operations
        if name == "write_text":
            out = ray.get(self.sandbox.write_text.remote(args["path"], args.get("content", "")))
            diags = ray.get(self.lsp.diagnostics.remote())
            return {"output": "", "metadata": {"write": out, "diagnostics": diags}}
        if name == "edit_replace":
            out = ray.get(
                self.sandbox.edit_replace.remote(
                    args["path"], args.get("old", ""), args.get("new", ""), int(args.get("count", 1))
                )
            )
            diags = ray.get(self.lsp.diagnostics.remote())
            return {"output": "", "metadata": {"edit": out, "diagnostics": diags}}
        if name == "multiedit":
            out = ray.get(self.sandbox.multiedit.remote(list(args.get("edits", []))))
            diags = ray.get(self.lsp.diagnostics.remote())
            return {"output": "", "metadata": {"edits": out, "diagnostics": diags}}
        # Search
        if name == "grep":
            out = ray.get(self.sandbox.grep.remote(args.get("pattern", ""), args.get("path", "."), args.get("include")))
            return {"output": out, "metadata": {}}
        # VCS/patch
        if name == "apply_patch":
            out = ray.get(
                self.sandbox.vcs.remote(
                    {
                        "action": "apply_patch",
                        "params": {
                            "patch": args.get("patch", ""),
                            "three_way": bool(args.get("three_way", True)),
                            "index": bool(args.get("index", True)),
                            "whitespace": args.get("whitespace", "fix"),
                            "reverse": bool(args.get("reverse", False)),
                            "keep_rejects": True,
                        },
                    }
                )
            )
            diags = ray.get(self.lsp.diagnostics.remote())
            return {"output": out, "metadata": {"diagnostics": diags}}
        # Fallback
        return {"output": {"error": f"unknown tool: {name}"}, "metadata": {}}


