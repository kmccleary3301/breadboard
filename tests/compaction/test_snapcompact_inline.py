"""Tests for apply_inline_snapcompact (tool results and system prompt imaging)."""

import base64
import copy
import io
import pytest
from PIL import Image

from breadboard_engine.compaction.settings import CompactionSettings, SnapcompactSettings
from breadboard_engine.compaction.state import NATIVE_MARKER_KEY
from breadboard_engine.compaction.snapcompact.inline import (
    CONTEXT_FRAMES_NOTE,
    CONTEXT_STUB,
    SYSTEM_FRAMES_NOTE,
    SYSTEM_STUB,
    TOOL_RESULT_NOTE,
    apply_inline_snapcompact,
)


def _make_settings(
    *,
    system_prompt: str = "none",
    tool_results: str = "none",
    inline_min_tokens: int = 100,
) -> CompactionSettings:
    return CompactionSettings(
        enabled=True,
        snapcompact=SnapcompactSettings(
            system_prompt=system_prompt,
            tool_results=tool_results,
            inline_min_tokens=inline_min_tokens,
        ),
    )


def test_inline_noop_when_not_supports_images():
    """Non-vision target returns messages unchanged."""
    settings = _make_settings(system_prompt="all", tool_results="all")
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello!"},
    ]
    transformed = apply_inline_snapcompact(messages, settings, supports_images=False)
    assert transformed == messages


def test_inline_noop_when_disabled():
    """When both system_prompt and tool_results are 'none', return messages unchanged."""
    settings = _make_settings(system_prompt="none", tool_results="none")
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello!"},
    ]
    transformed = apply_inline_snapcompact(messages, settings, supports_images=True)
    assert transformed == messages


def test_inline_images_large_historical_tool_result_keeping_recent():
    """Large historical tool result is imaged; small and freshest results stay crisp text."""
    settings = _make_settings(tool_results="all", inline_min_tokens=50)

    large_content = "Detailed log entry with lots of tokens. " * 300
    small_content = "Short OK"

    messages = [
        {"role": "system", "content": "Operating instructions."},
        {"role": "user", "content": "Run tests and inspect files."},
        {"role": "tool", "tool_call_id": "c1", "content": large_content},
        {"role": "tool", "tool_call_id": "c2", "content": small_content},
        {"role": "tool", "tool_call_id": "c3", "content": large_content},  # Freshest / last tool result
    ]

    transformed = apply_inline_snapcompact(messages, settings, supports_images=True)
    assert len(transformed) == 5

    # 1. Historical large tool result (c1) is imaged
    c1_content = transformed[2]["content"]
    assert isinstance(c1_content, list)
    assert c1_content[0]["type"] == "text"
    assert c1_content[0]["text"] == TOOL_RESULT_NOTE
    image_blocks = [b for b in c1_content if b.get("type") == "image_url"]
    assert len(image_blocks) > 0
    # Decodable
    raw = base64.b64decode(image_blocks[0]["image_url"]["url"].split("base64,")[1])
    img = Image.open(io.BytesIO(raw))
    assert img.format == "PNG"

    # 2. Small tool result (c2) stays text
    assert transformed[3]["content"] == small_content

    # 3. Last tool result (c3) stays crisp text
    assert transformed[4]["content"] == large_content


def test_inline_preserves_embedded_tool_source_images():
    """Mixed tool result with text and source images preserves original images after frames."""
    settings = _make_settings(tool_results="all", inline_min_tokens=50)

    large_text = "Analysis report with embedded graph. " * 200
    source_img_part = {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGNiAAAABgADNjd8qAAAAABJRU5ErkJggg=="},
    }

    messages = [
        {"role": "user", "content": "Analyze graph."},
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": [
                {"type": "text", "text": large_text},
                source_img_part,
            ],
        },
        {"role": "user", "content": "Next step?"},
        {"role": "tool", "tool_call_id": "c2", "content": "Done."},  # Last tool result
    ]

    transformed = apply_inline_snapcompact(messages, settings, supports_images=True)
    c1_content = transformed[1]["content"]
    assert c1_content[0]["type"] == "text"
    assert c1_content[0]["text"] == TOOL_RESULT_NOTE
    # Source image preserved at end
    assert any(b.get("type") == "image_url" and b.get("image_url") == source_img_part["image_url"] for b in c1_content)


