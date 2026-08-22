from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

import pytest
from fastapi.testclient import TestClient

from breadboard.rl.harness.api import create_app
from breadboard.rl.harness.contracts import RuntimeClass
from breadboard.rl.harness.evidence import (
    ExportAuthorizationClaimsV2,
    ExportAuthorizationV2,
    SafeFailureFactV2,
)
from breadboard.rl.harness.history import HistoricalV1EpisodeReader
from breadboard.rl.harness.service import (
    EpisodeCleanupDisposition,
    EpisodeLifecycleState,
    EpisodePrimaryDisposition,
    V2CancellationResult,
    V2CloseResult,
    V2CreateResult,
    V2EpisodeConflict,
    V2EpisodeQuarantined,
    V2EpisodeState,
    V2OperationDisposition,
    V2OperationResult,
    V2RunResult,
    V2SandboxPreflightIdentity,
)
from breadboard.rl.state.state_ref import ArtifactRef
from tests.rl.harness.v2_service_fixtures import exact_wp4_case


DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
TOKEN = "transport-secret"
EPISODE_ID = "episode-selection-a"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
V2_PATHS = {
    "/v2/episodes",
    "/v2/episodes/{episode_id}:run",
    "/v2/episodes/{episode_id}",
    "/v2/episodes/{episode_id}:cancel",
    "/v2/episodes/{episode_id}/envelopes/completed",
    "/v2/episodes/{episode_id}/envelopes/closed",
    "/v2/episodes/{episode_id}/exports/{role}",
}
HISTORICAL_V1_PATHS = {
    "/v1/episodes/{episode_id}",
    "/v1/episodes/{episode_id}/artifact",
}


def _ref(label: str, digest: str = DIGEST) -> ArtifactRef:
    return ArtifactRef(label, digest, 2, "application/json")
class RecordingHistoricalStore:
    def __init__(self, episode_id: str, payload: bytes) -> None:
        self.artifact_id = f"{episode_id}:episode-tombstone"
        self.payload = payload
        self.ref = ArtifactRef(
            self.artifact_id,
            "sha256:" + hashlib.sha256(payload).hexdigest(),
            len(payload),
            "application/json",
        )
        self.calls: list[tuple[str, object]] = []

    def has(self, value: object) -> bool:
        self.calls.append(("has", value))
        return value == self.artifact_id

    def get_ref(self, artifact_id: str) -> ArtifactRef:
        self.calls.append(("get_ref", artifact_id))
        if artifact_id != self.artifact_id:
            raise KeyError(artifact_id)
        return self.ref

    def get_bytes(self, value: object) -> bytes:
        self.calls.append(("get_bytes", value))
        return self.payload


