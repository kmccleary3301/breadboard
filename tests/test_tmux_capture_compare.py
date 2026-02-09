from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


compare_mod = _load_module(
    REPO_ROOT / "scripts" / "compare_tmux_run_to_golden.py",
    "compare_tmux_run_to_golden_test_mod",
)


def _write_run(run_dir: Path, scenario: str, frame_text: str, *, actions_count: int = 2) -> None:
    frames = run_dir / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    (frames / "frame_0001.txt").write_text(frame_text, encoding="utf-8")
    manifest = {
        "scenario": scenario,
        "run_id": run_dir.name,
        "target": "breadboard_test_x:0.0",
        "actions_count": actions_count,
        "must_contain": ["compact-ready"],
        "must_not_contain": ["Conversation interrupted"],
        "must_match_regex": ["compact-ready"],
    }
    summary = {
        "scenario": scenario,
        "run_id": run_dir.name,
        "scenario_result": "pass",
        "actions_count": actions_count,
        "semantic_failures_count": 0,
    }
    (run_dir / "scenario_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "run_summary.json").write_text(json.dumps(summary), encoding="utf-8")


def _write_frame(run_dir: Path, index: int, text: str) -> None:
    frames = run_dir / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    (frames / f"frame_{index:04d}.txt").write_text(text, encoding="utf-8")


def _write_png_frame(run_dir: Path, index: int, color: tuple[int, int, int, int]) -> None:
    frames = run_dir / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (8, 8), color)
    image.save(frames / f"frame_{index:04d}.png")


def test_normalize_text_strips_ansi_and_dynamic_tokens():
    raw = "\u001b[31mError\u001b[0m took 3m 10s at 2026-02-09T12:00:00Z run 20260209-120000"
    normalized = compare_mod.normalize_text(raw)
    assert "Error" in normalized
    assert "\u001b[" not in normalized
    assert "<duration>" in normalized
    assert "<iso_ts>" in normalized
    assert "<stamp>" in normalized


def test_redact_obj_masks_secret_like_text():
    payload = {"x": "OPENAI_API_KEY=sk-ABCDEFGHIJKLMNOPQRSTUV", "y": ["Bearer abcdefghijklmnop"]}
    redacted = compare_mod.redact_obj(payload)
    assert "<redacted>" in redacted["x"]
    assert "<redacted>" in redacted["y"][0]


def test_compare_identical_run_and_golden_pass(tmp_path: Path):
    run_dir = tmp_path / "run"
    gold_dir = tmp_path / "gold"
    text = 'Claude Code\ncompact-ready\nTry "refactor <filepath>"\n'
    _write_run(run_dir, "claude/e2e_compact_semantic_v1", text)
    _write_run(gold_dir, "claude/e2e_compact_semantic_v1", text)
    result = compare_mod.compare(
        run_dir=run_dir,
        golden_dir=gold_dir,
        provider_hint="claude",
        scenario_hint="",
        thresholds_cfg={},
        fail_mode="strict",
        max_layout_drift=None,
        max_semantic_misses=None,
        allow_provider_banner_drift=None,
        redact=False,
    )
    assert result.report["overall_status"] == "pass"
    assert result.strict_fail is False


def test_compare_warn_mode_does_not_strict_fail(tmp_path: Path):
    run_dir = tmp_path / "run"
    gold_dir = tmp_path / "gold"
    _write_run(run_dir, "codex/e2e_compact_semantic_v1", "OpenAI Codex\nTry \"x\"\n")
    _write_run(gold_dir, "codex/e2e_compact_semantic_v1", "OpenAI Codex\ncompact-ready\nTry \"x\"\n")
    result = compare_mod.compare(
        run_dir=run_dir,
        golden_dir=gold_dir,
        provider_hint="codex",
        scenario_hint="",
        thresholds_cfg={},
        fail_mode="warn",
        max_layout_drift=None,
        max_semantic_misses=None,
        allow_provider_banner_drift=None,
        redact=False,
    )
    assert result.report["overall_status"] == "warn"
    assert result.strict_fail is False


