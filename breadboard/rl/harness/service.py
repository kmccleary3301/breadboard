from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import quote

from breadboard.rl.harness.contracts import (
    AtomicEpisodeRunRequest,
    EpisodeCreateRequest,
    EpisodeCreateResponse,
    EpisodeRunRequest,
    EpisodeRunResponse,
    EpisodeStateResponse,
    SCHEMA_VERSION,
)
from breadboard.rl.harness.policy import PolicyClient
from breadboard.rl.harness.profiles import (
    HarnessProfile,
    HarnessProfileRegistry,
    is_content_digest,
)
from breadboard.rl.harness.sandbox import SandboxLease, SandboxLeaseManager
from breadboard.rl.state.cas import FilesystemCAS
from breadboard.rl.state.state_ref import ArtifactRef


class HarnessEpisodeError(RuntimeError):
    pass


class EpisodeNotFound(HarnessEpisodeError):
    pass


class EpisodeConflict(HarnessEpisodeError):
    pass


class EpisodeCancelled(HarnessEpisodeError):
    pass


class PolicyGenerator(Protocol):
    async def generate(self, request_payload: Mapping[str, Any]) -> dict[str, Any]: ...


class ArtifactStore(Protocol):
    def put_bytes(
        self,
        data: bytes,
        *,
        artifact_id: str | None = None,
        media_type: str = "application/octet-stream",
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRef: ...

    def get_ref(self, artifact_id: str) -> ArtifactRef: ...

    def get_bytes(self, artifact_ref: ArtifactRef | str) -> bytes: ...
    def has(self, artifact_ref: ArtifactRef | str) -> bool: ...


PolicyFactory = Callable[[str], PolicyGenerator]


@dataclass
class _EpisodeRecord:
    create_request: EpisodeCreateRequest
    profile: HarnessProfile
    image_digest: str
    verifier_ref: str
    verifier_command: str
    lease: SandboxLease
    state: str = "ready"
    reason: str = ""
    result: EpisodeRunResponse | None = None
    run_fingerprint: str | None = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    run_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass(frozen=True)
class _PendingCreate:
    request: EpisodeCreateRequest
    future: asyncio.Future[_EpisodeRecord | _EpisodeTombstone]


@dataclass(frozen=True)
class _EpisodeTombstone:
    create_fingerprint: str
    run_fingerprint: str
    result_artifact_id: str
    sandbox_attestation: dict[str, Any]
    cleanup_state: str


class BreadBoardEpisodeService:
    def __init__(
        self,
        *,
        profiles: HarnessProfileRegistry,
        lease_manager: SandboxLeaseManager | None = None,
        artifact_store: ArtifactStore | None = None,
        policy_factory: PolicyFactory | None = None,
        tombstone_limit: int | None = None,
    ) -> None:
        self.profiles = profiles
        self.lease_manager = lease_manager or SandboxLeaseManager()
        if artifact_store is None:
            artifact_root = os.environ.get("BREADBOARD_HARNESS_ARTIFACT_ROOT")
            resolved_root = (
                Path(artifact_root).expanduser()
                if artifact_root
                else Path.home() / ".breadboard" / "rl-harness" / "artifacts"
            )
            artifact_store = FilesystemCAS(resolved_root)
        self.artifact_store = artifact_store
        self.policy_factory = policy_factory or (
            lambda base_url: PolicyClient(base_url)
        )
        self._episodes: dict[str, _EpisodeRecord] = {}
        self._pending_creates: dict[str, _PendingCreate] = {}
        self._cancelled_creates: dict[str, str] = {}
        self._tombstones: OrderedDict[str, _EpisodeTombstone] = OrderedDict()
        self._tombstone_limit = int(
            tombstone_limit
            if tombstone_limit is not None
            else os.environ.get("BREADBOARD_HARNESS_TOMBSTONE_LIMIT", "1024")
        )
        if self._tombstone_limit < 1:
            raise ValueError("tombstone_limit must be positive")
        self._episodes_lock = asyncio.Lock()
        self._closed = False

    async def create_episode(
        self, request: EpisodeCreateRequest
    ) -> EpisodeCreateResponse:
        if self._closed:
            raise EpisodeConflict("BreadBoard episode service is closed")
        if request.task.repository_snapshot_digest and not is_content_digest(
            request.task.repository_snapshot_digest
        ):
            raise ValueError("repository_snapshot_digest must be a sha256 digest")
        profile = self.profiles.get(request.profile)
        image_digest = profile.admit_image(
            request.task.sandbox_image_digest,
            request.task.repository_snapshot_digest,
        )
        verifier_ref, verifier_command = profile.verifier_command(
            request.task.verifier_ref
        )
        create_fingerprint = _model_fingerprint(request)

        async with self._episodes_lock:
            tombstone = self._tombstones.get(request.episode_id)
            if tombstone is not None:
                if tombstone.create_fingerprint != create_fingerprint:
                    raise EpisodeConflict(
                        f"episode_id {request.episode_id!r} already has a different specification"
                    )
                return self._create_response_from_tombstone(
                    request.episode_id, tombstone
                )
            existing = self._episodes.get(request.episode_id)
            if existing is not None:
                if existing.create_request != request:
                    raise EpisodeConflict(
                        f"episode_id {request.episode_id!r} already has a different specification"
                    )
                return self._create_response(existing)
            pending = self._pending_creates.get(request.episode_id)
            if pending is not None:
                if pending.request != request:
                    raise EpisodeConflict(
                        f"episode_id {request.episode_id!r} already has a different specification"
                    )
                pending_future = pending.future
                owns_initialization = False
            else:
                pending_future = asyncio.get_running_loop().create_future()
                pending_future.add_done_callback(
                    lambda done: None if done.cancelled() else done.exception()
                )
                self._pending_creates[request.episode_id] = _PendingCreate(
                    request, pending_future
                )
                owns_initialization = True

        if not owns_initialization:
            resolved = await asyncio.shield(pending_future)
            if isinstance(resolved, _EpisodeTombstone):
                return self._create_response_from_tombstone(
                    request.episode_id, resolved
                )
            return self._create_response(resolved)

        lease: SandboxLease | None = None
        try:
            tombstone = await self._tombstone_for(request.episode_id)
            if tombstone is not None:
                if tombstone.create_fingerprint != create_fingerprint:
                    raise EpisodeConflict(
                        f"episode_id {request.episode_id!r} already has a different specification"
                    )
                async with self._episodes_lock:
                    pending = self._pending_creates.get(request.episode_id)
                    if (
                        self._closed
                        or pending is None
                        or pending.future is not pending_future
                        or pending_future.done()
                    ):
                        raise EpisodeCancelled(
                            "BreadBoard episode service is shutting down"
                        )
                    self._pending_creates.pop(request.episode_id, None)
                    self._cancelled_creates.pop(request.episode_id, None)
                    pending_future.set_result(tombstone)
                return self._create_response_from_tombstone(
                    request.episode_id, tombstone
                )
            lease = await self.lease_manager.open(
                episode_id=request.episode_id,
                driver=profile.sandbox_driver,
                image_digest=image_digest,
                network=profile.network,
            )
            async with self._episodes_lock:
                cancelled_reason = self._cancelled_creates.get(request.episode_id)
            if cancelled_reason:
                raise EpisodeCancelled(cancelled_reason)
            for command in profile.setup_commands:
                async with self._episodes_lock:
                    cancelled_reason = self._cancelled_creates.get(request.episode_id)
                if cancelled_reason:
                    raise EpisodeCancelled(cancelled_reason)
                setup_result = await lease.run_shell(
                    command, timeout=profile.action_timeout_seconds
                )
                if int(setup_result.get("exit", 1)) != 0:
                    raise HarnessEpisodeError(
                        f"profile setup command failed with exit {setup_result.get('exit')}: "
                        f"{str(setup_result.get('stderr') or '')[:2000]}"
                    )
            record = _EpisodeRecord(
                create_request=request,
                profile=profile,
                image_digest=image_digest,
                verifier_ref=verifier_ref,
                verifier_command=verifier_command,
                lease=lease,
            )
            async with self._episodes_lock:
                if self._closed:
                    raise EpisodeCancelled(
                        "BreadBoard episode service is shutting down"
                    )
                cancelled_reason = self._cancelled_creates.get(request.episode_id)
                if cancelled_reason:
                    raise EpisodeCancelled(cancelled_reason)
                self._episodes[request.episode_id] = record
                self._pending_creates.pop(request.episode_id, None)
                self._cancelled_creates.pop(request.episode_id, None)
                pending_future.set_result(record)
            return self._create_response(record)
        except BaseException as exc:
            cleanup_error: Exception | None = None
            if lease is not None:
                try:
                    await lease.close()
                except Exception as cleanup_exc:
                    cleanup_error = cleanup_exc
            resolved_error: BaseException = cleanup_error or exc
            async with self._episodes_lock:
                self._episodes.pop(request.episode_id, None)
                self._pending_creates.pop(request.episode_id, None)
                self._cancelled_creates.pop(request.episode_id, None)
                if not pending_future.done():
                    pending_future.set_exception(resolved_error)
            if cleanup_error is not None:
                raise cleanup_error from exc
            raise

    async def run_episode(
        self, episode_id: str, request: EpisodeRunRequest
    ) -> EpisodeRunResponse:
        run_fingerprint = _model_fingerprint(request)
        tombstone = await self._tombstone_for(episode_id)
        async with self._episodes_lock:
            record = self._episodes.get(episode_id)
            tombstone = self._tombstones.get(episode_id) or tombstone
        if record is None:
            if tombstone is None:
                raise EpisodeNotFound(episode_id)
            if tombstone.run_fingerprint != run_fingerprint:
                raise EpisodeConflict(
                    f"episode_id {episode_id!r} already has a different run request"
                )
            return await self._load_tombstone_result(tombstone)

        async with record.run_lock:
            if (
                record.run_fingerprint is not None
                and record.run_fingerprint != run_fingerprint
            ):
                raise EpisodeConflict(
                    f"episode_id {episode_id!r} already has a different run request"
                )
            if record.result is not None:
                return record.result
            record.run_fingerprint = run_fingerprint
            if record.state not in {"ready", "running"}:
                raise EpisodeConflict(
                    f"episode {episode_id!r} cannot run from state {record.state!r}"
                )
            if _contains_policy_visible_image(
                request.responses_create_params.get("input")
            ):
                error = ValueError(
                    "policy-visible image input is unsupported by the token-only policy bridge"
                )
                record.state = "failed"
                record.reason = str(error)
                await record.lease.close()
                raise error

            record.state = "running"
            policy = self.policy_factory(request.policy.base_url)
            try:
                result = await self._run_loop(record, request, policy)
            except EpisodeCancelled as exc:
                record.state = "cancelled"
                record.reason = str(exc)
                raise
            except Exception as exc:
                await record.lease.close()
                if record.cancel_event.is_set():
                    record.state = "cancelled"
                    record.reason = record.reason or str(exc)
                    raise EpisodeCancelled(record.reason) from exc
                record.state = "failed"
                record.reason = str(exc)
                raise
            record.result = result
            record.state = "completed"
            await self._persist_tombstone(record, cleanup_state="active")
            return result

    async def run_atomic(self, request: AtomicEpisodeRunRequest) -> EpisodeRunResponse:
        if _contains_policy_visible_image(request.responses_create_params.get("input")):
            raise ValueError(
                "policy-visible image input is unsupported by the token-only policy bridge"
            )
        create_request = EpisodeCreateRequest(
            schema_version=request.schema_version,
            episode_id=request.episode_id,
            profile=request.profile,
            task=request.task,
        )
        run_request = EpisodeRunRequest(
            responses_create_params=request.responses_create_params,
            policy=request.policy,
        )
        await self.create_episode(create_request)
        try:
            return await self.run_episode(request.episode_id, run_request)
        finally:
            await self.close_episode(request.episode_id)

    async def cancel_episode(
        self, episode_id: str, reason: str = "cancel requested"
    ) -> EpisodeStateResponse:
        tombstone = await self._tombstone_for(episode_id)
        async with self._episodes_lock:
            record = self._episodes.get(episode_id)
            tombstone = self._tombstones.get(episode_id) or tombstone
            pending = self._pending_creates.get(episode_id)
            if pending is not None:
                self._cancelled_creates[episode_id] = reason
        if record is None:
            if tombstone is not None:
                if tombstone.cleanup_state == "closed":
                    return EpisodeStateResponse(episode_id=episode_id, state="closed")
                raise HarnessEpisodeError(
                    f"episode {episode_id!r} completed but cleanup is not confirmed"
                )
            if pending is not None:
                return EpisodeStateResponse(
                    episode_id=episode_id, state="cancel_requested", reason=reason
                )
            raise EpisodeNotFound(episode_id)
        record.cancel_event.set()
        record.reason = reason
        if record.state not in {"completed", "closed", "failed"}:
            record.state = "cancelled"
        closed_attestation = await record.lease.close()
        if record.result is not None:
            record.result.sandbox_attestation = closed_attestation
            await self._persist_tombstone(record, cleanup_state="closed")
            async with self._episodes_lock:
                self._episodes.pop(episode_id, None)
            return EpisodeStateResponse(
                episode_id=episode_id, state="closed", reason=reason
            )
        return self._state_response(record)

    async def close_episode(self, episode_id: str) -> EpisodeStateResponse:
        tombstone = await self._tombstone_for(episode_id)
        async with self._episodes_lock:
            record = self._episodes.get(episode_id)
            tombstone = self._tombstones.get(episode_id) or tombstone
        if record is None:
            if tombstone is not None:
                if tombstone.cleanup_state == "closed":
                    return EpisodeStateResponse(episode_id=episode_id, state="closed")
                raise HarnessEpisodeError(
                    f"episode {episode_id!r} result was recovered but sandbox cleanup cannot be confirmed"
                )
            raise EpisodeNotFound(episode_id)

        closed_attestation = await record.lease.close()
        if record.result is not None:
            record.result.sandbox_attestation = closed_attestation
            await self._persist_tombstone(record, cleanup_state="closed")
        async with self._episodes_lock:
            self._episodes.pop(episode_id, None)
        return EpisodeStateResponse(
            episode_id=episode_id, state="closed", reason=record.reason
        )

    async def status(self, episode_id: str) -> EpisodeStateResponse:
        tombstone = await self._tombstone_for(episode_id)
        async with self._episodes_lock:
            record = self._episodes.get(episode_id)
            tombstone = self._tombstones.get(episode_id) or tombstone
        if record is not None:
            return self._state_response(record)
        if tombstone is not None:
            state = "closed" if tombstone.cleanup_state == "closed" else "completed"
            reason = (
                ""
                if tombstone.cleanup_state == "closed"
                else "sandbox cleanup is not confirmed"
            )
            return EpisodeStateResponse(
                episode_id=episode_id, state=state, reason=reason
            )
        raise EpisodeNotFound(episode_id)

    async def artifact(self, artifact_id: str) -> tuple[ArtifactRef, bytes]:
        try:
            ref = await asyncio.to_thread(self.artifact_store.get_ref, artifact_id)
            payload = await asyncio.to_thread(self.artifact_store.get_bytes, ref)
        except (KeyError, FileNotFoundError) as exc:
            raise EpisodeNotFound(f"artifact {artifact_id!r}") from exc
        return ref, payload

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        async with self._episodes_lock:
            records = list(self._episodes.values())
            pending = list(self._pending_creates.values())
            self._pending_creates.clear()
        shutdown_error = EpisodeCancelled("BreadBoard episode service is shutting down")
        for item in pending:
            if not item.future.done():
                item.future.set_exception(shutdown_error)
        for record in records:
            if record.state not in {"completed", "closed", "failed", "cancelled"}:
                record.reason = str(shutdown_error)
                record.state = "cancelled"
                record.cancel_event.set()
        results = await asyncio.gather(
            *(record.lease.close() for record in records),
            return_exceptions=True,
        )
        await self.lease_manager.close()
        cleanup_errors = [
            result for result in results if isinstance(result, BaseException)
        ]
        if cleanup_errors:
            raise HarnessEpisodeError(
                f"failed to close {len(cleanup_errors)} sandbox lease(s): {cleanup_errors[0]}"
            )

    async def _run_loop(
        self,
        record: _EpisodeRecord,
        request: EpisodeRunRequest,
        policy: PolicyGenerator,
    ) -> EpisodeRunResponse:
        profile = record.profile
        original_params = copy.deepcopy(request.responses_create_params)
        original_input = original_params.get("input", [])
        if isinstance(original_input, str):
            base_input: list[Any] = [{"role": "user", "content": original_input}]
        elif isinstance(original_input, list):
            base_input = copy.deepcopy(original_input)
        else:
            raise ValueError("responses_create_params.input must be a string or list")

        model_params = copy.deepcopy(original_params)
        model_params["tools"] = copy.deepcopy(profile.tool_definitions())
        model_params["parallel_tool_calls"] = False
        all_outputs: list[dict[str, Any]] = []
        trajectory: list[dict[str, Any]] = []
        last_response: dict[str, Any] | None = None
        termination_reason = "max_turns"
        turns = 0

        for turn in range(1, profile.max_turns + 1):
            self._raise_if_cancelled(record)
            turns = turn
            turn_request = model_params.copy()
            turn_request["input"] = [*base_input, *all_outputs]
            policy_response = await policy.generate(turn_request)
            self._raise_if_cancelled(record)
            raw_outputs = policy_response.get("output")
            if not isinstance(raw_outputs, list):
                raise HarnessEpisodeError("policy response output must be a list")
            normalized_outputs: list[dict[str, Any]] = []
            for index, item in enumerate(raw_outputs):
                if not isinstance(item, Mapping):
                    raise HarnessEpisodeError(
                        f"policy response output[{index}] must be an object"
                    )
                normalized_outputs.append(copy.deepcopy(dict(item)))
            all_outputs.extend(normalized_outputs)
            last_response = copy.deepcopy(policy_response)
            trajectory.append(
                {"turn": turn, "policy_output": copy.deepcopy(normalized_outputs)}
            )

            if policy_response.get("incomplete_details"):
                termination_reason = "policy_incomplete"
                break

            calls = [
                item
                for item in normalized_outputs
                if item.get("type") == "function_call"
            ]
            assistant_messages = [
                item
                for item in normalized_outputs
                if item.get("type") == "message" and item.get("role") == "assistant"
            ]
            if not calls:
                if not assistant_messages:
                    termination_reason = "invalid_policy_output"
                else:
                    termination_reason = "assistant_complete"
                break

            submitted = False
            observations: list[dict[str, Any]] = []
            for call in calls:
                self._raise_if_cancelled(record)
                observation, is_submit = await self._execute_action(record, call)
                observations.append(observation)
                all_outputs.append(observation)
                submitted = submitted or is_submit
            trajectory[-1]["observations"] = copy.deepcopy(observations)
            if submitted:
                termination_reason = "submitted"
                break

        if last_response is None:
            raise HarnessEpisodeError(
                "episode ended before the policy produced a response"
            )
        self._raise_if_cancelled(record)

        verifier_result, verifier_attestation = await self._run_verifier(record)
        reward = 1.0 if int(verifier_result.get("exit", 1)) == 0 else 0.0
        reward_components = {"verifier": reward}
        response = copy.deepcopy(last_response)
        response["output"] = all_outputs

        transcript = {
            "schema_version": SCHEMA_VERSION,
            "episode_id": record.create_request.episode_id,
            "profile": profile.name,
            "task_id": record.create_request.task.task_id,
            "termination_reason": termination_reason,
            "turns": turns,
            "trajectory": trajectory,
            "verifier": {
                "verifier_ref": record.verifier_ref,
                "result": _truncate_mapping(
                    verifier_result, profile.max_observation_chars
                ),
            },
            "sandbox_attestation": record.lease.attestation(),
            "verifier_attestation": verifier_attestation,
        }
        transcript_bytes = json.dumps(
            transcript, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        transcript_ref = await asyncio.to_thread(
            self.artifact_store.put_bytes,
            transcript_bytes,
            artifact_id=f"{record.create_request.episode_id}:trajectory",
            media_type="application/json",
            metadata={"kind": "breadboard_harness_trajectory"},
        )
        artifact_refs = [_artifact_ref_payload(transcript_ref)]
        artifact_refs.extend(await self._collect_artifacts(record))

        return EpisodeRunResponse(
            episode_id=record.create_request.episode_id,
            responses_create_params=original_params,
            response=response,
            reward=reward,
            reward_components=reward_components,
            termination_reason=termination_reason,
            turns=turns,
            artifact_refs=artifact_refs,
            sandbox_attestation=record.lease.attestation(),
            verifier_attestation=verifier_attestation,
            task_id=record.create_request.task.task_id,
            profile=profile.name,
            verifier_ref=record.verifier_ref,
            repository_snapshot_digest=record.create_request.task.repository_snapshot_digest,
        )

    async def _execute_action(
        self,
        record: _EpisodeRecord,
        call: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        profile = record.profile
        call_id = str(call.get("call_id") or call.get("id") or "").strip()
        name = str(call.get("name") or "").strip()
        if not call_id:
            call_id = "missing-call-id"
        try:
            raw_arguments = call.get("arguments", "{}")
            if not isinstance(raw_arguments, str):
                raise ValueError("arguments must be a JSON string")
            arguments = json.loads(raw_arguments)
            if not isinstance(arguments, dict):
                raise ValueError("arguments JSON must decode to an object")
            submitted = False
            if name == "shell":
                command = _required_text(arguments, "command")
                requested_timeout = int(
                    arguments.get("timeout_seconds", profile.action_timeout_seconds)
                )
                timeout = max(1, min(requested_timeout, profile.action_timeout_seconds))
                result = await record.lease.run_shell(command, timeout=timeout)
            elif name == "read_file":
                result = await record.lease.read_text(
                    _required_text(arguments, "path"),
                    offset=max(0, int(arguments.get("offset", 0))),
                    limit=max(0, int(arguments["limit"]))
                    if arguments.get("limit") is not None
                    else None,
                )
            elif name == "write_file":
                result = await record.lease.write_text(
                    _required_text(arguments, "path"),
                    str(arguments.get("content") or ""),
                )
            elif name == "list_files":
                depth = max(1, min(int(arguments.get("depth", 1)), 8))
                result = await record.lease.list_files(
                    _required_text(arguments, "path"), depth=depth
                )
            elif name == "submit":
                result = {
                    "accepted": True,
                    "result": str(arguments.get("result") or ""),
                }
                submitted = True
            else:
                raise ValueError(
                    f"tool {name!r} is not admitted by profile {profile.name!r}"
                )
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"}
            submitted = False

        output = _json_observation(result, profile.max_observation_chars)
        return {
            "type": "function_call_output",
            "call_id": call_id,
            "output": output,
        }, submitted

    async def _run_verifier(
        self, record: _EpisodeRecord
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        profile = record.profile
        verifier_lease = await self.lease_manager.open(
            episode_id=f"{record.create_request.episode_id}-verifier",
            driver=profile.sandbox_driver,
            image_digest=record.image_digest,
            network=profile.network,
            copy_from=record.lease.workspace,
        )
        try:
            result = await verifier_lease.run_shell(
                record.verifier_command,
                timeout=profile.verifier_timeout_seconds,
            )
        finally:
            attestation = await verifier_lease.close()
        return result, attestation

    async def _collect_artifacts(self, record: _EpisodeRecord) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        for index, path in enumerate(record.profile.artifact_paths):
            stat = await record.lease.stat(path)
            if not stat.get("exists") or stat.get("type") != "file":
                continue
            size = int(stat.get("size") or 0)
            if size > record.profile.max_artifact_bytes:
                continue
            payload = await record.lease.get_bytes(path)
            if len(payload) > record.profile.max_artifact_bytes:
                continue
            ref = await asyncio.to_thread(
                self.artifact_store.put_bytes,
                payload,
                artifact_id=f"{record.create_request.episode_id}:workspace:{index}",
                metadata={"kind": "workspace_file", "relative_path": path},
            )
            refs.append(_artifact_ref_payload(ref))
        return refs

    async def _tombstone_for(self, episode_id: str) -> _EpisodeTombstone | None:
        tombstone = self._tombstones.get(episode_id)
        if tombstone is not None:
            self._tombstones.move_to_end(episode_id)
            return tombstone
        candidates = (
            (f"{episode_id}:episode-tombstone", "closed"),
            (f"{episode_id}:episode-tombstone-completed", "active"),
        )
        for artifact_id, expected_cleanup_state in candidates:
            if not await asyncio.to_thread(self.artifact_store.has, artifact_id):
                continue
            try:
                raw = await asyncio.to_thread(
                    self.artifact_store.get_bytes, artifact_id
                )
                payload = json.loads(raw)
                if not isinstance(payload, Mapping):
                    raise ValueError("tombstone must be an object")
                if (
                    payload.get("schema_version") != SCHEMA_VERSION
                    or payload.get("episode_id") != episode_id
                ):
                    raise ValueError("identity or schema mismatch")
                cleanup_state = str(
                    payload.get("cleanup_state") or expected_cleanup_state
                )
                if cleanup_state not in {"active", "closed"}:
                    raise ValueError("invalid cleanup state")
                tombstone = _EpisodeTombstone(
                    create_fingerprint=str(payload["create_fingerprint"]),
                    run_fingerprint=str(payload["run_fingerprint"]),
                    result_artifact_id=artifact_id,
                    sandbox_attestation=dict(payload["sandbox_attestation"]),
                    cleanup_state=cleanup_state,
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise HarnessEpisodeError(
                    f"episode {episode_id!r} has a corrupt durable tombstone"
                ) from exc
            self._remember_tombstone(episode_id, tombstone)
            return tombstone
        return None

    def _remember_tombstone(
        self, episode_id: str, tombstone: _EpisodeTombstone
    ) -> None:
        existing = self._tombstones.get(episode_id)
        if (
            existing is not None
            and existing.cleanup_state == "closed"
            and tombstone.cleanup_state != "closed"
        ):
            return
        self._tombstones[episode_id] = tombstone
        self._tombstones.move_to_end(episode_id)
        while len(self._tombstones) > self._tombstone_limit:
            self._tombstones.popitem(last=False)

    async def _persist_tombstone(
        self, record: _EpisodeRecord, *, cleanup_state: str
    ) -> None:
        if record.result is None or record.run_fingerprint is None:
            raise EpisodeConflict("completed episode is missing its result fingerprint")
        if cleanup_state not in {"active", "closed"}:
            raise ValueError(f"invalid cleanup state {cleanup_state!r}")
        suffix = "" if cleanup_state == "closed" else "-completed"
        artifact_id = f"{record.create_request.episode_id}:episode-tombstone{suffix}"
        tombstone = _EpisodeTombstone(
            create_fingerprint=_model_fingerprint(record.create_request),
            run_fingerprint=record.run_fingerprint,
            result_artifact_id=artifact_id,
            sandbox_attestation=dict(record.result.sandbox_attestation),
            cleanup_state=cleanup_state,
        )
        recovery_payload = {
            "schema_version": SCHEMA_VERSION,
            "episode_id": record.create_request.episode_id,
            "create_fingerprint": tombstone.create_fingerprint,
            "run_fingerprint": tombstone.run_fingerprint,
            "sandbox_attestation": tombstone.sandbox_attestation,
            "cleanup_state": cleanup_state,
            "result": record.result.model_dump(mode="json"),
        }
        await asyncio.to_thread(
            self.artifact_store.put_bytes,
            json.dumps(recovery_payload, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            ),
            artifact_id=artifact_id,
            media_type="application/json",
            metadata={
                "kind": "breadboard_harness_episode_recovery",
                "cleanup_state": cleanup_state,
            },
        )
        self._remember_tombstone(record.create_request.episode_id, tombstone)

    async def _load_tombstone_result(
        self, tombstone: _EpisodeTombstone
    ) -> EpisodeRunResponse:
        raw = await asyncio.to_thread(
            self.artifact_store.get_bytes, tombstone.result_artifact_id
        )
        try:
            payload = json.loads(raw)
            if not isinstance(payload, Mapping) or not isinstance(
                payload.get("result"), Mapping
            ):
                raise ValueError("recovery result is missing")
            return EpisodeRunResponse.model_validate(payload["result"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HarnessEpisodeError("episode recovery result is corrupt") from exc

    @staticmethod
    def _create_response_from_tombstone(
        episode_id: str,
        tombstone: _EpisodeTombstone,
    ) -> EpisodeCreateResponse:
        return EpisodeCreateResponse(
            episode_id=episode_id,
            state="closed" if tombstone.cleanup_state == "closed" else "completed",
            sandbox_attestation=dict(tombstone.sandbox_attestation),
        )

    @staticmethod
    def _create_response(record: _EpisodeRecord) -> EpisodeCreateResponse:
        return EpisodeCreateResponse(
            episode_id=record.create_request.episode_id,
            state=record.state,
            sandbox_attestation=record.lease.attestation(),
        )

    @staticmethod
    def _state_response(record: _EpisodeRecord) -> EpisodeStateResponse:
        return EpisodeStateResponse(
            episode_id=record.create_request.episode_id,
            state=record.state,
            reason=record.reason,
        )

    @staticmethod
    def _raise_if_cancelled(record: _EpisodeRecord) -> None:
        if record.cancel_event.is_set() or record.lease.closed:
            raise EpisodeCancelled(record.reason or "episode cancelled")


def _model_fingerprint(value: Any) -> str:
    payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _artifact_ref_payload(ref: ArtifactRef) -> dict[str, Any]:
    payload = ref.to_dict()
    payload["retrieval_path"] = f"/v1/artifacts/{quote(ref.artifact_id, safe='')}"
    return payload


def _required_text(arguments: Mapping[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _json_observation(result: Mapping[str, Any], limit: int) -> str:
    raw = json.dumps(dict(result), sort_keys=True, separators=(",", ":"), default=str)
    if len(raw) <= limit:
        return raw
    return json.dumps(
        {"truncated": True, "preview": raw[: max(0, limit - 64)]}, separators=(",", ":")
    )


def _truncate_mapping(value: Mapping[str, Any], limit: int) -> dict[str, Any]:
    raw = _json_observation(value, limit)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"truncated": True}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _contains_policy_visible_image(value: Any) -> bool:
    allowed_types = {
        "message",
        "input_text",
        "output_text",
        "text",
        "function_call",
        "reasoning",
        "summary_text",
        "function_call_output",
        "tool_call",
        "tool_result",
    }
    if isinstance(value, str):
        return value.strip().lower().startswith("data:")
    if isinstance(value, Mapping):
        item_type = value.get("type")
        if (
            isinstance(item_type, str)
            and item_type.strip().lower() not in allowed_types
        ):
            return True
        forbidden_keys = {
            "image",
            "image_url",
            "screenshot",
            "screenshot_url",
            "file_data",
            "file_url",
            "audio",
            "video",
        }
        if any(str(key).lower() in forbidden_keys for key in value):
            return True
        return any(_contains_policy_visible_image(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_policy_visible_image(item) for item in value)
    return False


__all__ = [
    "BreadBoardEpisodeService",
    "EpisodeCancelled",
    "EpisodeConflict",
    "EpisodeNotFound",
    "HarnessEpisodeError",
]
