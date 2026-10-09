from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional
import pytest

from breadboard_engine.compaction.methods import (
    ArtifactSink,
    CompactionContext,
    MethodUnavailable,
)
from breadboard_engine.compaction.settings import settings_from_config
from breadboard_engine.compaction.shake import (
    AGGRESSIVE_SHAKE_CONFIG,
    DEFAULT_SHAKE_CONFIG,
    RESCUE_SHAKE_CONFIG,
    BlockShakeRegion,
    ShakeCompaction,
    ShakeConfig,
    ToolResultShakeRegion,
    apply_shake_region,
    apply_shake_regions,
    collect_shake_regions,
    scan_text_for_block_ranges,
    shake,
)
from breadboard_engine.compaction.state import CompactionState, ProjectionTarget
from breadboard_engine.compaction.tokens import estimate_message_tokens
from breadboard_engine.compaction.transcript import check_tool_pairing

TARGET = ProjectionTarget("openai", "responses", "gpt-x")


class MemoryArtifactSink(ArtifactSink):
    def __init__(self) -> None:
        self.stored: Dict[str, Tuple[str, str]] = {}
        self._counter = 0

    def store(self, name: str, content: str, media_type: str = "text/plain") -> str:
        self._counter += 1
        ref = f"art_ref_{self._counter}"
        self.stored[ref] = (name, content)
        return ref


def _context(
    messages: List[Dict[str, Any]],
    state: Optional[CompactionState] = None,
    artifacts: Optional[ArtifactSink] = None,
    *,
    reason: str = "threshold",
    window: int = 200000,
    **config: Any,
) -> CompactionContext:
    settings = settings_from_config({"enabled": True, **config})
    st = state or CompactionState()
    return CompactionContext(
        messages=messages,
        state=st,
        settings=settings,
        reason=reason,  # type: ignore[arg-type]
        target=TARGET,
        context_window=window,
        tokens_before=sum(estimate_message_tokens(m) for m in st.project(messages, TARGET)),
        artifacts=artifacts,
    )


def _fenced_block(approx_tokens: int, lang: str = "ts") -> str:
    line = "const x = 42;\n"
    repeat_count = max(1, approx_tokens // 4)
    return f"```{lang}\n{line * repeat_count}```"


def _xml_block(approx_tokens: int, tag: str = "example") -> str:
    line = "some xml content inside tags\n"
    repeat_count = max(1, approx_tokens // 6)
    return f"<{tag}>\n{line * repeat_count}</{tag}>"


# -----------------------------------------------------------------------------
# Unit tests ported from OMP packages/agent/test/shake.test.ts
# -----------------------------------------------------------------------------


def test_collect_shake_regions_tool_results_and_apply_sets_pruned_at():
    tr = {
        "role": "tool",
        "tool_call_id": "call_1",
        "name": "bash",
        "content": "x" * 1600,
    }
    messages = [
        {"role": "user", "content": "run bash"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "bash"}}]},
        tr,
    ]
    cfg = ShakeConfig(protect_tokens=0, min_savings=0)
    regions = collect_shake_regions(messages, cfg)
    assert len(regions) == 1
    reg = regions[0]
    assert reg.kind == "toolResult"
    assert reg.tokens > 0

    apply_shake_region(tr, reg, "[shaken]")
    assert tr.get("pruned_at") is not None
    assert tr["content"] == "[shaken]"


def test_keeps_images_in_provider_view_of_elided_mixed_tool_result():
    image = {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}}
    tr = {
        "role": "tool",
        "tool_call_id": "c1",
        "name": "bash",
        "content": [{"type": "text", "text": "heavy text " * 500}, image],
    }
    messages = [
        {"role": "user", "content": "show"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "bash"}}]},
        tr,
    ]
    cfg = ShakeConfig(protect_tokens=0, min_savings=0)
    regions = collect_shake_regions(messages, cfg)
    assert len(regions) == 1

    apply_shake_region(tr, regions[0], "[shaken]")
    assert isinstance(tr["content"], list)
    assert tr["content"][0] == {"type": "text", "text": "[shaken]"}
    assert tr["content"][1] == image


def test_never_collects_protected_tools():
    tr = {
        "role": "tool",
        "tool_call_id": "call_s",
        "name": "skill",
        "content": "skill data " * 500,
    }
    messages = [
        {"role": "user", "content": "load skill"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_s", "type": "function", "function": {"name": "skill"}}]},
        tr,
    ]
    regions = collect_shake_regions(messages, ShakeConfig(protect_tokens=0, min_savings=0, protected_tools=["skill"]))
    assert len(regions) == 0


def test_never_collects_skill_read_or_artifact_recovery_reads():
    skill_tr = {
        "role": "tool",
        "tool_call_id": "c_skill",
        "name": "read",
        "content": "skill content " * 500,
    }
    art_tr = {
        "role": "tool",
        "tool_call_id": "c_art",
        "name": "read",
        "content": "artifact content " * 500,
    }
    messages = [
        {"role": "user", "content": "read"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c_skill", "type": "function", "function": {"name": "read", "arguments": '{"path": "skill://foo"}'}},
                {"id": "c_art", "type": "function", "function": {"name": "read", "arguments": '{"path": "artifact://foo"}'}},
            ],
        },
        skill_tr,
        art_tr,
    ]
    regions = collect_shake_regions(messages, ShakeConfig(protect_tokens=0, min_savings=0))
    assert len(regions) == 0