def _historical_payload(episode_id: str) -> bytes:
    return json.dumps(
        {
            "schema_version": "bb.harness.episode.v1",
            "episode_id": episode_id,
            "create_fingerprint": DIGEST,
            "run_fingerprint": OTHER_DIGEST,
            "sandbox_attestation": {"cleanup_state": "closed"},
            "cleanup_state": "closed",
            "result": {"episode_id": episode_id, "reward": 1.0},
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _create_body() -> dict[str, Any]:
    _, request, _ = exact_wp4_case()
    body = {
        "schema_version": "bb.rl.episode.v2",
        "resolution": request.model_dump(mode="json"),
    }
    assert body["resolution"]["episode_id"] == EPISODE_ID
    return body


def _run_body() -> dict[str, Any]:
    return {
        "schema_version": "bb.rl.episode.v2",
        "create_fingerprint": DIGEST,
        "task_input": {"prompt": "deterministic", "nested": [None, True, 3]},
        "context": {"attempt": 1},
    }



class RecordingV2Service:
    def __init__(self) -> None:
        self.start_calls = 0
        self.close_calls = 0
        self.calls: list[tuple[Any, ...]] = []
        self.cached = False
        self.error: BaseException | None = None
        self.close_state = EpisodeLifecycleState.CLOSED
        self.cleanup = EpisodeCleanupDisposition.RELEASED
        self.cancel_reason: str | None = None

    async def start(self) -> None:
        self.start_calls += 1

    async def close(self) -> None:
        self.close_calls += 1

    async def create(self, request: Any) -> V2OperationResult[V2CreateResult]:
        self.calls.append(("create", request))
        if self.error is not None:
            raise self.error
        _, _, resolved = exact_wp4_case()
        result = V2CreateResult(
            episode_id=request.episode_id,
            create_fingerprint=DIGEST,
            state=EpisodeLifecycleState.READY,
            effective_plan_digest=resolved.effective_plan.canonical_digest(),
            selection_record_ref=resolved.selection_record_ref,
            effective_plan_ref=resolved.effective_plan_ref,
            policy_binding_digest=OTHER_DIGEST,
            selection_commit=resolved.selection_commit,
            base_receipt_digest=resolved.base_receipt_digest,
            final_receipt_digest=resolved.final_receipt_digest,
            policy_observation_digest=resolved.policy_capability_observation_digest,
            sandbox_preflight=V2SandboxPreflightIdentity(
                runtime="runtime-safe",
                runtime_class=RuntimeClass.TRUSTED_PROCESS,
                runtime_binary_digest=DIGEST,
                image_digest=OTHER_DIGEST,
                security_policy_digest=DIGEST,
                network_policy_digest=OTHER_DIGEST,
                verifier_digest=DIGEST,
                materialization_plan_digest=OTHER_DIGEST,
            ),
        )
        disposition = V2OperationDisposition.CACHED if self.cached else V2OperationDisposition.FRESH
        self.cached = True
        return V2OperationResult(result, disposition)

    async def run(
        self,
        episode_id: str,
        *,
        create_fingerprint: str,
        task_input: Any,
        context: Any,
    ) -> V2OperationResult[V2RunResult]:
        self.calls.append(("run", episode_id, create_fingerprint, task_input, context))
        if self.error is not None:
            raise self.error
        result = V2RunResult(
            episode_id=episode_id,
            create_fingerprint=create_fingerprint,
            run_fingerprint=OTHER_DIGEST,
            primary_disposition=EpisodePrimaryDisposition.SUCCEEDED,
            response={"answer": "stable", "canary": "body-is-persisted"},
            termination="completed",
            turn_count=2,
            completed_envelope_ref=_ref("completed"),
            closed_envelope_ref=_ref("closed", OTHER_DIGEST),
            result_ref=_ref("result"),
            evidence_manifest_ref=_ref("evidence", OTHER_DIGEST),
            evidence_root=DIGEST,
            reward=1.0,
            reward_components={"correctness": 1.0},
            artifact_manifest_ref=_ref("manifest"),
            primary_measurement_digest=DIGEST,
            verifier_measurement_digest=OTHER_DIGEST,
            verifier_result_digest=DIGEST,
        )
        disposition = V2OperationDisposition.CACHED if self.cached else V2OperationDisposition.FRESH
        self.cached = True
        return V2OperationResult(result, disposition)

    async def get_state(self, episode_id: str) -> V2EpisodeState:
        self.calls.append(("status", episode_id))
        if self.error is not None:
            raise self.error
        return V2EpisodeState(
            episode_id=episode_id,
            state=self.close_state,
            transition_sequence=8,
            transition_head_digest=DIGEST,
            create_fingerprint=DIGEST,
            run_fingerprint=OTHER_DIGEST,
            primary_disposition=EpisodePrimaryDisposition.SUCCEEDED,
            cleanup_disposition=self.cleanup,
            completed_envelope_ref=_ref("completed"),
            closed_envelope_ref=(
                _ref("closed", OTHER_DIGEST)
                if self.close_state is EpisodeLifecycleState.CLOSED
                else None
            ),
        )

    async def cancel(self, episode_id: str, reason: str) -> V2CancellationResult:
        self.calls.append(("cancel", episode_id, reason))
        if self.error is not None:
            raise self.error
        if self.cancel_reason is None:
            self.cancel_reason = reason
        return V2CancellationResult(
            episode_id,
            True,
            self.cancel_reason,
            EpisodeLifecycleState.CANCEL_REQUESTED,
        )

    async def close_episode(self, episode_id: str) -> V2OperationResult[V2CloseResult]:
        self.calls.append(("close", episode_id))
        if self.error is not None:
            raise self.error
        result = V2CloseResult(
            episode_id,
            self.close_state,
            self.cleanup,
            _ref("closed", OTHER_DIGEST)
            if self.close_state is EpisodeLifecycleState.CLOSED
            else None,
        )
        return V2OperationResult(result, V2OperationDisposition.FRESH)

    async def get_completed_envelope(self, episode_id: str) -> dict[str, Any]:
        self.calls.append(("completed", episode_id))
        return {
            "schema_version": "bb.rl.completed-envelope.v2",
            "episode_id": episode_id,
            "create_fingerprint": DIGEST,
            "run_fingerprint": OTHER_DIGEST,
            "cleanup_disposition": "pending",
            "envelope_ref": asdict(_ref("completed")),
        }

    async def get_closed_envelope(self, episode_id: str) -> dict[str, Any]:
        self.calls.append(("closed", episode_id))
        if self.close_state is not EpisodeLifecycleState.CLOSED:
            raise V2EpisodeQuarantined(
                SafeFailureFactV2("cleanup", "cleanup_not_released", "reconcile", "durable")
            )
        return {
            "schema_version": "bb.rl.closed-envelope.v2",
            "episode_id": episode_id,
            "completed_envelope_ref": asdict(_ref("completed")),
            "cleanup_disposition": "released",
            "envelope_ref": asdict(_ref("closed", OTHER_DIGEST)),
        }

    async def export_closed(
        self, episode_id: str, claims: ExportAuthorizationClaimsV2
    ) -> dict[str, Any]:
        self.calls.append(("export", episode_id, claims))
        if self.close_state is not EpisodeLifecycleState.CLOSED:
            raise V2EpisodeQuarantined(
                SafeFailureFactV2("export", "episode_not_closed", "reconcile", "none")
            )
        return {
            "schema_version": "bb.rl.export-manifest.v2",
            "episode_id": episode_id,
            "allowed_roles": list(claims.allowed_roles),
            "authorization_digest": claims.digest,
            "exported_objects": [],
            "omitted": [],
        }


class FrozenPinnedExportService(RecordingV2Service):
    """Frozen service seam backed by one service-produced per-role export pin."""

    def __init__(self) -> None:
        super().__init__()
        self.role = "runner_transcript"
        self.subject_digest = "sha256:" + "c" * 64
        self.evidence_policy_ref = "evidence-policy@7"
        self.retention_policy_ref = "retention-policy@4"
        self.redaction_decision_ref = _ref("redaction-decision", "sha256:" + "d" * 64)
        self.artifact_ref = _ref("runner-transcript", "sha256:" + "e" * 64)
        self.retention_active = True
        self.pinned_authorization = ExportAuthorizationV2(
            subject=self.subject_digest,
            scope="episode_export",
            evidence_policy_ref=self.evidence_policy_ref,
            retention_policy_ref=self.retention_policy_ref,
            allowed_roles=(self.role,),
            redaction_decision_digest=self.redaction_decision_ref.sha256,
            not_before="2026-01-01T00:00:05Z",
            not_after="2026-01-01T01:00:05Z",
        )
        self.pinned_claims = ExportAuthorizationClaimsV2(
            subject_digest=self.subject_digest,
            scope="episode_export",
            evidence_policy_ref=self.evidence_policy_ref,
            retention_policy_ref=self.retention_policy_ref,
            allowed_roles=(self.role,),
            redaction_decision_digest=self.redaction_decision_ref.sha256,
        )

    async def export_closed(
        self, episode_id: str, claims: ExportAuthorizationClaimsV2
    ) -> dict[str, Any]:
        self.calls.append(("export", episode_id, claims))
        if (
            self.close_state is not EpisodeLifecycleState.CLOSED
            or not self.retention_active
            or type(claims) is not ExportAuthorizationClaimsV2
            or claims != self.pinned_claims
        ):
            raise V2EpisodeQuarantined(
                SafeFailureFactV2("export", "export_not_authorized", "reconcile", "none")
            )
        return {
            "schema_version": "bb.rl.export-manifest.v2",
            "episode_id": episode_id,
            "allowed_roles": [self.role],
            "authorization_digest": self.pinned_authorization.digest,
            "evidence_policy_ref": self.evidence_policy_ref,
            "retention_policy_ref": self.retention_policy_ref,
            "redaction_decision_digest": self.redaction_decision_ref.sha256,
            "exported_objects": [
                {
                    "role": self.role,
                    "producer": "runner",
                    "artifact_ref": asdict(self.artifact_ref),
                }
            ],
            "omitted": [],
        }


def _client(
    v2: RecordingV2Service | None = None,
    *,
    token: str = TOKEN,
) -> tuple[TestClient, RecordingV2Service]:
    service = v2 or RecordingV2Service()
    app = create_app(service, auth_token=token)
    return TestClient(app), service


def _export_headers(
    service: FrozenPinnedExportService, **overrides: str
) -> dict[str, str]:
    claims = service.pinned_claims
    headers = {
        **AUTH,
        "X-BreadBoard-Export-Subject-Digest": claims.subject_digest,
        "X-BreadBoard-Export-Scope": claims.scope,
        "X-BreadBoard-Export-Evidence-Policy-Ref": claims.evidence_policy_ref,
        "X-BreadBoard-Export-Retention-Policy-Ref": claims.retention_policy_ref,
        "X-BreadBoard-Export-Redaction-Decision-Digest": claims.redaction_decision_digest,
    }
    headers.update(overrides)
    return headers


def test_v2_registers_only_frozen_routes_and_read_only_v1_history() -> None:
    client, _ = _client()
    with client:
        paths = client.get("/openapi.json").json()["paths"]
    assert {path for path in paths if path.startswith("/v2/")} == V2_PATHS
    assert {path for path in paths if path.startswith("/v1/")} == HISTORICAL_V1_PATHS
    assert "/v2/artifacts/{artifact_id}" not in paths
    assert "/v2/episodes/{episode_id}/artifacts/{artifact_id}" not in paths
def test_historical_v1_get_routes_require_auth_and_project_frozen_history() -> None:
    episode_id = "historical-episode"
    payload = _historical_payload(episode_id)
    history_store = RecordingHistoricalStore(episode_id, payload)
    v2 = RecordingV2Service()
    app = create_app(
        v2,
        history=HistoricalV1EpisodeReader(history_store),
        auth_token=TOKEN,
    )
    with TestClient(app) as client:
        status = client.get(f"/v1/episodes/{episode_id}", headers=AUTH)
        artifact = client.get(
            f"/v1/episodes/{episode_id}/artifact",
            headers=AUTH,
        )

    assert status.status_code == 200
    assert status.json() == {
        "schema_version": "bb.harness.episode.v1",
        "episode_id": episode_id,
        "state": "closed",
        "reason": "",
    }
    assert artifact.status_code == 200
    assert artifact.content == payload
    assert artifact.headers["content-type"] == "application/json"
    assert artifact.headers["etag"] == f'"{history_store.ref.sha256}"'
    assert (
        artifact.headers["x-breadboard-artifact-sha256"]
        == history_store.ref.sha256
    )
    assert history_store.calls
    assert v2.calls == []


@pytest.mark.parametrize(
    "path",
    [
        "/v1/episodes/historical-episode",
        "/v1/episodes/historical-episode/artifact",
    ],
)
def test_historical_v1_unauthorized_reads_do_not_touch_or_leak_history(
    path: str,
) -> None:
    episode_id = "historical-episode"
    history_store = RecordingHistoricalStore(
        episode_id,
        _historical_payload(episode_id),
    )
    v2 = RecordingV2Service()
    app = create_app(
        v2,
        history=HistoricalV1EpisodeReader(history_store),
        auth_token=TOKEN,
    )
    with TestClient(app) as client:
        response = client.get(path)

    assert response.status_code == 401
    assert response.json() == {
        "schema_version": "bb.rl.episode.v2",
        "category": "authentication",
        "code": "invalid_bearer_token",
        "retry_disposition": "new_credentials",
        "side_effect_boundary": "none",
    }
    assert episode_id not in response.text
    assert history_store.calls == []
    assert v2.calls == []


@pytest.mark.parametrize("method", ["POST", "DELETE"])
@pytest.mark.parametrize(
    "path",
    [
        "/v1/episodes/historical-episode",
        "/v1/episodes/historical-episode/artifact",
    ],
)
def test_historical_v1_mutations_are_rejected_without_v2_side_effects(
    method: str,
    path: str,
) -> None:
    episode_id = "historical-episode"
    history_store = RecordingHistoricalStore(
        episode_id,
        _historical_payload(episode_id),
    )
    v2 = RecordingV2Service()
    app = create_app(
        v2,
        history=HistoricalV1EpisodeReader(history_store),
        auth_token=TOKEN,
    )
    with TestClient(app) as client:
        response = client.request(method, path, headers=AUTH)

    assert response.status_code == 405
    assert response.json() == {"detail": "Method Not Allowed"}
    assert history_store.calls == []
    assert v2.calls == []


def test_v2_lifespan_starts_and_closes_exactly_once() -> None:
    v2 = RecordingV2Service()
    client, _ = _client(v2)
    assert (v2.start_calls, v2.close_calls) == (0, 0)
    with client:
        assert client.get("/openapi.json").status_code == 200
        assert (v2.start_calls, v2.close_calls) == (1, 0)
    assert (v2.start_calls, v2.close_calls) == (1, 1)


@pytest.mark.parametrize(
    ("mutation", "bad_value"),
    [
        ("schema_version", "bb.rl.episode.v1"),
        ("profile", "ambient-profile"),
        ("family", "openai"),
        ("base_url", "https://user:secret@example.invalid/v1"),
        ("policy_route", "ambient"),
    ],
)
def test_create_rejects_wrong_literal_and_v1_or_provider_fields(
    mutation: str, bad_value: str
) -> None:
    v2 = RecordingV2Service()
    body = _create_body()
    body[mutation] = bad_value
    client, _ = _client(v2)
    with client:
        response = client.post("/v2/episodes", json=body, headers=AUTH)
    assert response.status_code == 422
    assert v2.calls == []


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v2/episodes", lambda: {"resolution": _create_body()["resolution"]}),
        (
            f"/v2/episodes/{EPISODE_ID}:run",
            lambda: {
                key: value
                for key, value in _run_body().items()
                if key != "schema_version"
            },
        ),
        (
            f"/v2/episodes/{EPISODE_ID}:cancel",
            lambda: {"reason": "operator stop"},
        ),
    ],
)
def test_every_v2_request_requires_the_exact_schema_literal(
    path: str, body: Any
) -> None:
    v2 = RecordingV2Service()
    client, _ = _client(v2)
    with client:
        response = client.post(path, json=body(), headers=AUTH)
    assert response.status_code == 422
    assert v2.calls == []


