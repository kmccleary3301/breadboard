from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from breadboard.rl.harness import contracts as c
from breadboard.rl.harness.composition import load_production_composition
from breadboard.rl.harness.qualification import (
    materialize_production_composition_fixture,
)
from tests.rl.harness.test_production_composition_fixture_generator import (
    production_source_occurrences,
)
from tests.rl.harness.test_production_composition_public_lifecycle import (
    _policy_https_server,
)

ROOT = Path(__file__).parents[3]
CATALOG = (
    ROOT
    / "tests"
    / "fixtures"
    / "rl"
    / "harness"
    / "wp11"
    / "config_native_catalog.json"
)
HISTORICAL_UNKNOWN = "generated-zeta-unknown"


def test_generated_unknown_name_is_selected_and_drives_public_v2_lifecycle(
    tmp_path: Path,
) -> None:
    catalog = json.loads(CATALOG.read_bytes())
    historical = next(
        item for item in catalog["entries"] if item["name"] == HISTORICAL_UNKNOWN
    )
    assert historical["disposition"] == "v2_only"
    assert production_source_occurrences(HISTORICAL_UNKNOWN) == ()

    fixture = materialize_production_composition_fixture(tmp_path)
    assert production_source_occurrences(fixture.generated_candidate_name) == ()
    composition = load_production_composition(
        str(fixture.composition_ref_path),
        fixture.secret_files,
    )
    request = c.ResolveEpisodeRequest.model_validate(fixture.create_body["resolution"])
    resolved = composition.authority_graph.config_runtime.resolve_episode(request)
    selection_record = c.SelectionRecord.model_validate_json(
        composition.authority_graph.store.load(
            resolved.selection_record_ref.sha256,
            kind=c.ArtifactKind.SELECTION_RECORD,
            max_bytes=4 * 1024 * 1024,
        )
    )
    assert selection_record.selected_candidate_id == fixture.generated_candidate_name

    headers = {"Authorization": f"Bearer {fixture.api_bearer}"}
    episode_id = request.episode_id
    policy_server = (
        _policy_https_server(fixture) if sys.platform.startswith("linux") else None
    )
    try:
        if policy_server is None:
            with TestClient(composition.app) as client:
                rejected = client.post(
                    "/v2/episodes",
                    json=dict(fixture.create_body),
                    headers=headers,
                )
            assert rejected.status_code == 503
            assert rejected.json()["code"] == "runtime_unsupported"
            replayed = composition.authority_graph.config_runtime.resolve_episode(
                request
            )
            assert (
                replayed.effective_plan.canonical_digest()
                == resolved.effective_plan.canonical_digest()
            )
            assert (
                replayed.selection_commit.binding == resolved.selection_commit.binding
            )
            assert (
                replayed.selection_commit.binding_ref
                == resolved.selection_commit.binding_ref
            )
            assert replayed.final_receipt_digest == resolved.final_receipt_digest
            return

        with policy_server:
            with TestClient(composition.app) as client:
                created = client.post(
                    "/v2/episodes",
                    json=dict(fixture.create_body),
                    headers=headers,
                )
                assert created.status_code == 200, created.text
                create_payload = created.json()
                assert create_payload[
                    "selection_record_ref"
                ] == resolved.selection_record_ref.model_dump(mode="json")

                ran = client.post(
                    f"/v2/episodes/{episode_id}:run",
                    json={
                        "schema_version": "bb.rl.episode.v2",
                        "create_fingerprint": create_payload["create_fingerprint"],
                        "task_input": {
                            "prompt": "execute selected generated candidate"
                        },
                        "context": {"acceptance": "unknown-name-cutover"},
                    },
                    headers=headers,
                )
                assert ran.status_code == 200, ran.text
                assert ran.json()["episode_id"] == episode_id

                closed = client.delete(
                    f"/v2/episodes/{episode_id}",
                    headers=headers,
                )
                assert closed.status_code == 200, closed.text
                assert closed.json()["state"] == "closed"
                assert closed.json()["cleanup_disposition"] in {
                    "released",
                    "already_released",
                }
    finally:
        asyncio.run(composition.close())
