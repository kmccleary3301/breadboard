from __future__ import annotations

from typing import Any, Dict, List


class PromptArtifactLogger:
    def __init__(self, logger_v2) -> None:
        self.logger_v2 = logger_v2

    def save_compiled_system(self, text: str) -> str:
        if not getattr(self.logger_v2, "run_dir", None):
            return ""
        return self.logger_v2.write_text("prompts/system_compiled.md", text)

    def save_per_turn(self, turn_index: int, text: str) -> str:
        if not getattr(self.logger_v2, "run_dir", None):
            return ""
        return self.logger_v2.write_text(f"prompts/per_turn/turn_{turn_index}.md", text)

    def save_catalog(self, name: str, files: List[Dict[str, Any]]) -> str:
        if not getattr(self.logger_v2, "run_dir", None):
            return ""
        payload = {"catalog": name, "files": files}
        return self.logger_v2.write_json(f"prompts/catalogs/{name}/manifest.json", payload)
