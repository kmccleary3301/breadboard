from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.e4_parity import run_lane
from scripts.e4_parity.path_refs import ReferenceResolutionError, resolve_declared_reference


DERIVED_LEDGER_REF = "docs_tmp/phase_20/derived/BB_ER_FEATURE_LEDGER.json"


def _nested_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "workspace" / "docs_tmp" / "phase_20" / "worktrees" / "f5"
    checkout.mkdir(parents=True)
    return checkout


def _write_claim_lane(lane_def_dir: Path, lane_id: str, output_ref: str) -> None:
    lane_def_dir.mkdir(parents=True)
    payload = {
        "schema_version": "bb.e4.lane_def.v1",
        "lane_id": lane_id,
        "config_id": "f5_am19_resolution_fixture",
        "target_family": "test",
        "target_version": "v1",
        "kind": "target_support",
        "status": "claimed",
        "points": 1,
        "capture": {"strategy": "legacy_builder", "argv": None, "inputs": ["fixture"]},
        "normalize": {"mode": "identity", "translator": "identity", "config": {}},
        "replay": {
            "mode": "stored",
            "artifacts": ["fixture"],
            "session": None,
            "comparator_class": "byte",
        },
        "compare": {"comparator": "byte", "config": {}},
        "claim": {"scope": {"behaviors": ["claim"], "surfaces": ["artifact"]}, "exclusions": []},
        "artifacts_root": "artifacts/conformance/node_gate",
        "reverify_command": {
            "argv": ["python", "claim.py", "--json-out", output_ref],
            "cwd": ".",
        },
    }
    (lane_def_dir / f"{lane_id}.yaml").write_text(json.dumps(payload), encoding="utf-8")


def _json_output_path(command: list[str]) -> Path:
    for index, argument in enumerate(command):
        if argument == "--json-out":
            return Path(command[index + 1])
        if argument.startswith("--json-out="):
            return Path(argument.split("=", 1)[1])
    raise AssertionError(f"command has no --json-out argument: {command!r}")


def test_repo_namespace_resolves_phase_20_derived_inside_nested_checkout(tmp_path: Path) -> None:
    checkout = _nested_checkout(tmp_path)
    ledger = checkout / DERIVED_LEDGER_REF
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"features": []}\n', encoding="utf-8")

    resolved = resolve_declared_reference(
        DERIVED_LEDGER_REF,
        namespace="repo",
        checkout_root=checkout,
    )

    assert resolved == ledger


def test_run_lane_resolver_keeps_phase_20_derived_inside_nested_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _nested_checkout(tmp_path)
    monkeypatch.setattr(run_lane, "ROOT", checkout)

    assert run_lane._resolve_repo_path(DERIVED_LEDGER_REF) == checkout / DERIVED_LEDGER_REF


@pytest.mark.parametrize(
    "workspace_root",
    [
        pytest.param(None, id="unset"),
        pytest.param("workspace", id="relative"),
        pytest.param("missing-workspace", id="nonexistent"),
    ],
)
def test_workspace_evidence_fails_closed_without_valid_absolute_workspace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    workspace_root: str | None,
) -> None:
    checkout = _nested_checkout(tmp_path)
    (tmp_path / "workspace" / "docs_tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    if workspace_root is None:
        monkeypatch.delenv("BB_WORKSPACE_ROOT", raising=False)
    elif workspace_root == "workspace":
        monkeypatch.setenv("BB_WORKSPACE_ROOT", workspace_root)
    else:
        monkeypatch.setenv("BB_WORKSPACE_ROOT", str(tmp_path / workspace_root))

    with pytest.raises(ReferenceResolutionError):
        resolve_declared_reference(
            DERIVED_LEDGER_REF,
            namespace="workspace_evidence",
            checkout_root=checkout,
        )


def test_workspace_evidence_rejects_symlink_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _nested_checkout(tmp_path)
    workspace = tmp_path / "evidence-workspace"
    phase_dir = workspace / "docs_tmp" / "phase_20"
    outside = tmp_path / "outside"
    phase_dir.mkdir(parents=True)
    outside.mkdir()
    escaped_ledger = outside / "BB_ER_FEATURE_LEDGER.json"
    escaped_ledger.write_text('{"features": []}\n', encoding="utf-8")
    (phase_dir / "derived").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("BB_WORKSPACE_ROOT", str(workspace))

    with pytest.raises(ReferenceResolutionError):
        resolve_declared_reference(
            DERIVED_LEDGER_REF,
            namespace="workspace_evidence",
            checkout_root=checkout,
        )


def test_declared_absolute_references_are_rejected_by_both_resolvers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _nested_checkout(tmp_path)
    workspace = tmp_path / "evidence-workspace"
    absolute_reference = workspace / DERIVED_LEDGER_REF
    absolute_reference.parent.mkdir(parents=True)
    absolute_reference.write_text('{"features": []}\n', encoding="utf-8")
    monkeypatch.setenv("BB_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setattr(run_lane, "ROOT", checkout)

    with pytest.raises(ReferenceResolutionError):
        resolve_declared_reference(
            absolute_reference,
            namespace="repo",
            checkout_root=checkout,
        )
    with pytest.raises(ReferenceResolutionError):
        resolve_declared_reference(
            absolute_reference,
            namespace="workspace_evidence",
            checkout_root=checkout,
        )
    with pytest.raises(ReferenceResolutionError):
        run_lane._resolve_repo_path(str(absolute_reference))


def test_claim_only_run_preserves_tracked_canonical_ledger_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _nested_checkout(tmp_path)
    canonical_ledger = checkout / DERIVED_LEDGER_REF
    canonical_ledger.parent.mkdir(parents=True)
    original_bytes = b'{"features": []}\n'
    canonical_ledger.write_bytes(original_bytes)

    lane_id = "f5_am19_claim_immutability"
    lane_def_dir = tmp_path / "lane_defs"
    inventory_path = tmp_path / "inventory.json"
    scratch = tmp_path / "scratch"
    _write_claim_lane(lane_def_dir, lane_id, DERIVED_LEDGER_REF)
    inventory_path.write_text('{"lanes": []}\n', encoding="utf-8")
    monkeypatch.setattr(run_lane, "ROOT", checkout)

    def run_claim(command: list[str], **_: object) -> SimpleNamespace:
        generated = _json_output_path(command)
        generated.parent.mkdir(parents=True, exist_ok=True)
        generated.write_text('{"features": [], "gate_errors": []}\n', encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(run_lane.subprocess, "run", run_claim)

    result = run_lane.run_lane(
        lane_id,
        stage="claim",
        out_dir=scratch,
        lane_def_dir=lane_def_dir,
        inventory_path=inventory_path,
    )

    assert result["ok"] is True
    assert canonical_ledger.read_bytes() == original_bytes
    assert (scratch / DERIVED_LEDGER_REF).read_bytes() == original_bytes
