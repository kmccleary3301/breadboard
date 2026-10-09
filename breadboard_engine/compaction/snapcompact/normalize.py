"""Snapcompact text normalization, stopword dimming, and conversation serialization."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from .renderer import DIM_OFF, DIM_ON, FULL_BLOCK, snapcompact_supported_chars

NEWLINE_GLYPH = "\u2588"

CHAR_FOLD: Dict[str, str] = {
    # Quotation marks and primes.
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
    "\u201b": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u2032": "'",
    "\u2033": '"',
    "\u2035": "'",
    "\u2036": '"',
    "\u2039": "<",
    "\u203a": ">",
    # Dashes, hyphens, and the fraction slash NFKD leaves in vulgar fractions.
    "\u2010": "-",
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "-",
    "\u2014": "-",
    "\u2015": "-",
    "\u2212": "-",
    "\u2044": "/",
    # Dot leaders and ellipses.
    "\u2024": ".",
    "\u2025": "..",
    "\u2026": "...",
    "\u22ef": "...",
    # Bullets.
    "\u2022": "*",
    "\u2023": "*",
    "\u2043": "-",
    "\u2219": "*",
    "\u25cf": "*",
    "\u25a0": "*",
    "\u25aa": "*",
    # Arrows.
    "\u2190": "<-",
    "\u2191": "^",
    "\u2192": "->",
    "\u2193": "v",
    "\u2194": "<->",
    "\u21d0": "<=",
    "\u21d2": "=>",
    "\u21d4": "<=>",
    # Check marks and crosses.
    "\u2713": "v",
    "\u2714": "v",
    "\u2717": "x",
    "\u2718": "x",
    "\u2715": "x",
    "\u2716": "x",
}

EMOJI_FOLD: Dict[str, str] = {
    "✅": "[OK]",
    "☑": "[OK]",
    "✔": "[OK]",
    "❌": "[FAIL]",
    "❎": "[FAIL]",
    "✖": "[FAIL]",
    "⚠": "[WARN]",
    "⚠️": "[WARN]",
    "🚨": "[ALERT]",
    "ℹ": "[INFO]",
    "ℹ️": "[INFO]",
    "🐛": "[BUG]",
    "💥": "[CRASH]",
    "🔥": "[HOT]",
    "🔒": "[LOCK]",
    "🔓": "[UNLOCK]",
    "📁": "[DIR]",
    "📂": "[DIR]",
    "📄": "[FILE]",
    "📝": "[NOTE]",
    "🧪": "[TEST]",
    "⏳": "[WAIT]",
    "⌛": "[WAIT]",
    "🚀": "[RUN]",
}

ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
LINE_BREAK_PATTERN = re.compile(r"[\n\r\u2028\u2029]")
EDGE_RUNS = re.compile(r"^[ \u2588]+|[ \u2588]+$")

STOPWORDS: Set[str] = set(
    (
        "the a an and or of to in on at as is are was were be been by for with that this it its from had has have not but "
        "he she his her they their them which also who whom when where while will would could should there then than "
        "into over under about after before between during each such these those some most more other only same so"
    ).split()
)

ALPHA_RUN = re.compile(r"[a-zA-Z\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u00ff]+")
DIM_MARKER_SPLIT = re.compile(r"([\u000e\u000f])")


def _is_ascii_or_latin1(cp: int) -> bool:
    return (0x20 <= cp < 0x7F) or (0xA0 <= cp <= 0xFF)


def _is_emoji_pictograph(ch: str) -> bool:
    # Match standard emoji ranges
    cp = ord(ch)
    return (
        0x1F300 <= cp <= 0x1FAFF
        or 0x2600 <= cp <= 0x27BF
        or 0xFE00 <= cp <= 0xFE0F
        or 0x1F900 <= cp <= 0x1F9FF
    )


def _is_unrenderable(ch: str) -> bool:
    cat = unicodedata.category(ch)
    return cat in ("Cc", "Mn", "Me", "Cs")


def _fold_to_ascii(ch: str) -> Optional[str]:
    decomposed = unicodedata.normalize("NFKD", ch)
    # Strip combining marks
    decomposed = "".join(c for c in decomposed if unicodedata.category(c) not in ("Mn", "Me", "Mc"))
    if decomposed == ch:
        return None
    out = []
    for part in decomposed:
        cp = ord(part)
        if _is_ascii_or_latin1(cp):
            out.append(part)
        elif part in CHAR_FOLD:
            out.append(CHAR_FOLD[part])
        else:
            return None
    return "".join(out)


def normalize(text: str, options: Optional[Any] = None) -> str:
    """Prepare text for snapcompact printing."""
    font_name = "5x8"
    if options is not None:
        if isinstance(options, Mapping):
            font_name = options.get("font") or (options.get("shape", {}).font if hasattr(options.get("shape"), "font") else "5x8")
        elif hasattr(options, "font"):
            font_name = options.font
        elif hasattr(options, "shape") and hasattr(options.shape, "font"):
            font_name = options.shape.font

    # Strip ANSI
    if "\x1b" in text:
        text = ANSI_PATTERN.sub("", text)

    # Collapse whitespace runs
    # Runs containing line break become NEWLINE_GLYPH; pure format chars vanish; other whitespace becomes single space
    def _replace_run(m: re.Match) -> str:
        s = m.group(0)
        if LINE_BREAK_PATTERN.search(s):
            return NEWLINE_GLYPH
        # If contains genuine whitespace
        if any(not unicodedata.category(c).startswith("C") for c in s):
            return " "
        return ""

    collapsed = re.sub(r"[\s\u200b-\u200f\ufeff]+", _replace_run, text)
    collapsed = EDGE_RUNS.sub("", collapsed)

    out: List[str] = []
    for ch in collapsed:
        cp = ord(ch)
        if _is_ascii_or_latin1(cp):
            out.append(ch)
            continue
        if ch in ("\u000E", "\u000F", NEWLINE_GLYPH):
            out.append(ch)
            continue
        if ch in EMOJI_FOLD:
            out.append(EMOJI_FOLD[ch])
            continue
        if ch in CHAR_FOLD:
            out.append(CHAR_FOLD[ch])
            continue
        if 0x2500 <= cp <= 0x257F:
            # Box drawing
            if cp in (0x2502, 0x2503):
                out.append("|")
            elif cp in (0x2500, 0x2501):
                out.append("-")
            else:
                out.append("+")
            continue

        if not _is_emoji_pictograph(ch):
            # Check if supported by font or Silver
            try:
                supp = snapcompact_supported_chars(font_name, ch) or snapcompact_supported_chars("silver", ch)
                if supp:
                    out.append(ch)
                    continue
            except Exception:
                pass

        folded = _fold_to_ascii(ch)
        if folded is not None:
            out.append(folded)
        elif _is_emoji_pictograph(ch):
            # Drop decorative emoji
            continue
        elif not _is_unrenderable(ch):
            out.append("?")

    result = "".join(out)
    result = re.sub(r" +", " ", result)
    result = EDGE_RUNS.sub("", result)
    return result


def scan_renderability(text: str, options: Optional[Any] = None) -> Tuple[bool, float]:
    norm = normalize(text, options)
    # Count unrenderable ratio
    graphic_chars = sum(1 for c in norm if not unicodedata.category(c).startswith("C") and c not in (" ", NEWLINE_GLYPH))
    question_marks = norm.count("?")
    if graphic_chars == 0:
        return True, 0.0
    ratio = question_marks / graphic_chars
    return ratio <= 0.05, ratio


def dim_stopwords(text: str) -> str:
    parts = DIM_MARKER_SPLIT.split(text)
    dim = False
    out: List[str] = []
    for part in parts:
        if part == "\u000E":
            dim = True
            out.append(part)
        elif part == "\u000F":
            dim = False
            out.append(part)
        elif dim:
            out.append(part)
        else:
            def _dim_word(m: re.Match) -> str:
                w = m.group(0)
                if w.lower() in STOPWORDS:
                    return f"\u000E{w}\u000F"
                return w
            out.append(ALPHA_RUN.sub(_dim_word, part))
    return "".join(out)


def _truncate_text(text: str, max_chars: int, head_ratio: float = 0.6) -> str:
    if len(text) <= max_chars or max_chars <= 0:
        return text
    head_len = int(max_chars * head_ratio)
    tail_len = max_chars - head_len
    elided = len(text) - head_len - tail_len
    return f"{text[:head_len]}[…{elided}ch elided…]{text[-tail_len:]}"


def serialize_conversation(
    messages: Sequence[Mapping[str, Any]],
    *,
    tool_result_max_chars: int = 2000,
    tool_arg_max_chars: int = 500,
    tool_call_max_chars: int = 2000,
    truncate_head_ratio: float = 0.6,
    include_thinking: bool = True,
    dim_tool_results: bool = True,
) -> str:
    """Format conversation into archive transcript text."""
    # Pre-map tool calls and tool results
    useless_call_ids: Set[str] = set()
    tool_results_by_id: Dict[str, Mapping[str, Any]] = {}
    for msg in messages:
        role = msg.get("role")
        if role in ("tool", "toolResult"):
            call_id = msg.get("tool_call_id")
            if isinstance(call_id, str):
                tool_results_by_id[call_id] = msg
                if msg.get("useless") is True:
                    useless_call_ids.add(call_id)

    parts: List[str] = []

    def _render_result(res_msg: Mapping[str, Any]) -> str:
        content = res_msg.get("content", "")
        if isinstance(content, list):
            text_blocks = []
            for b in content:
                if isinstance(b, Mapping) and b.get("type") == "text":
                    text_blocks.append(str(b.get("text", "")))
                elif isinstance(b, str):
                    text_blocks.append(b)
            raw = "\n".join(text_blocks)
        else:
            raw = str(content)
        truncated = _truncate_text(raw, tool_result_max_chars, truncate_head_ratio)
        if dim_tool_results:
            return f"\u000E{truncated}\u000F"
        return truncated

    merged_call_ids: Set[str] = set()

    for msg in messages:
        role = msg.get("role")
        if role == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                parts_text = []
                for p in content:
                    if isinstance(p, Mapping) and p.get("type") == "text":
                        parts_text.append(str(p.get("text", "")))
                    elif isinstance(p, str):
                        parts_text.append(p)
                raw = " ".join(parts_text)
            else:
                raw = str(content)
            if raw.strip():
                parts.append(f"¶user:{raw}")

        elif role == "assistant":
            # Assistant content: may have thinking, text, tool_calls
            content = msg.get("content", "")
            thinking_parts: List[str] = []
            text_parts: List[str] = []

            if isinstance(content, list):
                for b in content:
                    if isinstance(b, Mapping):
                        b_type = b.get("type")
                        if b_type == "thinking" and include_thinking:
                            t = b.get("thinking", "")
                            if t:
                                thinking_parts.append(t)
                        elif b_type == "text":
                            t = b.get("text", "")
                            if t:
                                text_parts.append(t)
            elif isinstance(content, str) and content.strip():
                text_parts.append(content)

            if thinking_parts and include_thinking:
                parts.append(f"¶think:{' '.join(thinking_parts)}")

            if text_parts:
                parts.append(f"¶ai:{' '.join(text_parts)}")

            # Tool calls
            calls = msg.get("tool_calls") or ()
            call_lines: List[str] = []
            for call in calls:
                if not isinstance(call, Mapping):
                    continue
                c_id = str(call.get("id", ""))
                if c_id in useless_call_ids:
                    continue
                func = call.get("function") if isinstance(call.get("function"), Mapping) else call
                name = str(func.get("name", ""))
                args = func.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {"args": args}
                if not isinstance(args, Mapping):
                    args = {}

                intent = call.get("intent") or args.get("intent") or args.get("i")
                intent_comment = ""
                if intent:
                    intent_str = str(intent).replace("\n", " ").strip()
                    intent_comment = f"//{intent_str}"

                # Format args
                arg_strs = []
                for k, v in args.items():
                    if k in ("intent", "i"):
                        continue
                    v_str = json.dumps(v, ensure_ascii=False)
                    if len(v_str) > tool_arg_max_chars:
                        v_str = _truncate_text(v_str, tool_arg_max_chars)
                    arg_strs.append(f"{k}={v_str}")
                all_args = ", ".join(arg_strs)
                if len(all_args) > tool_call_max_chars:
                    all_args = _truncate_text(all_args, tool_call_max_chars)

                call_header = f"{name}({all_args}){intent_comment}"

                # Check if paired result exists
                paired = tool_results_by_id.get(c_id)
                if paired is not None:
                    merged_call_ids.add(c_id)
                    res_body = _render_result(paired)
                    call_lines.append(f"{call_header}\n<out>\n{res_body}\n</out>")
                else:
                    call_lines.append(call_header)

            if call_lines:
                parts.append(f"¶call:{chr(10).join(call_lines)}")

        elif role in ("tool", "toolResult"):
            call_id = str(msg.get("tool_call_id", ""))
            if call_id in useless_call_ids or call_id in merged_call_ids:
                continue
            res_body = _render_result(msg)
            parts.append(f"¶call:\n<out>\n{res_body}\n</out>")

    return "\n\n".join(parts)


def compute_file_lists(file_ops: Any) -> Tuple[List[str], List[str]]:
    """Return sorted lists of read and modified file paths, excluding URL schemes."""
    read_set: Set[str] = set()
    modified_set: Set[str] = set()

    if isinstance(file_ops, Mapping):
        read_set = set(file_ops.get("read") or ())
        edited_set = set(file_ops.get("edited") or ())
        written_set = set(file_ops.get("written") or ())
        modified_set = edited_set | written_set
    elif hasattr(file_ops, "read"):
        read_set = set(getattr(file_ops, "read", ()))
        edited_set = set(getattr(file_ops, "edited", ()))
        written_set = set(getattr(file_ops, "written", ()))
        modified_set = edited_set | written_set

    # Filter out scheme:// URLs like artifact://, conflict://, local://
    scheme_re = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
    clean_modified = {f for f in modified_set if not scheme_re.match(f)}
    clean_read = {f for f in read_set if not scheme_re.match(f) and f not in clean_modified}

    return sorted(clean_read), sorted(clean_modified)
