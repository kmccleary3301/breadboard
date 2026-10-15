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


def test_inline_imaged_tool_result_keeps_canonical_media_image():
    """A canonical media image inside an imaged tool result follows the frames instead of being dropped."""
    media = {
        "type": "media",
        "kind": "image",
        "uri": "attachment://sha256:1111222233334444555566667777888899990000aaaabbbbccccddddeeeeffff",
        "mime": "image/png",
    }
    messages = [
        {"role": "user", "content": "Analyze graph."},
        {"role": "tool", "tool_call_id": "c1", "content": [
            {"type": "text", "text": "Analysis report with embedded graph. " * 200},
            media,
        ]},
        {"role": "tool", "tool_call_id": "c2", "content": "Done."},
    ]

    transformed = apply_inline_snapcompact(
        messages, _make_settings(tool_results="all", inline_min_tokens=50), supports_images=True,
    )

    content = transformed[1]["content"]
    assert content[0]["text"] == TOOL_RESULT_NOTE
    assert content[-1] == media


def test_inline_counts_typed_tool_output_images_against_budget():
    """Images inside a typed Responses tool-output envelope consume the provider image budget."""
    from breadboard_engine.compaction.snapcompact.shapes import provider_image_budget
    from breadboard_engine.provider.contract_messages import responses_image_tool_result_message

    image = {
        "type": "input_image",
        "image_url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGNiAAAABgADNjd8qAAAAABJRU5ErkJggg==",
    }
    messages = [
        {"role": "user", "content": "Inspect."},
        responses_image_tool_result_message("c0", [image] * provider_image_budget(None)),
        {"role": "tool", "tool_call_id": "c1", "content": "Detailed log entry with lots of tokens. " * 300},
        {"role": "tool", "tool_call_id": "c2", "content": "Done."},
    ]

    transformed = apply_inline_snapcompact(
        messages, _make_settings(tool_results="all", inline_min_tokens=50), supports_images=True,
    )

    assert transformed == messages



