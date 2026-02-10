import importlib.util
import sys
from pathlib import Path


def _load_compare_module():
    repo_root = Path(__file__).resolve().parents[1]
    path = repo_root / "scripts" / "compare_tmux_run_to_golden.py"
    spec = importlib.util.spec_from_file_location("compare_tmux_run_to_golden", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclasses needs the module to be present in sys.modules to resolve
    # forward-reference annotations reliably under Python 3.12.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_layout_signature_normalizes_prompt_and_context_left():
    mod = _load_compare_module()
    text_a = "\n".join(
        [
            "[codex-logged] transport: chatgpt-auth (...)",
            "╭── header ──╮",
            "│ OpenAI Codex │",
            "╰─────────────╯",
            "",
            "› Run /review on my current changes",
            "  ? for shortcuts                                                                 99% context left",
        ]
    )
    text_b = "\n".join(
        [
            "[codex-logged] transport: chatgpt-auth (...)",
            "╭── header ──╮",
            "│ OpenAI Codex │",
            "╰─────────────╯",
            "",
            "› Try \"fix lint errors\"",
            "  ? for shortcuts                                                                 12% context left",
        ]
    )
    sig_a = mod.layout_signature(text_a, provider="codex")
    sig_b = mod.layout_signature(text_b, provider="codex")

    # Prompt content should be masked, but the chevron line should remain present.
    assert "› <prompt>" in sig_a
    assert "› <prompt>" in sig_b

    # Context-left counter should be normalized to a stable token.
    assert "<pct>% context left" in sig_a
    assert "<pct>% context left" in sig_b

    # Two different prompt/counter values should still look "layout-similar".
    assert mod.similarity(sig_a, sig_b) > 0.95


def test_layout_signature_preserves_box_drawing_lines():
    mod = _load_compare_module()
    text = "\n".join(
        [
            "line 1",
            "╭────────────╮",
            "│  content   │",
            "╰────────────╯",
            "middle content that should be ignored for layout",
            "another middle line",
            "tail",
            "› Try \"refactor <filepath>\"",
        ]
    )
    sig = mod.layout_signature(text, provider="unknown", head_lines=2, tail_lines=2)
    # Box drawing lines should be preserved even if they appear in the middle.
    assert "╭────────────╮" in sig
    assert "╰────────────╯" in sig
    # Prompt content should be masked.
    assert "› <prompt>" in sig
