"""
Enhanced System Prompt Compiler for Tool Calling
Generates and caches comprehensive system prompts implementing design decisions from 08-13-25 report.

Key features:
- Format preference based on research (Aider > OpenCode > Unified)
- Sequential execution patterns
- Assistant message continuation guidance
- Tool blocking constraints
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from fnmatch import fnmatch
from typing import List, Dict, Any, Set, Optional

from ..core.core import ToolDefinition
from ..dialects.pythonic02 import Pythonic02Dialect
from ..dialects.pythonic_inline import PythonicInlineDialect
from ..dialects.bash_block import BashBlockDialect
from ..dialects.aider_diff import AiderDiffDialect
from ..dialects.unified_diff import UnifiedDiffDialect
from ..dialects.opencode_patch import OpenCodePatchDialect
from ..dialects.yaml_command import YAMLCommandDialect
from .tool_prompt_synth import ToolPromptSynthesisEngine


class SystemPromptCompiler:
    """Compiles and caches comprehensive system prompts for tool calling"""
    
    def __init__(self, cache_dir: str = "implementations/tooling_sys_prompts_cached"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # All available dialects keyed by config name
        self.dialect_lookup = {
            "pythonic02": Pythonic02Dialect(),
            "pythonic_inline": PythonicInlineDialect(),
            "bash_block": BashBlockDialect(),
            "aider_diff": AiderDiffDialect(),
            "unified_diff": UnifiedDiffDialect(),
            "opencode_patch": OpenCodePatchDialect(),
            "yaml_command": YAMLCommandDialect(),
        }
        self.all_dialects = list(self.dialect_lookup.values())
        self._dialect_aliases = {
            "pythonic": "pythonic02",
        }
        
        # Tool format categories for the per-turn availability list
        # Ordered by research-based preference (Aider > OpenCode > Unified)
        self.format_categories = {
            "python": "python inside <TOOL_CALL> XML",
            "bash": "bash command inside of <BASH> XML", 
            "aider": "Aider SEARCH/REPLACE Format (PREFERRED - 2.3x success rate)",
            "opencode": "OpenCode Add File format (Good structured alternative)",
            "unified_diff": "Unified Patch Git-like format (Use only if Aider unavailable)"
        }
        
        # Research-based format preferences
        self.format_preferences = [
            "aider_diff",      # Highest success rate (59% vs 26%)
            "opencode_patch",  # Structured but more complex
            "unified_diff"     # Lowest success rate for smaller models
        ]
        
        self.tpsl = ToolPromptSynthesisEngine()

    def _compute_tools_hash(self, tools: List[ToolDefinition], dialects: List[str], primary_prompt: str = "", tool_prompt_mode: str = "system_once") -> str:
        """Compute hash of tools and dialects for caching"""
        # Create normalized representation
        tools_data = []
        for tool in sorted(tools, key=lambda t: t.name):
            tool_data = {
                "name": tool.name,
                "description": tool.description,
                "parameters": [
                    {
                        "name": p.name,
                        "type": p.type,
                        "description": p.description,
                        "default": p.default
                    }
                    for p in sorted(tool.parameters or [], key=lambda p: p.name)
                ],
                "blocking": getattr(tool, 'blocking', False)
            }
            tools_data.append(tool_data)
        
        hash_input = {
            "tools": tools_data,
            "dialects": sorted(dialects),
            "primary_prompt": primary_prompt.strip(),
            "tool_prompt_mode": tool_prompt_mode
        }
        
        hash_str = json.dumps(hash_input, sort_keys=True)
        return hashlib.sha256(hash_str.encode()).hexdigest()[:12]
    
    def _get_next_id(self) -> int:
        """Get next sequential ID for cached prompts"""
        existing_files = list(self.cache_dir.glob("sys_prompt_*.md"))
        if not existing_files:
            return 1
        
        max_id = 0
        for file_path in existing_files:
            try:
                # Extract ID from filename like "sys_prompt_003_a8bd45c7f0.md"
                parts = file_path.stem.split('_')
                if len(parts) >= 3:
                    id_part = parts[2]
                    max_id = max(max_id, int(id_part))
            except (ValueError, IndexError):
                continue
        
        return max_id + 1
    
    def _find_cached_prompt(self, tools_hash: str) -> Optional[Path]:
        """Find existing cached prompt with matching hash"""
        pattern = f"sys_prompt_*_{tools_hash}.md"
        matches = list(self.cache_dir.glob(pattern))
        return matches[0] if matches else None
    
    def _generate_minimal_system_prompt(self, primary_prompt: str, tool_prompt_mode: str) -> str:
        """
        Generate minimal system prompt for per_turn_append mode.
        Based on OpenCode/Crush insights: focus on behavior, not tool definitions.
        """
        prompt_parts = []
        
        # Primary system prompt first (if provided)
        if primary_prompt.strip():
            prompt_parts.append(primary_prompt.strip())
            prompt_parts.append("")
        
        # Based on Crush's approach: concise behavior-focused instructions
        prompt_parts.extend([
            "# ENHANCED TOOL USAGE SYSTEM",
            "",
            "You have access to tools that will be specified in each user message. Focus on:",
            "",
            "## CORE PRINCIPLES (From Production Research)",
            "- **Conciseness**: Answer in fewer than 4 lines unless detail requested",
            "- **Direct execution**: Use tools immediately without excessive explanation", 
            "- **Format preference**: Use Aider SEARCH/REPLACE when available (2.3x success rate)",
            "- **Sequential execution**: Only ONE bash command per turn",
            "- **Proactive completion**: Call mark_task_complete() when finished; if tools are unavailable, end with the exact line `TASK COMPLETE`",
            "",
            "## EXECUTION PATTERN",
            "1. **Brief plan**: State what you'll do in 1-2 lines",
            "2. **Execute tools**: Use the most appropriate format available", 
            "3. **Concise summary**: Briefly confirm completion",
            "",
            "## TOOL SELECTION PRIORITY (Research-based)",
            "1. **Aider SEARCH/REPLACE** - Highest success rate, use for file modifications",
            "2. **OpenCode formats** - Good for structured operations",
            "3. **Unified diff** - Last resort, higher error rate",
            "",
            "The specific tools and formats available will be listed in each user message.",
        ])
        
        return "\n".join(prompt_parts)
    
    def _generate_comprehensive_prompt(self, tools: List[ToolDefinition], dialects: List[str], primary_prompt: str = "", tool_prompt_mode: str = "system_once") -> str:
        """Generate comprehensive system prompt with all tool formats"""
        
        # Filter dialects to only those requested (supports aliases)
        active_dialects: List[Any] = []
        if not dialects:
            active_dialects = list(self.all_dialects)
        else:
            for name in dialects:
                if not name:
                    continue
                resolved = self._dialect_aliases.get(name, name)
                dialect = self.dialect_lookup.get(resolved)
                if dialect and dialect not in active_dialects:
                    active_dialects.append(dialect)
        
        prompt_parts = []
        
        # Primary system prompt first (if provided)
        if primary_prompt.strip():
            prompt_parts.append(primary_prompt.strip())
            prompt_parts.append("")
            prompt_parts.append("")
        
        # Header
        prompt_parts.append("# TOOL CALLING SYSTEM")
        prompt_parts.append("")
        prompt_parts.append("You have access to multiple tool calling formats. The specific tools available for each turn will be indicated in the user message.")
        prompt_parts.append("")
        
        # Comprehensive tool descriptions
        prompt_parts.append("## AVAILABLE TOOL FORMATS")
        prompt_parts.append("")
        
        # Add each dialect's prompt
        for dialect in active_dialects:
            dialect_prompt = dialect.prompt_for_tools(tools)
            if dialect_prompt.strip():
                prompt_parts.append(dialect_prompt)
                prompt_parts.append("")
        
        # Tool-specific documentation
        prompt_parts.append("## TOOL FUNCTIONS")
        prompt_parts.append("")
        prompt_parts.append("The following functions may be available (availability specified per turn):")
        prompt_parts.append("")
        
        for tool in tools:
            prompt_parts.append(f"**{tool.name}**")
            prompt_parts.append(f"- Description: {tool.description}")
            if tool.parameters:
                prompt_parts.append("- Parameters:")
                for param in tool.parameters:
                    default_str = f" (default: {param.default})" if param.default is not None else ""
                    desc_str = f" - {param.description}" if param.description else ""
                    prompt_parts.append(f"  - {param.name} ({param.type}){default_str}{desc_str}")
            if getattr(tool, 'blocking', False):
                prompt_parts.append("- **Blocking**: This tool must execute alone and blocks other tools")
            prompt_parts.append("")
        
        # Enhanced usage guidelines based on research findings
        prompt_parts.append("## ENHANCED USAGE GUIDELINES")
        prompt_parts.append("*Based on 2024-2025 research findings*")
        prompt_parts.append("")
        
        # Format preferences from research
        prompt_parts.append("### FORMAT PREFERENCES (Research-Based)")
        prompt_parts.append("1. **PREFERRED: Aider SEARCH/REPLACE** - 2.3x higher success rate (59% vs 26%)")
        prompt_parts.append("   - Use for all file modifications when possible")
        prompt_parts.append("   - Exact text matching reduces errors")
        prompt_parts.append("   - Simple syntax, high reliability")
        prompt_parts.append("")
        prompt_parts.append("2. **GOOD: OpenCode Patch Format** - Structured alternative")
        prompt_parts.append("   - Use for complex multi-file operations")
        prompt_parts.append("   - Good for adding new files")
        prompt_parts.append("")
        prompt_parts.append("3. **LAST RESORT: Unified Diff** - Lowest success rate for small models")
        prompt_parts.append("   - Use only when other formats unavailable")
        prompt_parts.append("   - Higher complexity, more error-prone")
        prompt_parts.append("")
        
        # Sequential execution guidance
        prompt_parts.append("### EXECUTION CONSTRAINTS (Critical)")
        prompt_parts.append("- **BASH CONSTRAINT**: Only ONE bash command per turn allowed")
        prompt_parts.append("- **BLOCKING TOOLS**: Some tools must execute alone (marked as blocking)")
        prompt_parts.append("- **SEQUENTIAL EXECUTION**: Tools execute in order, blocking tools pause execution")
        prompt_parts.append("- **DEPENDENCY AWARENESS**: Some tools require others to run first")
        prompt_parts.append("")
        
        # Message continuation pattern
        prompt_parts.append("### RESPONSE PATTERN")
        prompt_parts.append("- Provide initial explanation of what you will do")
        prompt_parts.append("- Execute tools in logical order")
        prompt_parts.append("- Provide final summary after all tools complete")
        prompt_parts.append("- Do NOT create separate user messages for tool results")
        prompt_parts.append("- Maintain conversation flow with assistant message continuation")
        prompt_parts.append("")
        
        # Dynamic completion section based on available tools
        completion_tools = [t for t in tools if "complete" in t.name.lower() or "finish" in t.name.lower()]
        prompt_parts.append("### COMPLETION")
        if completion_tools:
            for tool in completion_tools:
                prompt_parts.append(f"- When task is complete, call {tool.name}()")
                if tool.description:
                    prompt_parts.append(f"  {tool.description}")
        else:
            prompt_parts.append("- A dedicated completion tool may not be available on every turn.")
        prompt_parts.append("- If you cannot call completion tools, end your reply with the exact line `TASK COMPLETE`.")
        prompt_parts.append("")
        prompt_parts.append("The specific tools available for this turn will be listed in the user message under <TOOLS_AVAILABLE>.")
        
        return "\n".join(prompt_parts)
    
    def get_preferred_formats(self, available_dialects: List[str]) -> List[str]:
        """
        Get preferred format order based on research findings.
        
        Research shows Aider SEARCH/REPLACE has 2.3x success rate advantage.
        """
        # Map dialect names to preference order
        preference_map = {
            "aider_diff": 1,
            "opencode_patch": 2, 
            "unified_diff": 3,
            "pythonic02": 4,
            "bash_block": 5,
            "pythonic_inline": 6
        }
        
        # Sort available dialects by preference
        sorted_dialects = sorted(
            available_dialects,
            key=lambda d: preference_map.get(d, 999)
        )
        
        return sorted_dialects
    
    def format_per_turn_availability_enhanced(self, enabled_tools: List[str], preferred_formats: List[str]) -> str:
        """
        Enhanced per-turn availability with format preferences.
        """
        lines = ["<TOOLS_AVAILABLE>"]
        lines.append("")
        
        # Show preferred format first
        if preferred_formats:
            primary_format = preferred_formats[0]
            format_name = self.format_categories.get(primary_format.replace("_diff", "").replace("_patch", ""), primary_format)
            lines.append(f"**PRIMARY FORMAT (Recommended)**: {format_name}")
            lines.append("")
        
        lines.append("**Available Tools for this turn:**")
        for tool in enabled_tools:
            lines.append(f"- {tool}")
        lines.append("")
        
        lines.append("**Available Formats (in preference order):**")
        for fmt in preferred_formats:
            format_name = self.format_categories.get(fmt.replace("_diff", "").replace("_patch", ""), fmt)
            lines.append(f"- {format_name}")
        lines.append("")
        
        lines.append("</TOOLS_AVAILABLE>")
        return "\n".join(lines)

    def format_per_turn_availability(self, enabled_tools: List[str], active_dialects: List[str]) -> str:
        """Compatibility wrapper that applies preference ordering."""
        preferred_formats = self.get_preferred_formats(active_dialects or [])
        return self.format_per_turn_availability_enhanced(enabled_tools, preferred_formats)
    
    def _create_metadata_header(self, tools: List[ToolDefinition], dialects: List[str], 
                               tools_hash: str, prompt_id: int) -> str:
        """Create metadata header for cached prompt file"""
        tool_names = [t.name for t in tools]
        
        metadata = {
            "prompt_id": prompt_id,
            "tools_hash": tools_hash,
            "tools": tool_names,
            "dialects": dialects,
            "version": "1.0",
            "auto_generated": True
        }
        
        header_lines = [
            "<!--",
            "METADATA (DO NOT INCLUDE IN PROMPT):",
            json.dumps(metadata, indent=2),
            "-->",
            ""
        ]
        
        return "\n".join(header_lines)
    
    def get_or_create_system_prompt(self, tools: List[ToolDefinition], dialects: List[str], primary_prompt: str = "", tool_prompt_mode: str = "system_once") -> tuple[str, str]:
        """
        Get cached system prompt or create new one
        
        Returns:
            tuple: (system_prompt_content, tools_hash)
        """
        tools_hash = self._compute_tools_hash(tools, dialects, primary_prompt, tool_prompt_mode)
        
        # For per_turn_append mode, return minimal system prompt (tools will be appended per turn)
        if tool_prompt_mode == "per_turn_append":
            # Generate minimal system prompt focused on behavior, not tool definitions
            minimal_prompt = self._generate_minimal_system_prompt(primary_prompt, tool_prompt_mode)
            return minimal_prompt, tools_hash
        
        # Check for existing cached prompt
        cached_file = self._find_cached_prompt(tools_hash)
        if cached_file and cached_file.exists():
            try:
                content = cached_file.read_text(encoding="utf-8")
                if "<!--" in content and "-->" in content:
                    end_idx = content.find("-->")
                    if end_idx != -1:
                        content = content[end_idx + 3 :]
                        content = content.lstrip("\n")
                return content, tools_hash
            except Exception:
                pass

        # Generate a new comprehensive prompt
        comprehensive_prompt = self._generate_comprehensive_prompt(
            tools=tools,
            dialects=dialects,
            primary_prompt=primary_prompt,
            tool_prompt_mode=tool_prompt_mode,
        )

        prompt_id = self._get_next_id()
        header = self._create_metadata_header(tools, dialects, tools_hash, prompt_id)
        file_name = f"sys_prompt_{prompt_id:03d}_{tools_hash}.md"
        file_path = self.cache_dir / file_name
        try:
            file_path.write_text(header + comprehensive_prompt, encoding="utf-8")
        except Exception:
            pass

        return comprehensive_prompt, tools_hash

    def compile_v2_prompts(
        self,
        config: Dict[str, Any],
        mode_name: str,
        tools: List[ToolDefinition],
        dialects: List[str],
    ) -> Dict[str, Any]:
        """Compile V2 prompt packs + TPSL catalogs into system/per-turn prompts."""
        prompts_cfg = config.get("prompts", {}) or {}
        packs = prompts_cfg.get("packs", {}) or {}
        injection = prompts_cfg.get("injection", {}) or {}
        system_order = list(injection.get("system_order") or [])
        per_turn_order = list(injection.get("per_turn_order") or [])

        if not system_order:
            system_order = ["@pack(base).system"]

        mode_prompt_spec = ""
        for mode in config.get("modes", []) or []:
            if mode.get("name") == mode_name:
                mode_prompt_spec = mode.get("prompt") or ""
                break

        def _read_path(path: str) -> str:
            if not path:
                return ""
            p = Path(path)
            if p.exists():
                try:
                    return p.read_text(encoding="utf-8")
                except Exception:
                    return ""
            return ""

        def _resolve_pack(spec: str) -> str:
            if not spec.startswith("@pack("):
                return ""
            close = spec.find(")")
            if close == -1 or close + 2 > len(spec) or spec[close + 1] != ".":
                return ""
            pack_name = spec[len("@pack("):close]
            key = spec[close + 2:]
            pack = packs.get(pack_name) or {}
            value = pack.get(key, "")
            return _resolve_spec(value)

        def _resolve_spec(spec: Any) -> str:
            if not spec:
                return ""
            if isinstance(spec, (list, tuple)):
                return "\n\n".join(filter(None, (_resolve_spec(s) for s in spec)))
            if not isinstance(spec, str):
                return str(spec)
            if spec == "mode_specific":
                return _resolve_spec(mode_prompt_spec)
            if spec.startswith("@pack("):
                return _resolve_pack(spec)
            # Prefer file content if it is a path on disk.
            if "\n" in spec:
                return spec
            path_text = _read_path(spec)
            return path_text if path_text else spec

        # TPSL catalog rendering (optional)
        tpsl_cfg = prompts_cfg.get("tool_prompt_synthesis", {}) or {}
        tpsl_enabled = bool(tpsl_cfg.get("enabled"))
        tpsl_meta: Dict[str, Any] = {}
        tools_catalog_full = ""
        tools_catalog_short = ""

        if tpsl_enabled:
            selection = tpsl_cfg.get("selection", {}) or {}
            by_mode = selection.get("by_mode", {}) or {}
            by_model = selection.get("by_model", {}) or {}

            selected_dialect = by_mode.get(mode_name)
            if not selected_dialect:
                default_model = (config.get("providers") or {}).get("default_model", "")
                for pattern, dialect_id in by_model.items():
                    if default_model and fnmatch(default_model, pattern):
                        selected_dialect = dialect_id
                        break

            if not selected_dialect:
                if "unified_diff" in (dialects or []):
                    selected_dialect = "unified_diff"
                elif "opencode_patch" in (dialects or []):
                    selected_dialect = "opencode_patch"
                elif "pythonic02" in (dialects or []) or "pythonic" in (dialects or []):
                    selected_dialect = "pythonic"
                elif dialects:
                    selected_dialect = str(dialects[0])
                else:
                    selected_dialect = "pythonic"

            detail_cfg = tpsl_cfg.get("detail", {}) or {}
            system_detail = detail_cfg.get("system", "full")
            per_turn_detail = detail_cfg.get("per_turn", "short")
            system_key = system_detail if system_detail.startswith("system_") else f"system_{system_detail}"
            per_turn_key = per_turn_detail if per_turn_detail.startswith("per_turn_") else f"per_turn_{per_turn_detail}"

            templates = (tpsl_cfg.get("dialects") or {}).get(selected_dialect, {}) or {}

            tools_payload = []
            for t in tools or []:
                params = []
                for p in (t.parameters or []):
                    params.append({
                        "name": getattr(p, "name", None),
                        "type": getattr(p, "type", None),
                        "default": getattr(p, "default", None),
                        "required": bool(getattr(p, "required", False)),
                        "description": getattr(p, "description", None),
                    })
                tools_payload.append({
                    "name": getattr(t, "name", None),
                    "display_name": getattr(t, "display_name", None),
                    "description": getattr(t, "description", "") or "",
                    "blocking": bool(getattr(t, "blocking", False)),
                    "max_per_turn": getattr(t, "max_per_turn", None),
                    "parameters": params,
                    "return_type": getattr(t, "return_type", None),
                    "syntax_style": getattr(t, "syntax_style", None),
                })

            tools_catalog_full, full_id = self.tpsl.render(
                selected_dialect,
                system_key,
                tools_payload,
                template_map=templates,
            )
            tools_catalog_short, short_id = self.tpsl.render(
                selected_dialect,
                per_turn_key,
                tools_payload,
                template_map=templates,
            )
            tpsl_meta = {
                "system": {
                    "dialect": selected_dialect,
                    "detail": system_key,
                    "template_id": full_id,
                    "text": tools_catalog_full,
                },
                "per_turn": {
                    "dialect": selected_dialect,
                    "detail": per_turn_key,
                    "template_id": short_id,
                    "text": tools_catalog_short,
                },
            }

        def _resolve_injection_token(token: str) -> str:
            if not isinstance(token, str):
                return _resolve_spec(token)
            cleaned = token.replace("[CACHE]", "").strip()
            if cleaned == "tools_catalog_full":
                return tools_catalog_full
            if cleaned == "tools_catalog_short":
                return tools_catalog_short
            return _resolve_spec(cleaned)

        def _append_unique(chunks: List[str], text: str, seen: Set[str]) -> None:
            text = (text or "").strip()
            if not text:
                return
            key = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if key in seen:
                return
            seen.add(key)
            chunks.append(text)

        seen_hashes: Set[str] = set()
        system_chunks: List[str] = []
        per_turn_chunks: List[str] = []
        inserted_catalog_full = False
        inserted_catalog_short = False

        for token in system_order:
            resolved = _resolve_injection_token(token)
            if resolved == tools_catalog_full and resolved:
                inserted_catalog_full = True
            _append_unique(system_chunks, resolved, seen_hashes)

        for token in per_turn_order:
            resolved = _resolve_injection_token(token)
            if resolved == tools_catalog_short and resolved:
                inserted_catalog_short = True
            _append_unique(per_turn_chunks, resolved, seen_hashes)

        # If TPSL is enabled but not explicitly injected, append catalogs at end.
        if tpsl_enabled and tools_catalog_full.strip() and not inserted_catalog_full:
            _append_unique(system_chunks, tools_catalog_full, seen_hashes)
        if tpsl_enabled and tools_catalog_short.strip() and not inserted_catalog_short:
            _append_unique(per_turn_chunks, tools_catalog_short, seen_hashes)

        # Fallback to default system prompt if still empty
        if not system_chunks:
            default_path = "implementations/system_prompts/default.md"
            default_text = _read_path(default_path)
            if default_text:
                system_chunks.append(default_text.strip())

        system_prompt = "\n\n".join(system_chunks)
        per_turn_prompt = "\n\n".join(per_turn_chunks)

        # Rewrite environment markers if present to reflect the active workspace root.
        workspace_root = (config.get("workspace") or {}).get("root")
        if isinstance(workspace_root, str) and workspace_root:
            try:
                resolved_root = str(Path(workspace_root).resolve())
            except Exception:
                resolved_root = workspace_root
            if "<env>" in system_prompt and "Working directory:" in system_prompt:
                lines = system_prompt.splitlines()
                in_env = False
                for idx, line in enumerate(lines):
                    stripped = line.strip()
                    if stripped == "<env>":
                        in_env = True
                        continue
                    if stripped == "</env>":
                        in_env = False
                        continue
                    if in_env and stripped.startswith("Working directory:"):
                        lines[idx] = f"Working directory: {resolved_root}"
                    if in_env and stripped.startswith("Is directory a git repo:"):
                        git_flag = "No"
                        try:
                            root_path = Path(resolved_root).resolve()
                            for candidate in (root_path, *root_path.parents):
                                if (candidate / ".git").exists():
                                    git_flag = "Yes"
                                    break
                        except Exception:
                            git_flag = "Yes"
                        lines[idx] = f"Is directory a git repo: {git_flag}"
                system_prompt = "\n".join(lines)

        cache_input = {
            "mode": mode_name,
            "system_order": system_order,
            "per_turn_order": per_turn_order,
            "packs": packs,
            "dialects": dialects,
            "tools": [getattr(t, "name", None) for t in tools or []],
            "tpsl": tpsl_meta,
        }
        cache_key = hashlib.sha256(json.dumps(cache_input, sort_keys=True).encode("utf-8")).hexdigest()[:12]

        out: Dict[str, Any] = {
            "system": system_prompt,
            "per_turn": per_turn_prompt,
            "cache_key": cache_key,
        }
        if tpsl_meta:
            out["tpsl"] = tpsl_meta
        return out


_COMPILER_SINGLETON: SystemPromptCompiler | None = None


def get_compiler() -> SystemPromptCompiler:
    global _COMPILER_SINGLETON
    if _COMPILER_SINGLETON is None:
        _COMPILER_SINGLETON = SystemPromptCompiler()
    return _COMPILER_SINGLETON