@pytest.mark.parametrize(
    "patch",
    [
        {"schema_version": "bb.rl.episode.v1"},
        {"create_fingerprint": "sha256:abc"},
        {"unexpected": True},
        {"task_input": {"bad": float("nan")}},
        {"context": {"bad": float("inf")}},
    ],
)
def test_run_rejects_wrong_literal_malformed_digest_open_or_nonfinite_json(
    patch: dict[str, Any]
) -> None:
    v2 = RecordingV2Service()
    body = _run_body()
    body.update(patch)
    client, _ = _client(v2)
    with client:
        response = client.request(
            "POST",
            f"/v2/episodes/{EPISODE_ID}:run",
            content=json.dumps(body, allow_nan=True),
            headers={**AUTH, "Content-Type": "application/json"},
        )
    assert response.status_code == 422
    assert v2.calls == []



def test_create_rejects_a_malformed_nested_contract_digest_before_service() -> None:
    v2 = RecordingV2Service()
    body = _create_body()
    body["resolution"]["task"]["parameters_digest"] = "sha256:short"
    client, _ = _client(v2)
    with client:
        response = client.post("/v2/episodes", json=body, headers=AUTH)
    assert response.status_code == 422
    assert v2.calls == []


def test_encoded_path_separator_cannot_change_the_episode_identity() -> None:
    v2 = RecordingV2Service()
    client, _ = _client(v2)
    with client:
        response = client.post(
            f"/v2/episodes/{EPISODE_ID}%2Fother:run",
            json=_run_body(),
            headers=AUTH,
        )
    assert response.status_code == 404
    assert v2.calls == []

