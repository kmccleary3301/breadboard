"""Ray actor execution for durable children."""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from breadboard.product.runtime._child_state import ChildActivation, ChildError, ChildSpec, ExecutionTarget
from breadboard.product.runtime.artifacts import ArtifactRef, ArtifactStore


class RayJobAdapter:
    family = "ray-agent-job"
    absence_is_terminal = True
    _actor_namespace_prefix = "breadboard-durable-children-v1"

    def __init__(self, orchestrator: Any, *, actor_launcher: Any | None = None) -> None:
        self.orchestrator = orchestrator
        self._default_actor_launcher = actor_launcher is None
        self._actor_launcher = self._launch_actor if self._default_actor_launcher else actor_launcher
        self._actors: dict[tuple[str, str], Any] = {}
        self._actor_lookup_unavailable: set[tuple[str, str]] = set()
        self._released_actor_ids: set[tuple[str, str]] = set()
        self._workspace: Path | None = None

    def bind_workspace(self, workspace: Path) -> None:
        self._workspace = workspace

    def _target_workspace(self, target: Mapping[str, Any]) -> Path | None:
        metadata = target.get("metadata")
        job_data = metadata.get("job") if isinstance(metadata, Mapping) else None
        workspace = job_data.get("workspace") if isinstance(job_data, Mapping) else None
        if isinstance(workspace, str) and workspace.strip():
            return Path(workspace).expanduser().resolve()
        return self._workspace

    @staticmethod
    def _actor_key(job_id: str, workspace: Path) -> tuple[str, str]:
        return (str(workspace.expanduser().resolve()), job_id)

    def _actor_lookup_is_unavailable(
        self, job_id: str, workspace: Path | None
    ) -> bool:
        return workspace is None or self._actor_key(
            job_id, workspace
        ) in self._actor_lookup_unavailable

    @staticmethod
    def _actor_name(job_id: str) -> str:
        return "bb-child-" + job_id.replace(":", "_")

    @classmethod
    def _actor_namespace(cls, workspace: Path) -> str:
        workspace_digest = hashlib.sha256(
            str(workspace.expanduser().resolve()).encode("utf-8")
        ).hexdigest()
        return f"{cls._actor_namespace_prefix}-{workspace_digest[:32]}"

    @classmethod
    def _manager_job_id(cls, job_id: str, workspace: Path | None) -> str:
        if workspace is None:
            return job_id
        return f"{cls._actor_namespace(workspace)}:{job_id}"

    @staticmethod
    def _invocation_id(job_id: str) -> str:
        return "child-invocation:" + job_id

    @staticmethod
    def _submit_invocation(actor: Any, invocation_id: str, task: str) -> bool:
        submit = getattr(actor, "submit_message_once", None)
        if submit is None:
            return False
        parts = [{"type": "text", "text": task}]
        remote = getattr(submit, "remote", None)
        if callable(remote):
            remote(invocation_id, parts)
            deadline = time.monotonic() + 30.0
            while True:
                state = RayJobAdapter._invocation_state(actor, invocation_id)
                if state not in {None, "missing"}:
                    break
                if time.monotonic() >= deadline:
                    raise ChildError(
                        "Ray child invocation was not durably accepted"
                    )
                time.sleep(0.01)
        else:
            submit(invocation_id, parts)
        return True

    @staticmethod
    def _invocation_state(actor: Any, invocation_id: str) -> str | None:
        getter = getattr(actor, "get_invocation_state", None)
        if getter is None:
            return None
        remote = getattr(getter, "remote", None)
        try:
            value = remote(invocation_id) if callable(remote) else getter(invocation_id)
            if callable(remote):
                import ray
                value = ray.get(value)
        except BaseException:
            return None
        return str(value) if isinstance(value, str) else None

    def _launch_actor(
        self, job_id: str, workspace: Path, task: str, artifact_store_root: str | None = None
    ) -> Any:
        import ray
        from breadboard_engine.orchestration.agent_session import OpenCodeAgent

        name = self._actor_name(job_id)
        try:
            actor = ray.get_actor(
                name,
                namespace=self._actor_namespace(workspace),
            )
        except ValueError:
            root = artifact_store_root or str(workspace / ".breadboard" / "artifacts")
            actor = OpenCodeAgent.options(
                name=name,
                namespace=self._actor_namespace(workspace),
                lifetime="detached",
            ).remote(str(workspace), artifact_store_root=root)
        self._submit_invocation(actor, self._invocation_id(job_id), task)
        return actor

    def _lookup_actor(self, job_id: str, workspace: Path | None) -> Any | None:
        if workspace is None:
            return None
        key = self._actor_key(job_id, workspace)
        actor = self._actors.get(key)
        if actor is not None:
            return actor
        try:
            import ray

            actor = ray.get_actor(
                self._actor_name(job_id),
                namespace=self._actor_namespace(workspace),
            )
        except (ImportError, RuntimeError):
            self._actor_lookup_unavailable.add(key)
            return None
        except ValueError:
            self._actor_lookup_unavailable.discard(key)
            return None
        self._actor_lookup_unavailable.discard(key)
        self._actors[key] = actor
        return actor

    def _refresh_actor_after_rpc_failure(
        self, job_id: str, failed_actor: Any, workspace: Path | None
    ) -> Any | None:
        if workspace is not None:
            key = self._actor_key(job_id, workspace)
            if self._actors.get(key) is failed_actor:
                self._actors.pop(key, None)
        try:
            return self._lookup_actor(job_id, workspace)
        except BaseException:
            return None

    def recover(self, target: Mapping[str, Any]) -> ExecutionTarget | None:
        job_id = str(target.get("ref", "")).removeprefix("job:")
        workspace = self._target_workspace(target)
        manager_job_id = self._manager_job_id(job_id, workspace)
        actor = self._lookup_actor(job_id, workspace)
        invocation_missing = actor is not None and self._invocation_state(actor, self._invocation_id(job_id)) == "missing"
        if invocation_missing:
            actor = None
        job = self.orchestrator.job_manager.get(manager_job_id)
        metadata = target.get("metadata")
        if not isinstance(metadata, Mapping):
            return None
        job_data = metadata.get("job")
        if not isinstance(job_data, Mapping) or job_data.get("job_id") != job_id:
            return None
        if invocation_missing or (actor is None and (job is None or job.state not in {"completed", "failed", "killed"})):
            return None
        return ExecutionTarget(str(target.get("ref") or ""), volatile_handle=actor, metadata=dict(metadata))


    def _restore_job(self, target: Mapping[str, Any], job_id: str) -> None:
        metadata = target.get("metadata")
        job_data = metadata.get("job") if isinstance(metadata, Mapping) else None
        if not isinstance(job_data, Mapping) or job_data.get("job_id") != job_id:
            return
        workspace = self._target_workspace(target)
        manager_job_id = self._manager_job_id(job_id, workspace)
        if self.orchestrator.job_manager.get(manager_job_id) is not None:
            return
        from breadboard_engine.orchestration.job_manager import JobRef

        try:
            job = JobRef(
                job_id=manager_job_id,
                agent_id=str(job_data["agent_id"]),
                owner_agent=str(job_data["owner_agent"]),
                kind=str(job_data["kind"]),
                state=(
                    "accepted"
                    if str(job_data.get("state") or "accepted") == "completed"
                    else str(job_data.get("state") or "accepted")
                ),
                seq=int(job_data.get("seq") or 0),
                task_descriptor=dict(job_data.get("task_descriptor") or {}),
                result_payload=dict(job_data["result_payload"])
                if isinstance(job_data.get("result_payload"), Mapping)
                else None,
            )
        except (KeyError, TypeError, ValueError):
            return
        self.orchestrator.job_manager.restore_job(job)

    def release_terminal(self, target: Mapping[str, Any]) -> bool:
        job_id = str(target.get("ref", "")).removeprefix("job:")
        workspace = self._target_workspace(target)
        if workspace is None:
            return False
        key = self._actor_key(job_id, workspace)
        if key in self._released_actor_ids:
            return True
        actor = self._actors.pop(key, None)
        is_actor_handle = False
        if actor is None:
            actor = self._lookup_actor(job_id, workspace)
            self._actors.pop(key, None)
        if actor is None:
            if self._actor_lookup_is_unavailable(job_id, workspace):
                return False
            self._released_actor_ids.add(key)
            return True
        try:
            import ray

            is_actor_handle = isinstance(actor, ray.actor.ActorHandle)
            ray.kill(actor, no_restart=True)
        except BaseException:
            if is_actor_handle:
                return False
        self._released_actor_ids.add(key)
        return True

    def _mark_job_failed(self, target: Mapping[str, Any], job_id: str) -> None:
        manager_job_id = self._manager_job_id(job_id, self._target_workspace(target))
        marked = self.orchestrator.mark_job_failed(manager_job_id)
        metadata = target.get("metadata")
        job_data = metadata.get("job") if isinstance(metadata, Mapping) else None
        if isinstance(job_data, dict):
            job_data["state"] = "failed"
            if marked is not None:
                job_data["seq"] = marked.seq
        self.release_terminal(target)


    @staticmethod
    def _ray_get(value: Any) -> Any:
        if hasattr(value, "remote"):
            import ray
            return ray.get(value.remote())
        return value() if callable(value) else value
    def _artifact_store(self, target: Mapping[str, Any]) -> ArtifactStore:
        metadata = target.get("metadata")
        job_data = metadata.get("job") if isinstance(metadata, Mapping) else None
        root = job_data.get("artifact_store_root") if isinstance(job_data, Mapping) else None
        if not isinstance(root, str) or not root.strip():
            workspace = job_data.get("workspace") if isinstance(job_data, Mapping) else None
            root = str(Path(workspace) / ".breadboard" / "artifacts") if isinstance(workspace, str) and workspace.strip() else ""
        if not root.strip():
            raise ChildError("Ray child target has no durable artifact store")
        return ArtifactStore(Path(root))

    def _durably_prepare_result(self, target: Mapping[str, Any], payload: Mapping[str, Any]) -> Mapping[str, Any]:
        store = self._artifact_store(target)
        ref = payload.get("artifact_ref")
        if isinstance(ref, Mapping):
            artifact = ArtifactRef(str(ref["digest"]), int(ref["size_bytes"]), str(ref["media_type"]))
            store.read(artifact)
            return {"artifact_ref": artifact.as_dict()}
        value = payload.get("result_bytes")
        if not isinstance(value, bytes):
            result = payload.get("result")
            if isinstance(result, str):
                value = result.encode()
            elif isinstance(result, Mapping):
                value = json.dumps(dict(result), sort_keys=True).encode()
            else:
                raise ChildError("completed Ray child result has no durable payload")
        artifact = store.put(value, media_type="application/octet-stream")
        return {"artifact_ref": artifact.as_dict()}


    def start(self, activation: ChildActivation, spec: ChildSpec) -> ExecutionTarget:
        target_ref = activation.execution_target_ref
        job_id = target_ref.removeprefix("job:")
        invocation_id = self._invocation_id(job_id)
        workspace = (
            Path(activation.workspace).expanduser().resolve()
            if activation.workspace is not None
            else self._workspace
        )
        if workspace is None:
            raise ChildError("Ray child adapter is not bound to a workspace")
        manager_job_id = self._manager_job_id(job_id, workspace)
        artifact_store_root = activation.artifact_store_root or str(workspace / ".breadboard" / "artifacts")
        task_descriptor = {
            "child_session_id": activation.child_session_id,
            "recovery_ref": activation.recovery_ref,
            "task_hash": spec.retained()["task_hash"],
            "invocation_id": invocation_id,
        }
        job = self.orchestrator.job_manager.get(manager_job_id)
        if job is None:
            job = self.orchestrator.spawn_subagent(
                owner_agent=activation.parent_session_id,
                agent_id=activation.child_session_id,
                async_mode=True,
                task_descriptor=task_descriptor,
                job_id=manager_job_id,
            ).job
        elif not isinstance(job.task_descriptor, dict) or job.task_descriptor.get("invocation_id") != invocation_id:
            job.task_descriptor = dict(job.task_descriptor or {})
            job.task_descriptor["invocation_id"] = invocation_id
        if job.state in {"completed", "failed", "killed"}:
            raise ChildError(
                f"Ray job {job_id} is already terminal ({job.state})"
            )
        actor = self._lookup_actor(job_id, workspace)
        try:
            if actor is None:
                if self._default_actor_launcher:
                    actor = self._actor_launcher(job_id, workspace, spec.task, artifact_store_root)
                else:
                    actor = self._actor_launcher(job_id, workspace, spec.task)
            else:
                self._submit_invocation(actor, invocation_id, spec.task)
        except BaseException:
            if self._lookup_actor(job_id, workspace) is None:
                self.orchestrator.mark_job_failed(manager_job_id)
            raise
        self._actors[self._actor_key(job_id, workspace)] = actor
        metadata = {
            "job": {
                "job_id": job_id,
                "agent_id": job.agent_id,
                "owner_agent": job.owner_agent,
                "kind": job.kind,
                "state": job.state,
                "seq": job.seq,
                "task_descriptor": job.task_descriptor,
                "workspace": str(workspace),
                "artifact_store_root": artifact_store_root,
            }
        }
        return ExecutionTarget(target_ref, volatile_handle=actor, metadata=metadata)
    def observe(self, target: Mapping[str, Any]) -> str:
        job_id = str(target.get("ref", "")).removeprefix("job:")
        workspace = self._target_workspace(target)
        manager_job_id = self._manager_job_id(job_id, workspace)
        self._restore_job(target, job_id)
        job = self.orchestrator.job_manager.get(manager_job_id)
        if job is not None and job.state in {"failed", "killed"}:
            return str(job.state)
        metadata = target.get("metadata")
        job_data = metadata.get("job") if isinstance(metadata, Mapping) else None
        if (
            isinstance(job_data, Mapping)
            and job_data.get("state") == "completed"
            and isinstance(job_data.get("result_payload"), Mapping)
        ):
            try:
                durable_payload = self._durably_prepare_result(
                    target, job_data["result_payload"]
                )
            except FileNotFoundError:
                self._mark_job_failed(target, job_id)
                return "failed"
            except OSError:
                return "accepted"
            except (ChildError, KeyError, RuntimeError, TypeError, ValueError):
                self._mark_job_failed(target, job_id)
                return "failed"
            if job is None:
                self._mark_job_failed(target, job_id)
                return "failed"
            if isinstance(job_data, dict):
                job_data["state"] = "completed"
                job_data["result_payload"] = dict(durable_payload)
            return "completed"
        actor = self._lookup_actor(job_id, workspace)
        if actor is None:
            if self._actor_lookup_is_unavailable(job_id, workspace):
                return "pending"
            if job is not None and job.state == "completed":
                return "completed"
            if isinstance(job_data, Mapping) and job_data.get("seq") == 0:
                return "absent"
            if job is not None and job.state not in {"failed", "killed"}:
                self.orchestrator.mark_job_failed(manager_job_id)
            return "absent"
        if self._invocation_state(actor, self._invocation_id(job_id)) == "missing":
            if isinstance(job_data, Mapping) and job_data.get("seq") == 0:
                return "absent"
            return "absent" if self.cancel(target) else "pending"
        try:
            state = str(self._ray_get(getattr(actor, "get_state", None))).lower()
        except BaseException:
            if getattr(actor, "get_invocation_state", None) is None:
                return "pending"
            actor = self._refresh_actor_after_rpc_failure(
                job_id, actor, workspace
            )
            if actor is None:
                if self._actor_lookup_is_unavailable(job_id, workspace):
                    return "pending"
                self._mark_job_failed(target, job_id)
                return "absent"
            try:
                state = str(self._ray_get(getattr(actor, "get_state", None))).lower()
            except BaseException:
                return "pending"
        if state == "completed":
            result = getattr(actor, "get_result", None)
            if result is None:
                self._mark_job_failed(target, job_id)
                return "failed"
            try:
                result_payload = self._ray_get(result)
            except BaseException:
                return "pending"
            if not isinstance(result_payload, Mapping):
                self._mark_job_failed(target, job_id)
                return "failed"
            try:
                durable_payload = self._durably_prepare_result(target, result_payload)
            except (ChildError, KeyError, TypeError, ValueError, FileNotFoundError):
                self._mark_job_failed(target, job_id)
                return "failed"
            except OSError:
                return "accepted"
            except RuntimeError:
                self._mark_job_failed(target, job_id)
                return "failed"
            metadata = target.get("metadata")
            job_data = metadata.get("job") if isinstance(metadata, Mapping) else None
            if isinstance(job_data, dict):
                job_data["state"] = "completed"
                job_data["result_payload"] = dict(durable_payload)
        if state == "failed":
            self._mark_job_failed(target, job_id)
        return state

    def acknowledge_result(
        self,
        target: Mapping[str, Any],
        *,
        result_refs: Sequence[ArtifactRef] | None = None,
    ) -> None:
        job_id = str(target.get("ref", "")).removeprefix("job:")
        workspace = self._target_workspace(target)
        self._restore_job(target, job_id)
        manager_job_id = self._manager_job_id(job_id, workspace)
        metadata = target.get("metadata")
        job_data = metadata.get("job") if isinstance(metadata, Mapping) else None
        payload = job_data.get("result_payload") if isinstance(job_data, Mapping) else None
        if result_refs is not None:
            payload = (
                {"artifact_ref": result_refs[0].as_dict()}
                if len(result_refs) == 1
                else {"artifact_refs": [ref.as_dict() for ref in result_refs]}
            )
        job = self.orchestrator.job_manager.get(manager_job_id)
        if (not isinstance(payload, Mapping) or not payload) and job is not None:
            payload = getattr(job, "result_payload", None)
            if isinstance(job_data, dict) and isinstance(payload, Mapping):
                job_data["result_payload"] = dict(payload)
        if not isinstance(payload, Mapping):
            raise ChildError("completed Ray child has no durable result payload")
        if job is not None and job.state == "completed":
            marked = job
        else:
            marked = self.orchestrator.mark_job_completed(manager_job_id, result_payload=dict(payload))
        if marked is None:
            raise ChildError("completed Ray child could not be durably marked")
        if isinstance(job_data, dict):
            job_data["state"] = "completed"
            job_data["seq"] = marked.seq
            job_data["result_payload"] = dict(payload)
    def cancel(self, target: Mapping[str, Any]) -> bool:
        job_id = str(target.get("ref", "")).removeprefix("job:")
        workspace = self._target_workspace(target)
        manager_job_id = self._manager_job_id(job_id, workspace)
        metadata = target.get("metadata")
        job_data = metadata.get("job") if isinstance(metadata, Mapping) else None
        has_recovery_metadata = isinstance(job_data, Mapping) and all(
            key in job_data for key in ("agent_id", "owner_agent", "kind")
        )
        self._restore_job(target, job_id)
        job = self.orchestrator.job_manager.get(manager_job_id)
        if job is not None and job.state in {"completed", "failed"}:
            return False
        if job is not None and job.state == "killed":
            return True
        actor = self._lookup_actor(job_id, self._target_workspace(target))
        if actor is None:
            if has_recovery_metadata and not (
                not self._actor_lookup_is_unavailable(job_id, workspace)
                and isinstance(job_data, Mapping)
                and job_data.get("seq") == 0
            ):
                return False
            marked = self.orchestrator.mark_job_killed(manager_job_id)
            if job is not None and marked is None:
                return False
            if isinstance(job_data, dict) and marked is not None:
                job_data["state"] = "killed"
                job_data["seq"] = marked.seq
            self.release_terminal(target)
            return marked is not None
        try:
            cancel = getattr(actor, "cancel", None)
            if cancel is None:
                import ray

                ray.kill(actor, no_restart=True)
                cancellation_state = "killed"
            else:
                cancellation_state = self._ray_get(cancel)
                if cancellation_state is False:
                    return False
                cancellation_state = (
                    "killed"
                    if cancellation_state is True
                    else str(cancellation_state).lower()
                )
                if cancellation_state in {"completed", "failed"}:
                    observed = self.observe(target)
                    if observed == "completed":
                        self.acknowledge_result(target)
                        self.release_terminal(target)
                    return False
                if cancellation_state != "killed":
                    return False
        except BaseException:
            return False
        marked = self.orchestrator.mark_job_killed(manager_job_id)
        if job is not None and marked is None:
            return False
        if isinstance(job_data, dict) and marked is not None:
            job_data["state"] = "killed"
            job_data["seq"] = marked.seq
        self.release_terminal(target)
        return True

    def prepare_result(
        self, target: Mapping[str, Any], spec: ChildSpec
    ) -> bytes | ArtifactRef | None:
        job_id = str(target.get("ref", "")).removeprefix("job:")
        manager_job_id = self._manager_job_id(job_id, self._target_workspace(target))
        job = self.orchestrator.job_manager.get(manager_job_id)
        payload = getattr(job, "result_payload", None) if job is not None else None
        metadata = target.get("metadata")
        job_data = metadata.get("job") if isinstance(metadata, Mapping) else None
        if not isinstance(payload, Mapping) or not payload:
            payload = job_data.get("result_payload") if isinstance(job_data, Mapping) else None
        if not isinstance(payload, Mapping) or not payload:
            actor = self._lookup_actor(job_id, self._target_workspace(target))
            if actor is not None:
                result = getattr(actor, "get_result", None)
                payload = self._ray_get(result) if result is not None else None
        if not isinstance(payload, Mapping):
            return None
        value = payload.get("result_bytes")
        if isinstance(value, bytes):
            return value
        ref = payload.get("artifact_ref")
        if isinstance(ref, Mapping):
            try:
                return ArtifactRef(str(ref["digest"]), int(ref["size_bytes"]), str(ref["media_type"]))
            except (KeyError, TypeError, ValueError):
                return None