def test_inline_system_prompt_all():
    """system_prompt='all' replaces system message with stub and attaches frames to first user message."""
    settings = _make_settings(system_prompt="all", inline_min_tokens=50)

    large_instructions = "You are an autonomous engineering agent with extensive guidelines. " * 250
    messages = [
        {"role": "system", "content": large_instructions},
        {"role": "user", "content": "Please implement feature X."},
    ]

    transformed = apply_inline_snapcompact(messages, settings, supports_images=True)
    assert len(transformed) == 2

    # System message replaced with stub
    assert transformed[0]["content"] == SYSTEM_STUB

    # User message receives note + frames + original content
    user_content = transformed[1]["content"]
    assert isinstance(user_content, list)
    assert user_content[0]["type"] == "text"
    assert user_content[0]["text"] == SYSTEM_FRAMES_NOTE
    images = [b for b in user_content if b.get("type") == "image_url"]
    assert len(images) > 0
    assert user_content[-1]["text"] == "Please implement feature X."


def test_inline_system_prompt_frames_skip_a_native_boundary_marker():
    """The marker is replayed as native items, so the frames go to the next real user message."""
    settings = _make_settings(system_prompt="all", inline_min_tokens=50)
    marker = {"role": "user", "content": "", NATIVE_MARKER_KEY: {"record_id": "r", "items": []}}
    messages = [
        {"role": "system", "content": "You are an autonomous engineering agent with extensive guidelines. " * 250},
        marker,
        {"role": "user", "content": "Please implement feature X."},
    ]

    transformed = apply_inline_snapcompact(messages, settings, supports_images=True)
    assert transformed[0]["content"] == SYSTEM_STUB
    assert transformed[1] == marker
    assert transformed[2]["content"][0]["text"] == SYSTEM_FRAMES_NOTE



def test_inline_system_prompt_agents_md():
    """system_prompt='agents-md' extracts context sections and attaches them to user message."""
    settings = _make_settings(system_prompt="agents-md", inline_min_tokens=50)

    repo_rules = "<repo-rules>\n" + "Always follow formatting rules and test before commit. " * 200 + "\n</repo-rules>"
    sys_content = f"Base operating instructions.\n\n{repo_rules}\n\nFinal remarks."

    messages = [
        {"role": "system", "content": sys_content},
        {"role": "user", "content": "Help me."},
    ]

    transformed = apply_inline_snapcompact(messages, settings, supports_images=True)
    assert "<repo-rules>" not in transformed[0]["content"]
    assert CONTEXT_STUB in transformed[0]["content"]

    user_content = transformed[1]["content"]
    assert user_content[0]["text"] == CONTEXT_FRAMES_NOTE
    images = [b for b in user_content if b.get("type") == "image_url"]
    assert len(images) > 0


def _default_shape():
    from breadboard_engine.compaction.snapcompact.shapes import resolve_shape

    return resolve_shape(variant="auto", api="google-generative-ai", model_id=None)


def test_inline_agents_md_keeps_text_when_rendering_saves_nothing():
    """A context section too small to pass the savings gate stays as text, with no stub."""
    settings = _make_settings(system_prompt="agents-md", inline_min_tokens=50)
    sys_content = "Base instructions.\n\n<repo-rules>\nUse tabs.\n</repo-rules>\n\nEnd."
    messages = [
        {"role": "system", "content": sys_content},
        {"role": "user", "content": "Help me."},
    ]

    transformed = apply_inline_snapcompact(messages, settings, supports_images=True)

    assert transformed == messages


