#!/usr/bin/env python3
"""Run the exact F6 provider differential gate and optionally write evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence

from conformance.provider_differential.artifact_rows import (
    ArtifactBundle,
    build_artifact_bundle,
    observe_artifact_rows,
)
from conformance.provider_differential.auth_role_rows import (
    AUTH_ROLE_ROW_IDS,
    observe_auth_role_row,
)
from conformance.provider_differential.contracts import (
    ALL_ROW_IDS,
    ARTIFACT_ROW_IDS,
    AUTH_ROLE_ROW_IDS as CONTRACT_AUTH_ROLE_ROW_IDS,
    ORACLE_IDENTITY,
    PROVIDER_ROW_IDS,
    canonical_json,
    sha256_file,
    sha256_json,
    validate_manifest,
)
from conformance.provider_differential.gate import (
    DifferentialError,
    Evaluation,
    evaluate,
    load_matrix,
    load_oracle,
)
from conformance.provider_differential.provider_rows import observe_provider_row

ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "conformance/provider_differential/matrix.v1.json"
ORACLE_FIXTURE = ROOT / "conformance/provider_differential/oracle/omp-v18.0.1.json"
ORACLE_RUNNER = ROOT / "scripts/quality/capture_f6_omp_oracle.ts"
REFERENCE_TESTS = (
    "packages/ai/test/auth-storage-codex-selection.test.ts",
    "packages/ai/test/auth-storage-oauth-account-select.test.ts",
    "packages/coding-agent/test/model-resolver.test.ts",
    "packages/coding-agent/test/issue-985-subagent-auth-fallback.test.ts",
)
EXPECTED_REFERENCE_TEST_PASSES = 237
_SAFE_ORACLE_ENV = frozenset(
    {"PATH", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "TZ"}
)


class GateError(RuntimeError):
    """The executable F6 gate cannot produce claimable evidence."""


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
    timeout: float = 600,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        env=dict(environment) if environment is not None else None,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise GateError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            + completed.stdout[-8_000:]
        )
    return completed


def _safe_environment() -> dict[str, str]:
    environment = {
        key: os.environ[key] for key in _SAFE_ORACLE_ENV if key in os.environ
    }
    environment.update(
        {
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "127.0.0.1,localhost,::1",
            "BUN_TELEMETRY_DISABLE": "1",
        }
    )
    return environment


def _git_value(root: Path, *arguments: str) -> str:
    return _run(("git", *arguments), cwd=root, timeout=30).stdout.strip()


def _verify_candidate(root: Path, commit: str, tree: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(
        r"[0-9a-f]{40}", tree
    ):
        raise GateError("expected candidate commit/tree must be lowercase 40-hex IDs")
    if _git_value(root, "rev-parse", "HEAD") != commit:
        raise GateError("candidate HEAD differs from --expected-commit")
    if _git_value(root, "rev-parse", "HEAD^{tree}") != tree:
        raise GateError("candidate tree differs from --expected-tree")
    _run(("git", "diff", "--quiet"), cwd=root, timeout=30)
    _run(("git", "diff", "--cached", "--quiet"), cwd=root, timeout=30)


def _verify_oracle_fixture(oracle_root: Path) -> dict[str, Any]:
    bun = shutil.which("bun")
    if bun is None:
        raise GateError("bun is required for the exact F1 oracle check")
    completed = _run(
        (
            bun,
            str(ORACLE_RUNNER),
            "--oracle-root",
            str(oracle_root),
            "--check",
            "--fixture",
            str(ORACLE_FIXTURE),
        ),
        cwd=ROOT,
        environment=_safe_environment(),
        timeout=180,
    )
    if completed.stdout.strip() != "ok: exact 42 F6 oracle rows":
        raise GateError("oracle checker returned an unexpected success result")
    return dict(load_oracle(ORACLE_FIXTURE, ORACLE_RUNNER))


def _run_reference_tests(oracle_root: Path) -> dict[str, Any]:
    bun = shutil.which("bun")
    if bun is None:
        raise GateError("bun is required for exact F1 reference tests")
    if _git_value(oracle_root, "rev-parse", "HEAD") != ORACLE_IDENTITY["commit"]:
        raise GateError("reference-test checkout commit differs from F1")
    if _git_value(oracle_root, "rev-parse", "HEAD^{tree}") != ORACLE_IDENTITY["tree"]:
        raise GateError("reference-test checkout tree differs from F1")
    if _git_value(oracle_root, "status", "--porcelain"):
        raise GateError("reference-test checkout has tracked or untracked changes")
    command = (bun, "test", *REFERENCE_TESTS)
    completed = _run(
        command,
        cwd=oracle_root,
        environment=_safe_environment(),
        timeout=300,
    )
    pass_match = re.search(r"(?m)^\s*(\d+) pass\s*$", completed.stdout)
    fail_match = re.search(r"(?m)^\s*(\d+) fail\s*$", completed.stdout)
    passes = int(pass_match.group(1)) if pass_match else -1
    failures = int(fail_match.group(1)) if fail_match else -1
    if passes != EXPECTED_REFERENCE_TEST_PASSES or failures != 0:
        raise GateError(
            "exact F1 reference-test count mismatch "
            f"(passes={passes}, failures={failures})"
        )
    return {
        "oracle_identity": dict(ORACLE_IDENTITY),
        "command": ["bun", "test", *REFERENCE_TESTS],
        "files": list(REFERENCE_TESTS),
        "passes": passes,
        "failures": failures,
        "output": completed.stdout,
    }


def _source_observations(oracle: Mapping[str, Any]) -> list[dict[str, Any]]:
    oracle_by_id = {str(row["id"]): row for row in oracle["rows"]}
    rows = [
        observe_provider_row(row_id, oracle_by_id[row_id]["input"])
        for row_id in PROVIDER_ROW_IDS
    ]
    with tempfile.TemporaryDirectory(prefix="bb-f6-auth-role-") as root:
        base = Path(root).resolve()
        rows.extend(
            observe_auth_role_row(
                row_id,
                root=base / row_id.replace(".", "-"),
            )
            for row_id in AUTH_ROLE_ROW_IDS
        )
    return rows


def _canonical_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


def _relative_record(root: Path, path: Path) -> dict[str, str]:
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    return {"path": relative, "sha256": sha256_file(path)}


def _copy_file(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination


def _write_evidence(
    output: Path,
    *,
    oracle_root: Path,
    matrix: Mapping[str, Any],
    oracle: Mapping[str, Any],
    reference_tests: Mapping[str, Any],
    observations: Sequence[Mapping[str, Any]],
    evaluation: Evaluation,
    bundle: ArtifactBundle,
    commit: str,
    tree: str,
) -> Mapping[str, Any]:
    evidence_root = output.parent.resolve()
    if evidence_root.exists():
        raise GateError("evidence output directory must not already exist")
    evidence_root.mkdir(parents=True)
    matrix_copy = _copy_file(MATRIX, evidence_root / "matrix.v1.json")
    oracle_copy = _copy_file(ORACLE_FIXTURE, evidence_root / "oracle-fixture.json")
    tests_copy = evidence_root / "oracle-reference-tests.json"
    _canonical_write(tests_copy, reference_tests)

    oracle_blob_records: list[dict[str, str]] = []
    source_blobs = oracle["oracle"]["source_blobs"]
    for relative, expected_blob in sorted(source_blobs.items()):
        source = (oracle_root / relative).resolve()
        if _git_value(oracle_root, "rev-parse", f"HEAD:{relative}") != expected_blob:
            raise GateError(f"oracle source blob changed: {relative}")
        destination = _copy_file(
            source,
            evidence_root / "oracle-source" / relative,
        )
        oracle_blob_records.append(_relative_record(evidence_root, destination))

    observation_records: dict[str, dict[str, str]] = {}
    observation_by_id = {str(row["row_id"]): row for row in observations}
    for row_id in ALL_ROW_IDS:
        destination = evidence_root / "observations" / f"{row_id}.json"
        _canonical_write(destination, observation_by_id[row_id])
        observation_records[row_id] = _relative_record(evidence_root, destination)

    wheel_copy = _copy_file(
        bundle.wheel,
        evidence_root / "artifacts" / bundle.wheel.name,
    )
    sdk_copy = _copy_file(
        bundle.sdk_tarball,
        evidence_root / "artifacts" / bundle.sdk_tarball.name,
    )
    sdk_manifest_source = Path(f"{bundle.sdk_tarball}.installed-files.json")
    sdk_manifest_copy = _copy_file(
        sdk_manifest_source,
        evidence_root / "artifacts" / sdk_manifest_source.name,
    )
    artifact_sources = {
        ARTIFACT_ROW_IDS[0]: _relative_record(evidence_root, wheel_copy),
        ARTIFACT_ROW_IDS[1]: _relative_record(evidence_root, sdk_copy),
        ARTIFACT_ROW_IDS[2]: observation_records[ARTIFACT_ROW_IDS[2]],
    }
    common_evidence = [
        _relative_record(evidence_root, matrix_copy),
        _relative_record(evidence_root, oracle_copy),
    ]
    tests_record = _relative_record(evidence_root, tests_copy)
    sdk_manifest_record = _relative_record(evidence_root, sdk_manifest_copy)
    oracle_by_id = {str(row["id"]): row for row in oracle["rows"]}
    matrix_by_id = {str(row["row_id"]): row for row in matrix["rows"]}
    comparison_by_id = {row.row_id: row for row in evaluation.comparisons}
    verified_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    toolchain = (
        f"python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}; "
        f"bun {shutil.which('bun') or 'missing'}; uv {shutil.which('uv') or 'missing'}; "
        "F6 gate v1"
    )
    rows: list[dict[str, Any]] = []
    for row_id in ALL_ROW_IDS:
        matrix_row = matrix_by_id[row_id]
        comparison = comparison_by_id[row_id]
        if comparison.classification not in {"match", "intentional_divergence"}:
            raise GateError(
                f"refusing evidence for {row_id}: {comparison.classification}"
            )
        if row_id in oracle_by_id:
            oracle_row = oracle_by_id[row_id]
            oracle_input_hash = oracle_row["input_sha256"]
            oracle_output_hash = oracle_row["output_sha256"]
        else:
            oracle_input_hash = sha256_json(
                {
                    "matrix_id": matrix["matrix_id"],
                    "row": matrix_row,
                }
            )
            oracle_output_hash = sha256_json(
                {
                    "contract": matrix_row["comparator"],
                    "claim": matrix_row["claim"],
                }
            )
        evidence = [*common_evidence, observation_records[row_id]]
        if row_id in CONTRACT_AUTH_ROLE_ROW_IDS:
            evidence.append(tests_record)
        if row_id == ARTIFACT_ROW_IDS[1]:
            evidence.append(sdk_manifest_record)
        prefix = row_id.split(".", 1)[0]
        manifest_provider = (
            prefix
            if prefix in {"codex", "openai", "anthropic", "openrouter"}
            else "omp"
            if row_id in CONTRACT_AUTH_ROLE_ROW_IDS
            else "breadboard"
        )
        row: dict[str, Any] = {
            "row_id": row_id,
            "subject": matrix_row["subject"],
            "claim": matrix_row["claim"],
            "provider": manifest_provider,
            "seam": matrix_row["seam"],
            "comparator": matrix_row["comparator"],
            "oracle_identity": dict(ORACLE_IDENTITY),
            "oracle_source_blobs": oracle_blob_records,
            "oracle_runner": (
                "scripts/quality/capture_f6_omp_oracle.ts@"
                + str(oracle["runner_sha256"])
            ),
            "oracle_input_sha256": oracle_input_hash,
            "oracle_output_sha256": oracle_output_hash,
            "breadboard_commit": commit,
            "breadboard_tree": tree,
            "evidence": evidence,
            "classification": comparison.classification,
            "verification_toolchain": toolchain,
            "verified_at": verified_at,
        }
        if comparison.classification == "intentional_divergence":
            row["divergence_ref"] = matrix_row["allowed_divergence_ref"]
        if row_id in ARTIFACT_ROW_IDS:
            source_record = artifact_sources[row_id]
            row["artifact_provenance"] = {
                "artifact_id": row_id,
                "source": source_record["path"],
                "sha256": source_record["sha256"],
            }
            row["evidence"].append(source_record)
        rows.append(row)
    manifest: dict[str, Any] = {
        "schema_version": "bb.provider_differential_manifest.v1",
        "manifest_id": f"f6-provider-differential-{commit[:12]}",
        "matrix_id": matrix["matrix_id"],
        "created_at": verified_at,
        "oracle_identity": dict(ORACLE_IDENTITY),
        "breadboard_commit": commit,
        "breadboard_tree": tree,
        "row_count": len(rows),
        "rows": rows,
    }
    validate_manifest(manifest, root=evidence_root)
    _canonical_write(output, manifest)
    validate_manifest(
        json.loads(output.read_text(encoding="utf-8")),
        root=evidence_root,
    )
    return manifest


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--oracle-root", type=Path, required=True)
    parser.add_argument("--artifacts", action="store_true")
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-tree")
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--evidence-out", type=Path)
    args = parser.parse_args(argv)
    artifact_values = (
        args.expected_commit,
        args.expected_tree,
        args.work_root,
        args.evidence_out,
    )
    if args.artifacts and any(value is None for value in artifact_values):
        parser.error(
            "--artifacts requires --expected-commit, --expected-tree, "
            "--work-root, and --evidence-out"
        )
    if not args.artifacts and any(value is not None for value in artifact_values):
        parser.error("artifact/evidence arguments require --artifacts")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        oracle_root = args.oracle_root.resolve()
        matrix = load_matrix(MATRIX)
        oracle = _verify_oracle_fixture(oracle_root)
        reference_tests = _run_reference_tests(oracle_root)
        observations = _source_observations(oracle)
        bundle: ArtifactBundle | None = None
        if args.artifacts:
            _verify_candidate(ROOT, args.expected_commit, args.expected_tree)
            bundle = build_artifact_bundle(
                ROOT,
                args.work_root.resolve(),
                expected_commit=args.expected_commit,
                expected_tree=args.expected_tree,
            )
            observations.extend(observe_artifact_rows(bundle, args.work_root.resolve()))
        evaluation = evaluate(
            matrix,
            oracle,
            observations,
            reference_tests_passed=True,
            include_artifacts=args.artifacts,
        )
        result: dict[str, Any] = {
            "schema_version": "bb.provider_differential_gate_result.v1",
            "row_count": len(evaluation.comparisons),
            "counts": evaluation.counts,
            "claimable": evaluation.claimable,
            "rows": [
                {
                    "row_id": row.row_id,
                    "classification": row.classification,
                    "detail": row.detail,
                }
                for row in evaluation.comparisons
            ],
        }
        if not evaluation.claimable:
            print(canonical_json(result))
            return 1
        if args.evidence_out is not None:
            if bundle is None:
                raise GateError("artifact bundle is unavailable")
            manifest = _write_evidence(
                args.evidence_out.resolve(),
                oracle_root=oracle_root,
                matrix=matrix,
                oracle=oracle,
                reference_tests=reference_tests,
                observations=observations,
                evaluation=evaluation,
                bundle=bundle,
                commit=args.expected_commit,
                tree=args.expected_tree,
            )
            result["evidence_manifest"] = str(args.evidence_out.resolve())
            result["manifest_id"] = manifest["manifest_id"]
        print(canonical_json(result))
        return 0
    except (DifferentialError, GateError, OSError, ValueError) as exc:
        print(f"F6 provider differential gate failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
