"""Snapcompact compaction method.

Ports OMP session-maintenance.ts snapcompact execution:
- Image capability and Pillow availability gating
- Frame budgeting sized from model window and reserve
- Dead-end frame rescue for trailing over-threshold archives
- Symmetrical encrypted reasoning exclusion in no-reduction guard
- Boundary record with single user message carrying archive text + PNG image_url blocks
"""

from __future__ import annotations

import copy
import json
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from ..methods import CompactionContext, CompactionRecord, MethodUnavailable
from ..settings import resolve_budget_reserve_tokens
from ..state import CompactionState
from ..tokens import CHARS_PER_TOKEN, MESSAGE_OVERHEAD_TOKENS, estimate_message_tokens, estimate_messages_tokens, estimate_text_tokens
from ..transcript import find_cut_point, leading_system_count

from .archive import (
    Archive,
    compact_archive,
    history_blocks,
)
from .normalize import compute_file_lists
from .shapes import (
    FRAME_DATA_BYTES_BUDGET,
    MAX_FRAMES_DEFAULT,
    Shape,
    frame_data_bytes,
    geometry,
    max_frames_for_data_budget,
    provider_frame_budget,
    resolve_shape,
)


def _strip_opaque_reasoning(obj: Any) -> Any:
    """Recursively strip opaque reasoning signatures (thinkingSignature, redactedThinking, etc.)."""
    if isinstance(obj, Mapping):
        cleaned: Dict[str, Any] = {}
        for k, v in obj.items():
            if k in ("thinkingSignature", "redactedThinking", "signature", "encrypted_content"):
                continue
            cleaned[k] = _strip_opaque_reasoning(v)
        return cleaned
    if isinstance(obj, list):
        return [_strip_opaque_reasoning(x) for x in obj]
    return obj


def estimate_tokens_excluding_encrypted(messages: Sequence[Mapping[str, Any]]) -> int:
    """Token count excluding opaque reasoning signatures (symmetrical no-reduction baseline)."""
    cleaned_messages = [_strip_opaque_reasoning(m) for m in messages]
    return estimate_messages_tokens(cleaned_messages)


def _extract_file_ops_from_messages(messages: Sequence[Mapping[str, Any]]) -> Dict[str, Set[str]]:
    read_set: Set[str] = set()
    edited_set: Set[str] = set()
    written_set: Set[str] = set()

    for msg in messages:
        if msg.get("role") == "assistant":
            for call in msg.get("tool_calls") or ():
                if not isinstance(call, Mapping):
                    continue
                func = call.get("function") if isinstance(call.get("function"), Mapping) else call
                name = str(func.get("name") or "")
                args = func.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                if isinstance(args, Mapping):
                    path = args.get("path") or args.get("file")
                    if isinstance(path, str):
                        if name in ("read", "view", "cat"):
                            read_set.add(path)
                        elif name in ("edit", "patch"):
                            edited_set.add(path)
                        elif name in ("write", "create"):
                            written_set.add(path)

    return {"read": read_set, "edited": edited_set, "written": written_set}


