from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "validate_phase4_claude_parity.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("validate_phase4_claude_parity", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_run(tmp_path: Path, *, scenario: str, text: str) -> Path:
    return _make_run_frames(tmp_path, scenario=scenario, frames_text=[text])


def _make_run_frames(tmp_path: Path, *, scenario: str, frames_text: list[str]) -> Path:
    run_dir = tmp_path / scenario.replace("/", "__")
    frames = run_dir / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    (run_dir / "scenario_manifest.json").write_text(
        json.dumps({"scenario": scenario}) + "\n",
        encoding="utf-8",
    )
    index_lines: list[str] = []
    for idx, text in enumerate(frames_text, start=1):
        frame_name = f"frame_{idx:04d}.txt"
        (frames / frame_name).write_text(text, encoding="utf-8")
        index_lines.append(json.dumps({"frame": idx, "text": f"frames/{frame_name}"}))
    (run_dir / "index.jsonl").write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    return run_dir


def test_streaming_parity_passes_for_classic_footer(tmp_path: Path):
    module = _load_module()
    text = "\n".join(
        [
            "BreadBoard v0.2.0",
            "❯ replay:config/clibridgereplays/phase4/streamingsmokev1.jsonl",
            "• # Streaming smoke",
            "  - line 1",
            "  - line 2",
            "  end",
            "[done]",
            "────────────────",
            '❯ Try "fix typecheck errors"',
            "────────────────",
            "  ? for shortcuts  ✻ Cooked for 2s",
        ]
    )
    run_dir = _make_run(
        tmp_path,
        scenario="phase4_replay/streaming_v1_fullpane_v8",
        text=text,
    )
    result = module.validate_run(run_dir)
    assert result.ok is True
    assert result.errors == []


def test_todo_parity_passes_for_overlay_footer(tmp_path: Path):
    module = _load_module()
    text = "\n".join(
        [
            "BreadBoard v0.2.0",
            "[done]",
            "  ? for shortcuts  ✻ Cooked for 0s",
            "╭────────────╮",
            "│ Todos      │",
            "│ 8 items    │",
            "│ ☐ Implement replay mode │",
            "╰────────────╯",
        ]
    )
    run_dir = _make_run(
        tmp_path,
        scenario="phase4_replay/todo_preview_v1_fullpane_v7",
        text=text,
    )
    result = module.validate_run(run_dir)
    assert result.ok is True
    assert result.errors == []
    assert result.contract_profile == "phase4_text_contract_v1"


def test_overlay_fails_when_blank_gap_between_shortcuts_and_modal(tmp_path: Path):
    module = _load_module()
    text = "\n".join(
        [
            "BreadBoard v0.2.0",
            "[done]",
            "  ? for shortcuts  ✻ Cooked for 0s",
            "",
            "╭────────────╮",
            "│ Todos      │",
            "│ 8 items    │",
            "│ ☐ Implement replay mode │",
            "╰────────────╯",
        ]
    )
    run_dir = _make_run(
        tmp_path,
        scenario="phase4_replay/todo_preview_v1_fullpane_v7",
        text=text,
    )
    result = module.validate_run(run_dir)
    assert result.ok is False
    assert any("blank spacing found between shortcuts row and modal top border" in err for err in result.errors)


def test_classic_footer_fails_when_prompt_row_is_contaminated_by_modal_borders(tmp_path: Path):
    module = _load_module()
    text = "\n".join(
        [
            "BreadBoard v0.2.0",
            "────────────────────────────────",
            '❯ Try "fix typecheck errors" │',
            "────────────────────────────────",
            "  ? for shortcuts  ✻ Cooked for 2s",
        ]
    )
    run_dir = _make_run(
        tmp_path,
        scenario="phase4_replay/streaming_v1_fullpane_v8",
        text=text,
    )
    result = module.validate_run(run_dir)
    assert result.ok is False
    assert any("prompt row contaminated" in err for err in result.errors)


def test_subagents_requires_checkbox_style_status_anchors(tmp_path: Path):
    module = _load_module()
    text = "\n".join(
        [
            "BreadBoard v0.2.0",
            "[done]",
            "  ? for shortcuts  ✻ Cooked for 2s",
            "╭────────────╮",
            "│ Background tasks │",
            "│ ✔ completed · Index workspace files · task-1 │",
            "│ ☒ running · Compute TODO preview metrics · task-2 │",
            "╰────────────╯",
        ]
    )
    run_dir = _make_run(
        tmp_path,
        scenario="phase4_replay/subagents_v1_fullpane_v7",
        text=text,
    )
    result = module.validate_run(run_dir)
    assert result.ok is False
    assert any("missing subagents anchor: '☐ running'" == err for err in result.errors)


