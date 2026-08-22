from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from breadboard.rl.phase5.bootstrap import bootstrap_campaign


def _defaults() -> tuple[Path, Path, Path]:
    repository_root = Path(__file__).resolve().parents[2]
    phase_root = repository_root.parent / "docs_tmp" / "ZYPHRA" / "RL_PHASE_5"
    return (
        phase_root / "BB_Z_RL_PHASE_5_CONFIG_NATIVE_EXECUTION_AND_OPTIMIZATION_PLAYBOOK.md",
        phase_root / "phase5_config_native_1000_goal_prompt.txt",
        phase_root / "execution",
    )


def _parser() -> argparse.ArgumentParser:
    playbook, goal_prompt, output_dir = _defaults()
    parser = argparse.ArgumentParser(
        description="Validate and materialize the frozen RL Phase 5 WP0 campaign baseline."
    )
    parser.add_argument("--playbook", type=Path, default=playbook)
    parser.add_argument("--goal-prompt", type=Path, default=goal_prompt)
    parser.add_argument("--output-dir", type=Path, default=output_dir)
    parser.add_argument("--generated-at", required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    result = bootstrap_campaign(
        playbook_path=arguments.playbook,
        goal_prompt_path=arguments.goal_prompt,
        output_dir=arguments.output_dir,
        generated_at=arguments.generated_at,
    )
    counts = "/".join(
        str(result.workstream_counts[workstream]) for workstream in "ABCDEFGH"
    )
    print(
        "phase5 bootstrap valid: "
        f"items={result.item_count} "
        f"points={result.catalog_points} "
        f"counts={counts} "
        f"packets={result.packet_count} "
        "acyclic=true "
        f"catalog_sha256={result.catalog_sha256} "
        f"campaign_spec_sha256={result.campaign_spec_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
