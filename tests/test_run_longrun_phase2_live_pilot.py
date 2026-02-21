from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "run_longrun_phase2_live_pilot.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("run_longrun_phase2_live_pilot", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_estimate_cost_handles_missing_split_usage() -> None:
    module = _load_module()
    value = module._estimate_cost_usd(
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=1500,
        input_cost_per_million=1.0,
        output_cost_per_million=2.0,
    )
    assert value > 0.0
    # Fallback path bills all tokens at output rate when split usage is absent.
    assert round(value, 6) == round((1500 / 1_000_000.0) * 2.0, 6)


def test_dry_run_writes_expected_schema(tmp_path: Path, monkeypatch) -> None:
    module = _load_module()
    out_json = tmp_path / "live.json"
    out_md = tmp_path / "live.md"
    argv = [
        "run_longrun_phase2_live_pilot.py",
        "--out-json",
        str(out_json),
        "--out-markdown",
        str(out_md),
        "--workspace-root",
        str(tmp_path / "ws"),
        "--dry-run",
        "--allow-missing-keys",
        "--max-pairs",
        "2",
        "--max-total-tokens",
        "100000",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    rc = module.main()
    assert rc == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "longrun_phase2_live_pilot_v1"
    assert payload["dry_run"] is True
    assert len(payload["pairs"]) == 2
    assert payload["totals"]["total_tokens"] >= 0
    first_pair = payload["pairs"][0]
    assert "telemetry" in first_pair["baseline"]
    assert "telemetry" in first_pair["longrun"]
    assert out_md.exists()


def test_run_one_with_guard_fail_open_on_exception(tmp_path: Path, monkeypatch) -> None:
    module = _load_module()
    task = module.LiveTask(
        task_id="boom_task",
        prompt="boom",
        max_steps=2,
        context={},
    )

    def _boom(**_: object) -> dict:
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(module, "_run_one", _boom)
    row = module._run_one_with_guard(
        label="baseline",
        config_path=Path("agent_configs/base_v2.yaml"),
        workspace_root=tmp_path / "ws",
        task=task,
        model_override=None,
        dry_run=False,
        dry_run_seed=7,
        overrides={},
        pricing_input_per_million=0.5,
        pricing_output_per_million=2.0,
        arm_timeout_seconds=0.0,
    )
    assert row["completed"] is False
    assert row["completion_reason"] == "arm_exception"
    assert row["usage"]["total_tokens"] == 0
    assert row["errors"]
    assert row["telemetry"]["failure_class"] == "arm_exception"


def test_run_one_with_guard_timeout_returns_structured_failure(tmp_path: Path, monkeypatch) -> None:
    module = _load_module()
    task = module.LiveTask(
        task_id="timeout_task",
        prompt="timeout",
        max_steps=2,
        context={},
    )

    class _FakeQueue:
        def close(self) -> None:
            return None

        def join_thread(self) -> None:
            return None

        def empty(self) -> bool:
            return True

    class _FakeProcess:
        def __init__(self, *_: object, **__: object) -> None:
            self.exitcode = None
            self._alive = True

        def start(self) -> None:
            return None

        def join(self, timeout: float | None = None) -> None:
            return None

        def is_alive(self) -> bool:
            return self._alive

        def terminate(self) -> None:
            self._alive = False
            self.exitcode = -15

        def kill(self) -> None:
            self._alive = False
            self.exitcode = -9

    class _FakeContext:
        def Queue(self, maxsize: int = 0) -> _FakeQueue:
            return _FakeQueue()

        def Process(self, target=None, args=(), daemon=True) -> _FakeProcess:
            return _FakeProcess()

    monkeypatch.setattr(module.mp, "get_context", lambda _: _FakeContext())
    row = module._run_one_with_guard(
        label="longrun",
        config_path=Path("agent_configs/longrun_conservative_v1.yaml"),
        workspace_root=tmp_path / "ws",
        task=task,
        model_override=None,
        dry_run=False,
        dry_run_seed=7,
        overrides={},
        pricing_input_per_million=0.5,
        pricing_output_per_million=2.0,
        arm_timeout_seconds=0.001,
    )
    assert row["completed"] is False
    assert row["completion_reason"] == "arm_timeout"
    assert row["usage"]["total_tokens"] == 0
    assert row["errors"]
    assert row["telemetry"]["timeout_hit"] is True