def test_cancel_is_strict_and_passes_only_a_bounded_normalized_reason() -> None:
    v2 = RecordingV2Service()
    client, _ = _client(v2)
    with client:
        extra = client.post(
            f"/v2/episodes/{EPISODE_ID}:cancel",
            json={"schema_version": "bb.rl.episode.v2", "reason": "safe", "extra": 1},
            headers=AUTH,
        )
        blank = client.post(
            f"/v2/episodes/{EPISODE_ID}:cancel",
            json={"schema_version": "bb.rl.episode.v2", "reason": "   \t"},
            headers=AUTH,
        )
        long = client.post(
            f"/v2/episodes/{EPISODE_ID}:cancel",
            json={"schema_version": "bb.rl.episode.v2", "reason": "x" * 257},
            headers=AUTH,
        )
        unnormalized = client.post(
            f"/v2/episodes/{EPISODE_ID}:cancel",
            json={"schema_version": "bb.rl.episode.v2", "reason": "  operator   stop  "},
            headers=AUTH,
        )
        good = client.post(
            f"/v2/episodes/{EPISODE_ID}:cancel",
            json={"schema_version": "bb.rl.episode.v2", "reason": "operator stop"},
            headers=AUTH,
        )
    assert [extra.status_code, blank.status_code, long.status_code, unnormalized.status_code, good.status_code] == [422, 422, 422, 422, 200]
    assert v2.calls == [("cancel", EPISODE_ID, "operator stop")]