class SnapcompactCompaction:
    """Boundary compaction archiving conversation into dense pixel-font PNG frames."""

    name: str = "snapcompact"

    def run(self, context: CompactionContext) -> CompactionRecord:
        # 1. Availability check: Pillow
        try:
            from PIL import Image
        except ImportError as err:
            raise MethodUnavailable("Pillow is required for snapcompact") from err

        # 2. Vision capability check
        if not context.supports_images:
            raise MethodUnavailable("snapcompact requires a vision-capable model")

        # 3. Shape resolution
        target = context.target
        shape = resolve_shape(
            model=None,
            variant=getattr(context.settings.snapcompact, "shape", "auto") if hasattr(context.settings, "snapcompact") else "auto",
            api=target.api,
            model_id=target.model,
        )

        messages = context.messages
        head = leading_system_count(messages)
        active_boundary = context.state.latest_boundary()
        start = active_boundary.first_kept_index if active_boundary else head
        end = len(messages)

        # 4. Cut point and partition
        cut = find_cut_point(messages, start, end, context.settings.keep_recent_tokens)
        first_kept_index = cut.first_kept_index

        # Check for dead-end frame rescue if nothing new to summarize
        is_dead_end_rescue = False
        if first_kept_index <= start:
            # Check if active boundary carries frames that can be rescued
            if active_boundary is not None:
                arch_dict = active_boundary.details.get("snapcompact_archive") or active_boundary.details.get("preserve_data", {}).get("snapcompact")
                if arch_dict:
                    prev_arch = Archive.from_dict(arch_dict)
                    if len(prev_arch.frames) > 1:
                        is_dead_end_rescue = True
            if not is_dead_end_rescue:
                raise MethodUnavailable("nothing to compact")

        # 5. Budget arithmetic (OMP #computeSnapcompactMaxFrames)
        ctx_window = context.context_window or 0
        reserve = resolve_budget_reserve_tokens(ctx_window, context.settings)
        total_budget = ctx_window - reserve if ctx_window > 0 else float("inf")

        kept_recent = messages[first_kept_index:]
        leading_system = messages[:head]
        base_tokens = estimate_messages_tokens(kept_recent) + estimate_messages_tokens(leading_system)

        if ctx_window > 0 and base_tokens >= total_budget:
            raise MethodUnavailable("snapcompact: kept history alone exceeds the context budget")

        edge_cap = geometry(shape).capacity
        text_edge_tokens = math.ceil((2 * edge_cap * 1.15) / 4)
        summary_template_tokens = 2000
        cap_reserve = text_edge_tokens + summary_template_tokens
        frame_budget = total_budget - base_tokens - cap_reserve

        if frame_budget < shape.frame_token_estimate:
            max_frames = 1
        else:
            max_frames = min(
                math.floor(frame_budget / shape.frame_token_estimate),
                MAX_FRAMES_DEFAULT,
                max_frames_for_data_budget(),
                provider_frame_budget(target.provider),
            )
        max_frames = max(1, max_frames)

        if is_dead_end_rescue:
            # Rebuild existing archive at a smaller frame budget
            max_frames = max(1, min(max_frames, len(prev_arch.frames) - 1))
            messages_to_summarize = []
            previous_summary = active_boundary.summary
            previous_archive = prev_arch
            first_kept_index = active_boundary.first_kept_index or head
        else:
            messages_to_summarize = messages[start:first_kept_index]
            previous_summary = active_boundary.summary if active_boundary else None
            previous_archive = None
            if active_boundary:
                arch_dict = active_boundary.details.get("snapcompact_archive") or active_boundary.details.get("preserve_data", {}).get("snapcompact")
                if arch_dict:
                    previous_archive = Archive.from_dict(arch_dict)

        file_ops = _extract_file_ops_from_messages(messages_to_summarize)

        # 6. Render archive
        summary, short_summary, archive = compact_archive(
            messages_to_summarize,
            shape=shape,
            max_frames=max_frames,
            previous_summary=previous_summary,
            previous_archive=previous_archive,
            file_ops=file_ops,
            include_thinking=True,
            target_api=target.api,
        )

        # 7. Check frame data bytes budget
        payload_bytes = frame_data_bytes(archive.frames)
        if payload_bytes > FRAME_DATA_BYTES_BUDGET:
            raise MethodUnavailable("standing image payload exceeds the per-request budget")

        # 8. Build summary messages: one user message with archive text + PNG image_url parts
        has_images = bool(archive.frames)
        content_parts: List[Dict[str, Any]] = []

        lead_text = summary
        if archive.text_head:
            suffix = "\n-------------- imaged middle below\n" if has_images else ""
            lead_text = f"{summary}\n\n{archive.text_head}{suffix}"

        content_parts.append({"type": "text", "text": lead_text})

        for f in archive.frames:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{f.data}"},
            })

        if archive.text_tail:
            prefix = (
                "-------------- imaged middle above\n"
                if has_images
                else ("\n-------------- middle history omitted above\n" if archive.truncated_chars > 0 else "")
            )
            content_parts.append({"type": "text", "text": f"{prefix}{archive.text_tail}"})

        summary_messages = (
            {"role": "user", "content": content_parts},
        )

        read_files, modified_files = compute_file_lists(file_ops)
        details = {
            "read_files": read_files,
            "modified_files": modified_files,
            "snapcompact_archive": archive.to_dict(),
            "preserve_data": {"snapcompact": archive.to_dict()},
        }

        # 9. No-reduction guard & Window-fit guard
        # Compare projected view against reduction baseline with encrypted reasoning excluded symmetrically
        projected_before = context.projected()
        baseline_for_reduction = estimate_tokens_excluding_encrypted(projected_before)

        # Proposed new view
        proposed_record = context.new_record(
            method=self.name,
            first_kept_index=first_kept_index,
            summary=summary,
            short_summary=short_summary,
            summary_messages=summary_messages,
            details=details,
        )
        probe_state = CompactionState([*context.state.records, proposed_record])
        proposed_view = probe_state.project(messages, target)

        projected_for_reduction = estimate_tokens_excluding_encrypted(proposed_view)
        if projected_for_reduction >= baseline_for_reduction:
            raise MethodUnavailable("snapcompact would not reduce context")

        # Window-fit guard
        projected_full = estimate_messages_tokens(proposed_view)
        if ctx_window > 0 and projected_full > total_budget:
            raise MethodUnavailable("snapcompact could not bring the context under the limit")

        return proposed_record
