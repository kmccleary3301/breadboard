from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from breadboard.modules import (
    AuthorityDeclaration,
    ModuleInput,
    NetworkAuthority,
    NetworkOperation,
    ProjectAuthority,
    ProjectOperation,
)
from breadboard.product.harness.compile import compile_harness_definition
from breadboard_engine.api.cli_bridge.author_domains import (
    AuthorDomainError,
    EffectiveDomainScope,
)
from breadboard_engine.api.cli_bridge.engine_identity_config import (
    EngineProcessIdentity,
    LaunchBootstrapVerifier,
)
from breadboard_engine.api.cli_bridge.models import (
    BeginControlDrainRequest,
    BootstrapChallengeRequest,
    DrainControlRequest,
    ClientRegisterRequest,
    GracefulControlResultRequest,
    HardSignalOutcomeRequest,
    HardSignalPrepareRequest,
    OwnerAcquireRequest,
    SessionCreateRequest,
)
from breadboard_engine.api.cli_bridge.registry import LifecycleAuthorityError, SessionRegistry
from breadboard_engine.api.cli_bridge.service import SessionService
from breadboard_engine.tool_calling.ir import ToolCallIR

BOOTSTRAP = b"bootstrap-proof-material-000000000000000000"
OWNER = b"owner-proof-material-0000000000000000000000"
REGISTRATION = b"registration-proof-material-00000000000000000"
WORKSPACE = "workspace:v1:sha256:" + "a" * 64
CLIENT = "client-instance-0001"


def _identity() -> EngineProcessIdentity:
    return EngineProcessIdentity(
        pid=12345,
        os_process_start_token="darwin:1000:0",
        engine_instance_id="i" * 43,
        engine_boot_id="b" * 43,
        launch_id="l" * 43,
        launch_source="supervisor",
        started_at=datetime.fromtimestamp(1_000.0, tz=timezone.utc),
        started_at_unix=1_000.0,
        engine_artifact_sha256="sha256:" + "c" * 64,
    )


def _verifier(identity: EngineProcessIdentity) -> LaunchBootstrapVerifier:
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, BOOTSTRAP)
    finally:
        os.close(write_fd)
    return LaunchBootstrapVerifier.from_inherited_fd(read_fd, identity)


def _field(value: bytes) -> bytes:
    return len(value).to_bytes(2, "big") + value


def _proof(identity: EngineProcessIdentity, challenge_id: str, challenge: str) -> str:
    binding = b"breadboard-p30-launch-bootstrap-v1\0" + b"".join(
        _field(value.encode("ascii"))
        for value in (identity.launch_id, identity.engine_boot_id, identity.engine_instance_id)
    ) + _field(BOOTSTRAP)
    key = hashlib.sha256(binding).digest()
    message = b"breadboard-p30-launch-bootstrap-proof-v1\0" + b"".join(
        _field(value.encode("ascii"))
        for value in (
            identity.launch_id,
            identity.engine_boot_id,
            identity.engine_instance_id,
            challenge_id,
            challenge,
            OWNER.decode("ascii"),
        )
    )
    return "sha256:" + hmac.new(key, message, hashlib.sha256).hexdigest()


def _binding(identity: EngineProcessIdentity) -> dict[str, str]:
    return {
        "engine_instance_id": identity.engine_instance_id,
        "engine_boot_id": identity.engine_boot_id,
        "launch_id": identity.launch_id,
    }


