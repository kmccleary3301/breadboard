from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci import select_tests

ROOT = Path(__file__).resolve().parents[1]
SELECTOR = ROOT / "scripts" / "ci" / "select_tests.py"
EXPECTED_FULL_NODE_TARGETS = [
    "sdk/ts",
    "sdk/ts-backbone",
    "sdk/ts-execution-driver-local",
    "sdk/ts-execution-driver-oci",
    "sdk/ts-execution-driver-remote",
    "sdk/ts-execution-drivers",
    "sdk/ts-host-bridges",
    "sdk/ts-host-kits",
    "sdk/ts-host-t3",
    "sdk/ts-kernel-contracts",
    "sdk/ts-kernel-core",
    "sdk/ts-orchestration-temporal",
    "sdk/ts-transport-ai-sdk",
    "sdk/ts-workspace",
    "tui_skeleton",
    "vscode_sidebar",
]


def _assert_full(selection: select_tests.Selection) -> None:
    assert selection.full is True
    assert selection.python is True
    assert list(selection.node_targets) == EXPECTED_FULL_NODE_TARGETS


def test_empty_paths_choose_full_suite() -> None:
    _assert_full(select_tests.select_paths([]))


@pytest.mark.parametrize(
    "path",
    [
        "unknown/new_package/source.ts",
        "breadboard/core/worker.py",
        "pyproject.toml",
        "uv.lock",
        ".github/workflows/ci.yml",
        "tests/test_example.py",
        "tests/conftest.py",
        "tests/fixtures/shared.json",
        "sdk/ts-orchestration-temporal/test/temporal.test.ts",
        "sdk/ts-orchestration-temporal/package.json",
        "sdk/ts-orchestration-temporal/package-lock.json",
        "sdk/ts-orchestration-temporal/tsconfig.json",
        "sdk/ts-kernel-core/src/kernel.ts",
        "sdk/ts-workspace/src/workspace.ts",
    ],
    ids=[
        "unknown-path",
        "python-implementation",
        "python-build-config",
        "lockfile",
        "ci-config",
        "python-test",
        "shared-conftest",
        "shared-fixture",
        "node-test",
        "package-manifest",
        "package-lockfile",
        "typescript-build-config",
        "shared-kernel-source",
        "workspace-full-only",
    ],
)
def test_unknown_shared_test_and_config_paths_choose_full(path: str) -> None:
    _assert_full(select_tests.select_paths([path]))


def test_kernel_core_indirect_dependency_keeps_backbone_tests() -> None:
    # backbone.test.ts imports the backbone entry point; backbone source imports
    # kernel-core, so the test has no direct kernel-core import to map.
    selection = select_tests.select_paths(["sdk/ts-kernel-core/src/core.ts"])

    _assert_full(selection)
    # npm --prefix sdk/ts-backbone test runs test/backbone.test.ts.
    assert "sdk/ts-backbone" in selection.node_targets


def test_independent_temporal_source_selects_only_its_package() -> None:
    selection = select_tests.select_paths(
        ["sdk/ts-orchestration-temporal/src/orchestrator.ts"]
    )

    assert selection.full is False
    assert selection.python is False
    assert list(selection.node_targets) == ["sdk/ts-orchestration-temporal"]


def test_local_source_changes_union_targets_and_allow_documentation() -> None:
    selection = select_tests.select_paths(
        [
            "docs/ci/affected-tests.md",
            "tui_skeleton/src/main.ts",
            "vscode_sidebar/src/extension.ts",
        ]
    )

    assert selection.full is False
    assert selection.python is False
    assert list(selection.node_targets) == ["tui_skeleton", "vscode_sidebar"]


def test_documentation_only_changes_skip_tests() -> None:
    selection = select_tests.select_paths(
        ["docs/ci/affected-tests.md", "docs/ci/consolidation.md"]
    )

    assert selection.full is False
    assert selection.python is False
    assert selection.node_targets == ()


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "docs/INDEX.md",
        "docs/concepts/provider-plan-auth.md",
        "docs/contracts/kernel/semantics/kernel_event_v1.md",
        "docs/ci/REGRESSION_MATRIX.md",
        "docs/contracts/schema.json",
        "docs/fixtures/example.md",
    ],
)
def test_documentation_test_inputs_and_other_docs_choose_full(path: str) -> None:
    _assert_full(select_tests.select_paths([path]))


def test_renamed_paths_are_both_retained() -> None:
    assert select_tests.parse_name_status(
        b"R100\0sdk/ts-orchestration-temporal/src/old.ts\0"
        b"sdk/ts-orchestration-temporal/src/new.ts\0"
    ) == (
        "sdk/ts-orchestration-temporal/src/old.ts",
        "sdk/ts-orchestration-temporal/src/new.ts",
    )


def test_deleted_source_path_is_selected_from_git_diff(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    relative_path = "sdk/ts-orchestration-temporal/src/removed.ts"
    source = repo / relative_path
    source.parent.mkdir(parents=True)
    source.write_text("export const removed = true;\n", encoding="utf-8")
    subprocess.run(["git", "add", relative_path], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Selector test", "-c", "user.email=selector@example.invalid", "commit", "-qm", "initial"],
        cwd=repo,
        check=True,
    )
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    subprocess.run(["git", "rm", "-q", relative_path], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Selector test", "-c", "user.email=selector@example.invalid", "commit", "-qm", "delete source"],
        cwd=repo,
        check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    paths = select_tests.changed_paths(base, head, repo_root=repo)
    assert paths == (relative_path,)
    selection = select_tests.select_paths(paths)
    assert selection.full is False
    assert selection.node_targets == ("sdk/ts-orchestration-temporal",)


def test_empty_cli_diff_writes_full_github_outputs(tmp_path: Path) -> None:
    github_output = tmp_path / "github-output.txt"
    completed = subprocess.run(
        [
            sys.executable,
            str(SELECTOR),
            "--paths",
            "--github-output",
            str(github_output),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    result = json.loads(completed.stdout)
    assert result == {
        "full": True,
        "python": True,
        "node_targets": EXPECTED_FULL_NODE_TARGETS,
    }
    assert github_output.read_text(encoding="utf-8").splitlines() == [
        "full=true",
        "python=true",
        f"node_targets={json.dumps(EXPECTED_FULL_NODE_TARGETS, separators=(',', ':'))}",
    ]


def test_docs_only_cli_emits_empty_targets(tmp_path: Path) -> None:
    github_output = tmp_path / "github-output.txt"
    completed = subprocess.run(
        [
            sys.executable,
            str(SELECTOR),
            "--paths",
            "docs/ci/affected-tests.md",
            "--github-output",
            str(github_output),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {
        "full": False,
        "python": False,
        "node_targets": [],
    }
    assert github_output.read_text(encoding="utf-8").splitlines() == [
        "full=false",
        "python=false",
        "node_targets=[]",
    ]


def test_git_diff_failure_falls_back_to_full(tmp_path: Path) -> None:
    github_output = tmp_path / "github-output.txt"
    completed = subprocess.run(
        [
            sys.executable,
            str(SELECTOR),
            "--base",
            "missing-base-sha",
            "--head",
            "HEAD",
            "--github-output",
            str(github_output),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    result = json.loads(completed.stdout)
    assert result["full"] is True
    assert result["python"] is True
    assert result["node_targets"] == EXPECTED_FULL_NODE_TARGETS
    assert "selecting the full suite" in completed.stderr
