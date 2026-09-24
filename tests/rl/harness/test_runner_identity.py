from __future__ import annotations

import os
from pathlib import Path

import pytest

from breadboard.rl.harness.runner_identity import measure_module_artifact


def test_measure_module_artifact_rejects_hardlinked_installed_module_with_remedy(
    tmp_path: Path,
) -> None:
    cache_module = tmp_path / "uv-cache" / "module.py"
    installed_module = tmp_path / "site-packages" / "module.py"
    cache_module.parent.mkdir()
    installed_module.parent.mkdir()
    cache_module.write_bytes(b"module = 'installed'\n")
    os.link(cache_module, installed_module)
    nlink = os.stat(installed_module).st_nlink
    assert nlink >= 2

    with pytest.raises(RuntimeError) as raised:
        measure_module_artifact(str(installed_module))

    message = str(raised.value)
    assert "hardlinked installed module" in message
    assert f"path={installed_module}" in message
    assert f"nlink={nlink}" in message
    assert "uv pip install --link-mode=copy" in message
    assert "or pip" in message
