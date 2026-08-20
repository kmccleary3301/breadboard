from __future__ import annotations

from typing import Iterable, List, Optional


class TurnRelayer:
    """Encapsulates assistant/user relay patterns for tool execution results."""

    def __init__(self, logger_v2, md_writer) -> None:
        self.logger_v2 = logger_v2
        self.md_writer = md_writer

    def relay_execution_chunks(
        self,
        *,
        chunks: Iterable[str],
        artifact_links: List[str],
        session_state,
        markdown_logger,
        turn_cfg: dict,
    ) -> None:
        flow_strategy = (turn_cfg.get("flow") or "assistant_continuation").lower()
        provider_tool_msg = "\n\n".join(chunks)

        if flow_strategy == "assistant_continuation":
            assistant_msg = {
                "role": "assistant",
                "content": f"\n\nTool execution results:\n{provider_tool_msg}",
            }
            session_state.add_message(assistant_msg)
            try:
                markdown_logger.log_assistant_message(assistant_msg["content"])
            except Exception:
                pass
            self._append_conversation_assistant(assistant_msg["content"], artifact_links)
        else:
            session_state.add_message({"role": "user", "content": provider_tool_msg}, to_provider=True)
            try:
                markdown_logger.log_user_message(provider_tool_msg)
            except Exception:
                pass
            self._append_conversation_artifacts(artifact_links)

    def _append_conversation_assistant(self, message: str, artifact_links: List[str]) -> None:
        if not getattr(self.logger_v2, "run_dir", None):
            return
        try:
            self.logger_v2.append_text("conversation/conversation.md", self.md_writer.assistant(message))
        except Exception:
            pass
        self._append_conversation_artifacts(artifact_links)

    def _append_conversation_artifacts(self, artifact_links: List[str]) -> None:
        if not (artifact_links and getattr(self.logger_v2, "run_dir", None)):
            return
        try:
            self.logger_v2.append_text(
                "conversation/conversation.md",
                self.md_writer.text_tool_results("Tool execution results appended.", artifact_links),
            )
        except Exception:
            pass
