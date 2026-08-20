"""
Multi-Level Prompt Compiler for Advanced Tool Calling Modes

Supports granular prompt compilation with three levels:
1. System-level: Base system prompt compilation
2. Tool-level: Compiled tool availability and format information  
3. Per-turn: Dynamic per-turn tool availability

Modes supported:
- sys_compiled_per_turn_persistent--long-medium
- sys_compiled_per_turn_persistent--short-long
- sys_compiled_per_turn_persistent--medium-short
- sys_compiled_per_turn_temporary--long-medium
- sys_compiled_per_turn_temporary--short-long
- sys_compiled_per_turn_temporary--medium-short
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import List, Dict, Any, Set, Optional, Tuple
from enum import Enum
from dataclasses import dataclass

from ..core.core import ToolDefinition
from .system_prompt_compiler import SystemPromptCompiler
from .tool_prompt_synth import ToolPromptSynthesisEngine


class PromptLength(Enum):
    """Prompt length variations"""
    SHORT = "short"
    MEDIUM = "medium"  
    LONG = "long"


class PersistenceMode(Enum):
    """Per-turn persistence modes"""
    PERSISTENT = "persistent"
    TEMPORARY = "temporary"


@dataclass
class PromptCompilationConfig:
    """Configuration for multi-level prompt compilation"""
    # Base mode (always sys_compiled_per_turn for these variants)
    base_mode: str = "sys_compiled_per_turn"
    
    # Persistence mode for per-turn additions
    persistence: PersistenceMode = PersistenceMode.PERSISTENT
    
    # System prompt length (compiled and cached)
    system_length: PromptLength = PromptLength.LONG
    
    # Per-turn prompt length (tool availability info)
    per_turn_length: PromptLength = PromptLength.MEDIUM
    
    # Provider and model info
    provider_id: str = "openai"
    model_id: str = "gpt-4"
    
    def to_mode_string(self) -> str:
        """Convert config to mode string"""
        return f"{self.base_mode}_{self.persistence.value}--{self.system_length.value}-{self.per_turn_length.value}"
    
    @classmethod
    def from_mode_string(cls, mode_string: str) -> 'PromptCompilationConfig':
        """Parse mode string into config"""
        # Example: sys_compiled_per_turn_persistent--long-medium
        
        if not mode_string.startswith("sys_compiled_per_turn_"):
            raise ValueError(f"Unsupported mode: {mode_string}")
        
        # Extract persistence and lengths
        parts = mode_string.split("--")
        if len(parts) != 2:
            raise ValueError(f"Invalid mode format: {mode_string}")
            
        base_and_persistence = parts[0]  # sys_compiled_per_turn_persistent
        lengths = parts[1]  # long-medium
        
        # Extract persistence
        if base_and_persistence.endswith("_persistent"):
            persistence = PersistenceMode.PERSISTENT
        elif base_and_persistence.endswith("_temporary"):
            persistence = PersistenceMode.TEMPORARY
        else:
            raise ValueError(f"Unknown persistence mode in: {mode_string}")
        
        # Extract lengths
        length_parts = lengths.split("-")
        if len(length_parts) != 2:
            raise ValueError(f"Invalid length format: {lengths}")
            
        try:
            system_length = PromptLength(length_parts[0])
            per_turn_length = PromptLength(length_parts[1])
        except ValueError as e:
            raise ValueError(f"Invalid length values: {lengths}") from e
            
        return cls(
            persistence=persistence,
            system_length=system_length,
            per_turn_length=per_turn_length
        )


class MultiLevelPromptCompiler:
    """
    Advanced prompt compiler supporting multiple levels and granularity.
    
    Handles three-level compilation:
    1. System Level: Base behavior and general instructions
    2. Tool Level: Tool definitions and format specifications
    3. Per-Turn Level: Dynamic tool availability for each turn
    """
    
    def __init__(self, cache_dir: str = "implementations/tooling_sys_prompts_cached"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Legacy compiler for compatibility
        self.legacy_compiler = SystemPromptCompiler(str(cache_dir))
        # TPSL engine for Jinja2-based catalogs
        self.tpsl = ToolPromptSynthesisEngine()
        
        # Defer to TPSL for all prompt text (system + per-turn). Keep legacy only as hard fallback.
        self.system_templates = {}
        self.per_turn_templates = {}
    
    def _get_short_system_template(self) -> str:
        """Short system template - minimal instructions"""
        return """# Tool Usage System

You have access to tools. Use them efficiently and report results concisely.

## Core Principles
- Direct execution without excessive explanation
- Use research-preferred formats (Aider SEARCH/REPLACE when available)
- Sequential tool execution (max 1 bash per turn)
- Complete tasks end-to-end

Tools available per turn will be specified in user messages."""
    
    def _get_medium_system_template(self) -> str:
        """Medium system template - balanced detail"""
        return """# Enhanced Tool Usage System

You have access to various tools for code manipulation and system interaction. Focus on efficient, reliable execution.

