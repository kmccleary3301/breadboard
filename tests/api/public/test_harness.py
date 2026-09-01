from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from breadboard_engine.api.cli_bridge.app import create_app
from breadboard_engine.api.public import models as public_models
from breadboard_engine.api.public.models import scrub_public
from breadboard.product.cli import harness as cli_harness
from breadboard.product.operations import harness as harness_operations


def _client(monkeypatch, workspace: Path) -> TestClient:
    monkeypatch.setenv("BREADBOARD_PUBLIC_WORKSPACE", str(workspace))
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "0")
    monkeypatch.setenv("BREADBOARD_ENABLE_PUBLIC_API", "1")
    monkeypatch.setenv("RAY_SCE_LOCAL_MODE", "1")
    return TestClient(create_app(include_atp_routes=False))


def _workspace_files(workspace: Path) -> dict[str, bytes]:
    return {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def test_harness_family_delegates_to_product_operations(
    monkeypatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path)
    created = client.post("/v1/harnesses", json={})
    assert created.status_code == 200
    assert created.json()["data"]["path"] == "daily_driver.v1.yaml"
    assert created.json()["data"]["model_roles_path"] == "daily_driver_roles.v1.json"
    assert (tmp_path / "daily_driver_roles.v1.json").is_file()
    replayed = client.post("/v1/harnesses", json={})
    assert (
        replayed.status_code == 200
        and replayed.json()["data"] == created.json()["data"]
    )
    assert (
        cli_harness.init(SimpleNamespace(workspace=tmp_path, out=tmp_path)).as_dict()
        == created.json()
    )
    fetched = client.get("/v1/harnesses/daily_driver.v1.yaml")
    assert fetched.status_code == 200
    fetched_result = fetched.json()
    assert {
        key: fetched_result[key]
        for key in (
            "schema_version",
            "ok",
            "status",
            "command",
            "record_refs",
            "hashes",
            "stage_outcomes",
            "warnings",
            "next_actions",
            "error",
            "exit_code",
        )
    } == {
        "schema_version": "bb.cli.result.v1",
        "ok": True,
        "status": "ok",
        "command": ["harness", "get"],
        "record_refs": ["daily_driver.v1.yaml"],
        "hashes": {},
        "stage_outcomes": [
            {
                "stage": "harness.get",
                "status": "passed",
                "report_ref": None,
                "next_action": None,
            }
        ],
        "warnings": [],
        "next_actions": [],
        "error": None,
        "exit_code": 0,
    }
    assert fetched_result["data"]["path"] == "daily_driver.v1.yaml"
    assert fetched_result["data"]["definition"]["schema_version"] == (
        "bb.harness_definition.v1"
    )
    cli_arguments = SimpleNamespace(
        workspace=tmp_path,
        PATH=tmp_path / "daily_driver.v1.yaml",
    )
    assert cli_harness.get(cli_arguments).as_dict() == fetched_result
    validated = client.post("/v1/harnesses/daily_driver.v1.yaml/validate")
    assert validated.json()["ok"] is True
    assert cli_harness.validate(cli_arguments).as_dict() == validated.json()
    definition = fetched.json()["data"]["definition"]
    definition["modes"][0]["name"] = "review"
    definition["loop"]["sequence"][0]["mode"] = "review"
    updated = client.put(
        "/v1/harnesses/daily_driver.v1.yaml", json={"definition": definition}
    )
    assert (
        updated.status_code == 200
        and updated.json()["stage_outcomes"][0]["stage"] == "harness.update"
    )
    assert (
        client.get("/v1/harnesses/daily_driver.v1.yaml").json()["data"]["definition"][
            "modes"
        ][0]["name"]
        == "review"
    )
    assert (
        cli_harness.update(
            SimpleNamespace(
                workspace=tmp_path,
                PATH=tmp_path / "daily_driver.v1.yaml",
                document=definition,
                source=None,
            )
        ).as_dict()
        == updated.json()
    )
    nested = client.post("/v1/harnesses", json={"directory": "bundles"})
    assert nested.status_code == 200
    invalid_nested_update = client.put(
        "/v1/harnesses/bundles/daily_driver.v1.yaml", json={}
    )
    assert invalid_nested_update.status_code == 422
    assert invalid_nested_update.json()["schema_version"] == "bb.cli.result.v1"
    assert invalid_nested_update.json()["error"]["schema_version"] == "bb.problem.v1"
    outside = tmp_path.parent / f"{tmp_path.name}-outside-prompt.md"
    outside.write_text("outside-secret-value")
    definition["prompts"]["packs"]["base"]["system"] = "prompts/daily_driver_system.md"
    definition["extends"] = f"../{outside.name}"
    escaped = client.put(
        "/v1/harnesses/daily_driver.v1.yaml", json={"definition": definition}
    )
    assert (
        escaped.status_code == 422
        and escaped.json()["error"]["error_code"] == "invalid_state"
    )
    assert "outside-secret-value" not in escaped.text
    definition.pop("extends")
    definition["prompts"]["packs"]["base"]["system"] = f"../{outside.name}"
    resource_escaped = client.put(
        "/v1/harnesses/daily_driver.v1.yaml",
        json={"definition": definition},
    )
    assert resource_escaped.status_code == 404
    assert resource_escaped.json()["error"]["error_code"] == "path_unavailable"
    assert "outside-secret-value" not in resource_escaped.text
    assert (
        client.get("/v1/harnesses/daily_driver.v1.yaml").json()["data"]["definition"][
            "modes"
        ][0]["name"]
        == "review"
    )
    explained = client.post("/v1/harnesses/daily_driver.v1.yaml/explain")
    assert explained.json()["ok"] is True
    assert cli_harness.explain(cli_arguments).as_dict() == explained.json()
    locked = client.post("/v1/harnesses/daily_driver.v1.yaml/lock")
    assert locked.status_code == 200
    lock_id = locked.json()["data"]["path"]
    cli_locked = cli_harness.lock(
        SimpleNamespace(
            workspace=tmp_path,
            PATH=tmp_path / "daily_driver.v1.yaml",
            out=None,
            check=False,
        )
    ).as_dict()
    assert scrub_public(cli_locked, tmp_path) == locked.json()
    fetched_lock = client.get(f"/v1/harness-locks/{lock_id}")
    assert fetched_lock.json()["ok"] is True
    lock_arguments = SimpleNamespace(
        workspace=tmp_path,
        PATH=tmp_path / lock_id,
    )
    assert cli_harness.get_lock(lock_arguments).as_dict() == fetched_lock.json()
    (tmp_path / "unrelated.yaml").write_text("name: not-a-harness\n")
    listed = client.get("/v1/harnesses")
    assert listed.json()["data"]["harnesses"] == [
        "bundles/daily_driver.v1.yaml",
        "daily_driver.v1.yaml",
    ]
    assert (
        cli_harness.list_harnesses(
            SimpleNamespace(workspace=tmp_path, directory=None)
        ).as_dict()
        == listed.json()
    )


def test_harness_update_preserves_definition_when_replacement_is_invalid(
    monkeypatch,
    tmp_path: Path,
) -> None:
    client = _client(monkeypatch, tmp_path)
    assert client.post("/v1/harnesses", json={}).status_code == 200
    harness_path = tmp_path / "daily_driver.v1.yaml"
    before = harness_path.read_bytes()

    response = client.put(
        "/v1/harnesses/daily_driver.v1.yaml",
        json={"definition": {}},
    )

    assert response.status_code == 422
    assert response.json()["ok"] is False
    assert harness_path.read_bytes() == before
    assert not tuple(tmp_path.glob(f".{harness_path.name}.*.tmp"))


def test_harness_lock_rolls_back_pair_when_metadata_commit_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    client = _client(monkeypatch, tmp_path)
    assert client.post("/v1/harnesses", json={}).status_code == 200
    harness_path = tmp_path / "daily_driver.v1.yaml"
    assert client.post("/v1/harnesses/daily_driver.v1.yaml/lock").status_code == 200
    target = harness_operations.lock_path(harness_path)
    metadata = harness_operations.lock_metadata_path(target)
    before = (target.read_bytes(), metadata.read_bytes())
    (tmp_path / "prompts" / "daily_driver_system.md").write_text(
        "Changed prompt content.\n",
        encoding="utf-8",
    )
    replace = harness_operations.os.replace

    def fail_metadata_commit(source: Path, destination: Path) -> None:
        if Path(destination) == metadata:
            raise OSError("injected-secret-must-not-leak")
        replace(source, destination)

    monkeypatch.setattr(harness_operations.os, "replace", fail_metadata_commit)

    response = client.post("/v1/harnesses/daily_driver.v1.yaml/lock")

    assert response.status_code == 500
    assert response.json()["ok"] is False
    assert "injected-secret-must-not-leak" not in response.text
    assert str(tmp_path) not in response.text
    assert (target.read_bytes(), metadata.read_bytes()) == before
    assert not tuple(tmp_path.glob(".*.tmp"))


def test_public_api_cannot_write_maintainer_evidence_trees(
    monkeypatch, tmp_path: Path
) -> None:

    repository_evidence = Path(__file__).resolve().parents[3] / "docs_tmp"
    assert repository_evidence in public_models._MAINTAINER_ROOTS
    maintainer_tree = tmp_path / "docs_tmp"
    maintainer_tree.mkdir()
    monkeypatch.setattr(
        public_models,
        "_MAINTAINER_ROOTS",
        (*public_models._MAINTAINER_ROOTS, maintainer_tree),
    )
    marker = maintainer_tree / "daily_driver.v1.yaml"
    existed = marker.exists()
    client = _client(monkeypatch, maintainer_tree)
    with pytest.raises(PermissionError, match="maintainer evidence trees"):
        public_models.public_workspace()
    response = client.post("/v1/harnesses", json={})
    assert response.status_code == 404
    assert response.json()["error"]["error_code"] == "path_unavailable"
    assert marker.exists() is existed
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = _client(monkeypatch, workspace)
    assert client.post("/v1/harnesses", json={}).status_code == 200
    outside = tmp_path / "outside-lock.json"
    (workspace / "daily_driver.v1.lock.json").symlink_to(outside)
    blocked = client.post("/v1/harnesses/daily_driver.v1.yaml/lock")
    assert blocked.status_code == 422 and not outside.exists()


def test_harness_read_routes_do_not_mutate_workspace(
    monkeypatch,
    tmp_path: Path,
) -> None:
    client = _client(monkeypatch, tmp_path)
    assert client.post("/v1/harnesses", json={}).status_code == 200
    locked = client.post("/v1/harnesses/daily_driver.v1.yaml/lock")
    assert locked.status_code == 200
    lock_id = locked.json()["data"]["path"]
    before = _workspace_files(tmp_path)

    responses = (
        client.get("/v1/harnesses"),
        client.get("/v1/harnesses/daily_driver.v1.yaml"),
        client.post("/v1/harnesses/daily_driver.v1.yaml/validate"),
        client.post("/v1/harnesses/daily_driver.v1.yaml/explain"),
        client.get(f"/v1/harness-locks/{lock_id}"),
    )

    assert all(response.status_code == 200 for response in responses)
    assert _workspace_files(tmp_path) == before
