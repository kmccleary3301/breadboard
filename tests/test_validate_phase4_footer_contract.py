from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "validate_phase4_footer_contract.py"
    spec = importlib.util.spec_from_file_location("validate_phase4_footer_contract", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_run_dir(tmp_path: Path, *, final_text: str) -> Path:
    run_dir = tmp_path / "run"
    frames_dir = run_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    (frames_dir / "frame_0001.txt").write_text(final_text, encoding="utf-8")
    (frames_dir / "frame_0001.ansi").write_text(final_text, encoding="utf-8")
    (run_dir / "index.jsonl").write_text(
        json.dumps(
            {
                "frame": 1,
                "text": "frames/frame_0001.txt",
                "ansi": "frames/frame_0001.ansi",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return run_dir


def test_footer_contract_passes_with_border_prompt_shortcuts_and_status(tmp_path: Path):
    module = _load_module()
    run_dir = _write_run_dir(
        tmp_path,
        final_text="\n".join(
            [
                "header",
                "──────────────────────────────────────────────────",
                '❯ Try "fix typecheck errors"',
                "──────────────────────────────────────────────────",
                "? for shortcuts   ✶ Cooked for 2s",
            ]
        ),
    )

    result = module.validate_footer_contract(run_dir)
    assert result.ok is True
    assert result.errors == []
    assert result.prompt_line_index == 2
    assert result.shortcuts_line_index == 4
    assert result.contract_mode == "classic_input"


def test_footer_contract_fails_when_border_missing(tmp_path: Path):
    module = _load_module()
    run_dir = _write_run_dir(
        tmp_path,
        final_text="\n".join(
            [
                "header",
                "not-a-border",
                '❯ Try "fix typecheck errors"',
                "──────────────────────────────────────────────────",
                "? for shortcuts   ✶ Cooked for 2s",
            ]
        ),
    )

    result = module.validate_footer_contract(run_dir)
    assert result.ok is False
    assert any("not a border line" in err for err in result.errors)


def test_footer_contract_fails_when_shortcuts_or_status_missing(tmp_path: Path):
    module = _load_module()
    run_dir = _write_run_dir(
        tmp_path,
        final_text="\n".join(
            [
                "header",
                "──────────────────────────────────────────────────",
                '❯ Try "fix typecheck errors"',
                "──────────────────────────────────────────────────",
                "shortcuts row without required anchors",
            ]
        ),
    )

    result = module.validate_footer_contract(run_dir)
    assert result.ok is False
    assert any("shortcuts anchor missing" in err for err in result.errors)
    assert any("status anchor missing" in err for err in result.errors)


def test_footer_contract_fails_when_lower_input_border_missing(tmp_path: Path):
    module = _load_module()
    run_dir = _write_run_dir(
        tmp_path,
        final_text="\n".join(
            [
                "header",
                "──────────────────────────────────────────────────",
                '❯ Try "fix typecheck errors"',
                "line below prompt that is not a border",
                "? for shortcuts   ✶ Cooked for 2s",
            ]
        ),
    )

    result = module.validate_footer_contract(run_dir)
    assert result.ok is False
    assert any("line below prompt row is not a border line" in err for err in result.errors)


def test_footer_contract_passes_overlay_replacement_mode(tmp_path: Path):
    module = _load_module()
    run_dir = _write_run_dir(
        tmp_path,
        final_text="\n".join(
            [
                "[done]",
                "? for shortcuts   ✶ Cooked for 2s",
                "╭──────────────────────────────╮",
                "│  Todos                       │",
                "╰──────────────────────────────╯",
            ]
        ),
    )

    result = module.validate_footer_contract(run_dir)
    assert result.ok is True
    assert result.errors == []
    assert result.contract_mode == "overlay_replacement"
    assert result.shortcuts_line_index == 1