## Core Principles (Research-Based)
- **Direct execution**: Use tools immediately without excessive planning text
- **Format preferences**: Prioritize Aider SEARCH/REPLACE (2.3x success rate advantage)
- **Sequential execution**: Only ONE bash command per turn allowed
- **Proactive completion**: Mark tasks complete when finished
- **Concise communication**: Brief explanations, focus on actions

## Execution Pattern
1. Brief acknowledgment of task
2. Execute appropriate tools in sequence
3. Confirm completion with summary

## Tool Selection Priority
1. Aider SEARCH/REPLACE - Highest success rate for file modifications
2. OpenCode formats - Good for structured operations  
3. Unified diff - Use only as fallback

Available tools and formats will be specified in each user message."""
    
    def _get_long_system_template(self) -> str:
        """Long system template - comprehensive instructions"""
        return """# Advanced Tool Calling System

You are an AI assistant with access to a comprehensive toolkit for software development and system interaction. Your approach should be methodical, efficient, and grounded in research-validated best practices.

## Core Principles (Research-Validated)

### Execution Philosophy
- **Ownership mindset**: Treat each task as yours end-to-end. Plan, implement, validate, and iterate until completion.
- **Evidence-based decisions**: Never invent APIs, files, or results. Verify by reading files and running commands.
- **Minimal prose**: Prefer actions (tool calls) over lengthy explanations. Add brief notes only when they clarify decision points.
- **Surgical precision**: Keep edits focused and reversible. Preserve unrelated code and formatting.
- **Reproducible outcomes**: Make outputs deterministic. Pin versions and capture exact commands when relevant.

### Research-Based Tool Selection
Based on comprehensive analysis of production AI coding systems:

1. **PREFERRED: Aider SEARCH/REPLACE** - 2.3x higher success rate (59% vs 26%)
   - Use for all file modifications when possible
   - Exact text matching reduces errors significantly
   - Simple syntax minimizes model confusion

2. **GOOD: OpenCode structured formats** - Moderate success rate but versatile
   - Use for complex multi-file operations
   - Good for adding new files with proper structure
   - Clear block-based syntax

3. **FALLBACK: Unified diff patches** - Lower success rate for smaller models  
   - Use only when other formats unavailable
   - Higher complexity increases error probability
   - Better suited for larger, more capable models

## Execution Constraints (Critical)

### Tool Execution Rules
- **BASH CONSTRAINT**: Only ONE bash command per assistant turn allowed
- **SEQUENTIAL EXECUTION**: Tools execute in order; some tools block others
- **DEPENDENCY AWARENESS**: Consider tool execution dependencies
- **TIMEOUT MANAGEMENT**: Respect execution timeouts and cancellation

### Response Patterns
1. **Initial acknowledgment**: Briefly state what you will accomplish
2. **Sequential tool execution**: Execute tools in logical dependency order
3. **Incremental progress**: Provide brief updates for long-running operations
4. **Completion confirmation**: Summarize results and mark task complete

### Error Handling
- **Graceful degradation**: If preferred tools fail, fall back to alternatives
- **Clear error reporting**: Explain what went wrong and proposed solutions
- **Recovery strategies**: Attempt automatic recovery when possible
- **User guidance**: Provide actionable next steps for unrecoverable errors

## Quality Standards

### Code Standards
- Follow existing project conventions and style
- Maintain backward compatibility unless explicitly requested otherwise
- Include appropriate error handling and edge case management
- Write clear, self-documenting code with meaningful variable names

### Testing and Validation
- Run tests after making changes
- Validate that code compiles and executes without errors
- Check that changes don't break existing functionality
- Use example inputs to verify behavior

### Communication Standards
- Be concise but complete in explanations
- Focus on what changed and why, not implementation details
- Highlight important caveats or limitations
- Provide clear next steps when applicable

Available tools and formats for each turn will be specified in user messages. Tool availability may vary based on context, provider capabilities, and security constraints."""
    
    def _get_short_per_turn_template(self) -> str:
        """Short per-turn template - minimal tool list"""
        return """Available: {tool_list}"""
    
    def _get_medium_per_turn_template(self) -> str:
        """Medium per-turn template - structured tool availability"""
        return """<TOOLS_AVAILABLE>
{tool_details}
</TOOLS_AVAILABLE>"""
    
    def _get_long_per_turn_template(self) -> str:
        """Long per-turn template - comprehensive tool documentation"""
        return """<TOOLS_AVAILABLE>
The following tools are available for this turn:

{detailed_tool_docs}

Format Preferences (Research-Based):
- **PRIMARY**: Aider SEARCH/REPLACE (2.3x success rate)
- **SECONDARY**: OpenCode structured formats
- **FALLBACK**: Unified diff patches

Execution Constraints:
- Maximum 1 bash command per turn
- Sequential execution required
- 60-second timeout per tool