def test_inline_compacts_older_anthropic_batch_continuation_and_preserves_wire_contract():
    """Older Anthropic continuation tool result is compacted; duplicate IDs, errors, side obs, and freshest stay intact."""
    from breadboard_engine.provider.adapters import AnthropicAdapter
    from breadboard_engine.provider.anthropic_replay import replay_messages
    from breadboard_engine.provider.routing import provider_router
    from breadboard_engine.provider.runtime import provider_registry
    from breadboard_engine.provider.contract_runtime import ProviderRuntimeContext

    adapter = AnthropicAdapter()
    # Duplicate call IDs: ordinal 0 is large output (compacted), ordinal 1 is error (skipped)
    call_dup = "call_batch_dup"
    large_text = "System log diagnostic output line. " * 300

    native_batch = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "User side observation beside calls"},
                {"type": "tool_result", "tool_use_id": call_dup, "content": large_text, "is_error": False, "custom_field": "val1"},
                {"type": "tool_result", "tool_use_id": call_dup, "content": "Failed execution", "is_error": True, "custom_field": "val2"},
            ],
        }
    ]

    batch_msg = adapter.create_tool_result_batch_message(native_batch)
    batch_msg["extra_metadata"] = "custom_value"
    batch_msg["content"][0]["custom_block_meta"] = "bmeta1"
    batch_msg["content"][2]["custom_replay_meta"] = "rmeta1"
    # Outer-error control: message-level status='error' skips compaction even if large
    outer_err_batch = adapter.create_tool_result_batch_message([
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_outer_err", "content": large_text, "is_error": False},
            ],
        }
    ])
    outer_err_batch["is_error"] = True

    # Mismatch control: outer tool_call_id mismatched with replay tool_use_id
    mismatch_batch = adapter.create_tool_result_batch_message([
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_real", "content": large_text, "is_error": False},
            ],
        }
    ])
    mismatch_batch["tool_call_id"] = "call_mismatched"

    freshest_call = "call_fresh_03"
    freshest_msg = {"role": "tool", "tool_call_id": freshest_call, "content": "Freshest crisp output"}

    messages = [
        {"role": "user", "content": "Run diagnostics"},
        {"role": "assistant", "content": [
            {"type": "tool_call", "id": call_dup, "name": "diag", "arguments": {}},
            {"type": "tool_call", "id": call_dup, "name": "diag", "arguments": {}},
        ]},
        batch_msg,
        outer_err_batch,
        mismatch_batch,
        {"role": "assistant", "content": [{"type": "tool_call", "id": freshest_call, "name": "fresh", "arguments": {}}]},
        freshest_msg,
    ]

    snapshot = copy.deepcopy(messages)
    settings = _make_settings(tool_results="all", inline_min_tokens=100)

    transformed = apply_inline_snapcompact(
        messages,
        settings,
        supports_images=True,
        provider="google",
        api="google-generative-ai",
    )

    # 1. Source immutability
    assert messages == snapshot

    # 2. Freshest output stays crisp text
    assert transformed[6]["content"] == "Freshest crisp output"

    # 3. Outer-error control: message with is_error=True was skipped
    assert transformed[3]["content"] == outer_err_batch["content"]

    # 4. Mismatch control: mismatched tool_call_id was rejected from admission and untouched
    assert transformed[4]["content"] == mismatch_batch["content"]

    # 5. Batch message preserved role and wrapper/block metadata
    transformed_batch = transformed[2]
    assert transformed_batch.get("role") == "tool"
    assert transformed_batch.get("extra_metadata") == "custom_value"
    assert transformed_batch["content"][0].get("custom_block_meta") == "bmeta1"
    assert transformed_batch["content"][2].get("custom_replay_meta") == "rmeta1"
    # 6. Strict replay_messages validation succeeds
    replayed = replay_messages(transformed_batch["content"])
    assert len(replayed) == 1
    replayed_user_parts = replayed[0]["content"]

    # Side observation preserved verbatim
    assert replayed_user_parts[0] == {"type": "text", "text": "User side observation beside calls"}

    # Ordinal 0 (call_dup, large) was compacted into text note + native image blocks
    call_1_part = replayed_user_parts[1]
    assert call_1_part["type"] == "tool_result"
    assert call_1_part["tool_use_id"] == call_dup
    assert call_1_part.get("custom_field") == "val1"
    assert isinstance(call_1_part["content"], list)
    assert call_1_part["content"][0]["type"] == "text"
    assert call_1_part["content"][0]["text"] == TOOL_RESULT_NOTE
    native_images = [b for b in call_1_part["content"] if b.get("type") == "image"]
    assert len(native_images) > 0
    # Native image blocks have Anthropic base64 source, not image_url
    source_dict = native_images[0]["source"]
    assert source_dict["type"] == "base64"
    assert source_dict["media_type"] == "image/png"
    raw_png = base64.b64decode(source_dict["data"])
    img = Image.open(io.BytesIO(raw_png))
    assert img.format == "PNG"

    # Ordinal 1 (call_dup, error) was skipped and remains uncompacted string
    call_2_part = replayed_user_parts[2]
    assert call_2_part["type"] == "tool_result"
    assert call_2_part["tool_use_id"] == call_dup
    assert call_2_part.get("custom_field") == "val2"
    assert call_2_part["content"] == "Failed execution"
    assert call_2_part["is_error"] is True

    # 7. Downstream AnthropicMessagesRuntime conversion succeeds without error
    desc, _ = provider_router.get_runtime_descriptor("anthropic/claude-3-opus")
    runtime = provider_registry.create_runtime(desc)
    ctx = ProviderRuntimeContext(
        session_state=None,
        agent_config={"provider_tools": {"anthropic": {"native_tool_result_replay": True}}},
    )
    _, converted = runtime._convert_messages(transformed, context=ctx)
    tool_user_msg = converted[2]
    assert tool_user_msg["role"] == "user"
    assert len(tool_user_msg["content"]) == 3
    assert tool_user_msg["content"][0]["text"] == "User side observation beside calls"
    assert tool_user_msg["content"][1]["tool_use_id"] == call_dup
    assert tool_user_msg["content"][2]["tool_use_id"] == call_dup


@pytest.mark.parametrize(("incoming_images", "compacted"), [(47, True), (48, False)])
def test_inline_counts_nested_anthropic_continuation_images_against_budget(incoming_images, compacted):
    """Images nested in a native continuation consume the provider image budget exactly once."""
    from breadboard_engine.provider.adapters import AnthropicAdapter
    from breadboard_engine.provider.anthropic_replay import replay_messages

    valid_png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGNiAAAABgADNjd8qAAAAABJRU5ErkJggg=="
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": valid_png}}
    batch_msg = AnthropicAdapter().create_tool_result_batch_message([
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "obs with image"},
                image,
                {
                    "type": "tool_result",
                    "tool_use_id": "c1",
                    "content": [{"type": "text", "text": "System log diagnostic output line. " * 300}, image],
                },
            ],
        }
    ])
    # Google allows 50 images; 2 are nested in the continuation and the large result needs 1 frame.
    incoming = [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{valid_png}"}}] * incoming_images
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Process:"}, *incoming]},
        {"role": "assistant", "content": [{"type": "tool_call", "id": "c1", "name": "diag", "arguments": {}}]},
        batch_msg,
        {"role": "assistant", "content": [{"type": "tool_call", "id": "c2", "name": "fresh", "arguments": {}}]},
        {"role": "tool", "tool_call_id": "c2", "content": "Done"},
    ]
    snapshot = copy.deepcopy(messages)

    transformed = apply_inline_snapcompact(
        messages,
        _make_settings(tool_results="all", inline_min_tokens=100),
        supports_images=True,
        provider="google",
        api="google-generative-ai",
    )

    assert messages == snapshot
    assert transformed[0] == messages[0]
    assert transformed[4] == messages[4]
    assert (transformed[2] != batch_msg) is compacted
    replayed = replay_messages(transformed[2]["content"])
    side_images = [b for b in replayed[0]["content"] if b.get("type") == "image"]
    [result] = [b for b in replayed[0]["content"] if b.get("type") == "tool_result"]
    result_images = [b for b in result["content"] if b.get("type") == "image"]
    assert side_images == [image]
    assert result_images.count(image) == 1
    assert len(result_images) == (2 if compacted else 1)


