"""
Markdown logging for agentic coding sessions
"""

from pathlib import Path
from typing import Any, Dict, List, Optional


class MarkdownLogger:
    """Handles markdown logging for session debugging and review"""
    
    def __init__(self, output_path: Optional[str] = None):
        self.output_path = output_path
    
    def append_sections(self, sections: List[Dict[str, str]]) -> None:
        """
        Append sections to a markdown log file. Each section is {"role": label, "content": text}.
        Renders as bold role headings with blank lines around.
        """
        if not self.output_path:
            return
        
        try:
            outp = Path(self.output_path)
            outp.parent.mkdir(parents=True, exist_ok=True)
            with outp.open("a", encoding="utf-8") as f:
                for sec in sections:
                    role = sec.get("role", "")
                    content = sec.get("content", "")
                    if not isinstance(content, str):
                        content = str(content)
                    f.write("\n\n**" + role + "**\n\n")
                    f.write(content.rstrip("\n") + "\n")
        except Exception:
            # Best-effort only; ignore logging errors
            pass
    
    def log_system_message(self, content: str):
        """Log a system message"""
        self.append_sections([{"role": "System", "content": content}])
    
    def log_user_message(self, content: str):
        """Log a user message"""
        self.append_sections([{"role": "User", "content": content}])
    
    def log_assistant_message(self, content: str):
        """Log an assistant message"""
        self.append_sections([{"role": "Assistant", "content": content}])
    
    def log_tool_availability(self, tool_names: List[str]):
        """Log available tools in a concise format"""
        tools_content = f"<TOOLS_AVAILABLE>\n{chr(10).join(tool_names)}\n</TOOLS_AVAILABLE>"
        self.log_system_message(tools_content)
    
    def clear_log(self):
        """Clear the markdown log file"""
        if not self.output_path:
            return
        
        try:
            outp = Path(self.output_path)
            if outp.exists():
                outp.unlink()
        except Exception:
            pass
