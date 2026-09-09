"""Trusted host adapters for the distinct author domain ports.

This module only demultiplexes the worker's typed service frames.  Provider,
tool/permission, and Session context state remain owned by their existing
owners; no universal effect ledger or author-visible client is created here.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, TYPE_CHECKING
from urllib.parse import urlparse

from breadboard.modules.author import (
    ContextSnapshot,
    CompactionProposal,
    EffectiveContextDocument,
    ToolApproval,
    ToolApprovalRequest,
    ToolCancelled,
    ToolFailed,
    ToolOutcome,
    ToolSucceeded,
    ToolUnknown,
    TurnPolicyDecision,
    TurnPolicyReceipt,
)
from breadboard.modules.authority import (
    AuthorityDeclaration,
    ChildAuthority,
    CredentialDisclosure,
    NetworkAuthority,
    NetworkOperation,
    ProjectAuthority,
    ProjectOperation,
    AdmissionGrant,
)
from breadboard.modules.provider import (
    CanonicalProviderExchangeCodec,
    ProviderCallRequest,
    ProviderCancelled,
    ProviderDone,
    ProviderErrorTerminal,
    ProviderEvent,
    ProviderExchangeHandle,
    ProviderRoute,
    ProviderStreamItem,
    ProviderUnknown,
)
from breadboard.modules.transport import RequestKey, decode_bytes, encode_bytes
from breadboard.product.runtime.events import Session
from breadboard_engine.conductor.tool_executor import (
    ToolExecutor,
    execute_agent_calls,
    build_exec_func,
)
from breadboard_engine.permissions.authority import PermissionAuthority, PermissionResolution
from breadboard_engine.permissions.broker import PermissionBroker
from breadboard_engine.permissions.policy_pack import PolicyPack
from breadboard_engine.provider.author_access import (
    ConductorProviderRuntimePreparer,
    ProviderAccess,
    ProviderAccessError,
)
from breadboard_engine.messaging.markdown_logger import MarkdownLogger
from breadboard_engine.state.session_state import SessionState
from breadboard_engine.tool_calling.ir import ToolCallIR

if TYPE_CHECKING:
    from .registry.records import SessionRecord


class AuthorDomainError(RuntimeError):
    """A typed owner refusal at an author domain seam."""

    def __init__(self, code: str, detail: str) -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}")


class AuthorDomainUnknown(RuntimeError):
    """The owner cannot establish an external operation's settlement."""

    def __init__(self, detail: str, evidence_refs: tuple[str, ...] = ()) -> None:
        self.detail, self.evidence_refs = detail, evidence_refs
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class EffectiveDomainScope:
    """Canonical intersection of requested and admitted authority."""

    project: ProjectAuthority | None
    network: NetworkAuthority | None
    child: ChildAuthority | None
    provider_ids: frozenset[str]
    tool_ids: frozenset[str]
    credential_disclosures: tuple[CredentialDisclosure, ...]

    @classmethod
    def from_grants(
        cls,
        requested_authority: Mapping[str, object] | AuthorityDeclaration,
        effective_grant: Mapping[str, object] | AuthorityDeclaration,
    ) -> "EffectiveDomainScope":
        requested = _authority_declaration(requested_authority, "requested authority")
        granted = _authority_declaration(effective_grant, "effective grant")
        return cls(
            project=_project_intersection(requested.project, granted.project),
            network=_network_intersection(requested.network, granted.network),
            child=_child_intersection(requested.child, granted.child),
            provider_ids=frozenset(requested.provider_ids & granted.provider_ids),
            tool_ids=frozenset(requested.tool_ids & granted.tool_ids),
            credential_disclosures=tuple(
                disclosure
                for disclosure in requested.credential_disclosures
                if disclosure in granted.credential_disclosures
            ),
        )

    def require_provider(self, call: ProviderCallRequest) -> None:
        if call.provider.provider_id not in self.provider_ids:
            raise AuthorDomainError(
                "authority_denied",
                "provider is not in the caller-supplied effective grant",
            )

    def require_tool(self, tool_id: str) -> None:
        if tool_id not in self.tool_ids:
            raise AuthorDomainError(
                "authority_denied",
                "tool is not in the caller-supplied effective grant",
            )

    def require_tool_call(self, call: ToolCallIR, workspace: Path) -> None:
        """Enforce canonical domain scope before invoking ToolExecutor.

        A tool ID alone cannot confine arbitrary shell/network behavior.  Such
        calls are refused rather than pretending the allowlist is a sandbox.
        """

        self.require_tool(str(call.function))
        tool_id = str(call.function).strip().lower()
        arguments = call.arguments if isinstance(call.arguments, Mapping) else {}
        if tool_id in _UNCONFINED_TOOLS:
            raise AuthorDomainError(
                "tool_scope_unenforceable",
                f"{call.function} can perform arbitrary project/network effects and has no owner scope enforcement",
            )
        if tool_id in _PROJECT_READ_TOOLS or tool_id in _PROJECT_WRITE_TOOLS:
            project = self.project
            if project is None:
                raise AuthorDomainError(
                    "authority_denied", "project authority is not admitted for this tool"
                )
            operation = (
                ProjectOperation.WRITE
                if tool_id in _PROJECT_WRITE_TOOLS
                else ProjectOperation.READ
            )
            if operation not in project.operations:
                raise AuthorDomainError(
                    "authority_denied",
                    f"project {operation.value} is not in the effective grant",
                )
            path = _tool_path(arguments)
            if path is None:
                raise AuthorDomainError(
                    "tool_scope_unenforceable",
                    "project tool did not provide an owner-checkable path",
                )
            if not _under_roots(path, workspace, project.roots):
                raise AuthorDomainError(
                    "authority_denied", "project path is outside the effective roots"
                )
        if tool_id in _NETWORK_TOOLS:
            network = self.network
            if network is None or NetworkOperation.CONNECT not in network.operations:
                raise AuthorDomainError(
                    "authority_denied", "network connect is not in the effective grant"
                )
            destination = _network_destination(arguments)
            if destination is None:
                raise AuthorDomainError(
                    "tool_scope_unenforceable",
                    "network tool did not provide an owner-checkable destination",
                )
            if not _destination_allowed(destination, network.destinations):
                raise AuthorDomainError(
                    "authority_denied", "network destination is outside the effective grant"
                )
        credential = _credential_name(arguments)
        if credential is not None and not any(
            disclosure.secret_name == credential
            for disclosure in self.credential_disclosures
        ):
            raise AuthorDomainError(
                "authority_denied",
                f"credential disclosure is not admitted for {credential}",
            )


