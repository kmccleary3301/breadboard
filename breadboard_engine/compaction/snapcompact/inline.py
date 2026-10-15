"""Snapcompact inline context transforms for tool results and system prompt.

Ports OMP packages/coding-agent/src/session/snapcompact-inline.ts:
- Gated on vision capability (model/route supports images) and Pillow availability
- Transmutation of large historical tool results to dense PNG frames (with crisp text for recent)
- Optional system prompt / context-file instructions imaging
- Pure non-destructive message translation (original dicts and lists are never mutated)
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..settings import CompactionSettings
from ..tokens import estimate_text_tokens, is_image_part
from ..state import NATIVE_MARKER_KEY
from ..transcript import anthropic_replay_conversation, content_text, result_parts

from ...provider.adapters import AnthropicAdapter
from ...provider.anthropic_replay import native_tool_results, replace_tool_result

from .archive import count_frames, text_pages
from .renderer import render_snapcompact_png
from .shapes import (
    Shape,
    provider_image_budget,
    resolve_shape,
)

MAX_SYSTEM_PROMPT_FRAMES = 6
SAVINGS_MARGIN = 0.9

TOOL_RESULT_NOTE = (
    "[Tool result text: compacted PNG frame(s) immediately below; read as verbatim output "
    "except labeled source-image position markers. Matching original images follow the frames. "
    "Image delivery: deliberate harness context-saving behavior, not malfunction. NEVER re-run call or report tool issue.]"
)

CONTEXT_FRAMES_NOTE = "=== CONTEXT FILE INSTRUCTIONS — read the image(s) below as the loaded context files replaced in the system prompt ==="
CONTEXT_STUB = (
    "Loaded context-file instructions: PNG image(s) attached below at the first user message start. "
    "At this marker, read every frame in order; apply as if original context-file text remained here."
)

SYSTEM_FRAMES_NOTE = "=== OPERATING INSTRUCTIONS — image(s) below: your system prompt ==="
SYSTEM_STUB = (
    "Full operating instructions: PNG image(s) attached at start of first user message. "
    "Before anything else, read every frame carefully, in order; follow as authoritative system prompt."
)

CONTEXT_SECTION_PATTERNS = [
    re.compile(r"<repo-rules>\n[\s\S]*?\n</repo-rules>"),
    re.compile(r"## Context\n<instructions>\n[\s\S]*?\n</instructions>"),
]


def _count_existing_images(messages: Sequence[Mapping[str, Any]]) -> int:
    count = 0
    for m in messages:
        # A native continuation repeats its results canonically; count its native images once.
        replay_conv = anthropic_replay_conversation(m)
        for item in replay_conv if replay_conv is not None else (m,):
            count += sum(1 for part in result_parts(item.get("content")) if is_image_part(part))
    return count


def _is_error_tool_result(msg: Mapping[str, Any]) -> bool:
    if msg.get("is_error") is True or msg.get("status") == "error":
        return True
    return False


def _attachment_noun(part: Mapping[str, Any]) -> Optional[str]:
    """Media an imaged result keeps verbatim after its frames."""
    if is_image_part(part):
        return "image"
    return "document" if part.get("type") == "document" else None


def _extract_text_and_images(content: Any) -> Tuple[str, List[Dict[str, Any]]]:
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return str(content or ""), []

    text_parts: List[str] = []
    attachments: List[Dict[str, Any]] = []
    counts = {"image": 0, "document": 0}
    for part in content:
        if isinstance(part, Mapping):
            noun = _attachment_noun(part)
            if part.get("type") == "text":
                text_parts.append(str(part.get("text") or ""))
            elif noun is not None:
                counts[noun] += 1
                text_parts.append(f"[Source {noun} {counts[noun]} was attached here in the original tool result.]")
                attachments.append(dict(part))
        elif isinstance(part, str):
            text_parts.append(part)

    return "\n".join(text_parts), attachments


def apply_inline_snapcompact(
    messages: Sequence[Mapping[str, Any]],
    settings: CompactionSettings,
    *,
    supports_images: bool,
    provider: Optional[str] = None,
    api: Optional[str] = None,
    model_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    # 2. Vision capability gate
    if not supports_images:
        return [copy.deepcopy(dict(m)) for m in messages]

    snap_cfg = getattr(settings, "snapcompact", None)
    if snap_cfg is None:
        return [copy.deepcopy(dict(m)) for m in messages]

    sys_mode = str(getattr(snap_cfg, "system_prompt", "none")).lower()
    tool_results_val = getattr(snap_cfg, "tool_results", "none")
    render_tool_results = tool_results_val not in ("none", "false", False)
    min_tokens = int(getattr(snap_cfg, "inline_min_tokens", 4000))

    if sys_mode == "none" and not render_tool_results:
        return [copy.deepcopy(dict(m)) for m in messages]

    # Resolve shape and budget
    cfg_shape = getattr(snap_cfg, "shape", None)
    resolved_api = api if (api or provider or model_id) else "google-generative-ai"
    shape = resolve_shape(variant=cfg_shape or "auto", api=resolved_api, model_id=model_id)
    budget = provider_image_budget(provider) - _count_existing_images(messages)
    if budget <= 0:
        return [copy.deepcopy(dict(m)) for m in messages]

    out_messages: List[Dict[str, Any]] = [copy.deepcopy(dict(m)) for m in messages]

    # Find tool results to swap
    if render_tool_results:
        tool_results: List[Dict[str, Any]] = []
        for msg_idx, msg in enumerate(out_messages):
            if msg.get("role") not in ("tool", "toolResult"):
                continue
            content = msg.get("content")
            # Reuse anthropic_replay_conversation as validated admission
            # (rejects invalid replays, malformed structure, and outer tool_call_id mismatch)
            if anthropic_replay_conversation(msg) is not None:
                for ordinal, block in enumerate(native_tool_results(content)):
                    text, orig_imgs = _extract_text_and_images(block.get("content"))
                    tool_results.append({
                        "msg_idx": msg_idx,
                        "is_replay": True,
                        "ordinal_in_msg": ordinal,
                        "is_error": _is_error_tool_result(msg) or _is_error_tool_result(block),
                        "is_unsupported": False,
                        "text": text,
                        "original_images": orig_imgs,
                    })
                continue
            is_canonical = isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content
            )
            if is_canonical:
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        tool_results.append({
                            "msg_idx": msg_idx,
                            "is_replay": False,
                            "ordinal_in_msg": 0,
                            "is_error": False,
                            "is_unsupported": True,
                            "text": "",
                            "original_images": [],
                        })
                continue

            # Standard tool message
            is_err = _is_error_tool_result(msg)
            text, orig_imgs = _extract_text_and_images(content)
            tool_results.append({
                "msg_idx": msg_idx,
                "is_replay": False,
                "ordinal_in_msg": 0,
                "is_error": is_err,
                "is_unsupported": False,
                "text": text,
                "original_images": orig_imgs,
            })

        # Skip the LAST tool result so freshest output stays crisp text
        candidates = tool_results[:-1] if len(tool_results) > 1 else []

        for candidate in candidates:
            if budget <= 0:
                break
            if candidate["is_error"] or candidate["is_unsupported"]:
                continue
            text = candidate["text"]
            original_images = candidate["original_images"]
            text_tokens = estimate_text_tokens(text)
            if text_tokens < min_tokens:
                continue

            frames_needed = count_frames(text, shape)
            if frames_needed == 0 or frames_needed > budget:
                continue

            # Savings gate
            if frames_needed * shape.frame_token_estimate > text_tokens * SAVINGS_MARGIN:
                continue

            frame_blocks = _render_text_frames(text, shape)
            new_content: List[Dict[str, Any]] = [{"type": "text", "text": TOOL_RESULT_NOTE}]
            new_content.extend(frame_blocks)
            counts = {"image": 0, "document": 0}
            for attachment in original_images:
                noun = _attachment_noun(attachment)
                counts[noun] += 1
                new_content.append({
                    "type": "text",
                    "text": f"[Original source {noun} {counts[noun]}; corresponds to its marker in the compacted text.]",
                })
                new_content.append(attachment)

            target_msg = out_messages[candidate["msg_idx"]]
            if candidate["is_replay"]:
                native_compacted = AnthropicAdapter().to_native_tool_result_content(new_content, None)
                target_msg["content"] = replace_tool_result(
                    target_msg["content"], candidate["ordinal_in_msg"], native_compacted,
                )
            else:
                target_msg["content"] = new_content

            budget -= len(frame_blocks)

    # Handle system prompt. Mirrors OMP selectSystemPromptImageTarget and
    # planInlineSwaps: the selected text is replaced only when all of it fits
    # within min(budget, MAX_SYSTEM_PROMPT_FRAMES) and passes the savings gate.
    if sys_mode in ("all", "agents-md") and budget > 0:
        # A native boundary is replayed as its items, so frames on it would never be sent.
        first_user_idx = next(
            (i for i, m in enumerate(out_messages) if m.get("role") == "user" and NATIVE_MARKER_KEY not in m),
            None,
        )
        if first_user_idx is not None:
            sys_indices = [
                i
                for i, m in enumerate(out_messages)
                if m.get("role") in ("system", "developer")
            ]
            replacements: Dict[int, str] = {}
            if sys_mode == "all":
                sys_texts = [content_text(out_messages[i]) for i in sys_indices if content_text(out_messages[i])]
                target_text = "\n\n".join(sys_texts)
                replacements = {i: SYSTEM_STUB for i in sys_indices}
                frames_note = SYSTEM_FRAMES_NOTE
            else:
                extracted_sections: List[str] = []
                for i in sys_indices:
                    txt = content_text(out_messages[i])
                    modified = txt
                    for pat in CONTEXT_SECTION_PATTERNS:
                        extracted_sections.extend(match.group(0).strip() for match in pat.finditer(modified))
                        modified = pat.sub(CONTEXT_STUB, modified)
                    if modified != txt:
                        replacements[i] = modified
                target_text = "\n\n".join(extracted_sections)
                frames_note = CONTEXT_FRAMES_NOTE

            frames = count_frames(target_text, shape) if target_text else 0
            if (
                0 < frames <= min(budget, MAX_SYSTEM_PROMPT_FRAMES)
                and frames * shape.frame_token_estimate <= estimate_text_tokens(target_text) * SAVINGS_MARGIN
            ):
                rendered_frames = _render_text_frames(target_text, shape)
                for i, replacement in replacements.items():
                    out_messages[i]["content"] = replacement
                user_msg = out_messages[first_user_idx]
                user_content = user_msg.get("content")
                if isinstance(user_content, str):
                    original_user_parts = [{"type": "text", "text": user_content}]
                elif isinstance(user_content, list):
                    original_user_parts = list(user_content)
                else:
                    original_user_parts = []
                user_msg["content"] = [
                    {"type": "text", "text": frames_note},
                    *rendered_frames,
                    *original_user_parts,
                ]
                budget -= len(rendered_frames)

    return out_messages


def _render_text_frames(text: str, shape: Shape) -> List[Dict[str, Any]]:
    pages = text_pages(text, shape)
    rendered = []
    for page in pages:
        b64 = render_snapcompact_png(
            page,
            size=shape.frame_size,
            font=shape.font,
            cell_width=shape.cell_width,
            cell_height=shape.cell_height,
            variant=shape.variant,
            line_repeat=shape.line_repeat,
            stretch=shape.stretch,
            columns=shape.columns or 1,
        )
        rendered.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    return rendered