def test_subagents_smoke_alias_is_supported(tmp_path: Path):
    module = _load_module()
    text = "\n".join(
        [
            "BreadBoard v0.2.0",
            "✔ completed · Index workspace files · task-1",
            "☐ running · Compute TODO preview metrics · task-2",
            "[done]",
            "  ? for shortcuts  ✻ Cooked for 2s",
            "╭────────────╮",
            "│ Background tasks │",
            "│ › [primary] completed · Compute TODO preview metrics │",
            "│   [primary] completed · Index workspace files │",
            "╰────────────╯",
        ]
    )
    run_dir = _make_run(
        tmp_path,
        scenario="phase4_replay/subagents_smoke_v1_fullpane_v7",
        text=text,
    )
    result = module.validate_run(run_dir)
    assert result.ok is True
    assert result.errors == []


def test_todo_modal_rejects_blank_line_inflation_between_items(tmp_path: Path):
    module = _load_module()
    text = "\n".join(
        [
            "BreadBoard v0.2.0",
            "[done]",
            "  ? for shortcuts  ✻ Cooked for 0s",
            "╭────────────╮",
            "│ Todos      │",
            "│ 8 items    │",
            "│ ☐ Implement replay mode │",
            "",
            "│ ☐ Add tmux phase4 scenarios │",
            "╰────────────╯",
        ]
    )
    run_dir = _make_run(
        tmp_path,
        scenario="phase4_replay/todo_preview_v1_fullpane_v7",
        text=text,
    )
    result = module.validate_run(run_dir)
    assert result.ok is False
    assert any("blank-line inflation between TODO rows" in err for err in result.errors)


def test_thinking_lifecycle_fails_if_final_frame_still_shows_thinking_state(tmp_path: Path):
    module = _load_module()
    run_dir = _make_run_frames(
        tmp_path,
        scenario="phase4_replay/thinking_preview_v1",
        frames_text=[
            "\n".join(
                [
                    "BreadBoard v0.2.0",
                    "❯ replay:config/clibridgereplays/phase4/thinkingpreviewsmoke_v1.jsonl",
                    "│ [task tree] thinking │",
                    '❯ Try "fix typecheck errors"',
                    "  ? for shortcuts  ✻ Cooked for 0s",
                ]
            ),
            "\n".join(
                [
                    "BreadBoard v0.2.0",
                    "❯ replay:config/clibridgereplays/phase4/thinkingpreviewsmoke_v1.jsonl",
                    "• Done. I implemented the requested update.",
                    "│ [task tree] thinking │",
                    "Deciphering... (esc to interrupt · thinking)",
                    "────────────────",
                    '❯ Try "fix typecheck errors"',
                    "────────────────",
                    "  ? for shortcuts  ✻ Cooked for 0s",
                ]
            ),
        ],
    )
    result = module.validate_run(run_dir)
    assert result.ok is False
    assert any("final frame still shows '[task tree] thinking'" in err for err in result.errors)


def test_thinking_tool_interleaved_fails_when_result_precedes_tool_call(tmp_path: Path):
    module = _load_module()
    run_dir = _make_run_frames(
        tmp_path,
        scenario="phase4_replay/thinking_tool_interleaved_v1",
        frames_text=[
            "\n".join(
                [
                    "BreadBoard v0.2.0",
                    "❯ replay:config/clibridgereplays/phase4/thinkingtoolinterleavedsmokev1.jsonl",
                    "│ [task tree] thinking · 1 update │",
                    "Patched src/main.ts",
                    "────────────────",
                    '❯ Try "fix typecheck errors"',
                    "────────────────",
                    "  ? for shortcuts  ✻ Cooked for 0s",
                ]
            ),
            "\n".join(
                [
                    "BreadBoard v0.2.0",
                    "❯ replay:config/clibridgereplays/phase4/thinkingtoolinterleavedsmokev1.jsonl",
                    "Write(src/main.ts)",
                    "Patched src/main.ts and validated the output path.",
                    "│ [task tree] done │",
                    "────────────────",
                    '❯ Try "fix typecheck errors"',
                    "────────────────",
                    "  ? for shortcuts  ✻ Cooked for 1s",
                ]
            ),
        ],
    )
    result = module.validate_run(run_dir)
    assert result.ok is False
    assert any("interleaving grammar violation" in err for err in result.errors)


