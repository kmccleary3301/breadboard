from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import yaml


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_module(module_name: str):
    module_path = _repo_root() / "scripts" / "research" / "parity" / "audit_e4_target_drift.py"
    scripts_dir = str((_repo_root() / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _write_manifest(path: Path, *, repo: str, commit: str) -> Path:
    payload = {
        "schema_version": "e4_target_freeze_manifest_v1",
        "manifest_updated_utc": "2026-03-04T00:00:00Z",
        "e4_configs": {
            "sample_e4_config": {
                "harness": {
                    "upstream_repo": repo,
                    "upstream_commit": commit,
                }
            }
        },
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _write_snapshot(path: Path, *, repo: str, commit: str) -> Path:
    payload = {
        "snapshot_id": "e4_refsnapshot_test",
        "entries": {
            "sample": {
                "repo_url": repo,
                "commit": commit,
            }
        },
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def test_snapshot_mode_uses_snapshot_heads_without_remote_lookup(tmp_path: Path) -> None:
    module = _load_module("audit_e4_target_drift_snapshot")
    repo = "https://example.com/codex.git"
    report = module._build_report(  # noqa: SLF001
        e4_configs={
            "sample": {
                "harness": {
                    "upstream_repo": repo,
                    "upstream_commit": "abc123",
                }
            }
        },
        snapshot_heads={repo: "abc123"},
        remote_head_lookup=lambda _repo: (_ for _ in ()).throw(AssertionError("must not call remote lookup")),
    )
    assert report["comparison_source"] == "snapshot_json"
    assert report["drift_count"] == 0
    assert report["aligned_count"] == 1
    assert report["error_count"] == 0


def test_main_snapshot_mode_writes_report_and_exits_clean(tmp_path: Path, monkeypatch) -> None:
    module = _load_module("audit_e4_target_drift_main_snapshot")
    repo = "https://example.com/opencode.git"
    manifest = _write_manifest(tmp_path / "manifest.yaml", repo=repo, commit="def456")
    snapshot = _write_snapshot(tmp_path / "snapshot.json", repo=repo, commit="def456")
    out_json = tmp_path / "report.json"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit_e4_target_drift.py",
            "--repo-root",
            str(tmp_path),
            "--manifest",
            str(manifest),
            "--snapshot-json",
            str(snapshot),
            "--json-out",
            str(out_json),
        ],
    )
    rc = module.main()
    assert rc == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["comparison_source"] == "snapshot_json"
    assert payload["drift_count"] == 0
    assert payload["aligned_count"] == 1


def test_build_report_live_mode_uses_cached_remote_lookup() -> None:
    module = _load_module("audit_e4_target_drift_live_cache")
    repo = "https://example.com/claude.git"
    calls = {"count": 0}

    def _lookup(_repo: str) -> str:
        calls["count"] += 1
        return "123abc"

    report = module._build_report(  # noqa: SLF001
        e4_configs={
            "row_a": {"harness": {"upstream_repo": repo, "upstream_commit": "123abc"}},
            "row_b": {"harness": {"upstream_repo": repo, "upstream_commit": "999999"}},
        },
        remote_head_lookup=_lookup,
    )
    assert report["comparison_source"] == "live_remote_head"
    assert calls["count"] == 1
    assert report["aligned_count"] == 1
    assert report["drift_count"] == 1
    assert report["error_count"] == 0
