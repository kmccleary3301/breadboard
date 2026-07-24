from __future__ import annotations
import json
import hashlib
import os
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from breadboard.product.cli.result import CliResult, from_exception
from breadboard.product.runtime.events import ProcessLock
_FAMILIES = frozenset({"artifact", "harness", "harness_lock", "integration", "session", "system"})
_REPO_ROOT = Path(__file__).resolve().parents[3]
_MAINTAINER_ROOTS = (_REPO_ROOT / "contracts", _REPO_ROOT / "docs", _REPO_ROOT.parent / "docs_tmp")
_STATUS_BY_EXIT = {2: 422, 3: 404, 4: 500, 5: 409, 6: 409}
_ASYNC_OPERATIONS = frozenset(
    {"integration.probe", "session.approve", "session.cancel", "session.resume", "session.send_input", "session.start"}
)
def _operation_contract() -> tuple[frozenset[str], frozenset[tuple[str, re.Pattern[str]]]]:
    document = json.loads((_REPO_ROOT / "contracts/public/operations.v1.json").read_text())
    operation_ids: set[str] = set()
    routes: set[tuple[str, re.Pattern[str]]] = set()
    for operation in document.get("operations", []):
        operation_id = str(operation.get("operation_id", ""))
        binding = operation.get("bindings", {}).get("openapi", {})
        if operation_id.split(".", 1)[0] not in _FAMILIES or binding.get("status") != "candidate":
            continue
        operation_ids.add(operation_id)
        path = re.escape(str(binding["path"]))
        pattern = re.sub(r"\\\{[^}]+\\\}", r"[^/]+", path)
        routes.add((str(binding["method"]).upper(), re.compile(f"^{pattern}$")))
    return frozenset(operation_ids), frozenset(routes)
PUBLIC_OPERATION_IDS, PUBLIC_OPERATION_ROUTES = _operation_contract()
def is_public_operation_request(method: str, path: str, operation_id: str | None = None) -> bool:
    return operation_id in PUBLIC_OPERATION_IDS or any(candidate == method.upper() and pattern.fullmatch(path) for candidate, pattern in PUBLIC_OPERATION_ROUTES)
class Problem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["bb.problem.v1"] = "bb.problem.v1"
    error_code: str
    message: str
    record_refs: list[str] = Field(default_factory=list)
    failed_stage: str | None = None
    hint: str | None = None
    next_actions: list[str] = Field(default_factory=list)
class StageOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: str
    status: str
    report_ref: str | None = None
    next_action: str | None = None
class PublicResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["bb.cli.result.v1"] = "bb.cli.result.v1"
    ok: bool
    status: Literal["ok", "error"]
    command: list[str]
    record_refs: list[str]
    hashes: dict[str, str]
    stage_outcomes: list[StageOutcome]
    warnings: list[str]
    next_actions: list[str]
    error: Problem | None
    exit_code: int
    data: dict[str, Any]
class HarnessCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    directory: str = "."
class HarnessUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    definition: dict[str, Any]
class SessionStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lock_id: str = Field(min_length=1)
    task: str = Field(min_length=1)
    session_id: str | None = Field(default=None, min_length=1)
class SessionInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1)
class SessionApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1)
    decision: Literal["allow", "deny", "once", "always", "reject"]
class SessionCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="operator request", min_length=1)
def public_workspace() -> Path:
    configured = Path(os.environ.get("BREADBOARD_PUBLIC_WORKSPACE", Path.cwd())).expanduser()
    if configured.is_symlink():
        raise ValueError("public workspace cannot be a symlink")
    workspace = configured.resolve()
    if not workspace.is_dir():
        raise FileNotFoundError(f"public workspace is unavailable: {workspace.name}")
    if any(workspace == root or workspace.is_relative_to(root) for root in _MAINTAINER_ROOTS):
        raise PermissionError("maintainer evidence trees cannot be public workspaces")
    return workspace
def workspace_path(reference: str, workspace: Path) -> Path:
    relative = Path(reference)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("resource identifier must be workspace-relative")
    candidate = workspace.joinpath(relative)
    if any(workspace.joinpath(*relative.parts[:index]).is_symlink() for index in range(1, len(relative.parts) + 1)):
        raise ValueError("resource identifier cannot traverse a symlink")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(workspace) or any(resolved == root or resolved.is_relative_to(root) for root in _MAINTAINER_ROOTS):
        raise PermissionError("public operations cannot address maintainer evidence trees")
    return resolved
