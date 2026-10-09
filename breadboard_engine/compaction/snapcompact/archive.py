"""Snapcompact archive planning, rendering, and continuity management."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .normalize import (
    NEWLINE_GLYPH,
    compute_file_lists,
    dim_stopwords,
    normalize,
    serialize_conversation,
)
from .renderer import (
    DIM_OFF,
    DIM_ON,
    cell_units,
    is_wide,
    render_snapcompact_png,
)
from .shapes import (
    FRAME_DATA_BYTES_BUDGET,
    MAX_FRAMES_DEFAULT,
    SHAPE_VARIANTS,
    Shape,
    geometry,
    price_shape,
    resolve_shape,
)

HQ_EDGE_FRAMES = 3
TEXT_EDGE_PAGES = 1


def _uses_wide_cells(shape: Shape) -> bool:
    return shape.font != "silver"


def _char_cells(ch: str, wide_cells: bool) -> int:
    code = ord(ch) if ch else 0
    if code in (DIM_ON, DIM_OFF):
        return 0
    if wide_cells and is_wide(code):
        return 2
    return 1


def _cell_length(text: str, wide_cells: bool) -> int:
    return sum(_char_cells(ch, wide_cells) for ch in text)


def _slice_cells(text: str, width: int, wide_cells: bool) -> str:
    cells = 0
    out = []
    placed = False
    for ch in text:
        w = _char_cells(ch, wide_cells)
        if placed and cells + w > width:
            break
        out.append(ch)
        cells += w
        if w > 0:
            placed = True
    return "".join(out)


def paginate_cells(text: str, capacity: int, cols: int, wide_cells: bool) -> List[str]:
    """Split text into pages that each fill at most capacity grid cells."""
    chars = list(text)
    pages: List[str] = []
    start = 0
    cell = 0
    has_cell = False
    for i, ch in enumerate(chars):
        w = _char_cells(ch, wide_cells)
        if w == 0:
            continue
        at = cell
        if w == 2 and cols >= 2 and at % cols == cols - 1:
            at += 1
        if has_cell and at + w > capacity:
            pages.append("".join(chars[start:i]))
            start = i
            at = 0
        cell = at + w
        has_cell = True
    if has_cell:
        pages.append("".join(chars[start:]))
    return pages


def wrap(text: str, width: int, wide_cells: bool = False) -> List[str]:
    lines: List[str] = []
    cur = ""
    cur_cells = 0
    for token in re.split(r"\s+", text):
        if not token:
            continue
        word = token
        word_cells = _cell_length(word, wide_cells)
        while word_cells > width:
            if cur:
                lines.append(cur)
                cur = ""
                cur_cells = 0
            head = _slice_cells(word, width, wide_cells)
            lines.append(head)
            word = word[len(head):]
            word_cells = _cell_length(word, wide_cells)
        if not cur:
            cur = word
            cur_cells = word_cells
        elif cur_cells + 1 + word_cells <= width:
            cur = f"{cur} {word}"
            cur_cells += 1 + word_cells
        else:
            lines.append(cur)
            cur = word
            cur_cells = word_cells
    if cur:
        lines.append(cur)
    return lines


def doc_pages(normalized: str, cols: int, rows: int, wide_cells: bool) -> List[str]:
    lines = wrap(normalized, cols, wide_cells)
    per_page = 2 * rows
    pages: List[str] = []
    for offset in range(0, len(lines), per_page):
        pages.append("\n".join(lines[offset : offset + per_page]))
    return pages


def count_frames(text: str, shape: Shape) -> int:
    norm = normalize(text, shape)
    if not norm:
        return 0
    geo = geometry(shape)
    if shape.columns == 2:
        return len(doc_pages(norm, geo.cols, geo.rows, _uses_wide_cells(shape)))
    pages = paginate_cells(norm, geo.capacity, geo.cols, _uses_wide_cells(shape))
    return max(1, len(pages))


@dataclass
class PlanFrame:
    text: str
    shape: Shape


@dataclass
class ArchiveLayout:
    frames: List[PlanFrame]
    text_head: str
    text_tail: str
    kept_text: str
    truncated_chars: int


def _dense_companion(high: Shape, api: Optional[str]) -> Shape:
    if high.columns == 2 or high.font == "silver":
        return high
    from .shapes import billing_family
    fam = billing_family(api)
    low_key = "6x6u-sent" if fam == "openai" else "6x12-dim" if fam == "anthropic" else "8on16-bw"
    low = price_shape(SHAPE_VARIANTS[low_key], fam)
    if geometry(low).capacity > geometry(high).capacity:
        return low
    return high


def plan_archive(text: str, high: Shape, low: Shape, max_frames: int) -> ArchiveLayout:
    cap_hi = geometry(high).capacity
    edge_cap = TEXT_EDGE_PAGES * cap_hi
    if len(text) <= 2 * edge_cap:
        return ArchiveLayout(frames=[], text_head=text, text_tail="", kept_text=text, truncated_chars=0)

    if max_frames < 1:
        text_head = text[:edge_cap]
        text_tail = text[-edge_cap:]
        return ArchiveLayout(
            frames=[],
            text_head=text_head,
            text_tail=text_tail,
            kept_text=text_head + text_tail,
            truncated_chars=len(text) - len(text_head) - len(text_tail),
        )

    text_head = text[:edge_cap]
    text_tail = text[-edge_cap:]
    image_text = text[edge_cap : len(text) - edge_cap]
    if not image_text:
        return ArchiveLayout(frames=[], text_head=text, text_tail="", kept_text=text, truncated_chars=0)

    # Doc layouts
    if high.columns == 2:
        geo = geometry(high)
        pages = doc_pages(image_text, geo.cols, geo.rows, _uses_wide_cells(high))
        kept = pages
        truncated_chars = 0
        if len(pages) > max_frames:
            dropped = pages[1 : len(pages) - (max_frames - 1)]
            truncated_chars = sum(len(p) for p in dropped)
            kept = [pages[0]] + pages[len(pages) - (max_frames - 1):]
        flat = " ".join(p.replace("\n", " ") for p in kept)
        return ArchiveLayout(
            frames=[PlanFrame(p, high) for p in kept],
            text_head=text_head,
            text_tail=text_tail,
            kept_text=text_head + flat + text_tail,
            truncated_chars=truncated_chars,
        )

    # Grid layout
    hi_pages = paginate_cells(image_text, cap_hi, geometry(high).cols, _uses_wide_cells(high))
    if len(hi_pages) <= max_frames:
        return ArchiveLayout(
            frames=[PlanFrame(p, high) for p in hi_pages],
            text_head=text_head,
            text_tail=text_tail,
            kept_text=text_head + image_text + text_tail,
            truncated_chars=0,
        )

    # Foveate imaged middle: HQ edges, dense center, drop oldest dense slice
    cap_lo = geometry(low).capacity
    image_edge_frames = min(HQ_EDGE_FRAMES, max(0, (max_frames - 1) // 2))
    head_pages = hi_pages[:image_edge_frames]
    tail_pages = hi_pages[-image_edge_frames:] if image_edge_frames > 0 else []
    image_head = "".join(head_pages)
    image_tail = "".join(tail_pages)
    middle_source = image_text[len(image_head) : len(image_text) - len(image_tail)]

    middle_pages = paginate_cells(middle_source, cap_lo, geometry(low).cols, _uses_wide_cells(low))
    middle_budget = max_frames - 2 * image_edge_frames
    truncated_chars = 0
    middle_text = middle_source

    if len(middle_pages) > middle_budget:
        dropped = "".join(middle_pages[: len(middle_pages) - middle_budget])
        truncated_chars = len(dropped)
        middle_text = middle_source[len(dropped):]
        middle_pages = middle_pages[len(middle_pages) - middle_budget:]

    frames = [PlanFrame(p, high) for p in head_pages] + [PlanFrame(p, low) for p in middle_pages] + [PlanFrame(p, high) for p in tail_pages]
    return ArchiveLayout(
        frames=frames,
        text_head=text_head,
        text_tail=text_tail,
        kept_text=text_head + image_head + middle_text + image_tail + text_tail,
        truncated_chars=truncated_chars,
    )


def strip_thinking_sections(text: str) -> str:
    """Drop ¶think: sections from serialized archive source text."""
    segments = text.split(NEWLINE_GLYPH)
    out_segments = []
    for segment in segments:
        sections = re.split(r"\n\n(?=¶(?:user|think|ai|call):)", segment)
        kept = [s for s in sections if not s.startswith("¶think:")]
        if kept:
            out_segments.append("\n\n".join(kept))
    return NEWLINE_GLYPH.join(out_segments)


@dataclass
class Frame:
    data: str  # base64 encoded PNG
    mime_type: str = "image/png"
    cols: int = 0
    rows: int = 0
    chars: int = 0
    font: str = "5x8"
    variant: str = "bw"
    line_repeat: int = 1
    columns: Optional[int] = None
    stopword_dim: bool = False
    detail: Optional[str] = None


@dataclass
class Archive:
    frames: List[Frame] = field(default_factory=list)
    total_chars: int = 0
    truncated_chars: int = 0
    text: Optional[str] = None
    text_head: Optional[str] = None
    text_tail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "frames": [
                {
                    "data": f.data,
                    "mimeType": f.mime_type,
                    "cols": f.cols,
                    "rows": f.rows,
                    "chars": f.chars,
                    "font": f.font,
                    "variant": f.variant,
                    "lineRepeat": f.line_repeat,
                    **({"columns": f.columns} if f.columns else {}),
                    **({"stopwordDim": True} if f.stopword_dim else {}),
                    **({"detail": f.detail} if f.detail else {}),
                }
                for f in self.frames
            ],
            "totalChars": self.total_chars,
            "truncatedChars": self.truncated_chars,
        }
        if self.text:
            d["text"] = self.text
        if self.text_head:
            d["textHead"] = self.text_head
        if self.text_tail:
            d["textTail"] = self.text_tail
        return d

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Archive:
        raw_frames = raw.get("frames") or ()
        frames = [
            Frame(
                data=str(f.get("data", "")),
                mime_type=str(f.get("mimeType", "image/png")),
                cols=int(f.get("cols", 0)),
                rows=int(f.get("rows", 0)),
                chars=int(f.get("chars", 0)),
                font=str(f.get("font", "5x8")),
                variant=str(f.get("variant", "bw")),
                line_repeat=int(f.get("lineRepeat", 1)),
                columns=f.get("columns"),
                stopword_dim=bool(f.get("stopwordDim")),
                detail=f.get("detail"),
            )
            for f in raw_frames
            if isinstance(f, Mapping)
        ]
        return cls(
            frames=frames,
            total_chars=int(raw.get("totalChars") or 0),
            truncated_chars=int(raw.get("truncatedChars") or 0),
            text=raw.get("text"),
            text_head=raw.get("textHead"),
            text_tail=raw.get("textTail"),
        )


def history_blocks(archive: Archive) -> List[Dict[str, Any]]:
    """Ordered blocks for a compaction summary message: head, images, tail."""
    blocks: List[Dict[str, Any]] = []
    has_images = bool(archive.frames)

    if archive.text_head:
        suffix = "\n-------------- imaged middle below\n" if has_images else ""
        blocks.append({"type": "text", "text": archive.text_head + suffix})

    for f in archive.frames:
        blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{f.data}"},
        })

    if archive.text_tail:
        prefix = (
            "-------------- imaged middle above\n"
            if has_images
            else ("\n-------------- middle history omitted above\n" if archive.truncated_chars > 0 else "")
        )
        tail_text = prefix + archive.text_tail
        blocks.append({"type": "text", "text": tail_text})

    return blocks


def render_summary_prompt(
    *,
    frame_count: int,
    cols: Any,
    rows: int,
    doc_columns: bool,
    sentence_ink: bool,
    stopword_dimmed: bool,
    line_repeated: bool,
    truncated_chars: int,
    included_previous_summary: bool,
    read_files: Sequence[str],
    modified_files: Sequence[str],
    include_thinking: bool,
) -> str:
    """Format preamble matching OMP snapcompact-summary.md."""
    lines: List[str] = [
        "Resume prior conversation. Earlier turns archived under HISTORY below, oldest→newest. Read HISTORY fully; continue the live conversation following it.",
        "",
        "Archived transcript scopes:",
    ]
    if include_thinking:
        lines.append("- `¶user:`, `¶think:`, `¶ai:`, `¶call:`: user, assistant reasoning, assistant reply, tool call.")
    else:
        lines.append("- `¶user:`, `¶ai:`, `¶call:`: user, assistant reply, tool call.")
    lines.extend([
        "- Unprefixed following lines: current scope. Consecutive same-kind blocks omit repeated prefix.",
        "- Tool call: `¶call:name(args)//intent`; trailing `//intent` optional. `<out>…</out>`: tool output.",
        "",
        "Reading HISTORY:",
        "- Plain text: verbatim transcript; rely on it exactly.",
    ])

    if frame_count > 0:
        lines.append("- Some middle sections: images, not text. Each image: one page of that transcript, in reading order between marked delimiters. Solid black cell: newline; runs of spaces collapse to one.")
        if doc_columns:
            lines.append(f"  - Frame: two side-by-side columns, each {cols} characters wide, up to {rows} rows tall; read left top→bottom, then right.")
        else:
            lines.append(f"  - Frame: one grid {cols} characters wide, up to {rows} rows tall; read left→right, top→bottom. No word wrap; words may break across rows.")
        if sentence_ink:
            lines.append("  - Ink: six colors, one per sentence.")
        if stopword_dimmed:
            lines.append("  - Function words: dim gray; content words: full ink.")
        if line_repeated:
            lines.append("  - Each line printed twice (white, then pale-yellow band); copies identical.")

    if included_previous_summary:
        lines.append("- HISTORY opens with a condensed digest of still-older context predating archived turns.")
    if truncated_chars > 0:
        lines.append(f"- About {truncated_chars} characters of older middle history dropped to fit archive budget.")
    lines.append("- If an exact earlier detail matters and a section is unclear, re-derive from workspace (re-read files, re-run commands), rather than guess.")
    lines.append("")

    if read_files or modified_files:
        lines.append("FILES")
        lines.append("===================")
        # Group files
        by_dir: Dict[str, List[Tuple[str, str]]] = {}
        for f in read_files:
            p = f.rsplit("/", 1)
            d, name = (p[0] + "/", p[1]) if len(p) == 2 else ("", p[0])
            by_dir.setdefault(d, []).append((name, "Read"))
        for f in modified_files:
            p = f.rsplit("/", 1)
            d, name = (p[0] + "/", p[1]) if len(p) == 2 else ("", p[0])
            by_dir.setdefault(d, []).append((name, "Write"))

        for d in sorted(by_dir.keys()):
            if d:
                lines.append(f"# {d}")
            for name, action in sorted(by_dir[d]):
                lines.append(f"{name} ({action})")
        lines.append("")

    lines.append("HISTORY")
    lines.append("===================")
    return "\n".join(lines)


def compact_archive(
    messages: Sequence[Mapping[str, Any]],
    *,
    shape: Shape,
    max_frames: int = MAX_FRAMES_DEFAULT,
    previous_summary: Optional[str] = None,
    previous_archive: Optional[Archive] = None,
    file_ops: Any = None,
    include_thinking: bool = True,
    dim_tool_results: bool = True,
    target_api: Optional[str] = None,
) -> Tuple[str, str, Archive]:
    """Run snapcompact archive creation. Returns (summary, short_summary, archive)."""
    serialized = serialize_conversation(
        messages,
        include_thinking=include_thinking,
        dim_tool_results=dim_tool_results,
    )

    previous_text_raw = (
        previous_archive.text
        if previous_archive and previous_archive.text
        else (
            NEWLINE_GLYPH.join(
                p for p in (previous_archive.text_head, previous_archive.text_tail) if p
            )
            if previous_archive
            else ""
        )
    )
    if not include_thinking and previous_text_raw:
        previous_text = strip_thinking_sections(previous_text_raw)
    else:
        previous_text = previous_text_raw

    has_previous_text = bool(previous_text)
    included_previous_summary = not has_previous_text and bool(previous_summary)

    high = shape
    low = _dense_companion(high, target_api)
    geo = geometry(high)
    max_frames = max(1, min(max_frames, MAX_FRAMES_DEFAULT))

    archive_text = normalize(serialized, high)
    if included_previous_summary and previous_summary:
        head = f"[Summary of earlier history] {normalize(previous_summary, high)}"
        archive_text = f"{head} [Recent conversation] {archive_text}" if archive_text else head

    truncated_chars = previous_archive.truncated_chars if previous_archive else 0

    if has_previous_text:
        archive_text = f"{previous_text}{NEWLINE_GLYPH}{archive_text}" if archive_text else previous_text

    layout = plan_archive(archive_text, high, low, max_frames)
    truncated_chars += layout.truncated_chars

    # Render frames
    rendered_frames: List[Frame] = []
    dim_open = layout.text_head.rfind("\u000E") > layout.text_head.rfind("\u000F")

    for planned in layout.frames:
        page_text = f"\u000E{planned.text}" if dim_open else planned.text
        dim_open = page_text.rfind("\u000E") > page_text.rfind("\u000F")
        if planned.shape.stopword_dim:
            page_text = dim_stopwords(page_text)
        data = render_snapcompact_png(
            page_text,
            size=planned.shape.frame_size,
            font=planned.shape.font,
            cell_width=planned.shape.cell_width,
            cell_height=planned.shape.cell_height,
            variant=planned.shape.variant,
            line_repeat=planned.shape.line_repeat,
            stretch=planned.shape.stretch,
            columns=planned.shape.columns or 1,
        )
        f_geo = geometry(planned.shape)
        rendered_frames.append(
            Frame(
                data=data,
                mime_type="image/png",
                cols=f_geo.cols,
                rows=f_geo.rows,
                chars=len(page_text),
                font=planned.shape.font,
                variant=planned.shape.variant,
                line_repeat=planned.shape.line_repeat,
                columns=planned.shape.columns,
                stopword_dim=planned.shape.stopword_dim,
                detail=planned.shape.image_detail,
            )
        )

    text_head = layout.text_head
    text_tail = f"\u000E{layout.text_tail}" if (dim_open and layout.text_tail) else layout.text_tail
    text_chars = len(text_head) + len(text_tail)
    total_chars = sum(f.chars for f in rendered_frames) + text_chars

    frame_cols = []
    for f in rendered_frames:
        if f.cols not in frame_cols:
            frame_cols.append(f.cols)
    summary_cols = " or ".join(str(c) for c in frame_cols) if frame_cols else str(geo.cols)

    read_files, modified_files = compute_file_lists(file_ops) if file_ops else ([], [])

    if not rendered_frames and not text_head and not text_tail and not read_files and not modified_files:
        summary = "No prior history."
    else:
        summary = render_summary_prompt(
            frame_count=len(rendered_frames),
            cols=summary_cols,
            rows=geo.rows,
            doc_columns=(high.columns == 2),
            sentence_ink=(high.variant == "sent"),
            stopword_dimmed=high.stopword_dim,
            line_repeated=(high.line_repeat > 1),
            truncated_chars=truncated_chars,
            included_previous_summary=included_previous_summary,
            read_files=read_files,
            modified_files=modified_files,
            include_thinking=include_thinking,
        )

    persisted_text = layout.kept_text
    if layout.kept_text and text_tail:
        persisted_text = f"{layout.kept_text[: len(layout.kept_text) - len(layout.text_tail)]}{text_tail}"

    archive = Archive(
        frames=rendered_frames,
        total_chars=total_chars,
        truncated_chars=truncated_chars,
        text=persisted_text if persisted_text else None,
        text_head=text_head if text_head else None,
        text_tail=text_tail if text_tail else None,
    )

    text_note = f" (+{text_chars:,} chars as text)" if text_chars > 0 else ""
    frame_s = "" if len(rendered_frames) == 1 else "s"
    short_summary = f"Archived {total_chars:,} chars of history onto {len(rendered_frames)} snapcompact frame{frame_s}{text_note}"

    return summary, short_summary, archive
