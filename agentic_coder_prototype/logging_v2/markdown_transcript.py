from __future__ import annotations


class MarkdownTranscriptWriter:
    def system(self, text: str) -> str:
        return f"\n\n**System**\n\n{text}\n"

    def user(self, text: str) -> str:
        return f"\n\n**User**\n\n{text}\n"

    def assistant(self, text: str) -> str:
        return f"\n\n**Assistant**\n\n{text}\n"

    def provider_tool_calls(self, summary: str, path: str) -> str:
        return f"\n\n**Provider Tool Calls**\n\n{summary}\n\n(see {path})\n"

    def provider_tool_results(self, summary: str, path: str) -> str:
        return f"\n\n**Provider Tool Results**\n\n{summary}\n\n(see {path})\n"

    def provider_tools_provided(self, tools: list, path: str) -> str:
        tool_list = "\n".join(str(t) for t in tools)
        return f"\n\n**Provider Tools Provided**\n\n{tool_list}\n\n(see {path})\n"

    def tools_available_temp(self, note: str, path: str) -> str:
        return f"\n\n**Tools Available**\n\n{note}\n\n(see {path})\n"
