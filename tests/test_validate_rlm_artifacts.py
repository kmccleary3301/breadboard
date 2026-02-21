from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "validate_rlm_artifacts.py"
    spec = importlib.util.spec_from_file_location("validate_rlm_artifacts", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load validate_rlm_artifacts module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validate_rlm_artifacts_happy_path(tmp_path: Path) -> None:
    mod = _load_module()
    run_dir = tmp_path / "run"
    meta = run_dir / "meta"
    meta.mkdir(parents=True, exist_ok=True)

    (meta / "rlm_subcalls.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event": "llm.query",
                        "call_id": "abc",
                        "branch_id": "root",
                        "route_id": "openrouter/openai/gpt-5-nano",
                        "resolved_model": "openai/gpt-5-nano",
                        "prompt_hash": "x",
                        "consumed_blobs": [],
                        "created_blobs": [],
                    }
                )
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (meta / "rlm_blobs_manifest.jsonl").write_text(
        json.dumps(
            {
                "event": "blob.put",
                "blob_id": "sha256:abc",
                "created_blobs": ["sha256:abc"],
                "consumed_blobs": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (meta / "rlm_batch_subcalls.jsonl").write_text(
        json.dumps(
            {
                "event": "llm.batch_query",
                "batch_id": "batch-1",
                "request_index": 0,
                "call_id": "batch-1:0",
                "branch_id": "root",
                "status": "completed",
                "consumed_blobs": [],
                "created_blobs": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (meta / "rlm_batch_summary.json").write_text(
        json.dumps(
            {
                "batch_count": 1,
                "batch_item_count": 1,
                "batch_failures": 0,
                "status_counts": {"completed": 1},
            }
        ),
        encoding="utf-8",
    )
    (meta / "rlm_branches.json").write_text(
        json.dumps({"schema_version": "rlm_branch_ledger_v1", "branches": {}, "events": []}),
        encoding="utf-8",
    )

    payload = mod.validate(run_dir)
    assert payload["ok"] is True
    assert payload["subcalls"] == 1
    assert payload["batch_subcalls"] == 1
    assert payload["blobs"] == 1
    assert payload["has_branch_ledger"] is True
    assert payload["has_batch_summary"] is True


def test_validate_rlm_artifacts_rejects_missing_required_keys(tmp_path: Path) -> None:
    mod = _load_module()
    run_dir = tmp_path / "run"
    meta = run_dir / "meta"
    meta.mkdir(parents=True, exist_ok=True)

    (meta / "rlm_subcalls.jsonl").write_text(json.dumps({"event": "llm.query"}) + "\n", encoding="utf-8")
    (meta / "rlm_blobs_manifest.jsonl").write_text(json.dumps({"event": "blob.put"}) + "\n", encoding="utf-8")

    try:
        mod.validate(run_dir)
    except ValueError as exc:
        assert "missing required key" in str(exc)
    else:
        raise AssertionError("validate() should fail when required keys are missing")


def test_validate_rlm_artifacts_rejects_missing_batch_required_keys(tmp_path: Path) -> None:
    mod = _load_module()
    run_dir = tmp_path / "run"
    meta = run_dir / "meta"
    meta.mkdir(parents=True, exist_ok=True)

    (meta / "rlm_subcalls.jsonl").write_text("", encoding="utf-8")
    (meta / "rlm_blobs_manifest.jsonl").write_text("", encoding="utf-8")
    (meta / "rlm_batch_subcalls.jsonl").write_text(json.dumps({"event": "llm.batch_query"}) + "\n", encoding="utf-8")

    try:
        mod.validate(run_dir)
    except ValueError as exc:
        assert "rlm_batch_subcalls" in str(exc)
    else:
        raise AssertionError("validate() should fail when batch required keys are missing")