def test_inline_agents_md_keeps_text_when_section_exceeds_frame_cap():
    """A section needing more than MAX_SYSTEM_PROMPT_FRAMES frames is never partially imaged."""
    from breadboard_engine.compaction.snapcompact.archive import count_frames
    from breadboard_engine.compaction.snapcompact.inline import MAX_SYSTEM_PROMPT_FRAMES

    settings = _make_settings(system_prompt="agents-md", inline_min_tokens=50)
    section = "<repo-rules>\n" + "Rule: run the full test suite before every commit. " * 4000 + "\n</repo-rules>"
    assert count_frames(section, _default_shape()) > MAX_SYSTEM_PROMPT_FRAMES
    messages = [
        {"role": "system", "content": f"Base instructions.\n\n{section}"},
        {"role": "user", "content": "Help me."},
    ]

    transformed = apply_inline_snapcompact(messages, settings, supports_images=True)

    assert transformed == messages


def test_inline_system_prompt_all_keeps_text_when_prompt_exceeds_frame_cap():
    """system_prompt='all' does not stub a prompt it can only partly render."""
    from breadboard_engine.compaction.snapcompact.archive import count_frames
    from breadboard_engine.compaction.snapcompact.inline import MAX_SYSTEM_PROMPT_FRAMES

    settings = _make_settings(system_prompt="all", inline_min_tokens=50)
    prompt = "Operating rule: verify every change with a focused test. " * 4000
    assert count_frames(prompt, _default_shape()) > MAX_SYSTEM_PROMPT_FRAMES
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "Help me."},
    ]

    transformed = apply_inline_snapcompact(messages, settings, supports_images=True)

    assert transformed == messages


def test_inline_system_prompt_frames_carry_text_after_unrenderable_glyphs(monkeypatch):
    from breadboard_engine.compaction.snapcompact import inline

    pages = []
    monkeypatch.setattr(inline, "render_snapcompact_png", lambda page, **_: pages.append(page) or "")
    settings = _make_settings(system_prompt="all", inline_min_tokens=50)
    prompt = "\U0001fae0" * 50000 + " Critical rule: never delete the workspace."
    apply_inline_snapcompact(
        [{"role": "system", "content": prompt}, {"role": "user", "content": "Help me."}],
        settings, supports_images=True,
    )
    assert any("Critical rule" in page for page in pages)


def test_inline_does_not_mutate_input():
    """Input message dictionaries and lists MUST NOT be modified."""
    settings = _make_settings(tool_results="all", inline_min_tokens=50)
    orig_text = "Important text. " * 200
    messages = [
        {"role": "user", "content": "Do it"},
        {"role": "tool", "tool_call_id": "c1", "content": orig_text},
        {"role": "tool", "tool_call_id": "c2", "content": "Recent"},
    ]
    snapshot = copy.deepcopy(messages)

    _ = apply_inline_snapcompact(messages, settings, supports_images=True)

    assert messages == snapshot
    assert messages[1]["content"] == orig_text


