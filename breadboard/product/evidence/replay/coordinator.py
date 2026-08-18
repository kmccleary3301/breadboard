from __future__ import annotations

from typing import Mapping

from breadboard.product.runtime.artifacts import ArtifactRef, ArtifactStore

from .admission import ReplayAdmission, ReplayRunResult
from .execution import ReplayExecution
from .plan import ReplayPlan
from .ports import ReplayWorker, ReplayWorkerIntegrityError


class ReplayCoordinator:
    """Run one replay plan without importing E4 orchestration."""

    def __init__(self, store: ArtifactStore, worker: ReplayWorker, *, admission: ReplayAdmission | None = None) -> None:
        if not isinstance(store, ArtifactStore):
            raise TypeError("replay coordinator requires an ArtifactStore")
        self.store = store
        self.worker = worker
        self.admission = admission or ReplayAdmission()

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
            return ReplayRunResult("reused", plan.plan_id, reuse_candidate.execution, reuse_candidate.artifacts)
        if decision == "stored":
            assert stored_artifacts is not None
            self._verify_artifacts(stored_artifacts)
            return ReplayRunResult("stored", plan.plan_id, None, stored_artifacts)

        execution = ReplayExecution(plan.plan_id)
        execution.admit()
        execution.run()
        try:
            worker_result = self.worker.execute(plan, self.store.read(plan.input_artifact))
            outputs = dict(worker_result.outputs)
            outputs[plan.transcript_path] = worker_result.transcript_bytes()
            if set(outputs) != plan.manifest.paths:
                raise ReplayWorkerIntegrityError("replay worker outputs do not match the immutable manifest")
            artifacts = {
                path: self.store.put(content, media_type=plan.manifest.media_types[path])
                for path, content in sorted(outputs.items())
            }
            plan.manifest.validate_artifacts(artifacts)
            self._verify_artifacts(artifacts)
            execution.complete(artifacts, integrity_verified=True)
            return ReplayRunResult("executed", plan.plan_id, execution, artifacts)
        except ReplayWorkerIntegrityError as error:
            execution.integrity_fail(str(error))
            return ReplayRunResult("executed", plan.plan_id, execution, {}, str(error))
        except Exception as error:
            execution.fail(type(error).__name__)
            return ReplayRunResult("executed", plan.plan_id, execution, {}, type(error).__name__)

    def _verify_artifacts(self, artifacts: Mapping[str, ArtifactRef]) -> None:
        for ref in artifacts.values():
            self.store.read(ref)