def test_compare_strict_fail_on_semantic_miss(tmp_path: Path):
    run_dir = tmp_path / "run"
    gold_dir = tmp_path / "gold"
    _write_run(run_dir, "codex/e2e_compact_semantic_v1", "OpenAI Codex\nTry \"x\"\n")
    _write_run(gold_dir, "codex/e2e_compact_semantic_v1", "OpenAI Codex\ncompact-ready\nTry \"x\"\n")
    result = compare_mod.compare(
        run_dir=run_dir,
        golden_dir=gold_dir,
        provider_hint="codex",
        scenario_hint="",
        thresholds_cfg={},
        fail_mode="strict",
        max_layout_drift=None,
        max_semantic_misses=None,
        allow_provider_banner_drift=None,
        redact=False,
    )
    assert result.report["overall_status"] == "fail"
    assert result.strict_fail is True


def test_compare_flags_scenario_mismatch(tmp_path: Path):
    run_dir = tmp_path / "run"
    gold_dir = tmp_path / "gold"
    _write_run(run_dir, "codex/e2e_compact_semantic_v1", "compact-ready\n")
    _write_run(gold_dir, "claude/e2e_compact_semantic_v1", "compact-ready\n")
    result = compare_mod.compare(
        run_dir=run_dir,
        golden_dir=gold_dir,
        provider_hint="",
        scenario_hint="",
        thresholds_cfg={},
        fail_mode="strict",
        max_layout_drift=None,
        max_semantic_misses=None,
        allow_provider_banner_drift=None,
        redact=False,
    )
    assert any("scenario mismatch" in item for item in result.report["semantic"]["failures"])