def test_repeated_cancel_response_replays_first_reason_without_transport_drift() -> None:
    v2 = RecordingV2Service()
    client, _ = _client(v2)
    with client:
        first = client.post(
            f"/v2/episodes/{EPISODE_ID}:cancel",
            json={"schema_version": "bb.rl.episode.v2", "reason": "operator requested A"},
            headers=AUTH,
        )
        retry = client.post(
            f"/v2/episodes/{EPISODE_ID}:cancel",
            json={"schema_version": "bb.rl.episode.v2", "reason": "operator requested B"},
            headers=AUTH,
        )

    assert first.status_code == retry.status_code == 200
    assert first.json() == {
        "schema_version": "bb.rl.episode.v2",
        "episode_id": EPISODE_ID,
        "requested": True,
        "reason": "operator requested A",
        "state": "cancel_requested",
    }
    assert retry.json() == first.json()
    assert v2.calls == [
        ("cancel", EPISODE_ID, "operator requested A"),
        ("cancel", EPISODE_ID, "operator requested B"),
    ]


def test_authenticated_create_run_status_cancel_close_reach_exact_service_seams() -> None:
    v2 = RecordingV2Service()
    client, _ = _client(v2)
    with client:
        create = client.post("/v2/episodes", json=_create_body(), headers=AUTH)
        run = client.post(f"/v2/episodes/{EPISODE_ID}:run", json=_run_body(), headers=AUTH)
        status = client.get(f"/v2/episodes/{EPISODE_ID}", headers=AUTH)
        cancel = client.post(
            f"/v2/episodes/{EPISODE_ID}:cancel",
            json={"schema_version": "bb.rl.episode.v2", "reason": "operator stop"},
            headers=AUTH,
        )
        close = client.delete(f"/v2/episodes/{EPISODE_ID}", headers=AUTH)
    assert [x.status_code for x in (create, run, status, cancel, close)] == [200] * 5
    create_payload = create.json()
    assert create_payload["episode_id"] == EPISODE_ID
    assert create_payload["base_receipt_digest"].startswith("sha256:")
    assert create_payload["final_receipt_digest"].startswith("sha256:")
    assert create_payload["policy_observation_digest"].startswith("sha256:")
    assert create_payload["selection_commit"]["binding_ref"]["sha256"].startswith("sha256:")
    assert create_payload["sandbox_preflight"] == {
        "runtime": "runtime-safe",
        "runtime_class": "trusted_process",
        "runtime_binary_digest": DIGEST,
        "image_digest": OTHER_DIGEST,
        "security_policy_digest": DIGEST,
        "network_policy_digest": OTHER_DIGEST,
        "verifier_digest": DIGEST,
        "materialization_plan_digest": OTHER_DIGEST,
    }
    run_payload = run.json()
    assert run_payload["response"] == {"answer": "stable", "canary": "body-is-persisted"}
    assert run_payload["result_ref"]["artifact_id"] == "result"
    assert run_payload["evidence_manifest_ref"]["sha256"] == OTHER_DIGEST
    assert run_payload["reward"] == 1.0
    assert run_payload["reward_components"] == {"correctness": 1.0}
    assert run_payload["artifact_manifest_ref"]["artifact_id"] == "manifest"
    assert run_payload["primary_measurement_digest"] == DIGEST
    assert run_payload["verifier_measurement_digest"] == OTHER_DIGEST
    assert status.json()["cleanup_disposition"] == "released"
    assert cancel.json()["reason"] == "operator stop"
    assert close.json()["closed_envelope_ref"]["sha256"] == OTHER_DIGEST
    assert [call[0] for call in v2.calls] == ["create", "run", "status", "cancel", "close"]