@pytest.mark.asyncio
async def test_bootstrap_proof_is_challenge_bound_one_use_and_secret_buffers_are_wiped() -> None:
    identity = _identity()
    registry = SessionRegistry(process_identity=identity, bootstrap_verifier=_verifier(identity))
    challenge = await registry.issue_bootstrap_challenge(BootstrapChallengeRequest(**_binding(identity)))
    owner = bytearray(OWNER)
    competing = await registry.issue_bootstrap_challenge(BootstrapChallengeRequest(**_binding(identity)))
    assert competing.challenge_id == challenge.challenge_id
    assert competing.challenge == challenge.challenge
    acquired = await registry.acquire_owner(
        OwnerAcquireRequest(
            **_binding(identity),
            expected_owner_generation=0,
            bootstrap_challenge_id=challenge.challenge_id,
            bootstrap_proof_sha256=_proof(identity, challenge.challenge_id, challenge.challenge),
        ),
        owner_credential=owner,
    )
    assert acquired.result == "acquired"
    assert owner == bytearray(len(owner))
    assert registry._bootstrap_verifier is not None
    assert registry._bootstrap_verifier.verifier_wiped is True

    with pytest.raises(LifecycleAuthorityError, match="owner generation"):
        await registry.issue_bootstrap_challenge(BootstrapChallengeRequest(**_binding(identity)))


def test_bootstrap_challenge_expires_at_the_exact_boundary() -> None:
    identity = _identity()
    verifier = _verifier(identity)
    issued = verifier.issue_challenge(identity, now=1_000.0)
    assert issued is not None
    challenge_id, challenge, expires_at = issued
    assert expires_at == 1_010.0
    assert verifier.consume_proof(
        challenge_id,
        _proof(identity, challenge_id, challenge),
        bytearray(OWNER),
        identity,
        now=expires_at,
    ) is False


@pytest.mark.asyncio
async def test_hard_signal_requires_live_process_authorization_before_recorded_outcome() -> None:
    identity = _identity()
    registry = SessionRegistry(process_identity=identity, bootstrap_verifier=_verifier(identity))
    challenge = await registry.issue_bootstrap_challenge(BootstrapChallengeRequest(**_binding(identity)))
    acquired = await registry.acquire_owner(
        OwnerAcquireRequest(
            **_binding(identity),
            expected_owner_generation=0,
            bootstrap_challenge_id=challenge.challenge_id,
            bootstrap_proof_sha256=_proof(identity, challenge.challenge_id, challenge.challenge),
        ),
        owner_credential=bytearray(OWNER),
    )
    registration = await registry.register_client(
        ClientRegisterRequest(
            engine_instance_id=identity.engine_instance_id,
            client_instance_id=CLIENT,
            workspace_id=WORKSPACE,
            lifecycle_mode="local-owned",
        ),
        registration_credential=bytearray(REGISTRATION),
    )
    drained = await registry.begin_control_drain(
        BeginControlDrainRequest(
            **_binding(identity),
            owner_generation=acquired.owner_generation,
            control_request_id="q" * 43,
            registration_id=registration.registration_id,
            requester_registration_generation=registration.registration_generation,
            requester_client_instance_id=registration.client_instance_id,
            expected_admission_epoch=registration.admission_epoch,
        ),
        owner_credential=bytearray(OWNER),
        registration_credential=bytearray(REGISTRATION),
    )
    pending = await registry.record_graceful_control(
        GracefulControlResultRequest(
            **_binding(identity),
            owner_generation=acquired.owner_generation,
            drain_generation=drained.drain_generation,
            outcome="timeout",
        ),
        owner_credential=bytearray(OWNER),
    )
    assert pending.result == "hard_signal_decision_pending"

    wrong = HardSignalPrepareRequest(
        **_binding(identity),
        owner_generation=acquired.owner_generation,
        drain_generation=drained.drain_generation,
        pid=identity.pid,
        os_process_start_token="darwin:wrong",
    )
    with pytest.raises(LifecycleAuthorityError, match="process proof"):
        await registry.prepare_hard_signal(wrong, owner_credential=bytearray(OWNER))

    authorization = await registry.prepare_hard_signal(
        wrong.model_copy(update={"os_process_start_token": identity.os_process_start_token}),
        owner_credential=bytearray(OWNER),
    )
    assert registry._owner is not None
    registry._owner.released = True
    reacquired = await registry.acquire_owner(
        OwnerAcquireRequest(**_binding(identity), expected_owner_generation=acquired.owner_generation),
        owner_credential=bytearray(OWNER),
    )
    stale_outcome = HardSignalOutcomeRequest(
        **_binding(identity),
        owner_generation=reacquired.owner_generation,
        drain_generation=drained.drain_generation,
        authorization_id=authorization.authorization_id,
        outcome="sent",
    )
    with pytest.raises(LifecycleAuthorityError) as stale:
        await registry.record_hard_signal_outcome(
            stale_outcome,
            owner_credential=bytearray(OWNER),
        )
    assert stale.value.code == "hard_signal_authorization_conflict"

    current_prepare = wrong.model_copy(update={
        "owner_generation": reacquired.owner_generation,
        "os_process_start_token": identity.os_process_start_token,
    })
    with pytest.raises(LifecycleAuthorityError) as replacement:
        await registry.prepare_hard_signal(
            current_prepare,
            owner_credential=bytearray(OWNER),
        )
    assert replacement.value.code == "hard_signal_authorization_conflict"

    with pytest.raises(LifecycleAuthorityError) as exited:
        await registry.record_hard_signal_outcome(
            stale_outcome.model_copy(update={"outcome": "process_exited"}),
            owner_credential=bytearray(OWNER),
        )
    assert exited.value.code == "hard_signal_authorization_conflict"
    with pytest.raises(LifecycleAuthorityError) as prior_generation:
        await registry.record_hard_signal_outcome(
            stale_outcome.model_copy(update={
                "owner_generation": acquired.owner_generation,
                "outcome": "abandoned",
            }),
            owner_credential=bytearray(OWNER),
        )
    assert prior_generation.value.code == "owner_generation_conflict"

    abandoned = stale_outcome.model_copy(update={"outcome": "abandoned"})
    with pytest.raises(LifecycleAuthorityError) as preexpiry_abandon:
        await registry.record_hard_signal_outcome(
            abandoned,
            owner_credential=bytearray(OWNER),
        )
    assert preexpiry_abandon.value.code == "hard_signal_authorization_conflict"
    with pytest.raises(LifecycleAuthorityError) as preexpiry_rollback:
        await registry.rollback_control_drain(
            DrainControlRequest(
                **_binding(identity),
                owner_generation=reacquired.owner_generation,
                drain_generation=drained.drain_generation,
            ),
            owner_credential=bytearray(OWNER),
        )
    assert preexpiry_rollback.value.code == "drain_recovery_failed"
    with pytest.raises(LifecycleAuthorityError) as foreign:
        await registry.record_hard_signal_outcome(
            abandoned,
            owner_credential=bytearray(b"foreign-owner-credential-material"),
        )
    assert foreign.value.code == "owner_identity_mismatch"
    assert registry.authority_snapshot()["drain_phase"] == (
        "hard_signal_decision_pending"
    )
    assert registry.admission_epoch == pending.admission_epoch


