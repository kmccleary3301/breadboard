from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import pytest

from scripts.e4_parity.adapters import pi_p5_l1_capture as capture


def test_target_probe_argv_resolves_relative_scratch_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative_probe = Path("tmp/e4-pi-l1/pi_p5_target_probe.mjs")
    monkeypatch.setattr(capture, "TARGET_PROBE_SCRIPT_PATH", relative_probe)

    argv = capture._target_probe_argv()

    assert argv[:2] == ["npx", "tsx"]
    assert Path(argv[2]).is_absolute()
    assert argv[2] == relative_probe.resolve().as_posix()


def test_scratch_capture_reports_scratch_gate_without_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scratch_gate = tmp_path / "artifacts" / "node_gate.json"
    monkeypatch.setattr(capture, "_overlay_path", lambda _out_dir, _path: scratch_gate)
    monkeypatch.setattr(capture, "_scratch_paths", lambda _out_dir: nullcontext())
    monkeypatch.setattr(
        capture,
        "build",
        lambda **_kwargs: {
            "ok": False,
            "errors": ["scratch validation failed"],
            "node_gate": "artifacts/conformance/node_gate/accepted.json",
        },
    )

    report = capture.capture(promote_accepted=False, out_dir=tmp_path)

    assert report["ok"] is True
    assert report["accepted"] is False
    assert report["promotion_eligible"] is False
    assert report["scratch_node_gate_ok"] is False
    assert report["scratch_node_gate_errors"] == ["scratch validation failed"]
    assert report["accepted_node_gate_ref"] == "artifacts/conformance/node_gate/accepted.json"
    assert report["node_gate"] == scratch_gate.resolve().as_posix()
