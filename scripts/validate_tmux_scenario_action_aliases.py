#!/usr/bin/env python3
"""
Validate that legacy tmux scenario-action aliases match canonical files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ALIAS_PAIRS: tuple[tuple[str, str], ...] = (
    (
        "config/tmux_scenario_actions/phase4_replay/everything_showcase_v1.json",
        "config/tmux_scenario_actions/ci/phase4_replay/everything_showcase_v1.json",
    ),
    (
        "config/tmux_scenario_actions/phase4_replay/streaming_v1.json",
        "config/tmux_scenario_actions/ci/phase4_replay/streaming_v1.json",
    ),
    (
        "config/tmux_scenario_actions/phase4_replay/subagents_v1.json",
        "config/tmux_scenario_actions/ci/phase4_replay/subagents_v1.json",
    ),
    (
        "config/tmux_scenario_actions/phase4_replay/subagents_strip_churn_v1.json",
        "config/tmux_scenario_actions/ci/phase4_replay/subagents_strip_churn_v1.json",
    ),
    (
        "config/tmux_scenario_actions/phase4_replay/resize_overlay_interaction_v1.json",
        "config/tmux_scenario_actions/ci/phase4_replay/resize_overlay_interaction_v1.json",
    ),
    (
        "config/tmux_scenario_actions/phase4_replay/thinking_multiturn_lifecycle_v1.json",
        "config/tmux_scenario_actions/ci/phase4_replay/thinking_multiturn_lifecycle_v1.json",
    ),
    (
        "config/tmux_scenario_actions/phase4_replay/thinking_lifecycle_expiration_v1.json",
        "config/tmux_scenario_actions/ci/phase4_replay/thinking_lifecycle_expiration_v1.json",
    ),
    (
        "config/tmux_scenario_actions/phase4_replay/thinking_preview_v1.json",
        "config/tmux_scenario_actions/ci/phase4_replay/thinking_preview_v1.json",
    ),
    (
        "config/tmux_scenario_actions/phase4_replay/thinking_reasoning_only_v1.json",
        "config/tmux_scenario_actions/ci/phase4_replay/thinking_reasoning_only_v1.json",
    ),
    (
        "config/tmux_scenario_actions/phase4_replay/thinking_tool_interleaved_v1.json",
        "config/tmux_scenario_actions/ci/phase4_replay/thinking_tool_interleaved_v1.json",
    ),
    (
        "config/tmux_scenario_actions/phase4_replay/todo_preview_v1.json",
        "config/tmux_scenario_actions/ci/phase4_replay/todo_preview_v1.json",
    ),
    (
        "config/tmux_scenario_actions/phase4_replay/large_diff_artifact_v1.json",
        "config/tmux_scenario_actions/ci/phase4_replay/large_diff_artifact_v1.json",
    ),
    (
        "config/tmux_scenario_actions/phase4_replay/large_output_artifact_v1.json",
        "config/tmux_scenario_actions/ci/phase4_replay/large_output_artifact_v1.json",
    ),
    (
        "config/tmux_scenario_actions/phase4_replay/alt_buffer_enter_exit_v1.json",
        "config/tmux_scenario_actions/ci/phase4_replay/alt_buffer_enter_exit_v1.json",
    ),
    (
        "config/tmux_scenario_actions/claude_e2e_compact_semantic_v1.json",
        "config/tmux_scenario_actions/nightly_provider/claude_e2e_compact_semantic_v1.json",
    ),
    (
        "config/tmux_scenario_actions/codex_e2e_compact_semantic_v1.json",
        "config/tmux_scenario_actions/nightly_provider/codex_e2e_compact_semantic_v1.json",
    ),
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate tmux scenario action alias parity.")
    parser.add_argument(
        "--repo-root",
        default=".",
        help="repo root containing config/tmux_scenario_actions",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="optional JSON report output path",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).expanduser().resolve()
    errors: list[str] = []
    rows: list[dict[str, Any]] = []

    for legacy_rel, canonical_rel in ALIAS_PAIRS:
        legacy = repo_root / legacy_rel
        canonical = repo_root / canonical_rel
        row: dict[str, Any] = {
            "legacy": legacy_rel,
            "canonical": canonical_rel,
            "legacy_exists": legacy.exists(),
            "canonical_exists": canonical.exists(),
            "match": False,
        }
        if not legacy.exists():
            errors.append(f"missing legacy alias file: {legacy_rel}")
            rows.append(row)
            continue
        if not canonical.exists():
            errors.append(f"missing canonical file: {canonical_rel}")
            rows.append(row)
            continue
        legacy_hash = _sha256(legacy)
        canonical_hash = _sha256(canonical)
        row["legacy_sha256"] = legacy_hash
        row["canonical_sha256"] = canonical_hash
        row["match"] = legacy_hash == canonical_hash
        if legacy_hash != canonical_hash:
            errors.append(
                f"hash mismatch: {legacy_rel} != {canonical_rel} "
                f"({legacy_hash[:12]} vs {canonical_hash[:12]})"
            )
        rows.append(row)

    report = {"ok": len(errors) == 0, "errors": errors, "pairs": rows}
    if args.output_json:
        out = Path(args.output_json).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    if errors:
        print("[tmux-action-aliases] fail")
        for err in errors:
            print(f"- {err}")
        return 2

    print("[tmux-action-aliases] pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
