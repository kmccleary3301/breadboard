from __future__ import annotations


from fastapi import APIRouter

from breadboard.product.harness.lock import lock_metadata_path, lock_path
from breadboard.product.operations.harness import (
    ExplainHarnessRequest,
    GetHarnessLockRequest,
    GetHarnessRequest,
    ListHarnessesRequest,
    ValidateHarnessRequest,
    explain_harness,
    get_harness,
    get_harness_lock,
    list_harnesses,
    validate_harness,
)

from .models import (
    HarnessCreateRequest,
    HarnessUpdateRequest,
    PublicResult,
    invoke,
    public_operation_context,
    workspace_path,
)


router = APIRouter(tags=["public-harness"])


def _args(workspace, reference: str | None = None, **values):
    from types import SimpleNamespace

    fields = {"workspace": workspace, "contained": True, **values}
    if reference is not None:
        fields["PATH"] = workspace_path(reference, workspace)
    return SimpleNamespace(**fields)


def _contained(path, workspace):
    return workspace_path(str(path.relative_to(workspace)), workspace)


def _create(request: HarnessCreateRequest, workspace):
    from breadboard.product.cli import harness as legacy_harness_operations

    directory = workspace_path(request.directory, workspace)
    for target in legacy_harness_operations.daily_driver_bundle_paths(directory):
        _contained(target, workspace)
    return legacy_harness_operations.init(_args(workspace, out=directory))


def _lock(harness_id: str, workspace):
    from breadboard.product.cli import harness as legacy_harness_operations

    args = _args(workspace, harness_id, out=None, check=False)
    target = lock_path(args.PATH)
    _contained(target, workspace)
    _contained(lock_metadata_path(target), workspace)
    return legacy_harness_operations.lock(args)


def _update(
    harness_id: str,
    request: HarnessUpdateRequest,
    workspace,
):
    from breadboard.product.cli import harness as legacy_harness_operations

    return legacy_harness_operations.update(
        _args(workspace, harness_id, document=request.definition)
    )


@router.post(
    "/v1/harnesses", operation_id="harness.create", response_model=PublicResult
)
def create(request: HarnessCreateRequest):
    return invoke("harness.create", lambda workspace: _create(request, workspace))


@router.get("/v1/harnesses", operation_id="harness.list", response_model=PublicResult)
def list_harnesses_route():
    return invoke(
        "harness.list",
        lambda workspace: list_harnesses(
            ListHarnessesRequest(),
            public_operation_context(workspace),
        ),
    )


@router.post(
    "/v1/harnesses/{harness_id:path}/validate",
    operation_id="harness.validate",
    response_model=PublicResult,
)
def validate(harness_id: str):
    return invoke(
        "harness.validate",
        lambda workspace: validate_harness(
            ValidateHarnessRequest(harness_id),
            public_operation_context(workspace),
        ),
    )


@router.post(
    "/v1/harnesses/{harness_id:path}/explain",
    operation_id="harness.explain",
    response_model=PublicResult,
)
def explain(harness_id: str):
    return invoke(
        "harness.explain",
        lambda workspace: explain_harness(
            ExplainHarnessRequest(harness_id),
            public_operation_context(workspace),
        ),
    )


@router.post(
    "/v1/harnesses/{harness_id:path}/lock",
    operation_id="harness.lock",
    response_model=PublicResult,
)
def lock(harness_id: str):
    return invoke("harness.lock", lambda workspace: _lock(harness_id, workspace))


@router.get(
    "/v1/harness-locks/{lock_id:path}",
    operation_id="harness_lock.get",
    response_model=PublicResult,
)
def get_lock(lock_id: str):
    return invoke(
        "harness_lock.get",
        lambda workspace: get_harness_lock(
            GetHarnessLockRequest(lock_id),
            public_operation_context(workspace),
        ),
    )


@router.put(
    "/v1/harnesses/{harness_id:path}",
    operation_id="harness.update",
    response_model=PublicResult,
)
def update(harness_id: str, request: HarnessUpdateRequest):
    return invoke(
        "harness.update",
        lambda workspace: _update(harness_id, request, workspace),
    )


@router.get(
    "/v1/harnesses/{harness_id:path}",
    operation_id="harness.get",
    response_model=PublicResult,
)
def get(harness_id: str):
    return invoke(
        "harness.get",
        lambda workspace: get_harness(
            GetHarnessRequest(harness_id),
            public_operation_context(workspace),
        ),
    )