def test_never_collects_already_pruned_tool_results():
    tr = {
        "role": "tool",
        "tool_call_id": "call_1",
        "name": "bash",
        "content": "output " * 500,
        "pruned_at": "yesterday",
    }
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "bash"}}]},
        tr,
    ]
    assert collect_shake_regions(messages, ShakeConfig(protect_tokens=0, min_savings=0)) == []


def test_honors_protect_recent_token_window():
    text = "word " * 160
    m_older = {
        "role": "tool",
        "tool_call_id": "c1",
        "name": "bash",
        "content": text,
    }
    m_middle = {
        "role": "tool",
        "tool_call_id": "c2",
        "name": "bash",
        "content": text,
    }
    m_recent = {
        "role": "tool",
        "tool_call_id": "c3",
        "name": "bash",
        "content": text,
    }
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "bash"}},
                {"id": "c2", "type": "function", "function": {"name": "bash"}},
                {"id": "c3", "type": "function", "function": {"name": "bash"}},
            ],
        },
        m_older,
        m_middle,
        m_recent,
    ]
    per_entry = estimate_message_tokens(m_older)
    cfg = ShakeConfig(protect_tokens=int(per_entry * 1.5), min_savings=0)
    regions = collect_shake_regions(messages, cfg)
    assert len(regions) == 1
    assert regions[0].index == 1  # m_older


def test_min_savings_gates_the_whole_batch():
    tr = {"role": "tool", "tool_call_id": "c1", "name": "bash", "content": "q" * 1600}
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "bash"}}]},
        tr,
    ]
    tokens = estimate_message_tokens(tr)
    assert len(collect_shake_regions(messages, ShakeConfig(protect_tokens=0, min_savings=tokens * 10))) == 0
    assert len(collect_shake_regions(messages, ShakeConfig(protect_tokens=0, min_savings=0))) == 1


def test_detects_large_fenced_block_and_applies():
    fence = _fenced_block(200)
    text = f"intro line\n{fence}\noutro line"
    messages = [{"role": "assistant", "content": text}]
    regions = collect_shake_regions(messages, ShakeConfig(protect_tokens=0, min_savings=0, fence_min_tokens=50))
    assert len(regions) == 1
    reg = regions[0]
    assert reg.kind == "block"
    assert text[reg.start : reg.end] == fence

    mod = apply_shake_regions(messages, [(reg, "[shaken]")])
    assert mod[0]["content"] == "intro line\n[shaken]\noutro line"


def test_ignores_fenced_blocks_below_fence_min_tokens():
    text = "intro\n```ts\nconst a = 1;\n```\noutro"
    messages = [{"role": "assistant", "content": text}]
    assert len(collect_shake_regions(messages, ShakeConfig(protect_tokens=0, min_savings=0, fence_min_tokens=400))) == 0


def test_detects_top_level_xml_block():
    xml = _xml_block(150)
    text = f"before\n{xml}\nafter"
    messages = [{"role": "assistant", "content": text}]
    regions = collect_shake_regions(messages, ShakeConfig(protect_tokens=0, min_savings=0, fence_min_tokens=50))
    assert len(regions) == 1
    reg = regions[0]
    assert reg.kind == "block"
    assert text[reg.start : reg.end] == xml


def test_never_targets_tool_call_blocks():
    fence = _fenced_block(150)
    messages = [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "tiny"}, {"type": "text", "text": f"pre\n{fence}\npost"}],
            "tool_calls": [{"id": "tc1", "type": "function", "function": {"name": "read", "arguments": '{"path":"x"}'}}],
        }
    ]
    regions = collect_shake_regions(messages, ShakeConfig(protect_tokens=0, min_savings=0, fence_min_tokens=50))
    assert len(regions) == 1
    assert regions[0].block_index == 1


def test_splices_two_blocks_highest_start_first():
    first = _fenced_block(80)
    second = _fenced_block(80, "py")
    text = f"head\n{first}\nmiddle\n{second}\ntail"
    messages = [{"role": "assistant", "content": text}]
    regions = collect_shake_regions(messages, ShakeConfig(protect_tokens=0, min_savings=0, fence_min_tokens=50))
    assert len(regions) == 2

    mod = apply_shake_regions(messages, [(regions[0], "[A]"), (regions[1], "[B]")])
    assert mod[0]["content"] == "head\n[A]\nmiddle\n[B]\ntail"


def test_useless_tool_result_bypasses_protect_window():
    text = "No matches found in any scanned file.\n" * 50
    flagged = {
        "role": "tool",
        "tool_call_id": "c1",
        "name": "search",
        "content": text,
        "useless": True,
    }
    plain = {
        "role": "tool",
        "tool_call_id": "c2",
        "name": "search",
        "content": text,
    }
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "search"}},
                {"id": "c2", "type": "function", "function": {"name": "search"}},
            ],
        },
        flagged,
        plain,
    ]
    regions = collect_shake_regions(messages, ShakeConfig(protect_tokens=1_000_000, min_savings=0))
    assert len(regions) == 1
    assert regions[0].index == 1  # flagged