@dataclass(frozen=True, slots=True)
class AuthorDomainQuiescence:
    """Owner references that remain unresolved at a lifecycle boundary."""

    provider_exchanges: tuple[str, ...] = ()
    tool_executions: tuple[str, ...] = ()
    approvals: tuple[str, ...] = ()

    @property
    def safe_to_dispose(self) -> bool:
        return not (self.provider_exchanges or self.tool_executions or self.approvals)


class ToolAccessAdapter:
    """ToolAccess facade over the actual ToolExecutor and PermissionBroker."""

    def __init__(
        self,
        *,
        executor: ToolExecutor | None = None,
        permission_authority: PermissionAuthority | None = None,
        session_state: SessionState | None = None,
        owner_factory: Callable[[], tuple[ToolExecutor, PermissionAuthority, SessionState]] | None = None,
        scope: EffectiveDomainScope,
        workspace: Path,
        fence: Callable[[], None] | None = None,
        output_schema_id: str = "bb.tool_result.json.v1",
    ) -> None:
        if executor is None and owner_factory is None:
            raise TypeError("tool owner requires an executor or owner_factory")
        self._executor = executor
        self._authority = permission_authority
        self._session_state = session_state
        self._owner_factory = owner_factory
        self._scope = scope
        self._workspace = workspace.resolve()
        self._fence = fence
        self._output_schema_id = output_schema_id
        self._pending: dict[str, tuple[ToolCallIR, ToolApproval]] = {}
        self._active: dict[str, ToolCallIR] = {}
        self._lock = threading.RLock()
        self._closed = False

    def _ensure_owner(self) -> None:
        if self._executor is not None and self._authority is not None and self._session_state is not None:
            return
        factory = self._owner_factory
        if factory is None:
            raise AuthorDomainError("tool_unavailable", "tool owner is not prepared")
        executor, authority, session_state = factory()
        self._executor, self._authority, self._session_state = executor, authority, session_state

    def request_approval(self, request: ToolApprovalRequest) -> ToolApproval:
        if not isinstance(request, ToolApprovalRequest):
            raise TypeError("tool approval requires ToolApprovalRequest")
        _fence(self._fence)
        with self._lock:
            if self._closed:
                raise AuthorDomainError("owner_closed", "tool owner is closed")
        self._ensure_owner()
        assert self._authority is not None and self._session_state is not None
        call = _tool_call(request)
        self._scope.require_tool_call(call, self._workspace)
        try:
            if isinstance(self._authority, PermissionBroker):
                resolution = self._authority.authorize_tool_call(self._session_state, call)
            else:
                self._authority.ensure_allowed(self._session_state, [call])
                resolution = PermissionResolution(
                    "always", "owner_allow", persistent=True, rule_decision="allow"
                )
        except AuthorDomainError:
            raise
        except Exception as error:
            resolution = PermissionResolution(
                "reject", "owner_error", rule_decision="allow", stop=True
            )
            detail = str(error)
        else:
            detail = resolution.token
        policy_decision = "deny" if resolution.rule_decision == "deny" else "allow"
        operator: str | None
        if policy_decision == "deny":
            operator, scope, persistent = None, None, False
        elif resolution.value == "reject":
            operator, scope, persistent = "reject", "request", False
        elif resolution.token.startswith("user_"):
            operator = "always" if resolution.value == "always" else "once"
            scope, persistent = (
                ("generation", True) if operator == "always" else ("request", False)
            )
        else:
            operator, scope, persistent = (
                None,
                ("generation" if resolution.persistent else "request"),
                resolution.persistent,
            )
        approval = ToolApproval(
            approval_request_id=request.approval_request_id,
            policy_decision=policy_decision,
            operator_decision=operator,
            scope=scope,
            persistent=persistent,
            reason=detail,
        )
        with self._lock:
            self._pending[str(request.request_id)] = (call, approval)
        return approval

    def execute(self, request: ToolApprovalRequest, approval: ToolApproval) -> ToolOutcome:
        if not isinstance(request, ToolApprovalRequest) or not isinstance(approval, ToolApproval):
            raise TypeError("tool execute requires typed request and approval")
        _fence(self._fence)
        self._ensure_owner()
        assert self._executor is not None and self._authority is not None and self._session_state is not None
        call = _tool_call(request)
        self._scope.require_tool_call(call, self._workspace)
        with self._lock:
            pending = self._pending.pop(str(request.request_id), None)
            if pending is None or pending[1] != approval:
                return ToolUnknown(
                    request_id=request.request_id,
                    reason="tool approval is not owned by this request",
                    evidence_refs=(f"request:{request.request_id}",),
                )
            if self._closed:
                return ToolUnknown(
                    request_id=request.request_id,
                    reason="tool owner is closed",
                    evidence_refs=(f"request:{request.request_id}",),
                )
            self._active[str(request.request_id)] = call
        try:
            if approval.policy_decision == "deny":
                return ToolFailed(request.request_id, "permission_denied", approval.reason)
            if approval.operator_decision == "reject":
                return ToolFailed(request.request_id, "operator_denied", approval.reason)
            _fence(self._fence)
            batch = self._executor.execute([call])
            if not batch.executed_results:
                detail = str(batch.execution_error or "tool execution produced no result")
                return ToolFailed(request.request_id, "tool_execution_failed", detail)
            _, output = batch.executed_results[0]
            shaped = self._executor.shape_results(batch.executed_results)[0]
            encoded = _canonical_json_bytes(output)
            if bool(shaped.get("failed")) or batch.execution_error is not None:
                detail = str(output.get("error") if isinstance(output, Mapping) else output)
                return ToolFailed(request.request_id, "tool_execution_failed", detail)
            return ToolSucceeded(request.request_id, self._output_schema_id, encoded)
        except AuthorDomainError:
            raise
        except Exception as error:
            return ToolUnknown(
                request_id=request.request_id,
                reason=f"tool settlement is unknown: {type(error).__name__}",
                evidence_refs=(f"request:{request.request_id}",),
            )
        finally:
            with self._lock:
                self._active.pop(str(request.request_id), None)
            if isinstance(self._authority, PermissionBroker) and approval.operator_decision == "once":
                self._authority.consume_one_shot_approval(self._session_state, call)

    def quiescence(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        with self._lock:
            return (
                tuple(f"tool:{request_id}" for request_id in sorted(self._active)),
                tuple(f"approval:{request_id}" for request_id in sorted(self._pending)),
            )

    def close(self, reason: str = "owner_closed") -> tuple[tuple[str, ...], tuple[str, ...]]:
        with self._lock:
            self._closed = True
            active = tuple(self._active)
        cancel = getattr(self._executor, "cancel", None)
        if callable(cancel):
            for request_id in active:
                try:
                    cancel(request_id, reason)
                except Exception:
                    pass
        return self.quiescence()


class TurnContextAccessAdapter:
    """TurnContextAccess facade backed by the durable Product Session owner."""

    def __init__(
        self,
        *,
        session: Session,
        session_lock: AbstractContextManager[object],
        persist_session: Callable[[], None],
        scope_fence: Callable[[], None] | None = None,
    ) -> None:
        self._session = session
        self._session_lock = session_lock
        self._persist_session = persist_session
        self._fence = scope_fence
    def snapshot(self) -> ContextSnapshot:
        _fence(self._fence)
        with _locked(self._session_lock):
            return self._session.context_snapshot()

    def propose(self, decision: TurnPolicyDecision) -> TurnPolicyReceipt:
        if not isinstance(decision, TurnPolicyDecision):
            raise TypeError("context proposal requires TurnPolicyDecision")
        _fence(self._fence)
        with _locked(self._session_lock):
            receipt = self._session.propose_turn_policy(decision)
            if receipt.applied:
                self._persist_session()
            return receipt
class _WorkerOwnerFactory:
    """Lazily prepares real conductor owners from the admitted inputs."""

    def __init__(
        self,
        *,
        config: Mapping[str, Any],
        workspace: Path,
        session: Session,
        session_record: "SessionRecord",
        current_input_id: Callable[[], str],
        current_turn_id: Callable[[], str],
    ) -> None:
        self._config = dict(config)
        self._workspace = workspace.resolve()
        self._session = session
        self._session_record = session_record
        self._current_turn_id = current_turn_id
        self._conductor: Any | None = None
        self._current_input_id = current_input_id
        self._session_state: SessionState | None = None
        self._tool_owner: tuple[ToolExecutor, PermissionAuthority, SessionState] | None = None
        self._lock = threading.RLock()

    def _ensure(self) -> tuple[Any, SessionState]:
        with self._lock:
            if self._conductor is not None and self._session_state is not None:
                return self._conductor, self._session_state
            try:
                from breadboard_engine.agent_llm_openai import OpenAIConductor
                from breadboard_engine.security import protected_credential_paths
                conductor_cls = OpenAIConductor.__ray_metadata__.modified_class
                conductor = conductor_cls(
                    workspace=str(self._workspace),
                    admitted_workspace=self._workspace,
                    config=dict(self._config),
                    local_mode=True,
                    prompt_base_dirs=[],
                    protected_paths=tuple(str(path) for path in protected_credential_paths()),
                )
                state = SessionState(
                    str(self._workspace),
                    str(self._config.get("image") or "python-dev:latest"),
                    dict(self._config),
                )
                state.set_provider_metadata("session_id", self._session_record.session_id)
                state.set_provider_metadata("input_id", self._current_input_id())
                state.set_provider_metadata("turn_id", self._current_turn_id())
            except AuthorDomainError:
                raise
            except Exception as error:
                raise AuthorDomainError(
                    "owner_unavailable",
                    f"canonical conductor owner could not be prepared: {type(error).__name__}",
                ) from error
            self._conductor, self._session_state = conductor, state
            return conductor, state
    def provider_preparer(
        self, *, input_id: str, turn_id: str
    ) -> ConductorProviderRuntimePreparer:
        conductor, state = self._ensure()
        state.set_provider_metadata("session_id", self._session_record.session_id)
        state.set_provider_metadata("input_id", input_id)
        state.set_provider_metadata("turn_id", turn_id)
        return ConductorProviderRuntimePreparer(
            conductor=conductor,
            config=self._config,
            session_state=state,
            markdown_logger=_markdown_logger(conductor),
            turn_index=lambda: _turn_index(state),
        )

    def tool_owner(self) -> tuple[ToolExecutor, PermissionAuthority, SessionState]:
        with self._lock:
            if self._tool_owner is not None:
                return self._tool_owner
            conductor, state = self._ensure()
            executor = ToolExecutor(
                conductor=conductor,
                session_state=state,
                exec_func=build_exec_func(conductor, state),
                execute_calls=execute_agent_calls,
            )
            authority = getattr(conductor, "permission_authority", None)
            if authority is None or not callable(getattr(authority, "ensure_allowed", None)):
                authority = getattr(conductor, "permission_broker", None)
            if authority is None or not callable(getattr(authority, "ensure_allowed", None)):
                permissions = self._config.get("permissions")
                authority = PermissionBroker(
                    permissions if isinstance(permissions, Mapping) else {},
                    policy_pack=PolicyPack.from_config(self._config),
                )
            self._tool_owner = (executor, authority, state)
            return self._tool_owner
 
 
class _LazyProviderPreparer:
    def __init__(self, factory: _WorkerOwnerFactory) -> None:
        self._factory = factory

    def prepare(
        self,
        call: ProviderCallRequest,
        correlation: Any,
        route: ProviderRoute,
        grant: AdmissionGrant,
        cancel_requested: Callable[[], bool],
    ):
        return self._factory.provider_preparer(
            input_id=correlation.input_id,
            turn_id=correlation.turn_id,
        ).prepare(call, correlation, route, grant, cancel_requested)
 





class AuthorDomainDispatcher:
    """Semantic host dispatcher for worker service messages."""

    def __init__(
        self,
        provider: ProviderAccess | None = None,
        tools: ToolAccessAdapter | None = None,
        context: TurnContextAccessAdapter | None = None,
        *,
        scope: EffectiveDomainScope | None = None,
        scope_fence: Callable[[], None] | None = None,
    ) -> None:
        self.provider, self.tools, self.context = provider, tools, context
        self._scope = scope
        self._scope_fence = scope_fence
        self._closed = False

    @classmethod
    def for_worker(
        cls,
        *,
        config: Mapping[str, Any],
        workspace: Path,
        session: Session,
        session_record: "SessionRecord",
        scope: EffectiveDomainScope,
        worker_key: RequestKey,
        current_turn_id: Callable[[], str],
        current_input_id: Callable[[], str],
        scope_fence: Callable[[], None],
        session_lock: AbstractContextManager[object],
        persist_session: Callable[[], None],
    ) -> "AuthorDomainDispatcher":
        """Bind one admitted worker to the existing domain owners.

        Construction only resolves owner references and creates facades.  It
        does not invoke providers, execute tools, or mutate Session state.
        """

        if not isinstance(config, Mapping):
            raise TypeError("config must be a mapping")
        if not isinstance(workspace, Path):
            raise TypeError("workspace must be a Path")
        if not isinstance(session, Session):
            raise TypeError("session must be a Product Session")
        if not isinstance(scope, EffectiveDomainScope):
            raise TypeError("scope must be EffectiveDomainScope")
        if not isinstance(worker_key, RequestKey):
            raise TypeError("worker_key must be RequestKey")
        if (
            not callable(current_input_id)
            or not callable(current_turn_id)
            or not callable(scope_fence)
            or not callable(persist_session)
        ):
            raise TypeError("worker callbacks must be callable")
        factory = _WorkerOwnerFactory(
            config=config,
            workspace=workspace,
            session=session,
            session_record=session_record,
            current_input_id=current_input_id,
            current_turn_id=current_turn_id,
        )
        provider: ProviderAccess | None = None
        grant = getattr(session_record, "module_grant", None)
        if isinstance(grant, AdmissionGrant):
            route = ProviderRoute.from_dict(worker_key.as_dict())
            provider = ProviderAccess.from_runtime_preparer(
                preparer=_LazyProviderPreparer(factory),
                worker_key=worker_key,
                session_id=session_record.session_id,
                current_turn_id=current_turn_id,
                current_input_id=current_input_id,
                route=route,
                grant=grant,
                fence=scope_fence,
            )
        tools = ToolAccessAdapter(
            owner_factory=factory.tool_owner,
            scope=scope,
            workspace=workspace,
            fence=scope_fence,
        )
        context = TurnContextAccessAdapter(
            session=session,
            session_lock=session_lock,
            persist_session=persist_session,
            scope_fence=scope_fence,
        )
        return cls(provider, tools, context, scope=scope, scope_fence=scope_fence)

    def dispatch(self, kind: str, body: Mapping[str, object]) -> dict[str, object]:
        if self._closed:
            raise AuthorDomainError("owner_closed", "author domain owner is closed")
        if not isinstance(body, Mapping):
            raise AuthorDomainError("malformed_request", "service body must be an object")
        _fence(self._scope_fence)
        if kind == "provider_request":
            return self._provider(body)
        if kind == "tool_request":
            return self._tool(body)
        if kind == "context_request":
            return self._context(body)
        raise AuthorDomainError("unsupported_request", f"unsupported author domain: {kind}")

    def quiescence(self) -> AuthorDomainQuiescence:
        provider_exchanges = self.provider.quiescence() if self.provider is not None else ()
        tool_executions: tuple[str, ...] = ()
        approvals: tuple[str, ...] = ()
        if self.tools is not None:
            tool_executions, approvals = self.tools.quiescence()
        return AuthorDomainQuiescence(provider_exchanges, tool_executions, approvals)

    def close(self, reason: str = "owner_closed") -> AuthorDomainQuiescence:
        if not isinstance(reason, str) or not reason.strip() or reason.strip() != reason:
            raise ValueError("close reason must be a non-empty string")
        self._closed = True
        provider_exchanges: tuple[str, ...] = ()
        tool_executions: tuple[str, ...] = ()
        approvals: tuple[str, ...] = ()
        if self.provider is not None:
            provider_exchanges = self.provider.close(reason)
        if self.tools is not None:
            tool_executions, approvals = self.tools.close(reason)
        return AuthorDomainQuiescence(provider_exchanges, tool_executions, approvals)

    def _provider(self, body: Mapping[str, object]) -> dict[str, object]:
        adapter = self.provider
        if adapter is None:
            raise AuthorDomainError("authority_denied", "provider dependency was not admitted")
        operation = body.get("operation")
        if operation == "start":
            if set(body) - {"request_id", "operation", "call"}:
                raise AuthorDomainError("malformed_request", "provider start has unknown fields")
            try:
                call = CanonicalProviderExchangeCodec.decode_call_request(
                    decode_bytes(body.get("call"))
                )
            except Exception as error:
                raise AuthorDomainError("malformed_request", "provider call is invalid") from error
            if self._scope is None:
                raise AuthorDomainError(
                    "authority_denied", "provider scope is unavailable"
                )
            self._scope.require_provider(call)
            handle = adapter.start(call)
            return {
                "exchange_id": handle.exchange_id,
                "stream_id": handle.stream_id,
                "route": handle.route.as_dict(),
            }
        if operation not in {"next_event", "cancel"}:
            raise AuthorDomainError("malformed_request", "unsupported provider operation")
        exchange_id = _text(body.get("exchange_id"), "exchange_id")
        stream_id = _text(body.get("stream_id"), "stream_id")
        handle = ProviderExchangeHandle(exchange_id, stream_id, adapter.route_for(exchange_id))
        if operation == "next_event":
            if set(body) - {"request_id", "operation", "exchange_id", "stream_id"}:
                raise AuthorDomainError("malformed_request", "provider next has unknown fields")
            return _provider_item(adapter.next_event(handle))
        if set(body) - {"request_id", "operation", "exchange_id", "stream_id", "reason"}:
            raise AuthorDomainError("malformed_request", "provider cancel has unknown fields")
        return _provider_item(adapter.cancel(handle, _text(body.get("reason"), "cancel reason")))

    def _tool(self, body: Mapping[str, object]) -> dict[str, object]:
        adapter = self.tools
        if adapter is None:
            raise AuthorDomainError("authority_denied", "tool dependency was not admitted")
        raw_request = body.get("request")
        request = _tool_request_from_wire(raw_request)
        operation = body.get("operation")
        if operation == "request_approval":
            return {"approval": _dataclass_dict(adapter.request_approval(request))}
        if operation == "execute":
            raw_approval = body.get("approval")
            approval = _tool_approval_from_wire(raw_approval)
            return {"outcome": _tool_outcome_dict(adapter.execute(request, approval))}
        raise AuthorDomainError("malformed_request", "unsupported tool operation")

    def _context(self, body: Mapping[str, object]) -> dict[str, object]:
        adapter = self.context
        if adapter is None:
            raise AuthorDomainError("authority_denied", "context dependency was not admitted")
        operation = body.get("operation")
        if operation == "snapshot":
            return {"snapshot": _context_snapshot_dict(adapter.snapshot())}
        if operation == "propose":
            decision = _turn_policy_from_wire(body.get("decision"))
            return {"receipt": _dataclass_dict(adapter.propose(decision))}
        raise AuthorDomainError("malformed_request", "unsupported context operation")


def _authority_declaration(value: Mapping[str, object] | AuthorityDeclaration, label: str) -> AuthorityDeclaration:
    if isinstance(value, AuthorityDeclaration):
        return value
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be an AuthorityDeclaration mapping")
    try:
        return AuthorityDeclaration.from_dict(value)
    except (TypeError, ValueError) as error:
        raise TypeError(f"{label} is not canonical") from error


def _project_intersection(left: ProjectAuthority | None, right: ProjectAuthority | None) -> ProjectAuthority | None:
    if left is None or right is None:
        return None
    return ProjectAuthority(
        roots=tuple(sorted(set(left.roots) & set(right.roots))),
        operations=frozenset(left.operations & right.operations),
    )


def _network_intersection(left: NetworkAuthority | None, right: NetworkAuthority | None) -> NetworkAuthority | None:
    if left is None or right is None:
        return None
    return NetworkAuthority(
        destinations=tuple(sorted(set(left.destinations) & set(right.destinations))),
        operations=frozenset(left.operations & right.operations),
    )


def _child_intersection(left: ChildAuthority | None, right: ChildAuthority | None) -> ChildAuthority | None:
    if left is None or right is None:
        return None
    return ChildAuthority(
        allowed_module_ids=frozenset(left.allowed_module_ids & right.allowed_module_ids),
        max_depth=min(left.max_depth, right.max_depth),
    )


def _grant_path(value: str, workspace: Path) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else workspace / path).resolve()