def test_cached_retry_returns_byte_identical_body_with_only_source_header_changed() -> None:
    v2 = RecordingV2Service()
    client, _ = _client(v2)
    with client:
        fresh = client.post(f"/v2/episodes/{EPISODE_ID}:run", json=_run_body(), headers=AUTH)
        cached = client.post(f"/v2/episodes/{EPISODE_ID}:run", json=_run_body(), headers=AUTH)
    assert fresh.status_code == cached.status_code == 200
    assert fresh.content == cached.content
    assert fresh.headers["X-BreadBoard-Result-Source"] == "fresh"
    assert cached.headers["X-BreadBoard-Result-Source"] == "cached"


def test_v2_bearer_rejection_is_constant_shape_and_does_not_enter_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import breadboard.rl.harness.api as api_module

    calls: list[tuple[Any, Any]] = []
    real_compare = api_module.hmac.compare_digest

    def recording_compare(left: Any, right: Any) -> bool:
        calls.append((left, right))
        return real_compare(left, right)

    monkeypatch.setattr(api_module.hmac, "compare_digest", recording_compare)
    v2 = RecordingV2Service()
    client, _ = _client(v2)
    with client:
        missing = client.post("/v2/episodes", json=_create_body())
        wrong = client.post(
            "/v2/episodes", json=_create_body(), headers={"Authorization": "Bearer wrong"}
        )
    assert (missing.status_code, missing.content) == (wrong.status_code, wrong.content)
    assert missing.status_code == 401
    assert len(calls) == 2
    assert all(right == f"Bearer {TOKEN}" for _, right in calls)
    assert v2.calls == []