def _network_scope(
    requested_destinations: tuple[str, ...],
    granted_destinations: tuple[str, ...],
    *,
    workspace: Path,
) -> EffectiveDomainScope:
    operations = frozenset({NetworkOperation.CONNECT})
    return EffectiveDomainScope.from_grants(
        AuthorityDeclaration(
            network=NetworkAuthority(requested_destinations, operations),
            tool_ids=frozenset({"http_get"}),
        ),
        AuthorityDeclaration(
            network=NetworkAuthority(granted_destinations, operations),
            tool_ids=frozenset({"http_get"}),
        ),
        workspace=workspace,
    )


def _project_scope(
    requested_roots: tuple[str, ...],
    granted_roots: tuple[str, ...],
    *,
    workspace: Path,
    operations: frozenset[ProjectOperation] = frozenset(
        {ProjectOperation.READ, ProjectOperation.WRITE}
    ),
) -> EffectiveDomainScope:
    return EffectiveDomainScope.from_grants(
        AuthorityDeclaration(
            project=ProjectAuthority(requested_roots, operations),
            tool_ids=frozenset({"read", "write"}),
        ),
        AuthorityDeclaration(
            project=ProjectAuthority(granted_roots, operations),
            tool_ids=frozenset({"read", "write"}),
        ),
        workspace=workspace,
    )


