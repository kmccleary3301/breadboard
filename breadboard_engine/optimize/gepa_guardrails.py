from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple


DEFAULT_OPT_START = "<!-- OPT_BLOCK_START -->"
DEFAULT_OPT_END = "<!-- OPT_BLOCK_END -->"


@dataclass
class PromptMutationValidation:
    ok: bool
    issues: List[str]
    opt_block_count: int


def _normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n")


def _split_opt_blocks(text: str, start: str, end: str) -> List[Tuple[bool, str]]:
    segments: List[Tuple[bool, str]] = []
    idx = 0
    while True:
        start_idx = text.find(start, idx)
        if start_idx == -1:
            segments.append((False, text[idx:]))
            break
        if start_idx > idx:
            segments.append((False, text[idx:start_idx]))
        end_idx = text.find(end, start_idx + len(start))
        if end_idx == -1:
            segments.append((False, text[start_idx:]))
            break
        block_text = text[start_idx : end_idx + len(end)]
        segments.append((True, block_text))
        idx = end_idx + len(end)
    return segments


def validate_prompt_mutation(
    base_text: str,
    mutated_text: str,
    *,
    opt_start: str = DEFAULT_OPT_START,
    opt_end: str = DEFAULT_OPT_END,
    allow_unmarked: bool = False,
) -> PromptMutationValidation:
    base = _normalize_text(base_text)
    mutated = _normalize_text(mutated_text)
    base_segments = _split_opt_blocks(base, opt_start, opt_end)
    mutated_segments = _split_opt_blocks(mutated, opt_start, opt_end)

    base_opt_blocks = sum(1 for is_opt, _ in base_segments if is_opt)
    mutated_opt_blocks = sum(1 for is_opt, _ in mutated_segments if is_opt)

    issues: List[str] = []
    if base_opt_blocks == 0 and not allow_unmarked:
        issues.append("no_opt_blocks_in_base_prompt")
    if base_opt_blocks != mutated_opt_blocks:
        issues.append("opt_block_count_mismatch")

    base_outside = "".join(segment for is_opt, segment in base_segments if not is_opt)
    mutated_outside = "".join(segment for is_opt, segment in mutated_segments if not is_opt)
    if base_outside != mutated_outside:
        issues.append("mutated_text_changes_outside_opt_blocks")

    return PromptMutationValidation(ok=not issues, issues=issues, opt_block_count=base_opt_blocks)