def test_compare_flags_missing_frames(tmp_path: Path):
    run_dir = tmp_path / "run"
    gold_dir = tmp_path / "gold"
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_run(gold_dir, "claude/e2e_compact_semantic_v1", "Claude Code\ncompact-ready\n")
    (run_dir / "scenario_manifest.json").write_text(
        json.dumps(
            {
                "scenario": "claude/e2e_compact_semantic_v1",
                "run_id": "r1",
                "target": "breadboard_test_x:0.0",
                "actions_count": 1,
                "must_contain": [],
                "must_not_contain": [],
                "must_match_regex": [],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "run_summary.json").write_text(
        json.dumps(
            {
                "scenario": "claude/e2e_compact_semantic_v1",
                "run_id": "r1",
                "scenario_result": "pass",
                "actions_count": 1,
                "semantic_failures_count": 0,
            }
        ),
        encoding="utf-8",
    )
    result = compare_mod.compare(
        run_dir=run_dir,
        golden_dir=gold_dir,
        provider_hint="claude",
        scenario_hint="",
        thresholds_cfg={},
        fail_mode="strict",
        max_layout_drift=None,
        max_semantic_misses=None,
        allow_provider_banner_drift=None,
        redact=False,
    )
    assert result.report["layout"]["status"] == "fail"
    assert result.strict_fail is True


def test_compare_ignores_extra_tail_frames_outside_overlap(tmp_path: Path):
    run_dir = tmp_path / "run"
    gold_dir = tmp_path / "gold"
    _write_run(run_dir, "codex/e2e_compact_semantic_v1", "OpenAI Codex\ncompact-ready\n")
    _write_run(gold_dir, "codex/e2e_compact_semantic_v1", "OpenAI Codex\ncompact-ready\n")
    _write_frame(run_dir, 2, "OpenAI Codex\ncompact-ready\n")
    _write_frame(run_dir, 3, "OpenAI Codex\ncompact-ready\n")

    result = compare_mod.compare(
        run_dir=run_dir,
        golden_dir=gold_dir,
        provider_hint="codex",
        scenario_hint="",
        thresholds_cfg={},
        fail_mode="strict",
        max_layout_drift=None,
        max_semantic_misses=None,
        allow_provider_banner_drift=None,
        redact=False,
    )
    assert result.report["layout"]["status"] == "pass"
    assert result.report["layout"]["missing_in_run"] == []
    assert result.report["layout"]["missing_in_golden"] == []
    assert result.report["layout"]["extra_in_run_outside_overlap"] == [2, 3]


def test_threshold_resolution_precedence_provider_scenario_and_cli(tmp_path: Path):
    run_dir = tmp_path / "run"
    gold_dir = tmp_path / "gold"
    _write_run(run_dir, "codex/e2e_compact_semantic_v1", "OpenAI Codex\ncompact-ready\n")
    _write_run(gold_dir, "codex/e2e_compact_semantic_v1", "OpenAI Codex\ncompact-ready\n")
    thresholds_cfg = {
        "defaults": {
            "layout": {"max_drift_score": 0.4},
            "semantic": {"max_misses": 1},
            "provider": {"allow_banner_drift": True, "max_token_drifts": 2},
        },
        "providers": {
            "codex": {
                "layout": {"max_drift_score": 0.3},
                "semantic": {"max_misses": 2},
            }
        },
        "scenarios": {
            "codex/e2e_compact_semantic_v1": {
                "layout": {"max_drift_score": 0.2},
                "semantic": {"max_misses": 0},
            }
        },
    }
    result = compare_mod.compare(
        run_dir=run_dir,
        golden_dir=gold_dir,
        provider_hint="codex",
        scenario_hint="codex/e2e_compact_semantic_v1",
        thresholds_cfg=thresholds_cfg,
        fail_mode="strict",
        max_layout_drift=0.1,
        max_semantic_misses=3,
        allow_provider_banner_drift=False,
        redact=False,
    )
    effective = result.report["thresholds_effective"]
    assert effective["layout"]["max_drift_score"] == 0.1
    assert effective["semantic"]["max_misses"] == 3
    assert effective["provider"]["allow_banner_drift"] is False
    assert effective["provider"]["max_token_drifts"] == 2


def test_compare_semantic_miss_within_overlap_is_strict_failure(tmp_path: Path):
    run_dir = tmp_path / "run"
    gold_dir = tmp_path / "gold"
    _write_run(run_dir, "claude/e2e_compact_semantic_v1", "Claude Code\nTry \"x\"\n")
    _write_run(gold_dir, "claude/e2e_compact_semantic_v1", "Claude Code\ncompact-ready\nTry \"x\"\n")
    result = compare_mod.compare(
        run_dir=run_dir,
        golden_dir=gold_dir,
        provider_hint="claude",
        scenario_hint="",
        thresholds_cfg={},
        fail_mode="strict",
        max_layout_drift=None,
        max_semantic_misses=None,
        allow_provider_banner_drift=None,
        redact=False,
    )
    assert result.report["semantic"]["status"] == "fail"
    assert result.strict_fail is True


def test_compare_ignores_out_of_overlap_tail_drift_but_fails_in_window_drift(tmp_path: Path):
    run_pass = tmp_path / "run_pass"
    gold_pass = tmp_path / "gold_pass"
    _write_run(run_pass, "codex/e2e_compact_semantic_v1", "OpenAI Codex\ncompact-ready\n")
    _write_run(gold_pass, "codex/e2e_compact_semantic_v1", "OpenAI Codex\ncompact-ready\n")
    # Tail drift outside overlap should not affect layout score.
    _write_frame(run_pass, 2, "OpenAI Codex\ncompact-ready\nTAIL-DRIFT-A\nTAIL-DRIFT-B\n")
    pass_result = compare_mod.compare(
        run_dir=run_pass,
        golden_dir=gold_pass,
        provider_hint="codex",
        scenario_hint="",
        thresholds_cfg={},
        fail_mode="strict",
        max_layout_drift=None,
        max_semantic_misses=None,
        allow_provider_banner_drift=None,
        redact=False,
    )
    assert pass_result.report["layout"]["status"] == "pass"
    assert pass_result.strict_fail is False

    run_fail = tmp_path / "run_fail"
    gold_fail = tmp_path / "gold_fail"
    _write_run(run_fail, "codex/e2e_compact_semantic_v1", "OpenAI Codex\ncompact-ready\nx\nx\nx\nx\nx\nx\nx\n")
    _write_run(gold_fail, "codex/e2e_compact_semantic_v1", "OpenAI Codex\ncompact-ready\n")
    fail_result = compare_mod.compare(
        run_dir=run_fail,
        golden_dir=gold_fail,
        provider_hint="codex",
        scenario_hint="",
        thresholds_cfg={},
        fail_mode="strict",
        max_layout_drift=None,
        max_semantic_misses=None,
        allow_provider_banner_drift=None,
        redact=False,
    )
    assert fail_result.report["layout"]["status"] == "fail"
    assert fail_result.strict_fail is True


def test_cli_returns_error_on_corrupt_json(tmp_path: Path):
    run_dir = tmp_path / "run"
    gold_dir = tmp_path / "gold"
    run_dir.mkdir(parents=True, exist_ok=True)
    gold_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "scenario_manifest.json").write_text("{bad", encoding="utf-8")
    (run_dir / "run_summary.json").write_text("{}", encoding="utf-8")
    (gold_dir / "scenario_manifest.json").write_text("{}", encoding="utf-8")
    (gold_dir / "run_summary.json").write_text("{}", encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "compare_tmux_run_to_golden.py"),
            "--run-dir",
            str(run_dir),
            "--golden-dir",
            str(gold_dir),
        ],
        text=True,
        capture_output=True,
    )
    assert proc.returncode != 0


