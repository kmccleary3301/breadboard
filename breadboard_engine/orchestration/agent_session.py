from __future__ import annotations

import json
import time
import threading
import uuid

import ray

from breadboard.lsp_manager import LSPManager
from breadboard.sandbox import DevSandboxV2
from ..security import protected_credential_paths, purge_provider_credentials


def _now_ms() -> int:
    return int(time.time() * 1000)


@ray.remote(concurrency_groups={"execution": 1, "control": 4})
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
        protected_paths: Optional[Sequence[str]] = None,
        artifact_store_root: str | None = None,
    ) -> None:
        captured = tuple(
            str(path)
            for path in (
                protected_paths
                if protected_paths is not None
                else protected_credential_paths()
            )
        )
        purge_provider_credentials()
        self.session_id = str(uuid.uuid4())
        self.artifact_store_root = artifact_store_root or f"{workspace}/.breadboard/artifacts"
        self.workspace = workspace
        self.time_created = _now_ms()
        self.lsp = LSPManager.remote(protected_paths=captured)
        ray.get(self.lsp.register_root.remote(workspace))
        self.sandbox = DevSandboxV2.options(name=f"sb-{self.session_id}").remote(
            image=sandbox_image,
            workspace=workspace,
            lsp_actor=self.lsp,
            protected_paths=captured,
        )
        self.messages: List[Dict[str, Any]] = []
        self.storage_root: Optional[str] = None
        self.state = "accepted"
        self.result_payload: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None
        self.invocation_records: Dict[str, str] = {}
        self._state_lock = threading.Lock()

    @ray.method(concurrency_group="control")
    def get_state(self) -> str:
        with self._state_lock:
            return self.state

    @ray.method(concurrency_group="control")
    def get_result(self) -> Optional[Dict[str, Any]]:
        with self._state_lock:
            return (
                dict(self.result_payload)
                if self.result_payload is not None
                else None
            )

    @ray.method(concurrency_group="control")
    def get_invocation_state(self, invocation_id: str) -> str:
        return self.invocation_records.get(invocation_id, "missing")

    @ray.method(concurrency_group="execution")
    def submit_message_once(
        self, invocation_id: str, parts: List[Dict[str, Any]]
    ) -> Dict[str, str]:
        existing = self.invocation_records.get(invocation_id)
        if existing is not None:
            return {"state": existing}
        self.invocation_records[invocation_id] = "accepted"
        try:
            self.invocation_records[invocation_id] = "running"
            self.run_message(parts)
        except BaseException:
            self.invocation_records[invocation_id] = "failed"
            raise
        self.invocation_records[invocation_id] = "completed"
        return {"state": "completed"}

    @ray.method(concurrency_group="control")
    def cancel(self) -> str:
        with self._state_lock:
            self.state = "killed"
            return self.state

    @ray.method(concurrency_group="control")
    def get_session_info(self) -> Dict[str, Any]:
        with self._state_lock:
            return {
                "id": self.session_id,
                "created": self.time_created,
                "messages": len(self.messages),
                "state": self.state,
            }

    @ray.method(concurrency_group="execution")
    def run_message(self, parts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Execute a message comprised of parts. Supported part types:
          - {type: 'text', text: str}
          - {type: 'tool_call', name: str, args: dict}
        Returns a response with parts, where tool results appear as
        {type: 'tool_result', name, output, metadata}.
        """
        with self._state_lock:
            if self.state == "killed":
                raise RuntimeError("agent has been canceled")
            self.state = "running"
        try:
            response_parts: List[Dict[str, Any]] = []
            for p in parts:
                with self._state_lock:
                    if self.state == "killed":
                        raise RuntimeError("agent has been canceled")
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
            from breadboard.product.runtime.artifacts import ArtifactStore
            output = json.dumps(msg, sort_keys=True, separators=(",", ":")).encode()
            artifact = ArtifactStore(self.artifact_store_root).put(
                output, media_type="application/json"
            )
            with self._state_lock:
                if self.state == "killed":
                    raise RuntimeError("agent has been canceled")
                self.messages.append(msg)
                self.result_payload = {"artifact_ref": artifact.as_dict()}
                self.state = "completed"
            return msg
        except BaseException as error:
            self.error = type(error).__name__
            with self._state_lock:
                if self.state != "killed":
                    self.state = "failed"
            raise

    @ray.method(concurrency_group="execution")
    def enable_storage(self, storage_root: str) -> None:
        from breadboard.storage import JSONStorage

        self.storage_root = storage_root
        self.store = JSONStorage(storage_root)

    @ray.method(concurrency_group="execution")
    def persist_message(self, message: Dict[str, Any]) -> None:
        if not getattr(self, "store", None):
            return
        rel = f"session/{self.session_id}/messages/{message['time']}.json"
        self.store.write_json(rel, message)

    def _execute_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatch to sandbox methods and optionally collect diagnostics."""
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
        if name == "grep":
            out = ray.get(self.sandbox.grep.remote(args.get("pattern", ""), args.get("path", "."), args.get("include")))
            return {"output": out, "metadata": {}}
        if name == "apply_patch":
            out = ray.get(
                self.sandbox.vcs.remote(
                    {
                        "action": "apply_patch",
                        "params": {
                            "patch": args.get("patch") or args.get("input") or args.get("patchText") or "",
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
            if isinstance(out, dict):
                out.setdefault("action", "apply_patch")
                output = out
            else:
                output = {"action": "apply_patch", "result": out}
            return {"output": output, "metadata": {"diagnostics": diags}}
        return {"output": {"error": f"unknown tool: {name}"}, "metadata": {}}