def test_error_result_never_bypasses_window_even_when_flagged():
    err = {
        "role": "tool",
        "tool_call_id": "c1",
        "name": "search",
        "content": "boom\n" * 50,
        "useless": True,
        "is_error": True,
    }
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "search"}}]},
        err,
    ]
    assert len(collect_shake_regions(messages, ShakeConfig(protect_tokens=1_000_000, min_savings=0))) == 0


def test_presets_aggressive_and_rescue():
    older = {"role": "tool", "tool_call_id": "c1", "name": "bash", "content": "old-result " * 300}
    recent = {"role": "tool", "tool_call_id": "c2", "name": "bash", "content": "recent-result " * 3000}
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "bash"}},
                {"id": "c2", "type": "function", "function": {"name": "bash"}},
            ],
        },
        older,
        recent,
    ]
    reg_agg = collect_shake_regions(messages, AGGRESSIVE_SHAKE_CONFIG)
    assert len(reg_agg) == 1
    assert reg_agg[0].index == 1  # older is shaken, recent kept

    reg_rescue = collect_shake_regions(messages, RESCUE_SHAKE_CONFIG)
    assert len(reg_rescue) == 2  # both shaken under rescue


# -----------------------------------------------------------------------------
# Compaction execution and contract tests
# -----------------------------------------------------------------------------


class FailingArtifactSink(ArtifactSink):
    def store(self, name: str, content: str, media_type: str = "text/plain") -> str:
        raise OSError("disk full")


@pytest.mark.parametrize("artifacts", [None, FailingArtifactSink()])
def test_shake_without_artifact_store_elides_with_unrecoverable_placeholder(artifacts):
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "run task"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "bash"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "bash", "content": "massive output line\n" * 2000},
    ]
    ctx = _context(messages, artifacts=artifacts, shake={"protect_tokens": 0, "min_savings": 100})
    record = ShakeCompaction().run(ctx)

    edited = record.edits[0].message["content"]
    assert "massive output line" not in edited
    assert "shaken" in edited and "recover:" not in edited


def test_shake_executes_stores_artifact_and_validates():
    sink = MemoryArtifactSink()
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "run task"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "bash"}}],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "name": "bash",
            "content": "massive output line\n" * 2000,
        },
    ]
    ctx = _context(messages, artifacts=sink, shake={"protect_tokens": 0, "min_savings": 100})
    record = ShakeCompaction().run(ctx)
    assert record.method == "shake"
    assert not record.is_boundary
    assert len(record.edits) == 1
    assert record.edits[0].index == 3

    # Check that artifact was stored
    assert len(sink.stored) == 1
    ref = record.details["artifact_ref"]
    assert ref in sink.stored

    # Check placeholder in the edit
    edited_content = record.edits[0].message["content"]
    assert "shaken" in edited_content
    assert ref in edited_content

    # State validation
    ctx.state.validate(record, messages)
    ctx.state.append(record, messages)

    # Tool pairing check on projection
    view = ctx.state.project(messages, TARGET)
    assert check_tool_pairing(view) is None
    assert "massive output line" not in view[3]["content"]


def test_shake_repeated_passes_compose_on_top_of_earlier_edits():
    sink = MemoryArtifactSink()
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "first"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "bash"}},
                {"id": "c2", "type": "function", "function": {"name": "bash"}},
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "bash", "content": "tool 1 output " * 500},
        {"role": "tool", "tool_call_id": "c2", "name": "bash", "content": "tool 2 output " * 500},
    ]
    # Pass 1: protect_tokens allows shaking c1 but keeps c2
    ctx1 = _context(messages, artifacts=sink, shake={"protect_tokens": 1000, "min_savings": 50})
    rec1 = ShakeCompaction().run(ctx1)
    assert len(rec1.edits) == 1
    assert rec1.edits[0].index == 3
    ctx1.state.append(rec1, messages)

    # Pass 2: protect_tokens lowered, c2 is shaken now
    ctx2 = _context(messages, state=ctx1.state, artifacts=sink, shake={"protect_tokens": 0, "min_savings": 50})
    rec2 = ShakeCompaction().run(ctx2)
    assert len(rec2.edits) == 1
    assert rec2.edits[0].index == 4
    ctx2.state.append(rec2, messages)

    # Both edits compose in final projection
    view = ctx2.state.project(messages, TARGET)
    assert check_tool_pairing(view) is None
    assert "tool 1 output" not in view[3]["content"]
    assert "tool 2 output" not in view[4]["content"]
    assert "shaken" in view[3]["content"]
    assert "shaken" in view[4]["content"]


def test_shake_returns_none_when_below_savings_threshold():
    sink = MemoryArtifactSink()
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "bash"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "bash", "content": "tiny"},
    ]
    ctx = _context(messages, artifacts=sink, shake={"protect_tokens": 0, "min_savings": 1000})
    assert shake(ctx) is None
