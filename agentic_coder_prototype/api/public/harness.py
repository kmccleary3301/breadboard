from __future__ import annotations

from types import SimpleNamespace

from fastapi import APIRouter

from breadboard.product.cli import harness as operations
from .models import HarnessCreateRequest, PublicResult, invoke, workspace_path

router = APIRouter(tags=["public-harness"])


def _args(workspace, reference: str | None = None, **values):
    fields = {"workspace": workspace, **values}
    if reference is not None:
        fields["PATH"] = workspace_path(reference, workspace)
    return SimpleNamespace(**fields)


@router.post("/v1/harnesses", operation_id="harness.create", response_model=PublicResult)
def create(request: HarnessCreateRequest):
    return invoke("harness.create", lambda workspace: operations.init(_args(workspace, out=workspace_path(request.directory, workspace))))


@router.get("/v1/harnesses", operation_id="harness.list", response_model=PublicResult)
def list_harnesses():
    return invoke("harness.list", lambda workspace: operations.list_harnesses(_args(workspace, directory=workspace)))


@router.post("/v1/harnesses/{harness_id:path}/validate", operation_id="harness.validate", response_model=PublicResult)
def validate(harness_id: str):
    return invoke("harness.validate", lambda workspace: operations.validate(_args(workspace, harness_id)))


@router.post("/v1/harnesses/{harness_id:path}/explain", operation_id="harness.explain", response_model=PublicResult)
def explain(harness_id: str):
    return invoke("harness.explain", lambda workspace: operations.explain(_args(workspace, harness_id, strict=False)))


@router.post("/v1/harnesses/{harness_id:path}/lock", operation_id="harness.lock", response_model=PublicResult)
def lock(harness_id: str):
    return invoke("harness.lock", lambda workspace: operations.lock(_args(workspace, harness_id, out=None, check=False)))


@router.get("/v1/harness-locks/{lock_id:path}", operation_id="harness_lock.get", response_model=PublicResult)
def get_lock(lock_id: str):
    return invoke("harness_lock.get", lambda workspace: operations.get_lock(_args(workspace, lock_id)))


@router.put("/v1/harnesses/{harness_id:path}", operation_id="harness.update", response_model=PublicResult)
def update(harness_id: str):
    return invoke("harness.update", lambda workspace: operations.update(_args(workspace, harness_id)))


@router.get("/v1/harnesses/{harness_id:path}", operation_id="harness.get", response_model=PublicResult)
def get(harness_id: str):
    return invoke("harness.get", lambda workspace: operations.get(_args(workspace, harness_id)))