def test_typed_errors_never_leak_exception_text_secret_url_or_request_payload() -> None:
    secret = "super-secret-bearer"
    url = "https://user:password@provider.invalid/v1"
    prompt = "private-request-canary"
    failure = SafeFailureFactV2(
        "conflict", "create_fingerprint_mismatch", "new_episode_id", "none"
    )
    v2 = RecordingV2Service()
    error = V2EpisodeConflict(failure)
    error.__cause__ = RuntimeError(secret, url, prompt)
    v2.error = error
    client, _ = _client(v2)
    body = _create_body()
    body["resolution"]["subject"]["principal_id"] = prompt
    with client:
        response = client.post("/v2/episodes", json=body, headers=AUTH)
    assert response.status_code == 409
    assert response.json()["code"] == "create_fingerprint_mismatch"
    encoded = response.content.decode()
    assert secret not in encoded
    assert url not in encoded
    assert prompt not in encoded
    assert "V2EpisodeConflict" not in encoded


@pytest.mark.parametrize(
    ("state", "cleanup"),
    [
        (EpisodeLifecycleState.COMPLETED, EpisodeCleanupDisposition.PENDING),
        (EpisodeLifecycleState.QUARANTINED, EpisodeCleanupDisposition.QUARANTINED),
    ],
)
def test_pending_or_quarantined_cleanup_never_manufactures_a_closed_claim(
    state: EpisodeLifecycleState, cleanup: EpisodeCleanupDisposition
) -> None:
    v2 = RecordingV2Service()
    v2.close_state = state
    v2.cleanup = cleanup
    client, _ = _client(v2)
    with client:
        close = client.delete(f"/v2/episodes/{EPISODE_ID}", headers=AUTH)
        closed = client.get(
            f"/v2/episodes/{EPISODE_ID}/envelopes/closed", headers=AUTH
        )
    assert close.status_code in {200, 202, 409}
    assert close.json().get("closed_envelope_ref") is None
    assert close.json()["cleanup_disposition"] == cleanup.value
    assert closed.status_code == 409
    assert "closed_envelope_ref" not in closed.json()


def test_completed_and_closed_envelopes_are_retrieved_through_verified_service_reads() -> None:
    v2 = RecordingV2Service()
    client, _ = _client(v2)
    with client:
        completed = client.get(
            f"/v2/episodes/{EPISODE_ID}/envelopes/completed", headers=AUTH
        )
        closed = client.get(
            f"/v2/episodes/{EPISODE_ID}/envelopes/closed", headers=AUTH
        )
    assert completed.status_code == closed.status_code == 200
    assert completed.json()["cleanup_disposition"] == "pending"
    assert closed.json()["cleanup_disposition"] == "released"
    assert [call[0] for call in v2.calls] == ["completed", "closed"]


def test_closed_export_roundtrip_uses_exact_service_produced_per_role_pin() -> None:
    v2 = FrozenPinnedExportService()
    client, _ = _client(v2)
    path = f"/v2/episodes/{EPISODE_ID}/exports/{v2.role}"
    with client:
        completed = client.get(
            f"/v2/episodes/{EPISODE_ID}/envelopes/completed", headers=AUTH
        )
        closed = client.get(
            f"/v2/episodes/{EPISODE_ID}/envelopes/closed", headers=AUTH
        )
        exported = client.get(path, headers=_export_headers(v2))

    assert completed.status_code == closed.status_code == exported.status_code == 200
    body = exported.json()
    assert body["authorization_digest"] == v2.pinned_authorization.digest
    assert body["evidence_policy_ref"] == v2.evidence_policy_ref
    assert body["retention_policy_ref"] == v2.retention_policy_ref
    assert body["redaction_decision_digest"] == v2.redaction_decision_ref.sha256
    assert body["allowed_roles"] == [v2.role]
    assert body["omitted"] == []
    assert len(body["exported_objects"]) == 1
    assert body["exported_objects"][0] == {
        "role": v2.role,
        "producer": "runner",
        "artifact_ref": asdict(v2.artifact_ref),
    }
    assert [call[0] for call in v2.calls] == ["completed", "closed", "export"]


