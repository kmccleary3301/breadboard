"""Tests for apply_inline_snapcompact (tool results and system prompt imaging)."""

import base64
import copy
import io
import pytest
from PIL import Image

from breadboard_engine.compaction.settings import CompactionSettings, SnapcompactSettings
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