@pytest.mark.parametrize(
    ("requested", "granted", "effective", "allowed", "denied"),
    [
        (
            ("API.Example.com",),
            ("*",),
            ("api.example.com",),
            "  API.Example.com  ",
            "other.example.com",
        ),
        (
            ("api.example.com",),
            ("*.EXAMPLE.com",),
            ("api.example.com",),
            "api.example.com",
            "other.example.com",
        ),
        (
            ("*.example.com",),
            ("api.example.com",),
            ("api.example.com",),
            "api.example.com",
            "other.example.com",
        ),
        (
            ("*.example.com",),
            ("*.internal.example.com",),
            ("*.internal.example.com",),
            "api.internal.example.com",
            "api.example.com",
        ),
    ],
)
def test_network_scope_uses_the_narrower_semantic_destination_intersection(
    tmp_path,
    requested: tuple[str, ...],
    granted: tuple[str, ...],
    effective: tuple[str, ...],
    allowed: str,
    denied: str,
) -> None:
    scope = _network_scope(requested, granted, workspace=tmp_path)

    assert scope.network is not None
    assert scope.network.destinations == effective
    scope.require_tool_call(
        ToolCallIR("http_get", {"host": allowed}),
        tmp_path,
    )
    with pytest.raises(AuthorDomainError) as refusal:
        scope.require_tool_call(
            ToolCallIR("http_get", {"host": denied}),
            tmp_path,
        )
    assert refusal.value.code == "authority_denied"


def test_network_scope_denies_unrelated_destination_patterns(tmp_path) -> None:
    scope = _network_scope(("api.example.com",), ("other.example.com",), workspace=tmp_path)

    assert scope.network is not None
    assert scope.network.destinations == ()
    with pytest.raises(AuthorDomainError) as refusal:
        scope.require_tool_call(
            ToolCallIR("http_get", {"host": "api.example.com"}),
            tmp_path,
        )
    assert refusal.value.code == "authority_denied"


def test_network_scope_refuses_malformed_url_authority(tmp_path) -> None:
    with pytest.raises(AuthorDomainError) as declaration:
        _network_scope(("http://[::1",), ("*",), workspace=tmp_path)
    assert declaration.value.code == "authority_denied"

    scope = _network_scope(("*",), ("*",), workspace=tmp_path)
    with pytest.raises(AuthorDomainError) as request:
        scope.require_tool_call(
            ToolCallIR("http_get", {"url": "http://[::1"}),
            tmp_path,
        )
    assert request.value.code == "tool_scope_unenforceable"


@pytest.mark.parametrize(
    ("requested", "granted"),
    [
        (("src/data",), (".",)),
        ((".",), ("src/data",)),
    ],
)
def test_project_scope_retains_narrower_root_containment(
    tmp_path: Path,
    requested: tuple[str, ...],
    granted: tuple[str, ...],
) -> None:
    data_dir = tmp_path / "src" / "data"
    data_dir.mkdir(parents=True)
    file_path = data_dir / "file.txt"
    file_path.write_text("ok")

    scope = _project_scope(requested, granted, workspace=tmp_path)

    scope.require_tool_call(
        ToolCallIR("read", {"path": "src/data/file.txt"}),
        tmp_path,
    )
    scope.require_tool_call(
        ToolCallIR("write", {"path": "src/data/file.txt"}),
        tmp_path,
    )

    with pytest.raises(AuthorDomainError) as refusal:
        scope.require_tool_call(
            ToolCallIR("read", {"path": "src/sibling.txt"}),
            tmp_path,
        )
    assert refusal.value.code == "authority_denied"

    with pytest.raises(AuthorDomainError) as refusal:
        scope.require_tool_call(
            ToolCallIR("read", {"path": "../outside.txt"}),
            tmp_path,
        )
    assert refusal.value.code == "authority_denied"