def test_subagents_concurrency_stress_fails_with_insufficient_running_markers(tmp_path: Path):
    module = _load_module()
    text = "\n".join(
        [
            "BreadBoard v0.2.0",
            "❯ replay:config/clibridgereplays/phase4/subagentsconcurrency20v1.jsonl",
            "Subagent concurrency 20 replay complete",
            "✔ completed · Index shard 01 · task-01",
            "☐ running · Index shard 02 · task-02",
            "────────────────",
            '❯ Try "fix typecheck errors"',
            "────────────────",
            "  ? for shortcuts  ✻ Cooked for 2s",
        ]
    )
    run_dir = _make_run(
        tmp_path,
        scenario="phase4_replay/subagents_concurrency_20_v1",
        text=text,
    )
    result = module.validate_run(run_dir)
    assert result.ok is False
    assert any("too few running markers" in err for err in result.errors)


def test_subagents_concurrency_stress_passes_with_classic_footer_and_dense_markers(tmp_path: Path):
    module = _load_module()
    dense_rows = [
        "✔ completed · Index shard 01 · task-01",
        "✔ completed · Index shard 02 · task-02",
        "✔ completed · Index shard 03 · task-03",
        "☐ running · Parse symbols 04 · task-04",
        "☐ running · Parse symbols 05 · task-05",
        "☐ running · Parse symbols 06 · task-06",
        "☐ running · Parse symbols 07 · task-07",
        "☐ running · Parse symbols 08 · task-08",
        "☐ running · Parse symbols 09 · task-09",
        "☐ running · Parse symbols 10 · task-10",
        "☐ running · Parse symbols 11 · task-11",
    ]
    text = "\n".join(
        [
            "BreadBoard v0.2.0",
            "❯ replay:config/clibridgereplays/phase4/subagentsconcurrency20v1.jsonl",
            *dense_rows,
            "Subagent concurrency 20 replay complete",
            "│ [task tree] done │",
            "────────────────",
            '❯ Try "fix typecheck errors"',
            "────────────────",
            "  ? for shortcuts  ✻ Cooked for 3s",
        ]
    )
    run_dir = _make_run(
        tmp_path,
        scenario="phase4_replay/subagents_concurrency_20_v1",
        text=text,
    )
    result = module.validate_run(run_dir)
    assert result.ok is True
    assert result.errors == []


def test_subagents_strip_churn_stress_passes_without_echo_markers_when_task_grammar_holds(tmp_path: Path):
    module = _load_module()
    text = "\n".join(
        [
            "BreadBoard v0.2.0",
            "☐ running · Index workspace files · task-1",
            "☐ running · Compute TODO preview metrics · task-2",
            "[done]",
            "╭────────────╮",
            "│ Background tasks │",
            "│ Search: replay:config/cli_bridge_replays/phase4/subagents_strip_churn_smoke_v1.jsonl │",
            "│ No tasks match the current filter. │",
            "╰────────────╯",
            "────────────────",
            '❯ Try "fix typecheck errors"',
            "────────────────",
            "  ? for shortcuts  ✻ Cooked for 2s",
        ]
    )
    run_dir = _make_run(
        tmp_path,
        scenario="phase4_replay/subagents_strip_churn_v1",
        text=text,
    )
    result = module.validate_run(run_dir)
    assert result.ok is True
    assert result.errors == []


def test_subagents_strip_churn_stress_fails_with_too_few_running_markers(tmp_path: Path):
    module = _load_module()
    text = "\n".join(
        [
            "BreadBoard v0.2.0",
            "[done]",
            "╭────────────╮",
            "│ Background tasks │",
            "│ No tasks match the current filter. │",
            "╰────────────╯",
            "────────────────",
            '❯ Try "fix typecheck errors"',
            "────────────────",
            "  ? for shortcuts  ✻ Cooked for 2s",
        ]
    )
    run_dir = _make_run(
        tmp_path,
        scenario="phase4_replay/subagents_strip_churn_v1",
        text=text,
    )
    result = module.validate_run(run_dir)
    assert result.ok is False
    assert any("subagents strip churn scenario has too few running markers" in err for err in result.errors)


def test_unmapped_scenario_is_rejected(tmp_path: Path):
    module = _load_module()
    text = "\n".join(
        [
            "BreadBoard v0.2.0",
            "[done]",
            "  ? for shortcuts  ✻ Cooked for 0s",
        ]
    )
    run_dir = _make_run(
        tmp_path,
        scenario="phase4_replay/not_mapped_v1",
        text=text,
    )
    result = module.validate_run(run_dir)
    assert result.ok is False
    assert any("unsupported scenario for parity validation" in err for err in result.errors)
    assert result.contract_profile == "phase4_text_contract_v1"