{format_examples}
</TOOLS_AVAILABLE>"""
    
    def _compute_compilation_hash(self, config: PromptCompilationConfig, tools: List[ToolDefinition], 
                                 dialects: List[str], primary_prompt: str = "") -> str:
        """Compute hash for compiled prompt caching"""
        hash_input = {
            "config": {
                "mode": config.to_mode_string(),
                "provider": config.provider_id,
                "model": config.model_id
            },
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": [p.name for p in (t.parameters or [])]
                }
                for t in sorted(tools, key=lambda x: x.name)
            ],
            "dialects": sorted(dialects),
            "primary_prompt": primary_prompt.strip()
        }
        
        hash_str = json.dumps(hash_input, sort_keys=True)
        return hashlib.sha256(hash_str.encode()).hexdigest()[:16]
    
    def compile_system_prompt(self, config: PromptCompilationConfig, tools: List[ToolDefinition],
                            dialects: List[str], primary_prompt_path: str = None) -> Tuple[str, str]:
        """
        Compile system-level prompt based on configuration.
        Returns (system_prompt, compilation_hash)
        """
        # Load primary prompt from file
        if not primary_prompt_path:
            primary_prompt_path = "implementations/system_prompts/default.md"
        
        primary_prompt = ""
        if Path(primary_prompt_path).exists():
            primary_prompt = Path(primary_prompt_path).read_text(encoding='utf-8')
        
        # Select TPSL dialect and detail based on available dialects and requested length
        tpsl_dialect = "unified_diff" if "unified_diff" in dialects else "pythonic"
        if config.system_length == PromptLength.LONG:
            system_detail = "system_full"
        elif config.system_length == PromptLength.MEDIUM:
            system_detail = "system_medium"
        else:
            system_detail = "system_short"
        
        # Compute hash for caching
        compilation_hash = self._compute_compilation_hash(config, tools, dialects, primary_prompt)
        
        # Check cache
        cache_file = self.cache_dir / f"sys_{config.to_mode_string()}_{compilation_hash}.md"
        if cache_file.exists():
            return cache_file.read_text(encoding='utf-8'), compilation_hash
        
        # Compile system prompt
        compiled_sections = []
        
        # Primary prompt first
        if primary_prompt.strip():
            compiled_sections.append(primary_prompt.strip())
            compiled_sections.append("")
        
        # TPSL system catalog (rendered from Jinja2 templates)
        tools_payload = []
        for t in tools:
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
        templates = {
            "system_full": f"implementations/tool_prompt_synthesis/{tpsl_dialect}/system_full.j2.md",
        }
        catalog_text, _ = self.tpsl.render(tpsl_dialect, system_detail, tools_payload, templates)
        if catalog_text.strip():
            compiled_sections.append(catalog_text.strip())
            compiled_sections.append("")
        
        # TPSL templates encode format guidance; no additional legacy format info appended here.
        
        system_prompt = "\n".join(compiled_sections)
        
        # Cache the compiled prompt
        cache_file.write_text(system_prompt, encoding='utf-8')
        
        return system_prompt, compilation_hash
    
    def compile_per_turn_prompt(self, config: PromptCompilationConfig, available_tools: List[str],
                              available_dialects: List[str], user_message: str) -> str:
        """
        Compile per-turn tool availability information.
        Returns user_message with appended tool info.
        """
        # TPSL-rendered per-turn availability
        if config.per_turn_length == PromptLength.LONG:
            per_detail = "per_turn_long"
        elif config.per_turn_length == PromptLength.MEDIUM:
            per_detail = "per_turn_medium"
        else:
            per_detail = "per_turn_short"
        tools_payload = [{"name": n, "display_name": None, "description": ""} for n in available_tools]
        templates = {
            "per_turn_short": "implementations/tool_prompt_synthesis/pythonic/per_turn_short.j2.md",
        }
        per_text, _ = self.tpsl.render("pythonic", per_detail, tools_payload, templates)
        tool_section = per_text.strip()
        return f"{user_message}\n\n{tool_section}" if tool_section else user_message
    
    def _compile_tool_format_info(self, tools: List[ToolDefinition], dialects: List[str],
                                 system_length: PromptLength) -> str:
        # Legacy format info is handled by TPSL templates now.
        return ""
    
    def get_supported_modes(self) -> List[str]:
        """Get list of all supported compilation modes"""
        modes = []
        for persistence in PersistenceMode:
            for sys_length in PromptLength:
                for turn_length in PromptLength:
                    config = PromptCompilationConfig(
                        persistence=persistence,
                        system_length=sys_length,
                        per_turn_length=turn_length
                    )
                    modes.append(config.to_mode_string())
        return sorted(modes)
    
    def validate_mode(self, mode_string: str) -> bool:
        """Validate if a mode string is supported"""
        try:
            PromptCompilationConfig.from_mode_string(mode_string)
            return True
        except ValueError:
            return False


# Global compiler instance
_multi_level_compiler = None

def get_multi_level_compiler() -> MultiLevelPromptCompiler:
    """Get global multi-level prompt compiler instance"""
    global _multi_level_compiler
    if _multi_level_compiler is None:
        _multi_level_compiler = MultiLevelPromptCompiler()
    return _multi_level_compiler