def test_project_scope_resolves_symlinks_and_equivalents(tmp_path: Path) -> None:
    real_data = tmp_path / "src" / "data"
    real_data.mkdir(parents=True)
    (real_data / "file.txt").write_text("payload")
    symlink_dir = tmp_path / "link_data"
    symlink_dir.symlink_to(real_data, target_is_directory=True)

    symlink_scope = _project_scope(("link_data",), (".",), workspace=tmp_path)

    symlink_scope.require_tool_call(
        ToolCallIR("read", {"path": "link_data/file.txt"}),
        tmp_path,
    )
    symlink_scope.require_tool_call(
        ToolCallIR("write", {"path": "src/data/file.txt"}),
        tmp_path,
    )

    with pytest.raises(AuthorDomainError) as refusal:
        symlink_scope.require_tool_call(
            ToolCallIR("read", {"path": "src/other.txt"}),
            tmp_path,
        )
    assert refusal.value.code == "authority_denied"

    absolute_root = str(real_data.resolve())
    absolute_scope = _project_scope((absolute_root,), (".",), workspace=tmp_path)
    absolute_scope.require_tool_call(
        ToolCallIR("read", {"path": "src/data/file.txt"}),
        tmp_path,
    )

    unrelated_scope = _project_scope(("src",), ("docs",), workspace=tmp_path)
    with pytest.raises(AuthorDomainError) as refusal:
        unrelated_scope.require_tool_call(
            ToolCallIR("read", {"path": "src/file.txt"}),
            tmp_path,
        )
    assert refusal.value.code == "authority_denied"

    outside_dir = tmp_path.parent / f"outside-{tmp_path.name}"
    outside_dir.mkdir(exist_ok=True)
    (outside_dir / "file.txt").write_text("outside grant")
    symlink_dir.unlink()
    symlink_dir.symlink_to(outside_dir, target_is_directory=True)
    with pytest.raises(AuthorDomainError) as refusal:
        symlink_scope.require_tool_call(
            ToolCallIR("read", {"path": "link_data/file.txt"}),
            tmp_path,
        )
    assert refusal.value.code == "authority_denied"
    real_data.rename(real_data.with_name("admitted_data"))
    real_data.symlink_to(outside_dir, target_is_directory=True)
    with pytest.raises(AuthorDomainError) as refusal:
        absolute_scope.require_tool_call(
            ToolCallIR("read", {"path": "src/data/file.txt"}),
            tmp_path,
        )
    assert refusal.value.code == "authority_denied"
    escape_symlink = tmp_path / "escape_link"
    escape_symlink.symlink_to(outside_dir, target_is_directory=True)

    escape_scope = _project_scope(("escape_link",), (".",), workspace=tmp_path)
    with pytest.raises(AuthorDomainError) as refusal:
        escape_scope.require_tool_call(
            ToolCallIR("read", {"path": "escape_link/file.txt"}),
            tmp_path,
        )
    assert refusal.value.code == "authority_denied"


@pytest.mark.asyncio
async def test_pre_record_failure_releases_generation_admission_for_session_id_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("BREADBOARD_ENGINE_LAUNCH_ID", raising=False)
    service = SessionService(state_root=tmp_path / "session-state")
    lock = compile_harness_definition(
        {"name": "data-only"},
        source_ref="config.yaml",
    ).lock
    session_id = "pre-record-failure"

    for body in (b'{"attempt":1}', b'{"attempt":2}'):
        with pytest.raises(ValueError, match="data Locks require text input"):
            await service.create_session(
                SessionCreateRequest(
                    task="",
                    module_input=ModuleInput("bb.test.input.v1", body),
                ),
                session_id=session_id,
                generation_workspace=tmp_path,
                effective_lock=lock,
                effective_lock_source=tmp_path / "config.yaml",
            )

    assert await service.registry.get(session_id) is None
    projection = service.generation_lifecycle(tmp_path).inspect_generation(
        lock.generation_id
    )
    assert len(projection["admissions"]) == 2
    assert {
        (admission["session_id"], admission["status"])
        for admission in projection["admissions"]
    } == {(session_id, "released")}
    assert projection["retirement"]["pinned_session_count"] == 0
