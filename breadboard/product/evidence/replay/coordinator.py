from __future__ import annotations

from collections.abc import Mapping

from breadboard.product.runtime.artifacts import ArtifactRef, ArtifactStore

from .admission import ReplayAdmission, ReplayRunResult
from .execution import ReplayExecution
from .journal import ReplayJournal
from .plan import ReplayPlan
from .ports import (
    ReplayWorker,
    ReplayWorkerCanceled,
    ReplayWorkerIntegrityError,
    ReplayWorkerTimedOut,
)
from .redaction import ReplayRedactor


class ReplayPublicationAmbiguousError(RuntimeError):
    pass


class ReplayCoordinator:
    """Run one replay plan without importing E4 orchestration."""

    def __init__(
        self,
        store: ArtifactStore,
        worker: ReplayWorker,
        *,
        admission: ReplayAdmission | None = None,
        journal: ReplayJournal | None = None,
        redactor: ReplayRedactor | None = None,
    ) -> None:
        if not isinstance(store, ArtifactStore):
            raise TypeError("replay coordinator requires an ArtifactStore")
        self.store = store
        self.worker = worker
        self.admission = admission or ReplayAdmission()
        self.journal = journal
        inherited_redactor = getattr(worker, "redactor", None)
        self.redactor = redactor or (
            inherited_redactor
            if isinstance(inherited_redactor, ReplayRedactor)
            else ReplayRedactor()
        )

    def run(
        self,
        plan: ReplayPlan,
        *,
        reuse_candidate: ReplayRunResult | None = None,
        stored_artifacts: Mapping[str, ArtifactRef] | None = None,
        execute: bool = True,
    ) -> ReplayRunResult:
        if plan.worker_id != self.worker.worker_id:
            raise ValueError("replay plan worker_id does not match the selected worker")
        decision = self.admission.decide(
            plan,
            reuse_candidate=reuse_candidate,
            stored_artifacts=stored_artifacts,
            execute=execute,
        )
        if decision == "reuse":
            assert reuse_candidate is not None
            self._verify_artifacts(reuse_candidate.artifacts)
            return ReplayRunResult(
                "reused",
                plan.plan_id,
                reuse_candidate.execution,
                reuse_candidate.artifacts,
            )
        if decision == "stored":
            assert stored_artifacts is not None
            self._verify_artifacts(stored_artifacts)
            return ReplayRunResult("stored", plan.plan_id, None, stored_artifacts)

        if self.journal is not None:
            durable = self.journal.try_read(plan.plan_id)
            if durable is not None:
                artifacts = durable.artifacts
                if durable.state == "completed":
                    plan.manifest.validate_artifacts(artifacts)
                    self._verify_artifacts(artifacts)
                error = (
                    None if durable.claimable else f"durable replay is {durable.state}"
                )
                return ReplayRunResult(
                    "reused", plan.plan_id, durable, artifacts, error
                )
            try:
                sink = self.journal.start(plan.plan_id)
            except FileExistsError:
                durable = self.journal.read(plan.plan_id)
                artifacts = durable.artifacts
                if durable.state == "completed":
                    plan.manifest.validate_artifacts(artifacts)
                    self._verify_artifacts(artifacts)
                error = (
                    None if durable.claimable else f"durable replay is {durable.state}"
                )
                return ReplayRunResult(
                    "reused", plan.plan_id, durable, artifacts, error
                )
        else:
            sink = None

        execution = ReplayExecution(plan.plan_id, sink=sink)
        execution.admit()
        execution.run()
        created: set[ArtifactRef] = set()
        try:
            worker_result = self.worker.execute(
                plan, self.store.read(plan.input_artifact)
            )
            if plan.transcript_path in worker_result.outputs:
                raise ReplayWorkerIntegrityError(
                    "replay worker cannot publish the normalized transcript"
                )
            expected_outputs = plan.manifest.paths - {plan.transcript_path}
            if set(worker_result.outputs) != expected_outputs:
                raise ReplayWorkerIntegrityError(
                    "replay worker outputs do not match the immutable manifest"
                )
            redacted = self.redactor.worker_result(
                worker_result, plan.manifest.media_types, plan.transcript_path
            )
            outputs = dict(redacted.outputs)
            outputs[plan.transcript_path] = redacted.transcript_bytes()
            with self.store.transaction():
                try:
                    artifacts = {
                        path: self.store.put(
                            content,
                            media_type=plan.manifest.media_types[path],
                            created=created,
                        )
                        for path, content in sorted(outputs.items())
                    }
                    plan.manifest.validate_artifacts(artifacts)
                    self._verify_artifacts(artifacts)
                except BaseException:
                    for ref in created:
                        self.store.discard(ref)
                    created.clear()
                    raise
            # After releasing the CAS lock these refs may be shared by another plan.
            # Only the completed journal event makes them authoritative for this plan.
            created.clear()
            return self._complete(plan, execution, artifacts)
        except ReplayWorkerCanceled:
            self._rollback(created)
            execution.cancel("isolated replay canceled")
            return ReplayRunResult(
                "executed", plan.plan_id, execution, {}, "isolated replay canceled"
            )
        except ReplayWorkerTimedOut:
            self._rollback(created)
            execution.time_out("isolated replay timed out")
            return ReplayRunResult(
                "executed", plan.plan_id, execution, {}, "isolated replay timed out"
            )
        except ReplayWorkerIntegrityError as error:
            self._rollback(created)
            reason = self.redactor.text(str(error))
            execution.integrity_fail(reason)
            return ReplayRunResult("executed", plan.plan_id, execution, {}, reason)
        except ReplayPublicationAmbiguousError:
            raise
        except Exception as error:  # noqa: BLE001 - worker and storage failures become durable replay outcomes.
            self._rollback(created)
            reason = type(error).__name__
            execution.fail(reason)
            return ReplayRunResult("executed", plan.plan_id, execution, {}, reason)

    def _verify_artifacts(self, artifacts: Mapping[str, ArtifactRef]) -> None:
        for ref in artifacts.values():
            self.store.read(ref)

    def _rollback(self, created: set[ArtifactRef]) -> None:
        if not created:
            return
        with self.store.transaction():
            for ref in created:
                self.store.discard(ref)

    def _complete(
        self,
        plan: ReplayPlan,
        execution: ReplayExecution,
        artifacts: Mapping[str, ArtifactRef],
    ) -> ReplayRunResult:
        try:
            execution.complete(artifacts, integrity_verified=True)
        except Exception as error:
            if self.journal is None:
                raise
            try:
                durable = self.journal.try_read(plan.plan_id)
            except Exception as read_error:
                raise ReplayPublicationAmbiguousError(
                    "replay completion could not be reconciled"
                ) from read_error
            if durable is None:
                raise ReplayPublicationAmbiguousError(
                    "replay completion has no durable execution to reconcile"
                ) from error
            if durable.state == "completed":
                if dict(durable.artifacts) != dict(artifacts):
                    raise ReplayPublicationAmbiguousError(
                        "durable completion artifacts do not match prepared publication"
                    ) from error
                plan.manifest.validate_artifacts(durable.artifacts)
                self._verify_artifacts(durable.artifacts)
                return ReplayRunResult(
                    "executed", plan.plan_id, durable, durable.artifacts
                )
            if durable.state != "running":
                raise ReplayPublicationAmbiguousError(
                    f"replay completion reconciled to unexpected state {durable.state}"
                ) from error
            reason = type(error).__name__
            try:
                execution.fail(reason)
            except Exception as terminal_error:
                raise ReplayPublicationAmbiguousError(
                    "replay completion failure could not publish a terminal outcome"
                ) from terminal_error
            return ReplayRunResult("executed", plan.plan_id, execution, {}, reason)
        return ReplayRunResult("executed", plan.plan_id, execution, artifacts)
