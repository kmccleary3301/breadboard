from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts.e4_parity import run_lane
from scripts.e4_parity.path_refs import ReferenceResolutionError, resolve_declared_reference
from scripts.e4_parity.validate_atomic_feature_ledger import collect_atomic_feature_ledger_errors


DERIVED_LEDGER_REF = "docs_tmp/phase_20/derived/BB_ER_FEATURE_LEDGER.json"
CANONICAL_LEDGER_REF = (
    "config/e4_lanes/evidence_inputs/oh_my_pi_p6_6_atomic_feature_ledger.v1.json"
)
ROOT = Path(__file__).resolve().parents[2]
SOURCE_LINE_RANGE = re.compile(r":\d+(?:-\d+)?$")


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


def test_claim_only_run_preserves_repo_local_derived_ledger_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = _nested_checkout(tmp_path)
    derived_ledger = checkout / DERIVED_LEDGER_REF
    derived_ledger.parent.mkdir(parents=True)
    original_bytes = b'{"features": []}\n'
    derived_ledger.write_bytes(original_bytes)

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
    assert derived_ledger.read_bytes() == original_bytes
    assert (scratch / DERIVED_LEDGER_REF).read_bytes() == original_bytes


def _tracked_checkout_files() -> frozenset[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return frozenset(
        (ROOT / reference).resolve()
        for reference in result.stdout.split("\0")
        if reference
    )


def _row_digest(row_id: str, row: object) -> str:
    preimage = json.dumps(
        {"row_id": row_id, "row": row},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(preimage).hexdigest()


def _reference_resolution_error(
    reference: str,
    *,
    tracked_files: frozenset[Path],
    freeze_rows: object,
) -> str | None:
    kind, separator, payload = reference.partition(":")
    if not separator:
        return "reference has no kind prefix"
    if kind == "source_index":
        return "source_index is not backed by a declared tracked source index"

    if kind == "freeze":
        parts = payload.rsplit("#", 2)
        if len(parts) != 3 or not parts[2].startswith("sha256:"):
            return "freeze reference must identify a row and its sha256 digest"
        path_ref, row_id, expected_digest = parts
    else:
        path_ref, hash_separator, digest = payload.rpartition("#sha256:")
        if not hash_separator:
            path_ref = payload
            expected_digest = None
        else:
            expected_digest = f"sha256:{digest}"
        if kind == "source":
            path_ref = SOURCE_LINE_RANGE.sub("", path_ref)

    candidate = (ROOT / path_ref).resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError:
        return f"path escapes checkout: {path_ref}"
    if candidate not in tracked_files:
        return f"path is not tracked: {path_ref}"
    if not candidate.is_file():
        return f"tracked path is not a file: {path_ref}"

    if kind == "freeze":
        if not isinstance(freeze_rows, dict) or row_id not in freeze_rows:
            return f"freeze row does not exist: {row_id}"
        actual_digest = _row_digest(row_id, freeze_rows[row_id])
    elif expected_digest is not None:
        actual_digest = "sha256:" + hashlib.sha256(candidate.read_bytes()).hexdigest()
    else:
        return None

    if actual_digest != expected_digest:
        return (
            f"digest mismatch for {path_ref}: expected {expected_digest}, "
            f"got {actual_digest}"
        )
    return None


def test_canonical_f5_ledger_has_complete_valid_reference_disposition() -> None:
    ledger = json.loads((ROOT / CANONICAL_LEDGER_REF).read_text(encoding="utf-8"))
    rows = ledger["rows"]
    assert len(rows) == ledger["row_count"] == 98
    assert ledger["source_index_ref"] is None

    validation_errors = [
        f"{row['feature_id']}: {error}"
        for row in rows
        for error in collect_atomic_feature_ledger_errors(row)
    ]
    assert validation_errors == []

    tombstones: dict[tuple[str, str, str], dict[str, object]] = {}
    disposition_errors: list[str] = []
    for tombstone in ledger["reference_tombstones"]:
        key = (
            str(tombstone.get("feature_id")),
            str(tombstone.get("field")),
            str(tombstone.get("reference")),
        )
        if key in tombstones:
            disposition_errors.append(f"duplicate tombstone: {key!r}")
        tombstones[key] = tombstone
        if tombstone.get("disposition") != "historical_provenance_only":
            disposition_errors.append(f"invalid tombstone disposition: {key!r}")
        reason = tombstone.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            disposition_errors.append(f"tombstone has no reason: {key!r}")

    tracked_files = _tracked_checkout_files()
    freeze_manifest = yaml.safe_load(
        (ROOT / "config/e4_target_freeze_manifest.yaml").read_text(encoding="utf-8")
    )
    freeze_rows = freeze_manifest["e4_configs"]
    consumed_tombstones: set[tuple[str, str, str]] = set()
    resolved_count = 0
    tombstoned_count = 0

    for row in rows:
        feature_id = row["feature_id"]
        for field in ("source_refs", "fixture_refs"):
            for reference in row[field]:
                resolution_error = _reference_resolution_error(
                    reference,
                    tracked_files=tracked_files,
                    freeze_rows=freeze_rows,
                )
                key = (feature_id, field, reference)
                if resolution_error is None:
                    resolved_count += 1
                    continue
                tombstone = tombstones.get(key)
                if tombstone is None:
                    disposition_errors.append(
                        f"{feature_id}.{field} unresolved without exact tombstone: "
                        f"{reference!r} ({resolution_error})"
                    )
                    continue
                consumed_tombstones.add(key)
                tombstoned_count += 1

    orphan_tombstones = sorted(set(tombstones) - consumed_tombstones)
    if orphan_tombstones:
        disposition_errors.append(f"orphan tombstones: {orphan_tombstones!r}")

    summary = ledger["reference_resolution"]
    if resolved_count != summary["resolved_reference_count"]:
        disposition_errors.append(
            "resolved reference count mismatch: "
            f"metadata={summary['resolved_reference_count']}, observed={resolved_count}"
        )
    if tombstoned_count != summary["tombstone_count"]:
        disposition_errors.append(
            "tombstone count mismatch: "
            f"metadata={summary['tombstone_count']}, observed={tombstoned_count}"
        )

    assert disposition_errors == []
