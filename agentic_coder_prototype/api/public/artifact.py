from __future__ import annotations
from types import SimpleNamespace
from fastapi import APIRouter
from breadboard.product.cli import artifact as operations
from .models import PublicResult, invoke
router = APIRouter(tags=["public-artifact"])
def _args(workspace, artifact_id: str | None = None):
    return SimpleNamespace(workspace=workspace, REF=artifact_id, size=None, media_type=None)
@router.get("/v1/artifacts", operation_id="artifact.list", response_model=PublicResult)
def list_artifacts():
    return invoke("artifact.list", lambda workspace: operations.list_artifacts(_args(workspace)))
@router.post("/v1/artifacts/{artifact_id}/verify", operation_id="artifact.verify", response_model=PublicResult)
def verify(artifact_id: str):
    return invoke("artifact.verify", lambda workspace: operations.verify(_args(workspace, artifact_id)))
@router.get("/v1/artifacts/{artifact_id}", operation_id="artifact.get", response_model=PublicResult)
def get(artifact_id: str):
    return invoke("artifact.get", lambda workspace: operations.get(_args(workspace, artifact_id)))
