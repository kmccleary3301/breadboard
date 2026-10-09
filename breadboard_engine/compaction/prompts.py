"""Compaction prompts loader and template renderer.

Loads markdown prompt templates from ``breadboard_engine/compaction/prompts/*.md``
via ``importlib.resources`` and provides a minimal template engine compatible with
OMP's Handlebars-based prompt renderer.
"""

from __future__ import annotations

import importlib.resources
import re
from typing import Any, Mapping, Optional

_IF_BLOCK_RE = re.compile(
    r"\{\{#if\s+([a-zA-Z0-9_]+)\}\}([\s\S]*?)(?:\{\{else\}\}([\s\S]*?))?\{\{/if\}\}",
    re.MULTILINE,
)
_XML_BLOCK_RE = re.compile(
    r'\{\{#xml\s+["\']?([a-zA-Z0-9_-]+)["\']?\}\}([\s\S]*?)\{\{/xml\}\}',
    re.MULTILINE,
)
_VAR_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")


def format_prompt(content: str) -> str:
    """Post-render whitespace formatting matching OMP ``prompt.format``."""
    lines = content.split("\n")
    result: list[str] = []
    in_code_block = False

    for raw in lines:
        line = raw.rstrip()
        s = 0
        while s < len(line) and line[s] in (" ", "\t"):
            s += 1

        if line.startswith("```", s) or line.startswith("~~~", s):
            in_code_block = not in_code_block
            result.append(line)
            continue

        if in_code_block:
            result.append(line)
            continue

        if s >= len(line):
            # Blank line
            if result and result[-1] == "":
                continue
            if not result:
                continue
            result.append("")
            continue

        result.append(line)

    while result and result[-1] == "":
        result.pop()

    return "\n".join(result)


def render_template(template: str, context: Optional[Mapping[str, Any]] = None) -> str:
    """Render a Handlebars-like template string with the provided context dictionary."""
    ctx: dict[str, Any] = dict(context or {})

    def resolve_if(match: re.Match[str]) -> str:
        var_name = match.group(1)
        then_branch = match.group(2)
        else_branch = match.group(3) or ""
        val = ctx.get(var_name)
        if val:
            return then_branch
        return else_branch

    # Resolve conditionals repeatedly to handle nesting
    text = template
    prev = None
    while prev != text:
        prev = text
        text = _IF_BLOCK_RE.sub(resolve_if, text)

    def resolve_xml(match: re.Match[str]) -> str:
        tag = match.group(1)
        inner = match.group(2)
        # Recursively render variables in inner content first
        inner_rendered = _VAR_RE.sub(lambda m: str(ctx.get(m.group(1), "")), inner).strip()
        if not inner_rendered:
            return ""
        return f"<{tag}>\n{inner_rendered}\n</{tag}>"

    prev = None
    while prev != text:
        prev = text
        text = _XML_BLOCK_RE.sub(resolve_xml, text)

    def resolve_var(match: re.Match[str]) -> str:
        var_name = match.group(1)
        val = ctx.get(var_name)
        return str(val) if val is not None else ""

    text = _VAR_RE.sub(resolve_var, text)
    return format_prompt(text)


def load_prompt(name: str) -> str:
    """Load a markdown prompt template by name (with or without .md extension)."""
    filename = name if name.endswith(".md") else f"{name}.md"
    resource = importlib.resources.files("breadboard_engine.compaction").joinpath(
        "prompts", filename
    )
    return resource.read_text(encoding="utf-8")


def render_prompt(
    name_or_template: str,
    context: Optional[Mapping[str, Any]] = None,
    **kwargs: Any,
) -> str:
    """Render a prompt either from a file name or raw template string."""
    ctx = {**(context or {}), **kwargs}
    if "\n" not in name_or_template and not name_or_template.startswith("<") and not name_or_template.startswith("{"):
        try:
            template = load_prompt(name_or_template)
        except (FileNotFoundError, ModuleNotFoundError):
            template = name_or_template
    else:
        template = name_or_template
    return render_template(template, ctx)


# Cached base prompts (verbatim from OMP)
SUMMARIZATION_SYSTEM_PROMPT = render_prompt("summarization-system")
SUMMARIZATION_PROMPT = render_prompt("compaction-summary")
UPDATE_SUMMARIZATION_PROMPT = render_prompt("compaction-update-summary")
SHORT_SUMMARY_PROMPT = render_prompt("compaction-short-summary")
TURN_PREFIX_SUMMARIZATION_PROMPT = render_prompt("compaction-turn-prefix")
HANDOFF_DOCUMENT_PROMPT = render_prompt("handoff-document")
AUTO_HANDOFF_THRESHOLD_FOCUS = render_prompt("auto-handoff-threshold-focus")
CONTEXT_WINDOW_TRUNCATED_OUTPUT_MESSAGE = render_prompt("context-window-truncated-output")


def render_compaction_summary_context(summary: str) -> str:
    """Wrap a compaction summary for injection into the context."""
    return render_prompt("compaction-summary-context", {"summary": summary})


def render_handoff_summary_context(summary: str) -> str:
    """Wrap a handoff document for injection into the context."""
    return render_prompt("handoff-summary-context", {"summary": summary})


def render_handoff_prompt(custom_instructions: Optional[str] = None) -> str:
    """Render the user prompt requesting a handoff document."""
    if not custom_instructions:
        return HANDOFF_DOCUMENT_PROMPT
    return render_prompt("handoff-document", {"additionalFocus": custom_instructions})
