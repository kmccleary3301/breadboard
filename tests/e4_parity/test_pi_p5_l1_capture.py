from __future__ import annotations

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
