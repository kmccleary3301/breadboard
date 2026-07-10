from pathlib import Path

import pytest

from scripts.e4_parity.materialize_checkout_lane_inputs import (
    LaneInputMaterializationError,
    materialize_checkout_lane_inputs,
)


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    repo = workspace / "repo"
    repo.mkdir(parents=True)
    return workspace, repo


def test_materializes_only_declared_generated_inputs_with_exact_bytes(tmp_path: Path) -> None:
    workspace, repo = _workspace(tmp_path)
    source_index = workspace / "docs_tmp/phase_15/BB_E4_SOURCE_INDEX.json"
    ledger_seed = workspace / "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
    source_index.parent.mkdir(parents=True)
    source_index.write_bytes(b'{"source": "workspace"}\n')
    ledger_seed.write_bytes(b'{"ledger": "workspace"}\n')
    unrelated = workspace / "docs_tmp/phase_15/unrelated.json"
    unrelated.write_text("ambient\n")

    written = materialize_checkout_lane_inputs(
        repo_root=repo,
        workspace_root=workspace,
    )

    assert written == (
        "docs_tmp/phase_15/BB_E4_SOURCE_INDEX.json",
        "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json",
    )
    assert (repo / written[0]).read_bytes() == source_index.read_bytes()
    assert (repo / written[1]).read_bytes() == ledger_seed.read_bytes()
    assert not (repo / "docs_tmp/phase_15/unrelated.json").exists()


def test_rejects_checkout_destination_parent_symlink_escape(tmp_path: Path) -> None:
    workspace, repo = _workspace(tmp_path)
    source = workspace / "docs_tmp/phase_15/BB_E4_SOURCE_INDEX.json"
    source.parent.mkdir(parents=True)
    source.write_text("{}\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "docs_tmp").symlink_to(outside, target_is_directory=True)

    with pytest.raises(LaneInputMaterializationError, match="escapes root"):
        materialize_checkout_lane_inputs(
            repo_root=repo,
            workspace_root=workspace,
            inputs=("docs_tmp/phase_15/BB_E4_SOURCE_INDEX.json",),
        )


def test_rejects_missing_generated_workspace_input(tmp_path: Path) -> None:
    workspace, repo = _workspace(tmp_path)

    with pytest.raises(LaneInputMaterializationError, match="must be a regular file"):
        materialize_checkout_lane_inputs(
            repo_root=repo,
            workspace_root=workspace,
            inputs=("docs_tmp/phase_15/missing.json",),
        )
