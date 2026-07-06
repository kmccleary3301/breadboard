from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from breadboard.rl.m12 import write_m12_transfer_archive  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the non-scoring M12 companion evidence archive.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m12_transfer_prep"),
    )
    parser.add_argument("--archive-name", default="m12_transfer_evidence_pack.tar.gz")
    args = parser.parse_args()

    archive_manifest = write_m12_transfer_archive(
        repo_root=REPO_ROOT,
        output_dir=args.output_dir,
        archive_name=args.archive_name,
    )
    print(
        "archive="
        + archive_manifest["archive_name"]
        + f" entries={archive_manifest['included_entry_count']}"
        + f" sha256={archive_manifest['archive_sha256']}"
        + f" repo_replacement={archive_manifest['archive_is_repo_replacement']}"
        + f" scorecard_update_allowed={archive_manifest['scorecard_update_allowed']}"
    )


if __name__ == "__main__":
    main()