def _secret_values() -> tuple[str, ...]:
    markers = ("SECRET", "TOKEN", "PASSWORD", "API_KEY", "CREDENTIAL")
    return tuple(value for name, value in os.environ.items() if value and any(marker in name.upper() for marker in markers))
def _scrub(value: Any, workspace: Path | None, secrets: Sequence[str]) -> Any:
    if isinstance(value, str):
        text = value.replace(str(workspace), ".") if workspace is not None else value
        for secret in secrets:
            text = text.replace(secret, "<redacted>") if len(secret) >= 4 else re.sub(rf"(?<!\w){re.escape(secret)}(?!\w)", "<redacted>", text)
        return text
    if isinstance(value, Mapping):
        return {str(key): _scrub(item, workspace, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item, workspace, secrets) for item in value]
    return value
def scrub_public(value: Any, workspace: Path | None = None) -> Any:
    return _scrub(value, workspace, _secret_values())
def result_response(
    result: CliResult,
    *,
    workspace: Path | None = None,
    operation_id: str | None = None,
) -> JSONResponse:
    content = _scrub(result.as_dict(), workspace, _secret_values())
    PublicResult.model_validate(content)
    status_code = 202 if result.ok and operation_id in _ASYNC_OPERATIONS else 200
    return JSONResponse(status_code=status_code if result.ok else _STATUS_BY_EXIT.get(result.exit_code, 400), content=content)
def _operation_identity(operation_id: str) -> tuple[list[str], str]:
    command = [part.replace("_", "-") for part in operation_id.split(".")]
    return command, ".".join(command)
def invoke(operation_id: str, function: Callable[[Path], CliResult]) -> JSONResponse:
    workspace: Path | None = None
    try:
        workspace = public_workspace()
        result = function(workspace)
    except Exception as error:
        command, stage = _operation_identity(operation_id)
        result = from_exception(command, error, stage)
    return result_response(result, workspace=workspace, operation_id=operation_id)
def _write_idempotency_record(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.urandom(8).hex()}.tmp")
    descriptor = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
def invoke_idempotent(
    operation_id: str,
    idempotency_key: str | None,
    canonical_input: Mapping[str, Any],
    function: Callable[[Path], CliResult],
) -> JSONResponse:
    if not idempotency_key:
        return problem_response(operation_id, 422, "idempotency_key_required", "Idempotency-Key is required")
    workspace: Path | None = None
    try:
        workspace = public_workspace()
        encoded = json.dumps(canonical_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        input_sha256 = "sha256:" + hashlib.sha256(encoded).hexdigest()
        bucket = hashlib.sha256(f"{operation_id}\0{idempotency_key}".encode()).hexdigest()
        directory = workspace_path(".breadboard/public_api/idempotency", workspace)
        directory.mkdir(parents=True, exist_ok=True)
        record_path = workspace_path(str(directory.joinpath(f"{bucket}.json").relative_to(workspace)), workspace)
        with ProcessLock(record_path):
            if record_path.exists():
                record = json.loads(record_path.read_text())
                if record.get("input_sha256") != input_sha256:
                    return problem_response(operation_id, 409, "idempotency_conflict", "Idempotency-Key was used with different input")
                cached = scrub_public(record["result"], workspace)
                PublicResult.model_validate(cached); return JSONResponse(status_code=202, content=cached)
            result = function(workspace)
            if result.ok:
                content = scrub_public(result.as_dict(), workspace)
                PublicResult.model_validate(content)
                record = json.dumps({"input_sha256": input_sha256, "result": content}, sort_keys=True).encode()
                _write_idempotency_record(record_path, record)
                return JSONResponse(status_code=202, content=content)
    except Exception as error:
        command, stage = _operation_identity(operation_id)
        result = from_exception(command, error, stage)
    return result_response(result, workspace=workspace, operation_id=operation_id)
def problem_response(operation_id: str, status_code: int, error_code: str, message: str) -> JSONResponse:
    exit_code = {404: 3, 409: 6, 422: 2}.get(status_code, 4 if status_code >= 500 else 2)
    command, stage = _operation_identity(operation_id)
    result = CliResult.failure(command, exit_code, error_code, message, stage)
    return JSONResponse(status_code=status_code, content=_scrub(result.as_dict(), None, _secret_values()))
