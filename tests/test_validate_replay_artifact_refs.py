from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_module(module_name: str, rel_path: str):
    module_path = _repo_root() / rel_path
    scripts_dir = str((_repo_root() / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_replay_artifact_ref_validator_rejects_ambiguous_payload(tmp_path: Path):
    module = _load_module("validate_replay_artifact_refs", "scripts/validate_replay_artifact_refs.py")
    fixture = tmp_path / "ambiguous.jsonl"
    fixture.write_text(
        '\n'.join(
            [
                json.dumps(
                    {
                        "type": "tool_result",
                        "payload": {
                            "display": {
                                "detail": ["inline"],
                                "detail_artifact": {
                                    "schema_version": "artifact_ref_v1",
                                    "id": "artifact-1",
                                    "kind": "tool_result",
                                    "mime": "text/plain",
                                    "size_bytes": 100,
                                    "sha256": "abc",
                                    "storage": "workspace_file",
                                    "path": "docs_tmp/tui_tool_artifacts/tool_result/artifact-1.txt",
                                },
                            }
                        },
                    }
                )
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    errors = module.validate_jsonl(fixture)
    assert errors
    assert "ambiguous payload" in errors[0]


def test_replay_artifact_ref_validator_accepts_valid_ref_only_payload(tmp_path: Path):
    module = _load_module("validate_replay_artifact_refs_ok", "scripts/validate_replay_artifact_refs.py")
    fixture = tmp_path / "ok.jsonl"
    fixture.write_text(
        '\n'.join(
            [
                json.dumps(
                    {
                        "type": "tool_result",
                        "payload": {
                            "display": {
                                "detail_artifact": {
                                    "schema_version": "artifact_ref_v1",
                                    "id": "artifact-2",
                                    "kind": "tool_output",
                                    "mime": "text/plain",
                                    "size_bytes": 1200,
                                    "sha256": "def",
                                    "storage": "workspace_file",
                                    "path": "docs_tmp/tui_tool_artifacts/tool_output/artifact-2.txt",
                                    "preview": {"lines": ["line-1"], "omitted_lines": 10},
                                }
                            }
                        },
                    }
                )
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    errors = module.validate_jsonl(fixture)
    assert errors == []
