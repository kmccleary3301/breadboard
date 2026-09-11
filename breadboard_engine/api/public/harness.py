from __future__ import annotations


from fastapi import APIRouter, Request

from breadboard.product.operations.harness import (
    CreateHarnessRequest as CreateHarnessOperationRequest,
    ExplainHarnessRequest,
    GenerationPublicationPort,
    GetHarnessLockRequest,
    GetHarnessRequest,
    ListHarnessesRequest,
    LockHarnessRequest,
    PackageHarnessRequest,
    PublishHarnessOutcome,
    PublishHarnessRequest,
    UpdateHarnessRequest as UpdateHarnessOperationRequest,
    ValidateHarnessRequest,
    create_harness,
    explain_harness,
    get_harness,
    get_harness_lock,
    list_harnesses,
    lock_harness,
    package_harness,
    publish_harness,
    update_harness,
    validate_harness,
)
from breadboard.product.operations.model import portable_ref
from .models import (
    HarnessCreateRequest,
    HarnessPackageRequest,
    HarnessPublishRequest,
    HarnessUpdateRequest,
    PublicResult,
    invoke,
    public_operation_context,
)


router = APIRouter(tags=["public-harness"])
class _GenerationPublicationAdapter:
    def __init__(self, service) -> None:
        self._service = service

    def publish(
        self,
        request: PublishHarnessRequest,
        context,
        effective_lock,
        source_path,
    ) -> PublishHarnessOutcome:
        publication = self._service.generation_lifecycle(
            context.workspace
        ).prepare_and_publish(
            request.target,
            effective_lock,
            portable_ref(source_path, context.workspace),
            request.expected_revision,
            request.request_id,
        )
        return PublishHarnessOutcome(
            target=publication.target,
            revision=publication.revision,
            generation_id=publication.generation_id,
            preparation_id=publication.preparation_id,
            request_id=publication.request_id,
        )



def _create(request: HarnessCreateRequest, workspace):
    return create_harness(
        CreateHarnessOperationRequest(request.directory),
        public_operation_context(workspace),
    )


def _lock(harness_id: str, workspace):
    return lock_harness(
        LockHarnessRequest(harness_id),
        public_operation_context(workspace),
    )


def _update(
    harness_id: str,
    request: HarnessUpdateRequest,
    workspace,
):
    return update_harness(
        UpdateHarnessOperationRequest(harness_id, definition=request.definition),
        public_operation_context(workspace),
    )


@router.post(
    "/v1/harnesses", operation_id="harness.create", response_model=PublicResult
)
def create(request: HarnessCreateRequest):
    return invoke("harness.create", lambda workspace: _create(request, workspace))


@router.post(
    "/v1/harness-packages",
    operation_id="harness.package",
    response_model=PublicResult,
)
def package(request: HarnessPackageRequest):
    return invoke(
        "harness.package",
        lambda workspace: package_harness(
            PackageHarnessRequest(request.source, request.out),
            public_operation_context(workspace),
        ),
    )

@router.post(
    "/v1/harness-publications/{target:path}",
    operation_id="harness.publish",
    response_model=PublicResult,
)
def publish(target: str, body: HarnessPublishRequest, request: Request):
    return invoke(
        "harness.publish",
        lambda workspace: publish_harness(
            PublishHarnessRequest(
                target=target,
                lock_id=body.lock_id,
                expected_revision=body.expected_revision,
                request_id=body.request_id,
            ),
            public_operation_context(workspace),
            _GenerationPublicationAdapter(request.app.state.session_service),
        ),
    )



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
