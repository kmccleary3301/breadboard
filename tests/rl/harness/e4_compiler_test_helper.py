from __future__ import annotations

from pathlib import Path
from typing import Mapping

from breadboard.artifacts.cas import FilesystemCAS
from breadboard.product.harness.resolution import compile_e4_harness
from breadboard.rl.harness.policy_provider import E4TargetPolicyProjection
from breadboard.rl.harness.runners.base import FrozenJsonObject, freeze_json_object
from breadboard_engine.compilation.contracts import (
    CompileOptions,
    CompileTarget,
    TaskContract,
    TaskEvidenceContract,
    TaskRetentionContract,
    TaskVerifierContract,
)
from breadboard_engine.e4_targets import load_e4_target


PI_DYNAMIC_FIELDS = {
    "readme_path": "README.md",
    "docs_path": "docs",
    "examples_path": "examples",
    "current_date_time": "2026-09-14T00:00:00Z",
    "cwd": "/workspace",
}


def _compile_options() -> CompileOptions:
    return CompileOptions(
        target=CompileTarget(
            runner_adapter_id="breadboard.conductor.v1",
            runtime_abi="breadboard.conductor.v1",
        ),
        task_contract=TaskContract(
            contract_id="swe-task.v1",
            parameter_schema={
                "type": "object",
                "properties": {"instruction": {"type": "string"}},
                "required": ["instruction"],
                "additionalProperties": False,
            },
            artifacts=(),
            verifier=TaskVerifierContract(
                binding_id=None,
                input_artifact_ids=(),
                result_schema={
                    "type": "object",
                    "properties": {"passed": {"type": "boolean"}},
                    "required": ["passed"],
                    "additionalProperties": False,
                },
                timeout_ms=30_000,
            ),
            evidence=TaskEvidenceContract(
                required_event_types=("turn.completed",),
                required_artifact_ids=(),
            ),
            retention=TaskRetentionContract(
                retention_class_id="test-evidence",
                minimum_retention_seconds=60,
            ),
        ),
        source_contract="v2",
        v1_loss_policy="reject_all",
    )


def compile_pi_target(
    tmp_path: Path,
    *,
    dynamic_fields: Mapping[str, str] | None = None,
) -> tuple[E4TargetPolicyProjection, FrozenJsonObject]:
    """Compile the checked-in pi target and return only compiler-derived values."""
    cas = FilesystemCAS(tmp_path / "e4-target-cas")
    try:
        compiled = compile_e4_harness(
            load_e4_target("pi@0.57.1"),
            dict(PI_DYNAMIC_FIELDS if dynamic_fields is None else dynamic_fields),
            {
                "version": 2,
                "profile": {"name": "e4-test-profile"},
                "workspace": {"root": "workspace"},
                "provider_tools": {"use_native": True},
                "providers": {
                    "default_model": "test-model",
                    "models": [
                        {
                            "id": "test-model",
                            "adapter": "openai",
                            "params": {"temperature": 0.25},
                        }
                    ],
                },
            },
            cas=cas,
            options=_compile_options(),
        )
        projection = E4TargetPolicyProjection.from_compiled(compiled.manifest)
        semantics = freeze_json_object(
            compiled.manifest.semantic.to_canonical_obj(),
            field_name="compiled E4 target semantics",
        )
        return projection, semantics
    finally:
        cas.close()
