from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict

from breadboard.rl.harness.contracts import (
    AtomicEpisodeRunRequest,
    EpisodeCreateRequest,
    EpisodeCreateResponse,
    EpisodeRunRequest,
    EpisodeRunResponse,
    EpisodeStateResponse,
    SCHEMA_VERSION,
)
from breadboard.rl.harness.policy import PolicyResponseError, PolicyRouteError
from breadboard.rl.harness.profiles import HarnessProfileRegistry
from breadboard.rl.harness.service import (
    BreadBoardEpisodeService,
    EpisodeCancelled,
    EpisodeConflict,
    EpisodeNotFound,
    HarnessEpisodeError,
)
from breadboard.rl.harness.sandbox import SandboxCleanupError
from breadboard.rl.state.cas import ArtifactStoreError


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = "cancel requested"


def create_app(
    service: BreadBoardEpisodeService | None = None,
    *,
    auth_token: str | None = None,
    allow_unauthenticated_loopback: bool = False,
) -> FastAPI:
    resolved_service = service or BreadBoardEpisodeService(
        profiles=HarnessProfileRegistry.from_environment()
    )
    resolved_token = (
        auth_token
        if auth_token is not None
        else os.environ.get("BREADBOARD_HARNESS_TOKEN", "")
    )
    if not resolved_token and not allow_unauthenticated_loopback:
        raise RuntimeError(
            "BREADBOARD_HARNESS_TOKEN is required unless unauthenticated loopback mode is explicitly enabled"
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            await resolved_service.close()

    app = FastAPI(
        title="BreadBoard episode harness", version=SCHEMA_VERSION, lifespan=lifespan
    )
    app.state.episode_service = resolved_service

    def authorize(authorization: str | None) -> None:
        if not resolved_token:
            return
        expected = f"Bearer {resolved_token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(
                status_code=401, detail="invalid BreadBoard harness bearer token"
            )

    def translate_error(exc: Exception) -> HTTPException:
        if isinstance(exc, EpisodeNotFound):
            return HTTPException(status_code=404, detail=f"episode not found: {exc}")
        if isinstance(exc, (EpisodeConflict, EpisodeCancelled)):
            return HTTPException(status_code=409, detail=str(exc))
        if isinstance(
            exc,
            (
                PolicyResponseError,
                HarnessEpisodeError,
                SandboxCleanupError,
                ArtifactStoreError,
            ),
        ):
            return HTTPException(status_code=502, detail=str(exc))
        if isinstance(exc, (PolicyRouteError, KeyError, ValueError)):
            return HTTPException(status_code=422, detail=str(exc))
        return HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "schema_version": SCHEMA_VERSION,
            "profiles": list(resolved_service.profiles.names()),
        }

    @app.post("/v1/episodes", response_model=EpisodeCreateResponse)
    async def create_episode(
        body: EpisodeCreateRequest,
        authorization: str | None = Header(default=None),
    ) -> EpisodeCreateResponse:
        authorize(authorization)
        try:
            return await resolved_service.create_episode(body)
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.post("/v1/episodes:run", response_model=EpisodeRunResponse)
    async def run_atomic(
        body: AtomicEpisodeRunRequest,
        authorization: str | None = Header(default=None),
    ) -> EpisodeRunResponse:
        authorize(authorization)
        try:
            return await resolved_service.run_atomic(body)
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.post("/v1/episodes/{episode_id}:run", response_model=EpisodeRunResponse)
    async def run_episode(
        episode_id: str,
        body: EpisodeRunRequest,
        authorization: str | None = Header(default=None),
    ) -> EpisodeRunResponse:
        authorize(authorization)
        try:
            return await resolved_service.run_episode(episode_id, body)
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.get("/v1/artifacts/{artifact_id:path}")
    async def get_artifact(
        artifact_id: str,
        authorization: str | None = Header(default=None),
    ) -> Response:
        authorize(authorization)
        try:
            ref, payload = await resolved_service.artifact(artifact_id)
        except Exception as exc:
            raise translate_error(exc) from exc
        return Response(
            content=payload,
            media_type=ref.media_type,
            headers={
                "ETag": f'"{ref.sha256}"',
                "X-BreadBoard-Artifact-SHA256": ref.sha256,
            },
        )

    @app.get("/v1/episodes/{episode_id}", response_model=EpisodeStateResponse)
    async def episode_status(
        episode_id: str,
        authorization: str | None = Header(default=None),
    ) -> EpisodeStateResponse:
        authorize(authorization)
        try:
            return await resolved_service.status(episode_id)
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.post("/v1/episodes/{episode_id}:cancel", response_model=EpisodeStateResponse)
    async def cancel_episode(
        episode_id: str,
        body: CancelRequest,
        authorization: str | None = Header(default=None),
    ) -> EpisodeStateResponse:
        authorize(authorization)
        try:
            return await resolved_service.cancel_episode(episode_id, body.reason)
        except Exception as exc:
            raise translate_error(exc) from exc

    @app.delete("/v1/episodes/{episode_id}", response_model=EpisodeStateResponse)
    async def close_episode(
        episode_id: str,
        authorization: str | None = Header(default=None),
    ) -> EpisodeStateResponse:
        authorize(authorization)
        try:
            return await resolved_service.close_episode(episode_id)
        except Exception as exc:
            raise translate_error(exc) from exc

    return app


def main() -> None:
    import uvicorn

    host = os.environ.get("BREADBOARD_HARNESS_HOST", "127.0.0.1")
    port = int(os.environ.get("BREADBOARD_HARNESS_PORT", "8097"))
    token = os.environ.get("BREADBOARD_HARNESS_TOKEN", "")
    loopback = host in {"127.0.0.1", "localhost", "::1"}
    application = create_app(
        auth_token=token or None,
        allow_unauthenticated_loopback=loopback and not token,
    )
    uvicorn.run(application, host=host, port=port, reload=False)


if __name__ == "__main__":
    main()


__all__ = ["create_app", "main"]
