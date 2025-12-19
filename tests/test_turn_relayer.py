from __future__ import annotations

from agentic_coder_prototype.turn_relayer import TurnRelayer


class StubLoggerV2:
    def __init__(self) -> None:
        self.run_dir = "log"
        self.entries = []

    def append_text(self, path: str, content: str) -> None:
        self.entries.append((path, content))


class StubMdWriter:
    def assistant(self, message: str) -> str:
        return f"assistant:{message}"

    def text_tool_results(self, heading: str, links) -> str:
        return f"links:{heading}:{','.join(links)}"


class StubSessionState:
    def __init__(self) -> None:
        self.messages = []

    def add_message(self, message, to_provider: bool = False) -> None:
        self.messages.append((message, to_provider))


class StubMarkdownLogger:
    def __init__(self) -> None:
        self.assistant_logs = []
        self.user_logs = []

    def log_assistant_message(self, message: str) -> None:
        self.assistant_logs.append(message)

    def log_user_message(self, message: str) -> None:
        self.user_logs.append(message)


def test_turn_relayer_assistant_continuation():
    logger_v2 = StubLoggerV2()
    md_writer = StubMdWriter()
    relayer = TurnRelayer(logger_v2, md_writer)
    session = StubSessionState()
    markdown_logger = StubMarkdownLogger()

    relayer.relay_execution_chunks(
        chunks=["chunk-a", "chunk-b"],
        artifact_links=["art1"],
        session_state=session,
        markdown_logger=markdown_logger,
        turn_cfg={"flow": "assistant_continuation"},
    )

    assert session.messages[-1][0]["role"] == "assistant"
    assert markdown_logger.assistant_logs
    # Two entries: assistant text + artifact links
    assert len(logger_v2.entries) == 2


def test_turn_relayer_user_interleaved():
    logger_v2 = StubLoggerV2()
    md_writer = StubMdWriter()
    relayer = TurnRelayer(logger_v2, md_writer)
    session = StubSessionState()
    markdown_logger = StubMarkdownLogger()

    relayer.relay_execution_chunks(
        chunks=["result"],
        artifact_links=["artifacts"],
        session_state=session,
        markdown_logger=markdown_logger,
        turn_cfg={"flow": "user_interleaved"},
    )

    message, to_provider = session.messages[-1]
    assert message["role"] == "user"
    assert to_provider is True
    assert markdown_logger.user_logs
    # Only artifact links appended in conversation
    assert len(logger_v2.entries) == 1