def _under_roots(value: str, workspace: Path, roots: tuple[str, ...]) -> bool:
    candidate = _grant_path(value, workspace)
    for root in roots:
        try:
            candidate.relative_to(_grant_path(root, workspace))
        except ValueError:
            continue
        return True
    return False


def _tool_path(arguments: Mapping[str, object]) -> str | None:
    for key in ("path", "file", "filename", "target"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _network_destination(arguments: Mapping[str, object]) -> str | None:
    for key in ("url", "uri", "destination", "host"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            parsed = urlparse(value)
            return (parsed.hostname or value).lower()
    return None


def _destination_allowed(destination: str, grants: tuple[str, ...]) -> bool:
    for grant in grants:
        normalized = grant.lower().strip()
        if normalized == destination or normalized == "*":
            return True
        if normalized.startswith("*.") and destination.endswith(normalized[1:]):
            return True
        if "://" in normalized:
            parsed = urlparse(normalized)
            if parsed.hostname == destination:
                return True
    return False


def _credential_name(arguments: Mapping[str, object]) -> str | None:
    for key in ("credential", "credential_id", "secret", "secret_name"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


_PROJECT_READ_TOOLS = frozenset({"read", "read_file", "glob", "grep", "list_files", "file_search"})
_PROJECT_WRITE_TOOLS = frozenset({"write", "write_file", "edit", "apply_patch", "apply_unified_patch", "patch", "create_file", "delete_file"})
_NETWORK_TOOLS = frozenset({"webfetch", "fetch", "http_request", "http_get", "network_request"})
_UNCONFINED_TOOLS = frozenset({"bash", "run_shell", "shell_command", "exec", "execute", "mcp", "mcp_call"})


def _session_state(runner: object, conductor: object) -> SessionState | None:
    for owner in (runner, conductor):
        candidate = getattr(owner, "session_state", None)
        if isinstance(candidate, SessionState):
            return candidate
        candidate = getattr(owner, "_active_session_state", None)
        if isinstance(candidate, SessionState):
            return candidate
    return None


def _turn_index(session_state: SessionState) -> int:
    value = session_state.get_provider_metadata("current_turn_index", 0)
    return value if type(value) is int and value >= 0 else 0


def _markdown_logger(conductor: object) -> MarkdownLogger:
    logger = getattr(conductor, "markdown_logger", None)
    if isinstance(logger, MarkdownLogger):
        return logger
    return MarkdownLogger(None)


def _fence(fence: Callable[[], None] | None) -> None:
    if fence is not None:
        fence()


@contextmanager
def _locked(lock: AbstractContextManager[object] | Callable[[], AbstractContextManager[object]]):
    context = lock() if callable(lock) and not hasattr(lock, "__enter__") else lock
    with context:
        yield


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value.strip() != value:
        raise AuthorDomainError("malformed_request", f"{label} must be a non-empty string")
    return value


def _tool_call(request: ToolApprovalRequest) -> ToolCallIR:
    try:
        parsed = json.loads(request.arguments_json)
    except (TypeError, ValueError) as error:
        raise AuthorDomainError("malformed_request", "tool arguments_json is not JSON") from error
    if not isinstance(parsed, dict) or parsed != request.arguments:
        raise AuthorDomainError("malformed_request", "tool argument JSON is not lossless")
    if _canonical_json_bytes(parsed).decode("utf-8") != request.arguments_json:
        raise AuthorDomainError("malformed_request", "tool argument JSON is not canonical")
    return ToolCallIR(
        function=request.tool_id,
        arguments=dict(parsed),
        provider_name=request.tool_id,
        call_id=str(request.request_id),
    )


def _tool_request_from_wire(value: object) -> ToolApprovalRequest:
    if not isinstance(value, Mapping):
        raise AuthorDomainError("malformed_request", "tool request is missing")
    required = {
        "request_id", "approval_request_id", "tool_id", "operation",
        "arguments_schema_id", "arguments_json", "arguments",
    }
    if set(value) != required:
        raise AuthorDomainError("malformed_request", "tool request fields are invalid")
    try:
        return ToolApprovalRequest(**dict(value))
    except (TypeError, ValueError) as error:
        raise AuthorDomainError("malformed_request", "tool request is invalid") from error


def _tool_approval_from_wire(value: object) -> ToolApproval:
    if not isinstance(value, Mapping):
        raise AuthorDomainError("malformed_request", "tool approval is missing")
    required = {
        "approval_request_id", "policy_decision", "operator_decision", "scope",
        "persistent", "reason",
    }
    if set(value) != required:
        raise AuthorDomainError("malformed_request", "tool approval fields are invalid")
    try:
        return ToolApproval(**dict(value))
    except (TypeError, ValueError) as error:
        raise AuthorDomainError("malformed_request", "tool approval is invalid") from error


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise AuthorDomainError("tool_failed", "tool result is not canonical JSON") from error


def _provider_item(item: ProviderStreamItem) -> dict[str, object]:
    if isinstance(item, ProviderEvent):
        return {"item_kind": "event", "event": item.as_dict()}
    if isinstance(item, ProviderDone):
        value = item.as_dict()
        value.pop("kind", None)
        return {"item_kind": "done", "terminal": value}
    if isinstance(item, ProviderErrorTerminal):
        value = item.as_dict()
        value.pop("kind", None)
        return {"item_kind": "error", "terminal": value}
    if isinstance(item, ProviderCancelled):
        value = item.as_dict()
        value.pop("kind", None)
        return {"item_kind": "cancelled", "terminal": value}
    if isinstance(item, ProviderUnknown):
        return {"item_kind": "unknown", "unknown": _dataclass_dict(item)}
    raise AuthorDomainError("provider_failed", "provider owner returned an unsupported item")


def _dataclass_dict(value: object) -> dict[str, object]:
    if not is_dataclass(value) or isinstance(value, type):
        raise TypeError("expected dataclass")
    result: dict[str, object] = {}
    for field in fields(value):
        item = getattr(value, field.name)
        if isinstance(item, bytes):
            result[field.name] = encode_bytes(item)
        elif isinstance(item, tuple):
            result[field.name] = [
                _dataclass_dict(child) if is_dataclass(child) and not isinstance(child, type) else child
                for child in item
            ]
        elif is_dataclass(item) and not isinstance(item, type):
            result[field.name] = _dataclass_dict(item)
        else:
            result[field.name] = item
    return result


def _tool_outcome_dict(outcome: ToolOutcome) -> dict[str, object]:
    if isinstance(outcome, ToolSucceeded):
        return {
            "kind": "succeeded",
            "request_id": outcome.request_id,
            "output_schema_id": outcome.output_schema_id,
            "output": encode_bytes(outcome.output),
        }
    if isinstance(outcome, ToolFailed):
        return {
            "kind": "failed",
            "request_id": outcome.request_id,
            "code": outcome.code,
            "detail": outcome.detail,
        }
    if isinstance(outcome, ToolCancelled):
        return {
            "kind": "cancelled",
            "request_id": outcome.request_id,
            "owner": outcome.owner,
            "reason": outcome.reason,
        }
    return {
        "kind": "unknown",
        "request_id": outcome.request_id,
        "reason": outcome.reason,
        "evidence_refs": list(outcome.evidence_refs),
    }


def _context_snapshot_dict(snapshot: ContextSnapshot) -> dict[str, object]:
    return {
        "session_id": snapshot.session_id,
        "context_id": snapshot.context_id,
        "session_event_sequence": snapshot.session_event_sequence,
        "effective_context": {
            "encoding": snapshot.effective_context.encoding,
            "body": encode_bytes(snapshot.effective_context.body),
            "context_sha256": snapshot.effective_context.context_sha256,
        },
        "raw_fact_ids": list(snapshot.raw_fact_ids),
        "shadowed_raw_fact_ids": list(snapshot.shadowed_raw_fact_ids),
        "source": _dataclass_dict(snapshot.source),
        "compaction_index": snapshot.compaction_index,
        "turn_index": snapshot.turn_index,
    }


def _turn_policy_from_wire(value: object) -> TurnPolicyDecision:
    if not isinstance(value, Mapping):
        raise AuthorDomainError("malformed_request", "turn policy decision is missing")
    raw = dict(value)
    proposal_raw = raw.get("compaction")
    proposal: CompactionProposal | None = None
    if proposal_raw is not None:
        if not isinstance(proposal_raw, Mapping):
            raise AuthorDomainError("malformed_request", "compaction proposal is invalid")
        document = proposal_raw.get("effective_context")
        if not isinstance(document, Mapping):
            raise AuthorDomainError("malformed_request", "effective context document is invalid")
        try:
            proposal = CompactionProposal(
                expected_context_sha256=_text(proposal_raw.get("expected_context_sha256"), "expected context hash"),
                compaction_index=proposal_raw["compaction_index"],
                source_sequence_start=proposal_raw["source_sequence_start"],
                source_sequence_end=proposal_raw["source_sequence_end"],
                effective_context=EffectiveContextDocument(
                    encoding=document["encoding"],
                    body=decode_bytes(document["body"]),
                    context_sha256=_text(document.get("context_sha256"), "context hash"),
                ),
                raw_fact_ids=tuple(proposal_raw["raw_fact_ids"]),
                shadowed_raw_fact_ids=tuple(proposal_raw["shadowed_raw_fact_ids"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise AuthorDomainError("malformed_request", "compaction proposal is invalid") from error
    try:
        return TurnPolicyDecision(
            kind=raw["kind"],
            reason=_text(raw.get("reason"), "turn policy reason"),
            expected_context_sha256=_text(raw.get("expected_context_sha256"), "expected context hash"),
            expected_context_sequence=raw["expected_context_sequence"],
            turn_index=raw.get("turn_index"),
            compaction=proposal,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise AuthorDomainError("malformed_request", "turn policy decision is invalid") from error


__all__ = [
    "AuthorDomainDispatcher",
    "AuthorDomainError",
    "AuthorDomainQuiescence",
    "AuthorDomainUnknown",
    "EffectiveDomainScope",
    "ToolAccessAdapter",
    "TurnContextAccessAdapter",
]
