from __future__ import annotations

from fastapi import APIRouter

from breadboard.product.operations.artifact import (
    GetArtifactRequest,
    ListArtifactsRequest,
    VerifyArtifactRequest,
    get_artifact as run_get_artifact,
    list_artifacts as run_list_artifacts,
    verify_artifact as run_verify_artifact,
)
from .models import PublicResult, invoke, public_operation_context


router = APIRouter(tags=["public-artifact"])


@router.get(
    "/v1/artifacts",
    operation_id="artifact.list",
    response_model=PublicResult,
)
def list_artifacts():
    return invoke(
        "artifact.list",
        lambda workspace: run_list_artifacts(
            ListArtifactsRequest(),
            public_operation_context(workspace),
        ),
    )


@router.post(
    "/v1/artifacts/{artifact_id}/verify",
    operation_id="artifact.verify",
    response_model=PublicResult,
)
def verify(artifact_id: str):
    return invoke(
        "artifact.verify",
        lambda workspace: run_verify_artifact(
            VerifyArtifactRequest(reference=artifact_id),
            public_operation_context(workspace),
        ),
    )


@router.get(
    "/v1/artifacts/{artifact_id}",
    operation_id="artifact.get",
    response_model=PublicResult,
)
def get(artifact_id: str):
    return invoke(
        "artifact.get",
        lambda workspace: run_get_artifact(
            GetArtifactRequest(reference=artifact_id),
            public_operation_context(workspace),
        ),
    )
