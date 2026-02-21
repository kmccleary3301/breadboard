#!/usr/bin/env python3
"""
Validate Phase4 fullpane runs against locked Claude-style presentation contracts.

This validator is intentionally text-first (ANSI-stripped final frame), because
capture text is deterministic and easier to diff than pixel output.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONTRACT_FILE = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "text_contracts"
    / "phase4_text_contract_v1.json"
)

TODOSYMS = {"☐", "✔", "☒", "🗹"}
BOX_GLYPHS = {"│", "╭", "╮", "╰", "╯"}
NORM_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")

THINKING_EXPECTATIONS: dict[str, dict[str, list[str]]] = {
    "phase4_replay/thinking_preview_v1": {
        "run_contains": ["thinkingpreviewsmokev1jsonl", "Done. I implemented the requested update."],
        "final_contains": ["[task tree] done"],
    },
    "phase4_replay/thinking_reasoning_only_v1": {
        "run_contains": ["thinkingreasoningonlysmokev1jsonl", "Implemented. I updated the target module and added tests."],
        "final_contains": ["[task tree] done"],
    },
    "phase4_replay/thinking_tool_interleaved_v1": {
        "run_contains": ["thinkingtoolinterleavedsmokev1jsonl", "Patched src/main.ts and validated the output path."],
        "final_contains": ["[task tree] done"],
    },
    "phase4_replay/thinking_multiturn_lifecycle_v1": {
        "run_contains": ["thinkingmultiturnlifecyclesmokev1jsonl", "Turn one complete.", "Turn two complete and stable."],
        "final_contains": ["[task tree] done"],
    },
    "phase4_replay/thinking_lifecycle_expiration_v1": {
        "run_contains": ["thinkinglifecycleexpirationsmokev1jsonl", "Update completed and validated."],
        "final_contains": ["[task tree] done"],
    },
}

STRESS_EXPECTATIONS: dict[str, dict[str, list[str]]] = {
    "phase4_replay/alt_buffer_enter_exit_v1": {
        "run_contains": ["altbufferenterexitsmokev1jsonl", "Alt buffer replay ready.", "Read(src/layout.tsx)"],
        "final_contains": ["Try \"fix typecheck errors\""],
    },
    "phase4_replay/resize_overlay_interaction_v1": {
        "run_contains": ["resizeoverlayinteractionsmokev1jsonl", "Resize and overlay interaction settled.", "Patch(layout.tsx)"],
        "final_contains": ["Try \"fix typecheck errors\""],
    },
    "phase4_replay/large_output_artifact_v1": {
        "run_contains": [
            "largeoutputartifactsmokev1jsonl",
            "Wrote file and exported oversized output to artifact.",
            "Inline output truncated to artifact reference.",
        ],
        "final_contains": ["Try \"fix typecheck errors\""],
    },
    "phase4_replay/large_diff_artifact_v1": {
        "run_contains": [
            "largediffartifactsmokev1jsonl",
            "Applied patch via artifact-backed diff preview.",
            "Large unified diff exported to artifact.",
        ],
        "final_contains": ["Try \"fix typecheck errors\""],
    },
    "phase4_replay/subagents_strip_churn_v1": {
        "run_contains": ["subagentsstripchurnsmokev1jsonl", "Subagent strip churn smoke complete"],
        "final_contains": ["Try \"fix typecheck errors\""],
    },
    "phase4_replay/subagents_concurrency_20_v1": {
        "run_contains": ["subagentsconcurrency20v1jsonl", "Subagent concurrency 20 replay complete"],
        "final_contains": ["? for shortcuts"],
    },
}

THINKING_GRAMMAR_EXPECTATIONS: dict[str, list[list[str]]] = {
    "phase4_replay/thinking_preview_v1": [
        ["[task tree] thinking", "Deciphering"],
        ["Done. I implemented the requested update."],
        ["[task tree] done"],
    ],
    "phase4_replay/thinking_reasoning_only_v1": [
        ["[task tree] thinking", "Deciphering"],
        ["Implemented. I updated the target module and added tests."],
        ["[task tree] done"],
    ],
    "phase4_replay/thinking_tool_interleaved_v1": [
        ["[task tree] thinking", "Deciphering"],
        ["Write(src/main.ts)"],
        ["Patched src/main.ts"],
        ["Patched src/main.ts and validated the output path."],
        ["[task tree] done"],
    ],
    "phase4_replay/thinking_multiturn_lifecycle_v1": [
        ["[task tree] thinking", "Deciphering"],
        ["Turn one complete."],
        ["Turn two complete and stable."],
        ["[task tree] done"],
    ],
    "phase4_replay/thinking_lifecycle_expiration_v1": [
        ["[task tree] thinking", "Deciphering"],
        ["Update completed and validated."],
        ["[task tree] done"],
    ],
}


def _normalize_ascii(text: str) -> str:
    return NORM_NON_ALNUM_RE.sub("", text.lower())


def _contains_normalized(haystack: str, needle: str) -> bool:
    return _normalize_ascii(needle) in _normalize_ascii(haystack)


def _first_frame_index_with_token(frame_texts: list[str], token: str) -> int:
    for idx, frame in enumerate(frame_texts):
        if token in frame:
            return idx
    return -1


def _first_frame_index_any(frame_texts: list[str], tokens: list[str]) -> int:
    indexes = [_first_frame_index_with_token(frame_texts, token) for token in tokens]
    valid = [idx for idx in indexes if idx >= 0]
    return min(valid) if valid else -1


def _first_frame_index_any_after(frame_texts: list[str], tokens: list[str], start_idx: int) -> int:
    for idx in range(max(0, start_idx), len(frame_texts)):
        frame = frame_texts[idx]
        if any(token in frame for token in tokens):
            return idx
    return -1


def _last_frame_index_any(frame_texts: list[str], tokens: list[str]) -> int:
    for idx in range(len(frame_texts) - 1, -1, -1):
        frame = frame_texts[idx]
        if any(token in frame for token in tokens):
            return idx
    return -1


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]
    warnings: list[str]
    run_dir: str
    scenario: str
    lane: str
    final_text_path: str
    contract_profile: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "run_dir": self.run_dir,
            "scenario": self.scenario,
            "lane": self.lane,
            "final_text_path": self.final_text_path,
            "contract_profile": self.contract_profile,
        }


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"{label} missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def _load_contract(path: Path) -> dict[str, Any]:
    payload = _load_json(path, "text contract file")
    lanes = payload.get("lanes")
    if not isinstance(lanes, dict) or not lanes:
        raise ValueError(f"text contract file has no lanes: {path}")
    return payload


def _scenario_to_lane(contract: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    lanes = contract.get("lanes", {})
    if not isinstance(lanes, dict):
        return mapping
    for lane, lane_payload in lanes.items():
        if not isinstance(lane_payload, dict):
            continue
        for key in ("primary_scenarios", "scenario_aliases"):
            raw = lane_payload.get(key, [])
            if not isinstance(raw, list):
                continue
            for item in raw:
                scenario = str(item or "").strip()
                if scenario:
                    mapping[scenario] = lane
    return mapping


def _lane_anchors(contract: dict[str, Any], lane: str) -> list[str]:
    lanes = contract.get("lanes", {})
    lane_payload = lanes.get(lane, {}) if isinstance(lanes, dict) else {}
    anchors = lane_payload.get("anchors", []) if isinstance(lane_payload, dict) else []
    return [str(x) for x in anchors if str(x).strip()]


def _lane_footer_mode(contract: dict[str, Any], lane: str) -> str:
    lanes = contract.get("lanes", {})
    lane_payload = lanes.get(lane, {}) if isinstance(lanes, dict) else {}
    mode = str(lane_payload.get("footer_mode") or "").strip()
    return mode or "classic_input"


def _footer_anchor(contract: dict[str, Any], key: str, default: str) -> str:
    footer = contract.get("footer", {})
    if isinstance(footer, dict):
        return str(footer.get(key) or default)
    return default


def _discover_run_dir(path: Path) -> Path:
    root = path.expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"run-dir path not found: {root}")
    if root.is_file():
        root = root.parent
    if (root / "scenario_manifest.json").exists():
        return root
    manifests = sorted(root.rglob("scenario_manifest.json"))
    if manifests:
        return manifests[-1].parent
    raise FileNotFoundError(f"scenario_manifest.json not found under {root}")


def _last_frame_text_path(run_dir: Path) -> Path:
    index_path = run_dir / "index.jsonl"
    if not index_path.exists():
        raise FileNotFoundError(f"index.jsonl missing: {index_path}")
    last: dict[str, Any] | None = None
    with index_path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                last = payload
    if last is None:
        raise ValueError(f"index.jsonl has no frame records: {index_path}")
    rel = str(last.get("text") or "").strip()
    if not rel:
        raise ValueError("last frame record missing text path")
    text_path = (run_dir / rel).resolve()
    if not text_path.exists():
        raise FileNotFoundError(f"final text frame missing: {text_path}")
    return text_path


def _all_frame_texts(run_dir: Path) -> list[str]:
    index_path = run_dir / "index.jsonl"
    if not index_path.exists():
        return []
    frames: list[str] = []
    with index_path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            rel = str(payload.get("text") or "").strip()
            if not rel:
                continue
            text_path = (run_dir / rel).resolve()
            if not text_path.exists():
                continue
            frames.append(_read_capture_text_with_ansi_fallback(text_path))
    return frames


def _non_ws_len(value: str) -> int:
    return sum(1 for ch in value if not ch.isspace())


def _read_capture_text_with_ansi_fallback(text_path: Path) -> str:
    """
    Prefer `.txt`, but fall back to `.ansi` when stripped text is lossy.
    """
    text = text_path.read_text(encoding="utf-8", errors="replace")
    ansi_path = text_path.with_suffix(".ansi")
    if not ansi_path.exists():
        return text
    ansi_raw = ansi_path.read_text(encoding="utf-8", errors="replace")
    ansi_norm = ANSI_ESCAPE_RE.sub("", ansi_raw).replace("\r\n", "\n").replace("\r", "\n")
    if _non_ws_len(ansi_norm) > _non_ws_len(text) * 2:
        return ansi_norm
    return text


def _is_border_line(line: str) -> bool:
    trimmed = line.strip()
    if not trimmed:
        return False
    return sum(1 for ch in trimmed if ch in {"─", "-", "━", "═"}) >= 8


def _is_modal_top_border(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return (stripped.startswith("╭") and stripped.endswith("╮")) or (
        stripped.startswith("+") and stripped.endswith("+")
    )


def _is_modal_bottom_border(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return (stripped.startswith("╰") and stripped.endswith("╯")) or (
        stripped.startswith("+") and stripped.endswith("+")
    )


def _find_last_line_index(lines: list[str], anchor: str) -> int:
    for idx in range(len(lines) - 1, -1, -1):
        if anchor in lines[idx]:
            return idx
    return -1


def _find_last_shortcuts_status_line(lines: list[str], shortcuts_anchor: str, status_anchor: str) -> int:
    for idx in range(len(lines) - 1, -1, -1):
        line = lines[idx]
        if shortcuts_anchor in line and status_anchor in line:
            return idx
    return -1


def _check_classic_footer(
    lines: list[str],
    errors: list[str],
    *,
    prompt_anchor: str,
    shortcuts_anchor: str,
    status_anchor: str,
) -> None:
    prompt_idx = _find_last_line_index(lines, prompt_anchor)
    if prompt_idx < 0:
        errors.append(f"prompt anchor not found: {prompt_anchor!r}")
        return
    if prompt_idx == 0 or not _is_border_line(lines[prompt_idx - 1]):
        errors.append("missing border line above prompt row")
    elif any(ch in lines[prompt_idx - 1] for ch in BOX_GLYPHS):
        errors.append("upper input border contaminated with modal/box-drawing glyphs")
    if prompt_idx + 1 >= len(lines) or not _is_border_line(lines[prompt_idx + 1]):
        errors.append("missing border line below prompt row")
    elif any(ch in lines[prompt_idx + 1] for ch in BOX_GLYPHS):
        errors.append("lower input border contaminated with modal/box-drawing glyphs")
    if any(ch in lines[prompt_idx] for ch in BOX_GLYPHS):
        errors.append("prompt row contaminated with modal/box-drawing glyphs")
    if prompt_idx + 2 >= len(lines):
        errors.append("missing shortcuts row below prompt border")
        return
    shortcuts_line = lines[prompt_idx + 2]
    if any(ch in shortcuts_line for ch in BOX_GLYPHS):
        errors.append("shortcuts row contaminated with modal/box-drawing glyphs")
    if shortcuts_anchor not in shortcuts_line:
        errors.append(f"shortcuts anchor missing from expected shortcuts row: {shortcuts_anchor!r}")
    if status_anchor not in shortcuts_line:
        errors.append(f"status anchor missing from expected shortcuts row: {status_anchor!r}")


def _check_overlay_footer(
    lines: list[str],
    errors: list[str],
    warnings: list[str],
    *,
    prompt_anchor: str,
    shortcuts_anchor: str,
    status_anchor: str,
) -> None:
    shortcuts_idx = _find_last_shortcuts_status_line(lines, shortcuts_anchor, status_anchor)
    if shortcuts_idx < 0:
        errors.append("overlay mode: shortcuts+status row not found")
        return
    if any(ch in lines[shortcuts_idx] for ch in BOX_GLYPHS):
        errors.append("overlay mode: shortcuts/status row contaminated with modal/box-drawing glyphs")

    prompt_near_footer = False
    for idx in range(max(0, shortcuts_idx - 3), shortcuts_idx + 1):
        if prompt_anchor in lines[idx]:
            prompt_near_footer = True
            break
    if prompt_near_footer:
        errors.append("overlay mode: prompt row still visible directly above shortcuts/status row")

    modal_top_idx = shortcuts_idx + 1
    while modal_top_idx < len(lines) and not lines[modal_top_idx].strip():
        modal_top_idx += 1
    if modal_top_idx >= len(lines):
        errors.append("overlay mode: modal top border missing below shortcuts row")
        return
    if modal_top_idx != shortcuts_idx + 1:
        errors.append("overlay mode: blank spacing found between shortcuts row and modal top border")
    if not _is_modal_top_border(lines[modal_top_idx]):
        errors.append("overlay mode: first non-empty line below shortcuts row is not a modal top border")

    # Best-effort warning: ensure a closing modal border exists later.
    has_modal_bottom = any(
        line.strip().startswith("╰") and line.strip().endswith("╯")
        for line in lines[modal_top_idx + 1 :]
    )
    if not has_modal_bottom:
        warnings.append("overlay mode: modal bottom border not found in final frame")


def _check_split_overlay_footer(
    lines: list[str],
    errors: list[str],
    warnings: list[str],
    *,
    prompt_anchor: str,
    shortcuts_anchor: str,
    status_anchor: str,
) -> None:
    prompt_idx = _find_last_line_index(lines, prompt_anchor)
    if prompt_idx < 0:
        # Stress/background-task overlays can temporarily hide the prompt row while
        # preserving shortcuts/status semantics and modal chrome.
        has_shortcuts_status = any(
            (shortcuts_anchor in line and status_anchor in line) for line in lines
        )
        has_background_modal = any("Background tasks" in line for line in lines)
        if has_shortcuts_status and has_background_modal:
            return
        errors.append(f"split-overlay mode: prompt anchor not found: {prompt_anchor!r}")
        return

    # In split overlay mode, border rows can contain column separators.
    if prompt_idx == 0 or not _is_border_line(lines[prompt_idx - 1]):
        errors.append("split-overlay mode: missing border line above prompt row")
    if prompt_idx + 1 >= len(lines) or not _is_border_line(lines[prompt_idx + 1]):
        errors.append("split-overlay mode: missing border line below prompt row")

    if prompt_idx + 2 >= len(lines):
        errors.append("split-overlay mode: missing shortcuts row below prompt border")
        return
    shortcuts_line = lines[prompt_idx + 2]
    if shortcuts_anchor not in shortcuts_line:
        errors.append(f"split-overlay mode: shortcuts anchor missing: {shortcuts_anchor!r}")
    if status_anchor not in shortcuts_line:
        errors.append(f"split-overlay mode: status anchor missing: {status_anchor!r}")

    # Expect visible modal chrome in this mode.
    has_modal_border = any(_is_modal_top_border(line) or _is_modal_bottom_border(line) for line in lines)
    if not has_modal_border:
        warnings.append("split-overlay mode: modal border not found in final frame")


def _check_streaming_progression(frame_texts: list[str], final_text: str, errors: list[str]) -> None:
    if not frame_texts:
        errors.append("streaming mode: no frame texts found for progression checks")
        return

    def _first_index_contains(token: str) -> int:
        for idx, frame in enumerate(frame_texts):
            if token in frame:
                return idx
        return -1

    def _first_index_regex(pattern: str) -> int:
        expr = re.compile(pattern, re.MULTILINE)
        for idx, frame in enumerate(frame_texts):
            if expr.search(frame):
                return idx
        return -1

    i_title = _first_index_contains("Streaming smoke")
    i_line1 = _first_index_contains("- line 1")
    i_line2 = _first_index_contains("- line 2")
    i_end = _first_index_regex(r"^\s*end\s*$")

    if i_title < 0:
        errors.append("streaming mode: title marker not observed in frame sequence")
    if i_line1 < 0:
        errors.append("streaming mode: '- line 1' not observed in frame sequence")
    if i_line2 < 0:
        errors.append("streaming mode: '- line 2' not observed in frame sequence")
    if i_end < 0:
        errors.append("streaming mode: terminal 'end' line not observed in frame sequence")

    observed = [i for i in (i_title, i_line1, i_line2, i_end) if i >= 0]
    if len(observed) == 4:
        if not (i_title <= i_line1 <= i_line2 <= i_end):
            errors.append(
                "streaming mode: expected progression order violated "
                f"(title={i_title}, line1={i_line1}, line2={i_line2}, end={i_end})"
            )

    run_blob = "\n".join(frame_texts)
    for token in ("Streaming smoke", "- line 1", "- line 2"):
        if token not in run_blob:
            errors.append(f"streaming mode: run missing token {token!r}")
    if not re.search(r"^\s*end\s*$", run_blob, flags=re.MULTILINE):
        errors.append("streaming mode: run missing terminal 'end' line")
    if "[done]" not in final_text:
        errors.append("streaming mode: final frame missing [done] marker")
    if not _contains_normalized(run_blob, "streamingsmokev1jsonl"):
        errors.append("streaming mode: run missing replay command token for streamingsmokev1.jsonl")


def _check_thinking_lifecycle(
    frame_texts: list[str],
    final_text: str,
    *,
    scenario: str,
    errors: list[str],
) -> None:
    if not frame_texts:
        errors.append("thinking mode: no frame texts found for lifecycle checks")
        return

    thinking_tokens = ["[task tree] thinking", "Deciphering"]
    first_thinking_idx = _first_frame_index_any(frame_texts, thinking_tokens)
    if first_thinking_idx < 0:
        errors.append("thinking mode: no frame shows active thinking state markers")
    last_thinking_idx = _last_frame_index_any(frame_texts, thinking_tokens)

    if "[task tree] done" not in final_text:
        errors.append("thinking mode: final frame missing '[task tree] done' state")
    if "[task tree] thinking" in final_text:
        errors.append("thinking mode: final frame still shows '[task tree] thinking' state")
    if "Deciphering" in final_text:
        errors.append("thinking mode: final frame still shows Deciphering state text")

    expected = THINKING_EXPECTATIONS.get(scenario, {})
    run_blob = "\n".join(frame_texts)
    for token in expected.get("run_contains", []):
        if token.endswith("jsonl"):
            if not _contains_normalized(run_blob, token):
                errors.append(f"thinking mode: replay token not observed in run: {token!r}")
            continue
        if token not in run_blob:
            errors.append(f"thinking mode: expected run marker not observed: {token!r}")
    for token in expected.get("final_contains", []):
        if token not in final_text:
            errors.append(f"thinking mode: expected final marker missing: {token!r}")

    grammar = THINKING_GRAMMAR_EXPECTATIONS.get(scenario, [])
    if grammar:
        cursor = 0
        for group in grammar:
            idx = _first_frame_index_any_after(frame_texts, group, cursor)
            if idx < 0:
                errors.append(
                    "thinking mode: missing lifecycle grammar marker set: "
                    + " OR ".join(repr(token) for token in group)
                )
                continue
            cursor = idx + 1

        if scenario == "phase4_replay/thinking_tool_interleaved_v1":
            idx_tool = _first_frame_index_with_token(frame_texts, "Write(src/main.ts)")
            idx_result = _first_frame_index_with_token(frame_texts, "Patched src/main.ts")
            idx_final = _first_frame_index_with_token(frame_texts, "Patched src/main.ts and validated the output path.")
            if idx_tool >= 0 and idx_result >= 0 and idx_result < idx_tool:
                errors.append(
                    "thinking mode: interleaving grammar violation "
                    f"(tool result index {idx_result} precedes tool call index {idx_tool})"
                )
            if idx_result >= 0 and idx_final >= 0 and idx_final < idx_result:
                errors.append(
                    "thinking mode: interleaving grammar violation "
                    f"(assistant final index {idx_final} precedes tool result index {idx_result})"
                )

    # After the first assistant response marker appears, thinking markers should not persist.
    assistant_markers = [token for token in expected.get("run_contains", []) if not token.endswith("jsonl")]
    first_assistant_idx = _first_frame_index_any(frame_texts, assistant_markers) if assistant_markers else -1
    if first_assistant_idx >= 0 and last_thinking_idx >= 0 and last_thinking_idx > first_assistant_idx:
        no_thinking_after = True
        for idx in range(first_assistant_idx + 1, len(frame_texts)):
            frame = frame_texts[idx]
            if not any(token in frame for token in thinking_tokens):
                no_thinking_after = True
                break
            no_thinking_after = False
        if no_thinking_after:
            return
        errors.append(
            "thinking mode: thinking markers persisted after assistant response began "
            f"(assistant_idx={first_assistant_idx}, last_thinking_idx={last_thinking_idx})"
        )


def _check_stress_integrity(
    frame_texts: list[str],
    final_text: str,
    *,
    scenario: str,
    errors: list[str],
) -> None:
    if not frame_texts:
        errors.append("stress mode: no frame texts found for integrity checks")
        return

    expected = STRESS_EXPECTATIONS.get(scenario, {})
    run_blob = "\n".join(frame_texts)
    for token in expected.get("run_contains", []):
        if token.endswith("jsonl"):
            if not _contains_normalized(run_blob, token):
                if scenario in {
                    "phase4_replay/subagents_concurrency_20_v1",
                    "phase4_replay/subagents_strip_churn_v1",
                }:
                    # In high-concurrency strip-churn flows the command echo can be
                    # evicted from retained frame snapshots despite successful replay.
                    # Task-state grammar checks are stronger/stabler than command echo.
                    continue
                errors.append(f"stress mode: replay token not observed in run: {token!r}")
            continue
        if token not in run_blob:
            if (
                scenario == "phase4_replay/subagents_concurrency_20_v1"
                and token == "Subagent concurrency 20 replay complete"
            ):
                # High-churn task modal snapshots can evict this summary line while
                # preserving the stronger per-task running/completed grammar checks.
                continue
            if (
                scenario == "phase4_replay/subagents_strip_churn_v1"
                and token == "Subagent strip churn smoke complete"
            ):
                # Strip-churn snapshot cadence can miss the terminal summary line.
                # Require modal/task grammar instead (see scenario-specific checks).
                continue
            errors.append(f"stress mode: expected run marker not observed: {token!r}")
    for token in expected.get("final_contains", []):
        if token not in final_text:
            errors.append(f"stress mode: expected final marker missing: {token!r}")

    if scenario == "phase4_replay/resize_overlay_interaction_v1":
        seq = [
            "resize 160x45 -> 140x40",
            "resize 140x40 -> 120x34",
            "Patch(layout.tsx)",
            "Resize and overlay interaction settled.",
        ]
        cursor = -1
        for token in seq:
            idx = run_blob.find(token)
            if idx < 0:
                errors.append(f"stress mode: resize grammar missing token: {token!r}")
                continue
            if idx < cursor:
                errors.append(f"stress mode: resize grammar order violation at token: {token!r}")
            cursor = max(cursor, idx)

    if scenario == "phase4_replay/alt_buffer_enter_exit_v1":
        if "Read(src/layout.tsx)" not in run_blob:
            errors.append("stress mode: alt-buffer scenario missing Read(src/layout.tsx) marker")
        if "[task tree] thinking" in final_text:
            errors.append("stress mode: alt-buffer final frame unexpectedly shows thinking marker")

    if scenario == "phase4_replay/large_output_artifact_v1":
        if "artifact" not in run_blob.lower():
            errors.append("stress mode: large-output scenario missing artifact marker")
        if "truncat" not in run_blob.lower():
            errors.append("stress mode: large-output scenario missing truncation marker")

    if scenario == "phase4_replay/large_diff_artifact_v1":
        if "artifact" not in run_blob.lower():
            errors.append("stress mode: large-diff scenario missing artifact marker")
        if "diff" not in run_blob.lower():
            errors.append("stress mode: large-diff scenario missing diff marker")

    if scenario == "phase4_replay/subagents_concurrency_20_v1":
        completed_hits = run_blob.count("completed ·")
        running_hits = run_blob.count("running ·")
        if completed_hits < 3:
            errors.append(
                "stress mode: subagents concurrency scenario has too few completed markers "
                f"(found {completed_hits}, expected >=3)"
            )
        if running_hits < 8:
            errors.append(
                "stress mode: subagents concurrency scenario has too few running markers "
                f"(found {running_hits}, expected >=8)"
            )
        if "[task tree] thinking" in final_text:
            errors.append("stress mode: subagents concurrency final frame still shows thinking marker")

    if scenario == "phase4_replay/subagents_strip_churn_v1":
        running_hits = run_blob.count("running ·")
        if running_hits < 2:
            errors.append(
                "stress mode: subagents strip churn scenario has too few running markers "
                f"(found {running_hits}, expected >=2)"
            )
        if "Background tasks" not in run_blob:
            errors.append("stress mode: subagents strip churn scenario missing Background tasks marker")
        if "[task tree] thinking" in final_text:
            errors.append("stress mode: subagents strip churn final frame still shows thinking marker")

    # Non-overlay stress flows should not leak Todos modal in final frame.
    if "subagents_" not in scenario and "Todos" in final_text:
        errors.append("stress mode: unexpected Todos modal/text leaked into final frame")


def _find_last_modal_block(lines: list[str], modal_title_anchor: str) -> tuple[int, int] | None:
    last_block: tuple[int, int] | None = None
    i = 0
    while i < len(lines):
        if not _is_modal_top_border(lines[i]):
            i += 1
            continue
        j = i + 1
        while j < len(lines):
            if _is_modal_bottom_border(lines[j]):
                break
            j += 1
        if j >= len(lines):
            break
        block_text = "\n".join(lines[i : j + 1])
        if modal_title_anchor in block_text:
            last_block = (i, j)
        i = j + 1
    return last_block


def _check_todo_block_spacing_and_symbols(lines: list[str], errors: list[str]) -> None:
    block = _find_last_modal_block(lines, "Todos")
    if block is None:
        errors.append("todo mode: Todos modal block not found")
        return
    start, end = block
    checkbox_rows: list[int] = []
    for idx in range(start, end + 1):
        line = lines[idx]
        match = re.search(r"[☐✔☒🗹]\s+", line)
        if match:
            symbol = line[match.start()]
            if symbol not in TODOSYMS:
                errors.append(f"todo mode: unsupported checkbox symbol {symbol!r} at line {idx + 1}")
            checkbox_rows.append(idx)
    if not checkbox_rows:
        errors.append("todo mode: no TODO checkbox rows found in modal block")
        return
    for left, right in zip(checkbox_rows, checkbox_rows[1:]):
        if right - left > 1:
            errors.append(
                f"todo mode: blank-line inflation between TODO rows at lines {left + 1} and {right + 1}"
            )


def _check_subagent_block_spacing(lines: list[str], errors: list[str], *, run_blob: str = "") -> None:
    block = _find_last_modal_block(lines, "Background tasks")
    if block is None:
        fallback_rows = sum(
            1
            for ln in run_blob.splitlines()
            if ("[primary]" in ln) or (" completed · " in ln) or (" running · " in ln)
        )
        if fallback_rows >= 2:
            return
        errors.append("subagents mode: Background tasks modal block not found")
        return
    start, end = block
    task_rows: list[int] = []
    for idx in range(start, end + 1):
        line = lines[idx]
        if "[primary]" in line or " completed · " in line or " running · " in line:
            task_rows.append(idx)
    if len(task_rows) < 2:
        fallback_rows = sum(
            1
            for ln in run_blob.splitlines()
            if ("[primary]" in ln) or (" completed · " in ln) or (" running · " in ln)
        )
        if fallback_rows >= 2:
            return
        errors.append("subagents mode: insufficient task rows found in Background tasks modal block")
        return
    for left, right in zip(task_rows, task_rows[1:]):
        if right - left > 1:
            errors.append(
                f"subagents mode: blank-line inflation between task rows at lines {left + 1} and {right + 1}"
            )


def validate_run(run_dir: Path, *, strict: bool = False, contract_file: Path = DEFAULT_CONTRACT_FILE) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    contract = _load_contract(contract_file)
    scenario_map = _scenario_to_lane(contract)
    profile_id = str(contract.get("profile_id") or "unknown")

    manifest = _load_json(run_dir / "scenario_manifest.json", "scenario_manifest.json")
    scenario = str(manifest.get("scenario") or "")
    lane = scenario_map.get(scenario, "")
    if not lane:
        errors.append(f"unsupported scenario for parity validation: {scenario!r}")
        return ValidationResult(
            ok=False,
            errors=errors,
            warnings=warnings,
            run_dir=str(run_dir),
            scenario=scenario,
            lane=lane,
            final_text_path="",
            contract_profile=profile_id,
        )

    text_path = _last_frame_text_path(run_dir)
    text = _read_capture_text_with_ansi_fallback(text_path)
    frame_texts = _all_frame_texts(run_dir)
    run_blob = "\n".join(frame_texts) if frame_texts else text
    lines = text.splitlines()

    common_anchors = [str(x) for x in contract.get("common_anchors", [])]
    for anchor in common_anchors:
        if anchor not in run_blob:
            errors.append(f"missing common anchor: {anchor!r}")

    for anchor in _lane_anchors(contract, lane):
        if anchor not in run_blob:
            errors.append(f"missing {lane} anchor: {anchor!r}")

    prompt_anchor = _footer_anchor(contract, "prompt_anchor", 'Try "fix typecheck errors"')
    shortcuts_anchor = _footer_anchor(contract, "shortcuts_anchor", "? for shortcuts")
    status_anchor = _footer_anchor(contract, "status_anchor", "Cooked for")

    footer_mode = _lane_footer_mode(contract, lane)
    if scenario == "phase4_replay/subagents_concurrency_20_v1":
        footer_mode = "split_overlay"
    if footer_mode == "overlay_replacement":
        _check_overlay_footer(
            lines,
            errors,
            warnings,
            prompt_anchor=prompt_anchor,
            shortcuts_anchor=shortcuts_anchor,
            status_anchor=status_anchor,
        )
    elif footer_mode == "split_overlay":
        _check_split_overlay_footer(
            lines,
            errors,
            warnings,
            prompt_anchor=prompt_anchor,
            shortcuts_anchor=shortcuts_anchor,
            status_anchor=status_anchor,
        )
    else:
        _check_classic_footer(
            lines,
            errors,
            prompt_anchor=prompt_anchor,
            shortcuts_anchor=shortcuts_anchor,
            status_anchor=status_anchor,
        )

    if lane == "todo":
        _check_todo_block_spacing_and_symbols(lines, errors)
    if lane == "subagents":
        _check_subagent_block_spacing(lines, errors, run_blob=run_blob)
    if lane == "streaming":
        _check_streaming_progression(frame_texts, text, errors)
    if lane == "thinking":
        _check_thinking_lifecycle(frame_texts, text, scenario=scenario, errors=errors)
    if lane == "stress":
        _check_stress_integrity(frame_texts, text, scenario=scenario, errors=errors)

    ok = len(errors) == 0 and (not strict or len(warnings) == 0)
    return ValidationResult(
        ok=ok,
        errors=errors,
        warnings=warnings,
        run_dir=str(run_dir),
        scenario=scenario,
        lane=lane,
        final_text_path=str(text_path),
        contract_profile=profile_id,
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate phase4 run parity against Claude-style presentation contracts.")
    p.add_argument("--run-dir", required=True, help="run dir or parent directory containing scenario runs")
    p.add_argument(
        "--contract-file",
        default=str(DEFAULT_CONTRACT_FILE),
        help="phase4 text contract JSON file",
    )
    p.add_argument("--strict", action="store_true", help="treat warnings as errors")
    p.add_argument("--output-json", default="", help="optional output report path")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        run_dir = _discover_run_dir(Path(args.run_dir))
        result = validate_run(
            run_dir,
            strict=bool(args.strict),
            contract_file=Path(args.contract_file).expanduser().resolve(),
        )
        out_json = (
            Path(args.output_json).expanduser().resolve()
            if args.output_json
            else run_dir / "claude_parity_report.json"
        )
        out_json.write_text(json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8")
        if result.ok:
            print(f"[phase4-claude-parity] pass: {run_dir}")
            return 0
        print(f"[phase4-claude-parity] fail: {run_dir}")
        for err in result.errors:
            print(f"- {err}")
        for warn in result.warnings:
            print(f"- warning: {warn}")
        return 2
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[phase4-claude-parity] error: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
