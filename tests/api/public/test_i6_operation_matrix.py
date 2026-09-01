from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "contracts/public/operations.v2.json"
MATRIX = Path(__file__).parent / "fixtures/i6_operation_matrix.json"


def test_i6_matrix_is_an_independent_exact_catalog_projection() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    assert matrix["schema_version"] == "bb.product_integration.i6_operation_matrix.v1"

    expected = {
        operation["operation_id"]: (
            operation["bindings"]["openapi"]["method"],
            operation["bindings"]["openapi"]["path"],
            operation["bindings"]["python_sdk"]["method"],
            operation["bindings"]["tui"]["action_id"],
        )
        for operation in catalog["operations"]
    }
    observed = {
        operation["operation_id"]: (
            operation["http_method"],
            operation["path"],
            operation["python_method"],
            operation["typescript_action"],
        )
        for operation in matrix["operations"]
    }

    assert len(matrix["operations"]) == len(observed) == 26
    assert observed == expected
    assert all(operation["cli"] for operation in matrix["operations"])
    assert all(
        operation["success_status"] in {200, 202} for operation in matrix["operations"]
    )


def test_installed_differential_cleans_up_server_on_readiness_timeout(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import pytest
    from scripts.quality import run_i6_installed_differential as differential

    class FakeStderr:
        @staticmethod
        def read() -> str:
            return ""

    class FakeProcess:
        pid = 1234
        stderr = FakeStderr()
        returncode: int | None = None
        terminated = False
        waits: list[int] = []

        @staticmethod
        def poll() -> None:
            return None

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15

        def wait(self, *, timeout: int) -> int:
            self.waits.append(timeout)
            return self.returncode or 0

    process = FakeProcess()
    monotonic_values = iter((0.0, 21.0))
    monkeypatch.setattr(differential, "_free_port", lambda: 43210)
    monkeypatch.setattr(
        differential.subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(
        differential.time,
        "monotonic",
        lambda: next(monotonic_values),
    )

    with pytest.raises(RuntimeError, match="did not become ready"):
        with differential._server(Path("/python"), tmp_path):
            raise AssertionError("unreachable")

    assert process.terminated is True
    assert process.waits == [10]


def test_installed_differential_preserves_early_exit_error_during_cleanup(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import pytest
    from scripts.quality import run_i6_installed_differential as differential

    class FakeStderr:
        read_count = 0

        def read(self) -> str:
            self.read_count += 1
            return "startup failed"

    class FakeProcess:
        pid = 1234
        stderr = FakeStderr()
        returncode = 1
        terminated = False
        waits: list[int] = []

        def poll(self) -> int:
            return self.returncode

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, *, timeout: int) -> int:
            self.waits.append(timeout)
            return self.returncode

    process = FakeProcess()
    monotonic_values = iter((0.0, 1.0))
    monkeypatch.setattr(differential, "_free_port", lambda: 43210)
    monkeypatch.setattr(
        differential.subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(
        differential.time,
        "monotonic",
        lambda: next(monotonic_values),
    )

    with pytest.raises(
        RuntimeError,
        match="exited before readiness: startup failed",
    ) as captured:
        with differential._server(Path("/python"), tmp_path):
            raise AssertionError("unreachable")

    assert process.terminated is True
    assert process.waits == [10]
    assert process.stderr.read_count == 1
    assert any(
        "cleanup also failed" in note
        for note in getattr(captured.value, "__notes__", ())
    )