def test_inline_unsupported_canonical_only_preserved_without_imaging():
    """Unsupported canonical tool_result messages without replay are preserved unchanged."""
    canonical_msg = {
        "role": "tool",
        "content": [
            {"type": "tool_result", "call_id": "c1", "content": "Large log " * 400, "is_error": False},
        ],
    }
    freshest = {"role": "tool", "tool_call_id": "c2", "content": "Done"}
    messages = [
        {"role": "user", "content": "Go"},
        canonical_msg,
        freshest,
    ]
    settings = _make_settings(tool_results="all", inline_min_tokens=100)
    transformed = apply_inline_snapcompact(messages, settings, supports_images=True)

    # Canonical message without replay must not be imaged
    assert transformed[1]["content"] == canonical_msg["content"]
    assert transformed[2]["content"] == "Done"


def test_inline_compacts_multi_message_anthropic_batch_and_preserves_sibling_native_message():
    """Compacting an older tool result across multiple native messages preserves sibling messages and side observations exactly."""
    from breadboard_engine.provider.adapters import AnthropicAdapter
    from breadboard_engine.provider.anthropic_replay import replay_messages
    from breadboard_engine.provider.routing import provider_router
    from breadboard_engine.provider.runtime import provider_registry
    from breadboard_engine.provider.contract_runtime import ProviderRuntimeContext

    native = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "First side observation."},
                {"type": "tool_result", "tool_use_id": "call_multi_1", "content": "System log diagnostic output line. " * 300, "is_error": False},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Second side observation."},
                {"type": "tool_result", "tool_use_id": "call_multi_2", "content": "Fresh crisp second result.", "is_error": False},
            ],
        },
    ]
    batch = AnthropicAdapter().create_tool_result_batch_message(copy.deepcopy(native))
    messages = [
        {"role": "user", "content": "Fetch two observations."},
        {"role": "assistant", "content": [
            {"type": "tool_call", "id": "call_multi_1", "name": "fetch", "arguments": {}},
            {"type": "tool_call", "id": "call_multi_2", "name": "fetch", "arguments": {}},
        ]},
        batch,
    ]
    original = copy.deepcopy(messages)
    settings = _make_settings(tool_results="all", inline_min_tokens=100)
    transformed = apply_inline_snapcompact(
        messages,
        settings,
        supports_images=True,
        provider="google",
        api="google-generative-ai",
    )

    assert messages == original
    replayed = replay_messages(transformed[2]["content"])
    assert len(replayed) == 2
    assert replayed[0]["content"][0] == native[0]["content"][0]  # First side observation
    assert replayed[1] == native[1]  # Second native message completely preserved

    first_result = replayed[0]["content"][1]
    assert isinstance(first_result["content"], list)
    assert any(part.get("type") == "image" for part in first_result["content"])

    descriptor, _ = provider_router.get_runtime_descriptor("anthropic/claude-3-opus")
    runtime = provider_registry.create_runtime(descriptor)
    context = ProviderRuntimeContext(None, {"provider_tools": {"anthropic": {"native_tool_result_replay": True}}})
    _, wire = runtime._convert_messages(transformed, context=context)
    assert wire[-1] == native[1]


def test_inline_imaged_native_result_keeps_its_document():
    """An imaged Anthropic-native result keeps its document block for the next same-provider request."""
    from breadboard_engine.provider.adapters import AnthropicAdapter
    from breadboard_engine.provider.anthropic_replay import replay_messages

    document = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "JVBERi0="}}
    batch_msg = AnthropicAdapter().create_tool_result_batch_message([{
        "role": "user",
        "content": [{
            "type": "tool_result",
            "tool_use_id": "c1",
            "content": [{"type": "text", "text": "System log diagnostic output line. " * 300}, document],
        }],
    }])
    messages = [
        {"role": "user", "content": "Process:"},
        {"role": "assistant", "content": [{"type": "tool_call", "id": "c1", "name": "diag", "arguments": {}}]},
        batch_msg,
        {"role": "assistant", "content": [{"type": "tool_call", "id": "c2", "name": "fresh", "arguments": {}}]},
        {"role": "tool", "tool_call_id": "c2", "content": "Done"},
    ]

    transformed = apply_inline_snapcompact(
        messages,
        _make_settings(tool_results="all", inline_min_tokens=100),
        supports_images=True,
        provider="google",
        api="google-generative-ai",
    )

    [result] = replay_messages(transformed[2]["content"])[0]["content"]
    assert "image" in [block["type"] for block in result["content"]]
    assert result["content"][-2:] == [
        {"type": "text", "text": "[Original source document 1; corresponds to its marker in the compacted text.]"},
        document,
    ]