def test_compare_supports_legacy_manifest_without_run_summary(tmp_path: Path):
    run_dir = tmp_path / "run"
    gold_dir = tmp_path / "gold"
    _write_run(run_dir, "claude/reference_compaction", "Claude Code\ncompact-ready\n")
    _write_run(gold_dir, "claude/reference_compaction", "Claude Code\ncompact-ready\n")
    (run_dir / "run_summary.json").unlink()
    (gold_dir / "run_summary.json").unlink()
    result = compare_mod.compare(
        run_dir=run_dir,
        golden_dir=gold_dir,
        provider_hint="claude",
        scenario_hint="",
        thresholds_cfg={},
        fail_mode="strict",
        max_layout_drift=None,
        max_semantic_misses=None,
        allow_provider_banner_drift=None,
        redact=False,
    )
    assert result.report["overall_status"] == "pass"


def test_compare_pixel_checks_fail_when_enabled_and_drift_exceeds_threshold(tmp_path: Path):
    run_dir = tmp_path / "run"
    gold_dir = tmp_path / "gold"
    _write_run(run_dir, "codex/e2e_compact_semantic_v1", "OpenAI Codex\ncompact-ready\n")
    _write_run(gold_dir, "codex/e2e_compact_semantic_v1", "OpenAI Codex\ncompact-ready\n")
    _write_png_frame(run_dir, 1, (255, 255, 255, 255))
    _write_png_frame(gold_dir, 1, (0, 0, 0, 255))

    result = compare_mod.compare(
        run_dir=run_dir,
        golden_dir=gold_dir,
        provider_hint="codex",
        scenario_hint="",
        thresholds_cfg={
            "defaults": {
                "pixel": {
                    "enabled": True,
                    "max_final_change_ratio": 0.01,
                    "max_final_mean_abs_diff": 0.01,
                }
            }
        },
        fail_mode="strict",
        max_layout_drift=None,
        max_semantic_misses=None,
        allow_provider_banner_drift=None,
        redact=False,
    )
    assert result.report["pixel"]["status"] == "fail"
    assert result.report["overall_status"] == "fail"
    assert result.strict_fail is True


def test_compare_pixel_checks_skip_when_disabled(tmp_path: Path):
    run_dir = tmp_path / "run"
    gold_dir = tmp_path / "gold"
    _write_run(run_dir, "codex/e2e_compact_semantic_v1", "OpenAI Codex\ncompact-ready\n")
    _write_run(gold_dir, "codex/e2e_compact_semantic_v1", "OpenAI Codex\ncompact-ready\n")
    _write_png_frame(run_dir, 1, (10, 20, 30, 255))
    _write_png_frame(gold_dir, 1, (200, 210, 220, 255))

    result = compare_mod.compare(
        run_dir=run_dir,
        golden_dir=gold_dir,
        provider_hint="codex",
        scenario_hint="",
        thresholds_cfg={},
        fail_mode="strict",
        max_layout_drift=None,
        max_semantic_misses=None,
        allow_provider_banner_drift=None,
        redact=False,
    )
    assert result.report["pixel"]["status"] == "skip"
    assert result.report["overall_status"] == "pass"


def test_compare_pixel_checks_fail_when_enabled_without_png_overlap(tmp_path: Path):
    run_dir = tmp_path / "run"
    gold_dir = tmp_path / "gold"
    _write_run(run_dir, "claude/e2e_compact_semantic_v1", "Claude Code\ncompact-ready\n")
    _write_run(gold_dir, "claude/e2e_compact_semantic_v1", "Claude Code\ncompact-ready\n")

    result = compare_mod.compare(
        run_dir=run_dir,
        golden_dir=gold_dir,
        provider_hint="claude",
        scenario_hint="",
        thresholds_cfg={"defaults": {"pixel": {"enabled": True}}},
        fail_mode="strict",
        max_layout_drift=None,
        max_semantic_misses=None,
        allow_provider_banner_drift=None,
        redact=False,
    )
    assert result.report["pixel"]["status"] == "fail"
    assert any("no overlapping PNG frames" in item for item in result.report["pixel"]["failures"])
    assert result.strict_fail is True