def test_inline_canonical_media_at_budget_allocates_no_frames_and_preserves_images():
    """When incoming canonical media fills the provider budget, inline snapcompact adds no frames."""
    from breadboard_engine.compaction.snapcompact.shapes import provider_image_budget
    from breadboard_engine.provider.history_projection import portable_provider_messages

    budget_limit = provider_image_budget(None)  # 20
    canonical_att = {
        "type": "media",
        "kind": "image",
        "uri": "attachment://sha256:1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff",
        "mime": "image/png",
    }
    codex_img = {
        "type": "media",
        "kind": "codex_image_url",
        "uri": "https://example.com/canonical_photo.png",
        "detail": "high",
    }
    legacy_ctrl = {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGNiAAAABgADNjd8qAAAAABJRU5ErkJggg=="},
    }

    # 20 total incoming images: alternating canonical attachment, codex_image_url, and legacy
    incoming_images = []
    for i in range(8):
        incoming_images.append(dict(canonical_att))
        incoming_images.append(dict(codex_img, uri=f"{codex_img['uri']}_{i}"))
    incoming_images.append(legacy_ctrl)
    incoming_images.append(dict(canonical_att))
    incoming_images.append(dict(codex_img, uri=f"{codex_img['uri']}_final"))
    incoming_images.append(dict(legacy_ctrl))
    assert len(incoming_images) == budget_limit  # 20

    settings = _make_settings(system_prompt="all", inline_min_tokens=50)
    system_prompt = "Operating instructions for autonomous agents. " * 300
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": [{"type": "text", "text": "Process items:"}, *incoming_images]},
    ]

    transformed = apply_inline_snapcompact(messages, settings, supports_images=True)

    # Budget is fully saturated by incoming canonical images; no system frames should be added
    assert transformed[0]["content"] == system_prompt
    assert len(transformed) == 2
    user_content = transformed[1]["content"]
    new_frames = [b for b in user_content if b.get("type") == "image_url" and b != legacy_ctrl]
    assert len(new_frames) == 0

    # Preserves incoming images and order
    user_media_and_images = [b for b in user_content if b.get("type") in {"media", "image_url"}]
    assert user_media_and_images == incoming_images

    # Downstream portable conversion preserves count and budget
    portable = portable_provider_messages(
        transformed,
        source_provider="openai",
        source_api="chat",
        target_provider="openai",
        target_api="chat",
    )
    portable_user = next(m for m in portable if m.get("role") == "user")
    converted_images = [b for b in portable_user["content"] if b.get("type") == "media"]
    assert len(converted_images) == budget_limit


def test_inline_canonical_media_residual_slots_cap():
    """When incoming canonical media leaves fewer slots than needed frames, no frames are added."""
    canonical_att = {
        "type": "media",
        "kind": "image",
        "uri": "attachment://sha256:aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899",
        "mime": "image/png",
    }
    # 19 incoming images leaves 1 residual slot out of 20
    incoming_19 = [dict(canonical_att) for _ in range(19)]
    settings = _make_settings(system_prompt="all", inline_min_tokens=50)
    system_prompt = "Operating instructions with detailed guidance for agents. " * 600
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": [{"type": "text", "text": "Instructions follow:"}, *incoming_19]},
    ]

    transformed = apply_inline_snapcompact(messages, settings, supports_images=True)

    # System prompt requiring multiple frames cannot fit into 1 slot -> no frames added
    assert transformed[0]["content"] == system_prompt
    user_content = transformed[1]["content"]
    new_frames = [b for b in user_content if b.get("type") == "image_url"]
    assert len(new_frames) == 0
    user_media = [b for b in user_content if b.get("type") == "media"]
    assert user_media == incoming_19


def test_inline_already_over_budget_canonical_media_does_not_add_frames_or_drop():
    """When already over budget, inline snapcompact adds no frames and preserves all existing images."""
    codex_img = {
        "type": "media",
        "kind": "codex_image_url",
        "uri": "https://example.com/img.png",
        "detail": "high",
    }
    # 21 incoming images (over 20 budget)
    incoming_21 = [dict(codex_img, uri=f"{codex_img['uri']}_{i}") for i in range(21)]
    settings = _make_settings(system_prompt="all", inline_min_tokens=50)
    system_prompt = "Operating instructions for autonomous agents. " * 300
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": [{"type": "text", "text": "Run analysis:"}, *incoming_21]},
    ]

    transformed = apply_inline_snapcompact(messages, settings, supports_images=True)

    # No frames added
    assert transformed[0]["content"] == system_prompt
    user_content = transformed[1]["content"]
    new_frames = [b for b in user_content if b.get("type") == "image_url"]
    assert len(new_frames) == 0

    # Preexisting overage is NOT dropped or mutated
    user_media = [b for b in user_content if b.get("type") == "media"]
    assert len(user_media) == 21
    assert user_media == incoming_21