def test_export_transport_has_no_retention_window_input_and_cannot_alter_pinned_boundaries() -> None:
    v2 = FrozenPinnedExportService()
    client, _ = _client(v2)
    path = f"/v2/episodes/{EPISODE_ID}/exports/{v2.role}"
    headers = _export_headers(
        v2,
        **{
            "X-BreadBoard-Export-Not-Before": "1900-01-01T00:00:00Z",
            "X-BreadBoard-Export-Not-After": "9999-12-31T23:59:59Z",
        },
    )
    with client:
        exported = client.get(path, headers=headers)
        operation = client.get("/openapi.json").json()["paths"][
            "/v2/episodes/{episode_id}/exports/{role}"
        ]["get"]
    header_names = {
        parameter["name"]
        for parameter in operation["parameters"]
        if parameter["in"] == "header"
    }
    assert "X-BreadBoard-Export-Not-Before" not in header_names
    assert "X-BreadBoard-Export-Not-After" not in header_names
    assert exported.status_code == 200
    assert exported.json()["authorization_digest"] == v2.pinned_authorization.digest
    assert v2.calls == [("export", EPISODE_ID, v2.pinned_claims)]
    assert not hasattr(v2.pinned_claims, "not_before")
    assert not hasattr(v2.pinned_claims, "not_after")


@pytest.mark.parametrize(
    ("header", "value", "role"),
    (
        ("X-BreadBoard-Export-Subject-Digest", "sha256:" + "f" * 64, "runner_transcript"),
        ("X-BreadBoard-Export-Scope", "other_export", "runner_transcript"),
        ("X-BreadBoard-Export-Evidence-Policy-Ref", "evidence-policy@8", "runner_transcript"),
        ("X-BreadBoard-Export-Retention-Policy-Ref", "retention-policy@5", "runner_transcript"),
        (
            "X-BreadBoard-Export-Redaction-Decision-Digest",
            "sha256:" + "0" * 64,
            "runner_transcript",
        ),
        ("X-BreadBoard-Export-Scope", "episode_export", "verifier"),
    ),
)
def test_export_mismatch_or_unpinned_authority_fails_safe(
    header: str, value: str, role: str
) -> None:
    v2 = FrozenPinnedExportService()
    client, _ = _client(v2)
    headers = _export_headers(v2, **{header: value})
    with client:
        response = client.get(
            f"/v2/episodes/{EPISODE_ID}/exports/{role}", headers=headers
        )

    assert response.status_code in {403, 409, 422}
    assert "exported_objects" not in response.json()
    assert v2.calls == [] or [call[0] for call in v2.calls] == ["export"]


def test_expired_service_produced_retention_authority_fails_safe() -> None:
    v2 = FrozenPinnedExportService()
    v2.retention_active = False
    client, _ = _client(v2)
    with client:
        response = client.get(
            f"/v2/episodes/{EPISODE_ID}/exports/{v2.role}",
            headers=_export_headers(v2),
        )

    assert response.status_code in {403, 409}
    assert "exported_objects" not in response.json()
    assert [call[0] for call in v2.calls] == ["export"]


@pytest.mark.parametrize(
    ("state", "cleanup"),
    (
        (EpisodeLifecycleState.COMPLETED, EpisodeCleanupDisposition.PENDING),
        (EpisodeLifecycleState.QUARANTINED, EpisodeCleanupDisposition.QUARANTINED),
    ),
)
def test_completed_or_quarantined_episode_never_exports(
    state: EpisodeLifecycleState, cleanup: EpisodeCleanupDisposition
) -> None:
    v2 = FrozenPinnedExportService()
    v2.close_state = state
    v2.cleanup = cleanup
    client, _ = _client(v2)
    with client:
        response = client.get(
            f"/v2/episodes/{EPISODE_ID}/exports/{v2.role}",
            headers=_export_headers(v2),
        )

    assert response.status_code in {403, 409}
    assert "exported_objects" not in response.json()
