from __future__ import annotations

import pytest

from agentic_coder_prototype.optimize import DiagnosticBundle, DiagnosticEntry


def test_diagnostic_bundle_round_trip() -> None:
    bundle = DiagnosticBundle(
        bundle_id="diag.bundle.001",
        evaluation_id="eval.001",
        evaluator_mode="replay",
        determinism_class="deterministic",
        entries=[
            DiagnosticEntry(
                diagnostic_id="diag.entry.001",
                kind="replay_gate",
                severity="warning",
                message="Replay lane emitted a warning.",
            )
        ],
        cache_identity={"key": "diag-key", "version": "v1"},
        retry_policy_hint={"max_retries": 0},
        reproducibility_notes={"schema": 2},
    )

    restored = DiagnosticBundle.from_dict(bundle.to_dict())
    assert restored == bundle


def test_diagnostic_bundle_rejects_invalid_evaluator_mode() -> None:
    with pytest.raises(ValueError, match="evaluator_mode"):
        DiagnosticBundle(
            bundle_id="diag.bundle.002",
            evaluation_id="eval.002",
            evaluator_mode="bad_mode",
            determinism_class="deterministic",
            entries=[],
            cache_identity={"key": "diag-key", "version": "v1"},
        )


def test_diagnostic_bundle_requires_cache_identity_structure() -> None:
    with pytest.raises(ValueError, match="cache_identity"):
        DiagnosticBundle(
            bundle_id="diag.bundle.003",
            evaluation_id="eval.003",
            evaluator_mode="replay",
            determinism_class="deterministic",
            entries=[],
            cache_identity={"key": "diag-key"},
        )
